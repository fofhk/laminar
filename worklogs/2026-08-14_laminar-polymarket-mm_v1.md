# Laminar — Polymarket liquidity-reward MM, Stage 1 stood up

**Date:** 2026-08-14 · **Version:** v1 · **Codename:** Laminar

## What this was

User wants positive cash flow from market making on Polymarket. Two questions asked, in order:
(1) new top-level directory or a subproject under `farseer`? (2) pull the current rewards rules
before committing to a plan. Both answered; Stage 1 built and smoke-tested live.

## Decision — where Laminar lives

**`farseer/src/laminar/`, a sibling of `farseer/` and `crescendo/`.** Not a new top-level repo,
not inside `crescendo`.

Driver was **extensibility (H1 dimension 1)**: the repo already pins NautilusTrader 1.230.0 for
Crescendo, which ships a native Polymarket adapter (data + execution), validated in the
2026-07-14 de-risking spike. A new repo would re-pay that cost. Security (a separate repo for
the Polygon key) was the one argument the other way and was judged weaker — same machine, same
user; creds belong in env/keychain regardless. Human overhead also favoured the subpackage: one
venv, one pyproject, one set of pre-registration/worklog conventions.

**Not inside `crescendo`** because `crescendo/DESIGN.md` is a charter with a specific objective
function (ZGG-primary directional crypto, scenario/override/side-bet ports). MM's objective is
spread + incentive capture under inventory constraints; there is no "scenario port". Shared
Nautilus backbone, separate charters. Extract shared `risk/`/`tracking/` later if reuse is real.

**Surfaced conflict:** farseer's top-level README describes the repo as one system and says
"No HFT/MM"; `docs/droplet-provisioning.md` says "Explicitly NOT on this box: other projects'
code". Both now inaccurate. Recommended (NOT yet done — user's call): reframe the top README as
a monorepo of trading systems and demote "No HFT/MM" to the `farseer` package's own scope line.

## Blocking finding — jurisdiction

- The development machine's network cannot reach Polymarket at all (DNS is
  redirected to a regulator block page), so nothing can be collected locally.
- The **SGP1** droplet reaches `clob.polymarket.com` fine — HTTP 200 on
  `/sampling-markets` and `/book`.
- **But AU and SG are both close-only jurisdictions** for Polymarket (SG: Gambling Regulatory
  Authority, since 2025-01, penalties to SGD 10,000 / 6 months). The droplet relocates the
  problem, it does not solve it.
- Resolution adopted: **Stage 1 (reading public data) runs on the SGP droplet, no issue.
  Stage 3 (live orders) is blocked** pending the operating entity/jurisdiction decision, and
  will need its own droplet in a permitted region — not the Farseer box, which will hold a
  funded Hyperliquid key of its own.

## Region survey (added same day, after "can we just move the droplet?")

Polymarket's geoblock reference defines three tiers — full block (OFAC); **Tier 2 close-only on
frontend AND API**; **Tier 3 close-only on frontend only, API not restricted**. Against DO's 12
droplet regions: US (NYC/SFO/ATL/RIC/MKC), Canada (TOR1), UK (LON1), Germany (FRA1), Singapore
(SGP1), Australia (SYD1) are all Tier 2; **India (BLR1) is banned outright** since 2026-05-21
(PROGA 2025, MeitY ISP-level block — a third-party source claiming India had full access was
stale); **AMS3 (Netherlands) is the only Tier 3 region**, i.e. the only DO location where the
API is unrestricted. Bonus: it is near Polymarket's own infra (`eu-west-2` primary per their
docs) — better latency than SGP1. Ireland and Japan are also Tier 3; DO has neither.

Caveat kept explicit: that is **technical** geoblocking, not legality. The ToS binds on the
user's location, not the server's.

**Verified empirically** — `curl https://polymarket.com/api/geoblock` from SGP1 returns
`{"blocked":true,"country":"SG"}`. That endpoint is the only trustworthy region test; docs lag.

**Do not migrate the Farseer droplet.** DO has no in-place region change (snapshot → transfer →
rebuild → destroy, IP changes), and SGP1 was picked for Hyperliquid/Tokyo latency, its IP is
whitelisted in the mail box's rspamd (an IP change silently kills alerting), and it stores
minute bars REST cannot backfill. Stage 3 gets its own box; nothing needs buying before then.

## Rewards rules as verified 2026-08-14

