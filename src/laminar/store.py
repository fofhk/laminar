"""Parquet store for Laminar's order-book samples.

Same pattern as `farseer.data.store` (polars + append with dedupe-on-key) but a
different shape — book snapshots, not OHLCV bars — so it is a sibling, not a
reuse of that module's schemas.

Layout under DATA_DIR/laminar:
  markets/<YYYY-MM-DD>.parquet       hourly snapshot of reward params per market
  books/<YYYY-MM-DD>/<HH>.parquet    per-minute book levels inside the reward band
  trades/<YYYY-MM-DD>.parquet        real executed trades (data-api.polymarket.com), the
                                      public trade tape used for the fill-touch proxy
  activity/<YYYY-MM-DD>.parquet       one wallet's full own trade history (data-api
                                      /activity), pulled on demand per wallet — the input
                                      to consistent-winner / consistent-loser classification
  competitiveness/<YYYY-MM-DD>.parquet   Polymarket's own `market_competitiveness` index
                                      per watchlist market (clob.rewards_market), collected
                                      to compare against our own denominator-bound model —
                                      not yet validated, tracked in parallel
  gate_windows.parquet                one file, upserted by day: `gate_check`'s daily
                                      3x2h spot-check, one row per (day, window_idx, token)
  gate_risk.parquet                   one file, upserted by day: `gate_check`'s directional-
                                      risk proxy, one row per (day, token)

Books partition by HOUR, not day. A sample is ~1.1k rows, so a day is ~1.6M —
and since every append rewrites its whole partition to dedupe, a daily file
would have the collector rewriting a million-plus rows every minute by evening.
Hourly caps that at ~67k.
"""

from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from farseer.config import DATA_DIR

LAMINAR_DIR = DATA_DIR / "laminar"

BOOK_SCHEMA = {
    "ts": pl.Int64,  # sample time, ms UTC
    "token_id": pl.Utf8,
    "side": pl.Utf8,  # "bid" | "ask"
    "price": pl.Float64,
    "size": pl.Float64,
    "midpoint": pl.Float64,  # size-adjusted, at sample time
    "max_spread": pl.Float64,  # cents, as configured then
    "min_size": pl.Float64,
    "daily_rate": pl.Float64,
}

TRADE_SCHEMA = {
    "tx_hash": pl.Utf8,
    "ts": pl.Int64,  # trade timestamp, ms UTC
    "condition_id": pl.Utf8,
    "token_id": pl.Utf8,
    "outcome": pl.Utf8,
    "side": pl.Utf8,  # taker's side: "BUY" | "SELL" — see clob.trades docstring
    "price": pl.Float64,
    "size": pl.Float64,
    "wallet": pl.Utf8,  # data-api `proxyWallet` — WHO traded. Added 2026-08-19;
    # rows written before that date carry null, so any wallet-conditioned
    # analysis must start its window at the first day this is populated.
}

ACTIVITY_SCHEMA = {
    "wallet": pl.Utf8,
    "ts": pl.Int64,  # ms UTC
    "tx_hash": pl.Utf8,
    "condition_id": pl.Utf8,
    "token_id": pl.Utf8,
    "outcome": pl.Utf8,
    "outcome_index": pl.Int64,
    "side": pl.Utf8,  # this wallet's OWN side — NOT the taker/maker flag that
    # TRADE_SCHEMA.side carries. The two columns are not comparable.
    "price": pl.Float64,
    "size": pl.Float64,  # shares
    "usdc_size": pl.Float64,  # dollars actually moved — use this for PnL, not
    # price*size, which the API lets disagree with it on partial fills
    "market_slug": pl.Utf8,
    "title": pl.Utf8,
}

SHOCK_SCHEMA = {
    "detected_at": pl.Int64,  # ms UTC, when the collector first saw it
    "kind": pl.Utf8,  # "pool_rate" | "midpoint" | "book_depth" — what collapsed
    "category": pl.Utf8,  # market taxonomy: fed / geopolitics / election / entertainment / ...
    "market_slug": pl.Utf8,
    "token_id": pl.Utf8,  # "" for market-level shocks (pool_rate)
    "outcome": pl.Utf8,
    "before": pl.Float64,
    "after": pl.Float64,
    "pct_change": pl.Float64,
    "window_minutes": pl.Float64,  # over how long the move happened
    "scheduled_event": pl.Utf8,  # FOMC/CPI on that day, "" if none — expected vs surprise
    "note": pl.Utf8,
}

