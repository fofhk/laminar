"""Intent tests for the strategy-observation report.

These guard the two properties that matter for trusting the daily email:
the reference-quote offset never exceeds the reward band (a quote outside
it would silently score zero, making every downstream estimate wrong), and
the anomaly labels (band_breach, pool_pulled, missing) fire exactly on the
conditions the report's prose claims they fire on.
"""

import polars as pl
import pytest

from laminar import report, store


def _write_sample(monkeypatch, tmp_path, ts_ms, rows):
    monkeypatch.setattr(store, "LAMINAR_DIR", tmp_path)
    store.append_books(ts_ms, rows)


def _day_of(ts_ms):
    from datetime import UTC, datetime
    return datetime.fromtimestamp(ts_ms / 1000, UTC).strftime("%Y-%m-%d")


def _row(ts, token, side, price, size, mid, max_spread=4.5, min_size=200.0, daily_rate=100.0):
    return {
        "ts": ts, "token_id": token, "side": side, "price": price, "size": size,
        "midpoint": mid, "max_spread": max_spread, "min_size": min_size, "daily_rate": daily_rate,
    }


def test_reference_offset_never_exceeds_the_band():
    """A quote outside max_spread scores zero (score.py's own contract) — if
    the reference offset ever exceeded the band, every downstream estimate in
    the report would silently be zero instead of flagging the mistake."""
    tight_bid, tight_ask = report._reference_quote(200.0, max_spread=1.4, midpoint=0.50)
    assert abs(tight_ask.price - tight_bid.price) / 2 <= 1.4 / 100.0 + 1e-9

    wide_bid, wide_ask = report._reference_quote(200.0, max_spread=5.0, midpoint=0.50)
    assert wide_ask.price - 0.50 == pytest.approx(report.REF_OFFSET_CAP / 100.0)


def test_observe_flags_band_breach_and_estimates_reward(monkeypatch, tmp_path):
    ts = 1_700_000_000_000
    rows = [
        _row(ts, "tokA", "bid", 0.03, 300.0, 0.045, max_spread=4.5, min_size=200.0, daily_rate=50.0),
        _row(ts, "tokA", "ask", 0.06, 300.0, 0.045, max_spread=4.5, min_size=200.0, daily_rate=50.0),
    ]
    _write_sample(monkeypatch, tmp_path, ts, rows)
    obs = report.observe("2023-11-14")
    assert len(obs) == 1
    o = obs[0]
    assert o.band_breach is True  # midpoint 0.045 < 0.10
    assert o.pool_pulled is False
    assert o.est_usd_hi >= o.est_usd_lo >= 0.0


def test_observe_flags_pool_pulled(monkeypatch, tmp_path):
    ts = 1_700_000_000_000
    rows = [
        _row(ts, "tokB", "bid", 0.49, 300.0, 0.50, daily_rate=0.0),
        _row(ts, "tokB", "ask", 0.51, 300.0, 0.50, daily_rate=0.0),
    ]
    _write_sample(monkeypatch, tmp_path, ts, rows)
    obs = report.observe("2023-11-14")
    assert obs[0].pool_pulled is True
    assert obs[0].est_usd_hi == 0.0  # a zero pool can't pay anything regardless of share


def test_missing_from_watchlist_catches_a_delisted_token(monkeypatch, tmp_path):
    ts = 1_700_000_000_000
    rows = [_row(ts, "tokC", "bid", 0.49, 300.0, 0.50), _row(ts, "tokC", "ask", 0.51, 300.0, 0.50)]
    _write_sample(monkeypatch, tmp_path, ts, rows)
    obs = report.observe("2023-11-14")
    watchlist = {
        "markets": [
            {"market_slug": "seen", "tokens": [{"token_id": "tokC", "outcome": "Yes"}]},
            {"market_slug": "vanished", "tokens": [{"token_id": "tokD", "outcome": "Yes"}]},
        ]
    }
    missing = report.missing_from_watchlist(obs, watchlist)
    assert missing == [("vanished", "tokD")]


def _trade(tx, ts, token, side, price, size):
    return {
        "tx_hash": tx, "ts": ts, "condition_id": "cond", "token_id": token,
        "outcome": "Yes", "side": side, "price": price, "size": size,
    }