Source: `docs.polymarket.com/programs/liquidity-rewards` (via r.jina.ai — the domain is
DNS-blocked locally) plus live `clob.polymarket.com/sampling-markets`.

- `S(v,s) = ((v-s)/v)^2 * b`, order score `= S * size`. `v` = `max_spread` in **cents**.
- `c = 3.0`. midpoint ∈ [0.10, 0.90]: `Qmin = max(min(Q1,Q2), max(Q1,Q2)/c)` — one-sided scores
  1/3, and everything past a 3:1 side ratio is dead weight. Outside that band:
  `Qmin = min(Q1,Q2)` — two-sided or zero.
- Per-minute sampling, normalised across makers, **paid daily 00:00 UTC**, **sub-USD 1.00 days
  forfeited, not banked**.
- Live params: `rewards.min_size` = **200 shares** (not 5 USDC), `max_spread` 3.5–5.5 cents,
  `minimum_order_size` 5, `minimum_tick_size` 0.001/0.01, maker & taker base fees **0**.
- **Capital invariant:** a reward-eligible two-sided quote is 200 shares each side, costing
  `200p + 200(1-p) = 200 USDC` per market, independent of price.
- August's $1M pool sits in crypto up/down **TWAP** markets (TWAP settlement live 2026-08-07),
  BTC taking $575k. Per-market: BTC 4h ≈ $268/day, BTC 5min ≈ $33.6, BNB/DOGE 5min ≈ $2.80.
  **Scoped to August.**

**Unresolved, recorded not guessed:** `b` has no documented value (assumed 1.0; `game_start_time`
markets excluded); docs say 10,080 samples/epoch (= 7 days of minutes) while payouts are daily
(1,440) — contradictory, must be measured.

## Corrections applied to the reference advice the user brought

The other LLM's plan was directionally sane (start small, hard inventory cap, skew, kill switch)
but wrong on specifics: min order is 200 shares for rewards not 5 USDC; the per-market floor is
~200 USDC not 50–100; a 10¢ spread kill switch never fires because `max_spread` is 3.5–5.5¢;
and `Polymarket/poly-market-maker` (last commit 2024-03-11, release v0.0.3 2023-02, Python 3.10)
quotes around the raw midpoint with **no** `max_spread`/`min_size`/`Qmin` logic — it predates
the current reward regime by 2+ years. It omitted the quadratic, the 3:1 cliff, the $1/day
floor, cancel-budget tiering by 30-day maker volume, and that uptime is scored directly.

**Also rejected: its "no good backtest data, so forward-test with real money" premise.** The
scoring inputs are public, so a hypothetical order's score is computable offline at zero cost.

## Correction made mid-build (worth remembering)

First stated the reward payout was *exactly* reconstructible from the public book. It is not:
`/book` aggregates size per price level, but `Qmin` is per-maker and non-linear, so the maker
partition is unobservable. Only the numerator is exact. First attempt at bounding the
denominator was also wrong (lower bound could exceed upper on lopsided books) — caught by the
intent tests, not by inspection. Correct tight bounds:

```
D_max = min(Q1,Q2) + |Q1-Q2| / c          all makers balanced
D_min = (Q1+Q2) / (c+1)                   all makers at exactly c:1
```

Consequence: **on a balanced book the bounds are exactly 2x apart regardless of depth**, so the
3x abandon gate tests book *lopsidedness*, not depth.

## What was built

```
farseer/laminar_20260814_preregistration.md   frozen scope, model, unknowns, abandon gates
farseer/src/laminar/score.py                  reward arithmetic, pure, no I/O
farseer/src/laminar/clob.py                   read-only CLOB client (public endpoints ONLY)
farseer/src/laminar/store.py                  parquet, daily-partitioned book samples
farseer/src/laminar/collect.py                CLI: watchlist | books | markets
farseer/src/laminar/README.md
farseer/config/laminar_watchlist.json         30 markets, frozen 2026-08-14
farseer/tests/test_laminar_score.py           13 intent tests, all passing
```

Frozen selection filter: `daily_rate >= 20` USDC, `end_date >= 90d` out, midpoint ∈ [0.10,0.90],
no `game_start_time`, cap 30 markets. Top of the resulting watchlist: `fed-rate-hike-in-2026`
($500/day), `will-the-us-invade-iran-before-2027` ($400/day), Fed December rate markets
($265/$235), Iran/Hormuz ($200 each).

