from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.backend.backtest_protection_change_v3 import (
    CHANGE, ENTRY_ORDER, project_protection_change_v3,
    recover_protection_change_payload, seal_protection_changes_v3,
)
from src.trading_runtime.journal_contract import JournalRecord


def _record(*, entry_order_ids=None, price=5.75) -> JournalRecord:
    at = datetime(2025, 8, 18, 12, 0, tzinfo=timezone.utc)
    return JournalRecord(
        str(uuid4()), "run-1", 7, at, at, "protection", "protection_change",
        "broker-1", "account-1", {
            "schema_version": 1, "order_group_id": "group-1",
            "entry_order_ids": ["entry-1", "entry-2"] if entry_order_ids is None
            else entry_order_ids,
            "order_id": "broker-1", "client_order_id": "client-1",
            "kind": "stop", "phase": "effective", "price": price,
            "active": True, "ticker": "ABC", "source_intent_id": "intent-1",
            "strategy_id": "strategy-1", "strategy_revision": 7,
            "action": "enter_long", "intent_id": "intent-1",
            "correlation_id": "correlation-1", "causation_id": "causation-1",
        },
    )


def test_protection_change_is_normalized_and_exactly_recoverable():
    record = _record()
    batch = str(uuid4())
    result = project_protection_change_v3(record, attempt_id=str(uuid4()), batch_id=batch)
    assert set(result.detail) == {name for name, _ in CHANGE.columns}
    assert all(set(row) == {name for name, _ in ENTRY_ORDER.columns}
               for row in result.entry_orders)
    assert result.detail["price"] == "5.750000000000000000"
    assert [row["entry_order_id"] for row in result.entry_orders] == ["entry-1", "entry-2"]
    assert recover_protection_change_payload(
        result.event, result.detail, result.entry_orders) == record.payload
    assert seal_protection_changes_v3(
        [result.detail], result.entry_orders, [result.event],
        run_id=record.run_id, batch_id=batch)["protection_change_count"] == 1


def test_protection_change_rejects_missing_or_tampered_children():
    record = _record()
    batch = str(uuid4())
    result = project_protection_change_v3(record, attempt_id=str(uuid4()), batch_id=batch)
    for children in (
        result.entry_orders[:-1],
        tuple(reversed(result.entry_orders)),
        ({**result.entry_orders[0], "entry_order_id": "different"},
         result.entry_orders[1]),
    ):
        with pytest.raises(ValueError):
            seal_protection_changes_v3(
                [result.detail], children, [result.event],
                run_id=record.run_id, batch_id=batch)
    with pytest.raises(ValueError, match="content"):
        seal_protection_changes_v3(
            [{**result.detail, "price": "6.000000000000000000"}],
            result.entry_orders, [result.event], run_id=record.run_id, batch_id=batch)


def test_protection_change_rejects_unmodeled_or_lossy_source():
    record = _record()
    for payload in (
        {**record.payload, "unknown": 1},
        {**record.payload, "entry_order_ids": ["entry-2", "entry-1"]},
        {**record.payload, "price": 1e-19},
        {**record.payload, "active": 1},
    ):
        with pytest.raises(ValueError):
            project_protection_change_v3(
                replace(record, payload=payload), attempt_id=str(uuid4()),
                batch_id=str(uuid4()))
    empty = _record(entry_order_ids=[])
    result = project_protection_change_v3(
        empty, attempt_id=str(uuid4()), batch_id=str(uuid4()))
    assert result.entry_orders == ()
    assert recover_protection_change_payload(result.event, result.detail, ()) == empty.payload


