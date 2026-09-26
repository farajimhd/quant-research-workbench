"""A completed amendment is attributed to its own intent, not the entry."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from src.backend.backtest_protection_change_v3 import project_protection_change_v3
from src.trading_runtime.ibkr_schema import OrderRequest
from src.trading_runtime.journal_contract import JournalRecord
from src.trading_runtime.order_management import OrderManagementEngine
from src.trading_runtime.signals import StrategyIntent
from src.trading_runtime.strategy_one_contract import STRATEGY_ID, STRATEGY_NUMBER


def test_effective_stop_change_carries_amendment_and_entry_lineage():
    at = datetime(2026, 8, 18, 12, 1, tzinfo=timezone.utc)
    entry = StrategyIntent("entry-1", "AAA", at, "enter_long", 10., 10.)
    amendment = StrategyIntent(
        "amendment-1", "AAA", at, "replace_protective_stop", 10., 10.,
        invalidation_price=9.7)
    request = OrderRequest(
        acctId="DU1", conid=123, cOID="client-stop", ticker="AAA",
        orderType="STP", side="SELL", quantity=10., auxPrice=9.7)
    group = SimpleNamespace(
        group_id="group-1", account_id="DU1", intent=entry,
        broker_order_roles={"42": "protective_stop"}, orders=[])
    manager = object.__new__(OrderManagementEngine)
    manager.run_id = "run-1"
    manager.strategy_id = STRATEGY_ID
    manager.strategy_revision = STRATEGY_NUMBER
    manager._groups = {group.group_id: group}
    manager._protection_versions = {}
    manager.journal = SimpleNamespace(append=Mock())

    manager._record_protection(
        group, request, phase="effective", broker_order_id="42",
        event_time=at, amendment_intent=amendment)

    recorded = manager.journal.append.call_args.kwargs
    assert (recorded["category"], recorded["entity_type"]) == (
        "protection", "protection_change")
    assert recorded["payload"]["source_intent_id"] == entry.intent_id
    assert recorded["payload"]["intent_id"] == amendment.intent_id
    assert recorded["payload"]["action"] == amendment.action
    assert recorded["payload"]["price"] == 9.7
    projected = project_protection_change_v3(
        JournalRecord(str(uuid4()), recorded["run_id"], 1, at, at,
                      recorded["category"], recorded["entity_type"],
                      recorded["entity_id"], recorded["account_id"],
                      recorded["payload"]),
        attempt_id=str(uuid4()), batch_id=str(uuid4()))
    assert projected.detail["source_intent_id"] == entry.intent_id
    assert projected.detail["intent_id"] == amendment.intent_id