## First live measurement (single snapshot, 58 tokens)

Denominator bound ratio: **median 1.83x, range 1.39–1.98x, 0 of 58 above the 3x abandon gate.**
Encouraging for model identifiability — but it is one sample at one moment and proves nothing
until the 21-day window runs.

## Collection window OPENED 2026-08-14 (user go-ahead)

Cron on SGP1 — deliberately on the server, not the MacBook, since the whole point is continuous
collection and the Mac cannot even reach the API:

```
* * * * *   /opt/farseer/ops/run_laminar.sh books    >/dev/null 2>&1
22 * * * *  /opt/farseer/ops/run_laminar.sh markets  >/dev/null 2>&1
```

**Measured before installing, not assumed:**

- `books` takes **~15 s** per run (60 sequential HTTP calls) — comfortably inside a 60 s cadence,
  with `flock` guarding against pile-up if the API slows.
- **~1.1k rows/sample → 7.4 MB/day**; `markets` is ~2 MB/snapshot. ~1 GB over the 21-day window
  against 18 GB free. Fine.
- **Storage bug caught by that measurement:** books were partitioned per DAY, and every append
  rewrites its whole partition to dedupe — by evening the collector would be rewriting 1.6M rows
  every minute. **Changed to hourly partitions** (`books/<date>/<HH>.parquet`), capping it at
  ~67k. `load_books()` concatenates the hour files.

**`ops/run_laminar.sh`** follows `run_collect.sh` (log block + `notify.py` email) with two
changes a per-minute job forces: `flock`, and **alert throttling** — email only after 3
consecutive failures, then at most hourly, plus one OK on recovery. Unthrottled, 1440 runs/day
would be a mail flood, and 1440 green mails/day would train us to ignore them.

**The throttle was tested, not assumed** (2 test emails sent to MAIL_TO): failures 1 and 2
stayed silent, #3 mailed and stamped `LAST_MAIL`, #4 was throttled, and a seeded 3-failure state
followed by a success produced exactly one recovery mail. Test artifacts cleaned up.

## Scope notes added (user's instruction: annotate, don't overwrite)

Both original lines were **kept as-is** with a dated note appended, per the user's framing: this
is not a change of scope but testing/data-collection for a prediction-market-maker strategy whose
actual execution will not run under this project.

- `README.md` — "No HFT/MM" stands; footnote records that Laminar is Stage-1 measurement only,
  that Farseer remains analysis and decision support with no plan to go high-frequency, and that
  the line governs the `farseer` package rather than the repo.
- `docs/droplet-provisioning.md` — "no other projects' code" stands; note records the one narrow
  exception (a sampler that holds no keys and cannot place an order, by construction) and
  reaffirms that Laminar's *execution* stage will not run on this box.

**Coverage confirmed clean:** a 42-minute window entirely under cron (i.e. excluding the manual
test runs used to build the wrapper) showed **100% coverage, 0 gaps**. The earlier 06:12 gap was
from manual testing before the hourly-partition fix landed, not a defect in the running system.

## Daily ops report added (`ops/laminar_daily_report.py`)

User asked whether they get a daily dashboard email — they did not yet; built one, mirroring
`daily_report.py`'s contract (freshness over reachability, absence of the email is itself an
alarm). Cron'd **00:15 UTC** (after all of the prior UTC day's hourly book partitions are
written). Reports on **yesterday's** UTC day, since the reward epoch is a UTC calendar day:

- book coverage (minutes sampled / 1440), ALERT if < 90%
- watchlist age and market count
- disk usage
- **one non-gating diagnostic**: the denominator-bound ratio (median/max) from `score.py`,
  computed on the day's last sample — this is explicitly labelled "no Stage-1 conclusion" in the
  email body, so daily noise in one metric doesn't get mistaken for a verdict before the review
  point

Dry-run confirmed working: correctly ALERTed on 2026-08-13 (0% coverage — collection hadn't
started yet), which is the right answer, not a bug. First meaningful report lands 2026-08-16
00:15 UTC, covering the first full day (2026-08-15).

## Checkpoint schedule (recommended, not yet run)

- **Daily** — automatic, via the 00:15 UTC email above. No action needed unless it's red.
- **Day 7 (2026-08-21)** — informal check: coverage trend, denominator-bound trend across the
  watchlist (not just the single last-sample number the daily email shows). Catches a
  systematically-wrong assumption early, cheaply.
