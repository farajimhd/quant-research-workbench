from copy import deepcopy
from dataclasses import replace
from datetime import timedelta

import pytest

from src.trading_runtime import post_move_entries as P
from src.trading_runtime import vwap_resistance_ladder as V
from tests.test_resistance_zones import zone
from tests.test_vwap_resistance_ladder import fixture, NOW, S


def later():
    host, a, observations = fixture()
    a.parameters['vwap_ladder'].update(post_move_entries=1, pullback_independent_episode=1,
                                      require_late_retest=1, allow_retest_stop_fallback=1)
    broken = {str(i): zone(str(i), 2+i*.1, 2+i*.1) for i in range(6)}
    known = {'reference':zone('reference',3.99,4.)}
    known.update({str(p):zone(str(p),p,p) for p in (4.03,4.08,4.15,4.3)})
    state = dict(vwap_ladder_market=dict(session=NOW.date().isoformat(),at=NOW.timestamp(),
                 known=known,broken=list(broken),break_rows=broken),
                 post_move_entry_clock=dict(session=NOW.date().isoformat(),price=4.,consumed_pullbacks=[]))
    o = replace(observations(price=4.01),execution_vwap=3.5,structural_session_high=4.02)
    return host, replace(a,state=state), o


def test_breakout_target_is_midpoint_straddling_average_gap():
    _,a,_ = later()
    market = a.state['vwap_ladder_market']
    selection = P.midpoint_target(market,market['known']['reference'],.01)
    assert selection['average_gap'] == pytest.approx(.1)
    assert selection['raw_midpoint'] == pytest.approx(4.115)
    assert selection['price'] == pytest.approx(4.12)


def test_breakout_reference_is_highest_resistance_below_hod():
    _,a,_ = later()
    market = a.state['vwap_ladder_market']
    assert P.breakout_reference(market,4.10)['upper'] == pytest.approx(4.08)
    assert P.breakout_reference(market,4.08)['upper'] == pytest.approx(4.03)
    market['broken'] = []
    assert P.midpoint_target(market,market['known']['reference'],.01) is None


def test_breakout_uses_highest_band_below_hod_and_four_macds():
    host,a,o = later()
    o.source_values['indicator.macd.line@30s']['value'] = -.1
    r = host.evaluate(a,o)
    intent = r.evaluation.intents[0]
    assert r.evaluation.signals[0].reason == 'post_move_breakout'
    assert intent.invalidation_price == pytest.approx(3.98)
    assert intent.profit_target_price == pytest.approx(4.12)
    assert intent.metadata['initial_swing'] is None
    assert r.state['vwap_ladder_entry']['breakout_setup']['anchor']['unified_level_id'] == 'reference'


@pytest.mark.parametrize('tf', ['100ms','1s','5s','10s'])
def test_breakout_rejects_failed_required_macd(tf):
    host,a,o = later()
    o.source_values[f'indicator.macd.line@{tf}']['value'] = -.1
    assert not host.evaluate(a,o).evaluation.intents


def test_breakout_cannot_enter_from_price_already_above_trigger():
    host,a,o = later()
    a.state['post_move_entry_clock']['price'] = 4.01
    assert not host.evaluate(a,o).evaluation.intents


def pullback():
    host,a,o = later()
    for tf in ('100ms','5s','10s','30s'):
        o.source_values[f'indicator.macd.line@{tf}']['value'] = -.1
    swing = dict(side='support',lower=3.91,upper=3.93,price=3.92,
                 pivot_at=NOW.timestamp()-2,confirmed_at=NOW.timestamp())
    anchor = dict(anchor=zone('bounce',3.90,3.97),pivot_at=swing['pivot_at'],pivot_price=swing['price'],
                  recovered_at=NOW.timestamp(),break_count=6)
    o.structural_detector_state['row'].update(local_swings=[swing],vwap_retests=[anchor])
    a.state['vwap_ladder_episode'] = dict(session=NOW.date().isoformat(),used=True,bullish=True,
                                       started_at=NOW.timestamp()-30)
    return host,a,o


def test_fresh_pullback_uses_one_second_macd_despite_used_episode():
    host,a,o = pullback()
    r = host.evaluate(a,o)
    assert r.evaluation.signals[0].reason == 'post_move_pullback'
    assert r.evaluation.intents[0].invalidation_price == pytest.approx(3.91)
    assert r.state['post_move_entry_clock']['consumed_pullbacks'] == [NOW.timestamp()-2]
    again = replace(a,state=deepcopy(r.state),status=S.AssignmentStatus.WATCHING)
    assert not host.evaluate(again,o).evaluation.intents
    V.release_unfilled_episode(r.state)
    assert r.state['vwap_ladder_episode']['used']  # Original episode entry stays consumed.
    assert not r.state['post_move_entry_clock']['consumed_pullbacks']


