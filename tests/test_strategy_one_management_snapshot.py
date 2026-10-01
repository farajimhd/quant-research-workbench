"""Manager recovery is scalar, sealed, and lossless across all owned families."""
from dataclasses import replace
from datetime import date
import json

import pytest

from src.backend.backtest_strategy_one_management import (
    StrategyOneClosedPosition, StrategyOneManagementState,
)
from src.trading_runtime import strategy_one_management_snapshot as subject
from src.trading_runtime.strategy_one_management_snapshot import (
    TABLES, ManagerSnapshotHead, ManagedManagerSnapshotHeadReader,
    project_manager_snapshot, publish_manager_snapshot,
    restore_manager_snapshot, load_attested_manager_snapshot,
    load_unattested_manager_snapshot_rows,
)
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
from src.trading_runtime.keeper_session import ManagedKeeperSession
from tests.test_live_signal_completion_keeper import FakeKazoo
from src.trading_runtime.strategy_one_position import ProtectionState, ResistanceBreak
from src.trading_runtime.strategy_one_stateful import StrategyOneEntryProposal


KEY = ("DU1", "A1", "AAA")


def _rows():
    proposal = StrategyOneEntryProposal(
        "A1", "DU1", "AAA", 30_100, 30_000, 10.01, 9.69,
        10.3, "R3", .5, 30_000, "S1")
    state = StrategyOneManagementState(
        31_000, ((KEY, proposal),),
        ((KEY, ProtectionState(30_100, 9.69, 10.3)),),
        ((KEY, (ResistanceBreak(31_000, {
            "unified_level_id": "B1", "lower": 9.79, "upper": 9.81,
            "role": "resistance", "side": "resistance"}),)),),
        ((KEY, 101_000),),
        ((('DU1', 'A2', 'BBB'), StrategyOneClosedPosition(
            30_000, "R0", 98_000)),))
    return project_manager_snapshot(
        run_id="backtest:one", session_date=date(2026, 8, 18),
        checkpoint_sequence=42, state=state)


def test_manager_snapshot_roundtrips_all_owned_state_without_json_columns():
    rows = _rows()
    state = restore_manager_snapshot(rows)
    assert state.submitted[0][1].target_level_id == "R3"
    assert state.positions[0][1].stop == 9.69
    assert state.pending_breaks[0][1][0].level["unified_level_id"] == "B1"
    assert state.position_highs == ((KEY, 101_000),)
    assert state.closed_positions[0][1].entry_resistance_id == "R0"
    assert rows.snapshot["source_count"] == 1
    assert rows.snapshot["pending_break_count"] == 1
    assert rows.snapshot["position_high_count"] == 1
    assert rows.snapshot["closed_position_count"] == 1
    assert rows.snapshot["protection_hash"] == rows.protection.snapshot["content_hash"]
    assert all("live_market_ssd" in table.ddl() for table in TABLES)
    assert all("json" not in name and "blob" not in name
               for table in TABLES for name, _ in table.columns)


def test_nineteen_scalar_roundtrip_preserves_reference_without_claiming_source_authority():
    from tests.test_strategy_nineteen_entry_authority import graph
    proposal, _, _, _, _, _ = graph()
    key = (proposal.account_id, proposal.assignment_id, proposal.ticker)
    state = StrategyOneManagementState(proposal.boundary_ms, ((key, proposal),), (), ())
    rows = project_manager_snapshot(run_id='nineteen-reference', session_date=date(2026, 8, 18),
        checkpoint_sequence=1, state=state)
    reference = restore_manager_snapshot(rows)
    assert reference.submitted[0][1].momentum is None
    assert reference.submitted[0][1].initial_momentum is None
    assert replace(proposal, momentum=None, initial_momentum=None) == reference.submitted[0][1]
    with pytest.raises(ValueError):
        project_manager_snapshot(run_id='nineteen-reference', session_date=date(2026, 8, 18),
            checkpoint_sequence=1, state=reference)


def test_manager_snapshot_rejects_missing_or_modified_children():
    rows = _rows()
    with pytest.raises(ValueError, match="seal differs"):
        restore_manager_snapshot(replace(rows, sources=()))
    with pytest.raises(ValueError, match="break children differ"):
        restore_manager_snapshot(replace(
            rows, pending_breaks=({**rows.pending_breaks[0], "lower": "1.0"},)))
    with pytest.raises(ValueError, match="seal differs"):
        restore_manager_snapshot(replace(
            rows, protection=replace(rows.protection, snapshot={
                **rows.protection.snapshot, "content_hash": "0" * 64})))


