from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace

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
BATCH_ID = "00000000-0000-0000-0000-000000000012"
ZERO_BATCH = "00000000-0000-0000-0000-000000000000"


def reserve_direct(authority):
    authority.assert_next_batch(run_id="run-1", batch_id=BATCH_ID,
        prior_batch_id=ZERO_BATCH, first_sequence=1, last_sequence=1)


def compact_direct(authority, token="batch-1"):
    authority.compact_verified_batch(run_id="run-1", batch_id=BATCH_ID,
        prior_batch_id=ZERO_BATCH, first_sequence=1, last_sequence=1,
        commit_hash="a" * 64, operations=(("trading_event_v1", token),))


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

    def delete(self, path, version):
        self.ops.append(("delete", path, None, version))

    def commit(self):
        for path, version in self.checks:
            if path not in self.keeper.rows or self.keeper.rows[path][1].version != version:
                return [BadVersionError()]
        for kind, path, _, version in self.ops:
            if kind == "create" and path in self.keeper.rows:
                return [NodeExistsError()]
            if kind == "set" and self.keeper.rows[path][1].version != version:
                return [BadVersionError()]
            if kind == "delete" and self.keeper.rows[path][1].version != version:
                return [BadVersionError()]
        for kind, path, value, _ in self.ops:
            if kind == "delete":
                del self.keeper.rows[path]
            else:
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
    reserve_direct(authority)
    client = Client(authority)
    authority.execute_typed_insert(client, run_id="run-1", table="trading_event_v1",
                                   token="batch-1", sql=SQL, batch_id=BATCH_ID,
                                   batch_last_sequence=1)
    assert client.calls[0][1] == typed_insert_query_id("run-1", "trading_event_v1", "batch-1")
    with pytest.raises(KeeperUnavailable, match="pending or ambiguous"):
        authority.acquire_cold_barrier("run-1")
    # This simulates a caller that has verified its late parent commit fence.
    authority.seal_verified_operation(run_id="run-1", table="trading_event_v1",
                                      token="batch-1", sql=SQL, batch_id=BATCH_ID,
                                      batch_last_sequence=1)
    compact_direct(authority)
    barrier = authority.acquire_cold_barrier("run-1")
    with pytest.raises(KeeperUnavailable, match="prefix is not cold-verified"):
        barrier.assert_fenced("run-1")
    barrier.prefix_verified = True  # Gate-only race test; no CH fixture here.
    barrier.assert_fenced("run-1")
    with pytest.raises(KeeperUnavailable, match="cold-fenced"):
        authority.execute_typed_insert(client, run_id="run-1", table="trading_event_v1",
                                       token="batch-2", sql=SQL, batch_id=BATCH_ID,
                                       batch_last_sequence=1)
    barrier.release()
    assert len(client.calls) == 1


def test_lost_response_remains_durable_pending_even_after_late_server_commit() -> None:
    authority = TypedInsertDispatch(Keeper())
    authority.initialize_new_run("run-1")
    reserve_direct(authority)
    client = Client(authority, lose_response=True)
    with pytest.raises(TimeoutError, match="response lost"):
        authority.execute_typed_insert(client, run_id="run-1", table="trading_event_v1",
                                       token="batch-1", sql=SQL, batch_id=BATCH_ID,
                                       batch_last_sequence=1)
    # A delayed server-side commit cannot clear the Keeper pending operation.
    client.lose_response = False
    with pytest.raises(KeeperUnavailable, match="pending or ambiguous"):
        authority.acquire_cold_barrier("run-1")
    with pytest.raises(KeeperUnavailable, match="ambiguous pending"):
        authority.execute_typed_insert(client, run_id="run-1", table="trading_event_v1",
                                       token="batch-1", sql=SQL, batch_id=BATCH_ID,
                                       batch_last_sequence=1)
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
    reserve_direct(authority)
    client = Client(authority)
    writer._insert(client, "trading_event_v1", ({"run_id": "run-1"},),
                   "batch-1", dispatch_sequence=1, dispatch_batch_id=BATCH_ID)
    assert len(client.calls) == 1
    writer._insert(client, "trading_event_v1", ({"run_id": "run-1"},),
                   "batch-1", dispatch_sequence=1, dispatch_batch_id=BATCH_ID)
    assert len(client.calls) == 1
    with pytest.raises(KeeperUnavailable, match="ambiguous pending"):
        writer._insert(client, "trading_event_v1",
                       ({"run_id": "run-1", "different": 1},), "batch-1",
                       dispatch_sequence=1, dispatch_batch_id=BATCH_ID)
    assert len(client.calls) == 1


