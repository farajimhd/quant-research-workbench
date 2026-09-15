from copy import deepcopy
from dataclasses import replace
from src.trading_runtime import v7_setup as V, strategy_engine as S
from tests.test_v7_transition_acquisition import setup


def test_range_is_prior_only_and_phase_survives_episode_change():
    state={}
    settings=dict(setup_range_seconds=30,setup_minimum_bars=5)
    for i in range(1,7):
        d=dict(session='day',episode=1,bar=dict(time=i-1,end=i,open=10,close=10.1,high=10.2 if i<6 else 11,low=9.9))
        V.observe(state,d,settings,True)
    assert state['range']['high']==10.2
    entry=dict(confirmed_at=6,setup=dict(phase='building',breakout_threshold=10.21,range=deepcopy(state['range'])))
    d.update(episode=2,bar=dict(time=6,end=7,open=10.3,close=10.25,high=10.4,low=10))
    assert not V.phase(entry,d,True)  # red cannot activate
    d['bar'].update(end=8,time=7,open=10.2,close=10.25)
    assert V.phase(entry,d,True)
    assert entry['setup']['range']['high']==10.2
    assert not V.phase(entry,d,True)


def test_bounded_missing_seconds_retain_real_prior_bars_only():
    strict={};bounded={}
    settings=dict(setup_range_seconds=30,setup_minimum_bars=5)
    for end in (1,2,4,5,6,7):
        market=dict(session='day',episode=1,bar=dict(time=end-1,end=end,
            open=10,close=10.1,low=9.9,high=12 if end==7 else 10.2))
        V.observe(strict,market,settings,True)
        V.observe(bounded,market,dict(settings,setup_maximum_bar_gap_s=3),True)
    assert strict['range'] is None
    assert bounded['range']['count']==5 and bounded['range']['high']==10.2
    assert [b['end'] for b in bounded['bars']]==[1,2,4,5,6,7]
    market['bar']=dict(time=11,end=12,open=10,close=10.1,low=9.9,high=10.2)
    V.observe(bounded,market,dict(settings,setup_maximum_bar_gap_s=3),True)
    assert bounded['range'] is None  # Four missing seconds exceed the limit.


def prepared():
    host,a,obs=setup()
    p=deepcopy(a.parameters)
    p['historical_hod'].update(v7_setup_enabled=1,v7_encounters_enabled=1,v7_price_only_enabled=1)
    state=deepcopy(a.state)
    end=obs(2,10.02).observed_at.timestamp()
    state['v7_setup']=dict(session=state['historical_hod_state']['session'],bars=[
        dict(time=end-i-1,end=end-i,open=10,close=10,high=10.1,low=9.99) for i in range(6,0,-1)])
    return host,replace(a,parameters=p,state=state),obs


def test_fresh_base_can_enter_at_range_break_without_future_swing():
    host,a,obs=prepared()
    o=obs(2,10.11)
    market=deepcopy(o.structural_detector_state);now=o.observed_at.timestamp()
    swing=dict(side='support',state='active',lower=9.99,price=9.995,upper=10.,
        pivot_at=now-3,confirmed_at=now-1)
    market['row']['local_swings']=[swing]
    o=replace(o,structural_detector_state=market)
    assert not host.evaluate(a,o).evaluation.intents
    a.parameters['historical_hod']['setup_early_base_enabled']=1
    assert host.evaluate(a,o).evaluation.signals[0].reason=='v7_fresh_base_entry'
    for modified in [replace(o,bar_open=10.12),replace(o,execution_vwap=10.12),
                     replace(o,evaluation_events=('market_data_update',))]:
        assert not host.evaluate(a,modified).evaluation.intents
    for confirmed in (now+1,now-20):
        market['row']['local_swings']=[dict(swing,pivot_at=confirmed-1,confirmed_at=confirmed)]
        assert not host.evaluate(a,replace(o,structural_detector_state=market)).evaluation.intents


def test_research_base_geometry_is_available_before_vwap_gate_without_state_change():
    host,a,obs=prepared();a.parameters['historical_hod']['setup_early_base_enabled']=1
    o=replace(obs(2,10.11),execution_vwap=10.12);now=o.observed_at.timestamp()
    market=deepcopy(o.structural_detector_state)
    market['row']['local_swings']=[dict(side='support',state='active',lower=9.99,price=9.995,upper=10.,
        pivot_at=now-3,confirmed_at=now-1)]
    o=replace(o,structural_detector_state=market)
    baseline=host.evaluate(deepcopy(a),o)
    a.parameters['historical_hod']['setup_base_diagnostics_enabled']=1
    observed=host.evaluate(deepcopy(a),o)
    assert baseline.evaluation.signals[0].reason==observed.evaluation.signals[0].reason=='hod_history_or_vwap_gate'
    assert baseline.state==observed.state and baseline.evaluation.intents==observed.evaluation.intents
    assert 'early_base_assessment' not in observed.evaluation.signals[0].metadata
    diagnostic=observed.evaluation.signals[0].metadata['research_base_assessment']
    assert diagnostic['status']=='measured' and diagnostic['checks']['fresh_support']
    entered=host.evaluate(deepcopy(a),replace(o,execution_vwap=9.))
    metadata=entered.evaluation.signals[0].metadata
    assert {k:v for k,v in metadata['research_base_assessment'].items() if k!='status'}==metadata['early_base_assessment']
    future=deepcopy(market);future['row']['local_swings'][0]['confirmed_at']=now+1
    rejected=host.evaluate(deepcopy(a),replace(o,structural_detector_state=future))
    assert not rejected.evaluation.signals[0].metadata['research_base_assessment']['checks']['fresh_support']
    invalid=host.evaluate(deepcopy(a),replace(o,bid=0.))
    assert invalid.evaluation.signals[0].metadata['research_base_assessment']['reason']=='invalid_current_prices'


