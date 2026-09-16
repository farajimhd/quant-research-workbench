import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from src.trading_runtime import historical_hod as H, stalled_setup as T, strategy_engine as S
from tests.test_v7_setup import prepared


def evidence():
    entry = dict(first_fill_at=1000., setup=dict(phase='building', phase_progress=dict(
        observed_at=1120., ready=False, initial_fill=10., initial_risk=.2,
        required_r=.5, threshold=10.1)))
    quality = dict(checks={'current_trade_rate_10s':False, 'current_trade_rate_60s':False,
        'fresh_uncrossed_quote':True, 'detector_fresh':True,
        'market.trade_rate_10s_fresh':True, 'market.trade_rate_60s_fresh':True})
    return entry, quality


def test_elapsed_boundary_and_json_restoration():
    entry, quality = evidence()
    restored = json.loads(json.dumps(entry))
    assert T.assess(restored, quality, now=1120., minimum_seconds=120., completed_candle=True)['passed']
    assert not T.assess(restored, quality, now=1119., minimum_seconds=120., completed_candle=True)['passed']
    assert not T.assess(restored, quality, now=1120., minimum_seconds=0., completed_candle=True)['passed']


@pytest.mark.parametrize('change', ['missing_fill','future_fill','nan_fill','post_breakout',
    'progress_ready','stale_progress','unknown_risk','missing_rate','fresh_rate','stale_quote','stale_rate','intrabar'])
def test_incomplete_or_developed_setup_does_not_exit(change):
    entry, quality = evidence()
    completed = True
    if change=='missing_fill':entry.pop('first_fill_at')
    elif change=='future_fill':entry['first_fill_at']=1121.
    elif change=='nan_fill':entry['first_fill_at']=float('nan')
    elif change=='post_breakout':entry['setup']['phase']='post_breakout'
    elif change=='progress_ready':entry['setup']['phase_progress']['ready']=True
    elif change=='stale_progress':entry['setup']['phase_progress']['observed_at']=1119.
    elif change=='unknown_risk':entry['setup']['phase_progress']['initial_risk']=0.
    elif change=='missing_rate':quality['checks'].pop('current_trade_rate_10s')
    elif change=='fresh_rate':quality['checks']['current_trade_rate_10s']=True
    elif change=='stale_quote':quality['checks']['fresh_uncrossed_quote']=False
    elif change=='stale_rate':quality['checks']['market.trade_rate_60s_fresh']=False
    elif change=='intrabar':completed=False
    assert not T.assess(entry, quality, now=1120., minimum_seconds=120., completed_candle=completed)['passed']


def test_strategy_exit_cancels_acquisition_and_latches(monkeypatch):
    host,a,obs=prepared()
    entered=host.evaluate(a,obs(2,10.02))
    assert any(i.action=='enter_long' for i in entered.evaluation.intents)
    p=deepcopy(a.parameters)
    p['historical_hod'].update(setup_stalled_seconds=120.,setup_phase_minimum_progress_r=.5)
    p['liquidity_admission']['latched']=True
    state=deepcopy(entered.state)
    o=replace(obs(3,10.02),position_quantity=100,average_price=10.02)
    entry=state['historical_hod_entry']
    entry.update(first_fill_at=o.observed_at.timestamp()-120, initial_fill_price=10.02,
                 initial_risk=.1,fill_risk_frozen=True)
    _, quality=evidence()
    monkeypatch.setattr(H,'tradability',lambda *a,**kw:(False,deepcopy(quality)))
    a=replace(a,parameters=p,state=state,status=S.AssignmentStatus.MANAGING)
    result=host.evaluate(a,o)
    exits=[i for i in result.evaluation.intents if i.action=='exit']
    assert len(exits)==1 and exits[0].reason=='stalled_setup' and exits[0].quantity==100
    assert exits[0].metadata['cancel_entry_acquisition'] is True
    assert result.state['entry_acquisition_exit_latched'] is True
    again=host.evaluate(replace(a,state=result.state,status=result.status),replace(o,observed_at=o.observed_at+timedelta(milliseconds=1)))
    assert all(i.action not in ('enter_long','add_long') for i in again.evaluation.intents)


def test_fill_clock_starts_on_real_increment_and_survives_partial_fills():
    host,a,obs=prepared()
    o=obs(2,10.02);entered=host.evaluate(a,o)
    p=deepcopy(a.parameters)
    p['historical_hod'].update(setup_stalled_seconds=120.,setup_phase_minimum_progress_r=.5)
    p['liquidity_admission']['latched']=True
    a=replace(a,parameters=p)
    assert 'first_fill_at' not in entered.state['historical_hod_entry']
    assigned=S.AssignedLongMomentumStrategy([replace(a,state=entered.state,status=entered.status)])
    fill=SimpleNamespace(assignment_id=a.assignment_id,state='partially_filled',action='enter_long',
        fill_incremental_quantity=40.,filled_quantity=40.,updated_at=o.observed_at+timedelta(seconds=1))
    asyncio.run(assigned.on_order_group_update(fill,aggregate_position_quantity=40.))
    first=assigned.assignments()[0].state['historical_hod_entry']['first_fill_at']
    assert first==fill.updated_at.timestamp()
    fill.updated_at+=timedelta(seconds=5);fill.fill_incremental_quantity=60.;fill.filled_quantity=100.
    asyncio.run(assigned.on_order_group_update(fill,aggregate_position_quantity=100.))
    assert assigned.assignments()[0].state['historical_hod_entry']['first_fill_at']==first


@pytest.mark.parametrize('duration', [-1, True, float('nan'), float('inf')])
def test_invalid_stall_duration_is_rejected(duration):
    _,a,_=prepared();p=deepcopy(a.parameters)
    p['historical_hod']['setup_stalled_seconds']=duration
    with pytest.raises(ValueError,match='Stalled setup duration'):
        H.configure(p)


def test_enabled_exit_requires_current_quality_contract():
    _,a,_=prepared();p=deepcopy(a.parameters)
    p['historical_hod'].update(setup_stalled_seconds=120.,setup_phase_minimum_progress_r=.5)
    p['liquidity_admission']['latched']=False
    with pytest.raises(ValueError,match='requires latched liquidity admission'):
        H.configure(p)
