# Stage 1 — gate verdict, 2026-09-04 window

**Verdict: PROCEED to Stage 2.** All three pre-registered gates pass on the full
22-day window, none of them marginally, so the 42-day extension clause does not
trigger.

**And that verdict is much weaker evidence than it looks.** Section 3 argues
that none of the three gates was structurally capable of failing, and section 4
shows the number that actually decides anything moving the wrong way. Both are
here because a pre-registration is worth nothing if you only report the half
that went well.

Scored on 2026-09-06, two days late — the window closed on schedule but nothing
runs unattended except the collector.

---

## 1. What was measured

| | |
|---|---|
| Window | 2026-08-14 → 2026-09-04 inclusive, 22 days (day 1 is a part-day; the watchlist froze 05:51 UTC) |
| Snapshot rows | 1,852,684 (token × minute cross-sections) |
| Unique minute stamps | 31,186 of a theoretical 31,680 — **98.44%** |
| Tokens | 60 (30 markets × 2 outcomes), no gaps |
| Rows with a live pool | 100.0% |

Every figure below recomputes from **every stored snapshot**, not from the
end-of-day snapshot the daily report uses.

**Abandon trigger (3 consecutive days of >5% sample loss): never fired.**
Excluding the part-day start, the worst day lost 2.92% and 21 of 22 days lost
under 1%.

Three snapshots on 2026-08-30 are excluded: they were written by a manual
invocation of the collector during the frozen window — a write path mistaken for
a read path. That is recorded rather than deleted, because deleting from a
frozen dataset is itself an unrecorded change. The full-window bounds sweep in
section 4 uses the module's own 5-minute stride without that exclusion; the
affected share there is 3 of 360,914 samples, which is stated rather than
quietly ignored.

---

## 2. The gates

Definitions are frozen in [`laminar_20260814_preregistration.md`](laminar_20260814_preregistration.md).

### Gate ① — denominator bracket width. Abandon if the median market exceeds 3x.

| p5 | p25 | **p50** | p75 | p95 |
|---|---|---|---|---|
| 1.382 | 1.614 | **1.787** | 1.903 | 1.980 |

- Samples exceeding 3x: **0.0000%** of 1,848,153
- Worst single token, by its own median: **1.910**
- Samples outside the two-sided band, where the bracket collapses to a point: 2.53%

### Gate ② — annualised yield on a 200-share two-sided quote. Abandon if the *upper* bound is below 10%.

| | p5 | p25 | **p50** | p75 | p95 |
|---|---|---|---|---|---|
| upper | 35.6 | 104.5 | **323.1** | 608.1 | 2120.7 |
| lower | 21.1 | 63.8 | **199.1** | 376.3 | 1284.6 |

- Tokens whose median upper bound is below 10%: **0 of 60**. Lower bound: **0 of 60**.
- Snapshot-level: upper below 10% in 0.00% of samples, lower in 0.01%.

### Gate ③ — is the reward band reachable without crossing the spread?

| cents | p5 | p25 | **p50** | p75 | p95 |
|---|---|---|---|---|---|
| half-spread | 0.05 | 0.40 | **0.50** | 0.50 | 2.00 |
| `max_spread` | 3.5 | 4.5 | **4.5** | 4.5 | 5.5 |

- P(touch already inside the band, i.e. half-spread ≤ `max_spread`): **100.00%**,
  and 100.00% for every token individually
- P(midpoint inside the two-sided band): 97.47%

This statistic was due on 2026-08-28 and is nine days late.

---

## 3. Why passing three gates means so little

Each gate was written on 2026-08-14 to catch a specific collapse. None of the
collapses happened — that is real information. But looking at the measured
distributions, **none of the three could have fired even if the opportunity were
worthless**:

- **Gate ①** tests a ratio whose ceiling is structural. The pre-registration
  derives it itself: on a balanced book the two bounds are *exactly* 2x apart,
  regardless of depth or level count. So a 3x threshold is not a test of book
  depth — it is a test of how lopsided real books run, and 1.787 says "mildly".
  The bound could not plausibly have reached 3x. A gate that tests
  identifiability should constrain how much the bracket's width changes the
  optimal size, not the width itself.
- **Gate ②** is measured at 200 shares/side across 30 markets — roughly $6,000
  of capital. At that scale `mine << D`, share is near-linear in size, and the
  percentage yield is arithmetically large. The gate answers "is there any yield
  at all", which was never the binding question. Capacity is.
- **Gate ③** compares a 0.50c median half-spread against a 4.5c band. The band
  is wider than the spread by nearly an order of magnitude; resting at the
  existing touch is *already* a reward-eligible quote. Nothing had to be
  improved and nothing had to be crossed.

