from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.trading_runtime import arte_journal_writer as writer
from src.trading_runtime import arte_admission_epoch_proof as admission_proof
from src.trading_runtime.arte_typed_insert_dispatch import (
    TypedInsertDispatch, typed_insert_query_id,
)
from src.trading_runtime.keeper_ownership import KeeperUnavailable


SQL = ("INSERT INTO arte.trading_event_v1 (run_id) SETTINGS "
       "async_insert=1,wait_for_async_insert=1,insert_deduplicate=1,"
       "insert_deduplication_token='batch-1' FORMAT JSONEachRow\n{}")


class NoNodeError(Exception):
    pass


class NodeExistsError(Exception):
    pass


class BadVersionError(Exception):
    pass


@dataclass
class Stat:
    version: int = 0


class Transaction:
    def __init__(self, keeper):
        self.keeper = keeper
        self.checks = []
        self.ops = []

    def check(self, path, version):
        self.checks.append((path, version))

    def set_data(self, path, value, version):
        self.ops.append(("set", path, value, version))

    def create(self, path, value, ephemeral=False):
        assert not ephemeral
        self.ops.append(("create", path, value, None))

    def commit(self):
        for path, version in self.checks:
            if path not in self.keeper.rows or self.keeper.rows[path][1].version != version:
                return [BadVersionError()]
        for kind, path, _, version in self.ops:
            if kind == "create" and path in self.keeper.rows:
                return [NodeExistsError()]
            if kind == "set" and self.keeper.rows[path][1].version != version:
                return [BadVersionError()]
        for kind, path, value, _ in self.ops:
            self.keeper.rows[path] = (value, Stat(
                self.keeper.rows[path][1].version + 1 if kind == "set" else 0))
        return [True]


class Keeper:
    def __init__(self):
        self.rows = {}

    def ensure_path(self, _path):
        pass

    def create(self, path, value):
        if path in self.rows:
            raise NodeExistsError()
        self.rows[path] = (value, Stat())

    def get(self, path):
        if path not in self.rows:
            raise NoNodeError()
        return self.rows[path]

    def transaction(self):
        return Transaction(self)


class Client:
    typed_insert_strict = True

    def __init__(self, dispatch=None, *, lose_response=False):
        self.typed_insert_dispatch = dispatch
        self.lose_response = lose_response
        self.calls = []

    def execute(self, sql, *, query_id=None):
        self.calls.append((sql, query_id))
        if self.lose_response:
            # Server may commit after the HTTP response is lost.
            raise TimeoutError("response lost")
        return ""


def test_acknowledged_insert_blocks_cold_until_explicit_parent_seal() -> None:
    authority = TypedInsertDispatch(Keeper())
    authority.initialize_new_run("run-1")
    client = Client(authority)
    authority.execute_typed_insert(client, run_id="run-1", table="trading_event_v1",
                                   token="batch-1", sql=SQL)
    assert client.calls[0][1] == typed_insert_query_id("run-1", "trading_event_v1", "batch-1")
    with pytest.raises(KeeperUnavailable, match="pending or ambiguous"):
        authority.acquire_cold_barrier("run-1")
    # This simulates a caller that has verified its late parent commit fence.
    authority.seal_verified_operation(run_id="run-1", table="trading_event_v1",
                                      token="batch-1", sql=SQL)
    barrier = authority.acquire_cold_barrier("run-1")
    barrier.assert_fenced("run-1")
    with pytest.raises(KeeperUnavailable, match="cold-fenced"):
        authority.execute_typed_insert(client, run_id="run-1", table="trading_event_v1",
                                       token="batch-2", sql=SQL)
    barrier.release()
    assert len(client.calls) == 1


def test_lost_response_remains_durable_pending_even_after_late_server_commit() -> None:
    authority = TypedInsertDispatch(Keeper())
    authority.initialize_new_run("run-1")
    client = Client(authority, lose_response=True)
    with pytest.raises(TimeoutError, match="response lost"):
        authority.execute_typed_insert(client, run_id="run-1", table="trading_event_v1",
                                       token="batch-1", sql=SQL)
    # A delayed server-side commit cannot clear the Keeper pending operation.
    client.lose_response = False
    with pytest.raises(KeeperUnavailable, match="pending or ambiguous"):
        authority.acquire_cold_barrier("run-1")
    with pytest.raises(KeeperUnavailable, match="ambiguous pending"):
        authority.execute_typed_insert(client, run_id="run-1", table="trading_event_v1",
                                       token="batch-1", sql=SQL)
    assert len(client.calls) == 1


def test_strict_writer_refuses_unwrapped_insert(monkeypatch) -> None:
    monkeypatch.setattr(writer, "_wire_row", lambda _name, row: row)
    client = Client()
    with pytest.raises(RuntimeError, match="lacks durable dispatch authority"):
        writer._insert(client, "trading_event_v1", ({"run_id": "run-1"},), "batch-1")
    assert not client.calls


