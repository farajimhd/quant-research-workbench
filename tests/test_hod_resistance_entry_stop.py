"""R2 entry and R3 stop share the causal, qualified ladder below HOD."""
from dataclasses import replace
from datetime import timedelta

import pytest

from src.trading_runtime import strategy_engine as S
from tests.test_normalized_macd_regime import parameters as prior_parameters
from tests.test_long_momentum_r3_acceptance import bar
from tests.test_long_momentum_strategy import NOW, assignment
from tests.test_point_structure_strategy import row


def policy():
    p = prior_parameters()
    p['structural_entry']['entry_level_ordinal_below_high'] = 2
    p['momentum_management']['resistance_rejection_exit'] = False
    p['protection']['stop']['method'] = 'third_resistance_below_session_high'
    p['protection']['trailing']['enabled'] = False
    return S.resolve_long_momentum_parameters(p, revision=47)


def observation(price=102.3):
    levels = [dict(row(p),band_lower=p-.2,band_upper=p+.2,load_contract='merged-point-minmax-v1',p_norm=.95,minimum_p_norm=.9)
              for p in (101,104,103,102,105,106)]
    return replace(bar(close=price),structural_session_high=103.5,structural_resistance_levels=tuple(levels),structural_support_levels=())


@pytest.mark.parametrize('price,passed',[(101.3,False),(102.2,False),(102.3,True)])
def test_entry_requires_r2_upper_bound(price,passed):
    result = S._prior_completed_frame_resistance_trigger(observation(price),policy()['structural_entry'],{})
    assert result['passed'] == passed
    assert result['threshold_price'] == 102.2
    assert result['entry_level_ordinal_below_high'] == 2
    assert 'r3' not in result['reason']


@pytest.mark.parametrize('price,opening,passed', [(102.3,102.3,True),(102.2,102.1,False),(102.3,102.4,False)])
def test_live_r2_acceptance_without_completed_close(price,opening,passed):
    p=policy();p['structural_entry']['accept_live_price_above_entry_level']=True
    obs=replace(observation(price),bar_open=opening,evaluation_events=('market_data_update',),source_timeframe='')
    result=S._prior_completed_frame_resistance_trigger(obs,p['structural_entry'],{})
    assert result['passed']==passed
    if passed:
        assert result['reason']=='current_r2_live_price_accepted'
        assert result['acceptance']['acceptance_basis']=='live_price'
    assert not S._prior_completed_frame_resistance_trigger(obs,policy()['structural_entry'],{})['passed']


def test_real_engine_live_r2_entry_carries_r3_stop():
    p=policy();p['structural_entry']['accept_live_price_above_entry_level']=True
    p['liquidity_admission']['maximum_current_spread_bps']=200
    obs=replace(observation(),bar_open=102.3,macd_line=.4,macd_signal=.2,
                evaluation_events=('market_data_update',),source_timeframe='')
    result=S.LongMomentumStrategyEngine(revision=47).evaluate(assignment(strategy_revision=47,parameters=p),obs)
    assert any(i.action=='enter_long' for i in result.evaluation.intents), [s.reason for s in result.evaluation.signals]
    assert result.state['initial_stop']==101


def test_stop_is_r3_main_price_and_does_not_substitute_support_or_risk_cap():
    p=policy();p['protection']['stop']['maximum_risk_pct']=.1
    evidence={}
    assert S._initial_stop(observation(),p,None,side='long',selection_evidence=evidence)==101
    assert evidence['selected_resistance_level']['unified_level_id']=='101'
    assert [r['price'] for r in evidence['qualified_levels']]==[103,102,101]


@pytest.mark.parametrize('change',['missing','weak','future','support','not_protective'])
def test_missing_or_invalid_third_level_fails_closed(change):
    obs=observation();levels=list(obs.structural_resistance_levels)
    if change=='missing':levels=levels[1:]
    elif change=='weak':levels[0]['p_norm']=.89
    elif change=='future':levels[0]['confirmed_at_ms']=(NOW+timedelta(seconds=1)).timestamp()*1000
    elif change=='support':levels[0]['side']=1
    else:obs=replace(obs,price=100)
    assert S._initial_stop(replace(obs,structural_resistance_levels=tuple(levels)),policy(),None,side='long')==0


