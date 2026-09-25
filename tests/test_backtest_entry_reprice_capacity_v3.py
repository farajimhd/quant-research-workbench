"""Strict closed Portfolio entry-reprice capacity evidence."""
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from src.backend.backtest_entry_reprice_capacity_v3 import (
    project_entry_reprice_capacity_v3, recover_entry_reprice_capacity_payload,
    seal_entry_reprice_capacity_v3,
)
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from tests.test_arte_journal_writer import ATTEMPT, BATCH, RUN


AT = datetime(2026, 8, 18, 14, tzinfo=timezone.utc)


def _record(*, funding=10, reasons=None):
    journal = BacktestMemoryJournal(run_id=RUN)
    return journal.append(
        run_id=RUN, category="portfolio_management",
        entity_type="entry_reprice_capacity", entity_id="intent-1",
        account_id="DU1", event_time=AT,
        payload={"reason": "insufficient_reserved_capacity",
                 "limiting_reasons": reasons or ["limited_by_available_funds",
                                                 "limited_by_order_notional"],
                 "price": 10.02, "remaining_quantity": 5.0,
                 "capacity_quantity": 2.5,
                 "entry_funding_price": funding})


def test_real_journal_lineage_roundtrips_ordered_reasons_and_numeric_type():
    record = _record()
    projected = project_entry_reprice_capacity_v3(
        record, attempt_id=ATTEMPT, batch_id=BATCH)
    assert recover_entry_reprice_capacity_payload(
        projected.event, projected.detail, projected.reasons) == record.payload
    seal = seal_entry_reprice_capacity_v3(
        [projected.detail], projected.reasons, [projected.event],
        run_id=RUN, batch_id=BATCH)
    assert seal["entry_reprice_capacity_count"] == 1
    assert seal["entry_reprice_capacity_reason_count"] == 2
    assert [row["ordinal"] for row in projected.reasons] == [0, 1]


def test_actual_portfolio_authorize_reprice_emitter_projects(monkeypatch):
    import asyncio
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    import src.trading_runtime.portfolio as portfolio_module
    from src.trading_runtime.portfolio import (
        PortfolioManagementEngine, PortfolioControlMode, PortfolioSyncState,
    )
    from src.trading_runtime.signals import StrategyIntent

    journal = BacktestMemoryJournal(run_id=RUN)
    portfolio = PortfolioManagementEngine.__new__(PortfolioManagementEngine)
    portfolio.run_id = RUN
    portfolio.allocation_identity = "strategy-1"
    portfolio.reservations = {"reservation-1": SimpleNamespace(
        account_id="DU1", intent_id="intent-1", remaining_quantity=5.0,
        status="reserved", assignment_id="assignment-1", ticker="AAA")}
    portfolio.allocations = {}
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
    portfolio._entry_capacity = lambda *_args, **_kwargs: (
        2.5, ("limited_by_available_funds", "limited_by_order_notional"))
    portfolio._record = lambda kind, entity, account, payload: journal.append(
        run_id=RUN, category="portfolio_management", entity_type=kind,
        entity_id=entity, account_id=account, event_time=AT, payload=payload)
    monkeypatch.setattr(portfolio_module, "_reserved_entry_loss", lambda *_a: 1.0)
    intent = StrategyIntent("intent-1", "AAA", AT, "enter_long", 5.0, 10.0,
                            metadata={"portfolio_reservation_id": "reservation-1",
                                      "entry_funding_price": 10})
    assert asyncio.run(portfolio.authorize_entry_reprice(
        intent, "DU1", 10.02, 5.0)) is False
    record, = journal.unfenced_records()
    projected = project_entry_reprice_capacity_v3(
        record, attempt_id=ATTEMPT, batch_id=BATCH)
    assert projected.detail["capacity_quantity"] == 2.5
    assert recover_entry_reprice_capacity_payload(
        projected.event, projected.detail, projected.reasons) == record.payload


def test_capacity_seal_rejects_missing_extra_duplicate_and_corrupt_evidence():
    projected = project_entry_reprice_capacity_v3(
        _record(), attempt_id=ATTEMPT, batch_id=BATCH)
    for details, reasons in (
        ([], projected.reasons),
        ([projected.detail, projected.detail], projected.reasons),
        ([projected.detail], projected.reasons[:1]),
        ([projected.detail], projected.reasons + projected.reasons[:1]),
        ([projected.detail], [{**projected.reasons[0], "reason": "limited_by_ticker"},
                              projected.reasons[1]]),
    ):
        with pytest.raises(ValueError):
            seal_entry_reprice_capacity_v3(
                details, reasons, [projected.event], run_id=RUN, batch_id=BATCH)


def test_capacity_projection_rejects_unmodeled_source_and_unclosed_reasons():
    record = _record()
    for change in ({"new_field": "hidden"},
                   {"limiting_reasons": ["limited_by_unknown"]},
                   {"limiting_reasons": ["limited_by_ticker"] * 2},
                   {"entry_funding_price": 2**60 + 1},
                   {"capacity_quantity": 5.0}):
        with pytest.raises(ValueError):
            project_entry_reprice_capacity_v3(
                replace(record, payload={**record.payload, **change}),
                attempt_id=ATTEMPT, batch_id=BATCH)


