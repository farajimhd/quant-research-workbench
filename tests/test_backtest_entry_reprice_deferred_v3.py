"""Actual OMS capacity deferral to strict typed scalar evidence."""
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.backend.backtest_entry_reprice_deferred_v3 import (
    project_entry_reprice_deferred_v3,
    recover_entry_reprice_deferred_payload,
    seal_entry_reprice_deferred_v3,
)
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.trading_runtime.ibkr_schema import OrderRequest
from src.trading_runtime.order_management import (
    BrokerCommunicationPolicy, ExecutionQuote, OrderManagementEngine,
    OrderManagementState,
)
from src.trading_runtime.signals import StrategyIntent
from tests.test_arte_journal_writer import ATTEMPT, BATCH, RUN


AT = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)


def _emitted_record():
    journal = BacktestMemoryJournal(run_id=RUN)
    manager = OrderManagementEngine.__new__(OrderManagementEngine)
    manager.journal = journal
    manager.run_id = RUN
    manager.strategy_id = "strategy-1"
    manager.strategy_revision = 7
    manager.policy = BrokerCommunicationPolicy()
    manager.enforce_wall_clock_quote_freshness = False
    manager.reprice_authorizer = lambda *args: asyncio.sleep(0, result=False)
    manager._groups = {}
    manager._entry_body_valid = lambda *args: True
    quote = ExecutionQuote(10.0, 10.02, AT, 0.01)
    manager._execution_quote = lambda intent: quote
    intent = StrategyIntent("intent-1", "AAA", AT, "enter_long", 5.0, 10.0,
                            metadata={"entry_completion_quote": "bid"})
    group = SimpleNamespace(
        group_id="group-1", account_id="DU1", intent=intent,
        tactic=SimpleNamespace(side="BUY", quote=quote),
        filled_quantity=0.0, state=OrderManagementState.WORKING,
        reprice_count=0, current_limit_price=None,
        orders=[OrderRequest("DU1", 123, "LMT", "BUY", quantity=5.0,
                             ticker="AAA", price=9.99)],
        broker_order_ids=["order-1"], broker_order_roles={"order-1": "entry"},
        terminal_broker_order_ids=set(),
        broker_order_request_indexes={"order-1": 0},
        filled_by_broker_order={}, deferred_reprice=None,
        failed_reprice_at=None, last_reprice_at=None,
    )
    manager._groups[group.group_id] = group
    assert asyncio.run(manager._attempt_reprice(group, record_time=AT)) is False
    record, = journal.unfenced_records()
    return record


def _project(record):
    return project_entry_reprice_deferred_v3(
        record, attempt_id=ATTEMPT, batch_id=BATCH)


def test_real_oms_deferred_reprice_roundtrips_without_redundant_reason():
    record = _emitted_record()
    projected = _project(record)
    assert projected.event["entity_type"] == "entry_reprice_deferred"
    assert projected.detail["requested_price"] == 10.0
    assert projected.detail["remaining_quantity"] == 5.0
    assert "reason" not in projected.detail
    assert recover_entry_reprice_deferred_payload(
        projected.event, projected.detail) == record.payload
    assert seal_entry_reprice_deferred_v3(
        [projected.detail], [projected.event], run_id=RUN,
        batch_id=BATCH)["entry_reprice_deferred_count"] == 1


def test_deferred_reprice_rejects_extra_source_and_missing_or_corrupt_child():
    record = _emitted_record()
    for change in ({"reason": "other"}, {"broker_response": {}},
                   {"requested_price": float("nan")}):
        with pytest.raises(ValueError):
            _project(replace(record, payload={**record.payload, **change}))
    projected = _project(record)
    for children in ([], [projected.detail, projected.detail],
                     [{**projected.detail, "remaining_quantity": 4.0}]):
        with pytest.raises(ValueError):
            seal_entry_reprice_deferred_v3(
                children, [projected.event], run_id=RUN, batch_id=BATCH)
