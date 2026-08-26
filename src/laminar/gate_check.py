"""Daily gate spot-check — observation only, no ordering, no cancel/skew action.

Added 2026-08-22 per user request, after two things surfaced the same day:

1. The daily email's `est_usd` uses each market's own `min_size` (20-200
   shares) as the reference-quote size, not the pre-registration's own gate
   quote (**200 shares/side**, see `laminar_20260814_preregistration.md`).
   Reward share is concave in size (`score.payout_bounds`), so the two are
   not interchangeable. See `worklogs/2026-08-22_laminar-yield-gate-check_v1.md`.
2. The Direction-A skew/cancel design sketch
   (`laminar_20260822_direction_a_skew_sketch.md`) named three open gaps —
   none of them turned out to need new collection, all three are answerable
   from books/trades already flowing in (see the worklog conversation this
   module implements).

What this module does, once a day, from that day's already-stored books and
trades:

  - re-checks the annualised-yield gate at the CORRECT 200-share quote,
    sampled from 3 random non-overlapping 2h windows instead of one
    end-of-day snapshot (`sample_windows`, `window_pool_metrics`) — finds
    whether liquidity/volume/yield show a time-of-day pattern (goal a);
  - tracks each pool's yield day over day and flags a move past a threshold
    against its own trailing baseline (`pool_day_summary`, `flag_pool_changes`)
    — goal (b), the "毛利...变化" half;
  - computes a directional-risk proxy — mid-price drift after today's large
    trades — as the closest HONEST stand-in for "loss" this project can
    produce with zero live positions (`directional_risk_proxy`) — goal (b),
    the "损失" half. This is the mid-price version of the calibration-window
    finding in `worklogs/2026-08-22_laminar-direction-a-unlabeled_v1.md`, at
    one day's granularity; a single day is usually 0-2 qualifying trades per
    pool, so it is meant to be read as a trailing rollup over several days,
    not a daily number on its own.

Every threshold here (`YIELD_CHANGE_FLAG_PCT`, `LARGE_USD`) is a first pass,
not a calibrated one — named explicitly so it reads as adjustable, matching
`ops/laminar_daily_report.py`'s own `EARLY_WARNING_RATIO` /
`DRIFT_ALERT_CENTS_MULT` convention.

Pure/testable: every function here takes data in, returns data out. HTML
rendering and storage happen in `ops/laminar_daily_report.py`, same split as
`laminar.report`.
"""

import random
import statistics as stats
from datetime import UTC, datetime

import polars as pl

from laminar import score

REF_SIZE = 200.0  # shares/side — the pre-registration's gate quote, not min_size
REF_OFFSET_CAP = 1.0  # cents, same convention as report._reference_quote
WINDOW_HOURS = 2
N_WINDOWS = 3
MIN_SAMPLES_FOR_WINDOW = 10  # below this a window's mean is noise, not a reading

LARGE_USD = 1000.0  # matches the flat threshold in the 08-22 calibration finding;
                     # a market-relative threshold was flagged as a follow-up, not done here
RISK_HORIZON_MIN = 15  # the horizon with the clearest signal in that finding (t=4.23)

YIELD_CHANGE_FLAG_PCT = 50.0  # relative move vs trailing baseline — uncalibrated first pass
MIN_HISTORY_DAYS = 3  # fewer prior days than this and a baseline is noise, not a signal


def sample_windows(day: str, n: int = N_WINDOWS, window_hours: int = WINDOW_HOURS) -> list[tuple[int, int]]:
    """n non-overlapping window_hours-wide UTC slots within `day`, on a fixed
    grid (00-02, 02-04, ...) rather than continuous random start times — keeps
    boundaries aligned with the hourly book partitions, and is seeded by the
    day string itself so a re-run for the same day is reproducible rather than
    picking a different sample on retry."""
    y, m, d = (int(x) for x in day.split("-"))
    day_start = int(datetime(y, m, d, tzinfo=UTC).timestamp() * 1000)
    slots = 24 // window_hours
    chosen = sorted(random.Random(day).sample(range(slots), n))
    ms = window_hours * 3_600_000
    return [(day_start + i * ms, day_start + (i + 1) * ms) for i in chosen]


def _reference_quote(max_spread: float, midpoint: float) -> tuple[score.Level, score.Level]:
    offset = min(REF_OFFSET_CAP, max_spread * 0.5) / 100.0
    return (
        score.Level(round(midpoint - offset, 4), REF_SIZE),
        score.Level(round(midpoint + offset, 4), REF_SIZE),
    )


