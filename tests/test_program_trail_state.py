from dataclasses import replace
from datetime import timedelta
from types import MappingProxyType

import pytest

from src.trading_runtime.oms_assignment_observation import AssignmentOrderObservation, ObservedWorkingOrder
from tests.test_columnar_program_actor import actor_inputs


def observation(at, *, covered=5., pending=False):
    return AssignmentOrderObservation("account", "assignment", "ABC", at, pending, False, (),
        (ObservedWorkingOrder("stop", "protective_stop", covered, 0., 9., "Submitted"),), 999.)


def test_quote_peak_does_not_advance_water_when_trail_rule_is_inactive():
    actor, boundary, _, execution, manager = actor_inputs()
    actor.on_broker_executions((execution,), manager)
    at = boundary.boundary_at + timedelta(seconds=1)
    inactive = replace(boundary, boundary_at=at, available_at=at, close=20.)
    assert actor.select_from_orders(inactive, assignment_id="assignment", observation=observation(at)).proposals == ()
    assert actor.trail_state.water_for(actor.position_for("assignment")) == 10
    at += timedelta(seconds=1)
    active = replace(inactive, boundary_at=at, available_at=at, close=11., rules=dict(inactive.rules, trail=True))
    selected = actor.select_from_orders(active, assignment_id="assignment", observation=observation(at))
    assert selected.status == "selected" and selected.proposals[0].stage == "trail"
    assert selected.proposals[0].stop_price == pytest.approx(9.9)
    assert actor.trail_state.water_for(actor.position_for("assignment")) == 11
    assert actor.select_from_orders(active, assignment_id="assignment", observation=observation(at)).status == "already_consumed"
    with pytest.raises(ValueError, match="conflicting or out of order"):
        actor.select_from_orders(replace(active, close=12.), assignment_id="assignment", observation=observation(at))


def test_pending_and_missing_protection_remain_explicit_without_fabricated_prices():
    actor, boundary, _, execution, manager = actor_inputs()
    actor.on_broker_executions((execution,), manager)
    at = boundary.boundary_at + timedelta(seconds=1)
    row = replace(boundary, boundary_at=at, available_at=at)
    selected = actor.select_from_orders(row, assignment_id="assignment", observation=observation(at, covered=0.))
    assert selected.status == "awaiting_protection_observation" and not selected.proposals
    at += timedelta(seconds=1)
    row = replace(row, boundary_at=at, available_at=at, rules=dict(row.rules, exit=True))
    pending = replace(observation(at, pending=True), working_orders=())
    selected = actor.select_from_orders(row, assignment_id="assignment", observation=pending)
    assert selected.proposals[0].action == "cancel_entry"


def test_replaying_same_fill_and_boundary_prefix_rebuilds_policy_water():
    actor, boundary, _, execution, manager = actor_inputs()
    recovered, _, _, _, _ = actor_inputs()
    at = boundary.boundary_at + timedelta(seconds=1)
    row = replace(boundary, boundary_at=at, available_at=at, close=11., rules=dict(boundary.rules, trail=True))
    for target in (actor, recovered):
        target.on_broker_executions((execution,), manager)
        target.select_from_orders(row, assignment_id="assignment", observation=observation(at))
    assert recovered.trail_state.water_for(recovered.position_for("assignment")) == actor.trail_state.water_for(actor.position_for("assignment"))


def test_trail_age_gate_and_future_fill_are_checked_before_policy_mutation():
    actor, boundary, _, execution, manager = actor_inputs()
    actor.program_release = replace(actor.program_release, minimum_age_seconds=MappingProxyType(
        dict(actor.program_release.minimum_age_seconds, trail=10)))
    actor.on_broker_executions((execution,), manager)
    at = boundary.boundary_at + timedelta(seconds=1)
    row = replace(boundary, boundary_at=at, available_at=at, close=20., rules=dict(boundary.rules, trail=True))
    actor.select_from_orders(row, assignment_id="assignment", observation=observation(at))
    assert actor.trail_state.water_for(actor.position_for("assignment")) == 10
    future = replace(execution, execution_id="future", trade_time=at + timedelta(seconds=1))
    actor.on_broker_executions((future,), manager)
    with pytest.raises(ValueError, match="future fill"):
        actor.select_from_orders(row, assignment_id="assignment", observation=observation(at))
    assert actor.trail_state.water_for(actor.position_for("assignment")) == 10
