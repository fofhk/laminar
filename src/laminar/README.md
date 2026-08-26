# Laminar

Polymarket liquidity-reward market making. **Stage 1: read-only measurement.** No account, no
wallet, no orders — this package cannot place a trade, by construction.

Sibling to `farseer/` (the council) and `crescendo/` (the directional crypto app), sharing the
repo and its conventions but not its venue, its instrument, or its objective function. Farseer's
own scope statement excludes MM; that exclusion is about the `farseer` package, not the repo.

Governing document: **`../../laminar_20260814_preregistration.md`** — frozen scope, the exact
scoring model under test, the known unknowns, and the abandon gates. Read it before changing
anything here; a change to what is measured is a new pre-registration, not a commit.

## What Stage 1 answers

> Can a hypothetical maker order's reward payout be reconstructed from the public order book
> accurately enough to size a strategy?

The scoring inputs are all public — `max_spread`, `min_size`, the book, the midpoint — so our
own numerator is exact. The **denominator is not**: `/book` aggregates size per price level
while `Qmin` is per-maker and non-linear, so the field can only be bounded. `score.py` returns
that interval rather than a false point estimate; how wide it runs in practice is the main
Stage-1 finding.

## Layout

```
score.py     the reward arithmetic, pure and testable (no I/O)
clob.py      read-only CLOB client — public endpoints only
store.py     parquet, daily-partitioned book samples
collect.py   CLI: watchlist | books | markets
```

Watchlist lives at `config/laminar_watchlist.json`, frozen for the observation window.

## Running

```
python -m laminar.collect watchlist   # once, then frozen
python -m laminar.collect books       # every 60s  (matches reward sampling)
python -m laminar.collect markets     # hourly
pytest tests/test_laminar_score.py
```

## Jurisdiction — read before Stage 3

The Mac's ISP DNS-blocks `polymarket.com` (ACMA). The SGP droplet reaches the API fine, but
**both AU and SG are close-only jurisdictions** for Polymarket. Stage 1 reads public data and
raises no issue; **Stage 3 is blocked** until the operating entity and jurisdiction are settled,
and will need its own droplet in a permitted region — not the Farseer box, which is documented
as single-purpose and will hold a funded Hyperliquid key of its own.

## Not the starting point

`Polymarket/poly-market-maker` (last commit 2024-03-11, release v0.0.3 from 2023-02, Python
3.10) quotes around the raw midpoint with no notion of `max_spread`, `min_size`, or `Qmin`. It
predates the current reward regime by two years and optimises the wrong objective. Useful only
as a reference for CLOB auth/signing at Stage 3.
