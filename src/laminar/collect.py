"""Laminar Stage-1 collector. Read-only; exits non-zero on any failure.

    python -m laminar.collect watchlist   # re-derive and freeze the watchlist
    python -m laminar.collect books       # one book sample for the watchlist
    python -m laminar.collect markets     # snapshot reward params
    python -m laminar.collect trades      # poll the real trade tape
    python -m laminar.collect competitiveness  # poll Polymarket's own competitiveness index
    python -m laminar.collect leads [pages]    # record new PolyBeats smart-money leads
    python -m laminar.collect track [limit]    # resolve leads to markets + snapshot their pools
    python -m laminar.collect activity [0x.. ...] [--pages N]  # wallets' own history;
                                          # with no address, derives candidates from the tape

Cron on the SGP droplet: `books` every minute, the rest hourly.
The watchlist is frozen for the observation window per the pre-registration —
re-running `watchlist` mid-window is a pre-registration change, not a routine
refresh. `trades` was added 2026-08-14 as a recorded addendum (see the
pre-registration's addendum log) — it changes nothing about the frozen
watchlist filter or the abandon gates, only adds a second read-only data
source (real executed trades, still zero orders of our own) to build a
fill-touch proxy alongside the reward-score model.

`trades` gained a `wallet` column on 2026-08-19 (second recorded addendum),
making each print attributable to an address. This is the input to two
questions the pre-registration does not yet cover — whether informed flow
predicts continuation (so that pulling quotes after the first hit is worth its
forgone reward-seconds), and whether persistently-losing addresses are a usable
inverse indicator. Both are RECORDING ONLY. No classification rule is committed
here: those go in the pre-registration before the data is looked at, not after.
Still zero orders.
"""

import json
import sys
import time
from datetime import UTC, datetime, timedelta

import polars as pl

from farseer.config import CONFIG_DIR

from laminar import clob, polybeats, score, store, track

WATCHLIST_PATH = CONFIG_DIR / "laminar_watchlist.json"

# Selection filter — frozen in laminar_20260814_preregistration.md
MIN_DAILY_RATE = 20.0  # USDC/day; below this the $1/day payout floor bites
MIN_DAYS_OUT = 90  # long-dated only: news arrival rare vs sampling rate
MIDPOINT_BAND = (0.10, 0.90)  # outside, Qmin = min(Q1,Q2) — a different regime
MAX_MARKETS = 30


def _now_ms() -> int:
    return int(time.time() * 1000)


def _eligible(m: dict, now: datetime) -> bool:
    if not (m.get("accepting_orders") and m.get("enable_order_book")):
        return False
    if m.get("closed") or m.get("archived"):
        return False
    if m.get("game_start_time"):
        return False  # in-game multiplier `b` is undocumented — out of scope
    if clob.daily_rate(m) < MIN_DAILY_RATE:
        return False
    end = m.get("end_date_iso")
    if not end:
        return False
    try:
        if datetime.fromisoformat(end.replace("Z", "+00:00")) - now < timedelta(
            days=MIN_DAYS_OUT
        ):
            return False
    except ValueError:
        return False
    tokens = m.get("tokens") or []
    if len(tokens) != 2:
        return False
    price = float(tokens[0].get("price") or 0)
    return MIDPOINT_BAND[0] <= price <= MIDPOINT_BAND[1]


