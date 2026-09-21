from copy import deepcopy
from dataclasses import replace

import pytest

from src.market_engine.structural_detector import VERSION
from src.trading_runtime import early_squeeze_momentum as M
from src.trading_runtime.vwap_resistance_ladder import POLICY
from tests.test_early_squeeze_consistent import fixture


def level(key, lower, upper, role='resistance'):
    return dict(unified_level_id=key, lower=lower, upper=upper, role=role,
        input_policy=POLICY, seed_input_policy=POLICY)


def detector(now, *pivots):
    return dict(contract=VERSION, effective_at=now, local_swings=list(pivots))


def pivot(price, occurred, confirmed, side='support'):
    return dict(price=price, lower=price, upper=price, pivot_at=occurred,
        confirmed_at=confirmed, side=side)


def test_gap_uses_activation_book_and_excludes_entry_distance():
    rows = {r['unified_level_id']: r for r in [level('a', 11, 11),
        level('b', 13, 13), level('c', 40, 40), level('d', 41, 41),
        level('s', 15, 15, 'support')]}
    result = M.freeze_gap(rows, 10, 100)
    assert result['gaps'] == [2, 27]
    assert result['average'] == 14.5
    rows['a']['lower'] = 12
    assert result['levels'][0]['lower'] == 11
    assert M.freeze_gap({'a': rows['a']}, 10, 100)['average'] is None


@pytest.mark.parametrize('cutoff,last,valid', [(100, 100, True), (99, 98, True),
    (98.99, 98, False), (100.1, 100, False), (100, 100.1, False)])
def test_causal_v7_freshness(cutoff, last, valid):
    rows = {'a': level('a', 10, 11)}
    assert M.fresh_structure(rows, dict(as_of=cutoff, max_input_timestamp=last), 100) is valid
    rows['a']['seed_input_policy'] = 'retrospective'
    assert not M.fresh_structure(rows, dict(as_of=cutoff, max_input_timestamp=last), 100)


def test_swing_age_is_pivot_age_and_future_confirmation_is_rejected():
    rows = {'s': level('s', 9.8, 10.0, 'support')}
    low = pivot(9.9, 91, 95)
    result = M.supported_swing(detector(100, low), rows, 100)
    assert result['pivot'] == low
    assert M.supported_swing(detector(100, pivot(9.9, 89, 99)), rows, 100) is None
    assert M.supported_swing(detector(100, pivot(9.9, 99, 101)), rows, 100) is None
    assert M.supported_swing(detector(101, low), rows, 100) is None
    assert M.supported_swing(detector(100, pivot(9.7, 91, 95)), rows, 100) is None


def test_stop_fallbacks_and_below_lower_band():
    rows = {'s': level('s', 9.94, 9.96, 'support')}
    stop = M.initial_stop(detector(100, pivot(9.95, 95, 99)), rows, 100,
        10.05, 10, .01, distance_reference='vwap')
    assert stop['price'] == 9.94 and stop['reason'] == 'supported_swing_low_stop'
    stop = M.initial_stop(detector(100), rows, 100, 10.05, 10, .01, distance_reference='vwap')
    assert stop['price'] == 9.93 and stop['reason'] == 'below_vwap_support_stop'
    stop = M.initial_stop(detector(100), {}, 100, 10, 9.99, .01, distance_reference='entry')
    assert stop['price'] == 9.9 and stop['reason'] == 'one_percent_entry_stop'


def test_bos_uses_confirmed_high_not_resistance_and_latches_for_vwap():
    state = {}
    high = pivot(10.5, 95, 99, 'resistance')
    assert not M.observe_bos(state, detector(100, high), 100, 10.4, True, True).get('open')
    gate = M.observe_bos(state, detector(100, high), 100.1, 10.51, True, True)
    assert gate['open'] and gate['broken_pivot'] == high
    assert M.observe_bos(state, detector(100, high), 100.2, 10.6, True, True)['open']
    # Learning of a high after price was already above it is not a break.
    state = {}
    M.observe_bos(state, detector(100, high), 100, 10.6, True, True)
    assert not M.observe_bos(state, detector(100, high), 100.1, 10.7, True, True).get('open')


def event(key, at):
    return dict(level=level(key, 10, 11), opened_at=at, threshold=10.5)


def test_fast_triples_are_strictly_less_than_three_seconds_disjoint_and_capped():
    active = {}
    assert not M.record_breaks(active, [event('a', 100), event('b', 101), event('c', 103)])
    upgrades = M.record_breaks(active, [event('d', 103.1)])
    assert upgrades[0]['multiplier'] == 8
    assert not M.record_breaks(active, [event('e', 103.2), event('f', 103.3)])
    assert M.record_breaks(active, [event('g', 103.4)])[0]['multiplier'] == 10
    assert not M.record_breaks(active, [event('h', 104), event('i', 104.1), event('j', 104.2)])
    assert active['target_multiplier'] == 10
    count = len(active['broken_levels'])
    M.record_breaks(active, [event('a', 105)])
    assert len(active['broken_levels']) == count


def test_stop_advances_one_level_per_three_and_never_between():
    active = dict(stop=9.8, broken_levels=['a', 'b'])
    rows = {'r1': level('r1', 10, 10.1), 'r2': level('r2', 10.2, 10.3)}
    assert M.next_stop(active, rows, .01)['price'] == 9.8
    active['broken_levels'].append('c')
    first = M.next_stop(active, rows, .01)
    assert first['price'] == 9.99 and first['steps'] == 1
    active.update(stop=first['price'], stop_steps=1, stop_anchor_lower=10)
    assert M.next_stop(active, rows, .01)['price'] == 9.99
    active['broken_levels'] += ['d', 'e', 'f']
    assert M.next_stop(active, rows, .01)['price'] == 10.19


def test_target_uses_each_actual_fill_price():
    assert M.target_price(10.03, .2, 5, .01) == 11.03
    assert M.target_price(10.27, .2, 8, .01) == 11.87
    assert M.target_price(10.27, .2, 10, .01) == 12.27


@pytest.mark.parametrize('kind', ['valid', 'above_open', 'late', 'moved'])
def test_addition_acceptance_is_same_candle_cross_and_immediate_next_second(kind):
    _, _, trade, one, _ = fixture()
    row = level('R', 10.4, 10.42)
    rows = {'R': row}
    state = {}
    M.observe_resistances(state, one(1, 10.39, .01, .005), rows, True, False)
    candle = replace(one(2, 10.44, .02, .005), bar_open=10.42 if kind == 'above_open' else 10.4)
    M.observe_resistances(state, candle, rows, True, False)
    if kind == 'moved':
        rows = deepcopy(rows)
        rows['R']['lower'] += .01
    events, _, _ = M.observe_resistances(state,
        trade(3.01 if kind == 'late' else 2.01, 10.44), rows, True, True)
    assert bool(events) is (kind == 'valid')
