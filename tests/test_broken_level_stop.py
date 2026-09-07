from dataclasses import replace
from types import SimpleNamespace
import asyncio

from src.trading_runtime import strategy_engine as S
from tests.test_confirmed_setup_live_entry import parameters as base_parameters, tick, sample, enters
from tests.test_long_momentum_strategy import assignment, NOW
from tests.test_point_structure_strategy import row


def parameters():
    p=base_parameters();p.update(broken_level_stop_only=True,require_breakout_reset=False)
    p['structural_entry']['recover_stopped_level']=True
    p['protection']['trailing'].update(enabled=True,mode='third_resistance_below_session_high')
    p['protection']['profit_ladder']['enabled']=False
    p['protection']['luld_profit_target']['enabled']=False
    p['reentry'].update(after_protective_exit=True,require_new_confirmation=False)
    return p


def level(price,side):
    return dict(row(price),side=side,band_lower=price-.1,band_upper=price+.1,
                p_norm=.95,load_contract='merged-point-minmax-v1')


def test_highest_crossed_support_or_resistance_and_never_loosen():
    p=parameters();state={'active_stop':100,'previous_observed_price':101,'entry_reference_price':103}
    obs=replace(tick(103.4,103),structural_support_levels=(level(103,1),),
                structural_resistance_levels=(level(102,-1),))
    assert S._ratcheted_stop(obs,p,state,side='long')==103
    assert state['trailing_support_selection']['selected_level']['side']==1
    state.update(active_stop=103,previous_observed_price=103.4)
    assert S._ratcheted_stop(replace(obs,price=102.5),p,state,side='long')==103
    state.update(active_stop=100,previous_observed_price=103.2)
    assert S._ratcheted_stop(obs,p,state,side='long')==100  # No fresh crossing.
    state['previous_observed_price']=101
    weak=replace(obs,structural_support_levels=(dict(level(103,1),p_norm=.1),))
    assert S._ratcheted_stop(weak,p,state,side='long')==102


def test_position_exits_only_on_stop_and_recovers_same_support():
    p=parameters();engine=S.LongMomentumStrategyEngine(revision=47)
    support=level(103,1)
    state={**sample(),'entry_at':NOW.isoformat(),'entry_reference_price':102,
           'active_stop':101,'initial_stop':101,'last_price':102.9,'high_water_price':103}
    obs=replace(tick(103.4,103.2),position_quantity=10,
                structural_support_levels=(support,),structural_resistance_levels=())
    result=engine.evaluate(assignment(strategy_revision=47,parameters=p,state=state),obs)
    assert any(i.action=='replace_protective_stop' and i.invalidation_price==103 for i in result.evaluation.intents)
    held=engine.evaluate(assignment(strategy_revision=47,parameters=p,state=result.state),replace(obs,price=103.2,macd_line=-1,macd_signal=1,evaluation_events=('bar_close',)))
    assert not any(i.action=='exit' for i in held.evaluation.intents)
    stopped=engine.evaluate(assignment(strategy_revision=47,parameters=p,state=result.state),replace(obs,price=103))
    assert any(i.action=='exit' for i in stopped.evaluation.intents)
    assert stopped.state['stopped_level_recovery']['side']==1
    recovery={**sample(), 'stopped_level_recovery':support}
    recovered=engine.evaluate(assignment(strategy_revision=47,parameters=p,state=recovery),replace(obs,position_quantity=0))
    assert enters(recovered)
    assert recovered.state['initial_stop']==103
    assert recovered.state['structural_profit_targets']==[]
    assert all(i.profit_target_price is None for i in recovered.evaluation.intents)
    blocked=engine.evaluate(assignment(strategy_revision=47,parameters=p,state=recovery),replace(obs,position_quantity=0,price=103.1))
    assert not enters(blocked)
    pending=engine.evaluate(assignment(strategy_revision=47,parameters=p,state=recovery,status=S.AssignmentStatus.EXIT_PENDING),replace(obs,position_quantity=0,pending_exit_quantity=10))
    assert not enters(pending)


def test_recovery_requires_open_macd_even_above_30bps():
    p=parameters();state={**sample(.5,.6),'stopped_level_recovery':level(103,1)}
    obs=replace(tick(103.4,103.2),structural_support_levels=(level(103,1),))
    result=S.LongMomentumStrategyEngine(revision=47).evaluate(assignment(strategy_revision=47,parameters=p,state=state),obs)
    assert not enters(result)


def test_protection_profile_contains_no_profit_order():
    p=parameters();p['protection_profile_catalog']={'test':{'slices':[{'slice_id':'one','quantity_fraction':1,
        'strategy_profit_target_index':0,'profit_target_price':120,'stop':{'rule_type':'fixed_price','price':101}}]}}
    profile=S._protection_profile_from_phase({'protection_profile':'test'},observation=tick(),action='enter_long',quantity=10,
        parameters=p,state={'structural_profit_targets':[120]},invalidation_price=101,profit_target_price=120,trailing_amount=None)
    assert len(profile.slices)==1
    assert profile.slices[0].quantity_fraction==1
    assert profile.slices[0].profit_target_price is None


def test_broker_protective_fill_captures_recovery_level():
    p=parameters();support=level(103,1)
    assigned=assignment(strategy_revision=47,parameters=p,status=S.AssignmentStatus.EXIT_PENDING,
        state={'last_price':103,'trailing_support_selection':{'selected_level':support}})
    strategy=S.AssignedLongMomentumStrategy([assigned])
    asyncio.run(strategy.on_order_group_update(SimpleNamespace(action='exit',assignment_id=assigned.assignment_id,
        fill_role='protective_exit',reentry_after_fill=True,state='filled',updated_at=NOW),aggregate_position_quantity=0))
    assert strategy.assignments()[0].state['stopped_level_recovery']['side']==1


def test_rejected_stop_replacement_restores_stop_and_level():
    old={'selected_level':level(101,-1)}
    assigned=assignment(strategy_revision=47,parameters=parameters(),status=S.AssignmentStatus.MANAGING,
        state={'active_stop':103,'trailing_support_selection':{'selected_level':level(103,1)}})
    strategy=S.AssignedLongMomentumStrategy([assigned])
    intent=SimpleNamespace(action='replace_protective_stop',metadata={'assignment_id':assigned.assignment_id,
        'previous_stop':101,'previous_support_selection':old})
    asyncio.run(strategy.on_intent_rejected(intent,reasons=('replacement_rejected',),event_time=NOW))
    state=strategy.assignments()[0].state
    assert state['active_stop']==101
    assert state['trailing_support_selection']==old


def test_manual_exit_remains_available():
    state={**sample(),'entry_at':NOW.isoformat(),'entry_reference_price':102,'active_stop':100,
           'initial_stop':100,'last_price':102.3,'manual_exit_requested':True}
    result=S.LongMomentumStrategyEngine(revision=47).evaluate(assignment(strategy_revision=47,parameters=parameters(),state=state),replace(tick(),position_quantity=10))
    assert any(s.reason=='manual_exit' for s in result.evaluation.signals)
