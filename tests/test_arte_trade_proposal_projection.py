from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.trading_runtime.arte_trade_proposal_projection import (
    TABLES, project_trade_proposal,
)
from src.trading_runtime.signals import StrategyIntent


NOW = datetime(2026, 7, 14, 14, 0, tzinfo=UTC)


def record(entity_type, payload):
    return SimpleNamespace(
        run_id="run-1", record_id=str(uuid4()), category="trade_proposal",
        entity_type=entity_type, entity_id="proposal-1", account_id="SIM-01",
        event_time=NOW, payload=payload,
    )


def confirmed(intent=None):
    intent = intent or StrategyIntent(
        intent_id="proposal:proposal-1", ticker="AAPL", event_time=NOW,
        action="enter_long", quantity=10, reference_price=100,
        invalidation_price=95,
    )
    return record("trade_proposal_confirmed", {
        "proposal_id": "proposal-1", "authority": "manual", "status": "confirmed",
        "intent": intent.payload(),
    })


def test_closed_confirmation_projects_exact_named_intent() -> None:
    source = confirmed()
    rows = project_trade_proposal(source)
    assert rows.parent["proposal_id"] == "proposal-1"
    assert rows.intent["intent_id"] == source.payload["intent"]["intent_id"]
    assert rows.intent["event_time"] == NOW
    assert rows.intent["invalidation_price"] == "95.000000000000000000"
    assert set(rows.parent) == {name for name, _ in TABLES[0].columns}
    assert set(rows.intent) == {name for name, _ in TABLES[1].columns}
    assert rows.result is None
    assert all("JSON" not in table.ddl() and "storage_policy = 'live_market_ssd'" in table.ddl()
               for table in TABLES)


@pytest.mark.parametrize("payload,expected", [
    ({"status": "failed", "error": "broker unavailable"}, "broker unavailable"),
    ({"status": "exit_fill_pending", "decision": {"status": "exit_fill_pending",
      "held_quantity": 5.0}, "order_group": None}, "5.000000000000000000"),
    ({"status": "rejected", "decision": {"status": "rejected", "reason": "halted"},
      "order_group": None}, "halted"),
])
def test_closed_result_variants(payload, expected) -> None:
    source = record("trade_proposal_result", {
        "proposal_id": "proposal-1", "authority": "semi_automatic", **payload,
    })
    rows = project_trade_proposal(source)
    assert expected in rows.result.values()
    assert rows.intent is None
    assert set(rows.result) == {name for name, _ in TABLES[2].columns}


@pytest.mark.parametrize("mutation", [
    lambda p: p["intent"].update(metadata={"market_snapshot": {"freshness": "ready"}}),
    lambda p: p["intent"].update(unknown="lost"),
    lambda p: p["intent"].update(intent_id="other"),
    lambda p: p.update(unknown="lost"),
])
def test_confirmation_rejects_open_or_conflicting_fields(mutation) -> None:
    source = confirmed()
    mutation(source.payload)
    with pytest.raises(ValueError):
        project_trade_proposal(source)


@pytest.mark.parametrize("decision,order_group", [
    ({"status": "approved"}, {"state": "submitted"}),
    ({"status": "rejected", "metrics_before": {"cash": 10}}, None),
    ({"status": "rejected", "reason": "halted", "extra": 1}, None),
])
def test_result_rejects_unmodeled_nested_variants(decision, order_group) -> None:
    source = record("trade_proposal_result", {
        "proposal_id": "proposal-1", "authority": "manual", "status": decision["status"],
        "decision": decision, "order_group": order_group,
    })
    with pytest.raises(ValueError):
        project_trade_proposal(source)
