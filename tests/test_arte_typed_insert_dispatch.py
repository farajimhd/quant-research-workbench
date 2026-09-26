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
    attest_direct(authority, "run-1")
    authority.assert_next_batch(run_id="run-1", batch_id=BATCH_ID,
        prior_batch_id=ZERO_BATCH, first_sequence=1, last_sequence=1)


def attest_direct(authority, run_id):
    from src.trading_runtime.arte_typed_insert_dispatch import _context_receipt_path
    path = _context_receipt_path(run_id)
    if path not in authority.keeper.rows:
        authority.keeper.create(path, b"1\n" + b"a" * 64)


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
    barrier.context_verified = True
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


def test_run_context_receipt_compacts_four_exact_operations_and_survives_retry() -> None:
    authority = TypedInsertDispatch(Keeper(), max_operations=4)
    authority.initialize_new_run("run-1")
    client = Client(authority)
    operations = tuple((table, f"run-context:run-1:{table}") for table in (
        "trading_run_v1", "trading_runtime_config_v1",
        "trading_run_account_v1", "trading_run_context_commit_v1"))
    for table, token in operations:
        sql = SQL.replace("trading_event_v1", table)
        authority.execute_typed_insert(client, run_id="run-1", table=table,
            token=token, sql=sql, batch_id=ZERO_BATCH, batch_last_sequence=0)
    with pytest.raises(KeeperUnavailable, match="pending or ambiguous"):
        authority.acquire_cold_barrier("run-1")
    for table, token in operations:
        authority.seal_verified_operation(run_id="run-1", table=table,
            token=token, batch_id=ZERO_BATCH, batch_last_sequence=0)
    authority.compact_verified_run_context(run_id="run-1", fence_hash="a" * 64,
        operations=operations)
    authority.assert_run_context_receipt(run_id="run-1", fence_hash="a" * 64)
    assert authority._read_gate("run-1")[0].registered == 0
    authority.compact_verified_run_context(run_id="run-1", fence_hash="a" * 64,
        operations=operations)
    assert len(client.calls) == 4
    with pytest.raises(KeeperUnavailable, match="conflicts"):
        authority.compact_verified_run_context(run_id="run-1", fence_hash="b" * 64,
            operations=operations)


def test_run_context_lost_response_cannot_be_compacted_from_late_row() -> None:
    authority = TypedInsertDispatch(Keeper())
    authority.initialize_new_run("run-1")
    client = Client(authority, lose_response=True)
    with pytest.raises(TimeoutError):
        authority.execute_typed_insert(client, run_id="run-1",
            table="trading_run_v1", token="run:run-1",
            sql=SQL.replace("trading_event_v1", "trading_run_v1"),
            batch_id=ZERO_BATCH, batch_last_sequence=0)
    with pytest.raises(KeeperUnavailable, match="acknowledged"):
        authority.seal_verified_operation(run_id="run-1",
            table="trading_run_v1", token="run:run-1",
            batch_id=ZERO_BATCH, batch_last_sequence=0)
    with pytest.raises(KeeperUnavailable, match="unresolved"):
        authority.compact_verified_run_context(run_id="run-1", fence_hash="a" * 64,
            operations=(("trading_run_v1", "run:run-1"),))


def test_cold_context_rejects_unattested_clickhouse_fence(monkeypatch) -> None:
    from src.trading_runtime.journal_contract import canonical_json
    from hashlib import sha256

    authority = TypedInsertDispatch(Keeper())
    authority.initialize_new_run("run-1")
    barrier = authority.acquire_cold_barrier("run-1")
    stable = {"run_id": "run-1", "run_month": "2026-09-01",
              "run_hash": "a" * 64, "config_hash": "b" * 64,
              "account_count": 1, "account_hash": "c" * 64}
    monkeypatch.setattr(writer, "load_typed_run_context",
                        lambda _client, _run_id: {"accounts": ("account-1",)})
    monkeypatch.setattr(writer, "_rows", lambda _client, _query: [stable])
    with pytest.raises(KeeperUnavailable, match="receipt is missing"):
        barrier.verify_run_context_receipt(object())
    barrier.release()
    digest = sha256(canonical_json(stable).encode()).hexdigest()
    from src.trading_runtime.arte_typed_insert_dispatch import _context_receipt_path
    authority.keeper.create(_context_receipt_path("run-1"), ("1\n" + digest).encode())
    barrier = authority.acquire_cold_barrier("run-1")
    assert barrier.verify_run_context_receipt(object()) == {
        "accounts": ("account-1",)}


