from dataclasses import asdict
import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.trading_runtime.arte_trade_proposal_children import (
    METRICS, TABLES, project_market_child, project_result_children,
)
from src.trading_runtime.order_management import OrderGroupSnapshot, OrderManagementState
from src.trading_runtime.portfolio import PortfolioDecision, PortfolioDecisionStatus
from src.trading_runtime.signals import StrategyIntent
from src.trading_runtime.runtime import TradingRuntime


NOW = datetime(2026, 7, 14, 14, tzinfo=UTC)


def record(payload):
    return SimpleNamespace(run_id="run-1", record_id=str(uuid4()), event_time=NOW,
                           payload=payload)


def live_confirmation():
    market = {
        "authority": "qmd_gateway_live_memory", "ticker": "AAPL",
        "observed_at": NOW.isoformat(), "reference_price": 100.0,
        "bid": 99.99, "ask": 100.01, "source_sequence": 42,
        "age_ms": 10, "freshness": "ready",
        "client_chart_observed_at": NOW.isoformat(), "client_chart_sequence": "chart-1",
    }
    intent = StrategyIntent(
        intent_id="proposal:p-1", ticker="AAPL", event_time=NOW,
        action="enter_long", quantity=10, reference_price=100,
        metadata={
            "origin": "trade_proposal", "proposal_id": "p-1",
            "proposal_authority": "manual", "action_id": "enter_long",
            "identity_revision": "rev-1", "market_snapshot": market,
            "bid": 99.99, "ask": 100.01, "tick_size": .01,
            "quote_observed_at": NOW, "security_type": "STK", "currency": "USD",
            "conid": 265598, "exchange": "SMART",
        },
    )
    return record({"proposal_id": "p-1", "authority": "manual",
                   "status": "confirmed", "intent": intent.payload(),
                   "correlation_id": "corr-1", "causation_id": "cause-1"})


def full_result():
    metrics = {name: float(i) for i, name in enumerate(METRICS)}
    decision = PortfolioDecision(
        decision_id="d-1", request_id="proposal:p-1", account_key="paper",
        account_id="DU1", policy_id="policy", policy_revision=1,
        snapshot_id="s-1", status=PortfolioDecisionStatus.APPROVED,
        requested_quantity=10, approved_quantity=10, approved_notional=1000,
        planned_loss=50, reservation_id="r-1", reasons=("approved",),
        metrics_before=metrics, metrics_after=metrics, decided_at=NOW,
    )
    group = OrderGroupSnapshot(
        group_id="g-1", intent_id="proposal:p-1", account_id="DU1",
        ticker="AAPL", action="enter_long", state=OrderManagementState.WORKING,
        client_order_ids=("c-1", "c-2"), broker_order_ids=("b-1",),
        submitted_at=NOW, updated_at=NOW, filled_quantity=0,
        remaining_quantity=10, warning_message_ids=("w-1",),
        rejection_reason="", decision_to_submit_ms=12, policy_version=1,
        reentry_after_fill=False, assignment_id="",
    )
    return record({"proposal_id": "p-1", "authority": "manual", "status": "approved",
                   "decision": decision.payload(), "order_group": asdict(group)})


def test_live_producer_shape_market_rows_are_named_and_exact() -> None:
    source = live_confirmation()
    child = project_market_child(source).market
    assert child["scanner_sequence"] == 42
    assert child["conid"] == 265598
    assert child["record_id"] == source.record_id
    assert "correlation_id" not in child and "causation_id" not in child
    assert set(child) == {name for name, _ in TABLES[0].columns}
    source_market = source.payload["intent"]["metadata"]["market_snapshot"]
    assert child["scanner_sequence"] == source_market["source_sequence"]
    assert child["market_observed_at"] == NOW


