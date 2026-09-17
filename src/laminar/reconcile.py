"""V2 — book-increment reconciliation (shadow study design §4).

V1 proved the matcher is *internally* consistent: if our orders ARE the displayed
book, the tape lands on us exactly. That is a statement about the code, not about
the world. V2 asks the world a question instead:

    when a print consumes depth, does the next snapshot show that depth gone?

If yes — the book is close to consume-only at this cadence, and the static
front/back queue bracket is a sound description of what our order would have
faced. If no — resting size is cancelled and replaced within the minute, so the
"size ahead of us" in `shadow.size_ahead` is not a stable obstacle, and the
back-of-queue bound is not the pessimistic edge it claims to be.

The unit of observation is one *interval* between two consecutive snapshots of
one token (`tb`, `ta`], and one consumed side. For the prints on that side in
that interval:

    consumed    = total printed size on the side
    deepest     = the furthest price the takers reached (lowest bid, highest ask)
    depth_*     = resting size at prices at least as good as `deepest`, before/after
    drawdown    = depth_before - depth_after

Every print on that side is at a price at least as good as `deepest` by
construction, so `consumed` is exactly the size that depth had to absorb. Four
outcomes, and the point is that none of them is hidden:

    accounted   drawdown == consumed   the book is consume-only
    under       drawdown <  consumed   depth was replaced within the minute
    over        drawdown >  consumed   depth was cancelled (helps a back-of-queue order)
    no_depth    depth_before == 0      the print touched a price we never recorded

`over` is the case that bears directly on open question 1: if cancels routinely
shrink the size ahead of a resting order, back-of-queue fills *more* than the
static bound predicts, and the "pessimistic" corner is not pessimistic.

This module reads `books_full` (all depth, not just the reward band) — a print
that sweeps past the band is exactly the case the band-filtered series cannot
reconcile. It is a pure read: nothing here writes or quotes.

    python -m laminar.reconcile <YYYY-MM-DD>
"""

import sys
from bisect import bisect_right
from dataclasses import dataclass

import polars as pl

from laminar import shadow
from laminar.score import Level

# A trading halt or a missed collector run shows up as a wide gap. Reconcile the
# interval when the snapshots are one cadence apart (or one missed sample), else
# drop it rather than attribute a minute of activity to two distant books.
MAX_GAP_MS = 180_000

# drawdown is compared to consumed within the larger of these. The floor keeps a
# two-share print from being classified by a floating-point hair; the ratio keeps
# a large sweep from being called exact on a rounding difference.
TOL_ABS = 1.0
TOL_REL = 0.01

ACCOUNTED, UNDER, OVER, NO_DEPTH = "accounted", "under", "over", "no_depth"


@dataclass(frozen=True)
class Observation:
    """One side of one inter-snapshot interval that saw a print."""

    token_id: str
    side: str  # the RESTING side the prints consumed ("bid" | "ask")
    ts_before: int
    ts_after: int
    deepest_price: float
    consumed: float
    depth_before: float
    depth_after: float
    drawdown: float

    @property
    def klass(self) -> str:
        if self.depth_before <= 0.0:
            return NO_DEPTH
        tol = max(TOL_ABS, TOL_REL * self.consumed)
        if self.drawdown > self.consumed + tol:
            return OVER
        if self.drawdown < self.consumed - tol:
            return UNDER
        return ACCOUNTED


def _book_series(books: pl.DataFrame) -> dict[str, list[tuple[int, list[Level], list[Level]]]]:
    """token -> [(ts, bids, asks)] sorted by ts. Empty books give an empty series."""
    if books.is_empty():
        return {}
    grouped = books.group_by(["token_id", "ts"]).agg(
        pl.col("side"), pl.col("price"), pl.col("size")
    )
    series: dict[str, list[tuple[int, list[Level], list[Level]]]] = {}
    for token_id, ts, sides, prices, sizes in grouped.iter_rows():
        bids = [Level(p, z) for s, p, z in zip(sides, prices, sizes) if s == "bid"]
        asks = [Level(p, z) for s, p, z in zip(sides, prices, sizes) if s == "ask"]
        series.setdefault(token_id, []).append((int(ts), bids, asks))
    for snaps in series.values():
        snaps.sort(key=lambda snap: snap[0])
    return series


def _prints_by_token(trades: pl.DataFrame) -> dict[str, list[tuple[int, str, float, float]]]:
    """token -> [(ts, tape_side, price, size)] sorted by ts."""
    if trades.is_empty():
        return {}
    out: dict[str, list[tuple[int, str, float, float]]] = {}
    for row in trades.iter_rows(named=True):
        out.setdefault(row["token_id"], []).append(
            (int(row["ts"]), row["side"], float(row["price"]), float(row["size"]))
        )
    for prints in out.values():
        prints.sort(key=lambda p: p[0])
    return out


def _depth_at_or_better(levels: list[Level], side: str, price: float) -> float:
    """Resting size at prices a taker reaching `price` would have swept."""
    if side == "bid":
        return sum(lv.size for lv in levels if lv.price >= price)
    return sum(lv.size for lv in levels if lv.price <= price)


