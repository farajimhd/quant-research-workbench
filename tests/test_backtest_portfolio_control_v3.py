"""Closed scalar Portfolio control projection, including real memory ingress."""
from datetime import datetime, timezone
from hashlib import sha256

import pytest

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_portfolio_control_v3 import project_portfolio_control_v3
from src.trading_runtime.journal_contract import canonical_json
from tests.test_arte_journal_writer import ATTEMPT, BATCH, RUN


AT = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)


def _record(payload, entity_id="primary"):
    journal = BacktestMemoryJournal(run_id=RUN)
    return journal.append(run_id=RUN, category="portfolio_management",
                          entity_type="portfolio_control", entity_id=entity_id,
                          account_id="DU1", event_time=AT, payload=payload)


def _project(record):
    return project_portfolio_control_v3(
        record, attempt_id=ATTEMPT, batch_id=BATCH, account_key="primary")


@pytest.mark.parametrize("payload,entity_id,expected", [
    ({"event": "control_changed", "control_mode": "entries_paused", "reason": "risk"},
     "primary", ("entries_paused", None, None)),
    ({"event": "strategy_allocation_control_changed", "strategy_id": "s1",
      "enabled": False, "reason": "operator"},
     "primary:s1", (None, "s1", 0)),
])
def test_actual_append_projects_exact_scalar_variant(payload, entity_id, expected):
    source = _record(payload, entity_id)
    projected = _project(source)
    detail = projected.detail
    assert (detail["control_mode"], detail["strategy_id"], detail["enabled"]) == expected
    assert projected.event["correlation_id"] == source.payload["correlation_id"]
    assert projected.event["causation_id"] == source.payload["causation_id"]
    assert detail["content_hash"] == sha256(canonical_json({
        key: value for key, value in detail.items() if key != "content_hash"
    }).encode("utf-8")).hexdigest()
    assert set(source.payload) - {"correlation_id", "causation_id"} == (
        {"event", "control_mode", "reason"} if entity_id == "primary" else
        {"event", "strategy_id", "enabled", "reason"})


@pytest.mark.parametrize("payload,entity_id", [
    ({"event": "portfolio_policy_selected", "policy": {"policy_id": "p"},
      "entries_paused": True, "reason": "operator"}, "primary"),
    ({"event": "control_changed", "control_mode": "enabled", "reason": "",
      "extra": 1}, "primary"),
    ({"event": "control_changed", "control_mode": "invalid", "reason": ""},
     "primary"),
    ({"event": "strategy_allocation_control_changed", "strategy_id": "s1",
      "enabled": 1, "reason": ""}, "primary:s1"),
    ({"event": "strategy_allocation_control_changed", "strategy_id": "s1",
      "enabled": True, "reason": ""}, "other:s1"),
])
def test_unmodeled_or_mismatched_control_fails_closed(payload, entity_id):
    with pytest.raises(ValueError):
        _project(_record(payload, entity_id))
