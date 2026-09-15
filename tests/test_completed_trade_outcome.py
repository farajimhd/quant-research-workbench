from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from types import SimpleNamespace

import pytest

from src.trading_runtime.completed_trade_outcome import completed_trade_outcome
from src.trading_runtime.domain import Execution, InstrumentContract
from src.trading_runtime.runtime import TradingRuntime
from src.trading_runtime.strategy_engine import StrategyObservation
from src.trading_runtime.v7_setup import recovery_observe


START = datetime(2026, 1, 1, tzinfo=timezone.utc)
INSTRUMENT = InstrumentContract('test', 1, 'TEST', 'STK', 'USD')


def fill(number, seconds, side, quantity, price):
    return Execution(str(number), 'account', INSTRUMENT, side, Decimal(str(quantity)),
        Decimal(str(price)), START+timedelta(seconds=seconds), commission=Decimal('1'),
        commission_currency='USD', commission_status='final', run_id='run')


def outcome(rows, seconds=5):
    return completed_trade_outcome(rows, account_id='account', conid=1, run_id='run',
        entry_at=START.timestamp(), observed_at=START+timedelta(seconds=seconds),
        confirmation_seconds=1)


def test_scale_ins_and_partial_exits_use_whole_episode_net():
    rows = [fill(1, .1, 'BUY', 10, 10), fill(2, 1, 'BUY', 10, 12),
        fill(3, 2, 'SELL', 5, 13), fill(4, 3, 'SELL', 15, 10)]
    result = outcome(rows)
    assert result['status'] == 'verified'
    assert (result['gross_pnl'], result['fees'], result['net_pnl']) == (-5., 4., -9.)
    assert len(result['execution_ids']) == 4
    # The future final fill cannot retrospectively close the current position.
    assert outcome(rows, seconds=2)['status'] == 'unavailable'


@pytest.mark.parametrize('change', [dict(commission=None), dict(commission_currency='EUR'),
    dict(commission_status='pending'), dict(run_id='other')])
def test_missing_fees_or_wrong_run_never_prove_loss(change):
    rows = [fill(1, .1, 'BUY', 10, 10), replace(fill(2, 2, 'SELL', 10, 9), **change)]
    assert outcome(rows)['status'] == 'unavailable'


def test_other_accounts_and_contracts_are_excluded():
    rows = [fill(1, .1, 'BUY', 10, 10), fill(2, 2, 'SELL', 10, 11)]
    rows += [replace(rows[0], account_id='other'),
        replace(rows[1], instrument=replace(INSTRUMENT, conid=2))]
    assert outcome(rows)['net_pnl'] == 8.
    with pytest.raises(ValueError, match='Duplicate'):
        outcome(rows+rows[:1])


def test_runtime_binds_evidence_to_held_entry_and_checkpoints_flat_transition():
    rows = [fill(1, .1, 'BUY', 10, 10), fill(2, 2, 'SELL', 10, 9)]
    state = dict(held=dict(entry_at=START.timestamp(), setup={}))
    assignment = SimpleNamespace(account_id='account', ticker='TEST', conid=1,
        parameters={'historical_hod': {'setup_below_vwap_base_enabled': 1}},
        state={'v7_setup': state})
    runtime = object.__new__(TradingRuntime)
    runtime.run_id = 'run'
    runtime.strategy = SimpleNamespace(assignments=lambda: [assignment])
    runtime._canonical_session = SimpleNamespace(projector=SimpleNamespace(
        executions={e.execution_id: e for e in rows}))
    observation = StrategyObservation('TEST', START+timedelta(seconds=3), 9.)
    observed = runtime._with_completed_trade_outcome(observation, 'account')
    assert observed.completed_trade_outcome['net_pnl'] == -12.
    recovery_observe(state, None, {}, observed, 0., {}, False)
    restored = json.loads(json.dumps(state))
    assert restored['last_exit']['completed_trade_outcome']['net_pnl'] == -12.
    assert 'held' not in restored
    # Supplied market metadata is not canonical evidence, including other accounts.
    assert runtime._with_completed_trade_outcome(observed, 'other').completed_trade_outcome is None
    assert runtime._with_completed_trade_outcome(observed, 'account').completed_trade_outcome is None
