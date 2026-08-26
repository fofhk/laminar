# Pre-registration — Laminar Stage 1: Polymarket reward-score reconstruction (2026-08-14)

Written BEFORE the first collector tick and before any capital is committed. Stage 1 answers
one question and commits no money to answering it:

> **Can the liquidity-reward payout of a hypothetical maker order be reconstructed from the
> public order book accurately enough to size a strategy — and if so, what does the reward
> stream actually pay, net of the inventory it forces you to hold?**

Stage 1 is read-only. No account, no wallet, no orders. Failing Stage 1 is a valid and cheap
outcome; it is the point of doing it before Stage 3.

## Jurisdiction (governs everything downstream)

- The Mac sits behind Telstra AU: `polymarket.com` resolves to `block.acma.gov.au`.
- The Farseer droplet (SGP1, `DROPLET_IP`) reaches `clob.polymarket.com` fine (HTTP 200
  on `/sampling-markets` and `/book`, verified 2026-08-14).
- **Both AU and SG are "close-only" jurisdictions** for Polymarket — existing positions may be
  closed, new positions may not be opened. SG carries a Gambling Regulatory Authority penalty
  (to SGD 10,000 / 6 months, in force since 2025-01).
- **Therefore:** Stage 1 (reading public market data) runs on the SGP droplet and raises no
  issue — reading a public API is not trading. **Stage 3 (live orders) is blocked** until the
  operating entity and its jurisdiction are settled. Relocating a server does not settle it.
- Stage 3 will additionally need **its own droplet in a permitted region**, not this one:
  the Farseer box is documented as holding a funded Hyperliquid key at its own Stage 2, and
  `docs/droplet-provisioning.md` explicitly excludes other projects' code from it. A second
  funded key (Polygon) on the same box doubles the blast radius for no benefit.

### Region survey (2026-08-14) — one DO region qualifies, and only technically

Polymarket's own geoblock reference defines three tiers: full block (OFAC), **Tier 2 close-only
across frontend AND API**, and **Tier 3 close-only on the frontend only, with the API not
restricted**. Mapped onto DigitalOcean's 12 droplet regions:

| region | country | status |
|---|---|---|
| NYC1/2/3, SFO2/3, ATL1, RIC1, MKC1 | US | Tier 2 |
| TOR1 | Canada | Tier 2 (select provinces) |
| LON1 · FRA1 · SGP1 · SYD1 | UK · DE · SG · AU | Tier 2 |
| BLR1 | India | **banned outright** 2026-05-21 (PROGA 2025, MeitY ISP-level) |
| **AMS3** | **Netherlands** | **Tier 3 — API not restricted** |

AMS3 is the only match, and it also sits near Polymarket's own infra (their docs cite
`eu-west-2` primary / `eu-west-1` nearest unrestricted) — materially better latency than SGP1.
The other two Tier-3 jurisdictions (Ireland, Japan) have no DO region.

**This is a statement about Polymarket's technical geoblocking, not about legality.** The ToS
binds on the user's location, not the server's; a Dutch server does not relocate an operator.
AMS3 resolves Stage 3's *reachability* and leaves the entity/jurisdiction question exactly where
it was.

**Do not migrate the Farseer droplet to reach it.** DO has no in-place region change (snapshot →
transfer → rebuild → destroy, new IP), and SGP1 was chosen for Hyperliquid/Tokyo latency, its IP
is whitelisted in the mail box's rspamd (alerting dies silently on an IP change), and it holds
minute bars REST cannot backfill. Stage 3 gets a new box; nothing needs buying before then.

**Verify empirically before committing to any region** — geoblock policy changes and docs lag:

```
curl -s https://polymarket.com/api/geoblock
# SGP1, verified 2026-08-14: {"blocked":true,"country":"SG"}
# AMS3 must return blocked:false or the plan is void.
```

## Frozen scope (changes = a new pre-registration)

**Market class: long-dated, low-news political//macro markets only.** Explicitly NOT the
5-minute/15-minute/4-hour crypto TWAP markets, despite the $1M August pool sitting there.
Rationale, recorded so it can be judged later: that pool is *scoped to August* (17 days left
as of writing), it demands cancel-heavy requoting against rate limits that tier by 30-day
maker volume (new accounts get the tightest budget), and it puts us head-on against
crypto-native market makers. A strategy that only works while a temporary pool runs is a
strategy with an expiry date. Revisit in September when the next incentive policy is known.