def test_dispatch_requires_explicit_fresh_run_gate() -> None:
    authority = TypedInsertDispatch(Keeper())
    client = Client(authority)
    with pytest.raises(KeeperUnavailable, match="gate is absent"):
        authority.execute_typed_insert(client, run_id="run-1", table="trading_event_v1",
                                       token="batch-1", sql=SQL, batch_id=BATCH_ID,
                                       batch_last_sequence=1)
    assert not client.calls


def test_competing_writers_cannot_reserve_same_run_prefix() -> None:
    authority = TypedInsertDispatch(Keeper())
    authority.initialize_new_run("run-1")
    reserve_direct(authority)
    with pytest.raises(KeeperUnavailable, match="Competing typed batch"):
        authority.assert_next_batch(
            run_id="run-1", batch_id="00000000-0000-0000-0000-000000000013",
            prior_batch_id=ZERO_BATCH, first_sequence=1, last_sequence=1)
    assert authority._read_gate("run-1")[0].active_batch_id == BATCH_ID
    with pytest.raises(KeeperUnavailable, match="pending or ambiguous"):
        authority.acquire_cold_barrier("run-1")


def test_cold_barrier_is_accepted_by_strict_admission_audit(monkeypatch) -> None:
    authority = TypedInsertDispatch(Keeper())
    authority.initialize_new_run("run-1")
    barrier = authority.acquire_cold_barrier("run-1")
    monkeypatch.setattr(writer, "load_committed_prefix", lambda *_args, **_kwargs: None)
    class EmptyReader:
        def execute(self, sql):
            assert "FROM arte.trading_commit_v2 " in sql
            return ""
    barrier.verify_committed_prefix(EmptyReader(), journal_profile="v1")
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
    reserve_direct(authority)
    client = Client(authority)
    authority.execute_typed_insert(client, run_id="run-1", table="trading_event_v1",
                                   token="batch-1", sql=SQL, batch_id=BATCH_ID,
                                   batch_last_sequence=1)
    authority.seal_verified_operation(run_id="run-1", table="trading_event_v1",
                                      token="batch-1", sql=SQL, batch_id=BATCH_ID,
                                      batch_last_sequence=1)
    with pytest.raises(KeeperUnavailable, match="operation cap reached"):
        authority.execute_typed_insert(client, run_id="run-1", table="trading_event_v1",
                                       token="batch-2", sql=SQL, batch_id=BATCH_ID,
                                       batch_last_sequence=1)
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
    barrier.verify_committed_prefix(client, journal_profile="v1")
    barrier.assert_fenced(batch().run_id)


def test_cold_prefix_uses_explicit_v2_fence_and_rejects_mixed_profile(monkeypatch) -> None:
    from test_arte_journal_writer import MemoryClient, batch
    from src.trading_runtime.arte_journal_writer import V2CommittedPrefix

    authority = TypedInsertDispatch(Keeper())
    authority.initialize_new_run(batch().run_id)
    class WriterClient(MemoryClient):
        typed_insert_strict = True
        typed_insert_dispatch = authority
        def execute(self, sql, *, query_id=None):
            return super().execute(sql)
    client = WriterClient()
    writer.publish_typed_batch(client, batch())
    client.tables["trading_commit_v2"] = client.tables.pop("trading_commit_v1")
    v2_prefix = V2CommittedPrefix(batch().run_id, 1, batch().batch_id,
                                  "bucket-1", "running", (batch().batch_id,))
    calls = []
    def v2_read(_client, _run_id, *, journal_profile):
        calls.append(journal_profile)
        return v2_prefix
    monkeypatch.setattr(writer, "load_committed_prefix", v2_read)
    barrier = authority.acquire_cold_barrier(batch().run_id)
    assert barrier.verify_committed_prefix(client,
                                           journal_profile="backtest_v2") == v2_prefix
    assert calls == ["backtest_v2"]
    assert any("FROM arte.trading_commit_v2 " in sql for sql in client.selects)
    with pytest.raises(KeeperUnavailable, match="cannot mix V1 and V2"):
        barrier.verify_committed_prefix(client, journal_profile="v1")
    client.tables["trading_commit_v1"] = list(client.tables["trading_commit_v2"])
    with pytest.raises(KeeperUnavailable, match="cannot mix V1 and V2"):
        barrier.verify_committed_prefix(client, journal_profile="backtest_v2")
    with pytest.raises(KeeperUnavailable, match="prefix is not cold-verified"):
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