def test_run_context_cannot_attest_legacy_rows_without_dispatch_operations() -> None:
    authority = TypedInsertDispatch(Keeper())
    authority.initialize_new_run("run-1")
    with pytest.raises(KeeperUnavailable, match="lacks durable dispatch identity"):
        authority.compact_verified_run_context(run_id="run-1", fence_hash="a" * 64,
            operations=(("trading_run_v1", "run:run-1"),))


def test_batch_reservation_rejects_missing_or_invalid_context_receipt() -> None:
    from src.trading_runtime.arte_typed_insert_dispatch import _context_receipt_path
    authority = TypedInsertDispatch(Keeper())
    authority.initialize_new_run("run-1")
    kwargs = dict(run_id="run-1", batch_id=BATCH_ID,
                  prior_batch_id=ZERO_BATCH, first_sequence=1, last_sequence=1)
    with pytest.raises(KeeperUnavailable, match="lacks run-context Keeper receipt"):
        authority.assert_next_batch(**kwargs)
    assert authority._read_gate("run-1")[0].active_batch_id == ZERO_BATCH
    authority.keeper.create(_context_receipt_path("run-1"), b"legacy")
    with pytest.raises(KeeperUnavailable, match="receipt is invalid"):
        authority.assert_next_batch(**kwargs)
    assert authority._read_gate("run-1")[0].active_batch_id == ZERO_BATCH


def test_sealed_context_refuses_late_context_insert() -> None:
    authority = TypedInsertDispatch(Keeper())
    authority.initialize_new_run("run-1")
    attest_direct(authority, "run-1")
    client = Client(authority)
    with pytest.raises(KeeperUnavailable, match="already sealed"):
        authority.execute_typed_insert(client, run_id="run-1",
            table="trading_run_v1", token="run:run-1",
            sql=SQL.replace("trading_event_v1", "trading_run_v1"),
            batch_id=ZERO_BATCH, batch_last_sequence=0)
    assert client.calls == []


def test_terminal_lost_response_and_stale_prefix_remain_cold_blocked() -> None:
    authority = TypedInsertDispatch(Keeper())
    authority.initialize_new_run("run-1")
    reserve_direct(authority)
    client = Client(authority)
    authority.execute_typed_insert(client, run_id="run-1", table="trading_event_v1",
        token="batch-1", sql=SQL, batch_id=BATCH_ID, batch_last_sequence=1)
    authority.seal_verified_operation(run_id="run-1", table="trading_event_v1",
        token="batch-1", batch_id=BATCH_ID, batch_last_sequence=1)
    compact_direct(authority)
    terminal_sql = SQL.replace("trading_event_v1", "trading_backtest_snapshot_anchor_v1")
    with pytest.raises(KeeperUnavailable, match="differs from compacted run prefix"):
        authority.execute_typed_insert(client, run_id="run-1",
            table="trading_backtest_snapshot_anchor_v1", token="terminal:DU1",
            sql=terminal_sql, batch_id=BATCH_ID, batch_last_sequence=2,
            terminal_account_id="DU1")
    client.lose_response = True
    with pytest.raises(TimeoutError, match="response lost"):
        authority.execute_typed_insert(client, run_id="run-1",
            table="trading_backtest_snapshot_anchor_v1", token="terminal:DU1",
            sql=terminal_sql, batch_id=BATCH_ID, batch_last_sequence=1,
            terminal_account_id="DU1")
    with pytest.raises(KeeperUnavailable, match="pending or ambiguous"):
        authority.acquire_cold_barrier("run-1")
    with pytest.raises(KeeperUnavailable, match="acknowledged"):
        authority.seal_verified_operation(run_id="run-1",
            table="trading_backtest_snapshot_anchor_v1", token="terminal:DU1",
            batch_id=BATCH_ID, batch_last_sequence=1, terminal=True)


