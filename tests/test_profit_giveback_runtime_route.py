"""Prepared typed exit through real runtime dispatch; Portfolio/OMS mocked."""
import asyncio
from dataclasses import dataclass, replace
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.trading_runtime.runtime import TradingRuntime, RunMode
from src.trading_runtime.strategy_engine import StrategyEvaluation
from test_profit_giveback_journal_projection import values, RUN
from test_strategy_profit_giveback_exit import financial


def runtime_fixture(strategy_number=31):
    values_ = values(strategy_number=strategy_number)
    intent = values_['intent']
    held = financial()
    runtime = object.__new__(TradingRuntime)
    runtime.config = SimpleNamespace(mode=RunMode.BACKTEST,
        strategy_id='early-squeeze-strategy', strategy_revision=strategy_number,
        account_ids=(held.account_id,), anchor_date=date(2026, 8, 4))
    runtime.run_id = RUN
    runtime.journal = BacktestMemoryJournal(run_id=RUN, initial_sequence=9)
    runtime.intent_planner = object()
    @dataclass
    class Submitted:
        filled_quantity: float = 0.
    runtime.order_manager = SimpleNamespace(submit_intent=AsyncMock(
        return_value=Submitted()))
    decision = SimpleNamespace(payload=lambda: {'status': 'approved'})
    approved = replace(intent, metadata={'assignment_id': held.assignment_id})
    runtime.portfolio = SimpleNamespace(approve=AsyncMock(return_value=(decision, approved)),
                                        _typed_recovery=False)
    runtime.strategy = SimpleNamespace(assignments=lambda: ())
    runtime.last_event_time = intent.event_time
    return runtime, held, values_


def submit(runtime, held, values_):
    return runtime.submit_profit_giveback(held, values_['witness'],
        values_['source_entry_intent_id'], values_['arm_reference'])


@pytest.mark.parametrize('number', [31, 32, 33, 34, 35, 36, 37, 38])
def test_profit_exit_keeps_typed_witness_and_shared_assignment_admission(number):
    runtime, held, values_ = runtime_fixture(strategy_number=number)
    result = asyncio.run(submit(runtime, held, values_))
    assert result[0]['decision']['status'] == 'approved'
    runtime.portfolio.approve.assert_awaited_once_with(values_['intent'],
        account_id=held.account_id, assignment_id=held.assignment_id)
    approved = runtime.portfolio.approve.return_value[1]
    runtime.order_manager.submit_intent.assert_awaited_once_with(
        approved, account_id=held.account_id, event=None)
    records = runtime.journal.unfenced_records()
    assert len(records) == 1
    assert runtime.journal.profit_giveback_exit_for_record(records[0].record_id) == (
        values_['intent'], values_['witness'], values_['source_entry_intent_id'], values_['arm_reference'])
    assert runtime.journal.assignment_for_intent(values_['source_entry_intent_id']) == held.assignment_id
    runtime.journal.close()


@pytest.mark.parametrize('corruption', ['mode', 'revision', 'strategy', 'arm', 'unfenced', 'candidate'])
def test_invalid_authority_cannot_reach_portfolio_or_oms(corruption):
    runtime, held, values_ = runtime_fixture()
    if corruption == 'mode':
        runtime.config.mode = RunMode.REPLAY
    elif corruption == 'revision':
        runtime.config.strategy_revision = 30
    elif corruption == 'strategy':
        runtime.config.strategy_id = 'other'
    elif corruption == 'arm':
        values_['arm_reference'] = object()
    elif corruption == 'unfenced':
        runtime.journal = BacktestMemoryJournal(run_id=RUN, initial_sequence=6)
    else:
        values_['arm_reference'] = replace(values_['arm_reference'], candidate=replace(
            values_['arm_reference'].candidate, high_int=110001))
    with pytest.raises(ValueError):
        asyncio.run(submit(runtime, held, values_))
    runtime.portfolio.approve.assert_not_awaited()
    runtime.order_manager.submit_intent.assert_not_awaited()
    assert runtime.journal.pending_record_count == 0
    runtime.journal.close()


@pytest.mark.parametrize('number', [31, 32, 33, 34, 35, 36, 37, 38])
def test_ordinary_exit_route_cannot_bypass_profit_witness(number):
    runtime, held, values_ = runtime_fixture(strategy_number=number)
    with pytest.raises(ValueError, match='normalized witness'):
        asyncio.run(runtime._execute_intents(StrategyEvaluation(intents=(values_['intent'],)),
                                            held.account_id, None))
    runtime.portfolio.approve.assert_not_awaited()
    assert runtime.journal.pending_record_count == 0


def test_profit_exit_rejects_portfolio_assignment_loss_before_order():
    runtime, held, values_ = runtime_fixture()
    decision, approved = runtime.portfolio.approve.return_value
    runtime.portfolio.approve.return_value = decision, replace(approved, metadata={'assignment_id': 'other'})
    with pytest.raises(RuntimeError, match='lost its normalized assignment'):
        asyncio.run(submit(runtime, held, values_))
    runtime.order_manager.submit_intent.assert_not_awaited()


@pytest.mark.parametrize('number', [31, 32, 33, 34, 35, 36, 37, 38])
def test_prepared_successor_parent_loss_keeps_numbered_factory_and_shared_route(number):
    from src.trading_runtime.strategy_followthrough_exit import followthrough_exit_intent
    from src.trading_runtime.strategy_zero_regime_risk_failure import zero_regime_risk_failure
    from test_strategy_zero_regime_risk_failure import observation
    runtime, held, values_ = runtime_fixture(strategy_number=number)
    witness = zero_regime_risk_failure(observation())
    parent = followthrough_exit_intent(witness, held, session_date=runtime.config.anchor_date,
        source_entry_intent_id=values_['source_entry_intent_id'], strategy_number=number)
    decision, _ = runtime.portfolio.approve.return_value
    approved = replace(parent, metadata={'assignment_id': held.assignment_id})
    runtime.portfolio.approve.return_value = decision, approved
    asyncio.run(runtime.submit_followthrough_failure(held, witness, values_['source_entry_intent_id']))
    runtime.portfolio.approve.assert_awaited_once_with(parent, account_id=held.account_id,
                                                     assignment_id=held.assignment_id)
    runtime.order_manager.submit_intent.assert_awaited_once_with(approved,
        account_id=held.account_id, event=None)
    record, = runtime.journal.unfenced_records()
    assert runtime.journal.followthrough_exit_for_record(record.record_id) == (
        parent, witness, values_['source_entry_intent_id'])


def test_prepared_route_keeps_complete_parent_projection_certification():
    from src.backend.backtest_fixed_v4_certification import certify_numbered_fixed_v4_projection
    assert len(certify_numbered_fixed_v4_projection(30)) == 64