def test_fill_touch_counts_only_crossing_prints_on_the_matching_side(monkeypatch, tmp_path):
    """A SELL print above our bid, or a BUY print below our ask, never crossed
    our reference quote and must not be counted — the whole point of using
    real prints instead of a midpoint delta is to NOT count non-events."""
    monkeypatch.setattr(store, "LAMINAR_DIR", tmp_path)
    ts = 1_700_000_000_000
    store.append_books(ts, [
        _row(ts, "tokF", "bid", 0.49, 300.0, 0.50),
        _row(ts, "tokF", "ask", 0.51, 300.0, 0.50),
    ])
    obs = report.observe("2023-11-14")
    assert len(obs) == 1 and obs[0].token_id == "tokF"
    # ref bid/ask at offset 1.0c -> 0.49 / 0.51. Trade `ts` is ms, same as book ts.
    day_ts = ts
    store.append_trades([
        _trade("t1", day_ts, "tokF", "SELL", 0.48, 100.0),  # crosses our bid (<=0.49)
        _trade("t2", day_ts, "tokF", "SELL", 0.499, 999.0),  # does NOT cross (>0.49)
        _trade("t3", day_ts, "tokF", "BUY", 0.52, 50.0),  # crosses our ask (>=0.51)
        _trade("t4", day_ts, "tokF", "BUY", 0.505, 999.0),  # does NOT cross (<0.51)
    ])
    touch = report.fill_touch("2023-11-14", obs)
    assert touch["tokF"] == (100.0, 50.0)


def test_fill_touch_is_zero_with_no_matching_trades(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "LAMINAR_DIR", tmp_path)
    ts = 1_700_000_000_000
    store.append_books(ts, [_row(ts, "tokG", "bid", 0.49, 300.0, 0.50), _row(ts, "tokG", "ask", 0.51, 300.0, 0.50)])
    obs = report.observe("2023-11-14")
    assert report.fill_touch("2023-11-14", obs) == {"tokG": (0.0, 0.0)}


def test_pool_drift_flags_a_large_move_and_ignores_a_small_one(monkeypatch, tmp_path):
    """This exists because a real pool moved 500 -> 200 USDC/day on
    fed-rate-hike-in-2026 within a single day (found 2026-08-14) — the frozen
    watchlist's rate is a snapshot, and every dollar estimate in the report
    must be caught using the LIVE rate, with the drift itself surfaced."""
    monkeypatch.setattr(store, "LAMINAR_DIR", tmp_path)
    ts = 1_700_000_000_000
    store.append_books(ts, [
        _row(ts, "tokBig", "bid", 0.49, 300.0, 0.50, daily_rate=200.0),
        _row(ts, "tokBig", "ask", 0.51, 300.0, 0.50, daily_rate=200.0),
        _row(ts, "tokSmall", "bid", 0.49, 300.0, 0.50, daily_rate=98.0),
        _row(ts, "tokSmall", "ask", 0.51, 300.0, 0.50, daily_rate=98.0),
    ])
    obs = report.observe("2023-11-14")
    watchlist = {
        "markets": [
            {"condition_id": "cBig", "market_slug": "big-move", "daily_rate": 500.0,
             "tokens": [{"token_id": "tokBig", "outcome": "Yes"}]},
            {"condition_id": "cSmall", "market_slug": "small-move", "daily_rate": 100.0,
             "tokens": [{"token_id": "tokSmall", "outcome": "Yes"}]},
        ]
    }
    drift = report.pool_drift(obs, watchlist)
    assert drift == [("big-move", 500.0, 200.0)]  # -60%, over the 30% threshold; small-move (-2%) excluded


def test_categorise_buckets_the_real_watchlist_markets():
    """Categories drive the 'which category is unsafe' conclusion, so a market
    landing in the wrong bucket would corrupt that answer. These are real slugs
    from the frozen watchlist."""
    assert report.categorise("fed-rate-hike-in-2026") == "fed-macro"
    assert report.categorise("will-uk-annual-gdp-growth-in-2026-be-between-1-and-2") == "fed-macro"
    assert report.categorise("will-the-us-invade-iran-before-2027") == "geopolitics"
    assert report.categorise("israel-x-iran-ceasefire-continues-through-december-31") == "geopolitics"
    assert report.categorise("will-jd-vance-win-the-2028-us-presidential-election") == "election"
    assert report.categorise("will-mitch-mcconnell-resign-from-the-senate") == "us-politics"
    assert report.categorise("will-anthropic-ipo-by-october-31-2026") == "corporate"
    assert report.categorise("jack-lowdon-announced-as-next-james-bond-917") == "entertainment"
    assert report.categorise("will-kimi-antonelli-be-the-2026-f1-drivers-champion") == "sports"
    # a chess championship is sport, not an election — 'win-the-20' is greedy
    assert report.categorise("will-gukesh-dommaraju-win-the-2026-world-chess-championship") == "sports"
    assert report.categorise("something-totally-unmatched-xyz") == "other"


