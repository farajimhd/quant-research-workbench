"""Campaign checkpoint keeps exact normalized owner authority."""
from dataclasses import replace
from datetime import date
import json
from types import SimpleNamespace

import pytest

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.trading_runtime.strategy_one_campaign_snapshot import (
    TABLES, CampaignSnapshotHead, ManagedCampaignSnapshotHeadReader,
    load_attested_campaign_snapshot, load_campaign_snapshot, publish_campaign_snapshot,
    project_campaign_snapshot, verify_campaign_snapshot,
)


RUN = "backtest:strategy-one"
BATCH = "00000000-0000-0000-0000-000000000042"
DAY = date(2026, 8, 18)


def project(ownership=()):
    return project_campaign_snapshot(
        run_id=RUN, session_date=DAY, checkpoint_sequence=42,
        boundary_ms=1_200_000, journal_batch_id=BATCH,
        ownership=ownership)


def test_empty_and_owned_checkpoints_are_stable_and_normalized():
    empty = project()
    assert empty.snapshot["owner_count"] == 0
    assert empty.owners == ()
    assert verify_campaign_snapshot(empty) is empty
    owners = (
        {"resource_id": "book:AAA", "session_key": "2026-08-18",
         "owner_id": "campaign-a", "state": "confirmed", "epoch": 2,
         "updated_at": "2026-09-28T12:00:00+00:00"},
        {"resource_id": "book:BBB", "session_key": "2026-08-18",
         "owner_id": "campaign-b", "state": "reserved", "epoch": 1},
    )
    rows = project(owners)
    assert rows == project(tuple(reversed(owners)))
    assert rows.snapshot["owner_count"] == 2
    assert all("updated_at" not in row for row in rows.owners)
    assert verify_campaign_snapshot(rows) is rows
    assert all("live_market_ssd" in table.ddl() for table in TABLES)
    assert all("json" not in name and "blob" not in name
               for table in TABLES for name, _ in table.columns)


def test_changed_or_missing_owner_fails_seal():
    rows = project((
        {"resource_id": "book:AAA", "session_key": "2026-08-18",
         "owner_id": "campaign-a", "state": "confirmed", "epoch": 2},
    ))
    with pytest.raises(RuntimeError, match="committed seal"):
        verify_campaign_snapshot(replace(rows, owners=()))
    with pytest.raises(RuntimeError, match="committed seal"):
        verify_campaign_snapshot(replace(rows, owners=(
            {**rows.owners[0], "owner_id": "campaign-b"},)))
    with pytest.raises(ValueError, match="duplicate owner"):
        project((rows.owners[0], rows.owners[0]))


def test_memory_capture_contains_only_active_owner_scalars():
    journal = BacktestMemoryJournal(run_id=RUN)
    journal.acquire_campaign_session_ownership(
        "book:AAA", session_key="2026-08-18", owner_id="campaign-a",
        state="reserved")
    journal.acquire_campaign_session_ownership(
        "book:BBB", session_key="2026-08-18", owner_id="campaign-b",
        state="reserved")
    journal.acquire_campaign_session_ownership(
        "book:AAA", session_key="2026-08-18", owner_id="campaign-a",
        state="confirmed")
    journal.release_campaign_session_reservation(
        "book:BBB", session_key="2026-08-18", owner_id="campaign-b")
    captured = journal.campaign_ownership_snapshot()
    assert captured == ({
        "resource_id": "book:AAA", "session_key": "2026-08-18",
        "owner_id": "campaign-a", "state": "confirmed", "epoch": 2,
    },)
    assert verify_campaign_snapshot(project(captured)).snapshot["owner_count"] == 1


