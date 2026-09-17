from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.trading_runtime import v7_immediate_exits as X
from src.backend.replay_run_service import _ProvisionalMacdState
from tests.test_v7_setup import prepared
from src.trading_runtime import strategy_engine as S

SETTINGS = dict(setup_immediate_tail_body_ratio=.3,setup_entry_resistance_seconds=5.,
                setup_resistance_return_exit=1)
LEVEL = dict(unified_level_id=1,side='resistance',lower=10.,price=10.1,upper=10.2,confirmed_at_ms=99000)


def observation(at,price,opening=None,high=None):
    return SimpleNamespace(observed_at=datetime.fromtimestamp(at,timezone.utc),price=price,
        average_price=10.1,bar_open=opening,bar_high=high,
        evaluation_events=('market_data_update',),changed_source_ids=('market.last_price',))


def test_forming_tail_threshold_quotes_and_restart():
    entry=dict(first_fill_at=100.)
    market=dict(prior_rows=[LEVEL])
    assert X.assess(entry,observation(100.2,10.1,10.,10.13),market,SETTINGS,False)[0] == ''
    restored=deepcopy(entry)
    o=observation(100.3,10.09,10.,10.13)
    assert X.assess(restored,o,market,SETTINGS,False)[0] == 'topping_tail_immediate'
    o.changed_source_ids=()
    assert X.assess(entry,o,market,SETTINGS,False)[0] == ''
    assert X.assess(entry,observation(100.4,10.,10.,10.01),market,SETTINGS,False)[0] == 'topping_tail_immediate'


def test_dwell_uses_fill_clock_and_departure_disarms():
    entry=dict(first_fill_at=100.,entry_resistance_bands={'1':dict(level=LEVEL)})
    assert not X.assess(entry,observation(104.99,10.1),{},SETTINGS,False)[0]
    copy=deepcopy(entry)
    assert X.assess(entry,observation(105.,10.1),{},SETTINGS,False)[0]=='entry_resistance_timeout'
    X.assess(copy,observation(104.995,10.21),{},SETTINGS,False)
    assert not X.assess(copy,observation(105.,10.1),{},SETTINGS,False)[0]


def test_break_requires_crossing_and_completed_close_below_frozen_band():
    entry=dict(first_fill_at=100.)
    X.assess(entry,observation(100.1,10.1),dict(prior_rows=[LEVEL]),SETTINGS,False)
    X.assess(entry,observation(100.2,10.21),dict(prior_rows=[LEVEL]),SETTINGS,False)
    assert not X.assess(entry,observation(100.3,9.99),{},SETTINGS,False)[0]
    market=dict(bar=dict(time=101.,end=102.,open=10.1,high=10.15,low=9.98,close=9.99))
    assert X.assess(entry,observation(102.,9.99),market,SETTINGS,True)[0]=='broken_resistance_close_below'


def test_reentry_floor_survives_restart_and_never_recovers_after_breach():
    state=dict(topping_tail_reentry=dict(session='day',at=100.2,close=10.1,breached=False,episode=None))
    X.observe_floor(state,observation(100.3,10.1),dict(session='day'),False)
    assert not state['topping_tail_reentry']['breached']
    state=deepcopy(state)
    X.observe_floor(state,observation(100.4,10.09),dict(session='day'),False)
    X.observe_floor(state,observation(100.5,10.2),dict(session='day'),False)
    assert state['topping_tail_reentry']['breached']


def test_forming_high_is_causal_and_checkpointed():
    state=_ProvisionalMacdState()
    for at,price in [(100.1,10.),(100.2,10.2),(100.3,10.1)]:
        state.observe_forming_candle(price,datetime.fromtimestamp(at,timezone.utc))
    state=_ProvisionalMacdState.restore(state.checkpoint())
    assert state.forming_open==10. and state.forming_high==10.2
    state.observe_forming_candle(9.9,datetime.fromtimestamp(101.1,timezone.utc))
    assert state.forming_open==state.forming_high==9.9