def test_research_base_switch_validates_type_and_required_strategy():
    import pytest
    from src.trading_runtime import historical_hod as H
    _,a,_=prepared();a.parameters['historical_hod']['setup_early_base_enabled']=1
    H.configure(a.parameters)
    assert H.DEFAULTS['setup_base_diagnostics_enabled']==0
    for value in (-1,2,True,float('nan')):
        p=deepcopy(a.parameters);p['historical_hod']['setup_base_diagnostics_enabled']=value
        with pytest.raises(ValueError,match='Base diagnostics'):H.configure(p)
    p=deepcopy(a.parameters);p['historical_hod'].update(setup_base_diagnostics_enabled=1,setup_early_base_enabled=0)
    with pytest.raises(ValueError,match='Base diagnostics'):H.configure(p)


def test_ineligible_closer_swing_does_not_hide_a_fresh_base_support():
    host,a,obs=prepared()
    a.parameters['historical_hod']['setup_early_base_enabled']=1
    o=obs(2,10.11);now=o.observed_at.timestamp()
    market=deepcopy(o.structural_detector_state)
    fresh=dict(side='support',state='active',lower=9.99,price=9.995,upper=10.,
               pivot_at=now-3,confirmed_at=now-1)
    older=dict(fresh,lower=10.02,price=10.025,upper=10.03,pivot_at=now-40,confirmed_at=now-20)
    market['row']['local_swings']=[fresh]
    reference=host.evaluate(a,replace(o,structural_detector_state=market))
    assert reference.evaluation.signals[0].reason=='v7_fresh_base_entry'
    market['row']['confirmed_swings']=[older]
    result=host.evaluate(a,replace(o,structural_detector_state=market))
    assert result.evaluation.signals[0].reason=='v7_fresh_base_entry'
    assert result.evaluation.signals[0].metadata['early_base_entry']['swing']==fresh
    evidence=result.evaluation.signals[0].metadata['early_base_assessment']
    assert evidence['observed_at']==now and evidence['failed']==[]
    # A recently confirmed pivot from before this base must also be excluded.
    market['row']['confirmed_swings']=[dict(older,confirmed_at=now-1)]
    assert host.evaluate(a,replace(o,structural_detector_state=market)).evaluation.signals[0].reason=='v7_fresh_base_entry'
    market['row']['local_swings']=[]
    rejected=host.evaluate(a,replace(o,structural_detector_state=market))
    assert 'fresh_support' in rejected.evaluation.signals[0].metadata['early_base_assessment']['failed']


def test_quote_clearance_selects_an_existing_eligible_support_without_moving_it():
    host,a,obs=prepared()
    a.parameters['historical_hod'].update(setup_early_base_enabled=1,
        setup_minimum_quote_clearance_spreads=1)
    o=replace(obs(2,10.11),bid=10.07,ask=10.11);now=o.observed_at.timestamp()
    market=deepcopy(o.structural_detector_state)
    viable=dict(side='support',state='active',lower=9.99,price=9.995,upper=10.,
        pivot_at=now-3,confirmed_at=now-1)
    tight=dict(viable,lower=10.06,price=10.065,upper=10.07)
    market['row']['local_swings']=[viable,tight]
    o=replace(o,structural_detector_state=market)
    assert host.evaluate(a,o).evaluation.signals[0].reason=='setup_stop_inside_quote_noise'
    a.parameters['historical_hod']['setup_support_quote_clearance_selection']=1
    result=host.evaluate(a,o)
    signal=result.evaluation.signals[0]
    assert signal.reason=='v7_fresh_base_entry'
    assert signal.metadata['early_base_entry']['swing']==viable
    assert signal.metadata['entry_quote_clearance']['stop']==9.98
    assert signal.metadata['entry_quote_clearance']['minimum_spreads']==1
    assert any(i.action=='enter_long' for i in result.evaluation.intents)
    # Eligibility is still causal; a stale or future wider support cannot help.
    for confirmed in (now+1,now-20):
        market['row']['local_swings']=[tight,dict(viable,pivot_at=confirmed-1,confirmed_at=confirmed)]
        assert not host.evaluate(a,replace(o,structural_detector_state=market)).evaluation.intents


