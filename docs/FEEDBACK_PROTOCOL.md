# Feedback protocol — reporting a live run back to Laminar

> **The rule, in one line: send conclusions you computed locally, never the data
> you computed them from.**

Laminar is a read-only study. Its central weakness is that Stage 2 can only
*bracket* the quantities that decide whether any of this works, because the
venue does not publish them. Somebody actually quoting live can *measure* them.
This document defines that channel.

If you ran Laminar's methods against a live venue and are willing to say what
happened, this is what to send and how. Reporting is voluntary and creates no
obligation on either side — see [`../DISCLAIMER.md`](../DISCLAIMER.md).

---

## 1. Why the protocol refuses data

The obvious design — "send us your logs" — is wrong three times over.

**It is not permitted.** Order books and trade tapes are the venue's data, and
its terms restrict passing them on. Accepting a pile of it would make this
repository a redistribution point for something neither of us has the right to
redistribute. Reports as defined here carry *your own account outcomes* and the
arithmetic you did on them — not the venue's market data.

**It is not what the project needs.** Nothing in the open questions is blocked
for want of more book snapshots; collection already produces those. What is
missing is exactly one thing no amount of public data yields: **what the
competing field actually scored.** That is three numbers, not a dataset.

**It does not survive contact with review.** A JSON file of twenty numbers can
be checked in CI, argued with in a pull request, and cited in a worklog. A
multi-gigabyte drop cannot be any of those things, so in practice it would sit
unread.

So the schema has `additionalProperties: false` at every level. A book snapshot
has nowhere to go — that is the enforcement mechanism, not a stylistic
preference.

## 2. The measurement that matters most

Your share of a reward epoch is `mine / (mine + D)`, where `D` is everyone
else's total qualifying score. `D` is never published, which is why
`score.denominator_bounds()` returns an interval — measured median width
**~1.75x**. That interval is the weakest load-bearing assumption in the project.

If you were paid, `D` is not a mystery to you. Your realised share is
`reward_paid / pool`, so

```
D = mine_qmin x (pool_usdc - reward_paid_usdc) / reward_paid_usdc
```

`laminar.feedback.implied_denominator()` is that line, and it needs no market
data whatsoever:

| you need | where it comes from |
|---|---|
| `mine_qmin` | **your own** order log, through `laminar.score.qmin()` |
| `reward_paid_usdc` | **your own** reward statement |
| `pool_usdc` | the market's advertised daily rate x days |

Sum `mine_qmin` over exactly the samples the payment covers, or you are dividing
two different things.

**Then compare `D` against the bracket, and mind the asymmetry.** If the venue
does not distribute the full advertised pool — a market nobody quotes hard
enough to earn all of it — then `reward_paid / pool` understates your true share
and the computed `D` comes out **too high**. So:

* **below the bracket** — strong. Under-distribution cannot produce this, so it
  falsifies `D_min` outright, and question 4 is answered.
* **above the bracket** — weak. Could be a wrong bound, could be an
  under-distributed pool. Say in `notes` if you have any read on which.
* **inside** — corroboration. Also useful: it is the first live evidence the
  bracket has ever had.

One epoch, in one market, is enough to be worth sending.

## 3. What each open question needs

| [Open question](OPEN_QUESTIONS.md) | Observation `kind` | What it settles |
|---|---|---|
| **4** — denominator bracket | `denominator_measurement` | Everything above. The flagship. |
| **1** — is back-of-queue really pessimistic | `queue_bracket` | Whether real fills ever land *below* the pessimistic bound. One counterexample kills the assumption. |
| **5** — validating queue against book deltas | `queue_bracket` | Your fills are ground truth the offline check does not have. |
| **3** — the 60-second blind spot | `queue_bracket`, verdict `above` | How far real fills exceed the minute-cadence lower bound — i.e. what the blind spot actually costs. |
| — headline: does it pay at all | `yield_realised` | Realised gross yield against the predicted interval. |
| **2** — self-impact, **6** — allocation | *not yet reportable* | There is no prediction to test against; the layers are not written. See below. |

**Questions 2 and 6 are deliberately out of scope for v1.** Laminar has no
number to compare a report against, so a "measurement" of them would be a
number with nothing to falsify. When those layers exist the schema gains a kind.
Until then, if you measured something there, use `kind: "other"` and prose.

`kind: "other"` is a first-class escape hatch generally. A wrong field is worse
than free text.

## 4. What never goes in a report

Not order books, trade tapes, or quote-by-quote logs — no venue market data in
any form. Not wallet addresses, API keys, or account identifiers. Not
counterparty identities. Not e-mail addresses.

Three guards enforce this, and they run on every pull request:

* the **schema** rejects unlisted fields outright;
* `ops/validate_reports.py` rejects any 40-hex-character wallet-shaped literal,
  including in the free-text fields the schema cannot constrain;
* `ops/leak_scan.sh` — the repository's standing guard — rejects IPv4 literals
  and e-mail addresses in any tracked file.

Naming the market via `market` is optional. It is a public `condition_id`, but
if it would tell people more about your position than you want, omit it; nothing
downstream requires it.

## 5. How to submit

1. Read [`../schema/example_live_report.json`](../schema/example_live_report.json)
   — a worked example whose arithmetic CI checks, so it cannot drift.
2. Write `contrib/reports/YYYY-MM-DD-<handle>.json` against
   [`../schema/live_report.v1.json`](../schema/live_report.v1.json).
3. Validate locally:
   ```bash
   ops/validate_reports.py
   ```
4. Open a pull request. CI runs the same command, so a green check means the
   report is well-formed **and** that its stated conclusions follow from its own
   stated inputs.

That second half is the reason a report carries both its inputs and its
conclusion: the conclusion gets recomputed rather than believed. You cannot
submit a verdict your numbers do not support — not because anyone distrusts you,
but because this project's whole method is refusing to accept unverifiable
quantities, and a contributor's claim is no different from the author's in that
respect.

If you would rather not publish under your own name, use any handle, or omit
`reporter` entirely. An anonymous report is still worth having.

## 6. What happens to a report

It is data on the repository's own claims, so it is treated like any other
measurement here: cited in a worklog with its provenance and its caveats, and
credited to you unless you ask otherwise. **A report that contradicts a
published result is more welcome than one that confirms it** — every worklog in
this project that mattered recorded a hypothesis that measurement killed.

Reports are self-reported and unverifiable by definition. Nothing in this
repository will present one as though it were independently confirmed.
