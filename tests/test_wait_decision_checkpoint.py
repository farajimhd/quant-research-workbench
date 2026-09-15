import json
from dataclasses import replace
from datetime import date, datetime, timezone
from unittest.mock import Mock

import pytest

from src.trading_runtime.runtime import TradingRuntime, RunConfig, RunMode
from src.trading_runtime.signals import StrategyEvaluation, StrategySignal


def runtime():
    value = object.__new__(TradingRuntime)
    value.config = RunConfig(RunMode.BACKTEST, 'test', 1, ('account',), date(2026, 1, 1))
    value.run_id = 'test'
    value.journal = Mock()
    value._last_wait_decision_signatures = {}
    return value


def test_json_restart_retains_suppression_but_records_a_new_blocker():
    signal = StrategySignal(signal_id='one', signal_type='wait', ticker='TEST',
        event_time=datetime(2026, 1, 1, tzinfo=timezone.utc), action='wait', direction='neutral',
        score=0., confidence=1., reason='waiting', metadata={'liquidity_admission':{'failed':['current_spread']}})
    before = runtime()
    before._record_strategy_signals(StrategyEvaluation(signals=(signal,)), 'account')
    after = runtime()
    after.restore_wait_decisions(json.loads(json.dumps(before.checkpoint_wait_decisions())))
    after._record_strategy_signals(StrategyEvaluation(signals=(replace(signal, signal_id='two'),)), 'account')
    after.journal.append.assert_not_called()
    changed = replace(signal, signal_id='three', metadata={'liquidity_admission':{'failed':['current_trade_rate_10s']}})
    after._record_strategy_signals(StrategyEvaluation(signals=(changed,)), 'account')
    after.journal.append.assert_called_once()


def test_corrupt_checkpoint_does_not_replace_existing_state():
    value = runtime()
    value._last_wait_decision_signatures = {('account','TEST'):('wait','reason','watching',(),None,None,None)}
    prior = value.checkpoint_wait_decisions()
    bad = json.loads(json.dumps(prior));bad['entries'].append(bad['entries'][0])
    with pytest.raises(ValueError, match='entry'):value.restore_wait_decisions(bad)
    assert value.checkpoint_wait_decisions() == prior