def test_portfolio_and_oms_children_preserve_order_and_metrics() -> None:
    source = full_result()
    child = project_result_children(source)
    assert set(child.decision) == {name for name, _ in TABLES[1].columns}
    assert [row["phase"] for row in child.metrics] == ["before", "after"]
    assert child.reasons[0]["reason"] == "approved"
    assert [(row["kind"], row["ordinal"], row["value"]) for row in child.order_ids] == [
        ("client_order_ids", 0, "c-1"), ("client_order_ids", 1, "c-2"),
        ("broker_order_ids", 0, "b-1"), ("warning_message_ids", 0, "w-1"),
    ]
    assert set(child.order_group) == {name for name, _ in TABLES[4].columns}
    for name in ("group_id", "intent_id", "account_id", "ticker", "state",
                 "filled_quantity", "remaining_quantity", "submitted_at", "updated_at"):
        if name in {"filled_quantity", "remaining_quantity"}:
            assert float(child.order_group[name]) == source.payload["order_group"][name]
        else:
            assert child.order_group[name] == source.payload["order_group"][name]
    for name in ("decision_id", "request_id", "account_id", "status",
                 "approved_notional", "reservation_id", "decided_at"):
        if name == "approved_notional":
            assert float(child.decision[name]) == source.payload["decision"][name]
        else:
            assert child.decision[name] == source.payload["decision"][name]
    assert {name: float(child.metrics[0][name]) for name in METRICS} == source.payload["decision"]["metrics_before"]
    assert {name: float(child.metrics[1][name]) for name in METRICS} == source.payload["decision"]["metrics_after"]
    assert all("storage_policy = 'live_market_ssd'" in table.ddl() for table in TABLES)


@pytest.mark.parametrize("field", ["unknown", "provider_extra"])
def test_market_rejects_unmodeled_source_fields(field) -> None:
    source = live_confirmation()
    source.payload["intent"]["metadata"]["market_snapshot"][field] = "x"
    with pytest.raises(ValueError):
        project_market_child(source)


@pytest.mark.parametrize("field,value", [
    ("source_sequence", "seq-1"),
    ("reference_price", float("nan")),
    ("observed_at", "2026-07-14T14:00:00"),
    ("age_ms", -1),
])
def test_live_market_rejects_untyped_or_nonfinite_values(field, value) -> None:
    source = live_confirmation()
    source.payload["intent"]["metadata"]["market_snapshot"][field] = value
    with pytest.raises(ValueError):
        project_market_child(source)


def test_result_rejects_open_metrics_and_live_task() -> None:
    source = full_result()
    source.payload["decision"]["metrics_before"]["new_metric"] = 1
    with pytest.raises(ValueError):
        project_result_children(source)
    source = full_result()
    source.payload["decision"]["metrics_before"]["available_funds"] = float("inf")
    with pytest.raises(ValueError):
        project_result_children(source)
    source = full_result()
    source.payload["order_group"]["current_limit_price"] = float("nan")
    with pytest.raises(ValueError):
        project_result_children(source)
    source = full_result()
    source.payload["order_group"]["protection_task"] = "running"
    with pytest.raises(ValueError):
        project_result_children(source)


def test_actual_runtime_emitter_records_project_without_disk() -> None:
    emitted = []

    class Journal:
        def append(self, **kwargs):
            emitted.append(SimpleNamespace(
                record_id=str(uuid4()), run_id=kwargs["run_id"],
                event_time=kwargs["event_time"], payload={
                    **kwargs["payload"], "correlation_id": "corr-1",
                    "causation_id": "cause-1",
                },
            ))

    runtime = TradingRuntime.__new__(TradingRuntime)
    runtime.config = SimpleNamespace(account_ids=("DU1",))
    runtime.run_id = "run-1"
    runtime.journal = Journal()
    runtime.last_event_time = NOW
    expected = full_result().payload
    runtime._execute_intents = AsyncMock(return_value=[{
        "decision": expected["decision"], "order_group": expected["order_group"],
    }])
    intent = StrategyIntent(**live_confirmation().payload["intent"])
    asyncio.run(runtime.submit_external_intent(
        intent, account_id="DU1", proposal_id="p-1", proposal_authority="manual",
    ))
    assert project_market_child(emitted[0]).market["scanner_sequence"] == 42
    assert project_result_children(emitted[1]).order_group["group_id"] == "g-1"
