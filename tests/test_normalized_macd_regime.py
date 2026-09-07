from dataclasses import replace
from datetime import timedelta

import pytest

from src.trading_runtime import strategy_engine as S
from tests.test_breakout_confirmation import policy
from tests.test_long_momentum_r3_acceptance import bar
from tests.test_long_momentum_strategy import NOW, assignment


def parameters():
    p = policy()
    p['normalized_macd_threshold_bps'] = 30
    return S.resolve_long_momentum_parameters(p, revision=47)


@pytest.mark.parametrize('line,signal,exit_expected', [
    (29,29.001,True),(29,30,False),(29,31,False),(31,32,False),
    (29,28,False),(29,29,False),(-2,-1,True),
])
@pytest.mark.parametrize('gain', [-1,1])
@pytest.mark.parametrize('price', [6,100])
def test_macd_exit_boundary_and_price_scaling(line,signal,exit_expected,gain,price):
    p = parameters()
    p['momentum_management']['downside_loss_guard']['below_vwap'] = False
    obs = replace(bar(),price=price,macd_line=line*price/10000,macd_signal=signal*price/10000)
    route = S._matching_momentum_management_route(p,obs,{'entry_at':NOW.isoformat()},gain_pct=gain,side='long')
    assert bool(route) == exit_expected
    if route:
        assert route['position_fraction'] == 1
        assert route['evidence']['normalized_regime']['exit_confirmed']


@pytest.mark.parametrize('reentry', [False,True])
@pytest.mark.parametrize('line,signal,enters', [(31,32,True),(30,31,False),(0,-2,False),(-1,-2,False),(29,28.6,False),(29,27,True)])
def test_real_engine_entry_and_reentry(line,signal,enters,reentry):
    p = parameters()
    p['reentry']['require_new_confirmation'] = False
    p['structural_entry']['break_above_upper_bound'] = False
    sources = {'indicator.flow_structure.score@100ms':{'value':.7},
        'indicator.flow_structure.confidence@100ms':{'value':.8},
        'indicator.macd.line@5s':{'value':.4},'indicator.macd.signal@5s':{'value':.2},
        'indicator.macd.histogram@5s':{'value':.2}}
    state = {'reentries':1,'last_exit_at':(NOW-timedelta(seconds=3)).isoformat(),
        'histogram_slope_reentry_gate':{'exit_timestamp':NOW.timestamp()-3}} if reentry else {}
    obs = bar(macd_line=line*101.2/10000,macd_signal=signal*101.2/10000,source_values=sources)
    result = S.LongMomentumStrategyEngine(revision=47).evaluate(assignment(strategy_revision=47,parameters=p,state=state),obs)
    assert any(i.action=='enter_long' for i in result.evaluation.intents) == enters, [s.reason for s in result.evaluation.signals]
    assert not result.state.get('histogram_slope_reentry_gate')


def test_slope_disabled_and_resistance_rejection_preserved():
    p = parameters()
    assert not p['momentum_management']['histogram_slope_exit']['enabled']
    assert not p['entry_candle_confirmation']['slope_reentry_break_previous_high']
    assert p['momentum_management']['resistance_rejection_exit']
    from tests.test_point_structure_strategy import row
    state = {'entry_at':NOW.isoformat(),'entry_reference_price':100}
    for second,price in enumerate((100,101.5,100.99)):
        obs = replace(bar(second,close=price),macd_line=.4,macd_signal=.5,
            structural_resistance_levels=(dict(row(101.5),band_lower=101,band_upper=102),))
        route = S._matching_momentum_management_route(p,obs,state,gain_pct=1,side='long')
    assert route['mechanism']=='resistance_rejection'


