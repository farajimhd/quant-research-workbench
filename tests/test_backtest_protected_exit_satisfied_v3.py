"""Actual OMS protected-exit completion through the closed V3 child."""
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_protected_exit_satisfied_v3 import (
    SATISFIED, project_protected_exit_satisfied_v3,
    recover_protected_exit_satisfied_payload, seal_protected_exit_satisfied_v3,
)
from src.trading_runtime.order_management import BrokerCommunicationPolicy, OrderManagementEngine
from src.trading_runtime.signals import StrategyIntent
from tests.test_arte_journal_writer import ATTEMPT, BATCH, RUN


AT = datetime(2026, 8, 18, 14, tzinfo=timezone.utc)


def _actual_record(reason="protected_target_filled_before_exit_reprice"):
    journal = BacktestMemoryJournal(run_id=RUN)
    manager = OrderManagementEngine.__new__(OrderManagementEngine)
    manager.journal = journal
    manager.run_id = RUN
    manager.strategy_id = "strategy-1"
    manager.strategy_revision = 7
    manager.policy = BrokerCommunicationPolicy()
    manager.enforce_wall_clock_quote_freshness = False
    manager.execution_market_data = type("Market", (), {"snapshot": lambda self, ticker: None})()
    manager._groups = {}
    intent = StrategyIntent("intent-1", "AAA", AT, "exit", 5.0, 10.0)
    snapshot = manager._satisfied_exit_snapshot(intent, account_id="DU1", reason=reason)
    assert snapshot.state.value == "filled"
    record, = journal.unfenced_records()
    return record


@pytest.mark.parametrize("reason", [
    "protected_target_filled_before_exit_reprice",
    "protected_target_filled_during_exit_reconciliation",
])
def test_actual_oms_emitter_roundtrip_and_exact_seal(reason):
    record = _actual_record(reason)
    assert dict(SATISFIED.columns)["requested_quantity"] == "Decimal(38, 18)"
    projected = project_protected_exit_satisfied_v3(
        record, attempt_id=ATTEMPT, batch_id=BATCH)
    assert projected.detail["requested_quantity"].endswith(".000000000000000000")
    assert projected.detail["reason_code"] == reason
    assert recover_protected_exit_satisfied_payload(
        projected.event, projected.detail) == record.payload
    assert seal_protected_exit_satisfied_v3(
        [projected.detail], [projected.event], run_id=RUN,
        batch_id=BATCH)["protected_exit_satisfied_count"] == 1


def test_source_drift_missing_duplicate_and_tamper_fail_closed():
    record = _actual_record()
    projected = project_protected_exit_satisfied_v3(
        record, attempt_id=ATTEMPT, batch_id=BATCH)
    for change in ({"reason": "unknown"}, {"unexpected": "evidence"},
                   {"action": "enter_long"}, {"requested_quantity": float("nan")},
                   {"order_group_id": "another"}):
        with pytest.raises(ValueError):
            project_protected_exit_satisfied_v3(
                replace(record, payload={**record.payload, **change}),
                attempt_id=ATTEMPT, batch_id=BATCH)
    for rows in ([], [projected.detail, projected.detail],
                 [{**projected.detail, "reason_code": "unknown"}],
                 [{**projected.detail, "requested_quantity": 6.0}]):
        with pytest.raises(ValueError):
            seal_protected_exit_satisfied_v3(
                rows, [projected.event], run_id=RUN, batch_id=BATCH)


def test_v3_writer_and_cold_reader_reject_tampered_child(monkeypatch):
    from src.backend.backtest_typed_projection import project_pending_backtest_v3_prefix
    from src.backend.backtest_squeeze_episode_v3 import load_verified_squeeze_v3_prefix
    import src.backend.backtest_squeeze_episode_v3 as cold_module
    from src.trading_runtime import arte_journal_writer as writer
    from tests.test_backtest_portfolio_control_v3 import _V3Client
    from tests.test_backtest_squeeze_episode_v3 import _FakeColdClient

    source = _actual_record()
    journal = BacktestMemoryJournal(run_id=RUN)
    journal.append(run_id=RUN, category=source.category,
                   entity_type=source.entity_type, entity_id=source.entity_id,
                   account_id=source.account_id, event_time=source.event_time,
                   payload=source.payload)
    unit, = project_pending_backtest_v3_prefix(
        journal, attempt_id=ATTEMPT, run_month=AT.date().replace(day=1),
        prior_sequence=0, expected_market_plan_token="a" * 64,
        expected_query_sha256="b" * 64)
    monkeypatch.setattr(writer, "_v3_preflight", lambda client: None)
    monkeypatch.setattr(writer, "_verify_run_identity",
                        lambda client, run: {"mode": "backtest"})
    client = _V3Client()
    writer.publish_typed_squeeze_batch_v3(client, unit)
    assert client.inserts == ["trading_event_v1", SATISFIED.name, "trading_commit_v3"]
    assert client.tables["trading_commit_v3"][0]["protected_exit_satisfied_count"] == 1

    monkeypatch.setattr(cold_module, "storage_preflight",
                        lambda client, *, tables: None)
    monkeypatch.setattr(cold_module, "_verify_recovery_chunk",
                        lambda client, commits, *, journal_profile: None)
    cold = _FakeColdClient(client.tables["trading_commit_v3"][0], [], [])
    cold.satisfied_events = client.tables["trading_event_v1"]
    cold.satisfied_rows = client.tables[SATISFIED.name]
    original = cold.satisfied_rows[0]
    assert load_verified_squeeze_v3_prefix(
        cold, RUN, expected_market_plan_token="a" * 64,
        expected_query_sha256="b" * 64) is not None
    cold.satisfied_rows = []
    with pytest.raises((RuntimeError, ValueError)):
        load_verified_squeeze_v3_prefix(
            cold, RUN, expected_market_plan_token="a" * 64,
            expected_query_sha256="b" * 64)
    cold.satisfied_rows = [{**original, "requested_quantity": 6.0}]
    with pytest.raises((RuntimeError, ValueError)):
        load_verified_squeeze_v3_prefix(
            cold, RUN, expected_market_plan_token="a" * 64,
            expected_query_sha256="b" * 64)
