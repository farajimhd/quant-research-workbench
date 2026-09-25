"""Actual Portfolio fill-applied allocation through closed V3 evidence."""
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_portfolio_allocation_v3 import (
    ALLOCATION, project_portfolio_allocation_v3,
    recover_portfolio_allocation_payload, seal_portfolio_allocation_v3,
)
from src.trading_runtime.portfolio import PortfolioManagementEngine, PortfolioReservation
from tests.test_arte_journal_writer import ATTEMPT, BATCH, RUN


AT = datetime(2026, 8, 18, 14, tzinfo=timezone.utc)


def _actual_records():
    journal = BacktestMemoryJournal(run_id=RUN)
    portfolio = PortfolioManagementEngine.__new__(PortfolioManagementEngine)
    portfolio.journal = journal
    portfolio.run_id = RUN
    portfolio.strategy_revision = 7
    portfolio._typed_admission_stage = None
    portfolio._typed_recovery = False
    portfolio.allocations = {}
    portfolio._release_cash_holds = lambda *_args: None
    reservation = PortfolioReservation(
        reservation_id="reserve-1", decision_id="decision-1", intent_id="intent-1",
        account_key="account-1", account_id="DU1", strategy_id="strategy-1",
        assignment_id="assignment-1", ticker="AAA", action="enter_long",
        quantity=5.0, remaining_quantity=5.0, reference_price=10.0,
        reserved_notional=50.0, reserved_planned_risk=2.0, created_at=AT)
    portfolio._apply_fill(reservation, 3.0)
    portfolio._apply_fill(reservation, 1.0, action="reduce_long")
    return journal.unfenced_records()


def test_actual_portfolio_emitter_roundtrips_entry_and_reduction():
    records = _actual_records()
    assert len(records) == 2
    for record in records:
        projected = project_portfolio_allocation_v3(
            record, attempt_id=ATTEMPT, batch_id=BATCH)
        assert recover_portfolio_allocation_payload(
            projected.event, projected.detail) == record.payload
        assert seal_portfolio_allocation_v3(
            [projected.detail], [projected.event], run_id=RUN,
            batch_id=BATCH)["portfolio_allocation_fill_count"] == 1
    assert records[0].payload["incremental_quantity"] == 3.0
    assert records[1].payload["incremental_quantity"] == -1.0


def test_allocation_drift_missing_duplicate_and_corrupt_child():
    record = _actual_records()[0]
    projected = project_portfolio_allocation_v3(
        record, attempt_id=ATTEMPT, batch_id=BATCH)
    for change in ({"event": "unknown"}, {"unexpected": 1},
                   {"action": "other"}, {"assignment_id": "different"},
                   {"quantity": float("nan")},
                   {"incremental_quantity": 1e-20}):
        with pytest.raises(ValueError):
            project_portfolio_allocation_v3(
                replace(record, payload={**record.payload, **change}),
                attempt_id=ATTEMPT, batch_id=BATCH)
    for rows in ([], [projected.detail, projected.detail],
                 [{**projected.detail, "quantity": "99.000000000000000000"}],
                 [{**projected.detail, "assignment_id": "other"}]):
        with pytest.raises(ValueError):
            seal_portfolio_allocation_v3(
                rows, [projected.event], run_id=RUN, batch_id=BATCH)


def test_v3_writer_and_cold_reader_reject_missing_or_tampered_child(monkeypatch):
    from src.backend.backtest_typed_projection import project_pending_backtest_v3_prefix
    from src.backend.backtest_squeeze_episode_v3 import load_verified_squeeze_v3_prefix
    import src.backend.backtest_squeeze_episode_v3 as cold_module
    from src.trading_runtime import arte_journal_writer as writer
    from tests.test_backtest_portfolio_control_v3 import _V3Client
    from tests.test_backtest_squeeze_episode_v3 import _FakeColdClient

    source = _actual_records()[0]
    journal = BacktestMemoryJournal(run_id=RUN)
    journal.append(run_id=RUN, category=source.category,
                   entity_type=source.entity_type, entity_id=source.entity_id,
                   account_id=source.account_id, event_time=source.event_time,
                   payload=source.payload)
    unit, = project_pending_backtest_v3_prefix(
        journal, attempt_id=ATTEMPT, run_month=source.event_time.date().replace(day=1),
        prior_sequence=0, expected_market_plan_token="a" * 64,
        expected_query_sha256="b" * 64)
    monkeypatch.setattr(writer, "_v3_preflight", lambda client: None)
    monkeypatch.setattr(writer, "_verify_run_identity",
                        lambda client, run: {"mode": "backtest"})
    client = _V3Client()
    writer.publish_typed_squeeze_batch_v3(client, unit)
    assert client.inserts == ["trading_event_v1", ALLOCATION.name, "trading_commit_v3"]
    assert client.tables["trading_commit_v3"][0]["portfolio_allocation_fill_count"] == 1

    monkeypatch.setattr(cold_module, "storage_preflight", lambda client, *, tables: None)
    monkeypatch.setattr(cold_module, "_verify_recovery_chunk",
                        lambda client, commits, *, journal_profile: None)
    cold = _FakeColdClient(client.tables["trading_commit_v3"][0], [], [])
    cold.allocation_events = client.tables["trading_event_v1"]
    cold.allocation_rows = client.tables[ALLOCATION.name]
    original = cold.allocation_rows[0]
    assert load_verified_squeeze_v3_prefix(
        cold, RUN, expected_market_plan_token="a" * 64,
        expected_query_sha256="b" * 64) is not None
    cold.allocation_rows = []
    with pytest.raises((RuntimeError, ValueError)):
        load_verified_squeeze_v3_prefix(
            cold, RUN, expected_market_plan_token="a" * 64,
            expected_query_sha256="b" * 64)
    cold.allocation_rows = [{**original, "quantity": "99.000000000000000000"}]
    with pytest.raises((RuntimeError, ValueError)):
        load_verified_squeeze_v3_prefix(
            cold, RUN, expected_market_plan_token="a" * 64,
            expected_query_sha256="b" * 64)
