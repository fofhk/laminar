# Contributing

The reason this repo is public is that the parts I am least sure about are
methodological, and methodology is not something you can unit-test your way out
of. Disagreement is the contribution I want most.

## Setup

```bash
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest tests -q      # 75 tests, ~3s
```

CI runs exactly that on 3.12 and 3.13 for every PR.

Also install the pre-push guard once:

```bash
ops/leak_scan.sh --install-hook
```

It refuses to push if anything address-shaped — an IPv4 literal, an e-mail
address — reached a tracked file. CI runs the same script, so a push that would
go red locally goes red remotely too. It exists because this repository was
published by rewriting history, and a keyword-based pass missed a leak twice:
keyword passes only find the values you already remember.

## About the data

**No market data ships with this repo, and none will.** Polymarket's Terms of
Use grant a personal, non-sublicensable, non-transferable licence to the data
(§ *Intellectual Property*) and separately restrict redistribution of it. I hold
no right to pass it on to you, so publishing a sample would be handing you
something that is not mine to give.

The practical consequence, stated plainly: **you can run the code, but you
cannot reproduce my numbers from this repository alone.** What you can do is

* read the derivations and the recorded results in `worklogs/` and tell me where
  the reasoning is wrong — most of what I want reviewed lives there, not in the
  code;
* run `src/laminar/collect.py` against the public API to gather your own window,
  under whatever terms apply to you;
* attack the code paths with fixtures — `tests/test_laminar_shadow.py` shows the
  pattern, and every test there is written to encode *why* a behaviour matters,
  not merely that it happens.

If you have gathered comparable data independently and get a different answer
from mine, that is the single most valuable thing you could report.

And if you go further and quote **live**, see
[`docs/FEEDBACK_PROTOCOL.md`](docs/FEEDBACK_PROTOCOL.md). It defines a narrow
report format — outcomes you computed locally, never venue market data — with a
schema and a CI check. A single live reward payment measures the reward
denominator exactly, which no amount of public data does. Trading with this is
entirely your own decision and your own risk; [`DISCLAIMER.md`](DISCLAIMER.md)
says so properly.

## What I am actually asking for

See [`docs/OPEN_QUESTIONS.md`](docs/OPEN_QUESTIONS.md). The two I would most
like a second opinion on are the queue-position bracket and the self-impact
correction; both are described there with the reasoning I used, so you can
attack the reasoning rather than guess at it.

## House rules

These are the rules the existing code follows. They are unusual enough to be
worth stating, and a PR that breaks one will get pushback:

1. **Bracket, don't estimate.** Where a quantity is not recoverable from public
   data — the reward denominator, queue position — the code returns an interval
   and carries it through. Adding a point estimate "for convenience" defeats the
   purpose of the whole exercise.
2. **Count what you cannot resolve.** When the simulator meets a case it cannot
   decide, it increments a counter and declines. It never picks a default
   silently. `replay()` returns those counters; keep them.
3. **A test must encode intent.** A test that cannot fail when the business
   logic changes is not a test. Say in the name or a comment *why* the
   behaviour matters.
4. **Never re-freeze the watchlist mid-window.** `config/laminar_watchlist.json`
   was frozen before the observation window opened. Changing it mid-window is a
   pre-registration change, and `ops/deploy.sh` refuses to deploy over it.
5. **Results are gross unless the code says otherwise.** Yields in
   `bounds_sweep.py` and `gate_check.py` exclude adverse selection, pool decay
   and self-impact. Don't quote them as net.

## Style

Match the file you are editing. Comments in this codebase explain *why*,
especially where something is deliberately counter-intuitive — those comments
are load-bearing, so please keep them accurate rather than tidy.
