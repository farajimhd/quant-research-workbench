from dataclasses import replace
from types import SimpleNamespace
import numpy as np
import pytest

from research.rl_trading.v7.config import Config
from research.rl_trading.v7.experiments.entry_outcomes import single_entry


def session():
    n = 2400
    execution = {k: np.full((n, 1), value, dtype=dtype) for k, value, dtype in (
        ('mark', 10., float), ('bid', 9.99, float), ('ask', 10.01, float),
        ('bid_size', 1000., float), ('ask_size', 1000., float),
        ('quote_valid', True, bool), ('quote_us', 0, np.int64))}
    execution['quote_us'][:, 0] = np.arange(n)*1000000
    selection = np.full((1200, 5), -1, np.int32)
    selection[:, 0] = 0
    return SimpleNamespace(day='2026-01-05', tickers=['A'], clocks=np.arange(n),
        selection=selection, features=np.zeros((1200, 5, 98), np.float32), execution=execution)


@pytest.mark.parametrize('scenario', ['target', 'stop', 'age', 'forced', 'partial', 'withdrawn', 'unliquidated'])
def test_acceleration_preserves_reference_fills_fees_and_payoff(scenario):
    s = session()
    cfg = replace(Config(), max_hold_seconds=60 if scenario != 'forced' else 10000)
    if scenario in ('target', 'stop'):
        s.execution['mark'][20:, 0] = 10.8 if scenario == 'target' else 9.
        s.execution['bid'][21:, 0] = s.execution['mark'][21:, 0]-.01
    if scenario == 'partial':
        s.execution['bid_size'][:, 0] = 100
        s.execution['quote_us'][:, 0] = (np.arange(len(s.clocks))//10)*1000000
    if scenario == 'withdrawn':
        s.execution['quote_valid'][61:90, 0] = False
    if scenario == 'unliquidated':
        s.execution['quote_valid'][2:, 0] = False
    fast = single_entry(s, 0, 0, cfg)
    reference = single_entry(s, 0, 0, cfg, accelerated=False)
    assert fast == reference
    assert fast['status'] == ('unliquidated' if scenario == 'unliquidated' else 'closed')
    if scenario == 'unliquidated':
        assert fast['net_dollars'] is None


def test_current_ineligibility_and_next_second_nonfill_are_distinct():
    s = session()
    assert single_entry(s, 0, 1)['status'] == 'ineligible_now'
    s.execution['quote_valid'][1, 0] = False
    result = single_entry(s, 0, 0)
    assert result == dict(status='unfilled', net_dollars=0., fills=[])


def test_future_data_does_not_change_current_admission():
    s = session()
    s.selection[:, 0] = -1
    assert single_entry(s, 0, 0)['status'] == 'ineligible_now'
    s.execution['mark'][1:, 0] = 1000.
    assert single_entry(s, 0, 0)['status'] == 'ineligible_now'


def test_invalid_mark_after_early_exit_does_not_reject_closed_outcome():
    s = session()
    s.execution['mark'][20:30, 0] = 11.
    s.execution['mark'][30:, 0] = np.nan
    assert single_entry(s, 0, 0) == single_entry(s, 0, 0, accelerated=False)