def test_actual_oms_protection_emitter_roundtrips():
    from src.backend.backtest_journal_memory import BacktestMemoryJournal
    from src.trading_runtime.ibkr_schema import OrderRequest
    from src.trading_runtime.order_management import OrderManagementEngine
    from src.trading_runtime.signals import StrategyIntent

    at = datetime(2025, 8, 18, 12, 0, tzinfo=timezone.utc)
    journal = BacktestMemoryJournal(run_id="run-1")
    manager = OrderManagementEngine.__new__(OrderManagementEngine)
    manager.journal = journal
    manager.run_id = "run-1"
    manager.strategy_id = "strategy-1"
    manager.strategy_revision = 7
    intent = StrategyIntent("intent-1", "ABC", at, "enter_long", 5.0, 5.75)
    entry = OrderRequest("account-1", 1, "LMT", "BUY", quantity=5.0,
                         cOID="entry-1", price=5.75)
    stop = OrderRequest("account-1", 1, "STP", "SELL", quantity=5.0,
                        cOID="stop-1", parentId="entry-1", auxPrice=5.5)
    group = SimpleNamespace(group_id="group-1", account_id="account-1",
                            intent=intent, orders=(entry, stop), broker_order_roles={})
    manager._groups = {group.group_id: group}
    manager._record_protection(group, stop, phase="requested", event_time=at)
    record, = journal.unfenced_records()
    projected = project_protection_change_v3(
        record, attempt_id=str(uuid4()), batch_id=str(uuid4()))
    assert projected.detail["strategy_id"] == "strategy-1"
    assert projected.detail["action"] == "enter_long"
    assert projected.entry_orders[0]["entry_order_id"] == "entry-1"
    assert recover_protection_change_payload(
        projected.event, projected.detail, projected.entry_orders) == record.payload


def test_v3_writer_coalescing_and_cold_reader_verify_protection(monkeypatch):
    from src.backend.backtest_journal_memory import BacktestMemoryJournal
    from src.backend.backtest_squeeze_episode_v3 import (
        coalesce_squeeze_units_v3, load_verified_squeeze_v3_prefix,
    )
    from src.backend.backtest_typed_projection import project_pending_backtest_v3_prefix
    import src.backend.backtest_squeeze_episode_v3 as cold_module
    from src.trading_runtime import arte_journal_writer as writer
    from tests.test_backtest_portfolio_control_v3 import _V3Client
    from tests.test_backtest_squeeze_episode_v3 import _FakeColdClient
    from tests.test_arte_journal_writer import ATTEMPT, RUN

    journal = BacktestMemoryJournal(run_id=RUN)
    source = _record()
    for _ in range(2):
        journal.append(run_id=RUN, category=source.category,
                       entity_type=source.entity_type, entity_id=source.entity_id,
                       account_id=source.account_id, event_time=source.event_time,
                       payload=source.payload)
    units = project_pending_backtest_v3_prefix(
        journal, attempt_id=ATTEMPT, run_month=source.event_time.date().replace(day=1),
        prior_sequence=0, expected_market_plan_token="a" * 64,
        expected_query_sha256="b" * 64)
    merged = coalesce_squeeze_units_v3(units)
    assert len(merged.protection_changes) == 2
    assert len(merged.protection_entry_orders) == 4
    monkeypatch.setattr(writer, "_v3_preflight", lambda client: None)
    monkeypatch.setattr(writer, "_verify_run_identity",
                        lambda client, run: {"mode": "backtest"})
    client = _V3Client()
    writer.publish_typed_squeeze_batch_v3(client, merged)
    assert client.inserts == ["trading_event_v1", CHANGE.name,
                              ENTRY_ORDER.name, "trading_commit_v3"]
    assert client.tables["trading_commit_v3"][0]["protection_change_count"] == 2

    monkeypatch.setattr(cold_module, "storage_preflight", lambda client, *, tables: None)
    monkeypatch.setattr(cold_module, "_verify_recovery_chunk",
                        lambda client, commits, *, journal_profile: None)
    cold = _FakeColdClient(client.tables["trading_commit_v3"][0], [], [])
    cold.protection_events = client.tables["trading_event_v1"]
    cold.protection_rows = client.tables[CHANGE.name]
    cold.protection_entry_rows = client.tables[ENTRY_ORDER.name]
    assert load_verified_squeeze_v3_prefix(
        cold, RUN, expected_market_plan_token="a" * 64,
        expected_query_sha256="b" * 64) is not None
    cold.protection_entry_rows = cold.protection_entry_rows[:-1]
    with pytest.raises((ValueError, RuntimeError)):
        load_verified_squeeze_v3_prefix(
            cold, RUN, expected_market_plan_token="a" * 64,
            expected_query_sha256="b" * 64)