**Selection filter for the Stage-1 watchlist** (applied once, frozen for the observation
window; re-derived only at a review point):

- `rewards.rates[].rewards_daily_rate` ≥ 20 USDC/day — below this the USD 1.00/day payout
  floor makes a small share round to zero.
- `end_date_iso` ≥ 90 days out — long-dated, so news arrival is rare relative to sampling.
- midpoint ∈ [0.10, 0.90] at selection — outside this band `Qmin = min(Q_one, Q_two)`, i.e.
  strictly two-sided or zero, which is a different (harder) regime; excluded from Stage 1.
- `accepting_orders == true`, `enable_order_book == true`, `neg_risk` recorded but not filtered.
- Cap the watchlist at **30 markets**. This is an observation budget, not a portfolio.

**Sampling cadence:** order books every **60 s** (matching the reward sampling interval);
`/sampling-markets` reward parameters every **1 h** (the endpoint sets `cache-control:
max-age=30`, and the parameters move slowly). Both on the SGP droplet, cron-driven, with the
same non-zero-exit-plus-error-signature alert wrapper Farseer's collector uses.

**Observation window:** 21 days minimum before any modelling conclusion is drawn. Uptime is
itself a measurement — gaps are logged, never interpolated.

## The scoring model being tested (implemented in `src/laminar/score.py`)

Per resting order, at each sample, with `v` = market `rewards.max_spread` (cents), `s` = the
order's distance from the size-adjusted midpoint, `b` = in-game multiplier:

```
S(v, s) = ((v - s) / v)^2 * b            order score = S(v, s) * size
```

Orders with `s > v` or `size < rewards.min_size` score zero. Side totals `Q_one`, `Q_two` are
the sums. With `c = 3.0`:

```
midpoint in [0.10, 0.90]:   Qmin = max( min(Q_one, Q_two), max(Q_one, Q_two) / c )
midpoint outside:            Qmin = min(Q_one, Q_two)
```

Per-sample normalisation across makers, summed over the epoch, paid daily at 00:00 UTC.

**Known unknowns, recorded now so they are not quietly resolved later:**

1. **`b` (in-game multiplier) has no documented value.** Stage 1 assumes `b = 1.0` and treats
   any market with `game_start_time` set as out of scope rather than guessing.
2. **The docs state 10,080 samples per epoch — that is 7 days of minutes — while payouts are
   daily (1,440 minutes).** These contradict. Stage 1 measures which is true by comparing
   observed payouts against both hypotheses; until then no model output is trusted in absolute
   USD terms, only in relative share terms.
3. **The denominator is not exactly reconstructible.** `/book` returns size aggregated per
   price level, not per maker, while `Qmin` is per-maker and non-linear — so the split of the
   book among makers changes the total. Given aggregate side scores `Q1`, `Q2`, both extremes
   are reachable and tight:
   - **max** — every maker is balanced, so nothing is lost to `min()`; only the unmatched
     remainder pays the penalty: `D_max = min(Q1,Q2) + |Q1-Q2| / c`
   - **min** — every maker sits at exactly the `c:1` ratio, the worst point of
     `qmin(a,b)/(a+b)`: `D_min = (Q1+Q2) / (c+1)`

   Derived consequence, stated because it sizes the risk: **on a balanced book the two bounds
   are exactly 2x apart, regardless of depth or level count** (test-pinned). So the 3x abandon
   gate below is not really a test of book depth — it is a test of how *lopsided* real
   reward-market books run. Outside the two-sided band the bounds collapse to a point
   (`min(Q1,Q2)`) and the denominator is known exactly. **Measuring the realised spread of that
   band is a Stage-1 deliverable**, not an assumption.

## Success / abandon gates (scored on the collected data, not on vibes)

Review point: **21 days of collection, or 3 consecutive days of >5% sample loss, whichever
comes first.**

- **ABANDON Stage 2** if the upper/lower denominator bound spans more than **3x** on the
  median watchlist market — the model cannot then size anything, and no amount of live capital
  fixes an unidentifiable denominator.