@pytest.mark.parametrize('line,signal,exits', [(29,29.001,True),(31,32,False)])
def test_real_engine_macd_exit(line,signal,exits):
    p=parameters()
    p['protection']['trailing']['enabled']=False
    p['phase_policy']={'exit':{'mode':'automatic','rule_sets':[]}}
    current=assignment(strategy_revision=47,parameters=p,status=S.AssignmentStatus.MANAGING,
        state={'entry_at':NOW.isoformat(),'entry_reference_price':100,'active_stop':90,
               'initial_stop':90,'high_water_price':101.2})
    obs=replace(bar(),macd_line=line*101.2/10000,macd_signal=signal*101.2/10000,
        position_quantity=10,structural_support_levels=(),structural_resistance_levels=())
    result=S.LongMomentumStrategyEngine(revision=47).evaluate(current,obs)
    assert any(i.action=='exit' for i in result.evaluation.intents)==exits, [i.action for i in result.evaluation.intents]
    if exits:
        assert result.state['last_exit_reason']=='macd_signal_crossed_above_line'


@pytest.mark.parametrize('value', [0,-1,float('nan'),float('inf')])
def test_invalid_threshold_rejected(value):
    p=policy();p['normalized_macd_threshold_bps']=value
    with pytest.raises(ValueError):
        S.resolve_long_momentum_parameters(p,revision=47)


@pytest.mark.parametrize('line,signal,exits', [(29,30,True),(31,32,True),(100,110,True),(32,31,False),(32,32,False)])
@pytest.mark.parametrize('gain', [-1,1])
def test_exit_strength_exemption_removed_but_only_on_completed_bars(line,signal,exits,gain):
    p=parameters();p.update(macd_exit_ignore_strength_threshold=True,completed_macd_setup=True)
    p['momentum_management']['downside_loss_guard']['below_vwap']=False
    obs=replace(bar(),macd_line=line*101.2/10000,macd_signal=signal*101.2/10000)
    state={'entry_at':NOW.isoformat()}
    assert bool(S._matching_momentum_management_route(p,obs,state,gain_pct=gain,side='long'))==exits
    intrabar=replace(obs,evaluation_events=('market_data_update',))
    assert S._matching_momentum_management_route(p,intrabar,state,gain_pct=gain,side='long') is None
    assert S._normalized_macd_regime(p,obs)['entry_gap_bypassed']==S._normalized_macd_regime(parameters(),obs)['entry_gap_bypassed']


def test_real_engine_exits_above_30bps_with_new_policy():
    p=parameters();p.update(macd_exit_ignore_strength_threshold=True,completed_macd_setup=True)
    p['protection']['trailing']['enabled']=False
    p['phase_policy']={'exit':{'mode':'automatic','rule_sets':[]}}
    current=assignment(strategy_revision=47,parameters=p,status=S.AssignmentStatus.MANAGING,
        state={'entry_at':NOW.isoformat(),'entry_reference_price':100,'active_stop':90,'initial_stop':90,'high_water_price':101.2})
    obs=replace(bar(),macd_line=.4,macd_signal=.5,position_quantity=10,
                structural_support_levels=(),structural_resistance_levels=())
    result=S.LongMomentumStrategyEngine(revision=47).evaluate(current,obs)
    assert any(i.action=='exit' for i in result.evaluation.intents)
    assert result.state['last_exit_reason']=='macd_signal_crossed_above_line'


@pytest.mark.parametrize('reentry',[False,True])
@pytest.mark.parametrize('line,signal,enters',[(49,49.2,False),(38,47,False),(32,32,False),(32,31.9,True)])
def test_open_macd_required_even_when_strong(line,signal,enters,reentry):
    p=parameters();p.update(require_open_macd_for_entry=True,macd_exit_ignore_strength_threshold=True)
    p['reentry']['require_new_confirmation']=False
    p['structural_entry']['break_above_upper_bound']=False
    state={'reentries':1,'last_exit_at':(NOW-timedelta(seconds=3)).isoformat()} if reentry else {}
    sources={'indicator.flow_structure.score@100ms':{'value':.7},
        'indicator.flow_structure.confidence@100ms':{'value':.8},
        'indicator.macd.line@5s':{'value':.4},'indicator.macd.signal@5s':{'value':.2},
        'indicator.macd.histogram@5s':{'value':.2}}
    obs=bar(macd_line=line*101.2/10000,macd_signal=signal*101.2/10000,source_values=sources)
    result=S.LongMomentumStrategyEngine(revision=47).evaluate(assignment(strategy_revision=47,parameters=p,state=state),obs)
    assert any(i.action=='enter_long' for i in result.evaluation.intents)==enters, [s.reason for s in result.evaluation.signals]