- **Day 14 (2026-08-28)** — same, plus a first pass at the epoch-length question (10,080 vs
  1,440 samples) if enough days have accumulated to distinguish the hypotheses.
- **Day 21 (2026-09-04)** — the pre-registered review point: run the ABANDON/PROCEED gates from
  `laminar_20260814_preregistration.md` against the full window.
- **Early trigger, any time**: 3 consecutive days of >5% sample loss ends the window early per
  the pre-registration — the daily email's coverage check is what would catch this.

## Correction: user wanted a strategy report, not an ops report

User's actual five questions (pools watched / quote params / 24h fills+fees+PnL / estimated
rewards / deltas found / anomaly alerts) don't match what a health-check email answers. Answered
plainly: **(3) fills/fees/realised PnL is structurally N/A right now** — Stage 1 places zero
orders (`laminar.clob` has no signing, no POST), so there is no fill, no fee, no position, no
PnL to report. That becomes real at Stage 3, which is blocked on the jurisdiction/entity decision
already on record, not on anything reportable differently today.

Rebuilt `ops/laminar_daily_report.py` around a new `src/laminar/report.py`, replacing the
freshness-only version from earlier today. It defines an explicit **reference quote** (a MODEL
INPUT, not a live order) so "what params" and "modelled reward" have concrete numbers to report:
`min_size` shares each side, offset = min(1.0¢, max_spread×0.5) from the adjusted midpoint. This
operationalises the pre-registration's own reference point ("a reward-eligible two-sided quote,
200 shares per side") per-market instead of leaving it abstract.

**Answers as of the first snapshot (2026-08-14, ~1h into collection, 58 tokens across 30
markets):**

1. **Pools watched:** the frozen 30-market watchlist (see preregistration). Top by pool size:
   `fed-rate-hike-in-2026` $500/day, `will-the-us-invade-iran-before-2027` $400/day, two Fed
   December meetings $265/$235.
2. **Params:** per-market `max_spread` (3.5–5.5¢) and `min_size` (20–200 shares) from
   `/sampling-markets`; reference offset ±1.00¢ on all but the two 3.5¢-band markets.
3. **24h fills/fees/PnL:** N/A — see above.
4. **Modelled reward, this reference quote, all 30 markets:** **$160–$252/day**, bounded because
   the maker-partition of the book (needed for the exact denominator) isn't reconstructible from
   `/book` alone — see `laminar_20260814_preregistration.md`. Biggest single contributors:
   `will-anna-kelly-...` ($31–44/day, tight 4.5¢ band + only 50-share floor), `will-uk-gdp-...`
   ($21–31/day). Ironically the two largest pools (`fed-rate-hike-in-2026`, `invade-iran`) rank
   near the bottom of *our* estimate — larger pools attract more competing depth, which is
   exactly the effect the model is supposed to capture, not a bug.
5. **Deltas found:** the two bugs above (denominator-bound inversion, daily→hourly partition),
   plus a live one: **2 of 60 tracked tokens (1 market) are absent from the latest sample** —
   `will-cruz-perez-cuellar-win-the-2027-chihuahua-governor-election-...` lost a qualifying side
   or stopped accepting orders sometime after the watchlist froze. Denominator bound ratio
   holding at median 1.85x / max 1.98x, both comfortably under the 3.0x abandon gate.
6. **Anomaly alerts:** no live position exists, so a literal one-sided-loss alert isn't
   answerable yet; nearest honest proxy is **DRIFT** (midpoint move over ~30 min vs the reward
   band width — would the market have run through our reference quote). Plus **BAND** (midpoint
   leaves [0.10, 0.90]) and **DENOM** (reconstruction bound past an early-warning 2.5x, ahead of
   the 3.0x gate). None fired on the first snapshot; too little history for DRIFT yet.

New tests: `tests/test_laminar_report.py`, 5 cases (reference-offset never exceeds the band,
band_breach / pool_pulled / missing-token detection, watchlist labelling) — all pass, alongside
the existing 13 in `test_laminar_score.py`.

**Process note:** a combined `rsync` with three source args and a trailing-slash directory
source dumped `laminar/`'s files into `/opt/farseer/` root instead of `/opt/farseer/src/laminar/`
— caught immediately (stray `.py` files at repo root), cleaned up, no lasting effect. `src/laminar/`
and `ops/laminar_daily_report.py` verified correct afterward.