- **ABANDON Stage 2** if the modelled reward yield on a reward-eligible two-sided quote
  (200 shares per side ≈ 200 USDC locked, since `200p + 200(1-p) = 200` regardless of price)
  is below **10% annualised at the *upper* bound** — i.e. even the optimistic denominator
  does not clear a plain stablecoin yield, before a single cent of adverse selection.
- **PROCEED to Stage 2** only if both bounds clear that hurdle AND the observed book shows
  the reward band is reachable without crossing the spread.
- Anything between: extend the window once, to 42 days, then decide. No third extension.

**Non-gating diagnostics to collect anyway:** realised fill-implied adverse selection (how far
did midpoint travel in the 60 min after a level within the reward band was consumed?);
distribution of book spread vs `max_spread` (how often is the reward band even attainable?);
count of distinct price levels inside the band (a proxy for maker crowding); daily pool size
drift per market.

## Addendum — 2026-08-14: real trade tape added (fill-touch proxy)

User asked whether book/price deltas could be used to infer fills. Answer recorded here because
it shapes the model: **no, not safely** — this reward scheme specifically incentivises makers to
continuously cancel-and-repost toward the adjusted midpoint (the quadratic scoring term rewards
exactly that), so a tightening spread is at least as likely to be a competitor repricing as a
real trade. A price delta alone cannot distinguish the two.

Better, and still zero-capital: `data-api.polymarket.com/trades?market=<condition_id>` is a
public, unauthenticated endpoint returning the REAL executed trade tape (price, size, taker
side, timestamp), confirmed live 2026-08-14 (newest-first, `limit` up to 1000 confirmed working).
Added as a third collector (`laminar.collect trades`, hourly cron) and a new `report.fill_touch`
diagnostic: real trade volume that printed through the reference quote's price, split by side.

**This is an upper bound, not a fill probability or PnL** — queue position among makers resting
at the same price is unobservable from public data (a known open problem per the deep-dive
postmortem cited above), so a real order could have captured less than the touched volume,
including none of it. Framed to the user this way, not as a forecast.

This does not change the frozen watchlist filter, the reference-quote definition, or the
abandon/proceed gates — it adds a second, independent, real-data signal alongside the modelled
reward score, per the user's point that 21 days of collection is long enough that a rough,
clearly-bounded baseline is worth having even if later found to diverge from Stage 3 reality.

## Addendum — 2026-08-14 (later same day): collector bug found — reward params were stale

While investigating a suspicious pool-size discrepancy, found that `laminar.collect.sample_books`
read `max_spread`/`min_size`/`daily_rate` from the FROZEN watchlist JSON — the one-time snapshot
taken when the watchlist was built — instead of re-fetching live values each cycle. Confirmed on
disk: every book sample stored for `fed-rate-hike-in-2026` that day reported `daily_rate=500`
even after the market's real live pool dropped to 200 hours earlier. This silently understated
or overstated every dollar estimate for any market whose live params moved after the freeze.

Not a data-availability problem — `/sampling-markets` costs ~2.6s for the full ~12.6k-market
universe, cheap enough to re-pull every 60s cycle. Fixed: `sample_books` now re-fetches live
params each sample via a new `_live_reward_params()` helper; a token no longer reward-eligible is
skipped rather than scored on a stale number. `sample_markets`/`sample_competitiveness` were
already live-fetching and were not affected. Verified live: the stored `daily_rate` for
fed-rate-hike-in-2026 changed from 500 (stale, every sample that day) to 200 (live, matching a
fresh `/sampling-markets` pull) immediately after the fix.

**Consequence for 2026-08-14's data specifically:** early-day book samples (before the fix
landed) carry stale reward params for any market whose live params moved that day. Not corrected
retroactively — noted as a one-time data-quality caveat for this collection's first day, not a
pattern expected to recur (every day after this one collects with the fix in place).

## Addendum — 2026-08-14: `market_competitiveness` discovered, `pool_drift` added

Found `clob.polymarket.com/rewards/markets/<condition_id>` — not in any docs located — returning
`market_competitiveness` (a scalar, formula unknown) and `rewards_config` per market. Being
collected hourly (`laminar.collect competitiveness`) and shown alongside our own denominator
bound in the daily report as a second opinion — not substituted for the model until it's been
compared against our own bound over the observation window.