@pytest.mark.parametrize('closed',[False,True])
def test_runnable_strategy_tail_clock_and_pending_fill_gate(closed):
    host,a,obs=prepared()
    entered=host.evaluate(a,obs(2,10.02))
    assert any(i.action=='enter_long' for i in entered.evaluation.intents)
    p=deepcopy(a.parameters);p['historical_hod'].update(SETTINGS)
    p['historical_hod']['setup_tail_closed_candle']=int(closed)
    state=deepcopy(entered.state)
    state['historical_hod_entry']['first_fill_at']=obs(2,10.02).observed_at.timestamp()
    a=replace(a,parameters=p,state=state,status=S.AssignmentStatus.MANAGING)
    o=replace(obs(3,10.04),position_quantity=100,average_price=10.02,bar_open=10.02,
        bar_high=10.08,source_timeframe='',evaluation_events=('market_data_update',),
        changed_source_ids=('market.last_price',))
    if closed:
        assert not any(i.action=='exit' for i in host.evaluate(a,o).evaluation.intents)
        o=replace(o,source_timeframe='1s',evaluation_events=('bar_close',),changed_source_ids=())
    result=host.evaluate(a,o)
    exits=[i for i in result.evaluation.intents if i.action=='exit']
    assert len(exits)==1 and exits[0].reason=='topping_tail_immediate'
    assert exits[0].metadata['cancel_entry_acquisition']
    assert result.state['topping_tail_reentry']['close']==10.04
    assert exits[0].metadata['immediate_exits']['candle']['forming'] is not closed
    pending=host.evaluate(replace(a,state=result.state,status=result.status),replace(o,pending_exit_quantity=100))
    assert not pending.evaluation.intents


def test_reentry_preserves_gates_and_requests_tight_fill_protection():
    host,a,obs=prepared()
    p=deepcopy(a.parameters);p['historical_hod'].update(SETTINGS)
    state=deepcopy(a.state)
    state['topping_tail_reentry']=dict(session=state['historical_hod_state']['session'],
        at=obs(1,10.01).observed_at.timestamp(),close=10.01,breached=False,
        episode=obs(2,10.02).observed_at.timestamp())
    a=replace(a,parameters=p,state=state)
    result=host.evaluate(a,obs(2,10.02))
    intent=next(i for i in result.evaluation.intents if i.action=='enter_long')
    assert intent.invalidation_price==pytest.approx(10.01)
    assert intent.metadata['tight_reentry_stop']==dict(tick_size=.01)
    state['topping_tail_reentry']['breached']=True
    ordinary=host.evaluate(replace(a,state=state),obs(2,10.02))
    normal=next(i for i in ordinary.evaluation.intents if i.action=='enter_long')
    assert 'tight_reentry_stop' not in normal.metadata
    without_floor=deepcopy(state);without_floor.pop('topping_tail_reentry')
    baseline=host.evaluate(replace(a,state=without_floor),obs(2,10.02))
    expected=next(i for i in baseline.evaluation.intents if i.action=='enter_long')
    assert normal.invalidation_price==expected.invalidation_price
    assert normal.reason==expected.reason


def test_closed_tail_ignores_forming_wick_and_uses_completed_ohlc_once():
    settings=dict(SETTINGS,setup_tail_closed_candle=1)
    entry=dict(first_fill_at=100.)
    o=observation(100.3,10.09,10.,10.13)
    assert X.assess(entry,o,{},settings,False)[0]==''
    # The forming wick recovered before close: there must be no exit.
    market=dict(bar=dict(time=100.,end=101.,open=10.,high=10.13,low=10.,close=10.13))
    assert X.assess(entry,observation(101.,10.13),market,settings,True)[0]==''
    market['bar']=dict(time=101.,end=102.,open=10.,high=10.13,low=10.,close=10.09)
    reason,evidence=X.assess(entry,observation(102.,10.09),market,settings,True)
    assert reason=='topping_tail_immediate'
    assert evidence['candle']['forming'] is False
    assert evidence['candle']['close']==10.09 and evidence['candle']['end']==102.
    assert not X.assess(entry,observation(102.,10.09),market,settings,True)[0]


def test_expired_tail_does_not_cancel_an_ordinary_pending_entry():
    host,a,obs=prepared()
    p=deepcopy(a.parameters);p['historical_hod'].update(SETTINGS)
    state=deepcopy(a.state)
    state['topping_tail_reentry']=dict(session=state['historical_hod_state']['session'],
        at=obs(1,10.01).observed_at.timestamp(),close=10.03,breached=True)
    a=replace(a,parameters=p,state=state)
    entered=host.evaluate(a,obs(2,10.02))
    assert not entered.state['historical_hod_entry']['tail_reentry']
    pending=host.evaluate(replace(a,state=entered.state,status=entered.status),
        replace(obs(2,10.02),source_timeframe='',evaluation_events=('market_data_update',),
                changed_source_ids=('market.last_price',)))
    assert not any(i.action=='cancel_entry' for i in pending.evaluation.intents)


