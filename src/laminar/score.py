"""Polymarket liquidity-reward scoring — the arithmetic, isolated and testable.

Implements the published formula (docs.polymarket.com/programs/liquidity-rewards):

    S(v, s) = ((v - s) / v)^2 * b       order score = S(v, s) * size

with side totals Q_one / Q_two and, for c = 3.0,

    midpoint in [0.10, 0.90]:  Qmin = max(min(Q1, Q2), max(Q1, Q2) / c)
    midpoint outside:          Qmin = min(Q1, Q2)

Prices are decimals (0..1); `max_spread` is published in CENTS, so spreads are
compared in cents throughout.

The denominator caveat (see the Stage-1 pre-registration): /book aggregates size
per price level, but Qmin is per-maker and non-linear, so the field of competing
makers cannot be decomposed. `book_qmin_bounds` returns the reachable interval
instead of pretending to a point estimate.
"""

from dataclasses import dataclass

C = 3.0  # scaling factor; "currently 3.0 on all markets"
TWO_SIDED_BAND = (0.10, 0.90)  # outside this, Qmin = min(Q1, Q2)


@dataclass(frozen=True)
class Level:
    """One price level: `size` shares resting at `price` (decimal 0..1)."""

    price: float
    size: float


@dataclass(frozen=True)
class RewardParams:
    """Per-market reward configuration, as returned by /sampling-markets."""

    max_spread: float  # cents
    min_size: float  # shares
    daily_rate: float  # USDC/day
    b: float = 1.0  # in-game multiplier; undocumented, see pre-registration


def order_score(params: RewardParams, midpoint: float, level: Level) -> float:
    """Score one resting order. Zero if outside the band or under min_size."""
    if level.size < params.min_size:
        return 0.0
    s = abs(level.price - midpoint) * 100.0  # cents
    if s > params.max_spread:
        return 0.0
    v = params.max_spread
    return ((v - s) / v) ** 2 * params.b * level.size


def side_score(params: RewardParams, midpoint: float, levels: list[Level]) -> float:
    return sum(order_score(params, midpoint, lv) for lv in levels)


def qmin(q_one: float, q_two: float, midpoint: float) -> float:
    """Combine the two side scores into a maker's per-sample score."""
    if TWO_SIDED_BAND[0] <= midpoint <= TWO_SIDED_BAND[1]:
        return max(min(q_one, q_two), max(q_one, q_two) / C)
    return min(q_one, q_two)


def adjusted_midpoint(
    bids: list[Level], asks: list[Level], min_size: float
) -> float | None:
    """Midpoint of the best bid/ask, counting only levels at or above min_size.

    Dust levels are filtered first — that filter is what stops a tiny order at a
    wide price from dragging the midpoint. None if either side has no qualifying
    level.
    """
    qual_bids = [lv.price for lv in bids if lv.size >= min_size]
    qual_asks = [lv.price for lv in asks if lv.size >= min_size]
    if not qual_bids or not qual_asks:
        return None
    return (max(qual_bids) + min(qual_asks)) / 2.0


def denominator_bounds(
    params: RewardParams, midpoint: float, bids: list[Level], asks: list[Level]
) -> tuple[float, float]:
    """Bound the competing field's total Qmin from an aggregated book.

    /book gives size per price level, not per maker, so the partition of the
    book among makers is unknown. Qmin is per-maker and non-linear, so the total
    depends on that partition. Both extremes are reachable and tight:

    max — every maker is balanced, so nothing is lost to min(); the unmatched
          remainder is the only part paying the /c penalty:
              D_max = min(Q1, Q2) + |Q1 - Q2| / c
    min — every maker sits exactly at the c:1 ratio, the worst point of
          qmin(a, b) / (a + b), which bottoms out at 1/(c + 1):
              D_min = (Q1 + Q2) / (c + 1)

    Outside the two-sided band Qmin = min(Q1, Q2) and no partition can do better
    than pairing everything, so both bounds collapse onto min(Q1, Q2).
    """
    q_one = side_score(params, midpoint, bids)
    q_two = side_score(params, midpoint, asks)
    if not (TWO_SIDED_BAND[0] <= midpoint <= TWO_SIDED_BAND[1]):
        return min(q_one, q_two), min(q_one, q_two)
    d_max = min(q_one, q_two) + abs(q_one - q_two) / C
    d_min = (q_one + q_two) / (C + 1.0)
    return min(d_min, d_max), d_max


def payout_bounds(
    params: RewardParams,
    midpoint: float,
    my_bids: list[Level],
    my_asks: list[Level],
    book_bids: list[Level],
    book_asks: list[Level],
) -> tuple[float, float]:
    """Per-sample USDC bounds for our own quote, given the rest of the book.

    `book_*` must EXCLUDE our own orders. The per-sample pool is the daily rate
    spread over the epoch's samples — left to the caller to divide, since the
    epoch length (1,440 vs 10,080 samples) is unresolved; this returns the
    share of the pool, in [0, 1], as (lower, upper).
    """
    mine = qmin(
        side_score(params, midpoint, my_bids),
        side_score(params, midpoint, my_asks),
        midpoint,
    )
    if mine <= 0.0:
        return 0.0, 0.0
    d_min, d_max = denominator_bounds(params, midpoint, book_bids, book_asks)
    # Our share is smallest when the competing field scores most.
    return mine / (mine + d_max), mine / (mine + d_min)