Also added, once the collector bug above made clear that watchlist-frozen rates go stale fast:
`report.pool_drift` (flags any market whose live daily_rate has moved >30% from the value
recorded when the watchlist was frozen) and `report.denom_ratio_distribution` (the day's full
distribution of denominator-bound ratios, not just the last sample) plus a small
`metrics_history.parquet` the daily report appends to, so the day-7/14/21 checkpoints read an
actual time series instead of re-deriving one from a stack of emails.

None of this changes the frozen watchlist filter or the abandon/proceed gates.

## Addendum — 2026-08-14: audit, macro calendar, shock ledger

**Audit of the day's work found one gap and two classification bugs**, all fixed:
- `laminar.collect competitiveness` had been run manually but its cron was never installed —
  it was collecting nothing on a schedule. Installed at `:28` hourly.
- `report.categorise` put a *senate resignation* market in `election` (the "senate" keyword)
  and both *world chess championship* markets in `election` (the greedy "win-the-20" keyword).
  Both would have corrupted the per-category shock counts that are the point of the ledger
  below. Rule order fixed (us-politics and sports now precede election); verified against all
  30 real watchlist slugs, which now bucket as fed-macro 8 / geopolitics 7 / election 5 /
  entertainment 3 / sports 3 / us-politics 2 / corporate 2, with an empty `other`.
- `detect_shocks` logged token-level shocks twice per market, because a binary market's Yes
  and No books are the same resting orders viewed from both sides. Now one row per
  (market, kind). The ledger built under the buggy logic was discarded, not migrated.

Audit items checked and found clean: no other frozen-watchlist field read as live (two grep
hits were both false positives — one writes the frozen snapshot, one iterates live data);
book sampling at 100% per-minute coverage, 58/60 tokens (the 2 missing are the known
cruz-perez-cuellar market); all alert state files at zero consecutive failures; ~165 MB
projected for the 21-day window against 18 GB free.

Noted, not fixed: `denom_ratio_distribution` costs ~91 s against a full day of samples
(measured, extrapolated from 390 samples). Acceptable for a once-daily job; would need
attention if the watchlist grows.

**Macro calendar** (`laminar/calendar.py`) — the Fed markets are the only watchlist entries
with a *scheduled* catalyst, so without this a DRIFT/BAND flag on an FOMC day is
indistinguishable from a genuine surprise. FOMC dates from the Fed's own calendar; CPI dates
from a mirror of the BLS schedule (bls.gov 403s to scripts), so treat CPI as good-but-
secondhand. Remaining 2026: CPI Sep 11, Oct 14, Nov 10, Dec 10; FOMC Sep 16 (with SEP),
Oct 28, Dec 9 (with SEP). Every shock row is stamped with the day's scheduled event, so
"expected catalyst" can later be separated from "unscheduled surprise" — a category that only
breaks on scheduled days is a very different risk from one that breaks at random.

**Shock ledger** (`data/laminar/shocks.parquet`, cumulative across days, not per-day) records
sudden collapses in three kinds — `pool_rate` (reward pool cut ≥50% vs the frozen rate),
`midpoint` (price gapped ≥25% in ~30 min), `book_depth` (in-band resting size fell ≥60%) —
each with category, market, before, after, pct_change, window, and scheduled event. Purpose,
per the user: **if collapses concentrate in one category, that category comes off the
market-making list.** Thresholds are first guesses, not calibrated; they should be revisited
at the day-7 checkpoint once the base rate is visible.

First rows logged within minutes of going live: a UK-GDP pool cut 83 → 4 USDC/day (-95%,
fed-macro), a James Bond pool cut 148 → 69 (-53%, entertainment), and a James Bond in-band
depth collapse 988 → 296 shares (-70%, entertainment). Far too few to conclude anything —
recorded here only to show the mechanism fires on real events.

## Addendum — 2026-08-18: PolyBeats lead ledger (a second, separate observation series)

Added `src/laminar/polybeats.py`, `store.LEAD_SCHEMA` / `leads.parquet`, the
`laminar.collect leads` subcommand (hourly cron at `:31`), and section 10 of the daily
report.

