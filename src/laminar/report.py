"""Laminar Stage-1 strategy-observation report.

Answers the five things Stage 1 CAN answer from stored book samples, and is
explicit about the two it structurally cannot:

  1. which pools we're watching                    -> answerable
  2. what quote parameters                          -> answerable (HYPOTHETICAL — see below)
  3. fills / fees / realised PnL over 24h            -> NOT ANSWERABLE, by design
  4. modelled reward estimate                        -> answerable (bounded, not a point estimate)
  5. deltas from expectation (bugs/surprises found)   -> answerable
  6. anomaly alerts                                   -> answerable, book-derived proxies only

On (3): Stage 1 places no orders. `laminar.clob` has no signing, no keys, no
`POST`. There is no fill, no fee, no position, and therefore no PnL to report
— reporting one would mean fabricating it. This becomes answerable only at
Stage 3 (live orders), which is blocked on the jurisdiction/entity decision in
the pre-registration, not on anything this report could compute differently.

On (6): with no live position, "one-sided loss" cannot literally occur. The
nearest honest proxy computable from the book alone is `midpoint_drift` —
whether the market moved through our hypothetical reference quote after the
sample. It is a directional-risk signal, not a PnL number, and is labelled as
one.

## The reference quote (a MODEL INPUT, not a live order)

Stage 1 has never placed a bid or ask; "our quote" below is the
pre-registration's own reference point for sizing ("a reward-eligible
two-sided quote, 200 shares per side"), made concrete so it can be measured
against real books instead of staying abstract:

    size   = market's `rewards.min_size` (the floor; posting less scores zero)
    offset = min(1.0 cent, max_spread * 0.5) from the adjusted midpoint, both sides

The 1-cent default sits well inside the quadratic reward zone without hugging
the exact midpoint tick; `max_spread * 0.5` is the fallback for the rare
market with a sub-2-cent band. Changing this reference is a modelling choice,
recorded here, not a pre-registration change — it does not alter the frozen
watchlist filter or the abandon gates.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

import polars as pl

from laminar import score, store

REF_OFFSET_CAP = 1.0  # cents


@dataclass(frozen=True)
class PoolObservation:
    token_id: str
    market_slug: str
    outcome: str
    daily_rate: float
    max_spread: float
    min_size: float
    midpoint: float
    ref_size: float
    ref_offset: float
    our_score: float
    denom_lo: float
    denom_hi: float
    share_lo: float
    share_hi: float
    est_usd_lo: float
    est_usd_hi: float
    band_breach: bool  # midpoint outside [0.10, 0.90] — loses two-sided relief
    pool_pulled: bool  # absent from the latest sample entirely
    drift_cents: float | None  # |midpoint(latest) - midpoint(~30min ago)|, None if no history


def _reference_quote(min_size: float, max_spread: float, midpoint: float) -> tuple[score.Level, score.Level]:
    offset = min(REF_OFFSET_CAP, max_spread * 0.5) / 100.0
    return (
        score.Level(round(midpoint - offset, 4), min_size),  # our bid
        score.Level(round(midpoint + offset, 4), min_size),  # our ask
    )


def observe(day: str, lookback_minutes: int = 30) -> list[PoolObservation]:
    """One row per token currently in the stored book, using the LATEST sample
    as "now" and, where enough history exists, a sample ~`lookback_minutes`
    earlier for the drift proxy."""
    df = store.load_books(day)
    if df.is_empty():
        return []
    all_ts = sorted(df["ts"].unique())
    latest_ts = all_ts[-1]
    target = latest_ts - lookback_minutes * 60_000
    older_ts = max((t for t in all_ts if t <= target), default=None)

    out: list[PoolObservation] = []
    for (token_id,), g in df.filter(pl.col("ts") == latest_ts).group_by("token_id"):
        params = score.RewardParams(g["max_spread"][0], g["min_size"][0], g["daily_rate"][0])
        mid = g["midpoint"][0]
        bids = [score.Level(r["price"], r["size"]) for r in g.filter(pl.col("side") == "bid").iter_rows(named=True)]
        asks = [score.Level(r["price"], r["size"]) for r in g.filter(pl.col("side") == "ask").iter_rows(named=True)]

        my_bid, my_ask = _reference_quote(params.min_size, params.max_spread, mid)
        our_score = score.qmin(
            score.order_score(params, mid, my_bid), score.order_score(params, mid, my_ask), mid
        )
        d_lo, d_hi = score.denominator_bounds(params, mid, bids, asks)
        share_lo, share_hi = score.payout_bounds(params, mid, [my_bid], [my_ask], bids, asks)

        older_mid = None
        if older_ts is not None:
            og = df.filter((pl.col("ts") == older_ts) & (pl.col("token_id") == token_id))
            if og.height:
                older_mid = og["midpoint"][0]
        drift = abs(mid - older_mid) * 100.0 if older_mid is not None else None

        out.append(
            PoolObservation(
                token_id=token_id,
                market_slug="",  # filled in by caller from the watchlist, if available
                outcome="",
                daily_rate=params.daily_rate,
                max_spread=params.max_spread,
                min_size=params.min_size,
                midpoint=mid,
                ref_size=params.min_size,
                ref_offset=min(REF_OFFSET_CAP, params.max_spread * 0.5),
                our_score=our_score,
                denom_lo=d_lo,
                denom_hi=d_hi,
                share_lo=share_lo,
                share_hi=share_hi,
                est_usd_lo=params.daily_rate * share_lo,
                est_usd_hi=params.daily_rate * share_hi,
                band_breach=not (0.10 <= mid <= 0.90),
                pool_pulled=params.daily_rate <= 0,
                drift_cents=drift,
            )
        )
    return out


def label_from_watchlist(obs: list[PoolObservation], watchlist: dict | None) -> list[PoolObservation]:
    if not watchlist:
        return obs
    by_token = {t["token_id"]: (m["market_slug"], t["outcome"]) for m in watchlist["markets"] for t in m["tokens"]}
    out = []
    for o in obs:
        slug, outcome = by_token.get(o.token_id, (o.market_slug, o.outcome))
        out.append(
            PoolObservation(**{**o.__dict__, "market_slug": slug or "(off-watchlist)", "outcome": outcome})
        )
    return out


def fill_touch(day: str, obs: list[PoolObservation]) -> dict[str, tuple[float, float]]:
    """Real trade volume (shares) that printed through each observation's
    reference-quote price, split bid/ask. Added 2026-08-14 as a direct,
    ground-truth alternative to inferring fills from book/midpoint deltas —
    price can tighten purely from a competitor cancel-and-repost (this reward
    scheme is specifically built to encourage that), so a delta alone proves
    nothing. A real print at-or-through our price is the closest available
    evidence.

    This is an UPPER BOUND, not a fill probability and not PnL: our queue
    position among other makers resting at the same price is unobservable
    from public data (a known open problem — see the 2026 postmortem cited in
    the pre-registration), so a real order could have captured less than the
    volume shown here, including none of it.

    `side` is read as the taker's side (see clob.trades docstring): a SELL
    print at or below our bid price crossed through where our bid would sit;
    a BUY print at or above our ask price crossed through our ask.

    KNOWN SIMPLIFICATION: every trade in `day` is compared against `obs`'s
    single latest-snapshot reference price, not the reference price that
    would have applied at the trade's own timestamp — a true point-in-time
    comparison would need the whole day's book history, not just its last
    sample. Defensible for this watchlist (selected for slow-moving,
    long-dated markets specifically to keep intraday price drift low), but
    would misstate touch volume on a market that moved materially during the
    day. Not corrected for; stated so it isn't mistaken for precision.
    """
    trades = store.load_trades(day)
    if trades.is_empty():
        return {o.token_id: (0.0, 0.0) for o in obs}
    out: dict[str, tuple[float, float]] = {}
    for o in obs:
        bid_price = o.midpoint - o.ref_offset / 100.0
        ask_price = o.midpoint + o.ref_offset / 100.0
        tt = trades.filter(pl.col("token_id") == o.token_id)
        bid_hit = tt.filter((pl.col("side") == "SELL") & (pl.col("price") <= bid_price))
        ask_hit = tt.filter((pl.col("side") == "BUY") & (pl.col("price") >= ask_price))
        bid_touch = float(bid_hit["size"].sum() or 0.0)
        ask_touch = float(ask_hit["size"].sum() or 0.0)
        out[o.token_id] = (bid_touch, ask_touch)
    return out


# Keyword -> category, checked in order; first match wins. Deliberately coarse:
# the question this feeds is "does one CATEGORY collapse more than the others,
# and should we therefore not make markets in it", which needs buckets big
# enough to accumulate counts over 21 days, not fine-grained labels.
CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("fed-macro", ("fed-rate", "fed-interest", "interest-rates", "gdp-growth", "rate-cut", "rate-hike", "inflation", "cpi")),
    ("geopolitics", ("iran", "israel", "hormuz", "cuba", "russia", "ukraine", "invade", "strike", "blockade", "ceasefire", "khamenei", "war")),
    # us-politics BEFORE election: a "senate"/"governor" market can be about a
    # resignation or appointment rather than a vote, and mislabelling those as
    # elections would corrupt the per-category shock counts this feeds.
    ("us-politics", ("resign", "press-secretary", "white-house", "impeach", "cabinet", "confirmed-as")),
    # sports BEFORE election: election's "win-the-20" is greedy and would
    # otherwise swallow "win-the-2026-world-chess-championship".
    ("sports", ("f1", "drivers-champion", "chess", "world-cup", "nba", "nfl", "olympic")),
    ("election", ("election", "nomination", "presidential", "governor", "senate", "win-the-20")),
    ("corporate", ("ipo", "acquisition", "merger", "earnings", "openai", "anthropic")),
    ("entertainment", ("bond", "oscar", "grammy", "movie", "box-office", "album")),
]


def categorise(market_slug: str) -> str:
    """Coarse market taxonomy for shock clustering. 'other' when nothing matches
    — a growing 'other' bucket is itself a signal the rules need extending."""
    s = market_slug.lower()
    for name, keys in CATEGORY_RULES:
        if any(k in s for k in keys):
            return name
    return "other"


def detect_shocks(
    day: str,
    obs: list[PoolObservation],
    watchlist: dict | None,
    window_minutes: int = 30,
    midpoint_pct: float = 0.25,
    depth_pct: float = 0.60,
    pool_pct: float = 0.50,
) -> list[dict]:
    """Find sudden collapses in the day's stored samples, for the shock ledger.

    Three kinds, all measured over `window_minutes` between two book samples:
      pool_rate — the market's reward pool cut (vs the frozen watchlist rate)
      midpoint  — the price gapped (adverse-selection risk made concrete)
      book_depth — resting size inside the reward band vanished (liquidity fled)

    Each row is tagged with its category and with any FOMC/CPI event that day,
    so 'expected catalyst' can later be separated from 'surprise' — a category
    that only collapses on scheduled days is a very different risk from one
    that collapses at random.
    """
    from datetime import date as _date

    from laminar import calendar as cal

    df = store.load_books(day)
    if df.is_empty() or not obs:
        return []
    all_ts = sorted(df["ts"].unique())
    latest = all_ts[-1]
    target = latest - window_minutes * 60_000
    older = max((t for t in all_ts if t <= target), default=None)
    if older is None:
        return []
    actual_window = (latest - older) / 60_000

    y, m, d = (int(x) for x in day.split("-"))
    events = "; ".join(cal.events_on(_date(y, m, d)))

    frozen_rate = {}
    if watchlist:
        cond_by_token = {t["token_id"]: mk["condition_id"] for mk in watchlist["markets"] for t in mk["tokens"]}
        rate_by_cond = {mk["condition_id"]: mk["daily_rate"] for mk in watchlist["markets"]}
        frozen_rate = {tok: rate_by_cond[c] for tok, c in cond_by_token.items() if c in rate_by_cond}

    now_df = df.filter(pl.col("ts") == latest)
    old_df = df.filter(pl.col("ts") == older)
    out: list[dict] = []
    # One row per (market, kind), NOT per token. A binary market's Yes and No
    # books are the same resting orders seen from opposite sides, so a depth
    # collapse or price gap shows up identically on both tokens — logging both
    # would double every token-level count and corrupt the per-category
    # comparison this ledger exists to produce.
    seen: set[tuple[str, str]] = set()

    for o in obs:
        cat = categorise(o.market_slug)
        base = {
            "detected_at": latest,
            "category": cat,
            "market_slug": o.market_slug,
            "outcome": o.outcome,
            "window_minutes": actual_window,
            "scheduled_event": events,
        }

        fr = frozen_rate.get(o.token_id)
        if fr and fr > 0 and (o.market_slug, "pool_rate") not in seen:
            drop = (fr - o.daily_rate) / fr
            if drop >= pool_pct:
                seen.add((o.market_slug, "pool_rate"))
                out.append({**base, "kind": "pool_rate", "token_id": "", "outcome": "",
                            "before": float(fr), "after": float(o.daily_rate),
                            "pct_change": -drop,
                            "note": "reward pool cut vs frozen watchlist rate"})

        old_tok = old_df.filter(pl.col("token_id") == o.token_id)
        if old_tok.is_empty():
            continue

        old_mid = old_tok["midpoint"][0]
        if old_mid > 0 and (o.market_slug, "midpoint") not in seen:
            move = (o.midpoint - old_mid) / old_mid
            if abs(move) >= midpoint_pct:
                seen.add((o.market_slug, "midpoint"))
                out.append({**base, "kind": "midpoint", "token_id": o.token_id,
                            "before": float(old_mid), "after": float(o.midpoint),
                            "pct_change": float(move),
                            "note": f"midpoint moved {move:+.1%} in {actual_window:.0f} min"})

        old_depth = float(old_tok["size"].sum() or 0.0)
        new_depth = float(now_df.filter(pl.col("token_id") == o.token_id)["size"].sum() or 0.0)
        if old_depth > 0 and (o.market_slug, "book_depth") not in seen:
            dchange = (new_depth - old_depth) / old_depth
            if dchange <= -depth_pct:
                seen.add((o.market_slug, "book_depth"))
                out.append({**base, "kind": "book_depth", "token_id": o.token_id,
                            "before": old_depth, "after": new_depth,
                            "pct_change": dchange,
                            "note": f"in-band resting size fell {dchange:.0%}"})
    return out


def denom_ratio_distribution(day: str) -> list[float]:
    """Denominator bound ratio (hi/lo) pooled across EVERY sample of the day,
    not just the latest one the daily email shows — a single last-sample
    number is one point; the pre-registration's 3x abandon gate deserves a
    distribution by the time it's actually checked at a review point."""
    df = store.load_books(day)
    if df.is_empty():
        return []
    out = []
    for (_ts, _tok), g in df.group_by(["ts", "token_id"]):
        params = score.RewardParams(g["max_spread"][0], g["min_size"][0], g["daily_rate"][0])
        mid = g["midpoint"][0]
        bids = [score.Level(r["price"], r["size"]) for r in g.filter(pl.col("side") == "bid").iter_rows(named=True)]
        asks = [score.Level(r["price"], r["size"]) for r in g.filter(pl.col("side") == "ask").iter_rows(named=True)]
        lo, hi = score.denominator_bounds(params, mid, bids, asks)
        if lo > 0:
            out.append(hi / lo)
    return out