def build_watchlist() -> list[dict]:
    now = datetime.now(UTC)
    markets = [m for m in clob.sampling_markets() if _eligible(m, now)]
    markets.sort(key=clob.daily_rate, reverse=True)
    chosen = markets[:MAX_MARKETS]
    WATCHLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    WATCHLIST_PATH.write_text(
        json.dumps(
            {
                "frozen_at": now.isoformat(),
                "filter": {
                    "min_daily_rate": MIN_DAILY_RATE,
                    "min_days_out": MIN_DAYS_OUT,
                    "midpoint_band": MIDPOINT_BAND,
                    "max_markets": MAX_MARKETS,
                },
                "markets": [
                    {
                        "condition_id": m["condition_id"],
                        "market_slug": m["market_slug"],
                        "question": m["question"],
                        "end_date_iso": m["end_date_iso"],
                        "daily_rate": clob.daily_rate(m),
                        "max_spread": float(m["rewards"]["max_spread"]),
                        "min_size": float(m["rewards"]["min_size"]),
                        "tokens": [
                            {"token_id": t["token_id"], "outcome": t["outcome"]}
                            for t in m["tokens"]
                        ],
                    }
                    for m in chosen
                ],
            },
            indent=2,
        )
    )
    return chosen


def load_watchlist() -> dict:
    if not WATCHLIST_PATH.exists():
        raise SystemExit(
            f"no watchlist at {WATCHLIST_PATH} — run `python -m laminar.collect watchlist`"
        )
    return json.loads(WATCHLIST_PATH.read_text())


def _live_reward_params(token_ids: set[str]) -> dict[str, tuple[float, float, float]]:
    """Fresh (max_spread, min_size, daily_rate) per token, from a live
    `/sampling-markets` pull, filtered down to just what's asked for.

    BUG FOUND & FIXED 2026-08-14: `sample_books` used to read these three
    fields from the FROZEN watchlist JSON — the snapshot taken once when the
    watchlist was built — instead of re-fetching. Every book sample stored
    since the watchlist was frozen carried stale reward params; confirmed
    live on fed-rate-hike-in-2026, whose real pool moved 500 -> 200 USDC/day
    while every stored sample kept reporting 500. `/sampling-markets` costs
    ~2.6s for the full ~12.6k-market universe — cheap enough to re-pull every
    cycle rather than trust a stale copy.
    """
    out: dict[str, tuple[float, float, float]] = {}
    for m in clob.sampling_markets():
        for t in m.get("tokens") or []:
            if t["token_id"] in token_ids:
                rewards = m.get("rewards") or {}
                out[t["token_id"]] = (
                    float(rewards.get("max_spread") or 0),
                    float(rewards.get("min_size") or 0),
                    clob.daily_rate(m),
                )
    return out


def sample_books() -> int:
    """One book sample across the watchlist. Only levels inside the reward band
    are stored — everything outside scores zero and is dead weight on disk.

    Since 2026-08-25 the same sample is ALSO written full-depth to `books_full`
    (see store.append_books_full). Same API call, no extra network cost; the
    band-filtered `books` series and its write path are unchanged, and the
    full-depth write is best-effort so it cannot take the gate series down.
    """
    wl = load_watchlist()
    ts = _now_ms()
    token_ids = {tok["token_id"] for m in wl["markets"] for tok in m["tokens"]}
    live = _live_reward_params(token_ids)
    rows: list[dict] = []
    rows_full: list[dict] = []
    for m in wl["markets"]:
        for tok in m["tokens"]:
            params = live.get(tok["token_id"])
            if params is None:
                continue  # no longer reward-eligible right now — nothing to score
            max_spread, min_size, daily_rate = params
            if max_spread <= 0 or min_size <= 0:
                continue
            bk = clob.book(tok["token_id"])
            bids = [score.Level(float(x["price"]), float(x["size"])) for x in bk["bids"]]
            asks = [score.Level(float(x["price"]), float(x["size"])) for x in bk["asks"]]
            mid = score.adjusted_midpoint(bids, asks, min_size)
            if mid is None:
                continue
            band = max_spread / 100.0
            for side, levels in (("bid", bids), ("ask", asks)):
                for lv in levels:
                    row = {
                        "ts": ts,
                        "token_id": tok["token_id"],
                        "side": side,
                        "price": lv.price,
                        "size": lv.size,
                        "midpoint": mid,
                        "max_spread": max_spread,
                        "min_size": min_size,
                        "daily_rate": daily_rate,
                    }
                    rows_full.append(row)
                    if abs(lv.price - mid) <= band:
                        rows.append(row)
    stored = store.append_books(ts, rows)  # gate series first, and durable, before anything else
    try:
        store.append_books_full(ts, rows_full)
    except Exception as exc:  # noqa: BLE001 — never let the shadow series break collection
        print(f"  WARNING books_full write failed ({exc}); band series unaffected")
    return stored