def window_pool_metrics(books: pl.DataFrame, trades: pl.DataFrame, start_ms: int, end_ms: int) -> list[dict]:
    """Per-token metrics for one window: annualised yield at the FIXED
    200-share reference quote (averaged over every book sample inside the
    window, not just one snapshot), competing-book liquidity (Q_one + Q_two,
    excluding our hypothetical order), and real trade volume. A token/window
    with fewer than `MIN_SAMPLES_FOR_WINDOW` book samples is dropped rather
    than averaged over too little coverage to trust."""
    w = books.filter((pl.col("ts") >= start_ms) & (pl.col("ts") < end_ms))
    if w.is_empty():
        return []
    wt = trades.filter((pl.col("ts") >= start_ms) & (pl.col("ts") < end_ms))

    out = []
    for (token_id,), g in w.group_by("token_id"):
        n_samples = g["ts"].n_unique()
        yields_lo, yields_hi, liq = [], [], []
        for (_ts,), sample in g.group_by("ts"):
            params = score.RewardParams(sample["max_spread"][0], sample["min_size"][0], sample["daily_rate"][0])
            mid = sample["midpoint"][0]
            if not (0.10 <= mid <= 0.90) or params.daily_rate <= 0:
                continue  # outside the two-sided band, or pool pulled — not what this gate tests
            bids = [score.Level(r["price"], r["size"]) for r in sample.filter(pl.col("side") == "bid").iter_rows(named=True)]
            asks = [score.Level(r["price"], r["size"]) for r in sample.filter(pl.col("side") == "ask").iter_rows(named=True)]
            my_bid, my_ask = _reference_quote(params.max_spread, mid)
            share_lo, share_hi = score.payout_bounds(params, mid, [my_bid], [my_ask], bids, asks)
            yields_lo.append(params.daily_rate * share_lo * 365.0 / REF_SIZE * 100.0)
            yields_hi.append(params.daily_rate * share_hi * 365.0 / REF_SIZE * 100.0)
            liq.append(score.side_score(params, mid, bids) + score.side_score(params, mid, asks))
        if n_samples < MIN_SAMPLES_FOR_WINDOW or not yields_hi:
            continue
        tt = wt.filter(pl.col("token_id") == token_id)
        out.append({
            "token_id": token_id,
            "n_samples": n_samples,
            "yield_lo_pct": stats.mean(yields_lo),
            "yield_hi_pct": stats.mean(yields_hi),
            "liquidity": stats.mean(liq) if liq else 0.0,
            "volume_count": tt.height,
            "volume_notional": float((tt["price"] * tt["size"]).sum()) if tt.height else 0.0,
        })
    return out


def daily_spotcheck(day: str, books: pl.DataFrame, trades: pl.DataFrame, watchlist: dict | None = None) -> list[dict]:
    """Full day's persisted rows: N_WINDOWS sampled 2h slots x every token
    with enough coverage in each."""
    if books.is_empty():
        return []
    label_by_token = (
        {t["token_id"]: (m["market_slug"], t["outcome"]) for m in watchlist["markets"] for t in m["tokens"]}
        if watchlist else {}
    )
    rows = []
    for idx, (start, end) in enumerate(sample_windows(day)):
        for m in window_pool_metrics(books, trades, start, end):
            slug, outcome = label_by_token.get(m["token_id"], ("", ""))
            rows.append({
                "day": day, "window_idx": idx, "window_start": start, "window_end": end,
                "market_slug": slug, "outcome": outcome,
                **m,
            })
    return rows


def window_pattern_summary(rows: list[dict]) -> dict[int, dict]:
    """Cross-window comparison for one day — goal (a): does liquidity / volume
    / yield look different across the sampled times of day? Median across
    tokens per window, not mean — one illiquid outlier token shouldn't swing
    the whole window's reading."""
    by_window: dict[int, list[dict]] = {}
    for r in rows:
        by_window.setdefault(r["window_idx"], []).append(r)
    out = {}
    for idx, rs in by_window.items():
        out[idx] = {
            "window_start": rs[0]["window_start"],
            "window_end": rs[0]["window_end"],
            "n_tokens": len(rs),
            "median_yield_hi_pct": stats.median(r["yield_hi_pct"] for r in rs),
            "median_liquidity": stats.median(r["liquidity"] for r in rs),
            "total_volume_notional": sum(r["volume_notional"] for r in rs),
            "total_volume_count": sum(r["volume_count"] for r in rs),
        }
    return out