def test_snapshot_account_reservation_blocks_competing_revision_and_cold() -> None:
    authority = TypedInsertDispatch(Keeper())
    authority.initialize_new_run("run-1")
    attest_direct(authority, "run-1")
    assert authority.reserve_snapshot_revision(run_id="run-1", account_id="DU1",
        revision=1, latest_ch_revision=None) == "active"
    assert authority.reserve_snapshot_revision(run_id="run-1", account_id="DU1",
        revision=1, latest_ch_revision=None) == "active"
    with pytest.raises(KeeperUnavailable, match="Competing portfolio snapshot"):
        authority.reserve_snapshot_revision(run_id="run-1", account_id="DU1",
            revision=2, latest_ch_revision=None)
    with pytest.raises(KeeperUnavailable, match="pending or ambiguous"):
        authority.acquire_cold_barrier("run-1")


def test_policy_dispatch_compacts_to_one_immutable_receipt() -> None:
    from src.trading_runtime.arte_journal_schema import POLICY_ALLOWED_TABLES
    from src.trading_runtime.arte_typed_insert_dispatch import _POLICY_TABLES
    assert _POLICY_TABLES == {
        "trading_portfolio_policy_v1", "trading_portfolio_policy_commit_v2",
        *(table for table, _ in POLICY_ALLOWED_TABLES.values()),
    }
    policy_hash = "a" * 64
    authority = TypedInsertDispatch(Keeper(), max_operations=2)
    assert authority.begin_policy_publication(
        policy_hash=policy_hash, has_ch_rows=False) == "publishing"
    client = Client(authority)
    operations = (("trading_portfolio_policy_v1",
                   f"portfolio-policy:{policy_hash}:trading_portfolio_policy_v1"),
                  ("trading_portfolio_policy_commit_v2",
                   f"portfolio-policy:{policy_hash}:commit"))
    for table, token in operations:
        sql = SQL.replace("trading_event_v1", table).replace("batch-1", token)
        authority.execute_policy_insert(client, policy_hash=policy_hash,
            table=table, token=token, sql=sql)
        authority.seal_verified_policy_operation(
            policy_hash=policy_hash, table=table, token=token)
    authority.compact_verified_policy(
        policy_hash=policy_hash, fence_hash="b" * 64, operations=operations)
    authority.assert_policy_receipt(policy_hash=policy_hash, fence_hash="b" * 64)
    assert authority._read_policy_gate(policy_hash)[0].registered == 0
    assert len(client.calls) == 2
    assert authority.begin_policy_publication(
        policy_hash=policy_hash, has_ch_rows=True) == "committed"
    authority.compact_verified_policy(
        policy_hash=policy_hash, fence_hash="b" * 64, operations=operations)
    with pytest.raises(KeeperUnavailable, match="receipt differs"):
        authority.assert_policy_receipt(policy_hash=policy_hash,
                                        fence_hash="c" * 64)