def sample_markets() -> int:
    """Snapshot reward params for the whole reward universe, not just the
    watchlist — pool drift outside the watchlist is what tells us whether the
    selection filter still points at the right markets."""
    ts = _now_ms()
    rows = [
        {
            "ts": ts,
            "condition_id": m["condition_id"],
            "market_slug": m["market_slug"],
            "question": m["question"],
            "end_date_iso": m.get("end_date_iso"),
            "token_id": t["token_id"],
            "outcome": t["outcome"],
            "price": float(t.get("price") or 0),
            "max_spread": float(m["rewards"]["max_spread"]),
            "min_size": float(m["rewards"]["min_size"]),
            "daily_rate": clob.daily_rate(m),
            "minimum_tick_size": float(m.get("minimum_tick_size") or 0),
            "neg_risk": bool(m.get("neg_risk")),
        }
        for m in clob.sampling_markets()
        for t in (m.get("tokens") or [])
        if m.get("rewards")
    ]
    return store.append_markets(ts, rows)


def sample_competitiveness() -> int:
    """Poll Polymarket's own `market_competitiveness` index for every unique
    market in the watchlist. See clob.rewards_market's docstring for what's
    unresolved about it — collected to compare against our own denominator
    bound over the observation window, not yet trusted as a replacement."""
    wl = load_watchlist()
    ts = _now_ms()
    rows = []
    for cond in {m["condition_id"] for m in wl["markets"]}:
        m = clob.rewards_market(cond)
        if m is None:
            continue
        rate = sum(c.get("rate_per_day", 0) for c in m.get("rewards_config") or [])
        rows.append(
            {
                "ts": ts,
                "condition_id": cond,
                "market_slug": m.get("market_slug", ""),
                "competitiveness": float(m.get("market_competitiveness") or 0.0),
                "config_rate_per_day": float(rate),
            }
        )
    return store.append_competitiveness(ts, rows)


def sample_trades() -> int:
    """Poll the real trade tape for every unique market in the watchlist."""
    wl = load_watchlist()
    seen_conditions = {m["condition_id"] for m in wl["markets"]}
    outcome_by_token = {
        t["token_id"]: (m["condition_id"], t["outcome"])
        for m in wl["markets"]
        for t in m["tokens"]
    }
    rows: list[dict] = []
    for cond in seen_conditions:
        for t in clob.trades(cond):
            token_id = t.get("asset")
            _, outcome = outcome_by_token.get(token_id, (cond, t.get("outcome")))
            rows.append(
                {
                    "tx_hash": t.get("transactionHash", ""),
                    "ts": int(t["timestamp"]) * 1000,  # data-api gives seconds
                    "condition_id": t.get("conditionId", cond),
                    "token_id": token_id,
                    "outcome": outcome,
                    "side": t.get("side", ""),
                    "price": float(t["price"]),
                    "size": float(t["size"]),
                    "wallet": t.get("proxyWallet", ""),
                }
            )
    return store.append_trades(rows)


ACTIVITY_MIN_TRADES = 10  # tape prints needed to qualify as a candidate
ACTIVITY_MAX_WALLETS = 500  # ceiling on one run's API load, not a finding
ACTIVITY_PAGE_PAUSE = 0.3  # seconds; this job is the venue's guest
ACTIVITY_FLUSH_WALLETS = 25  # flush interval — see sample_activity on why buffering all of it is not an option