METRICS_HISTORY_SCHEMA = {
    "day": pl.Utf8,  # one row per UTC day, upserted — the daily report's own trend log,
                      # so the day-7/14/21 checkpoints read a time series instead of
                      # re-deriving it from a stack of individual emails
    "coverage": pl.Float64,
    "n_observed": pl.Int64,
    "median_denom_ratio": pl.Float64,
    "max_denom_ratio": pl.Float64,
    "est_usd_lo": pl.Float64,
    "est_usd_hi": pl.Float64,
    "missing_count": pl.Int64,
    "pool_drift_count": pl.Int64,
}

GATE_WINDOW_SCHEMA = {
    "day": pl.Utf8,
    "window_idx": pl.Int64,  # which of the day's sampled 2h windows, 0..n-1
    "window_start": pl.Int64,  # ms UTC
    "window_end": pl.Int64,
    "token_id": pl.Utf8,
    "market_slug": pl.Utf8,
    "outcome": pl.Utf8,  # Yes/No — the two tokens of one market share market_slug, this
                          # is what tells them apart in a per-token table
    "n_samples": pl.Int64,  # book snapshots inside the window — a coverage check
    "yield_lo_pct": pl.Float64,  # annualised, FIXED 200-share reference quote (not min_size)
    "yield_hi_pct": pl.Float64,  # ... upper bound — the pre-registration's actual gate test
    "liquidity": pl.Float64,  # mean(Q_one + Q_two), competing book excl. our hypothetical order
    "volume_count": pl.Int64,
    "volume_notional": pl.Float64,
}

GATE_RISK_SCHEMA = {
    "day": pl.Utf8,
    "token_id": pl.Utf8,
    "market_slug": pl.Utf8,
    "outcome": pl.Utf8,
    "n_large_trades": pl.Int64,  # notional >= gate_check.LARGE_USD, this UTC day
    "signed_drift_sum": pl.Float64,  # sum, not mean — a multi-day rollup is then a plain sum
}

COMPETITIVENESS_SCHEMA = {
    "ts": pl.Int64,
    "condition_id": pl.Utf8,
    "market_slug": pl.Utf8,
    "competitiveness": pl.Float64,
    "config_rate_per_day": pl.Float64,
}

LEAD_SCHEMA = {
    "msg_id": pl.Int64,  # PolyBeats Telegram message id — monotonic, the dedupe key
    "posted_at": pl.Utf8,  # ISO8601 UTC, straight from the channel
    "fetched_at": pl.Int64,  # ms UTC, when we scraped it — publication lag is measurable
    "headline": pl.Utf8,
    "body": pl.Utf8,  # verbatim, minus subscription boilerplate — the fallback if the
                      # channel's format shifts and the extractors below go stale
    "is_smart_money": pl.Boolean,  # False = ordinary market commentary, no position called
    "n_accounts": pl.Int64,
    "direction": pl.Utf8,  # 是 / 否 / "" when the post does not say
    "stake_usd": pl.Float64,  # 0.0 when withheld (the free tier usually withholds it)
    "avg_entry_pct": pl.Float64,
    "current_pct": pl.Float64,
    "event_slug": pl.Utf8,  # "" => not resolvable to a pool, see `trackable`
    "market_slug": pl.Utf8,  # only the richest post format carries this
    "wallets": pl.Utf8,  # comma-joined 0x addresses, when the post links a profile
    "best_win_rate": pl.Float64,  # highest stated per-account settled win rate
    "sector_pnl_usd": pl.Float64,  # summed across the accounts named in the post
    "trackable": pl.Boolean,  # has an event_slug, i.e. a pool tracker can follow it
    "url": pl.Utf8,
}

LEAD_MARKET_SCHEMA = {
    "msg_id": pl.Int64,  # the lead this market was resolved from
    "event_slug": pl.Utf8,
    "condition_id": pl.Utf8,
    "market_slug": pl.Utf8,
    "question": pl.Utf8,
    "token_id": pl.Utf8,
    "outcome": pl.Utf8,
    "end_date_iso": pl.Utf8,
    "resolved_at": pl.Int64,  # ms UTC, when we did the event->markets lookup
    "track_until": pl.Int64,  # ms UTC; snapshots stop here so the tracked set stays bounded
    "closed": pl.Boolean,  # already settled at resolve time — recorded, never snapshotted
    "named_by_lead": pl.Boolean,  # the post linked THIS market, not just the event
    "volume_24hr": pl.Float64,  # at resolve time, the basis for the fan-out cap
}