def test_first_floor_breach_clock_is_not_overwritten():
    state=dict(topping_tail_reentry=dict(session='day',at=100.,close=10.1,breached=False,episode=None))
    for at in (101.,102.):
        X.observe_floor(state,observation(at,10.09),dict(session='day'),False)
    assert state['topping_tail_reentry']['breached_at']==101.


def test_first_completed_tail_after_midcandle_fill_is_eligible():
    settings=dict(SETTINGS,setup_tail_closed_candle=1)
    market=dict(bar=dict(time=100.,end=101.,open=10.,high=10.13,low=10.,close=10.09))
    assert X.assess(dict(first_fill_at=100.5),observation(101.,10.09),market,settings,True)[0]=='topping_tail_immediate'
    assert not X.assess(dict(first_fill_at=101.),observation(101.,10.09),market,settings,True)[0]


def test_unbreached_floor_expires_with_its_original_momentum_episode():
    state=dict(topping_tail_reentry=dict(session='day',at=100.,close=10.1,breached=False,episode=90.))
    X.observe_floor(state,observation(101.,10.2),dict(session='day',episode=90.),False)
    assert not state['topping_tail_reentry'].get('expired')
    X.observe_floor(state,observation(102.,10.2),dict(session='day',episode=None),False)
    assert state['topping_tail_reentry']['expired']
    X.observe_floor(state,observation(103.,10.2),dict(session='day',episode=103.),False)
    assert state['topping_tail_reentry']['expired_at']==102.


def test_fresh_episode_entry_keeps_ordinary_stop_even_above_old_tail_close():
    host,a,obs=prepared()
    p=deepcopy(a.parameters);p['historical_hod'].update(SETTINGS)
    state=deepcopy(a.state)
    state['topping_tail_reentry']=dict(session=state['historical_hod_state']['session'],
        at=obs(1,10.01).observed_at.timestamp(),close=10.01,breached=False,episode=1.)
    result=host.evaluate(replace(a,parameters=p,state=state),obs(2,10.02))
    entry=next(i for i in result.evaluation.intents if i.action=='enter_long')
    assert 'tight_reentry_stop' not in entry.metadata
    assert result.state['topping_tail_reentry']['expired']


def test_missing_fill_clock_cannot_arm_position_exits():
    assert not X.assess({},observation(100.,10.01,10.,10.2),{},SETTINGS,False)[0]


def test_successor_keeps_parent_immutable_and_lowers_both_price_gates():
    from src.backend.strategy_222_immediate_candidate import (
        prepare_payload,BASELINE_ID,BASELINE_HASH,PARENT_PROFILE,PROFILE,PLAN)
    parent=dict(profile_id=PARENT_PROFILE,parameters=dict(liquidity_admission=dict(minimum_price=2.),
        historical_hod=dict(setup_minimum_session_relative_volume=2.)))
    baseline=dict(candidate_id=BASELINE_ID,content_hash=BASELINE_HASH,payload=dict(
        strategy=dict(profiles=[parent]),run_plans=dict(plans=[dict(run_plan_id=PLAN,
            profile_id=PARENT_PROFILE,allowed_environments=['backtest'])]),
        market_discovery=dict(rule_sets=[dict(rule_set_id='v7-setup-recovery-v9-tradability',
            conditions=[dict(condition_id='tradability-0',value=2.,comparator='greater_or_equal')])])) )
    before=deepcopy(baseline)
    payload=prepare_payload(baseline,{})
    assert baseline==before
    assert payload['strategy']['profiles'][0]==parent
    successor=next(p for p in payload['strategy']['profiles'] if p['profile_id']==PROFILE)
    assert successor['parameters']['liquidity_admission']['minimum_price']==1.
    assert successor['parameters']['historical_hod']['setup_minimum_session_relative_volume']==2.
    assert payload['market_discovery']['rule_sets'][0]['conditions'][0]['value']==1.
    from src.backend.strategy_222_immediate_candidate import (
        prepare_closed_payload,CLOSED_BASELINE_ID,CLOSED_BASELINE_HASH,CLOSED_PROFILE)
    closed_baseline=dict(candidate_id=CLOSED_BASELINE_ID,content_hash=CLOSED_BASELINE_HASH,payload=payload)
    saved=deepcopy(closed_baseline)
    v2=prepare_closed_payload(closed_baseline,{})
    assert closed_baseline==saved
    closed_profile=next(p for p in v2['strategy']['profiles'] if p['profile_id']==CLOSED_PROFILE)
    expected=deepcopy(successor['parameters'])
    expected['historical_hod']['setup_tail_closed_candle']=1
    assert closed_profile['parameters']==expected
