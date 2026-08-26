# 2026-08-18 — Laminar: PolyBeats lead ledger (v1)

Batch: add a second, independent observation series to Laminar — "smart money" leads
scraped from PolyBeats — plus backfill. Stage 1 remains read-only, zero orders.

## Why

User asked whether the PolyBeats feed (seen in the iKnow digest) could drive a side branch:
when a lead appears, track the related pool hourly, record only, and revisit after enough
cases accumulate. Explicit instruction: **do not pre-define the pattern** — "类似于选股",
collect first, analyse when there are cases. That instruction shaped the design: the collector
extracts what posts disclose and stores the raw body, and forms no view.

## Source decision — direct, not via iKnow

`https://t.me/s/PolyBeats_Bot` is the channel's public web preview. `curl` → HTTP 200, 20
messages/page, ISO timestamps, full body, Polymarket links, `?before=<id>` paginates back.
**No auth, no Telethon, no API key, no session file.**

Chose this over reading iKnow because iKnow renders straight to HTML and persists nothing
structured, and because coupling a 21-day study to another project's render pipeline is
fragile. Two bugs in iKnow's version of this feed found while checking (reported, not fixed
here — they belong to iKnow, not Laminar):

1. `collectors/telegram.py:235` — amount regex `\$[\d,]+(?:\.\d+)?[kK]?` stops at the digits,
   so `$53B` parses as 53 dollars and renders `$0.1K`. `M`/`B` never captured. The
   `amount_threshold: 30000` ⭐ tier therefore ranks on noise.
2. `templates/digest.html:752` — the PolyBeats branch renders only `item.text[:70]` + amount.
   `item["body"]` (the richest field — stake, direction, wallet, account stats) is collected
   and then discarded at render time.

## Post format tiers (this drove the schema)

The channel is the free tier of a paid product, so detail degrades:

| tier | carries |
|---|---|
| full | event **and** market slug, wallet `0x…`, stake, avg entry %, current % |
| partial | event slug, per-account sector PnL + settled win rate; stake withheld |
| vague | "买入 <topic> 相关市场" — no link, not resolvable to a pool |
| news | no smart money named; ordinary commentary |

`0.0` in stake/entry/current means **not disclosed**, never a real zero. Encoded in the
schema comments and asserted in tests, because a silent zero would corrupt any size-weighted
analysis later.

## Files

| path | change |
|---|---|
| `src/laminar/polybeats.py` | **new** — fetch + parse; `fetch_page`, `fetch_since`, `parse_message`, `_num` |
| `src/laminar/store.py` | `LEAD_SCHEMA`, `append_leads` (dedupe on `msg_id`, keep first), `load_leads`, `max_lead_id` |
| `src/laminar/collect.py` | `sample_leads()` + `leads [pages]` subcommand |
| `ops/laminar_daily_report.py` | section 10 — leads recorded, trackable count, day's signals |
| `ops/run_laminar.sh` | usage string only (wrapper was already command-generic) |
| `tests/test_laminar_polybeats.py` | **new** — 7 intent tests, fixtures are verbatim real messages |
| `laminar_20260814_preregistration.md` | Addendum 2026-08-18 |

## Deployment

Droplet `DROPLET_IP` (SGP1), `/opt/farseer`, venv at `/opt/farseer/.venv`.
Copied with **explicit per-file destination paths** (bulk rsync with multiple sources
previously dumped files into the repo root — do not repeat that).

New cron, matching the existing hourly stagger:
```
31 * * * * /opt/farseer/ops/run_laminar.sh leads >/dev/null 2>&1
```
Full laminar crontab is now 6 jobs: `books` per-minute, `markets`:22, `trades`:25,
`competitiveness`:28, `leads`:31, daily report 00:15.

Tests: 311 pass on Mac; 44 pass on droplet (`tests/test_eval.py` errors there on a missing
`numpy` — pre-existing, the droplet is a slim deploy, unrelated).

## Backfill result — much larger corpus than expected

`laminar.collect leads 25` walked back 25 pages:

