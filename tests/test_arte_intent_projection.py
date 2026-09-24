from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timezone
from decimal import Decimal
from math import nan

import pytest

from src.trading_runtime.arte_intent_projection import project_strategy_intent
from src.trading_runtime.execution_policies import (
    ExecutionEnvelope, ExecutionPolicy, ExecutionPolicyName,
    ProtectionProfile, ProtectionSlice, StopRule, StopRuleType,
    StructuralAnchor, TrailingRule, TrailingRuleType,
)
from src.trading_runtime.signals import CapitalRequest, StrategyIntent


def intent(**overrides):
    values = dict(
        intent_id="intent-1", ticker="test",
        event_time=datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc),
        action="enter_long", quantity=5.0, reference_price=12.5,
    )
    values.update(overrides)
    return StrategyIntent(**values)


def test_intent_projection_flattens_all_declared_policy_and_protection_fields():
    anchor = StructuralAnchor("level-1", 12.0,
                              datetime(2026, 8, 18, 8, 4, tzinfo=timezone.utc),
                              timeframe="1m", ordinal="latest")
    policy = ExecutionPolicy(
        policy_id="entry", revision=2, name=ExecutionPolicyName.ADAPTIVE_URGENT,
        envelope=ExecutionEnvelope(maximum_buy_price=12.75, deadline_ms=500,
                                   maximum_reprices=3, persist_until_cancelled=True),
    )
    profile = ProtectionProfile("stop", 3, (
        ProtectionSlice("first", 0.4,
                        StopRule(StopRuleType.SWING_ANCHORED, anchor=anchor),
                        profit_target_price=13.5,
                        trailing=TrailingRule(TrailingRuleType.BROKER_AMOUNT, amount=0.1)),
        ProtectionSlice("rest", 0.6, StopRule(price=11.5)),
    ))
    result = project_strategy_intent(intent(
        capital_request=CapitalRequest("fixed_quantity", 5, maximum_quantity=10),
        execution_policy=policy, protection_profile=profile,
        invalidation_price=11.5, profit_target_price=13.5,
    ))
    assert result.core["ticker"] == "TEST"
    assert result.core["capital_maximum_quantity"] == "10"
    assert result.core["execution_policy_name"] == "adaptive_urgent"
    assert result.core["execution_persist_until_cancelled"] == 1
    assert result.core["protection_slice_count"] == 2
    assert result.protection_slices[0]["anchor_observation_id"] == "level-1"
    assert result.protection_slices[0]["trailing_amount"] == "0.1"
    assert result.protection_slices[1]["stop_price"] == "11.5"
    assert all(not isinstance(value, (dict, list, tuple))
               for row in (result.core, *result.protection_slices)
               for value in row.values())


def test_intent_projection_rejects_unmapped_metadata_instead_of_dropping_it():
    with pytest.raises(ValueError, match="normalized typed contract"):
        project_strategy_intent(intent(metadata={"entry_body_trigger": {"price": 12.5}}))


def test_intent_projection_rejects_nonfinite_number():
    with pytest.raises(ValueError, match="non-finite"):
        project_strategy_intent(intent(reference_price=nan))


def test_intent_projection_rejects_unrepresentable_decimal():
    with pytest.raises(ValueError, match=r"Decimal\(38, 18\)"):
        project_strategy_intent(intent(reference_price=Decimal("1.1234567890123456789")))


def test_projection_field_inventory_fails_if_source_contract_expands():
    expected = {
        StrategyIntent: "intent_id ticker event_time action quantity reference_price schema_version "
                        "capital_request invalidation_price profit_target_price trailing_amount "
                        "execution_policy protection_profile urgency time_in_force outside_rth "
                        "reason metadata",
        CapitalRequest: "mode value minimum_quantity maximum_quantity allow_replacement",
        ExecutionPolicy: "policy_id revision name envelope partial_fill_policy quote_source",
        ExecutionEnvelope: "maximum_buy_price minimum_sell_price deadline_ms maximum_reprices "
                           "minimum_reprice_interval_ms persist_until_cancelled",
        ProtectionProfile: "profile_id revision slices add_policy profit_pocket_transition "
                           "mandatory_catastrophic_backstop emergency_repair_deadline_ms",
        ProtectionSlice: "slice_id quantity_fraction stop profit_target_price trailing "
                         "inherit_profit_target",
        StopRule: "rule_type order_type price distance_percent distance_bps maximum_cash_risk "
                  "volatility_multiple buffer_bps anchor stop_limit_offset_bps",
        TrailingRule: "rule_type amount percent volatility_multiple activation_gain_percent "
                      "breakeven_buffer_bps structural_timeframe",
        StructuralAnchor: "observation_id price confirmed_at timeframe ordinal",
    }
    for source, names in expected.items():
        assert {item.name for item in fields(source)} == set(names.split())