def test_manager_recovery_canonicalizes_clickhouse_decimal_text():
    rows = _rows()
    source = {**rows.sources[0],
              "reference_ask": "10.01", "initial_stop": "9.69",
              "initial_target": "10.3", "frozen_gap": "0.5"}
    position = {**rows.protection.states[0],
                "stop": "9.69", "target": "10.3"}
    stored = replace(
        rows, sources=(source,),
        protection=replace(rows.protection, states=(position,)))
    assert restore_manager_snapshot(stored) == restore_manager_snapshot(rows)
    with pytest.raises(ValueError, match="source children differ"):
        restore_manager_snapshot(replace(stored, sources=(
            {**source, "reference_ask": "10.02"},)))


def test_empty_manager_snapshot_seals_no_source_or_position():
    rows = project_manager_snapshot(
        run_id="backtest:one", session_date=date(2026, 8, 18),
        checkpoint_sequence=42,
        state=StrategyOneManagementState(31_000, (), (), ()))
    assert restore_manager_snapshot(rows) == StrategyOneManagementState(
        31_000, (), (), ())


def test_cold_loader_requires_same_keeper_head_and_verified_journal_cursor(monkeypatch):
    rows = _rows()
    run = rows.snapshot["run_id"]
    batch = "00000000-0000-0000-0000-000000000042"
    prefix = V4CommittedPrefix(run, 42, batch, "2026-08-18:31000",
                               "running", (batch,))
    from src.trading_runtime import arte_journal_commit_v4, arte_journal_projection
    monkeypatch.setattr(arte_journal_commit_v4, "load_verified_v4_prefix",
                        lambda _client, _run: prefix)
    monkeypatch.setattr(arte_journal_projection, "load_latest_backtest_cursor",
                        lambda _client, _prefix: {
                            "run_id": run, "event_sequence": 42,
                            "batch_id": batch, "boundary_ms": 31_000,
                            "session_date": "2026-08-18"})
    monkeypatch.setattr(subject, "load_protection_snapshot_rows",
                        lambda _client, **_kwargs: rows.protection)

    class Client:
        def __init__(self):
            self.queries = []

        def execute(self, sql):
            self.queries.append(sql)
            selected = (() if "manager_snapshot_v3" in sql else
                        (rows.snapshot,) if "manager_snapshot_v2" in sql else
                        rows.sources if "manager_source_v2" in sql else
                        rows.position_highs if "manager_position_high_v2" in sql else
                        rows.closed_positions if "manager_closed_position_v2" in sql else
                        rows.pending_breaks)
            return "\n".join(json.dumps(row) for row in selected)

    class Keeper:
        def __init__(self):
            self.head = ManagerSnapshotHead(
                run, 42, batch, rows.snapshot["content_hash"], 0)

        def read_head(self, *, run_id):
            assert run_id == run
            return self.head

    client, keeper = Client(), Keeper()
    assert load_attested_manager_snapshot(
        client, keeper, run_id=run, checkpoint_sequence=42
    ) == restore_manager_snapshot(rows)
    assert len(client.queries) == 6
    assert all(query.startswith("SELECT ") and "INSERT" not in query
               for query in client.queries)
    historical = Client()
    assert restore_manager_snapshot(load_unattested_manager_snapshot_rows(
        historical, run_id=run, checkpoint_sequence=42)) == restore_manager_snapshot(rows)
    assert len(historical.queries) == 6
    keeper.head = replace(keeper.head, snapshot_hash="0" * 64)
    with pytest.raises(RuntimeError, match="selected cursor"):
        load_attested_manager_snapshot(
            client, keeper, run_id=run, checkpoint_sequence=42)


def test_managed_keeper_head_is_exact_and_loses_authority_on_disconnect():
    rows = _rows()
    client = FakeKazoo()
    client.add_listener = lambda _listener: None
    session = ManagedKeeperSession(client)
    session._on_state("CONNECTED")
    reader = ManagedManagerSnapshotHeadReader(session)
    path = reader.path(rows.snapshot["run_id"])
    with pytest.raises(ValueError, match="missing or corrupt"):
        reader.read_head(run_id=rows.snapshot["run_id"])
    client.ensure_path(path.rsplit("/", 1)[0])
    batch = "00000000-0000-0000-0000-000000000042"
    client.create(path, (f"1\n{rows.snapshot['run_id']}\n42\n{batch}\n"
                         f"{rows.snapshot['content_hash']}").encode())
    assert reader.read_head(run_id=rows.snapshot["run_id"]) == ManagerSnapshotHead(
        rows.snapshot["run_id"], 42, batch,
        rows.snapshot["content_hash"], 0)
    session._on_state("SUSPENDED")
    with pytest.raises(RuntimeError, match="unavailable"):
        reader.read_head(run_id=rows.snapshot["run_id"])


