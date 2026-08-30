"""Why these matter: this arithmetic is the only thing standing between a
submitted claim and CI accepting it, so a silent wrong answer here would let a
report assert a conclusion its own inputs do not support."""

import pytest

from laminar.feedback import bracket_verdict, implied_denominator


def test_implied_denominator_inverts_the_share_formula():
    # Constructed backwards from share = mine/(mine+D): with mine=100 and
    # D=300 the share is 0.25, so a 1,000 pool pays 250.
    assert implied_denominator(100.0, 250.0, 1000.0) == pytest.approx(300.0)


def test_taking_the_whole_pool_means_no_competing_field():
    # Being paid the entire pool can only mean nobody else qualified. Anything
    # other than zero here would invent competitors that provably were not there.
    assert implied_denominator(100.0, 500.0, 500.0) == 0.0


def test_payment_larger_than_pool_is_refused_not_clamped():
    # A negative denominator is meaningless, and clamping it to zero would hide
    # the actual fault: the reporter summed the two over different windows.
    with pytest.raises(ValueError, match="different windows"):
        implied_denominator(100.0, 600.0, 500.0)


@pytest.mark.parametrize("mine,paid,pool", [(0.0, 10.0, 100.0), (10.0, 0.0, 100.0)])
def test_degenerate_inputs_raise_rather_than_return_a_number(mine, paid, pool):
    # An unpaid epoch is not evidence that D is huge — it is no evidence at all.
    # Returning a finite D would smuggle a non-measurement into the dataset.
    with pytest.raises(ValueError):
        implied_denominator(mine, paid, pool)


def test_verdict_treats_the_endpoints_as_corroboration():
    # denominator_bounds documents both corners as *reachable*, so landing on one
    # is the bracket working, not the bracket failing.
    assert bracket_verdict(2900.0, 2900.0, 5100.0) == "inside"
    assert bracket_verdict(5100.0, 2900.0, 5100.0) == "inside"


def test_verdict_distinguishes_the_two_directions():
    # The directions carry different evidential weight (see implied_denominator's
    # docstring), so collapsing them to a single "outside" would lose the point.
    assert bracket_verdict(2899.0, 2900.0, 5100.0) == "below"
    assert bracket_verdict(5101.0, 2900.0, 5100.0) == "above"


def test_inverted_interval_is_a_caller_bug():
    with pytest.raises(ValueError, match="inverted interval"):
        bracket_verdict(1.0, 10.0, 5.0)
