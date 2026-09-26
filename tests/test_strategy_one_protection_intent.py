"""Numbered protection intents use normalized scalar Portfolio/OMS inputs."""
from dataclasses import replace
from datetime import date

import pytest

from src.trading_runtime.arte_intent_projection import (
    project_strategy_intent, restore_strategy_intent,
)
from src.trading_runtime.strategy_engine import AssignmentStatus, StrategyPermissions
from src.trading_runtime.strategy_one_position import (
    ProtectionState, ProtectionTransition,
)
from src.trading_runtime.strategy_one_protection_intent import (
    strategy_one_protection_intents,
)
from src.trading_runtime.strategy_one_stateful import StrategyOneFinancialView


def financial():
    return StrategyOneFinancialView(
        "assignment-1", "DU1", "AAA", AssignmentStatus.MANAGING,
        StrategyPermissions(enter=True), 5., False, False, False, 1)


def transition():
    return ProtectionTransition(
        ProtectionState(31_000, 9.8, 11.),
        {"price": 9.8, "source": "completed_30s_bar_low"},
        {"price": 11., "ordinal": 3})


def previous():
    return ProtectionState(30_000, 9.6, 10.5)


def test_target_before_stop_is_typed_and_roundtrips_without_metadata():
    intents = strategy_one_protection_intents(
        previous(), transition(), financial(), session_date=date(2026, 8, 18),
        bid=10., ask=10.01)
    assert [intent.action for intent in intents] == [
        "replace_profit_target", "replace_protective_stop"]
    assert [intent.reason for intent in intents] == [
        "ordinal_resistance_target", "completed_30s_bar_low"]
    assert all(intent.metadata == {} and intent.quantity == 5.
               for intent in intents)
    assert all(restore_strategy_intent(project_strategy_intent(intent)) == intent
               for intent in intents)
    assert strategy_one_protection_intents(
        previous(), transition(), financial(), session_date=date(2026, 8, 18),
        bid=10., ask=10.01) == intents


def test_crossed_or_unowned_protection_is_rejected_before_portfolio():
    with pytest.raises(ValueError, match="not executable"):
        strategy_one_protection_intents(
            previous(),
            replace(transition(), state=replace(transition().state, stop=10.1),
                    stop_amendment={
                        "price": 10.1, "source": "completed_30s_bar_low"}),
            financial(), session_date=date(2026, 8, 18), bid=10., ask=10.01)
    with pytest.raises(ValueError, match="completed position authority"):
        strategy_one_protection_intents(
            previous(), transition(), replace(financial(), pending_exit=True),
            session_date=date(2026, 8, 18), bid=10., ask=10.01)