def test_configurable_base_extension_preserves_other_entry_checks():
    import pytest
    from src.trading_runtime.historical_hod import configure
    host,a,obs=prepared()
    a.parameters['historical_hod']['setup_early_base_enabled']=1
    o=obs(2,10.15);now=o.observed_at.timestamp()
    market=deepcopy(o.structural_detector_state)
    market['row']['local_swings']=[dict(side='support',state='active',lower=9.99,
        price=9.995,upper=10.,pivot_at=now-3,confirmed_at=now-1)]
    o=replace(o,structural_detector_state=market)
    result=host.evaluate(a,o)
    assert 'extension_limit' in result.evaluation.signals[0].metadata['early_base_assessment']['failed']
    a.parameters['historical_hod']['setup_base_maximum_extension_fraction']=1.5
    result=host.evaluate(a,o)
    evidence=result.evaluation.signals[0].metadata['early_base_assessment']
    assert evidence['maximum_extension_fraction']==1.5
    assert evidence['extension_limit']==pytest.approx(10.265)
    assert evidence['failed']==[]
    assert any(i.action=='enter_long' for i in result.evaluation.intents)
    for modified in (replace(o,bar_open=10.16),replace(o,execution_vwap=10.16)):
        assert not host.evaluate(a,modified).evaluation.intents
    a.parameters['historical_hod']['setup_base_maximum_risk_pct']=.1
    failed=host.evaluate(a,o).evaluation.signals[0].metadata['early_base_assessment']['failed']
    assert 'risk_limit' in failed
    for value in (-1,True,float('nan'),float('inf')):
        a.parameters['historical_hod']['setup_base_maximum_extension_fraction']=value
        with pytest.raises(ValueError):configure(a.parameters)
    a.parameters['historical_hod']['setup_base_maximum_extension_fraction']=0
    configure(a.parameters)


def test_early_entry_before_resistance_and_hold_rejection_then_stop():
    host,a,obs=prepared()
    # Price above previous close but below next resistance 10.16 and range 10.1.
    entered=host.evaluate(a,obs(2,10.02))
    assert any(i.action=='enter_long' for i in entered.evaluation.intents),entered.evaluation.signals[0].reason
    active=entered.state['historical_hod_entry']
    assert active['setup']['phase']=='building'
    assert active['level']['price']>10.02
    a=replace(a,state=entered.state,status=S.AssignmentStatus.MANAGING)
    end=obs(3,10.01).observed_at.timestamp()
    a.state['v7_encounters']=dict(session=a.state['historical_hod_state']['session'],levels={
        'x':dict(level=dict(price=10.2,lower=10.1,upper=10.3),threshold=10.21,failure_threshold=10.1,
            status='warning',warning=dict(open=10.1,end=end))})
    o=replace(obs(3,10.01),position_quantity=100,average_price=10.02,
        evaluation_events=('market_data_update',),changed_source_ids=('market.last_price',))
    result=host.evaluate(a,o)
    assert not any(i.action=='exit' for i in result.evaluation.intents)
    result=host.evaluate(replace(a,state=result.state),replace(o,price=9.97))
    assert any(i.action=='exit' and i.reason=='protective_stop' for i in result.evaluation.intents)


def test_early_entry_still_rejects_red_and_wide_spread():
    host,a,obs=prepared()
    for o in [replace(obs(2,10.02),bar_open=10.03),replace(obs(2,10.02),bid=9.9,ask=10.1)]:
        assert not any(i.action=='enter_long' for i in host.evaluate(a,o).evaluation.intents)


def test_post_breakout_enables_only_new_rejections():
    host,a,obs=prepared()
    entered=host.evaluate(a,obs(2,10.02))
    a=replace(a,state=entered.state,status=S.AssignmentStatus.MANAGING)
    r=host.evaluate(a,replace(obs(3,10.13),position_quantity=100,average_price=10.02))
    assert r.state['historical_hod_entry']['setup']['phase']=='post_breakout'
    assert not r.state['v7_encounters']
    end=obs(4,10.12).observed_at.timestamp()
    r.state['v7_encounters']=dict(session=r.state['historical_hod_state']['session'],levels={
        'x':dict(level=dict(price=10.3,lower=10.2,upper=10.4),threshold=10.31,failure_threshold=10.15,
            status='warning',warning=dict(open=10.2,end=end))})
    o=replace(obs(4,10.12),position_quantity=100,average_price=10.02,
        evaluation_events=('market_data_update',),changed_source_ids=('market.last_price',))
    r=host.evaluate(replace(a,state=r.state),o)
    assert any(i.action=='exit' and i.reason=='topping_rejection_next_open' for i in r.evaluation.intents)
