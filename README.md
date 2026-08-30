# Laminar

[![ci](https://github.com/fofhk/laminar/actions/workflows/ci.yml/badge.svg)](https://github.com/fofhk/laminar/actions/workflows/ci.yml)

Research into whether Polymarket's **liquidity-reward** programme can be farmed
profitably by a two-sided market maker — done as a pre-registered study rather
than as a backtest.

**No order has ever been placed.** Stage 1 is read-only observation of public
market data; Stage 2 replays that data against hypothetical quotes offline.

## Why this repo might interest you

Most "market making bot" repos start from a strategy and then look for evidence.
This one starts from a **pre-registration** (`docs/laminar_20260814_preregistration.md`,
frozen 2026-08-14): the market set, the scoring model, the pass/fail gates and
the observation window were all written down *before* the data existed, so the
result cannot be quietly fitted to what showed up.

Three things in here are reusable independently of Polymarket:

1. **Reward scoring under a non-reconstructible denominator.** Your share of a
   reward epoch is `mine / (mine + D)`, and `D` — everyone else's qualifying
   size — is never published. `src/laminar/score.py` and `bounds_sweep.py` do
   not estimate `D`; they **bracket** it (`D_min = (Q1+Q2)/(c+1)`,
   `D_max = min(Q1,Q2) + |Q1−Q2|/c`) and carry the bracket through to a yield
   *interval*. A point estimate here would be fiction.
2. **An offline fill simulator that refuses to guess.**
   `src/laminar/shadow.py` replays the public trade tape against hypothetical
   orders. Queue position is unknowable from public data, so it reports a
   **front-of-queue / back-of-queue bracket** rather than a number, and it
   *counts* the cases it cannot resolve instead of silently defaulting them.
   It was validated by degenerating to the real book: replaying the actual
   resting orders reproduced 5,148 real prints exactly.
3. **What a one-minute snapshot cadence actually costs you.** ~23% of prints
   land between snapshots, and 77% of those fall inside the last recorded
   best bid/ask — so simulated fill rates from minute data are a *lower bound*,
   not an estimate. Measured, not assumed; see the matching-core worklog.

## Layout

```
docs/       pre-registration (governing), Stage-2 study design, provisioning notes
src/        the laminar package; src/farseer/config.py is shared path constants
ops/        deploy.sh, backup.sh, cron entrypoints, the daily report
tests/      pytest — run before every deploy
worklogs/   dated record of what was done and, more importantly, what was wrong
```

`LAMINAR_STATE.md` is the live status document: what is running, what is next,
what is still undecided.

The `worklogs/` are written to be read. Several record findings that killed an
earlier plan of mine — a convergence hypothesis that measurement falsified, a
deploy command that was silently a no-op for eleven days, an alerting scheme
that fired every single day and therefore alerted on nothing. They are kept
because the failure is the useful part.

## Running it

```bash
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest tests -q
```

The package is called `farseer` in `pyproject.toml` because Laminar lives inside
a larger private trading tree; only `src/laminar/` and `src/farseer/config.py`
are part of this repo.

Collection is cron-driven on a small VPS. Host, key and mail endpoint are not in
the repo — copy `ops/local.env.example` to `ops/local.env` and fill it in.

```bash
ops/deploy.sh --check    # verify the box runs this code, change nothing
ops/deploy.sh            # deploy, then prove it by importing on the box
```

`deploy.sh` never trusts rsync's exit code, and it refuses to overwrite the
frozen watchlist — re-freezing mid-window would be a pre-registration change,
not a deploy.

## Data — and what that means for reproducing this

No market data ships with this repo, and none will. Polymarket's Terms of Use
grant a personal, **non-sublicensable, non-transferable** licence to the data
and separately restrict redistributing it; I hold no right to pass it on.

So, plainly: **you can run the code, but you cannot reproduce my numbers from
this repository alone.** The derivations and the recorded measurements are in
`worklogs/` and `docs/` and are meant to be argued with directly. You can also
point `src/laminar/collect.py` at the public API and gather your own window
under whatever terms apply to you.

## Status and scope

Stage 1 collection is running; the gate decision is due 2026-09-04. Stage 3
(live quoting) is blocked on Polymarket's jurisdiction terms, not on the data,
and nothing in this repo advises anyone on how to get around them.

Nothing here is investment advice, and no result in it has been confirmed
against live execution. This is an observational study, not a trading system —
if you intend to trade with any of it, read [`DISCLAIMER.md`](DISCLAIMER.md)
first. It is short, and the qualifications in it are not boilerplate: the
self-impact layer that would make published yields *worse* is not written yet.

## Contributing

Review is the point. [`docs/OPEN_QUESTIONS.md`](docs/OPEN_QUESTIONS.md) lists
six of them, each with the reasoning I used, so you can attack the reasoning
instead of guessing at it. The two I would most like a second opinion on:

- the **queue-position bracket** in `shadow.py` — back-of-queue is the
  pessimistic bound at the instant of a snapshot, but resting size ahead of you
  can be cancelled. I have not shown the bound cannot be violated.
- the **self-impact** correction: posting your own quote moves the adjusted
  midpoint and therefore *lowers* your own score. That layer is not written, so
  every yield figure here is optimistic by an unmeasured amount.

[`CONTRIBUTING.md`](CONTRIBUTING.md) has the setup and the five house rules —
the first being *bracket, don't estimate*.

## If you run this live

Then you can measure something this repository cannot, and it is the single
thing most worth having. Your share of a reward epoch is `mine / (mine + D)`,
and `D` — everyone else's qualifying size — is never published, which is why
Stage 2 only brackets it. **One reward payment pins it down exactly**, from
three numbers off your own account:

```
D = mine_qmin x (pool_usdc - reward_paid_usdc) / reward_paid_usdc
```

No market data is involved, and that is the point:
[`docs/FEEDBACK_PROTOCOL.md`](docs/FEEDBACK_PROTOCOL.md) defines a report format
that carries **conclusions you computed locally, never the data you computed
them from** — the schema rejects anything else. Submit one as a PR adding a file
to `contrib/reports/`; CI validates it and recomputes its conclusions from its
own inputs.

A report that contradicts a published result here is more welcome than one that
confirms it.

## Licence

Apache License 2.0 — see [`LICENSE`](LICENSE).

Copyright 2026 the Laminar authors.