def candidate_wallets(min_trades: int = ACTIVITY_MIN_TRADES,
                      max_wallets: int = ACTIVITY_MAX_WALLETS) -> list[str]:
    """Addresses seen often enough in our own tape to be worth a history pull.

    Recomputed per run rather than frozen: the point is to catch an address
    BEFORE its history rolls past clob.ACTIVITY_MAX_OFFSET, so a newly-active
    address has to be able to join. `max_wallets` bounds the API load; it is an
    operational limit, so if it ever binds the selection is truncated by trade
    count and that must not be read as a population boundary.
    """
    d = store.LAMINAR_DIR / "trades"
    files = sorted(d.glob("*.parquet"))
    if not files:
        return []
    df = pl.concat([pl.read_parquet(f) for f in files], how="diagonal")
    counts = (
        df.filter(pl.col("wallet").is_not_null() & (pl.col("wallet") != ""))
        .group_by("wallet")
        .agg(pl.len().alias("n"))
        .filter(pl.col("n") >= min_trades)
        .sort("n", descending=True)
    )
    if counts.height > max_wallets:
        print(f"  WARNING candidate set truncated {counts.height} -> {max_wallets} "
              f"by ACTIVITY_MAX_WALLETS; raise it or the tail is never sampled")
    return counts.head(max_wallets)["wallet"].to_list()


def sample_activity(wallets: list[str], max_pages: int = 4) -> int:
    """Pull each wallet's own trade history.

    Two different ceilings, and conflating them would misread the data:
      * `max_pages` is OURS — a daily top-up only needs the recent prints, since
        the store dedupes and earlier runs already hold the rest.
      * clob.ACTIVITY_MAX_OFFSET is the VENUE'S — past it the endpoint 400s and
        the remaining history is simply not obtainable. Reaching it means this
        address is permanently only partially known.
    Both are reported per wallet, separately, because only the second one is a
    fact about the world.

    Default max_pages is deliberately shallow. The deep first pass is a manual
    run with an explicit --pages; a daily job that re-walked every address to
    the ceiling would be a de-facto scrape of the whole venue.

    Flushes every ACTIVITY_FLUSH_WALLETS rather than once at the end. The
    droplet has 961MB and runs the per-minute `books` collector, so buffering a
    deep pass over hundreds of addresses (up to 5.5k prints each) would OOM —
    and the OOM killer would be as likely to take `books` as to take this, which
    would put a hole in the pre-registered window to collect a side dataset.
    Flushing also means a mid-run failure keeps the wallets already done, which
    matters because this history is perishable.
    """
    stored = 0
    rows: list[dict] = []
    at_ceiling: list[str] = []  # the venue cut us off — history is UNREACHABLE
    at_our_cap: list[str] = []  # max_pages cut us off — history is still there
    for i, w in enumerate(wallets, 1):
        pages, complete = 0, False
        while pages < max_pages:
            offset = pages * 500
            if offset > clob.ACTIVITY_MAX_OFFSET:
                at_ceiling.append(w)
                break
            try:
                page = clob.activity(w, limit=500, offset=offset)
            except RuntimeError as e:
                # The 400 at the ceiling is the expected end of the road, not a
                # failure — but a genuine outage must still fail loudly, so only
                # a request at/near the known ceiling is allowed to pass quietly.
                if offset >= clob.ACTIVITY_MAX_OFFSET - 500:
                    at_ceiling.append(w)
                    break
                raise RuntimeError(f"activity({w}) at offset={offset}: {e}") from e
            for a in page:
                rows.append(
                    {
                        "wallet": w,
                        "ts": int(a["timestamp"]) * 1000,  # data-api gives seconds
                        "tx_hash": a.get("transactionHash", ""),
                        "condition_id": a.get("conditionId", ""),
                        "token_id": a.get("asset", ""),
                        "outcome": a.get("outcome", ""),
                        "outcome_index": int(a.get("outcomeIndex") or 0),
                        "side": a.get("side", ""),
                        "price": float(a.get("price") or 0),
                        "size": float(a.get("size") or 0),
                        "usdc_size": float(a.get("usdcSize") or 0),
                        "market_slug": a.get("slug", ""),
                        "title": a.get("title", ""),
                    }
                )
            pages += 1
            if len(page) < 500:
                complete = True
                break
            time.sleep(ACTIVITY_PAGE_PAUSE)
        # A run that ends on a FULL page has not reached the end of the history,
        # whatever stopped it. Only a short page proves completeness — inferring
        # it from "the loop finished" is how a truncated tail gets recorded as a
        # whole history, which nothing downstream can then detect.
        if not complete and w not in at_ceiling:
            at_our_cap.append(w)
        if i % ACTIVITY_FLUSH_WALLETS == 0:
            stored += store.append_activity(rows)
            rows = []
            print(f"  ... {i}/{len(wallets)} wallets, {stored} rows stored", flush=True)
    stored += store.append_activity(rows)
    if at_our_cap:
        print(f"  WARNING {len(at_our_cap)}/{len(wallets)} wallet(s) stopped at our "
              f"{max_pages}-page cap with a full page — history NOT exhausted; "
              f"re-run with a larger --pages to reach the rest")
    if at_ceiling:
        print(f"  WARNING {len(at_ceiling)}/{len(wallets)} wallet(s) hit the venue's "
              f"offset ceiling ({clob.ACTIVITY_MAX_OFFSET}); their earlier history is "
              f"UNREACHABLE, not absent: {', '.join(w[:10] for w in at_ceiling[:8])}"
              + (" ..." if len(at_ceiling) > 8 else ""))
    return stored


