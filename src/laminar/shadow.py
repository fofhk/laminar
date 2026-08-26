"""Shadow fill matching — component A's core (see laminar_shadow_study_design_v1.md).

Answers ONE question: given a resting order we never placed, how much of the
real trade tape would have hit it? Nothing else. No inventory, no adverse
selection, no self-impact on the midpoint, no reward scoring — those are
separate layers that consume this one's output.

WHAT THIS CANNOT KNOW, AND HOW IT HANDLES THAT
----------------------------------------------
`/book` aggregates size per price level, not per maker, and snapshots are 60s
apart, so our position in the FIFO queue at a price level is unobservable in
principle — the same epistemic hole as the reward denominator (see
score.denominator_bounds). We therefore never return a point estimate: every
fill is computed at both extremes and the caller carries the interval.

    QUEUE_FRONT — our order is first at its level. Anything the print consumes
                  at that level is ours before it is anyone else's.
    QUEUE_BACK  — our order is last. The whole displayed size at our level has
                  to go before we see a share.

With a median print of ~20 shares against quotes of 200-3,300, QUEUE_BACK is
expected to fill almost nothing. That gap IS the uncertainty; do not average it
away.

MATCHING RULE
-------------
Each tx_hash is one print at one price (verified on 7,508 prints: no tx spans
two tokens, sides, or prices). A print of `size` at `price` that consumed the
bid side is absorbed by resting bids from the top down, so for our bid at `p`:

    ahead  = displayed bid size at prices > p        (QUEUE_FRONT)
             ... plus the size at exactly p          (QUEUE_BACK)
    fill   = clip(print_size - ahead, 0, our_size)

and eligibility needs `p >= print_price` — the taker was willing to sell that
low, so every bid above it was reachable. Asks mirror this.

Two consequences worth stating because they are the interesting ones:

  * Quoting INSIDE the spread fills first even though no print ever happened at
    that price. Our order is liquidity that did not exist; a taker sweeping to a
    worse price would have hit us on the way. Matching only at prices where a
    print landed would silently under-fill exactly the aggressive quotes we most
    need to price.
  * Quoting deep is penalised automatically, because `ahead` grows with the
    book above us. No separate rule needed.

THE ASSUMPTION THIS MAKES
-------------------------
Print size is held fixed. In the real counterfactual our order changes what the
taker does — they might fill more, or stop at a better price. That is the
reflexivity limit named in the study design; it cannot be simulated from public
data and is not silently papered over here.

THE 60-SECOND BLIND SPOT (measured, 2026-08-25)
-----------------------------------------------
V1 reconciles exactly on real data — 5,148 prints across 08-21..08-24, 100%
exact, worst absolute error 0.0 — but ~23% of prints are `over_book`, and the
cause is NOT the in-band filter: running the same reconciliation against
`books_full` gives an identical count. Breaking down 332 such prints on 08-23:

    254 (77%)  print price is INSIDE the best price on the side it consumed
     47        swept past the best price into depth we did not record
     31        at the best price, larger than the level we recorded

The dominant case is liquidity that was posted AND consumed between two 60s
snapshots — invisible to any sampling at this cadence, and unfixable
retroactively. It matters because those prints are systematically the ones that
traded at aggressive prices, i.e. the population our own aggressive quotes would
compete for. Fill rates computed here are therefore a LOWER bound, and the
honest reporting bracket is [matched only, matched + inside-spread prints].

TAPE SIDE CONVENTION
--------------------
Whether the tape's `side` is the taker's or the maker's decides which resting
side a print consumes. Get it backwards and every fill inverts, so it is a
parameter (`tape_is_taker`), not a guess — `tape_side_evidence()` settles it
empirically against best bid/ask, and V1 in the test module pins the answer.
"""

from bisect import bisect_right
from dataclasses import dataclass

import polars as pl

from laminar.score import Level

QUEUE_FRONT = "front"
QUEUE_BACK = "back"
QUEUES = (QUEUE_FRONT, QUEUE_BACK)

# Snapshots land every 60s; allow one missed sample before calling the book
# unknown. A print we cannot place against a book is dropped and counted, never
# matched against a stale guess.
MAX_SNAPSHOT_STALENESS_MS = 120_000


@dataclass(frozen=True)
class ShadowOrder:
    """A resting order we did not place. `side` is book convention ("bid"/"ask"),
    deliberately NOT the tape's BUY/SELL vocabulary — mixing the two is the
    mistake this whole module is arranged to make impossible."""

    token_id: str
    side: str
    price: float
    size: float