## Fill-touch proxy added (user: "can price deltas estimate fills?")

Answered no — this reward scheme specifically incentivises cancel-and-repost toward the midpoint
(quadratic scoring), so a tightening spread is at least as likely to be a competitor repricing as
a real fill; a price delta alone can't distinguish the two. Found a better, still-zero-capital
alternative instead: `data-api.polymarket.com/trades?market=<condition_id>` is public, returns
the REAL executed trade tape (price/size/taker-side/timestamp), verified live — e.g. a genuine
$0.52 × 120.4-share BUY print on `fed-rate-hike-in-2026`. `limit` up to 1000 confirmed working,
newest-first; first poll backfilled **18,732 real trades across all 30 watchlist markets**, some
back to January 2026 (several already hit the 1000-row cap, so deeper history exists but wasn't
pulled — noted as a future extension, not built).

Added: `clob.trades()`, `store.append_trades`/`load_trades` (day-partitioned by the trade's own
timestamp), `collect.py trades` command (hourly cron, `:25`), and `report.fill_touch()` — real
trade volume that printed through the reference quote's price, split bid/ask. **Framed
explicitly as an upper bound, not a fill probability or PnL**: queue position among makers
resting at the same price is unobservable from public data (the same open problem the deep-dive
postmortem flagged). Wired into the daily email as a new subsection of Q3, with a simple
"gross spread if capped-filled" number, clearly labelled a rough baseline, not a forecast.

**Known simplification, stated not hidden:** `fill_touch` compares every trade in a day against
that day's single LATEST reference price, not the price that applied at each trade's own moment
— defensible for this watchlist (selected for slow-moving, long-dated markets), would mislead on
one that moved materially intraday. Documented in both the module docstring and the email.

5 new tests (`test_fill_touch_*`) — all pass, 20/20 total across both laminar test files, on both
Mac and droplet. Recorded as a dated addendum in `laminar_20260814_preregistration.md` — the
frozen watchlist filter, reference-quote definition, and abandon gates are unchanged.

## Real bug found and fixed: books collector never refreshed reward params

While cross-checking a pool-size discrepancy (chasing the user's "what else is worth watching"
question), found `collect.sample_books` read `max_spread`/`min_size`/`daily_rate` from the
FROZEN watchlist JSON instead of re-fetching live — confirmed on disk, every stored sample for
`fed-rate-hike-in-2026` said `daily_rate=500` for hours after the real live pool dropped to 200.
This silently corrupted every dollar estimate for any market whose params moved after freeze —
a materially different (and worse) finding than the earlier "pools drift, handled fine" claim.

Root-caused and fixed same session: `/sampling-markets` costs only ~2.6s for the ~12.6k-market
universe, cheap enough to re-pull every 60s cycle. Added `collect._live_reward_params()`, wired
into `sample_books`; verified live (stored value flipped 500 -> 200 immediately post-fix).
`sample_markets`/`sample_competitiveness` were already correct. Today's early samples (before
the fix) carry stale params for markets that moved that day — a one-time first-day caveat, not
expected to recur. All 21 tests still pass post-fix.

## New discovery: `market_competitiveness`, plus two cheap high-value additions

Investigating the same discrepancy surfaced `clob.polymarket.com/rewards/markets/<condition_id>`
— undocumented anywhere found, returns `market_competitiveness` (scalar, formula unknown) and
its own `rewards_config`. Added as an hourly collector + a "bonus, uncalibrated" comparison table
in the daily email, tracked in parallel against our own denominator-bound model, not substituted
for it.

Also added, prompted directly by finding the collector bug:
- `report.pool_drift` — flags any market whose live rate has moved >30% from the frozen
  watchlist snapshot (would have caught the 500->200 move immediately, bug or not).
- `report.denom_ratio_distribution` — full-day distribution (pooled across every sample × token),
  not just the last sample, for a statistically meaningful number at the day-7/14/21 checkpoints.
- `store.append_metrics_history` — one row/day (coverage, denom ratio stats, $ estimate,
  missing/drift counts) so the checkpoints read a real time series, not a re-derivation from
  emails.

None of this touches the frozen watchlist filter or the abandon/proceed gates. Two dated
addenda recorded in `laminar_20260814_preregistration.md`.

## Audit pass (user asked for a bug/gap check on the day's work)

**One gap, two real bugs — all found by auditing rather than by anything failing loudly:**