def test_crash_before_keeper_compaction_retries_without_second_insert() -> None:
    from test_arte_journal_writer import MemoryClient, batch

    authority = TypedInsertDispatch(Keeper())
    authority.initialize_new_run(batch().run_id)
    class WriterClient(MemoryClient):
        typed_insert_strict = True
        typed_insert_dispatch = authority
        def execute(self, sql, *, query_id=None):
            return super().execute(sql)
    client = WriterClient()
    compact = authority.compact_verified_batch
    authority.compact_verified_batch = lambda **_kwargs: (_ for _ in ()).throw(
        OSError("crash before Keeper compaction"))
    with pytest.raises(OSError, match="before Keeper compaction"):
        writer.publish_typed_batch(client, batch())
    assert client.inserts == ["trading_event_v1", "trading_commit_v1"]
    with pytest.raises(KeeperUnavailable, match="pending or ambiguous"):
        authority.acquire_cold_barrier(batch().run_id)
    authority.compact_verified_batch = compact
    writer.publish_typed_batch(client, batch())
    assert client.inserts == ["trading_event_v1", "trading_commit_v1"]
    barrier = authority.acquire_cold_barrier(batch().run_id)
    assert barrier.verify_committed_prefix(client, journal_profile="v1").last_batch_id == batch().batch_id


def test_sealed_batch_compaction_reuses_bounded_keeper_window() -> None:
    from test_arte_journal_writer import MemoryClient, batch

    first = batch()
    second_id = "00000000-0000-0000-0000-000000000014"
    event = dict(first.events[0])
    event.update(batch_id=second_id, record_id="00000000-0000-0000-0000-000000000015",
                 sequence=2)
    event.pop("content_hash")
    second = replace(first, batch_id=second_id, prior_batch_id=first.batch_id,
                     first_sequence=2, last_sequence=2, source_cursor="bucket-2",
                     events=(writer.typed_row("trading_event_v1", event),))
    authority = TypedInsertDispatch(Keeper(), max_operations=2)
    authority.initialize_new_run(first.run_id)
    class WriterClient(MemoryClient):
        typed_insert_strict = True
        typed_insert_dispatch = authority
        def execute(self, sql, *, query_id=None):
            return super().execute(sql)
    client = WriterClient()
    writer.publish_typed_batch(client, first)
    assert authority._read_gate(first.run_id)[0].registered == 0
    writer.publish_typed_batch(client, second)
    gate = authority._read_gate(first.run_id)[0]
    assert (gate.registered, gate.compacted_through, gate.compacted_batch_id) == (
        0, 2, second_id)
    barrier = authority.acquire_cold_barrier(first.run_id)
    assert barrier.verify_committed_prefix(client, journal_profile="v1").last_batch_id == second_id
    client.tables["trading_commit_v1"][-1]["source_cursor"] = "tampered"
    with pytest.raises(KeeperUnavailable, match="commit differs from dispatch hash"):
        barrier.verify_committed_prefix(client, journal_profile="v1")
    client.tables["trading_commit_v1"][-1]["source_cursor"] = "bucket-2"
    barrier.release()
    before = len(client.inserts)
    with pytest.raises(KeeperUnavailable, match="precedes compacted watermark"):
        writer.publish_typed_batch(client, first)
    assert len(client.inserts) == before


def test_strict_compaction_rejects_unattested_legacy_prefix() -> None:
    from test_arte_journal_writer import MemoryClient, batch

    first = batch()
    second_id = "00000000-0000-0000-0000-000000000014"
    event = dict(first.events[0])
    event.update(batch_id=second_id, record_id="00000000-0000-0000-0000-000000000015",
                 sequence=2)
    event.pop("content_hash")
    second = replace(first, batch_id=second_id, prior_batch_id=first.batch_id,
                     first_sequence=2, last_sequence=2, source_cursor="bucket-2",
                     events=(writer.typed_row("trading_event_v1", event),))
    class WriterClient(MemoryClient):
        typed_insert_strict = True
        def execute(self, sql, *, query_id=None):
            return super().execute(sql)
    client = WriterClient()
    client.typed_insert_strict = False
    writer.publish_typed_batch(client, first)
    authority = TypedInsertDispatch(Keeper())
    authority.initialize_new_run(first.run_id)
    client.typed_insert_strict = True
    client.typed_insert_dispatch = authority
    with pytest.raises(KeeperUnavailable, match="does not extend compacted prefix"):
        writer.publish_typed_batch(client, second)
    assert client.inserts == ["trading_event_v1", "trading_commit_v1"]
    barrier = authority.acquire_cold_barrier(first.run_id)
    with pytest.raises(KeeperUnavailable, match="prefix is not cold-verified"):
        barrier.assert_fenced(first.run_id)
    with pytest.raises(KeeperUnavailable, match="prefix lacks dispatch compaction"):
        barrier.verify_committed_prefix(client, journal_profile="v1")