LEAD_TRACK_SCHEMA = {
    "ts": pl.Int64,
    "msg_id": pl.Int64,
    "condition_id": pl.Utf8,
    "token_id": pl.Utf8,
    "outcome": pl.Utf8,
    "hours_since_lead": pl.Float64,  # negative is impossible; 0 = first snapshot after the post
    "best_bid": pl.Float64,
    "best_ask": pl.Float64,
    "midpoint": pl.Float64,
    "spread": pl.Float64,
    "band_depth": pl.Float64,  # resting size inside the reward band (0 if no reward config)
    "book_depth": pl.Float64,  # total resting size on this side, band or not
    "n_levels": pl.Int64,
    "daily_rate": pl.Float64,  # the pool — the thing that cannot be backfilled
    "max_spread": pl.Float64,
    "min_size": pl.Float64,
    "competitiveness": pl.Float64,  # Polymarket's own index, 0 when it has no reward config
}

MARKET_SCHEMA = {
    "ts": pl.Int64,
    "condition_id": pl.Utf8,
    "market_slug": pl.Utf8,
    "question": pl.Utf8,
    "end_date_iso": pl.Utf8,
    "token_id": pl.Utf8,
    "outcome": pl.Utf8,
    "price": pl.Float64,
    "max_spread": pl.Float64,
    "min_size": pl.Float64,
    "daily_rate": pl.Float64,
    "minimum_tick_size": pl.Float64,
    "neg_risk": pl.Boolean,
}


def _day(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, UTC).strftime("%Y-%m-%d")


def _hour(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, UTC).strftime("%H")