1. **`competitiveness` cron was never installed.** I'd run it manually once and moved on, so
   it was collecting nothing on a schedule. Now at `:28` hourly. (Lesson: "ran it once
   successfully" is not "it is running".)
2. **`categorise` mis-bucketed two market types** — a senate *resignation* as `election` (the
   "senate" keyword) and both *chess championship* markets as `election` (greedy "win-the-20").
   Caught by writing the test against real watchlist slugs rather than invented ones. Rule
   order fixed; verified across all 30 markets, `other` bucket empty.
3. **`detect_shocks` double-counted every token-level shock** — a binary market's Yes and No
   books are the same orders from opposite sides, so one event logged twice, inflating exactly
   the per-category counts the ledger exists to produce. Now one row per (market, kind); the
   ledger built under the bad logic was deleted and rebuilt, not migrated.

**Checked and clean:** no other frozen-watchlist-as-live reads (2 grep hits, both false
positives); books at 100% per-minute coverage, 58/60 tokens (2 missing = known
cruz-perez-cuellar); all alert state files at 0 consecutive failures; ~165 MB projected for
21 days vs 18 GB free; trades stream 11 min fresh.

**Measured, accepted, noted:** `denom_ratio_distribution` takes ~91 s against a full day
(extrapolated from 390 samples). Fine for a daily job; revisit if the watchlist grows.

**Empirically tested the taker-side assumption** behind `fill_touch`: 68.2% of 908 real trades
sit on the side the reading implies (vs ~50% if it were noise). Supports it without proving it
— and the comparison itself uses a stale midpoint, so some of the 32% is expected measurement
noise rather than a wrong assumption.

## Macro calendar + shock ledger (user request)

**`laminar/calendar.py`** — FOMC from the Fed's own calendar; CPI from a mirror of the BLS
schedule (bls.gov returns 403 to scripts), flagged as good-but-secondhand. Remaining 2026:
CPI **Sep 11, Oct 14, Nov 10, Dec 10**; FOMC **Sep 16** (with SEP/dot plot), **Oct 28**,
**Dec 9** (with SEP). The Fed markets are the only watchlist entries with a scheduled
catalyst, so this is what separates "expected news response" from "genuine anomaly" in the
daily flags.

**`data/laminar/shocks.parquet`** — cumulative (not per-day) ledger of sudden collapses, three
kinds: `pool_rate` (≥50% reward-pool cut vs frozen), `midpoint` (≥25% price gap in ~30 min),
`book_depth` (≥60% in-band depth loss). Each row: kind, category, market, outcome, before,
after, pct_change, window_minutes, scheduled_event, note. Daily email shows new shocks plus a
cumulative category × kind table. **Thresholds are first guesses, not calibrated — revisit at
the day-7 checkpoint once the base rate is visible.**

Fired on real events within minutes: UK-GDP pool 83 → 4 USDC/day (-95%, fed-macro), James Bond
pool 148 → 69 (-53%, entertainment), James Bond in-band depth 988 → 296 shares (-70%,
entertainment). Three data points prove the mechanism works and nothing else.

27 tests passing (up from 21) on both Mac and droplet.

## Deliberately NOT done

- Nothing committed to git (repo had unrelated pre-existing modifications).
- Deeper trade-history backfill past the 1000-row cap on already-saturated markets.
- No fix for the `rewards_config` vs `/sampling-markets` rate discrepancy's root cause beyond
  "both are now live-fetched and agree" — WHY they briefly appeared to disagree (real platform
  reallocation vs a `rewards_config` representation quirk) was not tracked down; several markets
  showed >90% pool drops same-day (e.g. 153->3, 78->2), large enough to be worth a second look
  once more days of data exist.

## Deploy

```
rsync -az -e "ssh -i ~/.ssh/farseer_ed25519" src/laminar/ root@DROPLET_IP:/opt/farseer/src/laminar/
# droplet venv is /opt/farseer/.venv (NOT /opt/farseer/venv)
```

## User preferences observed

Asks for the rules to be pulled before a plan is fixed — will not accept a design argued from
memory. Brings other models' output as a reference to be checked, not adopted. Chose the smaller
durable pool over the larger expiring one ($1M August crypto TWAP) without prompting, and
deferred the high-reward path to "when September policy is known" — patient with incentive
timing, unwilling to build on a strategy with an expiry date.
