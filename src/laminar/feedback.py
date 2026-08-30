"""Turning a live participant's reward statement into a measurement of `D`.

Stage 2 cannot observe the reward denominator: `/book` aggregates size per price
level, so `score.denominator_bounds` returns an interval instead of a number.
That interval is the weakest load-bearing thing in the project, and no amount of
public data narrows it.

Somebody actually being paid does narrow it — completely. Their realised share of
a reward pool is

    share = mine / (mine + D)   and   share = reward_paid / pool

so `D` follows in one line from three quantities a participant already has about
their own account. No order book is involved, which is why the feedback protocol
asks for these three numbers and refuses market data (docs/FEEDBACK_PROTOCOL.md).

This module is the whole arithmetic of that channel. It is deliberately small;
the design work is in the protocol, and the point of putting the arithmetic here
is that CI can recompute a submitted report rather than take its word for it.
"""

VERDICTS = ("inside", "below", "above", "undetermined")


def implied_denominator(mine_qmin: float, reward_paid: float, pool: float) -> float:
    """The competing field's total score, solved from one reward payment.

    `mine_qmin` must be summed over exactly the samples the payment covers, and
    `pool` must be the pool over the same span, or the ratio is between two
    different things.

    The bias is one-directional and worth stating, because it decides how much a
    disagreement is worth: **if the venue does not distribute the full advertised
    pool** — a market that nobody quotes hard enough to earn all of it — then
    `reward_paid / pool` understates the true share, and the `D` returned here is
    too HIGH. So a measurement landing *above* the bracket is weak evidence, and
    could be under-distribution rather than a wrong bound. A measurement landing
    *below* the bracket cannot be explained that way, and falsifies the lower
    bound outright. Treat the two outcomes asymmetrically.
    """
    if mine_qmin <= 0.0:
        raise ValueError("mine_qmin must be positive; a zero score earns no share")
    if reward_paid <= 0.0:
        raise ValueError("reward_paid must be positive; an unpaid epoch says nothing about D")
    if pool <= 0.0:
        raise ValueError("pool must be positive")
    if reward_paid > pool:
        # Not a rounding problem — it means the two numbers cover different
        # spans, so failing loudly beats returning a negative denominator.
        raise ValueError("reward_paid exceeds pool; the two are measured over different windows")
    return mine_qmin * (pool - reward_paid) / reward_paid


def bracket_verdict(value: float, lo: float, hi: float) -> str:
    """Where a realised quantity fell relative to a predicted interval.

    Endpoints count as inside: the bounds are claimed to be reachable, so a
    measurement sitting exactly on one corroborates the bracket rather than
    breaking it.
    """
    if lo > hi:
        raise ValueError(f"inverted interval: lo={lo} > hi={hi}")
    if value < lo:
        return "below"
    if value > hi:
        return "above"
    return "inside"
