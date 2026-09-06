import json
from dataclasses import replace
from datetime import timedelta

import pytest

from src.trading_runtime import breakout_confirmation as B
from src.trading_runtime import strategy_engine as S
from tests.test_histogram_slope_exit import gated_policy, sample
from tests.test_long_momentum_r3_acceptance import bar, parameters
from tests.test_long_momentum_strategy import NOW, assignment
from tests.test_point_structure_strategy import row


def policy():
    p = S.resolve_long_momentum_parameters(parameters(), revision=47)
    p['entry_candle_confirmation'].update(slope_reentry_break_previous_high=True, minimum_reentry_macd_gap_bps=1)
    p['structural_entry']['break_above_upper_bound'] = True
    p['momentum_management'].update(histogram_slope_exit=gated_policy(), resistance_rejection_exit=True)
    return p


def test_breakout_requires_strict_upper_bound_and_retains_price_order():
    p = policy()['structural_entry']
    levels = tuple(dict(row(price), lower=price, upper=price, band_lower=price-.2, band_upper=price+.25)
                   for price in (103,102,101))
    state = {}
    for second, price, passed in ((0,101.2,False),(1,101.25,False),(2,101.3,True)):
        obs = replace(bar(second,close=price),structural_resistance_levels=levels)
        result = S._prior_completed_frame_resistance_trigger(obs,p,state)
        assert result['passed'] == passed
        assert result['threshold_price'] == 101.25
    tick = replace(obs,price=101.24,source_timeframe='',evaluation_events=('market_data_update',))
    assert not S._prior_completed_frame_resistance_trigger(tick,p,state)['passed']
    assert 'accepted_entry_r3' not in state


def test_candle_high_is_causal_completed_high_not_close_or_current_high():
    state = {}
    B.record_candle(state, replace(bar(0),bar_high=102))
    B.record_candle(state, replace(bar(1),bar_high=104))
    state = json.loads(json.dumps(state))
    obs = replace(bar(1,close=103),bar_high=104,bar_open=101)
    assert B.slope_reentry_confirmation(state,obs)[0] == ''
    assert B.slope_reentry_confirmation(state,replace(obs,price=102))[0] == 'reentry_previous_candle_high_not_broken'
    tick = replace(obs,source_timeframe='',evaluation_events=('market_data_update',),price=103.5)
    assert B.slope_reentry_confirmation(state,tick)[0] == 'reentry_previous_candle_high_not_broken'
    assert B.slope_reentry_confirmation(state,replace(tick,price=104.1))[0] == ''
    assert B.slope_reentry_confirmation(state,replace(tick,price=104.1,bar_open=105))[0] == 'entry_closed_candle_bearish'
    assert B.slope_reentry_confirmation(state,replace(tick,observed_at=NOW+timedelta(seconds=5)))[0] == 'reentry_previous_candle_unavailable'


@pytest.mark.parametrize('prices,exits', [([100,101,100.99],True),([100,101,102,100.99],True),
    ([100,101,102.01,100.99],False),([100,100.99],False),([102.5,101.5,100.99],False)])
def test_rejection_requires_touch_from_below_and_no_successful_break(prices,exits):
    state = {'entry_at':NOW.isoformat(),'entry_reference_price':prices[0]}
    level = dict(row(101.5),band_lower=101,band_upper=102)
    route = None
    for second, price in enumerate(prices):
        route = B.resistance_rejection(state,bar(second,close=price),[level])
        state=json.loads(json.dumps(state))
    assert bool(route) == exits
    if exits:
        assert route['position_fraction'] == 1
        assert route['evidence']['rejection_price'] == prices[-1]


