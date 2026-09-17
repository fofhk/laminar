"""Intent tests for V2 book-increment reconciliation (`laminar.reconcile`).

V2's whole output is a classification, so the tests pin one constructed case per
class — consume-only, resupplied, cancelled, and unrecorded — plus the boundary
rules that decide which interval a print belongs to. The classifications carry
the finding, so a bug that shifted a case between classes would change the
conclusion, not merely a number.
"""

import polars as pl
import pytest

from laminar import reconcile
from laminar.store import BOOK_SCHEMA, TRADE_SCHEMA

T0 = 1_787_000_000_000
T1 = T0 + 60_000


def _book_row(ts, token, side, price, size):
    return {
        "ts": ts, "token_id": token, "side": side, "price": price, "size": size,
        "midpoint": 0.50, "max_spread": 4.5, "min_size": 20.0, "daily_rate": 200.0,
    }


def _books(rows):
    return pl.DataFrame(rows, schema=BOOK_SCHEMA)


def _trade(tx, ts, token, side, price, size):
    return {
        "tx_hash": tx, "ts": ts, "condition_id": "cond", "token_id": token,
        "outcome": "Yes", "side": side, "price": price, "size": size, "wallet": "0xW",
    }


def _trades(rows):
    return pl.DataFrame(rows, schema=TRADE_SCHEMA)


def _one(books, trades):
    obs = reconcile.observations(books, trades)
    assert len(obs) == 1
    return obs[0]


# ------------------------------------------------------------- classification


def test_depth_consumed_at_the_touch_is_accounted():
    """The consume-only case: the top bid is printed and is gone next snapshot."""
    books = _books([
        _book_row(T0, "tok", "bid", 0.49, 100.0),
        _book_row(T0, "tok", "bid", 0.48, 200.0),
        _book_row(T1, "tok", "bid", 0.48, 200.0),
    ])
    trades = _trades([_trade("t1", T0 + 1_000, "tok", "SELL", 0.49, 100.0)])

    o = _one(books, trades)

    assert o.side == "bid"
    assert o.consumed == pytest.approx(100.0)
    assert o.drawdown == pytest.approx(100.0)
    assert o.klass == reconcile.ACCOUNTED


def test_depth_replaced_within_the_minute_is_under():
    """The resupply case: same print, but the level is back next snapshot. The
    static size-ahead assumption now describes liquidity that the tape already
    consumed and someone else re-posted."""
    books = _books([
        _book_row(T0, "tok", "bid", 0.49, 100.0),
        _book_row(T0, "tok", "bid", 0.48, 200.0),
        _book_row(T1, "tok", "bid", 0.49, 100.0),
        _book_row(T1, "tok", "bid", 0.48, 200.0),
    ])
    trades = _trades([_trade("t1", T0 + 1_000, "tok", "SELL", 0.49, 100.0)])

    o = _one(books, trades)

    assert o.drawdown == pytest.approx(0.0)
    assert o.klass == reconcile.UNDER


def test_depth_cancelled_beyond_the_print_is_over():
    """The case open question 1 turns on: only 50 shares printed, but the whole
    100-share touch was cancelled. A back-of-queue order would have been promoted
    for free — so back-of-queue is not an upper bound on the loss."""
    books = _books([
        _book_row(T0, "tok", "bid", 0.49, 100.0),
        _book_row(T0, "tok", "bid", 0.48, 200.0),
        _book_row(T1, "tok", "bid", 0.48, 200.0),
    ])
    trades = _trades([_trade("t1", T0 + 1_000, "tok", "SELL", 0.49, 50.0)])

    o = _one(books, trades)

    assert o.consumed == pytest.approx(50.0)
    assert o.drawdown == pytest.approx(100.0)
    assert o.klass == reconcile.OVER


def test_a_print_at_an_unrecorded_price_is_no_depth():
    """A sell above the best bid hit liquidity that was never in any snapshot.
    That is the 60-second blind spot, and it is counted separately rather than
    scored as a reconciliation failure."""
    books = _books([
        _book_row(T0, "tok", "bid", 0.49, 100.0),
        _book_row(T1, "tok", "bid", 0.49, 100.0),
    ])
    trades = _trades([_trade("t1", T0 + 1_000, "tok", "SELL", 0.50, 30.0)])

    o = _one(books, trades)

    assert o.depth_before == 0.0
    assert o.klass == reconcile.NO_DEPTH


