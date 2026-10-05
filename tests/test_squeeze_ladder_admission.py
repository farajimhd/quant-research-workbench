from dataclasses import replace
from datetime import date, timedelta

from src.trading_runtime.arte_oms_projection import freeze_oms_group
from src.trading_runtime.order_management import _ManagedOrderGroup, OrderManagementState
from src.trading_runtime.strategy_orders import StrategyOrderPlan
from src.trading_runtime.squeeze_ladder_admission import ladder_admission_lock
from tests.test_squeeze_ladder_protection import request


def snapshot(state, *, accepted=False, at=None):
    intent = request()
    if at is not None:
        intent = replace(intent, event_time=at)
    group = _ManagedOrderGroup("group-1", intent, "DU1", StrategyOrderPlan(()),
        state, intent.event_time, intent.event_time, [])
    if accepted:
        group.broker_order_ids = ["parent-1"]
        group.broker_order_roles = {"parent-1": "entry"}
    return freeze_oms_group(group)


def lock(*groups, session="premarket"):
    return ladder_admission_lock(tuple(groups), session_date=date(2026, 8, 18),
        session=session, account_id="DU1", ticker="TEST")


def test_cancelled_accepted_zero_fill_batch_still_consumes_session():
    assert lock(snapshot(OrderManagementState.CANCELLED, accepted=True)) == "accepted_batch_consumed_session"


def test_unknown_submission_blocks_without_inventing_acceptance():
    assert lock(snapshot(OrderManagementState.OUTCOME_UNKNOWN)) == "unresolved_submission"
    assert lock(snapshot(OrderManagementState.REJECTED)) is None


def test_premarket_acceptance_does_not_consume_afterhours():
    pm = snapshot(OrderManagementState.CANCELLED, accepted=True)
    assert lock(pm, session="afterhours") is None
    ah = snapshot(OrderManagementState.CANCELLED, accepted=True,
                  at=request().event_time + timedelta(hours=8))
    assert lock(pm, ah, session="afterhours") == "accepted_batch_consumed_session"