def pool_drift(
    obs: list[PoolObservation], watchlist: dict | None, threshold: float = 0.30
) -> list[tuple[str, float, float]]:
    """Markets whose live daily_rate has moved > `threshold` from the value
    recorded when the watchlist was frozen. Discovered live 2026-08-14: the
    reward pool on our single largest market (fed-rate-hike-in-2026) dropped
    500 -> 200 USDC/day within the same day — pools are not static, and the
    frozen watchlist's own daily_rate field is a snapshot, not a live value.
    Returns (market_slug, frozen_rate, live_rate), one row per market (not
    per token — both outcome tokens share the same market-level rate)."""
    if not watchlist:
        return []
    frozen_by_cond = {m["condition_id"]: (m["market_slug"], m["daily_rate"]) for m in watchlist["markets"]}
    cond_by_token = {t["token_id"]: m["condition_id"] for m in watchlist["markets"] for t in m["tokens"]}
    seen_conds: set[str] = set()
    out = []
    for o in obs:
        cond = cond_by_token.get(o.token_id)
        if cond is None or cond in seen_conds:
            continue
        seen_conds.add(cond)
        slug, frozen_rate = frozen_by_cond.get(cond, (o.market_slug, o.daily_rate))
        if frozen_rate > 0 and abs(o.daily_rate - frozen_rate) / frozen_rate > threshold:
            out.append((slug, frozen_rate, o.daily_rate))
    return out


def missing_from_watchlist(obs: list[PoolObservation], watchlist: dict | None) -> list[tuple[str, str]]:
    """Watchlist tokens with NO row in the latest sample — the pool sampler
    skips a token when `adjusted_midpoint` is None (a qualifying side vanished)
    or the market stopped accepting orders; either way it is not observable
    right now and that is itself a finding."""
    if not watchlist:
        return []
    seen = {o.token_id for o in obs}
    return [
        (m["market_slug"], t["token_id"])
        for m in watchlist["markets"]
        for t in m["tokens"]
        if t["token_id"] not in seen
    ]
