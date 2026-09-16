"""Episode stages and retry semantics, evaluated through the real strategy engine."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta

import pytest

from src.trading_runtime import r1_ladder as R, strategy_engine as S
from tests.test_r1_vwap import setup_entry
from tests.test_r1_ladder import obs


def ready():
    return setup_entry(contract=R.STAGED_CONTRACT)


def advance(host, a, o):
    result = host.evaluate(a, o)
    return replace(a, state=result.state, status=result.status), result


def test_between_vwap_and_r1_targets_r1_with_bounded_swing_stop():
    host, a, o = ready()
    result = host.evaluate(a, o)
    intent, = result.evaluation.intents
    assert intent.profit_target_price == pytest.approx(10.55)
    assert result.state['r1_entry']['target_level'] == result.state['r1_entry']['level']
    assert intent.metadata['episode_stage'] == 'approach_r1'
    assert 'r1_stop_bounds' in intent.metadata


def test_quality_rejection_does_not_consume_vwap_eligibility():
    host, a, o = ready()
    a, result = advance(host, a, replace(o, market_pressure={}))
    assert not result.evaluation.intents
    next_bar = replace(obs(4, 10.34, opened=10.32), execution_vwap=10.30,
                       structural_resistance_levels=o.structural_resistance_levels)
    intent, = host.evaluate(a, next_bar).evaluation.intents
    assert intent.profit_target_price == pytest.approx(10.55)
    assert intent.metadata['episode_entry_gate']['eligible']
    assert not intent.metadata['episode_entry_gate']['crossed']


def test_new_bullish_episode_can_enter_when_price_already_above_vwap():
    host, a, o = ready()
    a, _ = advance(host, a, replace(o, source_timeframe='5s', macd_line=-.1))
    a, _ = advance(host, a, replace(obs(4, 10.34, source_timeframe='5s'), macd_line=.1))
    current = replace(obs(4, 10.34, opened=10.32), execution_vwap=10.30,
                      structural_resistance_levels=o.structural_resistance_levels)
    intent, = host.evaluate(a, current).evaluation.intents
    assert intent.metadata['episode_stage'] == 'approach_r1'


def after_target():
    host, a, o = ready()
    a, _ = advance(host, a, o)
    R.record_exit(a.state, o.observed_at+timedelta(seconds=.1), 'profit_target', 0.)
    return host, replace(a, status=S.AssignmentStatus.WATCHING), o


def test_second_order_crosses_r1_not_episode_high_and_stops_under_r1():
    host, a, o = after_target()
    a.state['r1_market']['macd_episode']['high'] = 10.80
    current = replace(obs(4, 10.60), execution_vwap=10.30,
                      structural_resistance_levels=o.structural_resistance_levels)
    result = host.evaluate(a, current)
    intent, = result.evaluation.intents
    assert intent.invalidation_price == pytest.approx(10.52)
    assert intent.profit_target_price == pytest.approx(10.95)
    assert result.state['r1_entry']['continuation']
    assert intent.metadata['episode_stage'] == 'above_r1'


def test_second_order_waits_for_r1_upper_edge():
    host, a, o = after_target()
    result = host.evaluate(a, replace(obs(4, 10.56), execution_vwap=10.30,
                                     structural_resistance_levels=o.structural_resistance_levels))
    assert not result.evaluation.intents
    assert result.evaluation.signals[0].reason == 'waiting_for_episode_target_cross'


def test_ask_above_r1_cannot_promote_an_uncrossed_completed_candle():
    host, a, o = ready()
    o = replace(o, price=10.54, bar_high=10.54, ask=10.58, bid=10.53)
    assert not host.evaluate(a,o).evaluation.intents


def test_role_flipped_target_remains_the_next_episode_boundary():
    host, a, o = after_target()
    rows = tuple(dict(row, side=1, role='support') if row['lower']==10.55 else row
                 for row in o.structural_resistance_levels)
    result = host.evaluate(a, replace(obs(4, 10.60), execution_vwap=10.30,
                                     structural_resistance_levels=rows))
    intent, = result.evaluation.intents
    assert intent.invalidation_price == pytest.approx(10.52)
    assert intent.profit_target_price == pytest.approx(10.95)


def test_next_macd_episode_resets_to_first_trade():
    host, a, o = after_target()
    a, _ = advance(host, a, replace(obs(4, 10.34, source_timeframe='5s'), macd_line=-.1))
    assert 'r1_exit' not in a.state
    a, _ = advance(host, a, replace(obs(5, 10.34, source_timeframe='5s'), macd_line=.1))
    current = replace(obs(5, 10.34, opened=10.32), execution_vwap=10.30,
                      structural_resistance_levels=o.structural_resistance_levels)
    result = host.evaluate(a, current)
    intent, = result.evaluation.intents
    assert not result.state['r1_entry']['continuation']
    assert intent.profit_target_price == pytest.approx(10.55)
    assert 'r1_stop_bounds' in intent.metadata


@pytest.mark.parametrize('case', ['red','below_vwap','bearish','bad_quote'])
def test_persistent_gate_retains_current_safety_conditions(case):
    host, a, o = ready()
    if case == 'red': o = replace(o, bar_open=10.34)
    elif case == 'below_vwap': o = replace(o, execution_vwap=10.35)
    elif case == 'bearish': a.state['r1_market']['macd']['signal'] = 1.
    elif case == 'bad_quote': o = replace(o, bid=10.5, ask=10.3)
    assert not host.evaluate(a,o).evaluation.intents


def test_first_trade_above_r1_keeps_swing_protection():
    host, a, o = ready()
    o = replace(obs(3, 10.60), execution_vwap=10.30,
                structural_resistance_levels=o.structural_resistance_levels)
    intent, = host.evaluate(a, o).evaluation.intents
    assert 'r1_stop_bounds' in intent.metadata
    assert intent.metadata['episode_stage'] == 'above_r1'


def test_saved_v5_retains_its_diagnostic_stop_behavior():
    host, a, o = setup_entry(contract=R.EPISODE_CONTRACT)
    o = replace(obs(3, 10.60), execution_vwap=10.30,
                structural_resistance_levels=o.structural_resistance_levels)
    intent, = host.evaluate(a, o).evaluation.intents
    assert 'r1_fixed_stop' in intent.metadata


def test_staged_successor_preserves_source_parameters_except_identity():
    from src.backend.r1_ladder_candidate import build as v3
    from src.backend.r1_vwap_candidate import build as v4
    from src.backend.r1_episode_candidate import build as v5
    from src.backend.trading_configuration_service import configuration_base, _build_configuration_release
    from tests.test_r1_ladder_candidate import source
    base, canvas, _ = v3(configuration_base(), source_parameters=source())
    base['canvas'] = canvas
    original, _, _ = v4(base)
    saved = deepcopy(original)
    payload, canvas, plan = v5(original)
    assert original == saved
    _, released, _ = _build_configuration_release(canvas_revision=canvas['revision'],
        canvas_profile=canvas['profile'], configuration=payload,
        run_plan_id=plan, strategy_profile_id=R.STAGED_CONTRACT)
    p = next(p for p in released['strategy']['profiles'] if p['profile_id']==R.STAGED_CONTRACT)
    assert p['parameters']['r1_ladder_contract'] == R.STAGED_CONTRACT