```
total posts      499        date range 2026-06-12 .. 2026-08-17
smart-money      340 (68%)
trackable        332 (98% of signals)   unique events 194
stake disclosed  227   median $3,600   max $852,000
wallet linked    215        direction stated 222
recent rate      ~9-11 posts/day
```

**332 usable cases exist today**, not the ~50 expected from forward collection. 66 days of
history came free because the preview paginates backwards.

## The split that matters for analysis

- **Backfillable** for the historical 332: price and trade history via public endpoints →
  the directional question is testable now.
- **Not backfillable**: pool rate, book depth, reward-denominator dynamics are live config.
  Only leads tracked forward from 2026-08-18 carry those.

Report these two subsets separately. Merging them mixes 66 days of one kind of evidence with
a few days of another.

## Limits recorded up front (so they are not "discovered" later)

- **Selection** — PolyBeats curates, and picks accounts partly *because* of their record.
  Any hit rate is conditional on the channel's own filter.
- **Publication lag** — the post is downstream of the trade. Use the trade tape's execution
  times to separate the smart money's edge from post-publication drift.
- **Format degradation** — market link missing on ~2% of signals, stake on ~33%.

## Not done / next

- Pool tracker: event slug → markets (needs a gamma-API resolution layer; `laminar/clob.py`
  currently has only `clob.` and `data-api.` bases) → hourly snapshot per tracked market.
  Sized at ~100 markets × 2 tokens × 24/day ≈ +5.5% on current volume.
- Still open from 2026-08-16: model the $1/day reward floor (~69% of a naive allocation earns
  zero), pool-decay half-life, split the shock ledger into market vs operator shocks.
- Jurisdiction decision remains the only hard blocker on Stage 3.

## User preferences observed

- Wants raw/wide collection during a collection window; resists narrowing the question early.
  Corrected me for prescribing what the leads should be used for before data existed.
- Prefers a stable curated feed over building global monitoring — accepts the selection bias
  explicitly as a trade for reliability.
- Wants the daily email to show what is currently being observed.

---

# Part 2 — pool tracker (same day)

Second batch: the lead → pool tracker. Built, tested, deployed, verified with live data.

## Design: resolve and snapshot are separate stages

| stage | what it does | cadence |
|---|---|---|
| `resolve` | lead's event → its market(s), written once, never rewritten | hourly, capped at 50 gamma calls |
| `snapshot` | hourly book + reward-pool state for markets still in window | hourly |

Split this way because the resolution map is also the join key for a later price-history
backfill over the historical corpus, which needs no waiting; the snapshot is the part that
must run now or lose data forever.

**The asymmetry that justifies the cron:** price and trade history are backfillable from
public endpoints. Reward rate, book depth and the competing field are live state with no
history endpoint — uncaptured is gone.

## New API surface

`clob.event_markets(slug)` via `https://gamma-api.polymarket.com/events?slug=<slug>`.
Returns a LIST holding one event; the event's `markets[]` is what the CLOB actually trades.
Gamma encodes `clobTokenIds` and `outcomes` as **JSON strings inside the JSON** — normalised
in the client, not at call sites. Returns `[]` for a delisted slug rather than raising, so one
dead lead cannot poison a batch resolve.

## Two bounding constants (capacity choices, not findings)

- `TRACK_DAYS = 7` — snapshots stop 7 days after the post.
- `MAX_MARKETS_PER_LEAD = 8` — ranked by 24h volume, **the market the post named is always
  kept regardless of rank**. Necessary because an event is not a market: `what-price-will-wti-
  hit-in-august-2026` is one event holding **26** markets.

Observed fan-out across 332 leads: **median 8, mean 5.8** — the cap binds often. Consequence
for analysis: the tracked set is a volume-ranked *sample* of each event, not the whole event.

## Two real bugs, both caught by tests rather than inspection

1. **Half of every snapshot was being silently discarded.** `append_lead_tracks` keyed on
   `(ts, msg_id, token_id)`, but a snapshot writes one row per book SIDE and both sides share
   all three fields. Dedupe kept only the ask. Fixed by adding `outcome` (which carries
   `Yes/bid` vs `Yes/ask`) to the key. The test asserting band-vs-total depth is what exposed it.