def test_strict_writer_uses_dispatch_and_rejects_token_body_reuse(monkeypatch) -> None:
    monkeypatch.setattr(writer, "_wire_row", lambda _name, row: row)
    authority = TypedInsertDispatch(Keeper())
    authority.initialize_new_run("run-1")
    client = Client(authority)
    writer._insert(client, "trading_event_v1", ({"run_id": "run-1"},), "batch-1")
    assert len(client.calls) == 1
    writer._insert(client, "trading_event_v1", ({"run_id": "run-1"},), "batch-1")
    assert len(client.calls) == 1
    with pytest.raises(KeeperUnavailable, match="ambiguous pending"):
        writer._insert(client, "trading_event_v1",
                       ({"run_id": "run-1", "different": 1},), "batch-1")
    assert len(client.calls) == 1


def test_dispatch_requires_explicit_fresh_run_gate() -> None:
    authority = TypedInsertDispatch(Keeper())
    client = Client(authority)
    with pytest.raises(KeeperUnavailable, match="gate is absent"):
        authority.execute_typed_insert(client, run_id="run-1", table="trading_event_v1",
                                       token="batch-1", sql=SQL)
    assert not client.calls


def test_cold_barrier_is_accepted_by_strict_admission_audit(monkeypatch) -> None:
    authority = TypedInsertDispatch(Keeper())
    authority.initialize_new_run("run-1")
    barrier = authority.acquire_cold_barrier("run-1")
    monkeypatch.setattr(admission_proof, "_rows", lambda *_args: [])
    class EmptyProofs:
        def load_head(self, _run_id):
            return None
    assert admission_proof.audit_attested_admission_revisions(
        object(), EmptyProofs(), "run-1", quiescence=barrier) == 0
    barrier.release()
    with pytest.raises(KeeperUnavailable, match="barrier was lost"):
        barrier.assert_fenced("run-1")


def test_operation_cap_fails_closed_without_unbounded_keeper_nodes() -> None:
    authority = TypedInsertDispatch(Keeper(), max_operations=1)
    authority.initialize_new_run("run-1")
    client = Client(authority)
    authority.execute_typed_insert(client, run_id="run-1", table="trading_event_v1",
                                   token="batch-1", sql=SQL)
    authority.seal_verified_operation(run_id="run-1", table="trading_event_v1",
                                      token="batch-1", sql=SQL)
    with pytest.raises(KeeperUnavailable, match="operation cap reached"):
        authority.execute_typed_insert(client, run_id="run-1", table="trading_event_v1",
                                       token="batch-2", sql=SQL)
    assert len(client.calls) == 1


def test_typed_batch_seals_dispatched_rows_only_after_commit_readback() -> None:
    from test_arte_journal_writer import MemoryClient, batch

    authority = TypedInsertDispatch(Keeper())
    authority.initialize_new_run(batch().run_id)
    class WriterClient(MemoryClient):
        typed_insert_strict = True
        typed_insert_dispatch = authority
        query_ids = []
        def execute(self, sql, *, query_id=None):
            if sql.startswith("INSERT "):
                self.query_ids.append(query_id)
            return super().execute(sql)
    client = WriterClient()
    assert writer.publish_typed_batch(client, batch()) == batch().batch_id
    assert len(client.query_ids) == 2
    barrier = authority.acquire_cold_barrier(batch().run_id)
    barrier.assert_fenced(batch().run_id)


def test_retry_seals_prior_acknowledged_ops_after_exact_commit_readback() -> None:
    from test_arte_journal_writer import MemoryClient, batch

    authority = TypedInsertDispatch(Keeper())
    authority.initialize_new_run(batch().run_id)
    class WriterClient(MemoryClient):
        typed_insert_strict = True
        typed_insert_dispatch = authority
        def execute(self, sql, *, query_id=None):
            return super().execute(sql)
    client = WriterClient()
    seal = authority.seal_verified_operation
    calls = 0
    def crash_once(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("worker crashed after commit readback")
        return seal(**kwargs)
    authority.seal_verified_operation = crash_once
    with pytest.raises(OSError, match="after commit readback"):
        writer.publish_typed_batch(client, batch())
    assert client.inserts == ["trading_event_v1", "trading_commit_v1"]
    with pytest.raises(KeeperUnavailable, match="pending or ambiguous"):
        authority.acquire_cold_barrier(batch().run_id)
    authority.seal_verified_operation = seal
    assert writer.publish_typed_batch(client, batch()) == batch().batch_id
    assert client.inserts == ["trading_event_v1", "trading_commit_v1"]
    authority.acquire_cold_barrier(batch().run_id)
