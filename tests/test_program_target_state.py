from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

from src.trading_runtime.order_management import ExecutionAttribution
from tests.test_columnar_program_actor import actor_inputs


def test_targets_follow_actual_initial_fills_and_advance_once_per_target_intent():
    actor, boundary, _, first, manager = actor_inputs()
    recovered, _, _, _, _ = actor_inputs()
    def fill(target, execution, action, intent, role="entry", reason=""):
        source = SimpleNamespace(execution_attribution=lambda _: ExecutionAttribution(
            "account", "assignment", intent, action, role, reason))
        target.on_broker_executions((execution,), source)
    second = replace(first, execution_id="partial-two", price=12.,
        trade_time=boundary.boundary_at + timedelta(seconds=1))
    add = replace(second, execution_id="add", price=13., size=2.,
        trade_time=boundary.boundary_at + timedelta(seconds=2))
    reduction = replace(add, execution_id="target-one", side="S", size=1., price=20.,
        trade_time=boundary.boundary_at + timedelta(seconds=3))
    partial = replace(reduction, execution_id="target-two", trade_time=reduction.trade_time + timedelta(seconds=1))
    ordinary = replace(partial, execution_id="ordinary", trade_time=partial.trade_time + timedelta(seconds=1))
    for target in (actor, recovered):
        fill(target, first, "enter_long", "intent")
        assert target.target_state.target_for(target.position_for("assignment")) == Decimal("12")
        fill(target, second, "enter_long", "intent")
        anchor = target.target_state.target_for(target.position_for("assignment"))
        assert anchor == Decimal("13.2")  # actual initial weighted average 11
        fill(target, add, "add_long", "add-intent")
        assert target.target_state.target_for(target.position_for("assignment")) == anchor
        basis = target.position_for("assignment").average_price
        fill(target, reduction, "reduce_long", "target-intent", "managed_exit", "program:target")
        advanced = target.target_state.target_for(target.position_for("assignment"))
        from decimal import localcontext
        with localcontext() as context:
            context.prec = 80
            assert advanced == anchor + basis * Decimal(".2")
        fill(target, partial, "reduce_long", "target-intent", "managed_exit", "program:target")
        assert target.target_state.target_for(target.position_for("assignment")) == advanced
        # Duplicate delivery changes neither execution fees nor target progress.
        fill(target, partial, "reduce_long", "target-intent", "managed_exit", "program:target")
        assert target.target_state.target_for(target.position_for("assignment")) == advanced
        fill(target, ordinary, "reduce_long", "ordinary-intent", "managed_exit", "program:reduce")
        assert target.target_state.target_for(target.position_for("assignment")) == advanced
    assert recovered.position_for("assignment") == actor.position_for("assignment")
    assert recovered.target_state.target_for(recovered.position_for("assignment")) == advanced
