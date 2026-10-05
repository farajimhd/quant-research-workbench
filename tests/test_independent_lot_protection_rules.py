from dataclasses import replace

import pytest

from src.trading_runtime.arte_intent_projection import project_strategy_intent, restore_strategy_intent
from src.trading_runtime.domain import InstrumentContract
from src.trading_runtime.execution_policies import (
    AddProtectionPolicy, ProtectionProfile, protection_profile_from_payload,
)
from src.trading_runtime.strategy_orders import IbkrStrategyOrderPlanner
from tests.test_squeeze_ladder_protection import request


def test_generic_four_lot_rule_survives_both_journals_and_plans_owned_brackets():
    original = request()
    prototype = original.protection_profile.slices[0]
    profile = ProtectionProfile("unrelated-rule-owner", 7, tuple(
        replace(prototype, slice_id=f"custom-{index}", quantity_fraction=.25,
                profit_target_price=11 + index)
        for index in range(4)), add_policy=AddProtectionPolicy.INDEPENDENT_FIXED_LOTS)
    intent = replace(original, protection_profile=profile, quantity=100)
    assert protection_profile_from_payload(profile.payload()) == profile
    assert restore_strategy_intent(project_strategy_intent(intent)) == intent
    plan = IbkrStrategyOrderPlanner().plan(account_id="DU1",
        instrument=InstrumentContract("TEST", 123, "TEST", "STK", "USD"),
        intent=intent, strategy_id="different-strategy", strategy_revision=7)
    assert len(plan.orders) == 12
    assert set(plan.order_slice_ids) == {f"custom-{index}" for index in range(4)}
    assert sum(order.quantity for order in plan.orders if order.side == "BUY") == 100
    assert len(plan.broker_batches) == 4


@pytest.mark.parametrize("change", [
    {"inherit_profit_target": True}, {"profit_target_price": None},
    {"profit_target_price": float("nan")}, {"profit_target_price": 9},
])
def test_incomplete_fixed_lot_rule_rejected_before_execution(change):
    profile = request().protection_profile
    with pytest.raises(ValueError, match="explicit fixed"):
        replace(profile, slices=(replace(profile.slices[0], **change), *profile.slices[1:]))


@pytest.mark.parametrize("action", ["enter_short", "add_long", "add_short"])
def test_unsupported_acquisition_cannot_submit_independent_lots(action):
    with pytest.raises(ValueError, match="require enter_long"):
        IbkrStrategyOrderPlanner().plan(account_id="DU1",
            instrument=InstrumentContract("TEST", 123, "TEST", "STK", "USD"),
            intent=replace(request(), action=action), strategy_id="test", strategy_revision=1)


def test_existing_profiles_keep_old_payload_and_dispatch_rule():
    profile = replace(request().protection_profile, add_policy=AddProtectionPolicy.INDEPENDENT_SLICE)
    assert profile.payload()["add_policy"] == "independent_slice"
    assert "reconciliation_mode" not in profile.payload()
    assert protection_profile_from_payload(profile.payload()) == profile
