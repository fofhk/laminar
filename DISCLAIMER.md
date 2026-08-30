# Disclaimer

## What this project is

Laminar is an **observational study**. It watches Polymarket's public market
data and asks, offline, whether the liquidity-reward programme could be farmed
profitably by a two-sided maker. The question is still open; that is the point
of the study.

**No order has ever been placed by this project.** Stage 1 is read-only
collection. Stage 2 replays collected data against hypothetical quotes. Stage 3
— actually quoting — has not begun and is gated on conditions that have not
been met.

## What this project is not

It is **not a trading system**, not a strategy you can switch on, and not
advice — investment, legal, tax or otherwise. Nobody here is your licensed
adviser.

Every number produced by this repository carries at least the following
qualifications, and they are not boilerplate:

* **Gross, not net.** Yields exclude adverse selection, pool decay and
  self-impact. The self-impact layer is *not written yet*, and it is known to
  push in the unfavourable direction — so published figures are optimistic by an
  amount nobody has measured. See `docs/OPEN_QUESTIONS.md` #2.
* **Intervals, not estimates.** The reward denominator and queue position are
  not recoverable from public data, so results are brackets. A bracket is not a
  forecast, and the bracket itself may be wrong — see #1 and #4.
* **Lower bounds on fills.** Book snapshots are taken once a minute; ~23% of
  prints land between them. Simulated fill rates are floors, not predictions.
* **Never validated against live execution.** Not once. Simulation agreeing
  with itself is not evidence.

## If you trade with it

You are free to — the Apache-2.0 licence grants that, and this repository is
public precisely so that it can be examined and used. But the decision, the
capital and every consequence are yours alone.

The licence already says this in the operative language (see `LICENSE`,
**Section 7 — Disclaimer of Warranty** and **Section 8 — Limitation of
Liability**): the work is provided *as is*, without warranties or conditions of
any kind, and no contributor is liable for any damages arising from its use. In
plain terms, and to be unambiguous about it:

> **The authors accept no responsibility for any trading loss, missed reward,
> fee, tax exposure, account action, or regulatory consequence arising from
> anyone's use of this code, its methods, or its published results.**

Two specific things you must settle for yourself before running anything against
a live venue:

1. **Read the venue's Terms of Use yourself.** Pay attention to the clauses on
   eligibility, on who may access market data (including via API, and including
   derived or aggregated forms), and on redistribution. Whether *you* are
   permitted to collect this data or to trade on that venue is a question about
   *you* — your jurisdiction, and what kind of entity you are. Nothing in this
   repository determines that, and nothing in it describes how to work around a
   restriction. If the terms do not permit it, the answer is don't.
2. **The gates in the pre-registration are this study's gates, not yours.**
   `docs/laminar_20260814_preregistration.md` fixes a market set, a window and
   pass/fail thresholds for *this* study. They encode this author's risk
   tolerance and this author's capital. Do not read a passed gate as a
   recommendation.

## If you do run it live — please send results back

This is the one thing the project cannot do for itself. The reward denominator
`D` is never published, so Stage 2 can only bracket it. **A single live reward
payment pins it down exactly**, because your realised share of the pool is a
direct measurement of the quantity the bracket is guessing at.

If you are willing to report what actually happened, it would improve Laminar
materially, and you would be credited unless you ask otherwise.

**[`docs/FEEDBACK_PROTOCOL.md`](docs/FEEDBACK_PROTOCOL.md) defines exactly what
to send.** Read it before sending anything — it is deliberately narrow. In
short: send *outcomes you computed locally*, never market data. The schema
rejects anything else, and it is designed that way to keep both of us on the
right side of the venue's redistribution terms.

Reporting is voluntary, creates no obligation on either side, and confers no
warranty or support from the authors in return.