2. **`rewards_config` is a LIST, not a dict**, and `max_spread`/`min_size` live at the TOP
   level as `rewards_max_spread` / `rewards_min_size`. My first reader assumed a flat dict —
   *and so did my test fixture*, so the two agreed and both were wrong. Only the live call
   settled it. Lesson worth keeping: a fixture invented alongside the code it tests proves
   nothing about the API. `collect.sample_competitiveness` already had the correct idiom
   (summing `rate_per_day` across the list); should have matched it from the start.

## Live state after first full pass

```
leads resolved         332 / 332 trackable
resolution map        3,844 lead-token rows over 1,155 unique markets
  already settled     2,158 rows  (recorded, never snapshotted)
  window elapsed      1,348 rows  (Jun-Jul backfill — price-backfillable only)
actively snapshotting    31 leads / 138 markets / 338 tokens
snapshot volume         676 rows/hour ≈ 16k/day   (books remain 86k/day)
runtime                 ~3m30s per hourly pass
```

Data validation on the first 2 passes (1,304 rows): **zero nulls**, both book sides present,
`band_depth <= book_depth` on every row, midpoints within [0, 1], 696/1304 rows carry a live
reward pool.

Note `midpoint = 0.0` means one side of the book was empty, not a zero price — `best_bid` /
`best_ask` disambiguate. And only ~half of tracked markets have a reward pool at all, because
PolyBeats leads are not selected for reward eligibility; the pool series is dense on some cases
and absent on others and the two must not be averaged.

## Files (Part 2)

| path | change |
|---|---|
| `src/laminar/track.py` | **new** — `resolve_leads`, `active_tracks`, `snapshot_pools` |
| `src/laminar/clob.py` | `GAMMA` base + `event_markets()` |
| `src/laminar/store.py` | `LEAD_MARKET_SCHEMA`, `LEAD_TRACK_SCHEMA`, 4 accessors; dedupe-key fix |
| `src/laminar/collect.py` | `track [limit]` subcommand |
| `ops/laminar_daily_report.py` | tracker status line inside section 10 + gap warning |
| `tests/test_laminar_track.py` | **new** — 9 intent tests |

Tests: **320 pass on Mac**, 53 on droplet (`test_eval.py` still errors there on missing numpy —
pre-existing, slim deploy).

Cron now (7 laminar entries):
```
*  * * * *  run_laminar.sh books
22 * * * *  run_laminar.sh markets
25 * * * *  run_laminar.sh trades
28 * * * *  run_laminar.sh competitiveness
31 * * * *  run_laminar.sh leads
34 * * * *  run_laminar.sh track
15 0 * * *  laminar_daily_report.py
```

## Review checkpoints (set with user, 2026-08-18)

Deliberately earlier than the 21-day window — user explicitly did not want to wait.

| date | scope | gate |
|---|---|---|
| **2026-08-21** (combined w/ Day 7) | **data quality only** | 24/24 hourly passes, fan-out cap sane, settled markets exiting on time, do pools move at all after a lead. Also: backfill price history for the historical 332 — needs no waiting. |
| **2026-08-25** | first complete 7-day windows (~60-70 leads) | first genuine pattern look |
| **2026-08-28** (Day 14) | combined with main study | |
| **2026-09-04** (Day 21) | combined, pre-registered review point | |

**No claim about whether leads are worth acting on before 08-25.** The 08-21 review exists to
catch collection defects while they are cheap.

## Still open (carried forward)

- Model the $1/day reward floor — ~69% of a naive allocation earns zero (from 2026-08-16).
- Pool-decay half-life; split shock ledger into market vs operator shocks.
- Price/trade backfill for the historical 332 leads (can start any time).
- Jurisdiction decision — still the only hard blocker on Stage 3. Kalshi ruled out for an
  AU-resident operator (2026-08-17 analysis); Malaysia clears platform terms but fails on
  local law.