def test_real_engine_entry_carries_r3_stop():
    sources={'indicator.flow_structure.score@100ms':{'value':.7},'indicator.flow_structure.confidence@100ms':{'value':.8},
             'indicator.macd.line@5s':{'value':.4},'indicator.macd.signal@5s':{'value':.2},'indicator.macd.histogram@5s':{'value':.2}}
    for value in sources.values():
        value['observed_at']=NOW.isoformat()
    obs=replace(observation(),source_values=sources,macd_line=.4,macd_signal=.2)
    result=S.LongMomentumStrategyEngine(revision=47).evaluate(assignment(strategy_revision=47,parameters=policy()),obs)
    assert any(i.action=='enter_long' for i in result.evaluation.intents), [s.reason for s in result.evaluation.signals]
    assert result.state['initial_stop']==101
    blocked=S.LongMomentumStrategyEngine(revision=47).evaluate(assignment(strategy_revision=47,parameters=policy()),replace(obs,structural_resistance_levels=obs.structural_resistance_levels[1:]))
    assert not any(i.action=='enter_long' for i in blocked.evaluation.intents)
    assert blocked.evaluation.signals[0].reason=='third_resistance_stop_unavailable'


def test_fixed_stop_and_disabled_retest_exit():
    p=policy()
    state={'entry_at':NOW.isoformat(),'entry_reference_price':102.3,'initial_stop':101,'active_stop':101,'high_water_price':110}
    assert S._ratcheted_stop(observation(110),p,state,side='long')==101
    p['momentum_management']['downside_loss_guard']['below_vwap']=False
    for second,price in enumerate((102.3,103,102.7)):
        obs=replace(observation(price),observed_at=NOW+timedelta(seconds=second),macd_line=.4,macd_signal=.2)
        assert S._matching_momentum_management_route(p,obs,state,gain_pct=1,side='long') is None


@pytest.mark.parametrize('ordinal',[0,4,True,2.5])
def test_entry_rank_validation(ordinal):
    p=policy();p['structural_entry']['entry_level_ordinal_below_high']=ordinal
    with pytest.raises(ValueError,match='entry_level_ordinal_below_high'):
        S.resolve_long_momentum_parameters(p,revision=47)


def test_trailing_r3_tightens_and_retains_stop_when_lower_or_missing():
    p=policy();p['protection']['trailing'].update(enabled=True,mode='third_resistance_below_session_high')
    state={'entry_reference_price':100,'active_stop':100,'high_water_price':104}
    assert S._ratcheted_stop(observation(),p,state,side='long')==101
    assert state['trailing_support_selection']['selected_resistance_level']['price']==101
    state['active_stop']=101.5
    assert S._ratcheted_stop(observation(),p,state,side='long')==101.5
    assert S._ratcheted_stop(replace(observation(),structural_resistance_levels=()),p,state,side='long')==101.5


def test_real_engine_sends_resistance_stop_replacement():
    p=policy();p['protection']['trailing'].update(enabled=True,mode='third_resistance_below_session_high')
    p['phase_policy']={'exit':{'mode':'automatic','rule_sets':[]}}
    state={'entry_at':NOW.isoformat(),'entry_reference_price':100,'initial_stop':100,'active_stop':100,'high_water_price':102.3}
    obs=replace(observation(),position_quantity=10,macd_line=.4,macd_signal=.2)
    result=S.LongMomentumStrategyEngine(revision=47).evaluate(assignment(strategy_revision=47,parameters=p,status=S.AssignmentStatus.MANAGING,state=state),obs)
    intents=[i for i in result.evaluation.intents if i.action=='replace_protective_stop']
    assert len(intents)==1,[i.action for i in result.evaluation.intents]
    assert intents[0].invalidation_price==101
    assert intents[0].reason=='third_resistance_advanced'


def test_default_p_norm_is_inclusive_point_eight_and_saved_threshold_is_preserved():
    from src.trading_runtime.structure_level_contract import strategy_snapshot
    from src.trading_runtime.normalized_level_book import DEFAULT_THRESHOLD
    assert DEFAULT_THRESHOLD==.8
    rows=[dict(row(100+i),load_contract='merged-point-minmax-v1',p_norm=score) for i,score in enumerate((.799,.8,.9))]
    assert len(strategy_snapshot({'unified_levels':rows},NOW)['unified_levels'])==2
    assert len(strategy_snapshot({'unified_levels':rows},NOW,minimum_p_norm=.9)['unified_levels'])==1