def sample_leads(backfill_pages: int = 1) -> int:
    """Record new PolyBeats leads. Recording only — no market is resolved and no
    view is formed here; that is deliberate, see laminar_20260814_preregistration.md.

    Leads are perishable (the channel's public preview only exposes recent
    pages), so this runs hourly even though the channel posts ~6/day.
    """
    since = store.max_lead_id()
    leads = polybeats.fetch_since(since, max_pages=backfill_pages)
    now_ms = _now_ms()
    rows = [
        {
            **lead,
            "fetched_at": now_ms,
            "trackable": bool(lead["event_slug"]),
        }
        for lead in leads
    ]
    return store.append_leads(rows)


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "watchlist":
        chosen = build_watchlist()
        print(f"watchlist frozen: {len(chosen)} markets -> {WATCHLIST_PATH}")
        for m in chosen:
            print(f"  {clob.daily_rate(m):7.1f}/day  {m['market_slug'][:70]}")
    elif cmd == "books":
        print(f"book levels stored: {sample_books()}")
    elif cmd == "markets":
        print(f"market rows stored: {sample_markets()}")
    elif cmd == "trades":
        print(f"trade rows stored: {sample_trades()}")
    elif cmd == "competitiveness":
        print(f"competitiveness rows stored: {sample_competitiveness()}")
    elif cmd == "leads":
        pages = int(sys.argv[2]) if len(sys.argv) > 2 else 1
        print(f"new PolyBeats leads stored: {sample_leads(pages)}")
    elif cmd == "activity":
        args = sys.argv[2:]
        pages = int(args[args.index("--pages") + 1]) if "--pages" in args else 4
        wallets = [w for w in args if w.startswith("0x")]
        if not wallets:
            wallets = candidate_wallets()
            print(f"candidates from the tape: {len(wallets)} wallets "
                  f"(>={ACTIVITY_MIN_TRADES} prints)")
        if not wallets:
            raise SystemExit("no candidate wallets — is the trade tape populated?")
        print(f"activity rows stored: {sample_activity(wallets, max_pages=pages)}")
    elif cmd == "track":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 50
        n_leads, n_rows = track.resolve_leads(limit)
        snaps = track.snapshot_pools()
        print(
            f"leads resolved: {n_leads} (+{n_rows} market rows); "
            f"pool snapshots stored: {snaps}"
        )
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
