from dataclasses import replace
from datetime import date, timedelta

import pytest

from src.backend.backtest_squeeze_ladder_admission import admit_ladder_proposal
from src.trading_runtime.arte_intent_projection import project_strategy_intent, restore_strategy_intent
from src.trading_runtime.domain import InstrumentContract
from src.trading_runtime.order_management import OrderManagementState
from src.trading_runtime.strategy_engine import AssignmentStatus, StrategyPermissions
from src.trading_runtime.strategy_one_stateful import StrategyOneFinancialView
from src.trading_runtime.strategy_orders import IbkrStrategyOrderPlanner
from tests.test_backtest_squeeze_ladder_entry import prepared, decide
from tests.test_squeeze_ladder_admission import snapshot


DAY = date(2026,8,18)


def financial():
    return StrategyOneFinancialView('assignment-1', 'DU1', 'TEST', AssignmentStatus.WATCHING,
                                   StrategyPermissions(enter=True), 0., False, False, False, 0)


def proposal():
    observed, setup, v7 = prepared()
    return decide(observed, setup, v7)


def test_one_aggregate_capital_request_roundtrips_and_only_approved_total_splits():
    decision = proposal()
    result = admit_ladder_proposal(decision, financial(), session_date=DAY, groups=())
    assert result.reason == 'capital_request_proposed'
    intent = result.intent
    assert intent.quantity == 0
    assert intent.capital_request.mode == 'mandate_fraction'
    assert intent.capital_request.value == 1/3
    assert not intent.capital_request.allow_replacement
    assert intent.metadata == {}
    restored = restore_strategy_intent(project_strategy_intent(intent))
    assert restored.protection_profile == intent.protection_profile
    assert restored.capital_request == intent.capital_request
    approved = replace(restored, quantity=101.)
    plan = IbkrStrategyOrderPlanner().plan(account_id='DU1',
        instrument=InstrumentContract('TEST',123,'TEST','STK','USD'),
        intent=approved, strategy_id='prepared-ladder', strategy_revision=1)
    assert [batch[0].quantity for batch in plan.broker_batches] == [34,34,33]
    assert len(plan.orders) == 9
    assert len({batch[0].cOID for batch in plan.broker_batches}) == 3
    assert all(order.outsideRTH for order in plan.orders)
    assert intent.intent_id == admit_ladder_proposal(decision, financial(), session_date=DAY, groups=()).intent.intent_id


@pytest.mark.parametrize('changed,reason', [
    (dict(position_quantity=1.), 'position_requires_management'),
    (dict(pending_entry=True), 'entry_fill_pending'),
    (dict(pending_exit=True), 'exit_fill_pending'),
    (dict(pending_capital_request=True), 'entry_fill_pending'),
    (dict(permissions=StrategyPermissions()), 'entry_permission_closed'),
    (dict(status=AssignmentStatus.PAUSED), 'entry_permission_closed'),
])
def test_financial_state_cannot_bypass_permissions_and_pending_orders(changed, reason):
    result = admit_ladder_proposal(proposal(), replace(financial(), **changed), session_date=DAY, groups=())
    assert result.reason == reason
    assert result.intent is None


def test_accepted_zero_fill_locks_and_future_snapshot_is_error():
    decision = proposal()
    at = admit_ladder_proposal(decision, financial(), session_date=DAY, groups=()).intent.event_time
    accepted = snapshot(OrderManagementState.CANCELLED, accepted=True, at=at - timedelta(seconds=1))
    result = admit_ladder_proposal(decision, financial(), session_date=DAY, groups=(accepted,))
    assert result.reason == 'accepted_batch_consumed_session'
    assert result.intent is None
    with pytest.raises(ValueError, match='future'):
        admit_ladder_proposal(decision, financial(), session_date=DAY,
                              groups=(replace(accepted, updated_at=at + timedelta(milliseconds=100)),))
