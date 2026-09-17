import json
from copy import deepcopy

import pytest

from src.trading_runtime import pullback_impulse as I
from tests.test_post_move_entries import pullback


def impulse(scale=1):
    state = {}
    for i in range(301):
        I.observe(state, i / 10, scale * (100 + 5*i/300), 5)
    return state


def test_full_30_seconds_and_inclusive_five_percent_required():
    state = {}
    for i in range(300):
        I.observe(state, i/10, 100 if i == 0 else 105, 5)
    assert 'move' not in state
    I.observe(state, 30, 105, 5)
    assert state['move']['base'] == 100
    assert state['move']['peak'] == 105
    assert len(state['prices']) == 301
    before = deepcopy(state)
    I.observe(state, 29, 1000, 5)
    assert state == before


@pytest.mark.parametrize('fraction,valid', [(.199,False),(.20,True),(.30,True),(.45,True),(.451,False)])
@pytest.mark.parametrize('scale', [.01,1,100])
def test_retracement_boundaries_and_price_scale(fraction,valid,scale):
    state = impulse(scale)
    anchor = {'swing':dict(pivot_at=32,price=scale*(105-5*fraction))}
    proof = I.qualify({'pullback_impulse':state},anchor,[])
    assert bool(proof) == valid
    assert I.qualify({'pullback_impulse':state},anchor,[30]) is None
    anchor['swing']['pivot_at'] = 30.5
    assert I.qualify({'pullback_impulse':state},anchor,[]) is None


def test_deep_correction_cannot_revive():
    state = impulse()
    I.observe(state,30.1,102,5)
    I.observe(state,30.2,104,5)
    assert state['move']['invalid']


def test_sparse_candles_use_last_price_at_or_before_boundary():
    state = {}
    I.observe(state,0,100,5)
    I.observe(state,10,104,5)
    I.observe(state,31,105,5)
    assert state['move']['base_at'] == 0
    assert state['move']['base'] == 100
    # Price at t=10 must not leak backwards into the t=1 boundary.
    assert state['move']['peak'] == 105


def test_checkpoint_roundtrip_and_bounded_history():
    a = impulse()
    b = json.loads(json.dumps(a))
    for i in range(301,1000):
        I.observe(a,i/10,105,5)
        I.observe(b,i/10,105,5)
    assert a == b
    assert len(a['prices']) == 301


def test_threshold_chatter_does_not_rebase_one_rally():
    state = impulse()
    I.observe(state,30.1,104.9,5)
    I.observe(state,30.2,105.1,5)
    assert state['move']['id'] == 30
    assert state['move']['base'] == 100
    assert state['move']['peak'] == 105.1


def test_strategy_rejects_local_bounce_without_impulse_then_consumes_move():
    host,a,o = pullback()
    a.parameters['vwap_ladder'].update(group_resistances=1,pullback_min_rise_pct=5.)
    assert not host.evaluate(a,o).evaluation.intents
    t = o.observed_at.timestamp()
    a.state['vwap_ladder_market']['pullback_impulse'] = dict(
        move=dict(id=t-10,base=3.5,base_at=t-40,peak=4.1,peak_at=t-5))
    r = host.evaluate(a,o)
    assert r.evaluation.signals[0].reason == 'post_move_pullback'
    assert r.state['post_move_entry_clock']['consumed_moves'] == [t-10]
    assert r.evaluation.intents[0].metadata['pullback_move']['retracement'] == pytest.approx(.3)


def test_immutable_successor_enables_approved_threshold():
    from src.backend.impulse_pullback_candidate import BASELINE_ID, BASELINE_HASH, PLAN, prepare_payload
    baseline = dict(candidate_id=BASELINE_ID,content_hash=BASELINE_HASH,payload=dict(
        strategy={'profiles':[dict(profile_id='vwap-grouped-pullback-breakout-v3',parameters={'vwap_ladder':{}})]},
        run_plans={'plans':[dict(run_plan_id=PLAN,allowed_environments=['backtest'])]}))
    before = deepcopy(baseline)
    result = prepare_payload(baseline)
    assert baseline == before
    assert result['strategy']['profiles'][-1]['parameters']['vwap_ladder']['pullback_min_rise_pct'] == 5


def test_qualified_pullback_waits_for_macd_without_one_second_expiry():
    host,a,o = pullback()
    a.parameters['vwap_ladder'].update(group_resistances=1,pullback_min_rise_pct=5.)
    t = o.observed_at.timestamp()
    row = o.structural_detector_state['row']
    row['local_swings'][0].update(pivot_at=t-22,confirmed_at=t-20)
    row['vwap_retests'][0].update(pivot_at=t-22,recovered_at=t-20)
    a.state['vwap_ladder_market']['pullback_impulse'] = dict(
        move=dict(id=t-30,base=3.5,base_at=t-60,peak=4.1,peak_at=t-25))
    r = host.evaluate(a,o)
    assert r.evaluation.signals[0].reason == 'post_move_pullback'
    a.state['vwap_ladder_market']['pullback_impulse']['move']['invalid'] = True
    assert not host.evaluate(a,o).evaluation.intents


def test_ineligible_higher_anchor_does_not_hide_qualified_pullback():
    host,a,o = pullback()
    a.parameters['vwap_ladder'].update(group_resistances=1,pullback_min_rise_pct=5.)
    t = o.observed_at.timestamp()
    a.state['vwap_ladder_market']['pullback_impulse'] = dict(
        move=dict(id=t-10,base=3.5,base_at=t-40,peak=4.1,peak_at=t-5))
    row = o.structural_detector_state['row']
    invalid = dict(row['local_swings'][0],price=4.,pivot_at=t-1)
    witness = deepcopy(row['vwap_retests'][0])
    witness.update(pivot_at=t-1,pivot_price=4.)
    witness['anchor'].update(lower=3.95,upper=3.99)
    row['local_swings'].append(invalid)
    row['vwap_retests'].append(witness)
    r = host.evaluate(a,o)
    assert r.evaluation.signals[0].reason == 'post_move_pullback'
    assert r.evaluation.intents[0].metadata['initial_swing']['price'] == 3.92