The gates are kept as written — rewriting them after seeing the data is exactly
what a pre-registration exists to prevent. But their passing is recorded here as
*the absence of three specific catastrophes*, not as evidence for proceeding to
capital.

---

## 4. The number that does decide something

`bounds_sweep` over the full window: 360,914 token-snapshots, 5-minute stride.
Every yield is **gross** — the reward stream only.

| shares/side | ≈ capital, 30 markets | median lower | median upper | upper/lower | **p25 lower** |
|---|---|---|---|---|---|
| 200 | $6k | 195.8% | 320.7% | 1.64 | 60.6% |
| 1,000 | $30k | 138.0% | 216.4% | 1.57 | 54.0% |
| **3,000** | **$90k** | **115.3%** | **188.5%** | **1.63** | **35.2%** |
| 10,000 | $300k | 94.1% | 138.3% | 1.47 | 25.5% |

**Against the same measurement on the partial window (2026-08-23), every figure
moved unfavourably.** At ~90–100k the pessimistic corner fell from 136% to
115.3% (−21pp) and the optimistic corner from 225% to 188.5% (−37pp).

The project's own deployment floor is **110% net at the pessimistic corner at
100k**. The measured 115.3% is **gross**. Three cost layers are unwritten —
self-impact, adverse selection, inventory — and all three push one way, down.
The margin is about 5pp and the unmodelled costs are almost certainly larger
than 5pp.

> **Stage 1 clears its gates and does not clear its economics.** Those are two
> different statements and only the first was pre-registered.

Two further readings:

- **The convergence hypothesis stays falsified, now on the full window.**
  upper/lower is 1.63 at 3,000 shares against 1.64 at 200 — flat. It only starts
  narrowing near 10,000 shares (~$300k). At the size anyone would actually
  deploy, the bracket does not narrow on its own. Open question 4 still has no
  satisfying explanation for this.
- **Cross-sectional dispersion is larger than the headline.** At ~$90k the p25
  pessimistic corner is 35.2% against a 115.3% median — a 3x spread between
  markets. Spreading capital evenly over 30 markets puts a quarter of it in the
  35% corner, which makes the non-uniform allocation solver (open question 6) a
  precondition for the median being achievable, not an optimisation.

---

## 5. Capacity

Pools are per **market**; the two outcome tokens of a market share one pool, so
summing over tokens double-counts.

| addressable pool, whole watchlist ($/day) | p05 | p25 | **p50** | p75 | p95 |
|---|---|---|---|---|---|
| | 3,043 | 3,167 | **3,376** | 3,726 | 4,029 |

- Day 1 to day 22: **−13%**
- Concentration: the top 3 markets hold 24.4% of it, the top 10 hold 58.8%
- No market's pool went to zero; uptime is ~100% across all 30. The pools are
  shrinking, not disappearing.

Consistency check on section 4, since a yield figure and a pool figure have to
agree: $90k at 115.3% is $284/day, or **8.4%** of the whole watchlist's pool;
$300k at 94.1% is 23%. Both are demanding but not impossible, so the two
measurements are at least the same order of magnitude.

The daily report's count of markets whose reward parameters have drifted from
the frozen snapshot rose from 7–11 early in the window to 15–16 at the end.
Together with the −13%, the frozen watchlist is not merely too small for size —
it is going stale.

---

## 6. What is not in this document, and why

No book snapshots, no trade tape, no per-market time series, no per-market pool
figures. Polymarket's terms grant a personal, non-sublicensable,
non-transferable licence to the data and separately restrict redistribution, so
what is published here is the same thing this project asks of live contributors
in [`FEEDBACK_PROTOCOL.md`](FEEDBACK_PROTOCOL.md): **conclusions, never the data
they were computed from.** The frozen watchlist in `config/` is published because
it is part of the pre-registration — it is the *scope*, fixed before collection,
not a measurement.

You therefore cannot reproduce these numbers from this repository. You can read
every derivation, disagree with any of them, and point `collect.py` at the
public API to gather your own window under whatever terms apply to you.

## 7. Reproducing the computation, if you have your own window

- **Gates ①②③ and coverage.** Read the stored books hour by hour; score each
  level as `((v − s) / v)² · size` where `s` is |price − midpoint| in cents and
  `v` is `max_spread`, keeping only levels with `size ≥ min_size` and `s ≤ v`;
  group to `(ts, token)` for `Q1`/`Q2`; apply `score.denominator_bounds`
  vectorised. Full window: ~40 seconds.
- **The bracket sweep.** `python -m laminar.bounds_sweep <YYYY-MM-DD>` per day,
  then merge the `lo`/`hi` lists before taking medians — a median of daily
  medians is not the same statistic. Full window: ~7.5 minutes, and it is slow
  because it iterates groups in Python.
- **Pools.** Deduplicate to one pool per `market_slug` before summing, or the
  total comes out exactly 2x too high.