def test_real_engine_rejection_exit_and_quality_filter():
    for score,exits in ((4,True),(3.99,False)):
        p = policy()
        p['protection']['trailing']['enabled']=False
        p['momentum_management']['histogram_slope_exit']['enabled']=False
        p['phase_policy']={'exit':{'mode':'automatic','rule_sets':[]}}
        current=assignment(strategy_revision=47,parameters=p,status=S.AssignmentStatus.MANAGING,
            state={'entry_at':NOW.isoformat(),'entry_reference_price':100,'active_stop':90,'initial_stop':90,'high_water_price':100})
        for second, price in enumerate((100,101.5,100.99)):
            obs=replace(sample(second,.03),price=price,bar_open=99,
                structural_resistance_levels=(dict(row(101.5,score=score),band_lower=101,band_upper=102),),
                structural_support_levels=(),
                source_timeframe='',evaluation_events=('market_data_update',))
            result=S.LongMomentumStrategyEngine(revision=47).evaluate(current,obs)
            current=replace(current,state=result.state,status=result.status)
        intents=[i for i in result.evaluation.intents if i.action=='exit']
        assert bool(intents)==exits
        if exits:
            assert intents[0].reason=='resistance_rejection'
            assert intents[0].metadata['cancel_entry_acquisition']


@pytest.mark.parametrize('gap,reentry,enters',[(.99,True,False),(1,True,False),(1.01,True,True),(.99,False,True)])
def test_real_engine_reentry_histogram_gap(gap,reentry,enters):
    p=policy()
    # Isolate the new gap from the fixture's empty re-entry confirmation stage.
    p['reentry']['require_new_confirmation']=False
    p['structural_entry']['break_above_upper_bound']=False
    state={'reentries':1,'last_exit_at':(NOW-timedelta(seconds=3)).isoformat()} if reentry else {}
    sources={'indicator.flow_structure.score@100ms':{'value':.7},'indicator.flow_structure.confidence@100ms':{'value':.8},
        'indicator.macd.line@5s':{'value':.4},'indicator.macd.signal@5s':{'value':.2},'indicator.macd.histogram@5s':{'value':.2}}
    for value in sources.values():
        value['observed_at']=NOW.isoformat()
    obs=bar(macd_line=.2+101.2*gap/10000,macd_signal=.2,source_values=sources)
    result=S.LongMomentumStrategyEngine(revision=47).evaluate(assignment(strategy_revision=47,parameters=p,state=state),obs)
    assert any(i.action=='enter_long' for i in result.evaluation.intents)==enters, [s.reason for s in result.evaluation.signals]
    if not enters:
        assert result.evaluation.signals[0].reason == 'entry_macd_open_gap_below_threshold'


@pytest.mark.parametrize('opening,high,enters',[(101.1,101.15,True),(101.3,101.15,False),(101.1,101.2,False)])
def test_real_engine_slope_reentry_requires_green_and_previous_high(opening,high,enters):
    from src.trading_runtime import histogram_slope as H
    p=policy()
    p['structural_entry']['break_above_upper_bound']=False
    state={'histogram_slope_reentry_gate':{'exit_timestamp':NOW.timestamp()-10}}
    for second,h in enumerate((.02,.03)):
        H.record(state,sample(second,h))
        B.record_candle(state,replace(sample(second,h),bar_high=high))
    sources={'indicator.flow_structure.score@100ms':{'value':.7},'indicator.flow_structure.confidence@100ms':{'value':.8},
        'indicator.macd.line@5s':{'value':.4},'indicator.macd.signal@5s':{'value':.2},'indicator.macd.histogram@5s':{'value':.2}}
    obs=replace(bar(2,macd_line=.24,macd_signal=.2,source_values=sources),bar_open=opening,bar_high=101.3)
    result=S.LongMomentumStrategyEngine(revision=47).evaluate(assignment(strategy_revision=47,parameters=p,state=state),obs)
    assert any(i.action=='enter_long' for i in result.evaluation.intents)==enters, [s.reason for s in result.evaluation.signals]


def test_rejection_state_resets_with_geometry_removal_and_new_position():
    level=dict(row(101.5),band_lower=101,band_upper=102)
    for changed in ('bounds','removed','entry'):
        state={'entry_at':NOW.isoformat(),'entry_reference_price':100}
        assert B.resistance_rejection(state,bar(0,close=101.5),[level]) is None
        levels=[level]
        if changed=='bounds':
            levels=[dict(level,band_lower=101.1)]
        elif changed=='removed':
            levels=[]
        else:
            state['entry_at']=(NOW+timedelta(seconds=1)).isoformat()
            state['entry_reference_price']=100.5
        assert B.resistance_rejection(state,bar(1,close=100.99),levels) is None
