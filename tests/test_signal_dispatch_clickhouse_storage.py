from __future__ import annotations

import json

import pytest

from src.backend.signal_dispatch_clickhouse_storage import (
    ClickHouseDispatchColdStorage,
)
from src.backend.signal_dispatch_typed_cursor import (
    ACK, ACK_COMMIT, INTENT, INTENT_COMMIT, read_committed_dispatch_prefix,
)
from src.backend.strategy_one_live_signal_schema import strategy_one_signal_table
from tests.test_signal_dispatch_registered_writer import _packets


def test_dispatch_cold_storage_projects_only_exact_registered_families() -> None:
    intents, acks = _packets()
    rows = {INTENT.name: intents["intents"],
            INTENT_COMMIT.name: [intents["commit"]],
            ACK.name: acks["acks"],
            ACK_COMMIT.name: [acks["commit"]]}
    class Client:
        def __init__(self):
            self.queries = []
        def execute(self, sql):
            self.queries.append(sql)
            table = next(name for name in rows if f"arte.{name} " in sql)
            if sql.startswith("SELECT 1 "):
                return "1\n" if rows[table] else ""
            return "\n".join(json.dumps(row) for row in rows[table])
    client = Client()
    storage = ClickHouseDispatchColdStorage(client)
    assert storage.read_dispatch_rows(
        INTENT.name, session_key="2026-09-24",
        source_batch_sequence=1) == intents["intents"]
    assert storage.list_dispatch_commits(
        ACK_COMMIT.name, session_key="2026-09-24") == [acks["commit"]]
    assert storage.has_any_dispatch_rows(session_key="2026-09-24")
    assert all("SELECT " in sql and "INSERT" not in sql
               for sql in client.queries)
    with pytest.raises(ValueError, match="not registered"):
        storage.read_dispatch_rows("trading_event_v1", session_key="2026-09-24",
                                   source_batch_sequence=1)
    with pytest.raises(ValueError, match="commit table"):
        storage.list_dispatch_commits(INTENT.name, session_key="2026-09-24")
    with pytest.raises(ValueError, match="session"):
        storage.has_any_dispatch_rows(session_key="2026-09-24' OR 1=1")
    with pytest.raises(ValueError, match="batch sequence"):
        storage.read_dispatch_rows(INTENT.name, session_key="2026-09-24",
                                   source_batch_sequence=0)
    rows[INTENT.name] = rows[INTENT.name] * 2
    with pytest.raises(RuntimeError, match="bounded family"):
        ClickHouseDispatchColdStorage(client, max_family_rows=1).read_dispatch_rows(
            INTENT.name, session_key="2026-09-24", source_batch_sequence=1)


def test_empty_dispatch_prefix_requires_registered_gate_and_empty_all_table_inventory() -> None:
    class Client:
        rows = False
        def execute(self, sql):
            assert sql.startswith("SELECT 1 FROM arte.signal_dispatch_")
            return "1\n" if self.rows else ""
    class Gate:
        def assert_cold_receipts(self, run_id, receipts):
            assert run_id and receipts == {}
    client = Client()
    storage = ClickHouseDispatchColdStorage(client)
    kwargs = dict(session_key="2026-09-24", source_commit_hashes=(),
                  configuration_revision_id="approved-revision-1")
    with pytest.raises(ValueError, match="registered empty inventory"):
        read_committed_dispatch_prefix(storage, **kwargs)
    assert read_committed_dispatch_prefix(
        storage, registered_dispatch=Gate(), **kwargs) == ()
    client.rows = True
    with pytest.raises(ValueError, match="uncommitted rows"):
        read_committed_dispatch_prefix(
            storage, registered_dispatch=Gate(), **kwargs)


def test_strategy_one_cold_reader_uses_only_isolated_dispatch_tables() -> None:
    class Client:
        def __init__(self):
            self.sql = []

        def execute(self, sql):
            self.sql.append(sql)
            return ""

    client = Client()
    storage = ClickHouseDispatchColdStorage(client, strategy_one=True)
    assert not storage.has_any_dispatch_rows(session_key="2026-09-24")
    assert len(client.sql) == 4
    assert all("FROM arte.trading_strategy_one_signal_dispatch_" in sql
               for sql in client.sql)
    assert any(strategy_one_signal_table(ACK_COMMIT.name) in sql
               for sql in client.sql)