**Source.** `https://t.me/s/PolyBeats_Bot` — the public web preview of a Telegram channel
that posts when it detects "smart money" taking a Polymarket position. No auth, no API key,
no session file; `?before=<id>` paginates backwards. Deliberately independent of the iKnow
digest, which renders straight to HTML and persists nothing structured. Two extraction bugs
in iKnow's version of this feed were found while checking the source and are *not* inherited
here: its amount regex stops at the digits (`$53B` → 53 dollars), and it discards the message
body at render time.

**Why this does not disturb the frozen scope.** This is a *separate series*, not a change to
the watchlist, the filter, the scoring model, or the abandon gates. The frozen watchlist is 30
deliberately low-news long-dated markets; PolyBeats leads are the opposite by construction, so
overlap is near zero and the two must never be pooled. Nothing above this line changes.

**Recording only — no view is formed.** Per the user, the point of the collection window is to
have cases before defining what to look for; pre-defining the pattern would be selecting the
analysis on the same data that must test it. So the collector extracts what the posts disclose
and stores `body` verbatim as the fallback, and stops there. Whether leads are a trading signal,
a *risk* signal (informed flow is what makes a market maker lose money), or nothing at all is an
open question to be answered after the window, not a hypothesis being tested now.

**Backfill result (2026-08-18).** 499 posts recovered back to 2026-06-12 — 66 days of history
for free, far more than the ~50 cases expected from forward collection alone:

| | count |
|---|---|
| posts | 499 |
| called a position (`is_smart_money`) | 340 (68%) |
| resolve to a pool (`trackable`) | 332 (98% of signals) |
| unique events | 194 |
| stake disclosed | 227 — median $3.6k, max $852k |
| wallet address linked | 215 |

