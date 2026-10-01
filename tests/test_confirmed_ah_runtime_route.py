"""Real runtime dispatch with explicit Portfolio/OMS and constructed-runtime fixtures."""
import asyncio
from dataclasses import replace

import pytest

from test_profit_giveback_runtime_route import runtime_fixture
from test_arte_confirmed_ah_failure_v4 import prepared_case
from src.trading_runtime.runtime import RunMode
from src.trading_runtime.strategy_engine import StrategyEvaluation


def case():
    runtime, _, _ = runtime_fixture()
    witness, financial, args, intent, _ = prepared_case()
    runtime.config.strategy_revision = 34
    runtime.config.anchor_date = args['session_date']
    runtime.config.account_ids = (financial.account_id,)
    decision, _ = runtime.portfolio.approve.return_value
    runtime.portfolio.approve.return_value = decision, replace(intent, metadata={'assignment_id': financial.assignment_id})
    return runtime, witness, financial, args, intent


def test_complete_confirmation_reaches_shared_portfolio_and_oms():
    runtime, witness, financial, args, intent = case()
    try:
        result = asyncio.run(runtime.submit_confirmed_ah_failure(financial, witness, args['source_entry_intent_id']))
        assert result[0]['decision']['status'] == 'approved'
        runtime.portfolio.approve.assert_awaited_once_with(intent,
            account_id=financial.account_id, assignment_id=financial.assignment_id)
        approved = runtime.portfolio.approve.return_value[1]
        runtime.order_manager.submit_intent.assert_awaited_once_with(approved, account_id=financial.account_id, event=None)
        record = runtime.journal.unfenced_records()[0]
        assert runtime.journal.confirmed_ah_exit_for_record(record.record_id)[1] == witness
    finally:
        runtime.journal.close()


@pytest.mark.parametrize('change', ['mode', 'number', 'strategy', 'pending', 'forming'])
def test_invalid_confirmation_cannot_reach_portfolio_or_oms(change):
    runtime, witness, financial, args, _ = case()
    if change == 'mode': runtime.config.mode = RunMode.REPLAY
    elif change == 'number': runtime.config.strategy_revision = 33
    elif change == 'strategy': runtime.config.strategy_id = 'foreign'
    elif change == 'pending': financial = replace(financial, pending_exit=True)
    else: witness = replace(witness, completed_ten_second_boundary_ms=43_710_000)
    try:
        with pytest.raises(ValueError):
            asyncio.run(runtime.submit_confirmed_ah_failure(financial, witness, args['source_entry_intent_id']))
        runtime.portfolio.approve.assert_not_awaited()
        runtime.order_manager.submit_intent.assert_not_awaited()
        assert runtime.journal.pending_record_count == 0
    finally:
        runtime.journal.close()


def test_generic_intent_cannot_bypass_confirmation_witness():
    runtime, _, financial, _, intent = case()
    with pytest.raises(ValueError, match='normalized witness'):
        asyncio.run(runtime._execute_intents(StrategyEvaluation(intents=(intent,)), financial.account_id, None))
    runtime.portfolio.approve.assert_not_awaited()
    runtime.journal.close()
