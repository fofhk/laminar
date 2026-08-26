"""Payout bounds swept across quote size — the shadow study's §6.2 question.

    python -m laminar.bounds_sweep [YYYY-MM-DD]

Reward share is `mine / (mine + D)`, and D is only knowable as an interval (see
score.denominator_bounds), so every yield here is a pair, never a point. The
question this answers is whether that interval NARROWS at the size we would
actually quote: at small size `mine << D` and the two corners sit ~D_max/D_min
apart, but as `mine` grows the share tends to 1 regardless of D, so the corners
must converge somewhere. Where matters, because the go/no-go threshold is
stated on the pessimistic corner at ~100k.

Answer as of 2026-08-23: NOT at 100k. hi/lo is 1.65 at 3,000 shares/side,
barely better than 1.59 at 200 — the in-band books are deeper than that
argument assumed, and convergence only starts around 10,000 (~$300k).

Yields are GROSS: the reward stream only, with no adverse selection, inventory
cost, or pool decay netted off. They are an upper envelope on the truth, not an
estimate of it.
"""

import statistics as st
import sys
from datetime import UTC, datetime, timedelta

import polars as pl

from laminar import score, store

SIZES = (200.0, 1000.0, 3000.0, 10000.0)
REF_OFFSET_CAP = 1.0  # cents, same convention as gate_check._reference_quote
STRIDE = 5  # take every Nth snapshot; 1,439/day is far more than the medians need
N_MARKETS = 30  # watchlist size, for quoting the capital a size implies


def reference_quote(
    max_spread: float, midpoint: float, size: float
) -> tuple[score.Level, score.Level]:
    """The gate's reference quote, at an arbitrary size rather than a fixed 200."""
    offset = min(REF_OFFSET_CAP, max_spread * 0.5) / 100.0
    return (
        score.Level(round(midpoint - offset, 4), size),
        score.Level(round(midpoint + offset, 4), size),
    )


def sweep(day: str, sizes: tuple[float, ...] = SIZES) -> dict:
    books = store.load_books(day)
    if books.is_empty():
        raise SystemExit(f"no book samples for {day}")
    stamps = sorted(books["ts"].unique().to_list())[::STRIDE]
    books = books.filter(pl.col("ts").is_in(stamps))

    lo: dict[float, list[float]] = {s: [] for s in sizes}
    hi: dict[float, list[float]] = {s: [] for s in sizes}
    n = 0

    for _, g in books.group_by(["ts", "token_id"]):
        max_spread, min_size, rate = g["max_spread"][0], g["min_size"][0], g["daily_rate"][0]
        mid = g["midpoint"][0]
        if not (0.10 <= mid <= 0.90) or rate <= 0 or max_spread <= 0:
            continue  # outside the two-sided band, or not paying — nothing to score
        bids = [
            score.Level(r["price"], r["size"])
            for r in g.filter(pl.col("side") == "bid").iter_rows(named=True)
        ]
        asks = [
            score.Level(r["price"], r["size"])
            for r in g.filter(pl.col("side") == "ask").iter_rows(named=True)
        ]
        if not bids or not asks:
            continue
        params = score.RewardParams(max_spread, min_size, rate)
        n += 1
        for size in sizes:
            my_bid, my_ask = reference_quote(max_spread, mid, size)
            share_lo, share_hi = score.payout_bounds(
                params, mid, [my_bid], [my_ask], bids, asks
            )
            # Capital locked by a two-sided quote of `size` shares is
            # size * (1 - spread) ~= size USDC — see the shadow study design §3.5.
            lo[size].append(rate * share_lo * 365.0 / size * 100.0)
            hi[size].append(rate * share_hi * 365.0 / size * 100.0)

    return {"day": day, "samples": n, "lo": lo, "hi": hi}


def render(result: dict) -> str:
    out = [
        f"day={result['day']}  token-snapshots={result['samples']}  stride={STRIDE}min",
        "GROSS reward yield only — no adverse selection, inventory cost or pool decay.",
        "",
    ]
    head = (
        f"{'size/side':>10} {'capital':>9} | {'med_lo':>8} {'med_hi':>8} {'hi/lo':>6}"
        f" | {'mean_lo':>8} {'mean_hi':>8} {'hi/lo':>6}"
    )
    out += [head, "-" * len(head)]
    for size in SIZES:
        lo, hi = result["lo"][size], result["hi"][size]
        if not lo:
            continue
        med_lo, med_hi = st.median(lo), st.median(hi)
        avg_lo, avg_hi = st.mean(lo), st.mean(hi)
        capital = f"~${size * N_MARKETS / 1000:.0f}k"
        out.append(
            f"{size:>10.0f} {capital:>9} | {med_lo:>7.0f}% {med_hi:>7.0f}%"
            f" {med_hi / med_lo:>6.2f} | {avg_lo:>7.0f}% {avg_hi:>7.0f}%"
            f" {avg_hi / avg_lo:>6.2f}"
        )
    return "\n".join(out)


def main() -> None:
    day = sys.argv[1] if len(sys.argv) > 1 else (
        datetime.now(UTC) - timedelta(days=1)
    ).strftime("%Y-%m-%d")
    print(render(sweep(day)))


if __name__ == "__main__":
    main()