@pytest.mark.parametrize("strategy_nine", [False, True])
def test_manager_publication_is_rows_first_then_keeper_selected(monkeypatch, strategy_nine):
    from src.trading_runtime.arte_typed_insert_dispatch import (
        TypedInsertDispatch, _Gate, _gate_path, _context_receipt_path,
    )
    from tests.test_arte_typed_insert_dispatch import Keeper, Stat
    from src.trading_runtime import arte_journal_commit_v4, arte_journal_projection

    rows = _nine_rows() if strategy_nine else _rows()
    run, sequence = rows.snapshot["run_id"], rows.snapshot["checkpoint_sequence"]
    batch = "00000000-0000-0000-0000-000000000042"
    prefix = V4CommittedPrefix(run, sequence, batch, "2026-08-18:31000",
                               "running", (batch,))
    monkeypatch.setattr(arte_journal_commit_v4, "load_verified_v4_prefix",
                        lambda _client, _run: prefix)
    monkeypatch.setattr(arte_journal_projection, "load_latest_backtest_cursor",
                        lambda _client, _prefix: {
                            "run_id": run, "event_sequence": sequence,
                            "batch_id": batch, "boundary_ms": 31_000,
                            "session_date": "2026-08-18"})
    keeper = Keeper()
    keeper.add_listener = lambda _listener: None
    keeper.connected = True
    keeper.client_id = (101, b"secret")
    keeper.exists = lambda path: keeper.rows.get(path)
    session = ManagedKeeperSession(keeper)
    session._on_state("CONNECTED")
    dispatch = TypedInsertDispatch(keeper)
    dispatch.initialize_new_run(run)
    keeper.create(_context_receipt_path(run), b"1\n" + b"a" * 64)
    gate, version = dispatch._read_gate(run)
    keeper.rows[_gate_path(run)] = (
        _Gate("open", 0, gate.epoch, 0, sequence, batch,
              "a" * 64, "00000000-0000-0000-0000-000000000000").wire(),
        Stat(version + 1))

    class Client:
        typed_insert_strict = True
        typed_insert_dispatch = dispatch

        def __init__(self):
            self.tables = {}
            self.manager_keeper_session = session

        def close(self):
            return None

        def execute(self, sql, *, query_id=None):
            table = sql.split("arte.", 1)[1].split(" ", 1)[0]
            if sql.startswith("INSERT INTO "):
                self.tables.setdefault(table, []).extend(
                    json.loads(line) for line in sql.split("\n", 1)[1].splitlines())
                return ""
            assert sql.startswith("SELECT ")
            return "\n".join(json.dumps(row) for row in self.tables.get(table, ()))

    client = Client()
    head = publish_manager_snapshot(client, session, rows,
                                    journal_batch_id=batch)
    assert head == ManagerSnapshotHead(
        run, sequence, batch, rows.snapshot["content_hash"], 0)
    assert dispatch._read_gate(run)[0].registered == 0
    assert set(client.tables) == {
        "trading_strategy_one_protection_snapshot_v1",
        "trading_strategy_one_protection_state_v1",
        subject.PARENT_V3.name if strategy_nine else subject.PARENT.name,
        subject.SOURCE.name, subject.BREAK.name,
        subject.HIGH.name, subject.CLOSED.name,
    } | ({subject.FIRST_HELD.name} if strategy_nine else set())
    assert publish_manager_snapshot(client, session, rows,
                                    journal_batch_id=batch) == head
    assert all(len(stored) == 1 for stored in client.tables.values())

    from src.trading_runtime import arte_journal_writer as writer_module
    monkeypatch.setattr(writer_module, "_v4_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "_verify_run_identity",
                        lambda _client, _run: {
                            "mode": "backtest", "account_ids": ("DU1",)})
    journal = writer_module.ArteJournalWriter(
        client, run_id=run, journal_profile="backtest_v4",
        coalesce_batches=False)
    try:
        journal._last_commit_id = batch  # The fixture's compacted predecessor.
        assert journal.submit_manager_snapshot(
            session_date=date(2026, 8, 18),
            checkpoint_sequence=sequence, journal_batch_id=batch,
            state=restore_manager_snapshot(rows)).result(timeout=5) == batch
        assert journal.metrics()["publish_by_unit"]["_ManagerSnapshotUnit"]["units"] == 1
    finally:
        journal.close()


