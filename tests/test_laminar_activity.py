"""Intent tests for the /activity pull, added 2026-08-22.

What these protect, in order of how badly a silent failure would hurt:

1. The venue's offset ceiling must not be mistaken for the end of an address's
   history. `/activity` 400s past offset 5000, so a heavy address is only ever
   partially knowable. If a capped pull were recorded as complete, every PnL
   and win-rate figure derived from it would be computed on a truncated tail
   and would look perfectly well-formed while being wrong — the worst kind of
   failure, because nothing downstream can detect it.
2. A capped pull must not crash the job either. Before this change the 400
   propagated as RuntimeError, which for a daily cron over hundreds of wallets
   means one heavy address aborts the whole run and the other wallets' history
   — the perishable thing we are racing to capture — is silently never pulled.
3. A GENUINE outage must still fail loudly. The exemption is narrow on purpose:
   only a request at the known ceiling is allowed to end a wallet quietly. A
   500 on page 1 is an outage and must raise, or the job would "succeed" every
   day while storing nothing.
"""

import pytest

from laminar import clob, collect


def _pages(monkeypatch, behaviour):
    """Install a fake /activity. `behaviour(offset)` returns rows or raises."""
    monkeypatch.setattr(clob, "activity",
                        lambda wallet, limit=500, offset=0: behaviour(offset))
    monkeypatch.setattr(collect.time, "sleep", lambda _s: None)


def _row(i):
    return {"timestamp": 1_700_000_000 + i, "transactionHash": f"0x{i:064x}",
            "conditionId": "0xcond", "asset": "tok", "outcome": "Yes",
            "outcomeIndex": 0, "side": "BUY", "price": 0.5, "size": 2.0,
            "usdcSize": 1.0, "slug": "s", "title": "t"}


def test_venue_ceiling_is_reported_not_treated_as_the_end(monkeypatch, capsys):
    """A wallet whose history exceeds the ceiling must be named in the output.

    Full pages all the way down means the API never signals an end — the only
    signal that this address is truncated is the ceiling itself.
    """
    def behaviour(offset):
        if offset > clob.ACTIVITY_MAX_OFFSET:
            raise AssertionError("asked past the ceiling")
        return [_row(i) for i in range(500)]

    _pages(monkeypatch, behaviour)
    stored = []
    monkeypatch.setattr(collect.store, "append_activity", lambda rows: stored.extend(rows) or len(rows))

    collect.sample_activity(["0xdeadbeef"], max_pages=40)
    out = capsys.readouterr().out
    assert "UNREACHABLE" in out, "a truncated history must say so"
    assert "0xdeadbeef" in out, "the affected wallet must be named"


def test_the_400_at_the_ceiling_does_not_abort_the_other_wallets(monkeypatch, capsys):
    """One heavy address must not cost us every other address's history."""
    def behaviour(offset):
        if offset >= clob.ACTIVITY_MAX_OFFSET:
            raise RuntimeError("400 Client Error: Bad Request")
        return [_row(i) for i in range(500)]

    _pages(monkeypatch, behaviour)
    seen = []
    monkeypatch.setattr(collect.store, "append_activity",
                        lambda rows: seen.append(len(rows)) or len(rows))

    n = collect.sample_activity(["0xheavy", "0xalsoheavy"], max_pages=40)
    out = capsys.readouterr().out
    assert "2 wallet(s)" in out, "both wallets must be reported as capped"
    assert sum(seen) == n > 0, "rows fetched before the ceiling must still be stored"


def test_a_long_run_flushes_instead_of_buffering(monkeypatch, capsys):
    """Progress must reach disk during the run, not only at the end.

    Two reasons, and losing either one is expensive: the droplet has 961MB and
    runs the per-minute `books` collector, so a deep pass that buffered
    everything would OOM and could take `books` down with it — a hole in the
    pre-registered window, caused by a side dataset. And a mid-run failure must
    not discard the wallets already pulled, because this history is perishable.
    """
    _pages(monkeypatch, lambda offset: [_row(0)])  # one short page: wallet done
    calls = []
    monkeypatch.setattr(collect.store, "append_activity",
                        lambda rows: calls.append(len(rows)) or len(rows))

    wallets = [f"0x{i:040x}" for i in range(collect.ACTIVITY_FLUSH_WALLETS * 2)]
    total = collect.sample_activity(wallets, max_pages=4)

    assert len(calls) > 1, "a run this long must flush more than once"
    assert total == len(wallets), "every wallet's row must be counted exactly once"
    assert max(calls) <= collect.ACTIVITY_FLUSH_WALLETS, (
        "no flush may hold more than one interval's worth of wallets")


def test_stopping_on_a_full_page_is_reported_as_incomplete(monkeypatch, capsys):
    """Ending because OUR page cap ran out is not the same as reaching the end.

    This is the hole the first deep pass fell into: with --pages 11 the loop
    never asks past the venue ceiling and never sees a 400, so it exited
    normally on a full page and reported nothing — 188 of 371 wallets were
    truncated and the run looked clean. Only a SHORT page proves completeness.
    """
    _pages(monkeypatch, lambda offset: [_row(i) for i in range(500)])
    monkeypatch.setattr(collect.store, "append_activity", lambda rows: len(rows))

    # 4 pages tops out at offset 1500 — nowhere near the ceiling, so the only
    # thing that stopped us is our own cap.
    collect.sample_activity(["0xbusy"], max_pages=4)
    out = capsys.readouterr().out
    assert "history NOT exhausted" in out, "our own cap must be reported too"
    assert "UNREACHABLE" not in out, (
        "our page cap must NOT be reported as the venue's ceiling — one is "
        "fixable by re-running, the other is permanent data loss")


def test_a_real_outage_still_raises(monkeypatch):
    """Narrow exemption: only the ceiling is quiet. Page 1 failing is an outage.

    Without this the daily job would report success while storing nothing, and
    the perishable history it exists to capture would roll away unnoticed.
    """
    def behaviour(offset):
        raise RuntimeError("500 Server Error")

    _pages(monkeypatch, behaviour)
    monkeypatch.setattr(collect.store, "append_activity", lambda rows: len(rows))

    with pytest.raises(RuntimeError, match="offset=0"):
        collect.sample_activity(["0xwallet"], max_pages=4)
