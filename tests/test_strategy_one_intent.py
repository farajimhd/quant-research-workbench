"""Strategy 1 entry translates to normalized intent and protection columns."""
from datetime import date, datetime, timezone

import pytest

from src.trading_runtime.arte_intent_projection import project_strategy_intent
from src.trading_runtime.order_management import _mandatory_broker_target
from src.trading_runtime.strategy_one_intent import strategy_one_entry_intent
from src.trading_runtime.strategy_one_stateful import StrategyOneEntryProposal


def _proposal() -> StrategyOneEntryProposal:
    return StrategyOneEntryProposal(
        "assignment-1", "DU1", "AAA", 31_000, 30_000, 10.01, 9.89,
        12., "R4", .5, 30_000, "S1",
    )


def test_entry_intent_has_exact_typed_financial_and_protection_contract():
    proposal = _proposal()
    intent = strategy_one_entry_intent(proposal, session_date=date(2026, 8, 18))
    assert intent == strategy_one_entry_intent(proposal, session_date=date(2026, 8, 18))
    assert intent.event_time == datetime(2026, 8, 18, 8, 0, 31, tzinfo=timezone.utc)
    assert intent.metadata == {}
    assert intent.capital_request.mode == "mandate_fraction"
    assert intent.capital_request.value == pytest.approx(1 / 3)
    assert intent.execution_policy.name.value == "adaptive_urgent"
    assert intent.execution_policy.envelope.persist_until_cancelled
    assert intent.outside_rth
    projected = project_strategy_intent(intent)
    assert projected.core["capital_mode"] == "mandate_fraction"
    assert projected.core["execution_persist_until_cancelled"] == 1
    assert projected.core["invalidation_price"] == "9.890000000000000000"
    assert projected.core["profit_target_price"] == "12.000000000000000000"
    assert len(projected.protection_slices) == 1
    assert projected.protection_slices[0]["stop_price"] == "9.890000000000000000"
    assert projected.protection_slices[0]["profit_target_price"] == "12.000000000000000000"
    assert _mandatory_broker_target(intent)


def test_entry_intent_rejects_wrong_number_and_invalid_session():
    from dataclasses import replace

    with pytest.raises(ValueError, match="exact numbered proposal"):
        strategy_one_entry_intent(replace(_proposal(), strategy_number=2),
                                  session_date=date(2026, 8, 18))
    with pytest.raises(ValueError, match="exact numbered proposal"):
        strategy_one_entry_intent(_proposal(),
                                  session_date=datetime(2026, 8, 18, tzinfo=timezone.utc))


def test_claimed_full_target_profile_cannot_silently_lose_target_protection():
    from dataclasses import replace

    intent = strategy_one_entry_intent(_proposal(), session_date=date(2026, 8, 18))
    with pytest.raises(ValueError, match="differs"):
        _mandatory_broker_target(replace(intent, profit_target_price=12.5))
    assert not _mandatory_broker_target(replace(intent, protection_profile=None))