def observations(
    books: pl.DataFrame, trades: pl.DataFrame, max_gap_ms: int = MAX_GAP_MS
) -> list[Observation]:
    """Reconcile every consecutive-snapshot interval that contains a print."""
    series = _book_series(books)
    prints_by_token = _prints_by_token(trades)
    out: list[Observation] = []

    for token_id, snaps in series.items():
        prints = prints_by_token.get(token_id)
        if not prints:
            continue
        p_ts = [p[0] for p in prints]
        for (tb, bids_b, asks_b), (ta, bids_a, asks_a) in zip(snaps, snaps[1:]):
            if ta - tb <= 0 or ta - tb > max_gap_ms:
                continue
            window = prints[bisect_right(p_ts, tb):bisect_right(p_ts, ta)]
            if not window:
                continue
            for side in ("bid", "ask"):
                eaten = [p for p in window if shadow.consumed_side(p[1]) == side]
                if not eaten:
                    continue
                consumed = sum(p[3] for p in eaten)
                if side == "bid":
                    deepest = min(p[2] for p in eaten)
                    side_b = bids_b
                    side_a = bids_a
                else:
                    deepest = max(p[2] for p in eaten)
                    side_b = asks_b
                    side_a = asks_a
                depth_before = _depth_at_or_better(side_b, side, deepest)
                depth_after = _depth_at_or_better(side_a, side, deepest)
                out.append(
                    Observation(
                        token_id=token_id,
                        side=side,
                        ts_before=tb,
                        ts_after=ta,
                        deepest_price=deepest,
                        consumed=consumed,
                        depth_before=depth_before,
                        depth_after=depth_after,
                        drawdown=depth_before - depth_after,
                    )
                )
    return out


def summarize(obs: list[Observation]) -> dict:
    """Class counts plus the aggregate depth-recovery ratio.

    `recovery` is sum(drawdown) / sum(consumed) over every interval that had
    recorded depth. 1.0 is a perfectly consume-only book; below 1 means depth is
    replaced within the minute, above 1 means cancels remove depth the tape never
    touched. It is reported alongside the class mix because a mean can hide a
    bimodal split between net resupply and net cancellation.
    """
    counts = {ACCOUNTED: 0, UNDER: 0, OVER: 0, NO_DEPTH: 0}
    consumed_total = 0.0
    drawdown_total = 0.0
    for o in obs:
        counts[o.klass] += 1
        if o.klass != NO_DEPTH:
            consumed_total += o.consumed
            drawdown_total += o.drawdown
    n = len(obs)
    return {
        "n": n,
        "counts": counts,
        "frac": {k: (counts[k] / n if n else 0.0) for k in counts},
        "consumed_total": consumed_total,
        "drawdown_total": drawdown_total,
        "recovery": (drawdown_total / consumed_total if consumed_total else 0.0),
    }


def _fmt(day: str, obs: list[Observation]) -> str:
    s = summarize(obs)
    c, f = s["counts"], s["frac"]
    lines = [
        f"day={day}  intervals-with-prints={s['n']}",
        "V2 book-increment reconciliation (books_full vs trade tape)",
        "",
        f"  accounted   {c[ACCOUNTED]:>6}  {f[ACCOUNTED]:6.1%}   drawdown == consumed",
        f"  under       {c[UNDER]:>6}  {f[UNDER]:6.1%}   depth replaced within the minute",
        f"  over        {c[OVER]:>6}  {f[OVER]:6.1%}   depth cancelled ahead of the tape",
        f"  no_depth    {c[NO_DEPTH]:>6}  {f[NO_DEPTH]:6.1%}   touched a price we never recorded",
        "",
        f"  consumed shares   {s['consumed_total']:,.0f}",
        f"  drawdown shares   {s['drawdown_total']:,.0f}",
        f"  recovery          {s['recovery']:.3f}   (1.0 = consume-only)",
    ]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    """Reconcile one UTC day, one book partition at a time.

    The collector box is a 1 vCPU / 961 MB droplet and a full day of
    `books_full` is millions of rows; hourly partitions keep peak memory inside
    the hourly rewrite the collector itself already does. Intervals that straddle
    an hour boundary are not reconciled — the two snapshots land in different
    partitions and joining them would mean loading the day anyway. That is a
    handful of intervals out of thousands, and the output says so rather than
    silently dropping them.
    """
    if len(argv) != 2:
        print("usage: python -m laminar.reconcile <YYYY-MM-DD>", file=sys.stderr)
        return 2
    day = argv[1]
    from laminar import store

    trades = store.load_trades(day)
    parts = sorted((store.LAMINAR_DIR / "books_full" / day).glob("*.parquet"))
    if not parts:
        print(f"no books_full partitions for {day}", file=sys.stderr)
        return 1
    obs: list[Observation] = []
    for part in parts:
        obs.extend(observations(pl.read_parquet(part), trades))
    print(_fmt(day, obs))
    print(
        f"\n  note: {len(parts)} hourly partitions; intervals crossing an hour"
        " boundary are not reconciled"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