def test_select_only_recovery_requires_exact_cursor_and_child_seal(monkeypatch):
    from src.trading_runtime import arte_journal_commit_v4, arte_journal_projection

    rows = project((
        {"resource_id": "book:AAA", "session_key": "2026-08-18",
         "owner_id": "campaign-a", "state": "confirmed", "epoch": 2},
    ))

    class Reader:
        def __init__(self):
            self.owners = rows.owners
            self.queries = []

        def execute(self, sql):
            assert sql.startswith("SELECT ")
            self.queries.append(sql)
            if "campaign_snapshot_v1" in sql:
                return json.dumps(rows.snapshot)
            assert "campaign_owner_v1" in sql and "LIMIT 2" in sql
            return "\n".join(json.dumps(row) for row in self.owners)

    reader = Reader()
    prefix = SimpleNamespace(status="running", last_sequence=42,
                             last_batch_id=BATCH)
    selected = CampaignSnapshotHead(
        RUN, 42, BATCH, rows.snapshot["content_hash"], 0)
    keeper = SimpleNamespace(read_head=lambda **_kwargs: selected)
    cursor = dict(run_id=RUN, event_sequence=42, batch_id=BATCH,
                  session_date=DAY.isoformat(), boundary_ms=1_200_000)
    monkeypatch.setattr(arte_journal_commit_v4, "load_verified_v4_prefix",
                        lambda _client, _run_id: prefix)
    monkeypatch.setattr(arte_journal_projection, "load_latest_backtest_cursor",
                        lambda _client, _prefix: cursor)
    assert load_attested_campaign_snapshot(
        reader, keeper, run_id=RUN, checkpoint_sequence=42) == rows
    assert len(reader.queries) == 2
    reader.owners = ()
    with pytest.raises(RuntimeError, match="committed seal"):
        load_campaign_snapshot(reader, run_id=RUN, checkpoint_sequence=42)
    reader.owners = rows.owners
    cursor["boundary_ms"] += 100
    with pytest.raises(RuntimeError, match="committed market cursor"):
        load_attested_campaign_snapshot(reader, run_id=RUN,
                                        checkpoint_sequence=42, keeper=keeper)


def test_campaign_publication_is_children_first_and_keeper_selected(monkeypatch):
    from src.trading_runtime import arte_journal_commit_v4, arte_journal_projection
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
    from src.trading_runtime.arte_typed_insert_dispatch import (
        TypedInsertDispatch, _Gate, _context_receipt_path, _gate_path,
    )
    from src.trading_runtime.keeper_session import ManagedKeeperSession
    from tests.test_arte_typed_insert_dispatch import Keeper, Stat

    rows = project((
        {"resource_id": "book:AAA", "session_key": "2026-08-18",
         "owner_id": "campaign-a", "state": "confirmed", "epoch": 2},
    ))
    prefix = V4CommittedPrefix(RUN, 42, BATCH, "2026-08-18:1200000",
                               "running", (BATCH,))
    monkeypatch.setattr(arte_journal_commit_v4, "load_verified_v4_prefix",
                        lambda _client, _run: prefix)
    monkeypatch.setattr(arte_journal_projection, "load_latest_backtest_cursor",
                        lambda _client, _prefix: {
                            "run_id": RUN, "event_sequence": 42,
                            "batch_id": BATCH, "boundary_ms": 1_200_000,
                            "session_date": DAY.isoformat()})
    keeper = Keeper()
    keeper.add_listener = lambda _listener: None
    keeper.connected = True
    keeper.client_id = (101, b"secret")
    keeper.exists = lambda path: keeper.rows.get(path)
    session = ManagedKeeperSession(keeper)
    session._on_state("CONNECTED")
    dispatch = TypedInsertDispatch(keeper)
    dispatch.initialize_new_run(RUN)
    keeper.create(_context_receipt_path(RUN), b"1\n" + b"a" * 64)
    gate, version = dispatch._read_gate(RUN)
    keeper.rows[_gate_path(RUN)] = (
        _Gate("open", 0, gate.epoch, 0, 42, BATCH,
              "a" * 64, "00000000-0000-0000-0000-000000000000").wire(),
        Stat(version + 1))

    class Client:
        typed_insert_strict = True
        typed_insert_dispatch = dispatch

        def __init__(self):
            self.tables = {}
            self.inserts = []

        def execute(self, sql, *, query_id=None):
            table = sql.split("arte.", 1)[1].split(" ", 1)[0]
            if sql.startswith("INSERT INTO "):
                self.inserts.append(table)
                self.tables.setdefault(table, []).extend(
                    json.loads(line) for line in sql.split("\n", 1)[1].splitlines())
                return ""
            assert sql.startswith("SELECT ")
            return "\n".join(json.dumps(row) for row in self.tables.get(table, ()))

    client = Client()
    head = publish_campaign_snapshot(client, session, rows)
    assert head == CampaignSnapshotHead(RUN, 42, BATCH,
                                        rows.snapshot["content_hash"], 0)
    assert client.inserts == [TABLES[1].name, TABLES[0].name]
    assert dispatch._read_gate(RUN)[0].registered == 0
    assert ManagedCampaignSnapshotHeadReader(session).read_head(run_id=RUN) == head
    assert publish_campaign_snapshot(client, session, rows) == head
    assert len(client.inserts) == 2
