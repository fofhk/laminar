"""Pool tracker for PolyBeats leads — resolve, then snapshot hourly.

Two stages, deliberately separate:

  resolve   lead -> the market(s) its event contains, recorded once and never
            rewritten. Runs over every trackable lead including the June-July
            backfill, because the map is also what a later price-history
            backfill will join on.
  snapshot  hourly book + reward-pool state for the markets whose lead is still
            inside the tracking window.

Recording only. Nothing here scores a lead, ranks one, or forms a view on
whether smart money is worth following — per the pre-registration addendum of
2026-08-18, that question is answered after the window, not designed into the
collector.

Why the pool matters more than the price here: price and trade history are
backfillable from public endpoints, so a lead's directional outcome can be
reconstructed later. Reward rate, book depth and the competing field are LIVE
state with no history endpoint — if this job does not capture them at the time,
they are gone. That asymmetry is the entire reason this runs on a cron instead
of being deferred to the analysis.
"""

from __future__ import annotations

from laminar import clob, score, store

# How long after a post we keep snapshotting. Bounds the tracked set: at ~10
# leads/day this holds ~70 leads live at once. Not a claim that a lead's effect
# decays in a week — it is a capacity choice, and 7 days is long enough to
# cover the pool-decay horizon the 2026-08-16 report flagged.
TRACK_DAYS = 7

# One event can hold 26 markets (seen: "what price will WTI hit in august
# 2026"). Tracking every leg of every event would multiply the job by an order
# of magnitude for legs no one bet on, so fan-out is capped by 24h volume —
# with the market the post NAMED always kept regardless of rank.
MAX_MARKETS_PER_LEAD = 8

_DAY_MS = 86_400_000


def resolve_leads(limit: int = 50) -> tuple[int, int]:
    """Map unresolved leads onto markets. Returns (leads_resolved, rows_added).

    `limit` caps gamma calls per run so the hourly job stays quick; the initial
    backfill just runs it repeatedly.
    """
    leads = store.load_leads()
    if leads.is_empty():
        return 0, 0
    known = set(store.load_lead_markets()["msg_id"].to_list())
    todo = [
        r
        for r in leads.sort("msg_id", descending=True).iter_rows(named=True)
        if r["is_smart_money"] and r["trackable"] and r["msg_id"] not in known
    ][:limit]

    rows: list[dict] = []
    resolved = 0
    for lead in todo:
        markets = clob.event_markets(lead["event_slug"])
        if not markets:
            continue  # delisted or renamed slug — retried next run, not fatal
        resolved += 1
        named = lead["market_slug"]
        ranked = sorted(markets, key=lambda m: m["volume_24hr"], reverse=True)
        keep = [m for m in ranked if m["market_slug"] == named]
        keep += [m for m in ranked if m["market_slug"] != named][
            : MAX_MARKETS_PER_LEAD - len(keep)
        ]
        posted_ms = _iso_to_ms(lead["posted_at"])
        for m in keep:
            for tok in m["tokens"]:
                rows.append(
                    {
                        "msg_id": lead["msg_id"],
                        "event_slug": lead["event_slug"],
                        "condition_id": m["condition_id"],
                        "market_slug": m["market_slug"],
                        "question": m["question"][:200],
                        "token_id": tok["token_id"],
                        "outcome": tok["outcome"],
                        "end_date_iso": m["end_date_iso"],
                        "resolved_at": store_now(),
                        "track_until": posted_ms + TRACK_DAYS * _DAY_MS,
                        "closed": m["closed"],
                        "named_by_lead": m["market_slug"] == named,
                        "volume_24hr": m["volume_24hr"],
                    }
                )
    return resolved, store.append_lead_markets(rows)


def active_tracks(now_ms: int) -> list[dict]:
    """Markets still inside their lead's tracking window and not settled."""
    lm = store.load_lead_markets()
    if lm.is_empty():
        return []
    live = lm.filter(
        (~lm["closed"]) & (lm["track_until"] > now_ms) & (lm["resolved_at"] <= now_ms)
    )
    return live.to_dicts()


def snapshot_pools(now_ms: int | None = None) -> int:
    """One hourly pass over every actively tracked token."""
    now_ms = now_ms if now_ms is not None else store_now()
    tracks = active_tracks(now_ms)
    if not tracks:
        return 0

    # Reward config is per MARKET; fetch once per condition, not once per token.
    by_condition: dict[str, dict] = {}
    for t in tracks:
        by_condition.setdefault(t["condition_id"], {})
    for cond in by_condition:
        try:
            by_condition[cond] = clob.rewards_market(cond) or {}
        except Exception:  # noqa: BLE001 — one dead market must not kill the pass
            by_condition[cond] = {}

    rows: list[dict] = []
    for t in tracks:
        meta = by_condition.get(t["condition_id"]) or {}
        # `rewards_config` is a LIST of per-asset configs (summed, same as
        # collect.sample_competitiveness), while max_spread/min_size sit at the
        # top level — not inside it.
        max_spread = float(meta.get("rewards_max_spread") or 0)
        min_size = float(meta.get("rewards_min_size") or 0)
        daily_rate = float(
            sum(c.get("rate_per_day", 0) for c in (meta.get("rewards_config") or []))
        )
        competitiveness = float(meta.get("market_competitiveness") or 0)
        try:
            bk = clob.book(t["token_id"])
        except Exception:  # noqa: BLE001 — same, skip this token this hour
            continue
        bids = [score.Level(float(x["price"]), float(x["size"])) for x in bk.get("bids", [])]
        asks = [score.Level(float(x["price"]), float(x["size"])) for x in bk.get("asks", [])]
        bid = max((lv.price for lv in bids), default=0.0)
        ask = min((lv.price for lv in asks), default=0.0)
        # Fall back to the raw midpoint when there is no reward config, so a
        # market that loses its pool keeps producing comparable price rows
        # instead of silently dropping out of its own case.
        mid = score.adjusted_midpoint(bids, asks, min_size) if min_size else None
        if mid is None:
            mid = (bid + ask) / 2.0 if bid and ask else 0.0
        band = max_spread / 100.0

        for outcome_side, levels in (("bid", bids), ("ask", asks)):
            in_band = (
                sum(lv.size for lv in levels if abs(lv.price - mid) <= band)
                if band > 0
                else 0.0
            )
            rows.append(
                {
                    "ts": now_ms,
                    "msg_id": t["msg_id"],
                    "condition_id": t["condition_id"],
                    "token_id": t["token_id"],
                    # `outcome` is the token's Yes/No; the book side is folded in
                    # so one row per (token, side) keeps both books comparable.
                    "outcome": f"{t['outcome']}/{outcome_side}",
                    "hours_since_lead": max(
                        0.0, (now_ms - (t["track_until"] - TRACK_DAYS * _DAY_MS)) / 3_600_000
                    ),
                    "best_bid": bid,
                    "best_ask": ask,
                    "midpoint": mid,
                    "spread": (ask - bid) if bid and ask else 0.0,
                    "band_depth": in_band,
                    "book_depth": sum(lv.size for lv in levels),
                    "n_levels": len(levels),
                    "daily_rate": daily_rate,
                    "max_spread": max_spread,
                    "min_size": min_size,
                    "competitiveness": competitiveness,
                }
            )
    return store.append_lead_tracks(now_ms, rows)


def _iso_to_ms(iso: str) -> int:
    from datetime import datetime

    if not iso:
        return 0
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


def store_now() -> int:
    from datetime import UTC, datetime

    return int(datetime.now(UTC).timestamp() * 1000)
