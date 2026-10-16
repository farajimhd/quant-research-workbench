from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.trading_runtime.columnar_program_actor import AssignedProgramStrategy
from src.trading_runtime.ibkr_schema import Execution
from src.trading_runtime.order_management import ExecutionAttribution
from src.trading_runtime.strategy_engine import StrategyAssignment, StrategyPermissions, AssignmentStatus
from tests.test_columnar_program_coordinator import inputs


def actor_inputs():
    release, boundary, _, management = inputs(entry=True, add=True)
    assignment = StrategyAssignment("assignment", release.header["strategy_id"],
        release.header["strategy_number"], "account", "ABC", 1,
        AssignmentStatus.WATCHING, StrategyPermissions(enter=True, add=True), {})
    actor = AssignedProgramStrategy(release, (assignment,), {"assignment": "listing"})
    execution = Execution("execution", "ABC", "B", "client-order", boundary.boundary_at,
        int(boundary.boundary_at.timestamp() * 1000), 5., 10., "broker-order", "account", 1, 1.)
    lineage = ExecutionAttribution("account", "assignment", "intent", "enter_long", "entry")
    manager = SimpleNamespace(execution_attribution=lambda _: lineage)
    return actor, boundary, management, execution, manager


def test_risk_rejection_has_native_selection_reason_and_no_acquisition():
    from tests.test_configuration_execution_risk import contract
    from src.trading_runtime.oms_assignment_observation import AssignmentOrderObservation
    actor, boundary, _, _, _ = actor_inputs()
    actor = AssignedProgramStrategy(actor.program_release, actor.assignments(), actor._listings,
        execution_risk=contract(maximum_position_risk=100.))
    observation = AssignmentOrderObservation('account', 'assignment', 'ABC', boundary.boundary_at,
        False, False, (), (), 0.)
    selected = actor.select_from_orders(boundary, assignment_id='assignment', observation=observation)
    assert selected.proposals == ()
    assert selected.status == 'risk_rejected:maximum_position_risk_exceeded'


def test_unobserved_initial_price_is_an_explicit_consumed_wait_then_first_price_can_enter():
    from src.trading_runtime.oms_assignment_observation import AssignmentOrderObservation
    actor, boundary, _, _, _ = actor_inputs()
    missing = replace(boundary, close=None, initial_stop=None, eligible=False)
    observation = AssignmentOrderObservation('account', 'assignment', 'ABC', boundary.boundary_at,
        False, False, (), (), 0.)
    result = actor.select_from_orders(missing, assignment_id='assignment', observation=observation)
    assert result.status == 'awaiting_price_observation' and result.proposals == ()
    assert actor.select_from_orders(missing, assignment_id='assignment', observation=observation).status == 'already_consumed'
    assert not actor.execution_state.positions and not actor.trail_state._water
    at = boundary.boundary_at + timedelta(seconds=1)
    result = actor.select_from_orders(replace(boundary, boundary_at=at, available_at=at),
        assignment_id='assignment', observation=replace(observation, observed_at=at))
    assert result.status == 'selected' and result.proposals[0].stage == 'entry'


def test_missing_price_cannot_hide_a_filled_position():
    actor, boundary, _, execution, manager = actor_inputs()
    actor.on_broker_executions((execution,), manager)
    with pytest.raises(ValueError, match='Unobserved price'):
        actor.trail_state.observe(replace(boundary, close=None, eligible=False),
            actor.position_for('assignment'), minimum_age_seconds=0)


def test_actual_partial_fill_and_fee_choose_open_branch_and_age():
    actor, boundary, management, execution, manager = actor_inputs()
    assert actor.proposals(boundary, assignment_id="assignment", management=management)[0].stage == "entry"
    assert not actor.position_for("assignment").position_open
    actor.on_broker_executions((execution,), manager)
    position = actor.position_for("assignment")
    assert position.quantity == Decimal(5) and position.fees == Decimal(1)
    assert position.age_seconds(boundary.boundary_at) == 0
    # No guessed requested quantity or immediate add is admitted after a fill.
    assert actor.proposals(boundary, assignment_id="assignment", management=management) == ()
    at = boundary.boundary_at + timedelta(seconds=600)
    proposals = actor.proposals(replace(boundary, boundary_at=at, available_at=at, eligible=False),
        assignment_id="assignment", management=replace(management, observed_at=at))
    assert proposals and proposals[0].stage == "add" and proposals[0].requested_cash == 200


@pytest.mark.parametrize("change", [dict(account_id="other"), dict(assignment_id="other"),
    dict(action="add_short"), dict(role="entry", action="exit")])
def test_conflicting_assignment_or_role_does_not_create_a_position(change):
    actor, _, _, execution, _ = actor_inputs()
    lineage = ExecutionAttribution("account", "assignment", "intent", "enter_long", "entry")
    manager = SimpleNamespace(execution_attribution=lambda _: replace(lineage, **change))
    with pytest.raises(ValueError):
        actor.on_broker_executions((execution,), manager)
    assert not actor.execution_state.positions


def test_same_execution_prefix_rebuilds_positions_and_net_results():
    actor, boundary, _, execution, manager = actor_inputs()
    recovered, _, _, _, _ = actor_inputs()
    exit_execution = replace(execution, execution_id="exit", side="S", price=11., commission=1.,
        trade_time=boundary.boundary_at + timedelta(seconds=1))
    exit_manager = SimpleNamespace(execution_attribution=lambda _: ExecutionAttribution(
        "account", "assignment", "exit-intent", "enter_long", "profit_target"))
    for target in (actor, recovered):
        target.on_broker_executions((execution,), manager)
        target.on_broker_executions((exit_execution,), exit_manager)
    assert recovered.execution_state.finalized_positions() == actor.execution_state.finalized_positions()
    assert recovered.target_state.target_for(recovered.position_for("assignment")) is None
    position = recovered.position_for("assignment")
    assert not position.position_open and position.net_realized_pnl == Decimal(3)
    assert position.closed_positions == position.winning_positions == 1