def test_detect_shocks_finds_a_midpoint_gap_and_tags_the_category(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "LAMINAR_DIR", tmp_path)
    t0 = 1_786_000_000_000
    t1 = t0 + 40 * 60_000  # 40 min later, beyond the 30-min lookback
    for ts, mid, px in ((t0, 0.50, 0.49), (t1, 0.20, 0.19)):
        store.append_books(ts, [
            _row(ts, "tokS", "bid", px, 300.0, mid),
            _row(ts, "tokS", "ask", px + 0.02, 300.0, mid),
        ])
    day = _day_of(t1)
    obs = report.observe(day)
    wl = {"markets": [{"condition_id": "c1", "market_slug": "will-the-us-invade-iran-before-2027",
                       "daily_rate": 400.0, "tokens": [{"token_id": "tokS", "outcome": "Yes"}]}]}
    obs = report.label_from_watchlist(obs, wl)
    shocks = report.detect_shocks(day, obs, wl)
    mids = [s for s in shocks if s["kind"] == "midpoint"]
    assert len(mids) == 1
    assert mids[0]["before"] == pytest.approx(0.50)
    assert mids[0]["after"] == pytest.approx(0.20)
    assert mids[0]["pct_change"] == pytest.approx(-0.6)
    assert mids[0]["category"] == "geopolitics"


def test_detect_shocks_logs_one_row_per_market_not_per_token(monkeypatch, tmp_path):
    """A binary market's Yes and No books are the same orders seen from both
    sides, so one real event appears on both tokens. Logging both would double
    every category count — and the category comparison is the whole deliverable."""
    monkeypatch.setattr(store, "LAMINAR_DIR", tmp_path)
    t0 = 1_786_000_000_000
    t1 = t0 + 40 * 60_000
    for ts, mid in ((t0, 0.50), (t1, 0.20)):
        for tok, m in (("yesTok", mid), ("noTok", 1 - mid)):
            store.append_books(ts, [
                _row(ts, tok, "bid", m - 0.01, 300.0, m),
                _row(ts, tok, "ask", m + 0.01, 300.0, m),
            ])
    day = _day_of(t1)
    wl = {"markets": [{"condition_id": "c1", "market_slug": "will-the-us-invade-iran-before-2027",
                       "daily_rate": 400.0,
                       "tokens": [{"token_id": "yesTok", "outcome": "Yes"},
                                  {"token_id": "noTok", "outcome": "No"}]}]}
    obs = report.label_from_watchlist(report.observe(day), wl)
    assert len(obs) == 2  # both tokens observed...
    mids = [s for s in report.detect_shocks(day, obs, wl) if s["kind"] == "midpoint"]
    assert len(mids) == 1  # ...but only one shock logged


def test_detect_shocks_ignores_a_small_move(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "LAMINAR_DIR", tmp_path)
    t0 = 1_786_000_000_000
    t1 = t0 + 40 * 60_000
    for ts, mid in ((t0, 0.50), (t1, 0.48)):  # -4%, well under the 25% threshold
        store.append_books(ts, [
            _row(ts, "tokQ", "bid", mid - 0.01, 300.0, mid),
            _row(ts, "tokQ", "ask", mid + 0.01, 300.0, mid),
        ])
    day = _day_of(t1)
    obs = report.observe(day)
    assert [s for s in report.detect_shocks(day, obs, None) if s["kind"] == "midpoint"] == []


def test_shock_ledger_dedupes_a_repeated_detection(monkeypatch, tmp_path):
    """The same shock stays visible in the lookback window for a while; logging
    it once per minute would drown the cross-day category counts that are the
    whole point of the ledger."""
    monkeypatch.setattr(store, "LAMINAR_DIR", tmp_path)
    row = {
        "detected_at": 1_786_000_000_000, "kind": "midpoint", "category": "geopolitics",
        "market_slug": "m", "token_id": "t", "outcome": "Yes", "before": 0.5, "after": 0.2,
        "pct_change": -0.6, "window_minutes": 30.0, "scheduled_event": "", "note": "x",
    }
    store.append_shocks([row])
    store.append_shocks([{**row, "detected_at": row["detected_at"] + 60_000}])
    assert store.load_shocks().height == 1


def test_calendar_flags_fomc_and_cpi_days():
    from datetime import date as d

    from laminar import calendar as cal
    assert "FOMC decision + SEP/dot plot" in cal.events_on(d(2026, 9, 16))
    assert any("CPI release" in e for e in cal.events_on(d(2026, 9, 11)))
    assert cal.events_on(d(2026, 9, 12)) == []
    nxt = cal.next_events(d(2026, 8, 14), limit=2)
    assert nxt[0][0] == d(2026, 9, 11)  # CPI comes before the Sept FOMC


def test_label_from_watchlist_fills_in_slug_and_outcome(monkeypatch, tmp_path):
    ts = 1_700_000_000_000
    rows = [_row(ts, "tokE", "bid", 0.49, 300.0, 0.50), _row(ts, "tokE", "ask", 0.51, 300.0, 0.50)]
    _write_sample(monkeypatch, tmp_path, ts, rows)
    obs = report.observe("2023-11-14")
    watchlist = {"markets": [{"market_slug": "some-market", "tokens": [{"token_id": "tokE", "outcome": "No"}]}]}
    labeled = report.label_from_watchlist(obs, watchlist)
    assert labeled[0].market_slug == "some-market"
    assert labeled[0].outcome == "No"