@dataclass(frozen=True)
class Fill:
    ts: int
    tx_hash: str
    token_id: str
    side: str
    price: float
    size: float
    queue: str


def consumed_side(tape_side: str, tape_is_taker: bool = True) -> str:
    """Which resting side a print ate. A taker BUY lifts offers, so it consumes
    asks; if the tape reports the MAKER's side instead, that inverts."""
    is_buy = tape_side.upper() == "BUY"
    if not tape_is_taker:
        is_buy = not is_buy
    return "ask" if is_buy else "bid"


def size_ahead(levels: list[Level], side: str, price: float, queue: str) -> float:
    """Displayed size that must be consumed before our order at `price`.

    Better-priced orders always go first (higher bids, lower asks). At our own
    price level, queue position is unobservable — hence the two extremes.
    """
    if queue not in QUEUES:
        raise ValueError(f"queue must be one of {QUEUES}, got {queue!r}")
    if side == "bid":
        ahead = sum(lv.size for lv in levels if lv.price > price)
    elif side == "ask":
        ahead = sum(lv.size for lv in levels if lv.price < price)
    else:
        raise ValueError(f"side must be 'bid' or 'ask', got {side!r}")
    if queue == QUEUE_BACK:
        ahead += sum(lv.size for lv in levels if lv.price == price)
    return ahead


def reaches(side: str, order_price: float, print_price: float) -> bool:
    """Did the taker's price reach ours? A print that ate bids at `print_price`
    means every bid at or above it was reachable, and vice versa for asks."""
    return order_price >= print_price if side == "bid" else order_price <= print_price


def fill_for_order(
    order: ShadowOrder,
    print_price: float,
    print_size: float,
    eaten: str,
    levels: list[Level],
    queue: str,
) -> float:
    """Shares of one print that would have landed on one resting order."""
    if order.side != eaten or not reaches(order.side, order.price, print_price):
        return 0.0
    ahead = size_ahead(levels, order.side, order.price, queue)
    return max(0.0, min(order.size, print_size - ahead))


class BookIndex:
    """Book snapshots, queryable as "the latest one at or before this instant".

    Built once per replay; a print is matched against the book as it stood when
    the print happened, not against a snapshot taken after it.
    """

    def __init__(self, books: pl.DataFrame):
        self._stamps: dict[str, list[int]] = {}
        self._levels: dict[tuple[str, int], tuple[list[Level], list[Level]]] = {}
        if books.is_empty():
            return
        grouped = books.group_by(["token_id", "ts"]).agg(
            pl.col("side"), pl.col("price"), pl.col("size")
        )
        for token_id, ts, sides, prices, sizes in grouped.iter_rows():
            bids = [
                Level(p, z) for s, p, z in zip(sides, prices, sizes) if s == "bid"
            ]
            asks = [
                Level(p, z) for s, p, z in zip(sides, prices, sizes) if s == "ask"
            ]
            self._levels[(token_id, ts)] = (bids, asks)
            self._stamps.setdefault(token_id, []).append(ts)
        for stamps in self._stamps.values():
            stamps.sort()

    def at(
        self, token_id: str, ts: int, max_staleness_ms: int = MAX_SNAPSHOT_STALENESS_MS
    ) -> tuple[list[Level], list[Level], int] | None:
        """(bids, asks, snapshot_ts), or None if no fresh-enough snapshot exists."""
        stamps = self._stamps.get(token_id)
        if not stamps:
            return None
        i = bisect_right(stamps, ts) - 1
        if i < 0:
            return None
        snap = stamps[i]
        if ts - snap > max_staleness_ms:
            return None
        bids, asks = self._levels[(token_id, snap)]
        return bids, asks, snap


