"""Prepared exit exercises the real shared broker/OMS financial reader."""
import asyncio
from dataclasses import FrozenInstanceError, replace
from datetime import date
from types import SimpleNamespace

import pytest

from src.backend.backtest_strategy_liquidity_fade_decision import prepare_liquidity_fade_decision
from src.trading_runtime.strategy_engine import StrategyAssignment, StrategyPermissions, AssignmentStatus
from src.trading_runtime.order_management import OrderManagementState
from src.trading_runtime.strategy_one_contract import STRATEGY_ID
from test_arte_liquidity_fade_failure_v4 import prepared_case, IDENTITY


def case(quantity=100, groups=()):
    witness, financial, state, _ = prepared_case()
    assignment = StrategyAssignment(financial.assignment_id, STRATEGY_ID, 35,
        financial.account_id, financial.ticker, 123, AssignmentStatus.MANAGING,
        StrategyPermissions(), {})
    reads = []
    def exact(account, conid, ticker):
        reads.append((account, conid, ticker))
        return quantity
    broker = SimpleNamespace(position_quantity=exact, positions=lambda account: None)
    manager = SimpleNamespace(snapshots=lambda: groups,
                             snapshots_for_assignment=lambda account, assignment: groups)
    return witness, state, assignment, broker, manager, reads


def decide(data):
    witness, state, assignment, broker, manager, _ = data
    return asyncio.run(prepare_liquidity_fade_decision(witness, state, assignment, broker, manager,
        session_date=date(2026, 8, 10), source_entry_intent_id=IDENTITY))


def group(action='exit', state=OrderManagementState.WORKING):
    return SimpleNamespace(account_id='account', assignment_id='assignment', ticker='PLUG',
        group_id='group', action=action, state=state, filled_quantity=0, entry_submission_closed=False)


def test_exact_current_quantity_used_without_projected_account_or_cached_financial_view():
    data = case(quantity=37)
    def forbidden():
        pytest.fail('Indexed OMS financial read scanned every group')
    data[4].snapshots = forbidden
    decision = decide(data)
    assert decision.financial.position_quantity == decision.intent.quantity == 37
    assert data[-1] == [('account', 123, 'PLUG')]
    assert decision.intent.reason == 'strategy_thirty_five_liquidity_fade_failure'
    with pytest.raises(FrozenInstanceError):
        decision.financial = None
    # Every invocation re-reads the broker even at the same candidate boundary.
    decide(data)
    assert len(data[-1]) == 2


@pytest.mark.parametrize('quantity', [0, -1, True, float('nan'), float('inf')])
def test_invalid_or_no_long_broker_position_rejects(quantity):
    with pytest.raises((ValueError, TypeError)):
        decide(case(quantity=quantity))


@pytest.mark.parametrize('action', ['exit', 'exit_long', 'reduce_long'])
def test_actual_pending_exit_blocks_candidate(action):
    with pytest.raises(ValueError):
        decide(case(groups=(group(action=action),)))


def test_terminal_exit_does_not_block_a_new_current_position():
    decision = decide(case(groups=(group(state=OrderManagementState.CANCELLED),)))
    assert not decision.financial.pending_exit


def test_changed_broker_quantity_is_not_replaced_by_prior_financial_snapshot():
    data = case(quantity=37)
    assert decide(data).intent.quantity == 37
    data[3].position_quantity = lambda *args: 11
    assert decide(data).intent.quantity == 11


def test_pending_entry_is_retained_for_inherited_portfolio_cancellation():
    decision = decide(case(groups=(group(action='enter_long'),)))
    assert decision.financial.pending_entry
    assert not decision.financial.pending_exit


def test_wrong_manager_boundary_or_first_held_rejects():
    data = list(case())
    data[1] = replace(data[1], boundary_ms=data[0].boundary_ms-100)
    with pytest.raises(ValueError):
        decide(data)
    data = list(case())
    key, boundary = data[1].first_held_boundaries[0]
    data[1] = replace(data[1], first_held_boundaries=((key, boundary+100),))
    with pytest.raises(ValueError):
        decide(data)


def test_foreign_assignment_has_no_matching_native_manager_identity():
    data = list(case())
    data[2] = replace(data[2], assignment_id='foreign')
    with pytest.raises(ValueError):
        decide(data)


@pytest.mark.parametrize('changes', [dict(strategy_revision=34), dict(strategy_id='foreign'),
                                    dict(conid=True)])
def test_wrong_number_executor_or_identity_rejects_before_broker_read(changes):
    data = list(case())
    data[2] = replace(data[2], **changes)
    with pytest.raises(ValueError):
        decide(data)
    assert not data[-1]