def test_old_recovery_cannot_authorize_later_pullback_entry():
    host,a,o = pullback()
    o.structural_detector_state['row']['local_swings'][0]['confirmed_at'] -= 5
    o.structural_detector_state['row']['local_swings'][0]['pivot_at'] -= 10
    proof = o.structural_detector_state['row']['vwap_retests'][0]
    proof['pivot_at'] -= 10
    proof['recovered_at'] -= 5
    assert not host.evaluate(a,o).evaluation.intents


def test_pullback_requires_bullish_one_second_macd():
    host,a,o = pullback()
    o.source_values['indicator.macd.line@1s']['value'] = -.1
    assert not host.evaluate(a,o).evaluation.intents


def test_pullback_target_reuses_nearest_previously_broken_resistance():
    host,a,o = pullback()
    market = a.state['vwap_ladder_market']
    market['broken'].append('4.03')
    market['break_rows']['4.03'] = deepcopy(market['known']['4.03'])
    o.structural_detector_state['row']['vwap_retests'][0]['break_count'] = 7
    r = host.evaluate(a,o)
    assert r.evaluation.intents[0].profit_target_price == pytest.approx(4.04)


def test_pullback_recross_advances_target_without_incrementing_session_count():
    host,a,o = pullback()
    market = a.state['vwap_ladder_market']
    market['broken'].append('4.03')
    market['break_rows']['4.03'] = deepcopy(market['known']['4.03'])
    o.structural_detector_state['row']['vwap_retests'][0]['break_count'] = 7
    r = host.evaluate(a,o)
    state = deepcopy(r.state)
    state['vwap_ladder_market'].update(at=NOW.timestamp()+.1,price=4.031)
    a = replace(a,state=state,status=S.AssignmentStatus.MANAGING)
    o = replace(o,observed_at=NOW+timedelta(seconds=.1),price=4.031,
                bid=4.03,ask=4.035,position_quantity=100)
    r = host.evaluate(a,o)
    assert r.state['structural_profit_targets'] == pytest.approx([4.09])
    assert r.state['vwap_ladder_entry']['broken'] == ['4.03']
    assert r.state['vwap_ladder_entry']['target_moves'] == 1
    assert len(r.state['vwap_ladder_market']['broken']) == 7


def test_pullback_can_enter_above_vwap_below_midpoint_but_not_below_vwap():
    host,a,o = pullback()
    a.parameters['vwap_ladder']['pullback_above_vwap_only'] = 1
    o = replace(o,execution_vwap=3.99,structural_session_high=4.3)
    assert host.evaluate(a,o).evaluation.signals[0].reason == 'post_move_pullback'
    a.parameters['vwap_ladder']['pullback_above_vwap_only'] = 0
    assert host.evaluate(a,o).evaluation.signals[0].reason == 'below_vwap_hod_midpoint'
    a.parameters['vwap_ladder']['pullback_above_vwap_only'] = 1
    assert host.evaluate(a,replace(o,execution_vwap=4.02)).evaluation.signals[0].reason == 'above_vwap_required'


def test_pullback_acquisition_does_not_require_ten_second_macd():
    host,a,o = pullback()
    r = host.evaluate(a,o)
    pending = replace(a,state=r.state,status=S.AssignmentStatus.ENTRY_PENDING)
    r = host.evaluate(pending,replace(o,observed_at=NOW+timedelta(milliseconds=50)))
    assert r.evaluation.signals[0].reason == 'entry_fill_pending'
    assert not r.evaluation.intents


def test_candidate_preserves_baseline_and_enables_both_entry_paths():
    from src.backend.post_move_candidate import BASELINE_ID, BASELINE_HASH, PLAN, PROFILE, prepare_payload
    parent = dict(profile_id='vwap-midpoint-grouped-retests-v2', parameters={'vwap_ladder':{}})
    baseline = dict(candidate_id=BASELINE_ID,content_hash=BASELINE_HASH,payload=dict(
        strategy={'profiles':[parent]},run_plans={'plans':[dict(run_plan_id=PLAN,allowed_environments=['backtest'])]}))
    before = deepcopy(baseline)
    payload = prepare_payload(baseline)
    assert baseline == before
    assert payload['strategy']['profiles'][0] == parent
    successor = payload['strategy']['profiles'][-1]
    assert successor['profile_id'] == PROFILE
    assert successor['parameters']['vwap_ladder']['post_move_entries'] == 1
    assert successor['parameters']['vwap_ladder']['pullback_independent_episode'] == 1
