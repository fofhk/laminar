"""PolyBeats lead ingestion — read-only scrape of a public Telegram channel.

Source: https://t.me/s/PolyBeats_Bot (the channel's public web preview). No
auth, no API key, no session file — deliberately independent of the iKnow
digest pipeline, which renders to HTML and persists nothing structured.

What this is for: PolyBeats posts when it detects "smart money" taking a
position on Polymarket. Each such post is a LEAD. Stage 1 only records leads
and resolves them to a market; it forms no view on whether they are worth
acting on. That question is deliberately left to the end of the observation
window, when there are enough cases to look at.

Post formats seen in the wild (2026-08-18 sample of 20). They degrade — the
channel is a free tier of a paid product, so detail is inconsistent:

  full     event URL + market slug + wallet address + stake + entry/current price
  partial  event URL + per-account sector stats, no stake
  vague    "买入 <topic> 相关市场" — no link at all, not resolvable to a pool
  news     no smart money mentioned; ordinary market commentary

`body` is stored verbatim (minus the subscription boilerplate) precisely
because the extractors below only cover what today's formats expose. If the
channel changes shape, the raw text is the fallback and nothing is lost.
"""

from __future__ import annotations

import html
import re
import time

import requests

CHANNEL = "PolyBeats_Bot"
PREVIEW = f"https://t.me/s/{CHANNEL}"
TIMEOUT = 20
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# Everything from here down is the subscription pitch + channel signature,
# identical on every post and pure noise for analysis.
_BOILERPLATE = re.compile(
    r"(订阅\s*BlockBeats\s*会员|如需订阅\s*BlockBeats|-{10,}|让你更早看到未来)", re.S
)
_MSG_SPLIT = re.compile(r'(?=<div class="tgme_widget_message [^"]*"[^>]*data-post=)')
_TEXT_BLOCK = re.compile(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.S)


def _get(url: str, params: dict | None = None, retries: int = 3) -> str:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, headers={"User-Agent": UA}, timeout=TIMEOUT)
            r.raise_for_status()
            return r.text
        except Exception as e:  # noqa: BLE001 — retried, then raised
            last = e
            time.sleep(2**attempt)
    raise RuntimeError(f"GET {url} failed after {retries} attempts: {last}")


def _strip_tags(fragment: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", fragment)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def _num(raw: str, suffix: str = "") -> float:
    """PolyBeats writes money as $650, $10.0k, $483k, $3.2m."""
    value = float(raw.replace(",", ""))
    return value * {"k": 1e3, "m": 1e6, "b": 1e9}.get(suffix.lower(), 1.0)


def parse_message(block: str) -> dict | None:
    """One message div -> a lead row, or None if it is not a message."""
    post = re.search(r'data-post="[^/"]+/(\d+)"', block)
    if not post:
        return None
    stamps = re.findall(r'<time datetime="([^"]+)"', block)
    blocks = [_strip_tags(b) for b in _TEXT_BLOCK.findall(block)]
    text = "\n".join(b for b in blocks if b)
    body = _BOILERPLATE.split(text)[0].strip()
    headline = body.split("\n")[0].strip() if body else ""

    hrefs = re.findall(r'href="([^"]+)"', block)
    # The first polymarket.com link on every post is the channel's own referral
    # (`/?via=PolyBeats`); the market link, when present, carries a real path.
    event = next(
        (m for h in hrefs if (m := re.search(r"polymarket\.com/event/([^/?#\"]+)(?:/([^/?#\"]+))?", h))),
        None,
    )
    wallets = re.findall(r"polymarket\.com/profile/(0x[0-9a-fA-F]{40})", " ".join(hrefs))

    accounts = re.findall(r"胜率为\s*(\d+)\s*/\s*(\d+)", body)
    pnls = re.findall(r"板块净盈利\s*\$([\d.,]+)\s*([kKmMbB]?)", body)
    n_stated = re.search(r"(\d+)\s*名(?:盈利[^名]*?)?聪明钱", body)
    stake = re.search(r"投入\s*\$([\d.,]+)\s*([kKmMbB]?)", body)
    direction = re.search(r"买入\s*「(是|否)」|投入\s*\$[\d.,]+[kKmMbB]?\s*「(是|否)」", body)
    entry = re.search(r"平均买入概率为\s*([\d.]+)\s*%", body)
    current = re.search(r"目前「?[是否]?」?概率为\s*([\d.]+)\s*%", body)

    n_accounts = int(n_stated.group(1)) if n_stated else len(accounts)
    win_rates = [int(w) / int(t) for w, t in accounts if int(t)]

    return {
        "msg_id": int(post.group(1)),
        "posted_at": stamps[-1] if stamps else "",
        "headline": headline[:300],
        "body": body,
        "is_smart_money": "聪明钱" in body,
        "n_accounts": n_accounts,
        "direction": (direction.group(1) or direction.group(2)) if direction else "",
        "stake_usd": _num(*stake.groups()) if stake else 0.0,
        "avg_entry_pct": float(entry.group(1)) if entry else 0.0,
        "current_pct": float(current.group(1)) if current else 0.0,
        "event_slug": event.group(1) if event else "",
        "market_slug": (event.group(2) or "") if event else "",
        "wallets": ",".join(dict.fromkeys(wallets)),
        "best_win_rate": max(win_rates) if win_rates else 0.0,
        "sector_pnl_usd": sum(_num(v, s) for v, s in pnls),
        "url": f"https://t.me/{CHANNEL}/{post.group(1)}",
    }


def fetch_page(before: int | None = None) -> list[dict]:
    """One page of the public preview — 20 messages, oldest first.

    `before` walks backwards through history for backfill; omit it for the
    newest page.
    """
    page = _get(PREVIEW, params={"before": before} if before else None)
    leads = [lead for block in _MSG_SPLIT.split(page) if (lead := parse_message(block))]
    return sorted(leads, key=lambda x: x["msg_id"])


def fetch_since(min_msg_id: int = 0, max_pages: int = 25) -> list[dict]:
    """Walk backwards until we reach `min_msg_id`, then return everything newer.

    Pass the highest msg_id already stored to pick up only what is new. With
    the default it fetches one page, which is what the hourly cron wants —
    the channel posts ~6/day, so 20 messages is over three days of slack.
    """
    seen: dict[int, dict] = {}
    cursor: int | None = None
    for _ in range(max_pages):
        page = fetch_page(cursor)
        if not page:
            break
        seen.update({lead["msg_id"]: lead for lead in page})
        oldest = min(lead["msg_id"] for lead in page)
        if oldest <= min_msg_id + 1:
            break
        cursor = oldest
    return sorted(
        (lead for lead in seen.values() if lead["msg_id"] > min_msg_id),
        key=lambda x: x["msg_id"],
    )