def pool_day_summary(rows: list[dict]) -> dict[str, dict]:
    """One row per token: this day's figures collapsed across its windows —
    the unit `flag_pool_changes` compares day over day."""
    by_token: dict[str, list[dict]] = {}
    for r in rows:
        by_token.setdefault(r["token_id"], []).append(r)
    out = {}
    for tid, rs in by_token.items():
        out[tid] = {
            "market_slug": rs[0]["market_slug"],
            "outcome": rs[0]["outcome"],
            "n_windows": len(rs),
            "median_yield_hi_pct": stats.median(r["yield_hi_pct"] for r in rs),
            "median_liquidity": stats.median(r["liquidity"] for r in rs),
            "total_volume_notional": sum(r["volume_notional"] for r in rs),
        }
    return out


def flag_pool_changes(today: dict[str, dict], history: pl.DataFrame, day: str) -> list[dict]:
    """Compare today's per-pool yield against the trailing median of prior
    stored days (strictly before `day`). Skips a pool with fewer than
    `MIN_HISTORY_DAYS` prior days rather than flagging off a thin baseline."""
    if history.is_empty():
        return []
    prior = history.filter(pl.col("day") < day)
    flags = []
    for tid, t in today.items():
        tok_hist = prior.filter(pl.col("token_id") == tid)
        if tok_hist.is_empty() or tok_hist["day"].n_unique() < MIN_HISTORY_DAYS:
            continue
        # collapse to one median per prior day first, then take the trailing
        # median of THOSE — a single noisy window on one prior day can't
        # outweigh an entire other day the way pooling all raw rows would
        day_medians = tok_hist.group_by("day").agg(pl.col("yield_hi_pct").median().alias("m"))["m"].to_list()
        baseline = stats.median(day_medians)
        if baseline <= 0:
            continue
        change_pct = (t["median_yield_hi_pct"] - baseline) / baseline * 100.0
        if abs(change_pct) >= YIELD_CHANGE_FLAG_PCT:
            flags.append({
                "token_id": tid, "market_slug": t["market_slug"], "outcome": t["outcome"],
                "today_pct": t["median_yield_hi_pct"], "baseline_pct": baseline,
                "change_pct": change_pct,
            })
    return sorted(flags, key=lambda f: -abs(f["change_pct"]))


def directional_risk_proxy(day: str, books: pl.DataFrame, trades: pl.DataFrame, watchlist: dict | None = None) -> list[dict]:
    """Per-token: today's large trades (>= LARGE_USD) and the book MID-PRICE
    drift RISK_HORIZON_MIN minutes later, signed by trade direction — the
    mid-price version of the 2026-08-22 continuation finding, at one day's
    granularity. NOT a PnL/loss number: Stage 1 holds no position, so there is
    nothing to literally lose (see `laminar.report`'s own note on this). It is
    the same kind of proxy as that module's `drift_cents`, just anchored to
    real trade events instead of a fixed lookback."""
    if trades.is_empty() or books.is_empty():
        return []
    label_by_token = (
        {t["token_id"]: (m["market_slug"], t["outcome"]) for m in watchlist["markets"] for t in m["tokens"]}
        if watchlist else {}
    )
    large = trades.with_columns(
        (pl.col("price") * pl.col("size")).alias("notional"),
        pl.when(pl.col("side") == "BUY").then(1).otherwise(-1).alias("dir"),
    ).filter(pl.col("notional") >= LARGE_USD)

    out: dict[str, dict] = {}
    for (token_id,), tok_books in books.sort("ts").group_by("token_id"):
        tok_trades = large.filter(pl.col("token_id") == token_id)
        if tok_trades.is_empty():
            continue
        ts_list = tok_books["ts"].to_list()
        mid_list = tok_books["midpoint"].to_list()
        for row in tok_trades.iter_rows(named=True):
            t0 = row["ts"]
            before = [i for i, t in enumerate(ts_list) if t <= t0]
            if not before:
                continue  # trade predates our first book sample for this token
            mid0 = mid_list[before[-1]]
            deadline = t0 + RISK_HORIZON_MIN * 60_000
            at_deadline = [i for i, t in enumerate(ts_list) if t0 < t <= deadline]
            if not at_deadline:
                continue  # no later sample within the horizon
            mid_h = mid_list[at_deadline[-1]]
            drift = row["dir"] * (mid_h - mid0)
            slug, outcome = label_by_token.get(token_id, ("", ""))
            d = out.setdefault(token_id, {
                "day": day, "token_id": token_id, "market_slug": slug, "outcome": outcome,
                "n_large_trades": 0, "signed_drift_sum": 0.0,
            })
            d["n_large_trades"] += 1
            d["signed_drift_sum"] += drift
    return list(out.values())
