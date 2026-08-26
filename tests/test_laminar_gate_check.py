"""Intent tests for the daily gate spot-check (`laminar.gate_check`).

Guards the two things this module exists to fix/add over the plain daily
email: (1) the reference-quote size is the pre-registration's fixed 200
shares, not each market's own `min_size` — that gap is the whole reason this
module was written, see its module docstring; (2) the trailing-baseline
comparison and the directional-risk proxy don't fire (or divide by zero) on
too little history/data, since both are meant to be read over several days,
not off one day alone.
"""

import polars as pl
import pytest

from laminar import gate_check as gc
from laminar import score


def _book_row(ts, token, side, price, size, mid, max_spread=4.5, min_size=20.0, daily_rate=200.0):
    return {
        "ts": ts, "token_id": token, "side": side, "price": price, "size": size,
        "midpoint": mid, "max_spread": max_spread, "min_size": min_size, "daily_rate": daily_rate,
    }


def _books_df(rows):
    from laminar.store import BOOK_SCHEMA
    return pl.DataFrame(rows, schema=BOOK_SCHEMA)


def _trade_row(tx, ts, token, side, price, size):
    return {
        "tx_hash": tx, "ts": ts, "condition_id": "cond", "token_id": token,
        "outcome": "Yes", "side": side, "price": price, "size": size, "wallet": "0xW",
    }


def _trades_df(rows):
    from laminar.store import TRADE_SCHEMA
    return pl.DataFrame(rows, schema=TRADE_SCHEMA) if rows else pl.DataFrame(schema=TRADE_SCHEMA)


def test_sample_windows_are_non_overlapping_and_reproducible_per_day():
    """Same day -> same windows on a re-run (seeded by the day string, not
    wall-clock random) — a manual re-run of the daily report shouldn't spot-
    check a different sample than the one already in the email/history."""
    a = gc.sample_windows("2026-08-22")
    b = gc.sample_windows("2026-08-22")
    c = gc.sample_windows("2026-08-23")
    assert a == b
    assert len(a) == gc.N_WINDOWS
    intervals = sorted(a)
    for (s1, e1), (s2, e2) in zip(intervals, intervals[1:]):
        assert e1 <= s2  # non-overlapping
    assert a != c  # different day, not guaranteed to collide


def test_window_yield_uses_the_fixed_200_share_quote_not_min_size():
    """The whole reason this module exists: two pools identical in every way
    except `min_size` must produce the SAME yield, because the reference
    quote size is fixed at REF_SIZE=200, not read from the book's min_size.
    (The plain daily email gets this wrong — see the module docstring.)"""
    day_start = 1_755_820_800_000  # 2026-08-22T00:00:00Z
    ts = day_start + 3 * 3_600_000  # inside window index range, doesn't matter which
    rows_small_min = [
        _book_row(ts + i * 60_000, "tokA", "bid", 0.49, 300.0, 0.50, min_size=20.0)
        for i in range(gc.MIN_SAMPLES_FOR_WINDOW)
    ] + [
        _book_row(ts + i * 60_000, "tokA", "ask", 0.51, 300.0, 0.50, min_size=20.0)
        for i in range(gc.MIN_SAMPLES_FOR_WINDOW)
    ]
    rows_big_min = [dict(r, token_id="tokB", min_size=200.0) for r in rows_small_min]
    books = _books_df(rows_small_min + rows_big_min)
    trades = _trades_df([])

    out = gc.window_pool_metrics(books, trades, day_start, day_start + 24 * 3_600_000)
    by_token = {r["token_id"]: r for r in out}
    assert by_token["tokA"]["yield_hi_pct"] == pytest.approx(by_token["tokB"]["yield_hi_pct"])


