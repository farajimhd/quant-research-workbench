"""Closed simulated OMS reply-policy source, not arbitrary IBKR responses."""
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.backend.backtest_broker_policy_v3 import (
    project_broker_reply_policy_v3, recover_broker_reply_policy_payload,
    seal_broker_reply_policy_v3,
)
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.trading_runtime.order_management import (
    BrokerCommunicationPolicy, OrderManagementEngine,
)
from src.trading_runtime.signals import StrategyIntent
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter
from tests.test_arte_journal_writer import ATTEMPT, BATCH, RUN


AT = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)


def _manager(broker, policy):
    journal = BacktestMemoryJournal(run_id=RUN)
    manager = OrderManagementEngine.__new__(OrderManagementEngine)
    manager.broker = broker
    manager.policy = policy
    manager.journal = journal
    manager.run_id = RUN
    manager.strategy_id = "strategy-1"
    manager.strategy_revision = 7
    manager._groups = {}
    manager._transition = lambda *args: None
    return manager, journal


def _project(record):
    return project_broker_reply_policy_v3(
        record, attempt_id=ATTEMPT, batch_id=BATCH)


def test_real_suppression_emitter_preserves_order_and_deduplicated_response():
    broker = SimulatedBrokerAdapter(["DU1"])
    manager, journal = _manager(broker, BrokerCommunicationPolicy(
        suppressed_message_ids=("o163", "o163", "o164")))
    asyncio.run(manager.configure_broker_session())
    record, = journal.unfenced_records()
    projection = _project(record)
    assert projection.event["entity_type"] == "order_reply_suppression"
    assert projection.detail["request_message_count"] == 3
    assert projection.detail["response_message_count"] == 2
    assert [(row["role"], row["ordinal"], row["message_id"])
            for row in projection.messages] == [
                ("request", 0, "o163"), ("request", 1, "o163"),
                ("request", 2, "o164"), ("response", 0, "o163"),
                ("response", 1, "o164")]
    assert projection.event["correlation_id"] == record.payload["correlation_id"]
    assert "broker_response" not in projection.detail
    assert recover_broker_reply_policy_payload(
        projection.event, projection.detail, projection.messages) == record.payload
    assert seal_broker_reply_policy_v3(
        [projection.detail], list(projection.messages), [projection.event],
        run_id=RUN, batch_id=BATCH)["broker_reply_policy_message_count"] == 5
    for response in ({"status": "submitted", "messageIds": ["o164", "o163"]},
                     {"status": "submitted", "messageIds": ["o163", "o164"],
                      "raw": {"open": 1}}):
        with pytest.raises(ValueError, match="response"):
            _project(replace(record, payload={**record.payload,
                                              "broker_response": response}))


@pytest.mark.parametrize("message_ids,expected", [
    (["o163", "o164"], True), (["unknown-1"], False),
])
def test_real_warning_decision_emitter_preserves_group_lineage_and_message_order(
    message_ids, expected,
):
    class Broker:
        async def reply(self, reply_id, confirmed):
            assert (reply_id, confirmed) == ("reply-1", expected)
            return []

    manager, journal = _manager(Broker(), BrokerCommunicationPolicy(
        auto_confirm_message_ids=("o163", "o164")))
    intent = StrategyIntent("intent-1", "AAA", AT, "enter_long", 5.0, 10.0)
    group = SimpleNamespace(group_id="group-1", account_id="DU1",
                            intent=intent, warning_message_ids=[], orders=[])
    manager._groups[group.group_id] = group
    manager.risk = SimpleNamespace(release=lambda *args: None)
    asyncio.run(manager._resolve_warning_chain_locked(group, [
        {"id": "reply-1", "message": "caution", "messageIds": message_ids}]))
    record, = journal.unfenced_records()
    projection = _project(record)
    assert projection.detail["confirmed"] == int(expected)
    assert projection.detail["order_group_id"] == "group-1"
    assert projection.detail["intent_id"] == "intent-1"
    assert [row["message_id"] for row in projection.messages] == message_ids
    assert projection.event["correlation_id"] == record.payload["correlation_id"]
    assert recover_broker_reply_policy_payload(
        projection.event, projection.detail, projection.messages) == record.payload
    seal_broker_reply_policy_v3(
        [projection.detail], list(projection.messages), [projection.event],
        run_id=RUN, batch_id=BATCH)
    for children in ([], list(projection.messages) * 2,
                     [{**projection.messages[0], "message_id": "wrong"},
                      *projection.messages[1:]]):
        with pytest.raises(ValueError):
            seal_broker_reply_policy_v3(
                [projection.detail], children, [projection.event],
                run_id=RUN, batch_id=BATCH)
    with pytest.raises(ValueError, match="Warning decision"):
        _project(replace(record, payload={**record.payload, "known": not expected}))
