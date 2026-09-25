"""Actual Portfolio invalid-protection reprice refusal through V3 facts."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.backend.backtest_entry_reprice_rejected_v3 import (
    project_entry_reprice_rejected_v3,
    recover_entry_reprice_rejected_payload, seal_entry_reprice_rejected_v3,
)
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.trading_runtime.signals import StrategyIntent
from tests.test_arte_journal_writer import ATTEMPT, BATCH, RUN


AT = datetime(2026, 8, 18, 14, tzinfo=timezone.utc)


def _actual_record(monkeypatch):
    import src.trading_runtime.portfolio as portfolio_module
    from src.trading_runtime.portfolio import (
        PortfolioManagementEngine, PortfolioControlMode, PortfolioSyncState,
    )

    journal = BacktestMemoryJournal(run_id=RUN)
    portfolio = PortfolioManagementEngine.__new__(PortfolioManagementEngine)
    portfolio.run_id = RUN
    portfolio.allocation_identity = "strategy-1"
    portfolio.reservations = {"reservation-1": SimpleNamespace(
        account_id="DU1", intent_id="intent-1", remaining_quantity=5.0,
        status="reserved", assignment_id="assignment-1", ticker="AAA")}
    state = SimpleNamespace(
        profile=SimpleNamespace(enabled=True), summary=object(),
        control_mode=PortfolioControlMode.ENABLED,
        disabled_strategy_allocations=set(),
        sync_state=PortfolioSyncState.SYNCHRONIZED)

    @asynccontextmanager
    async def admission(_account_id):
        yield state

    portfolio._admission_fence = admission
    portfolio._snapshot_stale = lambda *_args: False
    portfolio._policy = lambda _state: SimpleNamespace(
        maximum_daily_loss=100, maximum_drawdown=100)
    portfolio._metrics = lambda _state: {"daily_loss": 0, "drawdown": 0}
    portfolio._record = lambda kind, entity, account, payload: journal.append(
        run_id=RUN, category="portfolio_management", entity_type=kind,
        entity_id=entity, account_id=account, event_time=AT, payload=payload)
    def invalid_protection(*_args):
        raise ValueError("inherited position stop is on the wrong side of the add")
    monkeypatch.setattr(portfolio_module, "_reserved_entry_loss", invalid_protection)
    intent = StrategyIntent("intent-1", "AAA", AT, "add_long", 5.0, 10.0,
                            metadata={"portfolio_reservation_id": "reservation-1"})
    assert asyncio.run(portfolio.authorize_entry_reprice(
        intent, "DU1", 10.02, 5.0)) is False
    record, = journal.unfenced_records()
    return record


def test_real_portfolio_emitter_roundtrips_scalar_diagnostic(monkeypatch):
    record = _actual_record(monkeypatch)
    projected = project_entry_reprice_rejected_v3(
        record, attempt_id=ATTEMPT, batch_id=BATCH)
    assert projected.detail["reason_detail"] == record.payload["detail"]
    assert recover_entry_reprice_rejected_payload(
        projected.event, projected.detail) == record.payload
    seal = seal_entry_reprice_rejected_v3(
        [projected.detail], [projected.event], run_id=RUN, batch_id=BATCH)
    assert seal["entry_reprice_rejected_count"] == 1


def test_rejected_projection_and_seal_fail_closed(monkeypatch):
    record = _actual_record(monkeypatch)
    projected = project_entry_reprice_rejected_v3(
        record, attempt_id=ATTEMPT, batch_id=BATCH)
    for change in ({"unexpected": "evidence"}, {"reason": "other"},
                   {"detail": ""}, {"price": float("nan")}):
        with pytest.raises(ValueError):
            project_entry_reprice_rejected_v3(
                replace(record, payload={**record.payload, **change}),
                attempt_id=ATTEMPT, batch_id=BATCH)
    for rows in ([], [projected.detail, projected.detail],
                 [{**projected.detail, "reason_detail": "tampered"}]):
        with pytest.raises(ValueError):
            seal_entry_reprice_rejected_v3(
                rows, [projected.event], run_id=RUN, batch_id=BATCH)


def test_v3_writer_cold_read_and_tamper(monkeypatch):
    from src.backend.backtest_typed_projection import project_pending_backtest_v3_prefix
    from src.backend.backtest_squeeze_episode_v3 import load_verified_squeeze_v3_prefix
    import src.backend.backtest_squeeze_episode_v3 as cold_module
    from src.trading_runtime import arte_journal_writer as writer
    from tests.test_backtest_portfolio_control_v3 import _V3Client
    from tests.test_backtest_squeeze_episode_v3 import _FakeColdClient
    from src.backend.backtest_entry_reprice_rejected_v3 import REJECTED

    source = _actual_record(monkeypatch)
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
    assert client.inserts == ["trading_event_v1", REJECTED.name, "trading_commit_v3"]
    assert client.tables["trading_commit_v3"][0]["entry_reprice_rejected_count"] == 1

    monkeypatch.setattr(cold_module, "storage_preflight",
                        lambda client, *, tables: None)
    monkeypatch.setattr(cold_module, "_verify_recovery_chunk",
                        lambda client, commits, *, journal_profile: None)
    cold = _FakeColdClient(client.tables["trading_commit_v3"][0], [], [])
    cold.rejected_events = client.tables["trading_event_v1"]
    cold.rejected_rows = client.tables[REJECTED.name]
    assert load_verified_squeeze_v3_prefix(
        cold, RUN, expected_market_plan_token="a" * 64,
        expected_query_sha256="b" * 64) is not None
    cold.rejected_rows = [{**cold.rejected_rows[0], "reason_detail": "tampered"}]
    with pytest.raises((RuntimeError, ValueError)):
        load_verified_squeeze_v3_prefix(
            cold, RUN, expected_market_plan_token="a" * 64,
            expected_query_sha256="b" * 64)