def test_window_dropped_below_min_samples_coverage():
    """A window with too few book samples is dropped, not averaged over thin
    coverage and reported as if it were a full reading."""
    day_start = 1_755_820_800_000
    ts = day_start + 3_600_000
    rows = [
        _book_row(ts + i * 60_000, "tokA", "bid", 0.49, 300.0, 0.50)
        for i in range(gc.MIN_SAMPLES_FOR_WINDOW - 1)
    ] + [
        _book_row(ts + i * 60_000, "tokA", "ask", 0.51, 300.0, 0.50)
        for i in range(gc.MIN_SAMPLES_FOR_WINDOW - 1)
    ]
    books = _books_df(rows)
    out = gc.window_pool_metrics(books, _trades_df([]), day_start, day_start + 24 * 3_600_000)
    assert out == []


def test_flag_pool_changes_skips_pool_with_thin_history():
    """Fewer than MIN_HISTORY_DAYS prior days -> no flag, even if today looks
    wildly different — a 1-day baseline is noise, not a signal, and firing on
    it would be exactly the kind of thing this project's honesty conventions
    (score.py, report.py) argue against."""
    today = {"tokA": {"market_slug": "m", "outcome": "Yes", "median_yield_hi_pct": 500.0}}
    history = pl.DataFrame(
        [{"day": "2026-08-21", "token_id": "tokA", "yield_hi_pct": 50.0}],
        schema={"day": pl.Utf8, "token_id": pl.Utf8, "yield_hi_pct": pl.Float64},
    )
    assert gc.flag_pool_changes(today, history, "2026-08-22") == []


def test_flag_pool_changes_fires_past_threshold_with_enough_history():
    today = {"tokA": {"market_slug": "m", "outcome": "Yes", "median_yield_hi_pct": 500.0}}
    history = pl.DataFrame(
        [
            {"day": d, "token_id": "tokA", "yield_hi_pct": 100.0}
            for d in ("2026-08-19", "2026-08-20", "2026-08-21")
        ],
        schema={"day": pl.Utf8, "token_id": pl.Utf8, "yield_hi_pct": pl.Float64},
    )
    flags = gc.flag_pool_changes(today, history, "2026-08-22")
    assert len(flags) == 1
    assert flags[0]["token_id"] == "tokA"
    assert flags[0]["change_pct"] == pytest.approx(400.0)  # 500 vs baseline 100 -> +400%


def test_directional_risk_proxy_sign_matches_trade_direction():
    """A large BUY followed by the mid rising should score POSITIVE
    (continuation); the sign is direction x drift, not just drift — get the
    sign backwards and every 'is this pool getting adversely selected'
    reading in the email is inverted."""
    t0 = 1_755_820_800_000
    books = _books_df([
        _book_row(t0, "tokA", "bid", 0.49, 300.0, 0.50),
        _book_row(t0, "tokA", "ask", 0.51, 300.0, 0.50),
        _book_row(t0 + 10 * 60_000, "tokA", "bid", 0.54, 300.0, 0.55),
        _book_row(t0 + 10 * 60_000, "tokA", "ask", 0.56, 300.0, 0.55),
    ])
    trades = _trades_df([_trade_row("t1", t0 + 60_000, "tokA", "BUY", 0.51, 2000.0)])  # $1020 notional
    out = gc.directional_risk_proxy("2026-08-22", books, trades)
    assert len(out) == 1
    r = out[0]
    assert r["n_large_trades"] == 1
    assert r["signed_drift_sum"] > 0  # BUY, mid rose after -> positive (continuation)


def test_directional_risk_proxy_skips_trades_below_the_large_threshold():
    t0 = 1_755_820_800_000
    books = _books_df([
        _book_row(t0, "tokA", "bid", 0.49, 300.0, 0.50),
        _book_row(t0, "tokA", "ask", 0.51, 300.0, 0.50),
        _book_row(t0 + 10 * 60_000, "tokA", "bid", 0.54, 300.0, 0.55),
        _book_row(t0 + 10 * 60_000, "tokA", "ask", 0.56, 300.0, 0.55),
    ])
    trades = _trades_df([_trade_row("t1", t0 + 60_000, "tokA", "BUY", 0.51, 10.0)])  # $5.10, well below LARGE_USD
    assert gc.directional_risk_proxy("2026-08-22", books, trades) == []
