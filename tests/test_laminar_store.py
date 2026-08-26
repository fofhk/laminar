"""Intent tests for the wallet column added to the trade tape on 2026-08-19.

What these protect, in order of how badly a silent failure would hurt:

1. Widening TRADE_SCHEMA must not break appends onto files written before the
   new column existed. If it did, the hourly `trades` job would start failing
   the moment it ran against an existing day file — and because the collector
   is a cron job whose output nobody reads hourly, the tape would simply stop
   with no one noticing until a review found a hole.
2. Two wallets on the SAME transaction must both survive the dedupe. The tape's
   whole purpose now is attributing flow to addresses; a dedupe key that omits
   `wallet` would collapse a fill and its counterparty into one row, silently
   halving exactly the signal these columns were added to measure.
3. Rows written before the column exists must read back as null, not as an
   empty string. Null is what makes "we were not recording yet" distinguishable
   from "the API returned no wallet" — a wallet-conditioned analysis has to be
   able to find the first honest day of data and start there.
"""

import polars as pl

from laminar import store

BASE = {
    "tx_hash": "0xaa",
    "ts": 1787000000000,
    "condition_id": "0xcond",
    "token_id": "tok1",
    "outcome": "Yes",
    "side": "BUY",
    "price": 0.5,
    "size": 10.0,
}


def _pre_wallet_file(tmp_path):
    """A trades file exactly as the collector wrote them before 2026-08-19."""
    store.LAMINAR_DIR = tmp_path
    old_schema = {k: v for k, v in store.TRADE_SCHEMA.items() if k != "wallet"}
    day = store._day(BASE["ts"])
    (tmp_path / "trades").mkdir(parents=True)
    pl.DataFrame([BASE], schema=old_schema).write_parquet(tmp_path / "trades" / f"{day}.parquet")
    return day


def test_append_survives_a_widened_schema(tmp_path):
    day = _pre_wallet_file(tmp_path)

    store.append_trades([{**BASE, "tx_hash": "0xbb", "wallet": "0xW1"}])

    df = store.load_trades(day)
    assert df.height == 2, "the pre-existing row must not be lost by the widening"
    assert "wallet" in df.columns


def test_pre_wallet_rows_read_back_as_null(tmp_path):
    day = _pre_wallet_file(tmp_path)

    store.append_trades([{**BASE, "tx_hash": "0xbb", "wallet": "0xW1"}])

    df = store.load_trades(day)
    old = df.filter(pl.col("tx_hash") == "0xaa")
    assert old["wallet"].null_count() == 1, "not-yet-recorded must be null, not ''"


def test_two_wallets_on_one_tx_are_both_kept(tmp_path):
    store.LAMINAR_DIR = tmp_path

    # Identical in every field the old dedupe key looked at — only `wallet` differs.
    store.append_trades(
        [
            {**BASE, "wallet": "0xW1"},
            {**BASE, "wallet": "0xW2"},
        ]
    )

    df = store.load_trades(store._day(BASE["ts"]))
    assert df.height == 2, "dropping a counterparty row would halve the tape"
    assert set(df["wallet"]) == {"0xW1", "0xW2"}


def test_rerunning_the_same_poll_still_dedupes(tmp_path):
    store.LAMINAR_DIR = tmp_path
    rows = [{**BASE, "wallet": "0xW1"}]

    store.append_trades(rows)
    store.append_trades(rows)

    df = store.load_trades(store._day(BASE["ts"]))
    assert df.height == 1, "adding `wallet` to the key must not disable dedupe"