def test_unmodeled_portfolio_reprice_variant_remains_fail_closed():
    from src.backend.backtest_typed_projection import project_pending_backtest_v3_prefix

    journal = BacktestMemoryJournal(run_id=RUN)
    journal.append(run_id=RUN, category="portfolio_management",
                   entity_type="entry_reprice_anomaly", entity_id="intent-1",
                   account_id="DU1", event_time=AT,
                   payload={"reason": "invalid_protection_at_reprice",
                            "detail": "stop unavailable", "price": 10.0,
                            "remaining_quantity": 5.0})
    with pytest.raises(ValueError):
        project_pending_backtest_v3_prefix(
            journal, attempt_id=ATTEMPT, run_month=AT.date().replace(day=1),
            prior_sequence=0, expected_market_plan_token="a" * 64,
            expected_query_sha256="b" * 64)


def test_v3_writer_and_cold_reader_require_complete_capacity_children(monkeypatch):
    from src.backend.backtest_typed_projection import project_pending_backtest_v3_prefix
    from src.backend.backtest_entry_reprice_capacity_v3 import TABLES
    from src.backend.backtest_squeeze_episode_v3 import load_verified_squeeze_v3_prefix
    import src.backend.backtest_squeeze_episode_v3 as cold_module
    from src.trading_runtime import arte_journal_writer as writer
    from tests.test_backtest_portfolio_control_v3 import _V3Client
    from tests.test_backtest_squeeze_episode_v3 import _FakeColdClient

    journal = BacktestMemoryJournal(run_id=RUN)
    source = _record()
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
    assert client.inserts == ["trading_event_v1", TABLES[0].name,
                              TABLES[1].name, "trading_commit_v3"]
    assert client.tables["trading_commit_v3"][0]["entry_reprice_capacity_reason_count"] == 2

    monkeypatch.setattr(cold_module, "storage_preflight",
                        lambda client, *, tables: None)
    monkeypatch.setattr(cold_module, "_verify_recovery_chunk",
                        lambda client, commits, *, journal_profile: None)
    cold = _FakeColdClient(client.tables["trading_commit_v3"][0], [], [])
    cold.capacity_events = client.tables["trading_event_v1"]
    cold.capacity_rows = {table.name: client.tables[table.name] for table in TABLES}
    assert load_verified_squeeze_v3_prefix(
        cold, RUN, expected_market_plan_token="a" * 64,
        expected_query_sha256="b" * 64) is not None
    cold.capacity_rows[TABLES[1].name] = cold.capacity_rows[TABLES[1].name][:1]
    with pytest.raises((RuntimeError, ValueError)):
        load_verified_squeeze_v3_prefix(
            cold, RUN, expected_market_plan_token="a" * 64,
            expected_query_sha256="b" * 64)
    cold.capacity_rows[TABLES[1].name] = [
        {**row, "reason": "limited_by_ticker"} if index == 0 else dict(row)
        for index, row in enumerate(client.tables[TABLES[1].name])]
    with pytest.raises((RuntimeError, ValueError)):
        load_verified_squeeze_v3_prefix(
            cold, RUN, expected_market_plan_token="a" * 64,
            expected_query_sha256="b" * 64)


def test_two_capacity_events_coalesce_with_parent_and_child_identity(monkeypatch):
    from src.backend.backtest_typed_projection import project_pending_backtest_v3_prefix
    from src.backend.backtest_squeeze_episode_v3 import coalesce_squeeze_units_v3
    from src.trading_runtime import arte_journal_writer as writer
    from tests.test_backtest_portfolio_control_v3 import _V3Client

    source = _record()
    journal = BacktestMemoryJournal(run_id=RUN)
    for entity in ("intent-1", "intent-2"):
        journal.append(run_id=RUN, category=source.category,
                       entity_type=source.entity_type, entity_id=entity,
                       account_id=source.account_id, event_time=source.event_time,
                       payload=source.payload)
    units = project_pending_backtest_v3_prefix(
        journal, attempt_id=ATTEMPT, run_month=AT.date().replace(day=1),
        prior_sequence=0, expected_market_plan_token="a" * 64,
        expected_query_sha256="b" * 64)
    merged = coalesce_squeeze_units_v3(units)
    assert len(merged.base.events) == 2
    assert len(merged.entry_reprice_capacities) == 2
    assert len(merged.entry_reprice_capacity_reasons) == 4
    assert {row["batch_id"] for row in merged.entry_reprice_capacity_reasons} == {
        merged.base.batch_id}
    monkeypatch.setattr(writer, "_v3_preflight", lambda client: None)
    monkeypatch.setattr(writer, "_verify_run_identity",
                        lambda client, run: {"mode": "backtest"})
    client = _V3Client()
    writer.publish_typed_squeeze_batch_v3(client, merged)
    assert client.tables["trading_commit_v3"][0]["entry_reprice_capacity_count"] == 2
    assert client.tables["trading_commit_v3"][0]["entry_reprice_capacity_reason_count"] == 4
