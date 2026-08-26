"""Polymarket CLOB read-only client. No keys, no signing, no orders.

Stage 1 touches only public endpoints. Anything that would require an API
credential belongs to Stage 3 and is deliberately absent from this module.

Endpoint shapes verified against the live API on 2026-08-14 from the SGP
droplet (the Mac's ISP DNS-blocks polymarket.com).
"""

import json
import time

import requests

BASE = "https://clob.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
# /activity 400s at offset=5500 (probed 2026-08-22, four separate wallets), so
# 5000 is the last offset that returns. Not documented by Polymarket and not
# a page count — a hard ceiling of ~5.5k prints per address, whatever `limit`
# is. There is no `since` cursor to work around it with: an address busier than
# ~5.5k prints has history that is already unreachable and rolls further out of
# reach every day. This is the binding constraint on any per-address study.
ACTIVITY_MAX_OFFSET = 5000
GAMMA = "https://gamma-api.polymarket.com"
TIMEOUT = 20


def _get(base: str, path: str, params: dict | None = None, retries: int = 3):
    last: Exception | None = None
    for attempt in range(retries):
        try:
            r = requests.get(f"{base}{path}", params=params, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001 — retried, then raised
            last = e
            time.sleep(2**attempt)
    raise RuntimeError(f"GET {path} failed after {retries} attempts: {last}")


def sampling_markets() -> list[dict]:
    """Every market currently carrying a liquidity-reward pool.

    Cursor-paginated at 1000/page. `next_cursor == "LTE="` marks the end.
    """
    out: list[dict] = []
    cursor = ""
    while True:
        page = _get(BASE, "/sampling-markets", {"next_cursor": cursor} if cursor else None)
        out.extend(page["data"])
        cursor = page.get("next_cursor") or ""
        if not cursor or cursor == "LTE=":
            return out


def book(token_id: str) -> dict:
    """Full order book for one outcome token.

    The API returns bids ascending and asks descending — i.e. index 0 is the
    WORST price on both sides. Callers must not assume index 0 is the top of
    book; use `best_bid`/`best_ask`.
    """
    return _get(BASE, "/book", {"token_id": token_id})


def best_bid(bk: dict) -> float | None:
    return max((float(x["price"]) for x in bk["bids"]), default=None)


def best_ask(bk: dict) -> float | None:
    return min((float(x["price"]) for x in bk["asks"]), default=None)


def daily_rate(market: dict) -> float:
    """USDC/day pool for a market, summed across reward assets (0 if none)."""
    rates = (market.get("rewards") or {}).get("rates") or []
    return sum(float(r.get("rewards_daily_rate", 0)) for r in rates)


def rewards_market(condition_id: str) -> dict | None:
    """Polymarket's OWN per-market reward snapshot — discovered 2026-08-14,
    not in any docs found. Includes `market_competitiveness` (a single scalar,
    formula undocumented) and `rewards_config` (which can disagree with
    `/sampling-markets`'s `rewards.rates[].rewards_daily_rate` for the same
    market at the same moment — seen live, e.g. 500 vs 200 on
    fed-rate-hike-in-2026 within the same day; not yet explained, being
    tracked rather than assumed). None if the market has no reward config.
    """
    data = _get(BASE, f"/rewards/markets/{condition_id}").get("data") or []
    return data[0] if data else None


def trades(condition_id: str, limit: int = 1000) -> list[dict]:
    """Recent REAL executed trades for a market — the public trade tape, not a
    price-delta inference. Newest first; `limit` up to 1000 confirmed working.

    Each print carries `proxyWallet`, so the trade is attributable to an address
    with no auth. `side` and `proxyWallet` describe THE SAME PARTY, and that
    party is the TAKER. Established 2026-08-20 on 939 collected prints, three
    checks:

      * one row per `tx_hash`, never two — a match is reported once, not once
        per side, so `proxyWallet` is one of the two parties, not both;
      * 55/55 sampled prints agreed with that same wallet's own `side` in
        /activity, 0 opposite — so `side` is this row's wallet's own direction,
        not the counterparty's;
      * priced against our own per-minute book: 675 TAKER vs 30 MAKER, with 234
        landing strictly inside the spread — consistent with a book snapshot up
        to 120s stale, not with a maker reading.

    So: BUY = this wallet crossed the ask (a resting ASK was touched); SELL =
    it crossed the bid (a resting BID was touched). The 3% reading as MAKER is
    within the staleness error and is not evidence of a mixed convention, but
    it is also not zero — any per-trade role claim should re-derive the role
    from the book rather than assume it.

    Coverage caveat: this endpoint has no documented `since` cursor, so a
    market trading faster than `limit` prints between polls will silently lose
    the middle of its history — store.append_trades's dedupe only protects
    against re-fetching the same page, not against a page never fetched. Fine
    for the thin long-dated political markets Stage 1 watches; would need a
    tighter poll interval or a real cursor for anything higher-volume.
    """
    return _get(DATA_API, "/trades", {"market": condition_id, "limit": limit}) or []


def activity(wallet: str, limit: int = 500, offset: int = 0) -> list[dict]:
    """One address's own trade history — public, unauthenticated.

    `trades()` answers "what happened in this market"; this answers "what has
    this address ever done", which is what a skill/luck classification needs and
    what the trade tape alone cannot give (the tape only holds moments we were
    polling, and only markets on our watchlist).

    Offset-paginated; a short page means the end. `type=TRADE` filters out
    deposits/redeems, which would otherwise pollute a PnL series.

    Bounded by ACTIVITY_MAX_OFFSET — past it the endpoint 400s, so a heavy
    address's history is NOT fully reachable here. Callers must treat a run
    that reaches the bound as truncated, not as the end of the history.
    """
    return _get(DATA_API, "/activity",
                {"user": wallet, "type": "TRADE", "limit": limit, "offset": offset}) or []


def event_markets(event_slug: str) -> list[dict]:
    """Markets belonging to one Polymarket EVENT, via the Gamma API.

    A PolyBeats lead links an `/event/<slug>`, but the CLOB works in markets —
    and an event is not one market. "what price will WTI hit in august 2026"
    is a single event holding 26 of them. Every consumer of this must therefore
    handle fan-out; a lead pointing at an event is a lead pointing at a SET.

    Returns [] for an unknown slug rather than raising, because leads reference
    events that may since have been delisted, and one dead slug must not stop a
    batch resolve.

    Fields are normalised here because Gamma returns `clobTokenIds` and
    `outcomes` as JSON-encoded STRINGS inside the JSON, not as arrays.
    """
    events = _get(GAMMA, "/events", {"slug": event_slug}) or []
    if not events:
        return []
    out = []
    for m in events[0].get("markets") or []:
        try:
            token_ids = json.loads(m.get("clobTokenIds") or "[]")
            outcomes = json.loads(m.get("outcomes") or "[]")
        except (TypeError, ValueError):
            continue
        if not token_ids:
            continue
        out.append(
            {
                "condition_id": m.get("conditionId", ""),
                "market_slug": m.get("slug", ""),
                "question": m.get("question", ""),
                "end_date_iso": m.get("endDateIso") or m.get("endDate") or "",
                "tokens": [
                    {"token_id": str(t), "outcome": str(o)}
                    for t, o in zip(token_ids, outcomes, strict=False)
                ],
                "closed": bool(m.get("closed")),
                "active": bool(m.get("active")),
                "volume_24hr": float(m.get("volume24hr") or 0),
                "liquidity": float(m.get("liquidityClob") or m.get("liquidity") or 0),
                "max_spread": float(m.get("rewardsMaxSpread") or 0),
                "min_size": float(m.get("rewardsMinSize") or 0),
            }
        )
    return out
