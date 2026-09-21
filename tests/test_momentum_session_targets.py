from copy import deepcopy
from dataclasses import replace
import json
import pytest
from tests.test_early_squeeze_momentum import M, S, momentum_fixture, event, advance


def test_session_progression_counts_slow_distinct_breaks_and_survives_restore():
    progress = {}
    for i, expected in enumerate([8, 10, 12, 13, 14]):
        batch = [event(str(i*3+j), 100+i*100+j*10) for j in range(3)]
        assert len(M.record_session_breaks(progress, batch)) == 1
        assert progress['target_multiplier'] == expected
        assert not M.record_session_breaks(progress, batch)
        progress = json.loads(json.dumps(progress))
    assert len(progress['broken_levels']) == 15


def test_flat_break_advances_session_and_new_position_inherits(monkeypatch):
    h, a, t, _, _ = momentum_fixture()
    a = replace(a, parameters={**a.parameters, 'momentum_session_progression': True})
    d = a.state['squeeze_breakout']
    d['session_targets'] = dict(broken_levels=['a','b'], target_multiplier=5)
    frozen = deepcopy(d['frozen_gap'])
    monkeypatch.setattr(M, 'observe_resistances', lambda *args: ([event('c',t(16.02).observed_at.timestamp())], False, False))
    result = h.evaluate(a, t(16.02,10.44))
    entry = next(i for i in result.evaluation.intents if i.action == 'enter_long')
    assert entry.metadata['momentum_target']['multiplier'] == 8
    assert result.state['squeeze_entry']['broken_levels'] == []
    assert result.state['squeeze_breakout']['frozen_gap'] == frozen
    assert result.state['squeeze_breakout']['session_targets']['broken_levels'] == ['a','b','c']


@pytest.mark.parametrize('age,qualifies', [(0,True),(30,True),(30.000001,False)])
def test_recent_reentry_starts_at_two_then_inherits_next_session_step(monkeypatch, age, qualifies):
    h, a, t, _, _ = momentum_fixture()
    a = replace(a, parameters={**a.parameters, 'momentum_session_progression': True})
    o = t(16.02,10.44)
    market = deepcopy(o.structural_detector_state)
    market['momentum_session'].update(open=8., high=10.44, prior_high=10.44, late=True)
    o = replace(o, structural_detector_state=market)
    d = a.state['squeeze_breakout']
    d['last_entry_fill_at'] = o.observed_at.timestamp()-age
    d['hod_gate'] = dict(level=M.levels(o)['R2'], threshold=10.41)
    d['session_targets'] = dict(broken_levels=['a','b','c','d','e'], target_multiplier=8)
    result = h.evaluate(a,o)
    entry = next(i for i in result.evaluation.intents if i.action == 'enter_long')
    assert entry.metadata['momentum_target']['multiplier'] == (2 if qualifies else 8)
    if qualifies:
        assert entry.invalidation_price == 10.39
        assert entry.metadata['stop_exit_reason'] == 'recent_reentry_resistance_stop'
    a = replace(advance(a,result), status=S.AssignmentStatus.MANAGING)
    monkeypatch.setattr(M, 'observe_resistances', lambda *args: ([event('f',t(16.04).observed_at.timestamp())], False, False))
    result = h.evaluate(a,t(16.04,10.44,100))
    amendment = next(i for i in result.evaluation.intents if i.action == 'replace_profit_target')
    assert amendment.metadata['momentum_target_multiplier'] == 10
    assert result.state['squeeze_entry']['stop_steps'] == 0


def test_reentry_stop_requires_resistance_below_price():
    from tests.test_early_squeeze_momentum import level
    rows = {'a':level('a',10,10.2),'b':level('b',10.5,10.7)}
    assert M.recent_reentry_stop(rows,{},10.4,.01)['price'] == 9.99
    assert M.recent_reentry_stop(rows,{},9.,.01) is None


@pytest.mark.parametrize('multiplier',[2,5,8,10,12,13,20])
def test_extended_target_arithmetic(multiplier):
    assert M.target_price(10.,.2,multiplier,.01) == pytest.approx(10+.2*multiplier)


def test_new_session_resets_target_progress_and_recent_entry_clock():
    from datetime import timedelta
    h, a, t, _, _ = momentum_fixture()
    a = replace(a, parameters={**a.parameters, 'momentum_session_progression': True})
    d = a.state['squeeze_breakout']
    d.update(session_targets=dict(broken_levels=['a','b','c'],target_multiplier=8),
        last_entry_fill_at=t(16).observed_at.timestamp())
    o = t(16.02,10.44)
    result = h.evaluate(a,replace(o, observed_at=o.observed_at+timedelta(days=1)))
    d = result.state['squeeze_breakout']
    assert not d['session_targets']
    assert 'last_entry_fill_at' not in d


def test_exact_thirty_percent_does_not_use_special_reentry():
    h, a, t, _, _ = momentum_fixture()
    a = replace(a, parameters={**a.parameters, 'momentum_session_progression': True})
    o = t(16.02,10.44)
    market = deepcopy(o.structural_detector_state)
    market['momentum_session'].update(open=o.price/1.3, high=o.price, prior_high=o.price, late=True)
    o = replace(o, structural_detector_state=market)
    d = a.state['squeeze_breakout']
    d.update(last_entry_fill_at=o.observed_at.timestamp()-10,
        hod_gate=dict(level=M.levels(o)['R2'], threshold=10.41),
        session_targets=dict(broken_levels=['a','b','c'],target_multiplier=8))
    result = h.evaluate(a,o)
    entry = next(i for i in result.evaluation.intents if i.action=='enter_long')
    assert entry.metadata['momentum_target']['multiplier']==8
    assert not result.state['squeeze_entry']['recent_reentry']