def replay(
    orders: dict[str, list[ShadowOrder]],
    trades: pl.DataFrame,
    index: BookIndex,
    queue: str,
    tape_is_taker: bool = True,
) -> tuple[list[Fill], dict[str, int]]:
    """Run the tape past a fixed set of resting orders.

    Orders are STATIC: they neither deplete nor get re-quoted here. Inventory
    and re-quoting are the next layer's business — keeping them out is what
    makes this layer testable against a known answer (see V1).

    Returns (fills, counters). The counters are not decoration: `no_book` is how
    often we refused to match rather than guessing, and it belongs in any result
    that quotes a fill rate.
    """
    fills: list[Fill] = []
    counters = {"prints": 0, "no_book": 0, "no_orders": 0, "matched": 0}
    if trades.is_empty():
        return fills, counters

    for row in trades.sort("ts").iter_rows(named=True):
        counters["prints"] += 1
        token_orders = orders.get(row["token_id"])
        if not token_orders:
            counters["no_orders"] += 1
            continue
        snap = index.at(row["token_id"], row["ts"])
        if snap is None:
            counters["no_book"] += 1
            continue
        bids, asks, _ = snap
        eaten = consumed_side(row["side"], tape_is_taker)
        levels = bids if eaten == "bid" else asks
        hit = False
        for order in token_orders:
            got = fill_for_order(
                order, row["price"], row["size"], eaten, levels, queue
            )
            if got > 0:
                fills.append(
                    Fill(
                        ts=row["ts"],
                        tx_hash=row["tx_hash"],
                        token_id=row["token_id"],
                        side=order.side,
                        price=order.price,
                        size=got,
                        queue=queue,
                    )
                )
                hit = True
        if hit:
            counters["matched"] += 1
    return fills, counters


def book_as_orders(token_id: str, levels: list[Level], side: str) -> list[ShadowOrder]:
    """Turn a book side into the shadow orders that replicate it exactly.

    Only used to construct V1's degenerate case: if we ARE the book, the tape
    must land entirely on us.
    """
    return [ShadowOrder(token_id, side, lv.price, lv.size) for lv in levels]


def v1_reconcile(
    trades: pl.DataFrame,
    index: BookIndex,
    tape_is_taker: bool = True,
    queue: str = QUEUE_FRONT,
    tol: float = 1e-6,
) -> dict[str, int | float]:
    """V1 on real data: be the whole book, and check the tape lands on us.

    The unit tests pin this identity on constructed books; this runs it over
    every print in a real window, where the book is whatever it happened to be.
    A print can legitimately exceed the depth we recorded — snapshots are up to
    60s stale and `books` keeps only in-band levels — so the identity is
    against the OBSERVED eligible depth, not against the print size.

    `over_book` is not a failure; it is the honest count of prints bigger than
    anything we could see, and it bounds how much of the tape the matcher can
    speak to at all.
    """
    out = {"prints": 0, "exact": 0, "mismatch": 0, "over_book": 0, "no_book": 0,
           "worst_abs_err": 0.0}
    for row in trades.iter_rows(named=True):
        out["prints"] += 1
        snap = index.at(row["token_id"], row["ts"])
        if snap is None:
            out["no_book"] += 1
            continue
        bids, asks, _ = snap
        eaten = consumed_side(row["side"], tape_is_taker)
        levels = bids if eaten == "bid" else asks
        eligible = sum(
            lv.size for lv in levels if reaches(eaten, lv.price, row["price"])
        )
        expected = min(row["size"], eligible)
        got = sum(
            fill_for_order(o, row["price"], row["size"], eaten, levels, queue)
            for o in book_as_orders(row["token_id"], levels, eaten)
        )
        err = abs(got - expected)
        out["worst_abs_err"] = max(out["worst_abs_err"], err)
        if err <= tol:
            out["exact"] += 1
        else:
            out["mismatch"] += 1
        if row["size"] > eligible + tol:
            out["over_book"] += 1
    return out


def tape_side_evidence(trades: pl.DataFrame, index: BookIndex) -> dict[str, int]:
    """Cross-tab the tape's `side` against the side the price implies.

    A print at or above the best ask can only have lifted offers; at or below
    the best bid, only hit bids. Counting how the tape labels those two
    populations settles whether `side` is the taker's or the maker's, without
    trusting any documentation.
    """
    counts = {
        "BUY_at_or_above_ask": 0,
        "BUY_at_or_below_bid": 0,
        "SELL_at_or_above_ask": 0,
        "SELL_at_or_below_bid": 0,
        "inside_spread": 0,
        "no_book": 0,
    }
    for row in trades.iter_rows(named=True):
        snap = index.at(row["token_id"], row["ts"])
        if snap is None:
            counts["no_book"] += 1
            continue
        bids, asks, _ = snap
        if not bids or not asks:
            counts["no_book"] += 1
            continue
        best_bid = max(lv.price for lv in bids)
        best_ask = min(lv.price for lv in asks)
        side = row["side"].upper()
        if row["price"] >= best_ask:
            counts[f"{side}_at_or_above_ask"] += 1
        elif row["price"] <= best_bid:
            counts[f"{side}_at_or_below_bid"] += 1
        else:
            counts["inside_spread"] += 1
    return counts
