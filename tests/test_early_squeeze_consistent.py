from copy import deepcopy
from dataclasses import replace

import pytest

from src.trading_runtime import early_squeeze_consistent as C, strategy_engine as S
from tests.test_early_squeeze_price_episode import dual_macd_fixture
from tests.test_vwap_resistance_ladder import advance


def fixture():
    h, a, trade, one, tenth = dual_macd_fixture()
    a = replace(a, parameters={**a.parameters, 'early_squeeze_breakout_contract': C.CONTRACT})
    a = advance(a, h.evaluate(a, trade()))
    for k in range(1, 17):
        a = advance(a, h.evaluate(a, one(k, 10.39, .01, .005)))
        a = advance(a, h.evaluate(a, tenth(k+.005, 10.39)))
        a = advance(a, h.evaluate(a, trade(k+.01, 10.39)))
    return h, a, trade, one, tenth


def entered():
    h, a, trade, one, tenth = fixture()
    a = advance(a, h.evaluate(a, one(17, 10.44, .02, .01)))
    result = h.evaluate(a, trade(17.01, 10.44))
    assert any(i.action == 'enter_long' for i in result.evaluation.intents)
    a = advance(a, result)
    a.state['squeeze_entry']['slice_notional'] = 1000.
    return h, a, trade, one, tenth


def test_entry_requires_completed_1s_and_actual_next_open():
    h, a, trade, one, _ = fixture()
    result = h.evaluate(a, trade(16.2, 10.44))
    assert not result.evaluation.intents
    a = advance(a, result)
    result = h.evaluate(a, one(17, 10.44, .02, .01))
    assert not result.evaluation.intents
    a = advance(a, result)
    result = h.evaluate(a, trade(17.01, 10.44))
    intent = next(i for i in result.evaluation.intents if i.action == 'enter_long')
    proof = intent.metadata['resistance_confirmation']
    assert proof['previous_close'] <= proof['threshold'] < proof['close']
    assert proof['candle_open'] < proof['close']
    assert proof['closed_at'] < proof['opened_at']
    assert proof['next_open'] > proof['threshold']


@pytest.mark.parametrize('kind', ['red','opens_below','quiet_gap','moved_level'])
def test_invalid_acceptance_never_executes(kind):
    h, a, trade, one, _ = fixture()
    close = one(17, 10.44, .02, .01)
    if kind == 'red':
        close = replace(close, bar_open=10.45)
    if kind == 'moved_level':
        a.state['squeeze_breakout']['resistance_1s']['levels']['R2']['lower'] += .01
    a = advance(a, h.evaluate(a, close))
    opening = trade(18.01 if kind == 'quiet_gap' else 17.01, 10.40 if kind == 'opens_below' else 10.44)
    result = h.evaluate(a, opening)
    assert not any(i.action == 'enter_long' for i in result.evaluation.intents)


def test_trade_spike_cannot_ratchet_stop_or_reset_completed_episode():
    h, a, trade, one, _ = entered()
    stop = a.state['active_stop']
    episode = a.state['squeeze_breakout']['macd_1s']['episode_id']
    result = h.evaluate(a, trade(17.2, 10.59, 100.))
    assert result.state['active_stop'] == stop
    assert result.state['squeeze_breakout']['macd_1s']['episode_id'] == episode
    a = advance(a, result)
    a = advance(a, h.evaluate(a, replace(one(18, 10.47, .03, .01), position_quantity=100.)))
    result = h.evaluate(a, trade(18.01, 10.48, 100.))
    updates = [i for i in result.evaluation.intents if i.action == 'replace_protective_stop']
    assert len(updates) == 1
    assert updates[0].metadata['trail_close'] == 10.47
    assert updates[0].invalidation_price < 10.4


def test_add_and_break_count_and_ceiling_share_event():
    h, a, trade, one, tenth = entered()
    # Wider prior volatility permits this extension without curve-fitting a symbol.
    for k in range(18, 33):
        o = replace(one(k, 10.59, .03, .01), bar_high=10.69, bar_low=10.49, position_quantity=100.)
        a = advance(a, h.evaluate(a, o))
        a = advance(a, h.evaluate(a, tenth(k+.005, 10.59, position=100.)))
        a = advance(a, h.evaluate(a, trade(k+.01, 10.59, 100.)))
    a = advance(a, h.evaluate(a, replace(one(33, 10.615, .04, .02), position_quantity=100.)))
    result = h.evaluate(a, trade(33.01, 10.615, 100.))
    adds = [i for i in result.evaluation.intents if i.action == 'add_long']
    assert len(adds) == 1
    proof = adds[0].metadata['resistance_confirmation']
    assert proof['threshold'] < proof['close'] < proof['level']['upper']
    d = result.state['squeeze_breakout']
    assert 'R3' in d['macd_1s']['broken_levels']
    assert 'R3' in d['resistance_1s']['accepted']
    assert adds[0].intent_id in d['midpoint_add_requests']
    assert C.ceiling(d, d['resistance_1s']['levels'], result.state['active_stop'])['unified_level_id'] != 'R3'


