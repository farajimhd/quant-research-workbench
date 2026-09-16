from copy import deepcopy
from datetime import datetime, timezone
import json
import pytest
from src.trading_runtime.support_reversal import observe_volume,volume_evidence,assess


def fixture():
    now=120.
    row={'effective_at':now,'candle':{'end':now},'labels':{
        'geometry':['bullish_engulfing'],'interaction':['local:support_rejection']}}
    pressure={'contract':'trade-nbbo-pressure-v1','ready':True,
        'observed_at':datetime.fromtimestamp(now,timezone.utc).isoformat(),'quote_age_ms':100.,
        'fast':{'trades':5,'classified_fraction':.8,'trade_imbalance':.4,
        'quote_imbalance':.1,'progress_spreads':1.,'quote_ready':True}}
    volume={'ready':True,'observed_at':now,'ratio':2.}
    return row,pressure,volume


def test_causal_volume_checkpoint_continuation_and_prefix():
    state={}
    for end in range(1,61):observe_volume(state,dict(time=end-1,end=end,volume=1 if end<=55 else 2),'session')
    before=deepcopy(state);assert volume_evidence(state,60)['ratio']==2
    restored=json.loads(json.dumps(state))
    for end in range(61,70):
        bar=dict(time=end-1,end=end,volume=end)
        observe_volume(state,bar,'session');observe_volume(restored,bar,'session')
    assert state==restored and len(state['bars'])==60
    assert volume_evidence(before,60)['ratio']==2
    assert not volume_evidence(before,61)['ready']
    observe_volume(state,dict(time=80,end=81,volume=10),'session')
    assert not volume_evidence(state,81)['ready']
    observe_volume(state,dict(time=81,end=82,volume=10),'new')
    assert len(state['bars'])==1


@pytest.mark.parametrize('field,value', [('volume',-1),('volume',float('nan')),('end',3)])
def test_invalid_volume_rejected(field,value):
    bar=dict(time=0,end=1,volume=5);bar[field]=value
    with pytest.raises(ValueError):observe_volume({},bar,'session')


def test_revised_candle_is_not_silently_replayed():
    state={};observe_volume(state,dict(time=0,end=1,volume=5),'s')
    with pytest.raises(ValueError):observe_volume(state,dict(time=0,end=1,volume=6),'s')


def test_evidence_passes_without_granting_order_permission():
    row,p,v=fixture();assert assess(row,p,v,now=120)['passed']
    assert 'action' not in assess(row,p,v,now=120)


@pytest.mark.parametrize('change', ['future_pressure','naive_pressure','stale_quote','missing_classification','no_quote_flow','no_engulfing','stale_volume','intrabar'])
def test_missing_stale_or_incomplete_evidence_fails_closed(change):
    row,p,v=fixture()
    if change=='future_pressure':p['observed_at']=datetime.fromtimestamp(121,timezone.utc).isoformat()
    if change=='naive_pressure':p['observed_at']='1970-01-01T00:02:00'
    if change=='stale_quote':p['quote_age_ms']=1001
    if change=='missing_classification':p['fast']['classified_fraction']=.49
    if change=='no_quote_flow':p['fast']['quote_ready']=False
    if change=='no_engulfing':row['labels']['geometry']=[]
    if change=='stale_volume':v['observed_at']=119
    if change=='intrabar':row['candle']['end']=121
    assert not assess(row,p,v,now=120)['passed']


def test_pending_rechecks_flow_and_never_extends_original_setup_lifetime():
    from src.trading_runtime.support_reversal import pending_ready
    row, pressure, volume = fixture()
    saved=dict(at=120.,price=10.,assessment=assess(row,pressure,volume,now=120.))
    def check(**changes):
        args=dict(now=120.1,price=10.,lifetime_s=1.,maximum_quote_age_ms=1000.)
        args.update(changes)
        return pending_ready(saved,pressure,**args)
    assert check()
    assert not check(now=121.)
    assert not check(now=119.)
    assert not check(price=9.99)
    pressure['fast']['trade_imbalance']=-.1
    assert not check()


def engine_candidate():
    from dataclasses import replace
    from tests.test_fresh_pivot_entry import merged_candidate
    host,a,o=merged_candidate();now=o.observed_at.timestamp()
    a.parameters['historical_hod'].update(setup_reversal_enabled=1,
        setup_below_vwap_base_enabled=0,setup_fresh_pivot_enabled=0,
        setup_recovery_enabled=1)
    a.state['v7_setup']['reversal_volume']=dict(session=a.state['historical_hod_state']['session'],
        bars=[dict(end=now-i,volume=100.) for i in range(59,0,-1)])
    market=deepcopy(o.structural_detector_state)
    market['row']['labels']=dict(geometry=['bullish_engulfing'],interaction=['local:support_rejection'])
    market['row']['candle']['end']=now
    _,pressure,_=fixture();pressure['observed_at']=o.observed_at.isoformat()
    return host,a,replace(o,execution_vwap=9.8,bar_volume=10000.,
        structural_detector_state=market,market_pressure=pressure)


def test_reversal_runs_through_real_engine_and_is_disabled_by_default():
    host,a,o=engine_candidate()
    result=host.evaluate(deepcopy(a),o)
    assert result.evaluation.signals[0].action=='enter_long', result.evaluation.signals[0]
    assert result.state['historical_hod_entry']['support_reversal']['assessment']['passed']
    a.parameters['historical_hod']['setup_reversal_enabled']=0
    assert host.evaluate(a,o).evaluation.signals[0].action=='wait'


@pytest.mark.parametrize('failure',['pressure','volume','retired','future','liquidity','risk'])
def test_reversal_engine_keeps_causal_and_execution_gates(failure):
    from dataclasses import replace
    from src.trading_runtime import v7_setup as V
    host,a,o=engine_candidate()
    if failure=='pressure':o.market_pressure['fast']['trade_imbalance']=-.1
    if failure=='volume':a.state['v7_setup']['reversal_volume']['bars']=[]
    if failure=='retired':
        support=V.fresh_pivot_supports(o.structural_detector_state['row'],o.observed_at.timestamp())[0]
        a.state['v7_setup']['retired_swings']={V.swing_key(support):True}
    if failure=='future':o.structural_detector_state['row']['pivot_swings'][0]['fresh_pivot']['confirmed_at']+=10
    if failure=='liquidity':o=replace(o,bid=0.)
    if failure=='risk':a.parameters['historical_hod']['setup_base_maximum_risk_pct']=.001
    assert host.evaluate(a,o).evaluation.signals[0].action=='wait'


def test_reversal_recovery_requires_new_base_and_preserves_retirement_and_reclaim():
    from src.trading_runtime import v7_setup as V
    from tests.test_v7_setup_recovery import swing
    state=dict(last_exit=dict(at=10.,stop=12.,body_high=13.,stop_above_initial_fill=True,
        setup=dict(phase='post_breakout',breakout_threshold=12.)),range=dict(start=11.,end=40.))
    support=swing(38.,9.9);market=dict(bar=dict(end=41.,close=10.1))
    def permission():return V.recovery_permission(state,support,market,stop_gain_guard=True,support_reversal=True)[0]
    assert V.recovery_permission(state,support,market,stop_gain_guard=True)[0]
    assert permission()==''
    state['range']['start']=9.
    assert permission()=='waiting_for_post_move_recovery_or_higher_base'
    state['range']['start']=11.
    state['retired_swings']={V.swing_key(support):True}
    assert permission()=='breached_setup_swing'
    state['retired_swings']={}
    state['last_exit']['setup']['entry_failure_recovery']=10.2
    assert permission()=='waiting_for_failed_setup_reclaim'