def _append(path: Path, rows: list[dict], schema: dict, key: list[str] | None = None) -> int:
    """Append rows, deduped on `key` (a re-run of the same sample or an
    overlapping trade-tape poll must not double-count when scoring)."""
    if not rows:
        return 0
    new = pl.DataFrame(rows, schema=schema)
    if path.exists():
        # `diagonal` so a schema that GAINED a column still appends onto files
        # written before that column existed — old rows take null rather than
        # the whole append dying on a shape mismatch. Only this helper needs it;
        # the other append_* functions' schemas have not been widened.
        new = pl.concat([pl.read_parquet(path), new], how="diagonal")
    key = key or [c for c in ("ts", "token_id", "side", "price") if c in schema]
    merged = new.unique(subset=key, keep="last").sort(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    merged.write_parquet(path)
    return len(rows)


def append_books(ts_ms: int, rows: list[dict]) -> int:
    path = LAMINAR_DIR / "books" / _day(ts_ms) / f"{_hour(ts_ms)}.parquet"
    return _append(path, rows, BOOK_SCHEMA)


def append_books_full(ts_ms: int, rows: list[dict]) -> int:
    """Same samples as `append_books`, but every level — not just the in-band
    ones. Added 2026-08-25 for the shadow study: pricing an inventory flatten
    means knowing how deep the book is BEYOND the reward band, which the
    band-filtered series cannot answer. Deliberately a separate path so a fault
    here cannot touch the series the Stage-1 gates are adjudicated on.
    """
    path = LAMINAR_DIR / "books_full" / _day(ts_ms) / f"{_hour(ts_ms)}.parquet"
    return _append(path, rows, BOOK_SCHEMA)


def append_markets(ts_ms: int, rows: list[dict]) -> int:
    return _append(
        LAMINAR_DIR / "markets" / f"{_day(ts_ms)}.parquet", rows, MARKET_SCHEMA
    )


def append_trades(rows: list[dict]) -> int:
    """Rows carry their own `ts` (the trade's real timestamp), which may span
    more than one UTC day in a single poll near midnight — split per day."""
    if not rows:
        return 0
    by_day: dict[str, list[dict]] = {}
    for r in rows:
        by_day.setdefault(_day(r["ts"]), []).append(r)
    # `wallet` is in the key because one tx can produce several fills identical
    # in every other field; without it the dedupe silently drops a row and
    # under-counts the tape.
    key = ["tx_hash", "token_id", "price", "size", "wallet"]
    return sum(
        _append(LAMINAR_DIR / "trades" / f"{day}.parquet", day_rows, TRADE_SCHEMA, key)
        for day, day_rows in by_day.items()
    )


def load_books(day: str) -> pl.DataFrame:
    """All samples for one UTC day, hourly partitions concatenated."""
    parts = sorted((LAMINAR_DIR / "books" / day).glob("*.parquet"))
    if not parts:
        return pl.DataFrame(schema=BOOK_SCHEMA)
    return pl.concat([pl.read_parquet(p) for p in parts]).sort("ts")


def load_books_full(day: str) -> pl.DataFrame:
    """Full-depth counterpart of `load_books`. Empty before 2026-08-25."""
    parts = sorted((LAMINAR_DIR / "books_full" / day).glob("*.parquet"))
    if not parts:
        return pl.DataFrame(schema=BOOK_SCHEMA)
    return pl.concat([pl.read_parquet(p) for p in parts]).sort("ts")


def append_activity(rows: list[dict]) -> int:
    """One wallet's own trade history, pulled per wallet on demand.

    Deliberately NOT on the hourly job. Unlike the trade tape — which only
    exists for moments we happened to be polling — /activity is retrospective:
    a wallet first noticed next month can still be backfilled to its first
    trade. Nothing is lost by collecting it late, so nothing is spent
    collecting it early.
    """
    if not rows:
        return 0
    by_day: dict[str, list[dict]] = {}
    for r in rows:
        by_day.setdefault(_day(r["ts"]), []).append(r)
    key = ["tx_hash", "wallet", "token_id", "price", "size"]
    return sum(
        _append(LAMINAR_DIR / "activity" / f"{day}.parquet", day_rows, ACTIVITY_SCHEMA, key)
        for day, day_rows in by_day.items()
    )


def load_activity(day: str) -> pl.DataFrame:
    path = LAMINAR_DIR / "activity" / f"{day}.parquet"
    return pl.read_parquet(path) if path.exists() else pl.DataFrame(schema=ACTIVITY_SCHEMA)


def load_trades(day: str) -> pl.DataFrame:
    path = LAMINAR_DIR / "trades" / f"{day}.parquet"
    return pl.read_parquet(path) if path.exists() else pl.DataFrame(schema=TRADE_SCHEMA)


def append_competitiveness(ts_ms: int, rows: list[dict]) -> int:
    return _append(
        LAMINAR_DIR / "competitiveness" / f"{_day(ts_ms)}.parquet", rows, COMPETITIVENESS_SCHEMA,
        key=["ts", "condition_id"],
    )


def load_competitiveness(day: str) -> pl.DataFrame:
    path = LAMINAR_DIR / "competitiveness" / f"{day}.parquet"
    return pl.read_parquet(path) if path.exists() else pl.DataFrame(schema=COMPETITIVENESS_SCHEMA)


def append_shocks(rows: list[dict]) -> int:
    """Append to the cumulative shock ledger (one file, not per-day — the whole
    point is cross-day pattern analysis: if collapses cluster in one category,
    that category comes off the market-making list).

    Deduped on (kind, market_slug, token_id, before, after) so a re-run, or the
    same shock still visible in a later detection window, doesn't double-count.
    """
    if not rows:
        return 0
    path = LAMINAR_DIR / "shocks.parquet"
    new = pl.DataFrame(rows, schema=SHOCK_SCHEMA)
    if path.exists():
        new = pl.concat([pl.read_parquet(path), new])
    key = ["kind", "market_slug", "token_id", "before", "after"]
    merged = new.unique(subset=key, keep="first").sort("detected_at")
    path.parent.mkdir(parents=True, exist_ok=True)
    merged.write_parquet(path)
    return len(rows)


def load_shocks() -> pl.DataFrame:
    path = LAMINAR_DIR / "shocks.parquet"
    return pl.read_parquet(path) if path.exists() else pl.DataFrame(schema=SHOCK_SCHEMA)


def append_leads(rows: list[dict]) -> int:
    """Append to the cumulative PolyBeats lead ledger (one file, like shocks —
    the analysis it exists for is cross-day and case-based, not per-day).

    Deduped on msg_id keeping the FIRST copy, so re-scraping the same page does
    not overwrite a lead with a later re-parse. Returns the count of genuinely
    new leads, not the number passed in.
    """
    if not rows:
        return 0
    path = LAMINAR_DIR / "leads.parquet"
    before = len(pl.read_parquet(path)) if path.exists() else 0
    new = pl.DataFrame(rows, schema=LEAD_SCHEMA)
    if path.exists():
        new = pl.concat([pl.read_parquet(path), new])
    merged = new.unique(subset=["msg_id"], keep="first").sort("msg_id")
    path.parent.mkdir(parents=True, exist_ok=True)
    merged.write_parquet(path)
    return len(merged) - before


def load_leads() -> pl.DataFrame:
    path = LAMINAR_DIR / "leads.parquet"
    return pl.read_parquet(path) if path.exists() else pl.DataFrame(schema=LEAD_SCHEMA)


def max_lead_id() -> int:
    """Highest msg_id stored, so a scrape only walks back as far as it must."""
    leads = load_leads()
    return int(leads["msg_id"].max()) if len(leads) else 0


def append_lead_markets(rows: list[dict]) -> int:
    """The lead -> market(s) resolution map. Cumulative, one file.

    Deduped on (msg_id, token_id) keeping FIRST, so re-resolving a lead never
    rewrites the original mapping — `track_until` in particular must stay
    anchored to the post, not to whenever the resolver last ran.
    """
    if not rows:
        return 0
    path = LAMINAR_DIR / "lead_markets.parquet"
    before = len(pl.read_parquet(path)) if path.exists() else 0
    new = pl.DataFrame(rows, schema=LEAD_MARKET_SCHEMA)
    if path.exists():
        new = pl.concat([pl.read_parquet(path), new])
    merged = new.unique(subset=["msg_id", "token_id"], keep="first").sort(["msg_id", "token_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    merged.write_parquet(path)
    return len(merged) - before


def load_lead_markets() -> pl.DataFrame:
    path = LAMINAR_DIR / "lead_markets.parquet"
    return pl.read_parquet(path) if path.exists() else pl.DataFrame(schema=LEAD_MARKET_SCHEMA)


def append_lead_tracks(ts_ms: int, rows: list[dict]) -> int:
    """Hourly pool snapshots for tracked leads, partitioned by day.

    Day partitions, not hourly like `books`: this runs 24x/day against a few
    hundred tokens, so a day is thousands of rows, not millions.

    `outcome` is part of the key because a snapshot writes one row per book
    SIDE — it carries "Yes/bid" and "Yes/ask", which share ts/msg_id/token_id.
    Keying without it silently kept only the ask side.
    """
    if not rows:
        return 0
    path = LAMINAR_DIR / "lead_tracks" / f"{_day(ts_ms)}.parquet"
    return _append(path, rows, LEAD_TRACK_SCHEMA, key=["ts", "msg_id", "token_id", "outcome"])


def load_lead_tracks(day: str) -> pl.DataFrame:
    path = LAMINAR_DIR / "lead_tracks" / f"{day}.parquet"
    return pl.read_parquet(path) if path.exists() else pl.DataFrame(schema=LEAD_TRACK_SCHEMA)


def append_metrics_history(row: dict) -> None:
    """Upsert one day's summary row (re-running the report for the same day —
    e.g. a manual re-run — replaces that day's row, not duplicates it)."""
    path = LAMINAR_DIR / "metrics_history.parquet"
    new = pl.DataFrame([row], schema=METRICS_HISTORY_SCHEMA)
    if path.exists():
        new = pl.concat([pl.read_parquet(path), new])
    new.unique(subset=["day"], keep="last").sort("day").write_parquet(path)


def load_metrics_history() -> pl.DataFrame:
    path = LAMINAR_DIR / "metrics_history.parquet"
    return pl.read_parquet(path) if path.exists() else pl.DataFrame(schema=METRICS_HISTORY_SCHEMA)


def append_gate_windows(rows: list[dict]) -> None:
    """Upsert one day's spot-check rows. Unlike `append_metrics_history` this
    is many rows per day (one per window x token), so a re-run replaces by
    filtering that day out and re-appending, not by a single-key `unique()`."""
    if not rows:
        return
    path = LAMINAR_DIR / "gate_windows.parquet"
    new = pl.DataFrame(rows, schema=GATE_WINDOW_SCHEMA)
    if path.exists():
        new = pl.concat([pl.read_parquet(path).filter(pl.col("day") != rows[0]["day"]), new])
    new.sort(["day", "window_idx", "token_id"]).write_parquet(path)


def load_gate_windows() -> pl.DataFrame:
    path = LAMINAR_DIR / "gate_windows.parquet"
    return pl.read_parquet(path) if path.exists() else pl.DataFrame(schema=GATE_WINDOW_SCHEMA)


def append_gate_risk(rows: list[dict]) -> None:
    """Upsert one day's directional-risk-proxy rows (day, token_id) — same
    filter-and-replace pattern as `append_gate_windows`, for the same reason."""
    if not rows:
        return
    path = LAMINAR_DIR / "gate_risk.parquet"
    new = pl.DataFrame(rows, schema=GATE_RISK_SCHEMA)
    if path.exists():
        new = pl.concat([pl.read_parquet(path).filter(pl.col("day") != rows[0]["day"]), new])
    new.sort(["day", "token_id"]).write_parquet(path)


def load_gate_risk() -> pl.DataFrame:
    path = LAMINAR_DIR / "gate_risk.parquet"
    return pl.read_parquet(path) if path.exists() else pl.DataFrame(schema=GATE_RISK_SCHEMA)