def test_unavailable_100ms_gate_does_not_become_delayed_entry():
    h, a, trade, one, tenth = fixture()
    a = advance(a, h.evaluate(a, tenth(16.5, 10.39, .0, .1)))
    a = advance(a, h.evaluate(a, one(17, 10.44, .02, .01)))
    result = h.evaluate(a, trade(17.01, 10.44))
    assert result.evaluation.signals[0].reason == 'macd_100ms_line_above_signal_required'
    a = advance(a, result)
    a = advance(a, h.evaluate(a, tenth(17.1, 10.44)))
    assert not h.evaluate(a, trade(17.11, 10.44)).evaluation.intents


def test_purchase_window_rejects_chase_and_spread_relative_to_risk():
    _, _, trade, _, _ = fixture()
    o = trade(20.01, 10.5)
    event = dict(opened_at=o.observed_at.timestamp(), threshold=10.41, level=dict(lower=10.4, upper=10.42))
    d = dict(decision_volatility=dict(value=.02))
    assert not C.purchase_window(event, o, d, 10.3, .01)
    o = replace(o, price=10.43, ask=10.44, bid=10.30)
    assert not C.purchase_window(event, o, d, 10.29, .01)


def test_detector_support_role_alone_cannot_clear_trailing_ceiling():
    row = dict(unified_level_id='R', lower=10.4, upper=10.5, role='resistance')
    d = dict(resistance_1s=dict(catalog={'R': row}, accepted={}))
    assert C.ceiling(d, {'R':dict(row, role='support')}, 10.3)['unified_level_id'] == 'R'
    d['resistance_1s']['accepted']['R'] = dict(level=row, threshold=10.45)
    assert C.ceiling(d, {'R':dict(row, role='support')}, 10.3) is None


def test_v23_stop_does_not_trail_between_resistances():
    h, a, trade, one, _ = entered()
    a = replace(a, parameters={**a.parameters, 'early_squeeze_breakout_contract': C.STRUCTURAL_CONTRACT})
    stop = a.state['active_stop']
    for o in [trade(17.2, 10.59, 100.), replace(one(18, 10.49, .03, .01), position_quantity=100.),
              trade(18.01, 10.49, 100.)]:
        result = h.evaluate(a, o)
        assert result.state['active_stop'] == stop
        assert not any(i.action == 'replace_protective_stop' for i in result.evaluation.intents)
        a = advance(a, result)


def test_v23_higher_resistance_add_and_stop_use_identical_confirmation():
    h, a, trade, one, tenth = entered()
    a = replace(a, parameters={**a.parameters, 'early_squeeze_breakout_contract': C.STRUCTURAL_CONTRACT})
    for k in range(18, 33):
        a = advance(a, h.evaluate(a, replace(one(k, 10.59, .03, .01),
            bar_high=10.69, bar_low=10.49, position_quantity=100.)))
        a = advance(a, h.evaluate(a, tenth(k+.005, 10.59, position=100.)))
        a = advance(a, h.evaluate(a, trade(k+.01, 10.59, 100.)))
    a = advance(a, h.evaluate(a, replace(one(33, 10.65, .04, .02), position_quantity=100.)))
    result = h.evaluate(a, trade(33.01, 10.65, 100.))
    add = next(i for i in result.evaluation.intents if i.action == 'add_long')
    stop = next(i for i in result.evaluation.intents if i.action == 'replace_protective_stop')
    assert add.metadata['resistance_confirmation'] == stop.metadata['resistance_confirmation']
    assert stop.invalidation_price < stop.metadata['resistance_confirmation']['level']['lower']


def test_v23_failed_open_can_reclaim_on_next_green_candle():
    _, _, trade, one, _ = fixture()
    level = dict(unified_level_id='R', lower=10.4, upper=10.42, role='resistance')
    rows = {'R':level}
    d = {}
    C.observe(d, one(1, 10.39, .01, .005), rows, True, False, reclaim=True)
    C.observe(d, one(2, 10.44, .02, .005), rows, True, False, reclaim=True)
    assert not C.observe(d, trade(2.01, 10.40), rows, True, True, reclaim=True)[0]
    C.observe(d, replace(one(3, 10.45, .03, .005), bar_open=10.40), rows, True, False, reclaim=True)
    events = C.observe(d, trade(3.01, 10.45), rows, True, True, reclaim=True)[0]
    assert len(events) == 1
    assert events[0]['previous_close'] > events[0]['threshold']
    assert events[0]['candle_open'] <= events[0]['threshold'] < events[0]['close']


def test_late_exit_callback_preserves_new_unfilled_entry():
    from src.trading_runtime import early_squeeze_breakout as E
    state = dict(squeeze_entry=dict(requested_at=10.))
    E.record_exit(state, None, 'protective_stop', 0., contract=C.STRUCTURAL_CONTRACT)
    assert state['squeeze_entry']['requested_at'] == 10.
    state['squeeze_entry']['first_fill_at'] = 11.
    E.record_exit(state, None, 'protective_stop', 0., contract=C.STRUCTURAL_CONTRACT)
    assert 'squeeze_entry' not in state
