"""V4 entries through the installed engine, retaining the frozen V3 policy."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from src.trading_runtime import r1_ladder as R, historical_hod as H, strategy_engine as S
from tests.test_r1_ladder import ready, obs
from tests.test_historical_hod import rows as historical_rows


def setup_entry(*, blocked=False, contract=R.VWAP_CONTRACT):
    host, a, _ = ready()
    a = replace(a, parameters=dict(a.parameters, r1_ladder_contract=contract))
    rows = [r for r in historical_rows() if r['lower'] >= 10.55]
    if blocked:
        rows.extend(r for r in historical_rows() if r['lower'] == 10.4)
    prior = replace(obs(2, 10.29, bar_high=10.8), execution_vwap=10.30,
                    structural_resistance_levels=tuple(rows))
    result = host.evaluate(a, prior)
    a = replace(a, state=result.state, status=result.status)
    current = replace(obs(3, 10.32, opened=10.29), execution_vwap=10.30,
                      structural_resistance_levels=tuple(rows))
    return host, a, current


def test_green_vwap_cross_enters_below_r1_and_keeps_original_target_and_swing_stop():
    host, a, o = setup_entry()
    result = host.evaluate(a, o)
    intent, = result.evaluation.intents
    assert intent.reason == 'r1_vwap_episode_entry'
    assert o.price < result.state['r1_entry']['level']['upper']
    assert intent.metadata['episode_entry_gate']['path'] == 'vwap'
    assert result.state['r1_entry']['contract'] == R.VWAP_CONTRACT
    assert not result.state['r1_entry']['continuation']
    assert intent.profit_target_price == pytest.approx(10.95)
    assert 'r1_stop_bounds' in intent.metadata


def test_v3_does_not_gain_vwap_entry():
    host, a, o = setup_entry(contract=R.CONTRACT)
    assert not host.evaluate(a, o).evaluation.intents


def test_intervening_resistance_blocks_vwap_then_allows_r1_cross():
    host, a, o = setup_entry(blocked=True)
    result = host.evaluate(a, o)
    assert not result.evaluation.intents
    a = replace(a, state=result.state, status=result.status)
    crossed = replace(obs(4, 10.60), execution_vwap=10.30,
                      structural_resistance_levels=o.structural_resistance_levels)
    intent, = host.evaluate(a, crossed).evaluation.intents
    assert intent.reason == 'r1_resistance_breakout'
    assert intent.metadata['episode_entry_gate']['blocking_level_ids']


@pytest.mark.parametrize('case', ['red', 'doji', 'missing', 'nan', 'gap', 'already_above', 'bearish'])
def test_invalid_vwap_entry_is_not_authorized(case):
    host, a, o = setup_entry()
    if case in ('red', 'doji'):
        o = replace(o, bar_open=o.price + (.01 if case == 'red' else 0))
    elif case in ('missing', 'nan'):
        o = replace(o, execution_vwap=None if case == 'missing' else float('nan'))
    elif case == 'gap':
        a.state['r1_market']['closed_at'] -= 1
    elif case == 'already_above':
        a.state['r1_market']['vwap'] = 10.20
    elif case == 'bearish':
        a.state['r1_market']['macd']['signal'] = 1.
    assert not host.evaluate(a, o).evaluation.intents


def test_cross_does_not_latch_after_quality_rejection():
    host, a, o = setup_entry()
    rejected = replace(o, market_pressure={})
    result = host.evaluate(a, rejected)
    assert not result.evaluation.intents
    a = replace(a, state=result.state, status=result.status)
    later = replace(obs(4, 10.34), execution_vwap=10.30,
                    structural_resistance_levels=o.structural_resistance_levels)
    assert not host.evaluate(a, later).evaluation.intents


def test_vwap_entry_can_continue_only_after_actual_full_target_exit():
    host, a, o = setup_entry()
    entered = host.evaluate(a, o)
    a = replace(a, state=entered.state, status=S.AssignmentStatus.MANAGING)
    R.record_exit(a.state, o.observed_at+timedelta(seconds=.2), 'profit_target', 0.)
    assert a.state['r1_exit']['continuation_episode_id'] == a.state['r1_market']['macd_episode']['episode_id']
    a = replace(a, status=S.AssignmentStatus.WATCHING)
    intent, = host.evaluate(a, obs(4, 11.0)).evaluation.intents
    assert intent.reason == 'r1_macd_episode_continuation'
    assert intent.invalidation_price == pytest.approx(10.92)


def test_passive_history_supplies_adjacent_vwap_without_replaying_old_cross():
    host, a, o = setup_entry()
    saved = {}
    for candle in (replace(obs(1, 10.29, source_timeframe='5s'), execution_vwap=10.30),
                   replace(obs(2, 10.29, bar_high=10.8), execution_vwap=10.30), o):
        frame = SimpleNamespace(as_of=candle.observed_at, timeframe=candle.source_timeframe,
            bar=dict(open=candle.bar_open, high=candle.bar_high, low=candle.bar_low,
                     close=candle.price, volume=candle.bar_volume),
            indicator=dict(execution_vwap=candle.execution_vwap, atr_14=candle.volatility,
                           macd_line=candle.macd_line, macd_signal=candle.macd_signal))
        saved = H.observe_frame(frame, saved, a.parameters,
                                {'unified_levels': o.structural_resistance_levels})
    assert saved['prior_r1_vwap'] == 10.30
    market = dict(o.structural_detector_state, historical_hod_observation=saved)
    fresh = replace(a, state={}, status=S.AssignmentStatus.WATCHING)
    intent, = host.evaluate(fresh, replace(o, structural_detector_state=market)).evaluation.intents
    assert intent.reason == 'r1_vwap_episode_entry'


def test_transition_and_support_do_not_obstruct_but_overlapping_resistance_does():
    host, a, o = setup_entry()
    boundary = a.state['r1_market']['rows'][0]
    band = dict(historical_rows()[0], lower=10.29, price=10.30, upper=10.31)
    for ignored in (dict(band, side=1), dict(band, role='transition')):
        passed, gate = R.episode_entry_gate(boundary, [boundary, ignored], 10.29, 10.30,
                                           o.observed_at.timestamp()-1, o)
        assert passed and gate['path'] == 'vwap'
    passed, gate = R.episode_entry_gate(boundary, [boundary, band], 10.29, 10.30,
                                       o.observed_at.timestamp()-1, o)
    assert not passed and gate['blocking_level_ids'] == [band['unified_level_id']]


def test_successor_copies_source_parameters_and_releases_with_new_identity():
    from src.backend.r1_ladder_candidate import build as build_v3
    from src.backend.r1_vwap_candidate import build, PROFILE_ID
    from src.backend.trading_configuration_service import configuration_base, _build_configuration_release
    from tests.test_r1_ladder_candidate import source

    original, original_canvas, _ = build_v3(configuration_base(), source_parameters=source())
    original['canvas'] = original_canvas
    saved = deepcopy(original)
    published = configuration_base()
    published_profile = next(p for p in published['strategy']['profiles']
                             if p.get('publication_status') == 'published')
    stale = next(p for p in original['strategy']['profiles']
                 if p['profile_id'] == published_profile['profile_id'])
    stale['description'] = 'Historical copy differs from the current published profile.'
    saved = deepcopy(original)
    payload, canvas, plan = build(original, published_configuration=published)
    assert original == saved
    assert next(p for p in payload['strategy']['profiles']
                if p['profile_id'] == published_profile['profile_id']) == published_profile
    old = next(p for p in original['strategy']['profiles'] if p['profile_id'] == R.CONTRACT)
    new = next(p for p in payload['strategy']['profiles'] if p['profile_id'] == PROFILE_ID)
    assert new['parameters'] == dict(old['parameters'], r1_ladder_contract=R.VWAP_CONTRACT,
        entry_rules=new['parameters']['entry_rules'])
    _, released, _ = _build_configuration_release(canvas_revision=canvas['revision'],
        canvas_profile=canvas['profile'], configuration=payload,
        run_plan_id=plan, strategy_profile_id=PROFILE_ID)
    active = next(p for p in released['strategy']['profiles'] if p['profile_id'] == PROFILE_ID)
    assert active['parameters']['r1_ladder_contract'] == R.VWAP_CONTRACT