@pytest.mark.parametrize('reentry', [False, True])
@pytest.mark.parametrize('price,line,signal,closed,enters', [
    (103.3,.4,.2,True,True), (103.2,.4,.2,True,False),
    (102.3,.4,.2,True,False), (103.3,.4,.2,False,False),
    (103.3,.4,0,True,False), (103.3,.4,-.1,True,False),
    (103.3,.4,.4,True,False), (103.3,.4,.5,True,False),
    (103.3,.20001,.2,True,True),
])
def test_completed_r1_entry_requires_positive_open_macd(price,line,signal,closed,enters,reentry):
    p=policy()
    p.update(strict_green_entry=True,completed_macd_setup=True,require_completed_entry_candle=True,
             require_open_macd_for_entry=True,require_positive_macd_signal_for_entry=True)
    p['structural_entry'].update(entry_level_ordinal_below_high=1,accept_live_price_above_entry_level=False)
    p['entry_candle_confirmation'].update(require_closed_bar=True,evaluate_macd_intrabar=False,
        minimum_macd_open_gap_bps=0,minimum_reentry_macd_gap_bps=0)
    p['reentry']['require_new_confirmation']=False
    sources={'indicator.flow_structure.score@100ms':{'value':.7},
        'indicator.flow_structure.confidence@100ms':{'value':.8},
        'indicator.macd.line@5s':{'value':.4},'indicator.macd.signal@5s':{'value':.2},
        'indicator.macd.histogram@5s':{'value':.2}}
    for value in sources.values():
        value["observed_at"]=NOW.isoformat()
    p["protection"]["stop"]["method"]="first_resistance_below_session_high"
    obs=replace(observation(price),bar_open=103.1,macd_line=line,macd_signal=signal,
        macd_histogram=line-signal,source_values=sources,source_timeframe='1s' if closed else '',
        evaluation_events=('bar_close',) if closed else ('market_data_update',))
    state={'reentries':1,'last_exit_at':(NOW-timedelta(seconds=3)).isoformat()} if reentry else {}
    state['completed_entry_macd']={'observed_at':NOW.isoformat(),'line':line,'signal':signal,'histogram':line-signal}
    result=S.LongMomentumStrategyEngine(revision=47).evaluate(assignment(strategy_revision=47,parameters=p,state=state),obs)
    assert any(i.action=='enter_long' for i in result.evaluation.intents)==enters, [s.reason for s in result.evaluation.signals]


def test_r1_stop_trails_up_and_engine_replaces_protection():
    p=policy()
    p['protection']['stop']['method']='first_resistance_below_session_high'
    p['protection']['trailing'].update(enabled=True,mode='first_resistance_below_session_high',activation_gain_pct=0)
    p=S.resolve_long_momentum_parameters(p,revision=47)
    assert S._initial_stop(observation(103.3),p,None,side='long')==103
    state={'entry_at':NOW.isoformat(),'entry_reference_price':102.3,'initial_stop':101,'active_stop':101,'high_water_price':103.3}
    p['phase_policy']={'exit':{'mode':'automatic','rule_sets':[]}}
    result=S.LongMomentumStrategyEngine(revision=47).evaluate(
        assignment(strategy_revision=47,parameters=p,status=S.AssignmentStatus.MANAGING,state=state),
        replace(observation(103.3),position_quantity=10,macd_line=.4,macd_signal=.2))
    stops=[i for i in result.evaluation.intents if i.action=='replace_protective_stop']
    assert len(stops)==1
    assert stops[0].invalidation_price==103
    assert stops[0].reason=='first_resistance_advanced'
    state['active_stop']=103.1
    assert S._ratcheted_stop(observation(103.3),p,state,side='long')==103.1
    assert S._ratcheted_stop(replace(observation(103.3),structural_resistance_levels=()),p,state,side='long')==103.1


def test_completed_entry_policy_survives_resolution_and_blocks_latched_intrabar():
    p=policy();p.update(require_completed_entry_candle=True)
    p['structural_entry']['entry_level_ordinal_below_high']=1
    p=S.resolve_long_momentum_parameters(p,revision=47)
    assert not p['entry_candle_confirmation']['evaluate_macd_intrabar']
    assert not p['structural_entry']['accept_live_price_above_entry_level']
    state={}
    assert S._prior_completed_frame_resistance_trigger(observation(103.3),p['structural_entry'],state)['passed']
    obs=replace(observation(103.4),bar_open=103.1,evaluation_events=('market_data_update',),source_timeframe='')
    result=S.LongMomentumStrategyEngine(revision=47).evaluate(assignment(strategy_revision=47,parameters=p,state=state),obs)
    assert not any(i.action=='enter_long' for i in result.evaluation.intents)
