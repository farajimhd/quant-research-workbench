"""Actual OMS protected-exit broker-position reconciliation to V3 scalars."""
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_protected_exit_snapshot_v3 import (
    SNAPSHOT, project_protected_exit_snapshot_v3, recover_protected_exit_snapshot_payload,
    seal_protected_exit_snapshot_v3,
)
from src.trading_runtime.order_management import OrderManagementEngine
from src.trading_runtime.signals import StrategyIntent
from tests.test_arte_journal_writer import ATTEMPT, BATCH, RUN


AT = datetime(2026, 8, 18, 14, tzinfo=timezone.utc)


def _actual_record(*, signed=3.0, attempt=1, side="long"):
    journal = BacktestMemoryJournal(run_id=RUN)
    manager = OrderManagementEngine.__new__(OrderManagementEngine)
    manager.journal = journal
    manager.run_id = RUN
    manager.strategy_id = "strategy-1"
    manager.strategy_revision = 7
    manager._groups = {}

    async def positions(account_id):
        assert account_id == "DU1"
        return [SimpleNamespace(contractDesc="AAA", position=signed)]

    manager.broker = SimpleNamespace(positions=positions)
    intent = StrategyIntent("intent-1", "AAA", AT, "exit", 5.0, 10.0,
                            metadata={"position_side": side})
    recovered = asyncio.run(manager._reconcile_stale_protected_exit(
        intent, account_id="DU1", attempt=attempt))
    record, = journal.unfenced_records()
    return record, recovered


@pytest.mark.parametrize("signed,attempt,side,expected", [
    (3.0, 1, "long", 3.0),
    (0.0, 3, "long", None),
    (-2.0, 2, "short", 2.0),
])
def test_real_oms_emitter_exact_roundtrip_and_seal(signed, attempt, side, expected):
    record, recovered = _actual_record(signed=signed, attempt=attempt, side=side)
    assert (recovered.quantity if recovered else None) == expected
    projected = project_protected_exit_snapshot_v3(
        record, attempt_id=ATTEMPT, batch_id=BATCH)
    assert recover_protected_exit_snapshot_payload(
        projected.event, projected.detail) == record.payload
    assert seal_protected_exit_snapshot_v3(
        [projected.detail], [projected.event], run_id=RUN,
        batch_id=BATCH)["protected_exit_snapshot_count"] == 1


def test_snapshot_source_drift_missing_duplicate_and_corrupt_child():
    record, _ = _actual_record()
    projected = project_protected_exit_snapshot_v3(
        record, attempt_id=ATTEMPT, batch_id=BATCH)
    for change in ({"reason": "new"}, {"unexpected": "evidence"},
                   {"attempt": 4}, {"requested_quantity": float("nan")},
                   {"executable_remaining_quantity": -1.0},
                   {"broker_position_quantity": 1e-20}):
        with pytest.raises(ValueError):
            project_protected_exit_snapshot_v3(
                replace(record, payload={**record.payload, **change}),
                attempt_id=ATTEMPT, batch_id=BATCH)
    for rows in ([], [projected.detail, projected.detail],
                 [{**projected.detail, "attempt": 3}],
                 [{**projected.detail, "requested_quantity": "6.000000000000000000"}]):
        with pytest.raises(ValueError):
            seal_protected_exit_snapshot_v3(
                rows, [projected.event], run_id=RUN, batch_id=BATCH)


def test_v3_writer_and_cold_reader_reject_missing_or_tampered_child(monkeypatch):
    from src.backend.backtest_typed_projection import project_pending_backtest_v3_prefix
    from src.backend.backtest_squeeze_episode_v3 import load_verified_squeeze_v3_prefix
    import src.backend.backtest_squeeze_episode_v3 as cold_module
    from src.trading_runtime import arte_journal_writer as writer
    from tests.test_backtest_portfolio_control_v3 import _V3Client
    from tests.test_backtest_squeeze_episode_v3 import _FakeColdClient

    source, _ = _actual_record()
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
    assert client.inserts == ["trading_event_v1", SNAPSHOT.name, "trading_commit_v3"]
    assert client.tables["trading_commit_v3"][0]["protected_exit_snapshot_count"] == 1

    monkeypatch.setattr(cold_module, "storage_preflight", lambda client, *, tables: None)
    monkeypatch.setattr(cold_module, "_verify_recovery_chunk",
                        lambda client, commits, *, journal_profile: None)
    cold = _FakeColdClient(client.tables["trading_commit_v3"][0], [], [])
    cold.snapshot_events = client.tables["trading_event_v1"]
    cold.snapshot_rows = client.tables[SNAPSHOT.name]
    original = cold.snapshot_rows[0]
    assert load_verified_squeeze_v3_prefix(
        cold, RUN, expected_market_plan_token="a" * 64,
        expected_query_sha256="b" * 64) is not None
    cold.snapshot_rows = []
    with pytest.raises((RuntimeError, ValueError)):
        load_verified_squeeze_v3_prefix(
            cold, RUN, expected_market_plan_token="a" * 64,
            expected_query_sha256="b" * 64)
    cold.snapshot_rows = [{**original,
                           "requested_quantity": "6.000000000000000000"}]
    with pytest.raises((RuntimeError, ValueError)):
        load_verified_squeeze_v3_prefix(
            cold, RUN, expected_market_plan_token="a" * 64,
            expected_query_sha256="b" * 64)
