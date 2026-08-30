# Open questions

Ranked roughly by how much a good answer would change the project. Each states
what I currently believe and why, so you can attack the reasoning rather than
reverse-engineer it. Numbers refer to files in this repo.

---

## 1. Is back-of-queue actually the pessimistic bound? — `src/laminar/shadow.py`

Queue position is not recoverable from public data: the book shows size at a
price, never the order in which it arrived. So `replay()` takes a `queue`
parameter and is run twice, at `QUEUE_FRONT` and `QUEUE_BACK`, and the truth is
asserted to lie between.

`size_ahead()` implements back-of-queue as *everything currently resting at my
price counts as ahead of me*. **My doubt:** that is the pessimistic bound at the
instant of the snapshot, but resting size ahead of you can also be *cancelled*,
which promotes you for free. Over a minute-long gap between snapshots a
back-of-queue order might realistically do better than the bound suggests —
which would be fine — but I have not shown that the bound cannot be *violated*
by partial fills interacting with the fixed-print-size assumption.

**What would settle it:** a counterexample where a real fill sequence puts the
true fill outside `[back, front]`, or an argument that none exists.

**If you quote live, you are running the counterexample search.** Report your
realised fills against `replay()` at both queue settings as a `queue_bracket`
observation — [`FEEDBACK_PROTOCOL.md`](FEEDBACK_PROTOCOL.md).

## 2. Self-impact: the correction that makes you worse — not yet written

Scoring uses `S(v,s) = ((v-s)/v)^2 * b` where `s` is distance from the adjusted
midpoint. Posting your own two-sided quote *moves* that midpoint. The
counter-intuitive consequence is that inserting your own order generally
**lowers your own score**, because it drags the midpoint toward you and
increases your measured distance from it.

Nothing in the repo models this yet, so every yield figure currently published
is optimistic by an unmeasured amount. **The question is whether the correction
is a fixed point** — your quote moves the midpoint, which changes the optimal
quote, which moves the midpoint again — and if so, whether it converges.

**Difficulty: high. This is the one I would most like help with.**

## 3. The 60-second blind spot — measured, not yet handled

Collection samples the book once per minute. Measured against the trade tape:
**~23% of prints land between snapshots, and 77% of those fall inside the last
recorded best bid/ask.** I verified this is not an artefact of the midpoint-band
filter by re-measuring against full-depth capture.

Consequently every simulated fill rate in this repo is a **lower bound**, not an
estimate. That is honest but weak.

**The question:** is there a defensible way to bound the missed fills from
above — using trade-tape sequencing between snapshots — or is a lower bound
genuinely the best available from minute data? A wrong answer here inflates
yields, so I would rather stay conservative than adopt a clever estimator I
cannot defend.

**A live run prices the blind spot:** the gap between your realised fills and
the minute-cadence lower bound *is* the cost, measured rather than argued.

## 4. Bracketing the reward denominator — `src/laminar/score.py`, `bounds_sweep.py`

Your share of a reward epoch is `mine / (mine + D)`. `D` — everyone else's
qualifying size — is never published. Rather than estimate it, the code brackets
it from the published per-side quantities:

```
D_max = min(Q1, Q2) + |Q1 - Q2| / c
D_min = (Q1 + Q2) / (c + 1)          # c = 3.0
```

Measured on real data the ratio `D_max / D_min` has median ~1.75x.

**I had a hypothesis and measurement killed it.** I expected the bracket to
narrow at size, since `share = mine/(mine+D)` tends to 1 as `mine` grows and the
two corners should converge. Measured: hi/lo = 1.65 at 3,000 shares versus 1.59
at 200 — essentially flat, and mildly *worse* at size. I do not have a
satisfying explanation for why the convergence argument fails.

**The question:** is the derivation of the bounds tight? Is there a sharper pair
recoverable from the same public quantities? And why doesn't the bracket narrow?

**A live run does not narrow this bracket — it replaces it.** Being paid makes
`D` observable: `D = mine_qmin x (pool - reward_paid) / reward_paid`, three
numbers off your own account and no market data. One epoch in one market is
enough. Mind the asymmetry, though: an under-distributed pool biases `D` upward,
so a measurement landing *below* the bracket falsifies it outright while one
landing *above* is weak evidence. See
[`FEEDBACK_PROTOCOL.md`](FEEDBACK_PROTOCOL.md) §2.

## 5. V2 — validating the queue assumption against book deltas

Question 1 is currently answered by assertion. The intended empirical check:
take the depth change at a price level across a trade, and see whether it is
consistent with front- or back-of-queue behaviour for a hypothetical order.

**Not started.** This is the most tractable unclaimed item here and does not
require agreeing with any of my other choices to be useful. It is answerable
entirely offline; a live quoter's own fills would additionally supply the ground
truth the offline check has to do without.

## 6. Non-uniform allocation under concavity

Reward share is concave in your own quote size. It follows that spreading a
fixed capital budget across many markets beats concentrating it, and that the
optimum is *not* an equal split. The solver for this does not exist yet.

Note the practical constraint that makes it matter: the observed reward pool
across the tracked market set is only about **$3,171/day**, so a large book
cannot be deployed uniformly without the marginal share collapsing.

---

## Reporting from a live run

Questions 1, 3, 4 and 5 are all answerable — 4 decisively — by somebody actually
quoting. [`FEEDBACK_PROTOCOL.md`](FEEDBACK_PROTOCOL.md) defines what to send:
**conclusions you computed locally, never venue market data.** The schema at
`schema/live_report.v1.json` rejects anything else, and CI recomputes a report's
conclusions from its own inputs before it can merge.

**Questions 2 and 6 are deliberately not reportable yet.** Neither layer is
written, so there is no prediction for a live result to contradict, and a
measurement with nothing to falsify is not worth the format. They get a schema
kind when they get a model. Until then use `kind: "other"` and prose.

Doing any of this live is your own decision and your own risk —
[`../DISCLAIMER.md`](../DISCLAIMER.md).