def _nine_rows(number=9):
    inherited = restore_manager_snapshot(_rows())
    state = replace(inherited,
        submitted=((KEY, replace(inherited.submitted[0][1], strategy_number=number)),),
        first_held_boundaries=((KEY, 30_200),))
    return project_manager_snapshot(run_id="backtest:nine", session_date=date(2026, 8, 18),
        checkpoint_sequence=42, state=state)


@pytest.mark.parametrize('number', (9, 10, 11))
def test_ninth_manager_persists_first_held_in_versioned_scalar_family(number):
    rows = _nine_rows(number)
    assert set(rows.snapshot) == {name for name, _ in subject.PARENT_V3.columns}
    assert "first_held_count" not in _rows().snapshot
    assert rows.snapshot['first_held_count'] == 1
    assert rows.first_held_boundaries[0]['first_held_boundary_ms'] == 30_200
    restored = restore_manager_snapshot(rows)
    assert restored.first_held_boundaries == ((KEY, 30_200),)
    assert restored.submitted[0][1].strategy_number == number
    assert "live_market_ssd" in subject.PARENT_V3.ddl()
    assert "live_market_ssd" in subject.FIRST_HELD.ddl()


def test_ninth_manager_rejects_missing_duplicate_and_changed_first_held():
    rows = _nine_rows()
    with pytest.raises(ValueError, match="seal differs"):
        restore_manager_snapshot(replace(rows, first_held_boundaries=()))
    with pytest.raises(ValueError, match="seal differs"):
        restore_manager_snapshot(replace(rows, first_held_boundaries=rows.first_held_boundaries * 2))
    with pytest.raises(ValueError, match="first held children differ"):
        restore_manager_snapshot(replace(rows, first_held_boundaries=(
            {**rows.first_held_boundaries[0], "first_held_boundary_ms": 30_300},)))


@pytest.mark.parametrize('boundary', [30_000, 31_100, 30_201])
def test_ninth_manager_rejects_resealed_noncausal_first_held(boundary):
    rows = _nine_rows()
    child = {k: v for k, v in rows.first_held_boundaries[0].items() if k != 'content_hash'}
    child['first_held_boundary_ms'] = boundary
    child['content_hash'] = subject._digest(child)
    seal = {k: v for k, v in rows.snapshot.items() if k != 'content_hash'}
    seal['first_held_hash'] = subject._digest([child['content_hash']])
    seal['content_hash'] = subject._digest(seal)
    with pytest.raises(ValueError, match='first held boundary is not causal'):
        restore_manager_snapshot(replace(rows, snapshot=seal, first_held_boundaries=(child,)))


def test_ninth_manager_cold_load_reads_exact_v3_family_and_rejects_two_seals(monkeypatch):
    rows = _nine_rows()
    monkeypatch.setattr(subject, 'load_protection_snapshot_rows', lambda *_a, **_k: rows.protection)
    class Client:
        duplicate = False
        def execute(self, sql):
            table = sql.split('arte.', 1)[1].split(' ', 1)[0]
            selected = {
                subject.PARENT.name: (rows.snapshot,) if self.duplicate else (),
                subject.PARENT_V3.name: (rows.snapshot,), subject.SOURCE.name: rows.sources,
                subject.BREAK.name: rows.pending_breaks, subject.HIGH.name: rows.position_highs,
                subject.CLOSED.name: rows.closed_positions,
                subject.FIRST_HELD.name: rows.first_held_boundaries,
            }[table]
            return '\n'.join(json.dumps(row) for row in selected)
    client = Client()
    loaded = load_unattested_manager_snapshot_rows(client, run_id='backtest:nine', checkpoint_sequence=42)
    assert restore_manager_snapshot(loaded) == restore_manager_snapshot(rows)
    client.duplicate = True
    with pytest.raises(RuntimeError, match='exactly one selected seal'):
        load_unattested_manager_snapshot_rows(client, run_id='backtest:nine', checkpoint_sequence=42)
