"""Intent tests for the reward arithmetic.

These encode WHY the formula matters, not just what it computes: the quadratic
term is what makes distance dominate size, and the c=3.0 term is what makes
lopsided quoting dead weight. If either property is lost, sizing decisions built
on this module become wrong in the same direction — too confident.
"""

import pytest

from laminar.score import (  # noqa: I001
    C,
    Level,
    RewardParams,
    adjusted_midpoint,
    denominator_bounds,
    order_score,
    payout_bounds,
    qmin,
    side_score,
)

P = RewardParams(max_spread=3.0, min_size=200.0, daily_rate=100.0)


def test_distance_beats_size_quadratically():
    """4x the size is needed to offset moving 1c to 2c from the midpoint.

    This is the property that makes 'just post more' a losing answer to a
    tighter competitor, and it is the reason the strategy must quote close
    rather than large.
    """
    tight = order_score(P, 0.50, Level(0.49, 200.0))  # 1c out, 200 shares
    wide = order_score(P, 0.50, Level(0.48, 400.0))  # 2c out, 2x the size
    assert tight > wide
    matched = order_score(P, 0.50, Level(0.48, 800.0))  # 2c out, 4x the size
    assert matched == pytest.approx(tight, rel=1e-9)


def test_below_min_size_scores_zero_not_a_little():
    """min_size is a cliff, not a taper — 199 shares earn nothing at all."""
    assert order_score(P, 0.50, Level(0.49, 199.0)) == 0.0
    assert order_score(P, 0.50, Level(0.49, 200.0)) > 0.0


def test_the_band_edge_itself_pays_nothing():
    """At s == max_spread the quadratic vanishes, so quoting on the edge of the
    band is indistinguishable from not quoting. The usable band is open, not
    closed — a strategy that parks at max_spread earns exactly zero."""
    assert order_score(P, 0.50, Level(0.47, 1000.0)) == 0.0  # exactly 3c out
    assert order_score(P, 0.50, Level(0.46, 1000.0)) == 0.0  # beyond
    assert order_score(P, 0.50, Level(0.475, 1000.0)) > 0.0  # 2.5c out


def test_lopsided_quoting_beyond_3to1_is_dead_weight():
    """400/100 scores the same as 400/133: everything past the 3:1 ratio is
    capital that earns nothing. Sizing that ignores this over-quotes one side."""
    assert qmin(400.0, 100.0, 0.50) == pytest.approx(400.0 / C)
    assert qmin(400.0, 133.4, 0.50) == pytest.approx(133.4)


def test_one_sided_scores_a_third_inside_the_band():
    assert qmin(300.0, 0.0, 0.50) == pytest.approx(100.0)


def test_one_sided_scores_zero_outside_the_band():
    """Below 0.10 / above 0.90 the /c relief disappears entirely — a strategy
    that drifts one-sided in a tail market stops earning, silently."""
    assert qmin(300.0, 0.0, 0.05) == 0.0
    assert qmin(300.0, 0.0, 0.95) == 0.0


def test_adjusted_midpoint_ignores_dust():
    """A tiny far-away order must not drag the midpoint — that filter is the
    book's defence against a cheap midpoint-pinning attack."""
    bids = [Level(0.10, 5.0), Level(0.49, 500.0)]
    asks = [Level(0.90, 5.0), Level(0.51, 500.0)]
    assert adjusted_midpoint(bids, asks, 200.0) == pytest.approx(0.50)


def test_adjusted_midpoint_none_when_a_side_is_all_dust():
    assert adjusted_midpoint([Level(0.49, 5.0)], [Level(0.51, 500.0)], 200.0) is None


def test_denominator_bounds_bracket_the_single_maker_case():
    """Whatever the true partition is, it must lie inside the bounds. The
    one-maker reading is one legal partition, so it is a direct check that the
    bracket is not inverted — if it ever is, every payout estimate is wrong."""
    bids = [Level(0.49, 500.0), Level(0.48, 800.0)]
    asks = [Level(0.51, 500.0), Level(0.52, 300.0)]
    lo, hi = denominator_bounds(P, 0.50, bids, asks)
    one_maker = qmin(
        side_score(P, 0.50, bids), side_score(P, 0.50, asks), 0.50
    )
    assert 0 < lo <= one_maker <= hi


def test_balanced_book_pins_the_bound_gap_at_2x():
    """The Stage-1 abandon gate trips if the bounds span more than 3x. On a
    balanced book the gap is exactly 2x whatever the level count, so the gate
    is about how LOPSIDED real books are, not about book depth."""
    for asks in ([Level(0.51, 500.0)], [Level(0.51, 300.0), Level(0.52, 200.0)]):
        bids = [Level(1.0 - lv.price, lv.size) for lv in asks]
        lo, hi = denominator_bounds(P, 0.50, bids, asks)
        assert hi / lo == pytest.approx(2.0)


def test_outside_the_band_the_bounds_collapse():
    """Below 0.10 no partition helps: Qmin = min(Q1, Q2) for everyone, so the
    denominator is known exactly and the model has no ambiguity to report."""
    bids = [Level(0.04, 500.0)]
    asks = [Level(0.06, 800.0)]
    lo, hi = denominator_bounds(P, 0.05, bids, asks)
    assert lo == hi


def test_payout_share_shrinks_as_competition_scores_more():
    mine_b, mine_a = [Level(0.49, 500.0)], [Level(0.51, 500.0)]
    thin = payout_bounds(P, 0.50, mine_b, mine_a, [Level(0.49, 200.0)], [Level(0.51, 200.0)])
    thick = payout_bounds(P, 0.50, mine_b, mine_a, [Level(0.49, 5000.0)], [Level(0.51, 5000.0)])
    assert thick[1] < thin[0]


def test_unqualifying_quote_pays_nothing():
    """Our own quote under min_size earns zero regardless of how thin the book
    is — the floor applies to us too, which is what sets the ~200 USDC/market
    minimum working capital."""
    assert payout_bounds(
        P, 0.50, [Level(0.49, 10.0)], [Level(0.51, 10.0)], [], []
    ) == (0.0, 0.0)
