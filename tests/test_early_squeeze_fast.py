from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from src.trading_runtime import early_squeeze_fast as F, strategy_engine as S
from tests.test_early_squeeze_breakout import fixture as old_fixture
from tests.test_vwap_resistance_ladder import advance, NOW


def fixture():
    host, a, old = old_fixture()
    a = replace(a, parameters={**a.parameters, 'early_squeeze_breakout_contract': F.CONTRACT})
    context = {}
    def obs(i=0., price=10.39, position=0., body=.01, mean=None):
        nonlocal context
        o = replace(old(i,price,position,open_price=price-body,high=price,low=price-body),
                    source_timeframe='100ms', average_price=10.49 if position else 0.)
        frame = SimpleNamespace(as_of=o.observed_at,bar=dict(open=o.bar_open,high=o.bar_high,low=o.bar_low,close=price))
        context = F.observe_candle(frame, context, {})
        context['prior_hod'] = 10.5
        if mean is not None:
            context.update(mean_before=mean,count_before=10)
        return replace(o,structural_detector_state=dict(row=dict(effective_at=int(o.observed_at.timestamp()),
            candle=dict(volume=1000.)),fast_squeeze_context=deepcopy(context)))
    return host,a,obs


def entered():
    h,a,o=fixture()
    a=advance(a,h.evaluate(a,o()))
    result=h.evaluate(a,o(.1,10.48,body=.09))
    assert result.evaluation.intents[0].action=='enter_long'
    a=advance(a,result)
    a.state['squeeze_entry'].update(first_fill_at=NOW.timestamp()+.1,slice_notional=3000.)
    return h,replace(a,status=S.AssignmentStatus.MANAGING),o


def test_body_average_excludes_current_and_resets_session():
    def frame(t,body):
        return SimpleNamespace(as_of=t,bar=dict(open=10.,close=10.+body,high=10.+max(body,0),low=10.+min(body,0)))
    d=F.observe_candle(frame(NOW,.1),{},{});assert d['mean_before'] is None
    d=F.observe_candle(frame(NOW+timedelta(seconds=.1),-.2),d,{})
    d=F.observe_candle(frame(NOW+timedelta(seconds=.2),.2),d,{})
    assert d['mean_before']==pytest.approx(.1) and d['count_before']==1
    assert F.observe_candle(frame(NOW+timedelta(seconds=.2),.2),d,{})==d
    d=F.observe_candle(frame(NOW+timedelta(days=1),.2),d,{})
    assert d['mean_before'] is None


@pytest.mark.parametrize('body,price,allowed',[(.019,10.48,False),(.02,10.48,True),(.09,10.46,False),(.09,10.47,True)])
def test_big_body_and_five_ticks_are_both_required(body,price,allowed):
    h,a,o=fixture();a=advance(a,h.evaluate(a,o()))
    result=h.evaluate(a,o(.1,price,body=body,mean=.01))
    assert bool(result.evaluation.intents)==allowed


def test_gray_resistance_closer_to_hod_wins_and_is_not_a_target():
    h,a,o=fixture();initial=o();base=initial.structural_resistance_levels[1]
    gray=dict(base,unified_level_id='gray',lower=10.44,upper=10.46,role='transition',side=0,transition_from='resistance')
    a=advance(a,h.evaluate(a,replace(initial,structural_transition_levels=(gray,))))
    result=h.evaluate(a,replace(o(.1,10.52,body=.13),structural_transition_levels=(gray,)))
    intent=result.evaluation.intents[0]
    assert intent.metadata['entry_selection']['unified_level_id']=='gray'
    assert intent.metadata['profit_target_selection']['level']['unified_level_id']!='gray'


def test_partial_target_liquidates_remainder_without_adds():
    h,a,o=entered();a.state['entry_acquisition_exit_latched']=True
    result=h.evaluate(a,replace(o(.2,10.7,100,body=.2),pending_exit_quantity=30.))
    assert [(i.action,i.quantity) for i in result.evaluation.intents]==[('exit',70.)]


def test_stop_restores_structural_anchor_without_rebasing_distance():
    h,a,o=entered()
    a.state.update(initial_stop=10.2,active_stop=10.2)
    a.state['squeeze_entry'].update(structural_stop=10.4,trail_distance=.29,peak_price=10.49)
    result=h.evaluate(a,replace(o(.2,10.51,100),source_timeframe='',evaluation_events=('market_data_update',)))
    assert result.state['active_stop']==pytest.approx(10.4)
    assert result.state['squeeze_entry']['trail_distance']==.29
    assert F.below(7.253677,.01)==7.25
    assert F.below(7.25,.01)==7.24


def test_targets_and_break_count_use_100ms_close_not_tick_or_one_second():
    h,a,o=entered()
    bar=o(.2,10.85,100,body=.37)
    a.state['active_stop']=9.
    result=h.evaluate(a,replace(bar,source_timeframe='',evaluation_events=('market_data_update',)))
    assert not any(i.action=='replace_profit_target' for i in result.evaluation.intents)
    result=h.evaluate(a,bar)
    assert any(i.action=='replace_profit_target' for i in result.evaluation.intents)
    assert len(result.state['squeeze_entry']['broken_levels'])>0