# ------------------------------------------------------------- side handling


def test_a_buy_print_reconciles_the_ask_side():
    """Mirror of the bid case, and the guard that the tape's taker direction is
    applied: a BUY consumes offers and must never be booked against bids."""
    books = _books([
        _book_row(T0, "tok", "ask", 0.51, 100.0),
        _book_row(T0, "tok", "ask", 0.52, 200.0),
        _book_row(T1, "tok", "ask", 0.51, 100.0),
    ])
    trades = _trades([_trade("t1", T0 + 1_000, "tok", "BUY", 0.52, 200.0)])

    o = _one(books, trades)

    assert o.side == "ask"
    assert o.deepest_price == pytest.approx(0.52)
    assert o.drawdown == pytest.approx(200.0)
    assert o.klass == reconcile.ACCOUNTED


def test_a_sweep_uses_the_deepest_price_for_the_whole_side():
    """Several prints at different levels in one interval are one sweep: depth is
    measured down to the last level reached and consumed is their sum."""
    books = _books([
        _book_row(T0, "tok", "bid", 0.49, 100.0),
        _book_row(T0, "tok", "bid", 0.48, 200.0),
        _book_row(T1, "tok", "bid", 0.48, 100.0),
    ])
    trades = _trades([
        _trade("t1", T0 + 1_000, "tok", "SELL", 0.49, 100.0),
        _trade("t2", T0 + 2_000, "tok", "SELL", 0.48, 100.0),
    ])

    o = _one(books, trades)

    assert o.deepest_price == pytest.approx(0.48)
    assert o.consumed == pytest.approx(200.0)
    assert o.depth_before == pytest.approx(300.0)
    assert o.drawdown == pytest.approx(200.0)
    assert o.klass == reconcile.ACCOUNTED


# ------------------------------------------------------------- interval rules


def test_prints_are_assigned_to_one_interval_only():
    """Boundaries are half-open (tb, ta]: a print at the opening timestamp was
    already there when the interval began, and counting it twice would inflate
    consumed. The two intervals here get one print each."""
    books = _books([
        _book_row(T0, "tok", "bid", 0.49, 100.0),
        _book_row(T1, "tok", "bid", 0.49, 100.0),
        _book_row(T0 + 120_000, "tok", "bid", 0.49, 100.0),
    ])
    trades = _trades([
        _trade("t1", T0, "tok", "SELL", 0.49, 10.0),          # at tb -> excluded
        _trade("t2", T1, "tok", "SELL", 0.49, 20.0),          # at ta -> first interval
    ])

    obs = reconcile.observations(books, trades)

    assert len(obs) == 1
    assert obs[0].consumed == pytest.approx(20.0)


def test_a_stale_gap_is_not_reconciled():
    """A halt or a missed run makes the after-book uninformative about what the
    print consumed; the interval is dropped rather than guessed at."""
    books = _books([
        _book_row(T0, "tok", "bid", 0.49, 100.0),
        _book_row(T0 + reconcile.MAX_GAP_MS + 1, "tok", "bid", 0.49, 100.0),
    ])
    trades = _trades([_trade("t1", T0 + 1_000, "tok", "SELL", 0.49, 50.0)])

    assert reconcile.observations(books, trades) == []


def test_a_token_with_no_prints_produces_nothing():
    books = _books([
        _book_row(T0, "tok", "bid", 0.49, 100.0),
        _book_row(T1, "tok", "bid", 0.49, 100.0),
    ])
    assert reconcile.observations(books, _trades([])) == []


# ------------------------------------------------------------- summary


def test_summary_counts_and_recovery():
    books = _books([
        _book_row(T0, "tok", "bid", 0.49, 100.0),
        _book_row(T0, "tok", "bid", 0.48, 200.0),
        _book_row(T1, "tok", "bid", 0.48, 200.0),
    ])
    trades = _trades([_trade("t1", T0 + 1_000, "tok", "SELL", 0.49, 100.0)])

    s = reconcile.summarize(reconcile.observations(books, trades))

    assert s["n"] == 1
    assert s["counts"][reconcile.ACCOUNTED] == 1
    assert s["recovery"] == pytest.approx(1.0)


def test_empty_summary_is_well_defined():
    s = reconcile.summarize([])
    assert s["n"] == 0
    assert s["recovery"] == 0.0
