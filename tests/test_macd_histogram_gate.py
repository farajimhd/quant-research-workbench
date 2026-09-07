from dataclasses import replace
from datetime import timedelta
import pytest
from src.trading_runtime import strategy_engine as S
from tests.test_hod_resistance_entry_stop import policy, observation
from tests.test_long_momentum_strategy import NOW, assignment


@pytest.mark.parametrize('reentry',[False,True])
@pytest.mark.parametrize('line,signal,enters,exits',[
    (16,10,True,False),(15,10,False,False),(14,10,False,True),
    (2,-4,True,False),(0,-6,False,False),(-1,-7,False,False),
    (40,39,False,True),(10,11,False,True)])
def test_real_entry_and_exit_strict_signed_gap(line,signal,enters,exits,reentry):
    p=policy();p.update(macd_histogram_gate_bps=5,require_completed_entry_candle=True,
        completed_macd_setup=True,require_open_macd_for_entry=True,strict_green_entry=True)
    p['structural_entry']['entry_level_ordinal_below_high']=1
    p['reentry']['require_new_confirmation']=False
    p['momentum_management']['downside_loss_guard']['below_vwap']=False
    p['momentum_management']['resistance_rejection_exit']=False
    sources={key:{'value':value,'observed_at':NOW.isoformat()} for key,value in {
        'indicator.flow_structure.score@100ms':.7,'indicator.flow_structure.confidence@100ms':.8,
        'indicator.macd.line@5s':.4,'indicator.macd.signal@5s':.2,'indicator.macd.histogram@5s':.2}.items()}
    obs=replace(observation(103.3),bar_open=103.1,source_values=sources,
        macd_line=line*103.3/10000,macd_signal=signal*103.3/10000,macd_histogram=(line-signal)*103.3/10000)
    state={'reentries':1,'last_exit_at':(NOW-timedelta(seconds=3)).isoformat()} if reentry else {}
    result=S.LongMomentumStrategyEngine(revision=47).evaluate(assignment(strategy_revision=47,parameters=p,state=state),obs)
    assert any(i.action=='enter_long' for i in result.evaluation.intents)==enters,[s.reason for s in result.evaluation.signals]
    route=S._matching_momentum_management_route(p,obs,{'entry_at':NOW.isoformat()},gain_pct=1,side='long')
    assert bool(route)==exits
    if exits:
        assert route['mechanism']=='macd_histogram_gap_below_threshold'
    assert S._matching_momentum_management_route(p,replace(obs,evaluation_events=('market_data_update',)),
        {'entry_at':NOW.isoformat()},gain_pct=1,side='long') is None