def test_no_entry_before_activation_or_without_prior_green_history():
    h,a,o=fixture()
    assert not h.evaluate(a,o(.1,10.48,body=.09)).evaluation.intents
    candle=o(.2,10.48,body=.09)
    sv={k:v for k,v in candle.source_values.items() if not k.startswith('signal.activation')}
    assert not h.evaluate(a,replace(candle,source_values=sv)).evaluation.intents


def test_later_big_green_can_confirm_crossing_by_red_candle():
    h,a,o=fixture();a=advance(a,h.evaluate(a,o()))
    a=advance(a,h.evaluate(a,o(.1,10.46,body=-.01,mean=.01)))
    result=h.evaluate(a,o(.2,10.49,body=.02,mean=.01))
    assert result.evaluation.intents[0].action=='enter_long'


def test_recovery_without_qualifying_swing_uses_last_completed_open():
    h,a,o=fixture();a=advance(a,h.evaluate(a,o()))
    candle=o(.1,10.6,body=.1,mean=.01)
    anchor=deepcopy(candle.structural_resistance_levels[0])
    anchor.update(lower=10.,upper=10.02)
    a.state['squeeze_breakout']['recovery']=dict(anchor=anchor,high=10.5,
        stopped_at=NOW.timestamp(),breakout_at=NOW.timestamp()-10)
    # Recovery with no current R1 cross and no eligible swing.
    context=deepcopy(candle.structural_detector_state)
    context['fast_squeeze_context'].update(previous_close=10.55,prior_hod=10.)
    result=h.evaluate(a,replace(candle,structural_detector_state=context))
    intent=result.evaluation.intents[0]
    assert intent.reason=='stopout_close_high_reentry'
    assert intent.invalidation_price==pytest.approx(10.5)


def test_fast_candidate_compiles_without_inherited_trading_gates(monkeypatch):
    from src.backend import early_squeeze_fast_candidate as C
    from src.backend.trading_configuration_service import configuration_base, _build_configuration_release
    from tests.test_early_squeeze_candidate import baseline
    base=configuration_base()
    monkeypatch.setattr('src.backend.trading_configuration_service.configuration_base',lambda:deepcopy(base))
    before=deepcopy(base)
    payload,canvas,plan=C.build(base,baseline(base))
    assert base==before
    profile=next(p for p in payload['strategy']['profiles'] if p['profile_id']==F.CONTRACT)
    p=profile['parameters']
    assert not p['require_open_macd_for_entry'] and not p['require_positive_macd_signal_for_entry']
    assert p['liquidity_admission']['maximum_current_spread_bps']==250.
    assert p['sizing']['request_value']==pytest.approx(1/3)
    assert sorted(k for k in p if k.endswith('_contract'))==['early_squeeze_breakout_contract','structural_recovery_contract']
    _build_configuration_release(canvas_revision=canvas['revision'],canvas_profile=canvas['profile'],
        configuration=payload,run_plan_id=plan,strategy_profile_id=F.CONTRACT)


def test_replay_collects_100ms_body_history_before_activation():
    import asyncio
    from src.backend.replay_run_service import ReplayRunController, ReplayDerivedFrame
    controller=ReplayRunController.__new__(ReplayRunController)
    controller.definition=SimpleNamespace(configuration_revision={'payload':{'strategy':{
        'parameters':{'early_squeeze_breakout_contract':F.CONTRACT}}}})
    controller._candle_detector_states={}
    async def run():
        for index,body in enumerate((.1,.2)):
            await controller._observe_episode_candle(ReplayDerivedFrame(ticker='JUNS',timeframe='100ms',
                as_of=NOW+timedelta(seconds=index*.1),sequence=index,
                bar=dict(open=10.,high=10.+body,low=10.,close=10.+body,volume=100),indicator={}))
    asyncio.run(run())
    context=controller._candle_detector_states['JUNS']['structural_recovery']['fast_squeeze_context']
    assert context['count_before']==1 and context['mean_before']==pytest.approx(.1)


@pytest.mark.parametrize('count,ordinal',[(0,3),(3,3),(4,2),(5,2),(6,1),(9,1)])
def test_target_ordinal_changes_at_lifecycle_boundaries(count,ordinal):
    h,a,o=entered()
    a.state['squeeze_entry']['broken_levels']=[str(i) for i in range(count)]
    a.state['structural_profit_targets']=[10.55]
    result=h.evaluate(a,o(.2,10.5,100,body=0.))
    target=next(i for i in result.evaluation.intents if i.action=='replace_profit_target')
    assert target.metadata['profit_target_selection']['ordinal']==ordinal


def test_swing_invalidity_survives_later_bounce():
    swing=dict(side=1,lower=10.,pivot_at=1,confirmed_at=2)
    frame=SimpleNamespace(as_of=NOW,bar=dict(open=10.1,high=10.1,low=9.9,close=10.))
    context=F.observe_candle(frame,{},dict(local_swings=[swing]))
    frame.as_of+=timedelta(seconds=.1)
    frame.bar=dict(open=10.1,high=10.2,low=10.1,close=10.2)
    context=F.observe_candle(frame,context,dict(local_swings=[swing]))
    assert F.swing_key(swing) in context['invalid_swings']