**What the historical 332 can and cannot answer.** Price and trade history are backfillable
from public endpoints, so the *directional* question ("did price move the way the smart money
bet, and when relative to the post") is testable on the full corpus today. Pool rate, book
depth and reward-denominator dynamics are live config and are **not** backfillable — only leads
tracked forward from 2026-08-18 will carry those. The two subsets must be reported separately;
merging them would silently mix 66 days of one kind of evidence with a few days of another.

**Known limits, recorded now so they are not discovered as findings later.**
- *Selection.* PolyBeats curates. An account described as "93% win rate" was chosen partly
  *because* of that record, which is the classic way to manufacture a signal that does not
  survive. Any hit rate computed on this corpus is conditional on the channel's own filter.
- *Publication lag.* The post is downstream of the trade. Measuring price movement from the
  post time conflates the smart money's edge with post-publication drift. The trade tape gives
  actual execution times and must be used to separate them.
- *Format degradation.* The free tier withholds the market link on ~2% of signals and the stake
  on ~33%. `0.0` in those fields means "not disclosed" and must never be read as a real zero.

## Addendum — 2026-08-18 (later same day): pool tracker for PolyBeats leads

Added `src/laminar/track.py`, `clob.event_markets` (Gamma API), `store.LEAD_MARKET_SCHEMA` /
`lead_markets.parquet` and `LEAD_TRACK_SCHEMA` / `lead_tracks/<day>.parquet`, the
`laminar.collect track` subcommand (hourly cron at `:34`), and a tracker status line in
report section 10.

**Two stages, deliberately separate.** `resolve` maps a lead's event onto its markets, once,
never rewritten. `snapshot` records hourly book and reward-pool state for markets whose lead is
still inside the window. Recording only — nothing here scores or ranks a lead.

**Why hourly capture is not optional.** Price and trade history are backfillable from public
endpoints, so a lead's directional outcome can be reconstructed at any time. Reward rate, book
depth and the competing field are LIVE state with no history endpoint: uncaptured is gone. That
asymmetry, not a view about leads, is why this runs on a cron.

**Bounding decisions (both are capacity choices, not findings).**
- `TRACK_DAYS = 7` — snapshots stop 7 days after the post. At ~10 leads/day this holds ~30-70
  leads live at once and lets the June-July backfill resolve without adding any polling load.
- `MAX_MARKETS_PER_LEAD = 8`, ranked by 24h volume, with the market the post NAMED always kept
  regardless of rank. Needed because an event is not a market: "what price will WTI hit in
  august 2026" is one event holding 26 of them. Observed fan-out across the 332 leads: median 8,
  mean 5.8 — the cap binds often, so any per-lead statistic must treat the tracked set as a
  volume-ranked sample of its event, not the whole event.

**State after the first pass (2026-08-18).**

| | |
|---|---|
| leads resolved | 332 of 332 trackable |
| resolution map | 3,844 lead-token rows over 1,155 unique markets |
| already settled at resolve | 2,158 rows — recorded, never snapshotted |
| window already elapsed | 1,348 rows — the June-July backfill, price-backfillable only |
| actively snapshotting | 31 leads / 138 markets / 338 tokens |
| snapshot volume | 676 rows/hour ≈ 16k/day (books remain 86k/day) |
| runtime | ~3.5 min per hourly pass |

**Two bugs found and fixed during the build, both by intent tests rather than by inspection.**
1. `append_lead_tracks` keyed on (ts, msg_id, token_id) — but a snapshot writes one row per book
   SIDE, which share all three. The dedupe silently kept only the ask side, discarding half of
   every snapshot. `outcome` (carrying "Yes/bid" / "Yes/ask") is now part of the key.
2. The first `rewards_config` reader assumed a flat dict. It is a LIST of per-asset configs, and
   `max_spread`/`min_size` sit at the TOP level as `rewards_max_spread`/`rewards_min_size`. The
   test fixture had encoded the same fiction as the code, so both agreed and both were wrong —
   only the live call settled it. Fixture now carries the verified shape.

**Limits recorded now.**
- Half the tracked markets carry no reward pool at all (696 of 1,304 snapshot rows have
  `daily_rate > 0`). PolyBeats leads are not selected for reward eligibility, so the pool series
  is dense on some cases and absent on others; the two must not be averaged together.
- `midpoint = 0.0` means one side of the book was empty, not a price of zero. `best_bid` /
  `best_ask` disambiguate.
- The 7-day window and the fan-out cap are both revisable, but changing either mid-window makes
  cases before and after non-comparable. Any change goes in this log first.

## Review checkpoints for this series

The existing Laminar checkpoints (Day 7 = 2026-08-21, Day 14 = 2026-08-28, Day 21 = 2026-09-04)
score the frozen watchlist. The lead series is younger and is reviewed on its own cadence,
deliberately earlier than the 21-day window, per the user:

- **2026-08-21 (combined with Day 7) — data quality only, no pattern claims.** 3 full days of
  hourly tracking. Check: 24/24 passes per day, no gaps; fan-out cap behaving; settled markets
  leaving the set on schedule; whether pools actually move at all after a lead. Also the point
  at which the historical 332 can get their price backfill, since that needs no waiting.
- **2026-08-25 — first complete windows.** Leads posted 2026-08-18 finish their 7 days. First
  look at whether anything is there, on ~60-70 leads with complete before/after pool series.
- **2026-08-28 (Day 14) and 2026-09-04 (Day 21)** — combined reviews with the main study.

Nothing before 2026-08-25 should produce a claim about whether leads are worth acting on. The
08-21 review exists to catch collection defects while they are still cheap to fix.

## Honesty notes

- **The reward stream is not the PnL.** Every credible account of this trade says the same
  thing: rewards are a thin bonus, and inventory losses can exceed them comfortably. Stage 1
  measures the reward stream because it is the part that is measurable without capital — that
  is a scoping decision, not a claim that it is the dominant term. Stage 2 must model adverse
  selection before any order is sized, and a positive Stage-1 result is explicitly *not*
  sufficient to trade.
- **`Polymarket/poly-market-maker` is not the starting point.** Last commit 2024-03-11, last
  release v0.0.3 (2023-02), Python 3.10, and its AMM/Bands strategies quote around the raw
  midpoint with no notion of `max_spread`, `min_size`, or `Qmin`. It predates the current
  reward regime by more than two years and optimises the wrong objective. Its value here is as
  a reference for CLOB auth/signing at Stage 3, nothing more.
- **Uptime is scored directly.** An hour of downtime is an hour of lost score with no catch-up
  mechanism. This makes collector reliability a first-class strategy parameter, not ops hygiene
  — which is why Stage 1 measures our own uptime before Stage 3 depends on it.
- **This is a farmed incentive, not an edge.** The denominator is set by competitors we cannot
  see. Any conclusion here is conditional on the competitive set staying as it was during the
  observation window, and must be re-checked, not assumed forward.