def test_policy_lost_response_and_legacy_rows_fail_closed() -> None:
    policy_hash = "a" * 64
    authority = TypedInsertDispatch(Keeper())
    with pytest.raises(KeeperUnavailable, match="unattested legacy"):
        authority.begin_policy_publication(
            policy_hash=policy_hash, has_ch_rows=True)
    authority.begin_policy_publication(policy_hash=policy_hash, has_ch_rows=False)
    client = Client(authority, lose_response=True)
    table = "trading_portfolio_policy_v1"
    token = f"portfolio-policy:{policy_hash}:{table}"
    with pytest.raises(TimeoutError, match="response lost"):
        authority.execute_policy_insert(client, policy_hash=policy_hash,
            table=table, token=token,
            sql=SQL.replace("trading_event_v1", table).replace("batch-1", token))
    with pytest.raises(KeeperUnavailable, match="ambiguous pending"):
        authority.execute_policy_insert(client, policy_hash=policy_hash,
            table=table, token=token,
            sql=SQL.replace("trading_event_v1", table).replace("batch-1", token))
    with pytest.raises(KeeperUnavailable, match="unresolved"):
        authority.compact_verified_policy(
            policy_hash=policy_hash, fence_hash="b" * 64,
            operations=((table, token),))
    with pytest.raises(KeeperUnavailable, match="receipt differs"):
        authority.assert_policy_receipt(policy_hash=policy_hash,
                                        fence_hash="b" * 64)


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
    barrier.context_verified = True  # Prefix-only fixture; context verification is tested separately.
    monkeypatch.setattr(admission_proof, "_rows", lambda *_args: [])
    class EmptyProofs:
        def load_head(self, _run_id):
            return None
    assert admission_proof.audit_attested_admission_revisions(
        object(), EmptyProofs(), "run-1", quiescence=barrier) == 0
    barrier.release()
    with pytest.raises(KeeperUnavailable, match="barrier was lost"):
        barrier.assert_fenced("run-1")


def test_v4_cold_barrier_accepts_only_empty_verified_prefix() -> None:
    from test_arte_journal_writer import MemoryClient

    authority = TypedInsertDispatch(Keeper())
    authority.initialize_new_run("run-1")
    barrier = authority.acquire_cold_barrier("run-1")
    client = MemoryClient()
    assert barrier.verify_committed_prefix(
        client, journal_profile="backtest_v4") is None
    assert barrier.prefix_verified
    client.tables["trading_commit_v1"] = [{"run_id": "run-1", "batch_id": BATCH_ID}]
    with pytest.raises(KeeperUnavailable, match="cannot mix V4"):
        barrier.verify_committed_prefix(client, journal_profile="backtest_v4")
    assert not barrier.prefix_verified


def test_v4_cold_barrier_matches_nonempty_keeper_watermark() -> None:
    from test_arte_journal_writer import MemoryClient, batch
    from src.trading_runtime.arte_journal_commit_v4 import (
        publish_base_typed_batch_v4,
    )

    item = batch()
    authority = TypedInsertDispatch(Keeper())
    authority.initialize_new_run(item.run_id)
    attest_direct(authority, item.run_id)

    class WriterClient(MemoryClient):
        typed_insert_strict = True
        typed_insert_dispatch = authority

        def execute(self, sql, *, query_id=None):
            return super().execute(sql)

    client = WriterClient()
    assert publish_base_typed_batch_v4(client, item) == item.batch_id
    barrier = authority.acquire_cold_barrier(item.run_id)
    prefix = barrier.verify_committed_prefix(
        client, journal_profile="backtest_v4")
    assert (prefix.last_sequence, prefix.last_batch_id) == (1, item.batch_id)
    client.tables["trading_commit_v4"][0]["source_cursor"] = "tampered"
    with pytest.raises((RuntimeError, KeeperUnavailable)):
        barrier.verify_committed_prefix(client, journal_profile="backtest_v4")
    assert not barrier.prefix_verified


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
    attest_direct(authority, batch().run_id)
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
    with pytest.raises(KeeperUnavailable, match="run context is not cold-verified"):
        barrier.assert_fenced(batch().run_id)
    barrier.context_verified = True  # This fixture isolates the batch prefix.
    barrier.assert_fenced(batch().run_id)


def test_cold_prefix_uses_explicit_v2_fence_and_rejects_mixed_profile(monkeypatch) -> None:
    from test_arte_journal_writer import MemoryClient, batch
    from src.trading_runtime.arte_journal_writer import V2CommittedPrefix

    authority = TypedInsertDispatch(Keeper())
    authority.initialize_new_run(batch().run_id)
    attest_direct(authority, batch().run_id)
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
    attest_direct(authority, batch().run_id)
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
    attest_direct(authority, batch().run_id)
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
    attest_direct(authority, first.run_id)
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
    attest_direct(authority, first.run_id)
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
