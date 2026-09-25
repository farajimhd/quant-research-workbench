from __future__ import annotations

import json

import pytest

from src.trading_runtime import arte_backtest_snapshot_anchor as anchor
from src.trading_runtime.arte_journal_writer import CommittedPrefix
from tests.test_arte_admission_fence import BATCH, RUN, captured


HASH = "a" * 64
PREFIX = CommittedPrefix(RUN, 1, BATCH, "terminal", "completed", (BATCH,))


class Client:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.inserts = 0

    def execute(self, sql: str) -> str:
        if sql.startswith("INSERT INTO arte.trading_backtest_snapshot_anchor_v1"):
            self.inserts += 1
            self.rows.extend(json.loads(line) for line in sql.split("\n", 1)[1].splitlines())
            return ""
        assert "FROM arte.trading_backtest_snapshot_anchor_v1" in sql
        return "\n".join(json.dumps(row) for row in self.rows)


def _install(monkeypatch):
    monkeypatch.setattr(anchor, "load_committed_prefix", lambda _client, _run: PREFIX)
    monkeypatch.setattr(anchor, "load_typed_run_context", lambda _client, _run: {
        "mode": "backtest", "account_ids": ("DU1",),
    })
    monkeypatch.setattr(anchor, "publish_prepared_portfolio_snapshot",
                        lambda _client, _prepared: HASH)
    monkeypatch.setattr(anchor, "load_portfolio_snapshot",
                        lambda _client, **_kwargs: {"state_hash": HASH})


def test_terminal_anchor_binds_one_snapshot_to_exact_prefix(monkeypatch) -> None:
    _install(monkeypatch)
    client = Client()
    with pytest.raises(RuntimeError, match="lacks one terminal"):
        anchor.load_terminal_backtest_snapshot(client, PREFIX, account_id="DU1")
    assert anchor.publish_terminal_backtest_snapshot(client, PREFIX, captured()) == HASH
    assert client.inserts == 1
    assert anchor.publish_terminal_backtest_snapshot(client, PREFIX, captured()) == HASH
    assert client.inserts == 1
    assert anchor.load_terminal_backtest_snapshot(client, PREFIX, account_id="DU1") == {
        "state_hash": HASH}
    with pytest.raises(RuntimeError, match="differs from terminal events"):
        anchor.load_terminal_backtest_snapshot(
            client, CommittedPrefix(RUN, 2, BATCH, "terminal", "completed", (BATCH,)),
            account_id="DU1")
    client.rows[0]["snapshot_hash"] = "b" * 64
    with pytest.raises(RuntimeError, match="differs from its hash"):
        anchor.load_terminal_backtest_snapshot(client, PREFIX, account_id="DU1")


def test_terminal_anchor_rejects_changed_prefix_before_snapshot_write(monkeypatch) -> None:
    _install(monkeypatch)
    client = Client()
    monkeypatch.setattr(anchor, "load_committed_prefix", lambda _client, _run: None)
    with pytest.raises(RuntimeError, match="current terminal prefix"):
        anchor.publish_terminal_backtest_snapshot(client, PREFIX, captured())
    assert client.inserts == 0


def test_strict_terminal_anchor_receipt_is_bounded_and_retry_safe(monkeypatch) -> None:
    from test_arte_typed_insert_dispatch import (
        Client as DispatchClient, Keeper, SQL, ZERO_BATCH, attest_direct,
    )
    from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch

    _install(monkeypatch)
    authority = TypedInsertDispatch(Keeper(), max_operations=1)
    authority.initialize_new_run(RUN)
    attest_direct(authority, RUN)
    authority.assert_next_batch(run_id=RUN, batch_id=BATCH,
        prior_batch_id=ZERO_BATCH, first_sequence=1, last_sequence=1)
    authority.execute_typed_insert(DispatchClient(authority), run_id=RUN,
        table="trading_event_v1", token="batch-1", sql=SQL,
        batch_id=BATCH, batch_last_sequence=1)
    authority.seal_verified_operation(run_id=RUN, table="trading_event_v1",
        token="batch-1", batch_id=BATCH, batch_last_sequence=1)
    authority.compact_verified_batch(run_id=RUN, batch_id=BATCH,
        prior_batch_id=ZERO_BATCH, first_sequence=1, last_sequence=1,
        commit_hash="b" * 64, operations=(("trading_event_v1", "batch-1"),))

    class StrictClient(Client):
        typed_insert_strict = True
        typed_insert_dispatch = authority
        def execute(self, sql: str, *, query_id=None) -> str:
            return super().execute(sql)

    client = StrictClient()
    assert anchor.publish_terminal_backtest_snapshot(client, PREFIX, captured()) == HASH
    assert authority._read_gate(RUN)[0].registered == 0
    assert client.inserts == 1
    assert anchor.publish_terminal_backtest_snapshot(client, PREFIX, captured()) == HASH
    assert client.inserts == 1
    row = client.rows[0]
    authority.assert_terminal_anchor_receipt(run_id=RUN, account_id="DU1",
        batch_id=BATCH, last_sequence=1, anchor_hash=row["content_hash"],
        snapshot_hash=HASH)
    barrier = authority.acquire_cold_barrier(RUN)
    with pytest.raises(Exception, match="prefix is not cold-verified"):
        barrier.verify_terminal_anchor_receipt(client, PREFIX, account_id="DU1")
    barrier.prefix_verified = True  # Isolate anchor receipt; prefix proof has separate tests.
    barrier.context_verified = True
    assert barrier.verify_terminal_anchor_receipt(client, PREFIX,
        account_id="DU1") == {"state_hash": HASH}
    with pytest.raises(Exception, match="conflicts"):
        authority.assert_terminal_anchor_receipt(run_id=RUN, account_id="DU1",
            batch_id=BATCH, last_sequence=1, anchor_hash="c" * 64,
            snapshot_hash=HASH)
    from src.trading_runtime.arte_typed_insert_dispatch import _terminal_receipt_path
    del authority.keeper.rows[_terminal_receipt_path(RUN, "DU1")]
    with pytest.raises(Exception, match="receipt is missing"):
        barrier.verify_terminal_anchor_receipt(client, PREFIX, account_id="DU1")
