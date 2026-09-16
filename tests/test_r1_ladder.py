"""R1 policy contracts through the real strategy engine and fill callbacks."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from src.trading_runtime import historical_hod as H, r1_ladder as R, strategy_engine as S
from tests.test_historical_hod import candle
from tests.test_structural_recovery import NOW, level, parameters


def obs(i, price, **kw):
    value = candle(i, price, **kw)
    now = value.observed_at.timestamp()
    return replace(value, structural_session_high=10.8, market_pressure={
        'session_relative_volume': dict(contract='session-relative-volume-1', observed_at=now,
                                       effective_at=int(now), ready=True, ratio=3.)})


def ready():
    p = parameters()
    p.pop('structural_recovery_contract')
    p.pop('structural_recovery')
    p.update(historical_hod_contract=H.CONTRACT,
             historical_hod=dict(H.DEFAULTS, forming_macd_entry_enabled=0),
             r1_ladder_contract=R.CONTRACT, r1_ladder=dict(R.DEFAULTS))
    p = S.resolve_long_momentum_parameters(p, revision=47)
    a = S.StrategyAssignment('r1', S.STRATEGY_ID, 47, 'sim', 'TEST', 123,
        S.AssignmentStatus.WATCHING, S.StrategyPermissions(enter=True, reenter=True, add=True), p)
    host = S.LongMomentumStrategyEngine(revision=47)
    for o in (obs(0, 10.5, source_timeframe='5s'), obs(1, 10.55)):
        result = host.evaluate(a, o)
        a = replace(a, state=result.state, status=result.status)
    return host, a, obs(2, 10.6)


def acquired():
    host, a, o = ready()
    result = host.evaluate(a, o)
    assert result.evaluation.signals[0].action == 'enter_long'
    return host, replace(a, state=result.state, status=S.AssignmentStatus.MANAGING), o


def test_r1_is_closest_resistance_under_hod_not_closest_to_price():
    rows = [level(-1, 8, 8.1), level(-1, 9.8, 9.9), level(-1, 10, 10.1), level(1, 9.95, 9.99)]
    assert R.r1_level(rows, 10)['upper'] == 9.9
    assert R.r1_level(rows, 9.9)['upper'] == 8.1


def test_retired_resistance_transition_is_neither_r1_nor_target():
    live = level(-1, 9., 9.1)
    transition = dict(level(-1, 9.8, 9.9), role='transition',
                      transition_from='resistance', v7_all_origins=True)
    assert R.r1_level([live, transition], 10) == live
    assert R.next_target([live, transition], 9.2) is None


def test_entry_uses_completed_crossover_full_target_and_cash_fraction():
    host, a, o = ready()
    result = host.evaluate(a, o)
    intent, = result.evaluation.intents
    assert intent.action == 'enter_long'
    assert intent.metadata['entry_selection']['upper'] == pytest.approx(10.57)
    assert intent.capital_request.mode == 'mandate_fraction'
    assert intent.capital_request.value == .9
    assert intent.profit_target_price == pytest.approx(10.95)
    assert intent.metadata['profit_targets'] == [pytest.approx(10.95)]
    profile = intent.resolved_protection_profile()
    assert len(profile.slices) == 1
    assert profile.slices[0].quantity_fraction == 1
    assert 'cash_tranche' not in intent.metadata
    envelope = intent.resolved_execution_policy().envelope
    assert envelope.maximum_buy_price == pytest.approx(10.61)
    assert not envelope.persist_until_cancelled
    assert envelope.deadline_ms == 1000
    assert intent.invalidation_price >= envelope.maximum_buy_price * .95 - 1e-9


def test_target_must_clear_completed_trade_price_even_when_ask_is_lower():
    host, a, _ = ready()
    o = replace(obs(2, 10.96), bid=10.59, ask=10.60)
    intent, = host.evaluate(a, o).evaluation.intents
    assert intent.profit_target_price == pytest.approx(11.5)
    assert intent.profit_target_price > o.price


def test_tick_rounding_cannot_turn_target_into_non_overhead_price():
    host, a, _ = ready()
    o = replace(obs(2, 10.9501), bid=10.59, ask=10.60)
    band = dict(o.structural_resistance_levels[0], lower=10.955, price=10.956, upper=10.96)
    o = replace(o, structural_resistance_levels=(band,))
    result = host.evaluate(a, o)
    assert not result.evaluation.intents
    assert result.evaluation.signals[0].reason == 'invalid_executable_stop_or_target'


@pytest.mark.parametrize('changes', [
    dict(evaluation_events=('market_data_update',), source_timeframe=''),
    dict(source_timeframe='5s'), dict(source_timeframe='100ms'),
])
def test_intrabar_or_other_timeframe_cannot_enter(changes):
    host, a, o = ready()
    assert not host.evaluate(a, replace(o, **changes)).evaluation.intents


def test_1s_macd_cannot_replace_completed_5s_authority():
    host, a, o = ready()
    a.state['r1_market']['macd']['line'] = -.01
    result = host.evaluate(a, replace(o, macd_line=100., macd_signal=0.))
    assert result.evaluation.signals[0].reason == 'completed_5s_macd_not_bullish'


@pytest.mark.parametrize('case', ['missing', 'stale', 'future', 'two', 'unready'])
def test_rvol_requires_strictly_above_two_current_available_evidence(case):
    host, a, o = ready()
    pressure = deepcopy(o.market_pressure)
    value = pressure['session_relative_volume']
    if case == 'missing': pressure = {}
    if case == 'stale': value['observed_at'] -= 2
    if case == 'future': value['observed_at'] += 1
    if case == 'two': value['ratio'] = 2.
    if case == 'unready': value['ready'] = False
    result = host.evaluate(a, replace(o, market_pressure=pressure))
    assert not result.evaluation.intents
    assert result.evaluation.signals[0].reason == 'session_rvol_not_above_two'


def test_failed_gate_does_not_replay_old_cross():
    host, a, o = ready()
    failed = host.evaluate(a, replace(o, market_pressure={}))
    a = replace(a, state=failed.state, status=failed.status)
    result = host.evaluate(a, obs(3, 10.62))
    assert result.evaluation.signals[0].reason == 'waiting_for_fresh_resistance_break'


def test_no_next_resistance_means_no_entry():
    host, a, o = ready()
    result = host.evaluate(a, replace(o, structural_resistance_levels=tuple(
        row for row in o.structural_resistance_levels if row['upper'] < 10.8)))
    assert not result.evaluation.intents
    assert result.evaluation.signals[0].reason == 'next_resistance_unavailable'


@pytest.mark.parametrize('entry,swing', [(1.5,.5),(1.5,1.49),(1.99,1.),(2.,1.),(2.,2.),(10.,1.),(10.,10.)])
def test_stop_distance_is_clamped_on_both_price_regimes(entry, swing):
    stop = R.stop_price(entry, swing, .01)
    assert stop is not None
    distance = entry-stop
    assert distance >= .10-1e-9
    assert distance <= (.30 if entry < 2 else entry*.05)+1e-9
    assert stop > 0


def test_acquired_position_does_not_add_trail_or_change_full_target():
    host, a, _ = acquired()
    stop = a.state['active_stop']
    result = host.evaluate(a, obs(3, 11.1, position_quantity=100))
    assert not result.evaluation.intents
    assert result.evaluation.signals[0].reason == 'fixed_stop_and_resistance_target'
    assert result.state['active_stop'] == stop
    assert result.state['structural_profit_targets'] == a.state['structural_profit_targets']


@pytest.mark.parametrize('role,remaining,increment,advance', [
    ('profit_target',0.,100.,True), ('profit_target',50.,50.,False),
    ('profit_target',0.,0.,False), ('protective_stop',0.,100.,False),
    ('managed_exit',0.,100.,False),
])
def test_actual_fill_callback_advances_only_completed_target(role, remaining, increment, advance):
    _, a, _ = acquired()
    assigned = S.AssignedLongMomentumStrategy([a])
    fill = SimpleNamespace(assignment_id=a.assignment_id, state='FILLED', action='exit',
        fill_incremental_quantity=increment, filled_quantity=increment,
        updated_at=NOW+timedelta(seconds=3.2), fill_role=role, reentry_after_fill=True)
    asyncio.run(assigned.on_order_group_update(fill, aggregate_position_quantity=remaining))
    state = assigned.assignments()[0].state
    assert bool((state.get('r1_exit') or {}).get('level')) == advance
    if remaining or increment == 0:
        assert 'r1_entry' in state


def test_reentry_requires_fresh_post_exit_cross_of_previous_target_upper():
    host, a, _ = acquired()
    R.record_exit(a.state, NOW+timedelta(seconds=4), 'profit_target', 0.)
    a = replace(a, status=S.AssignmentStatus.WATCHING)
    # The confirmation must be later than the completed liquidation.
    blocked = host.evaluate(a, obs(4, 11.0))
    assert not blocked.evaluation.intents
    assert blocked.evaluation.signals[0].reason == 'waiting_for_post_exit_breakout'
    a = replace(a, state=blocked.state, status=blocked.status)
    # Reset below the old target, then cross its far edge on a new complete bar.
    for o in (obs(5, 10.96, source_timeframe='5s'), obs(5, 10.96)):
        result = host.evaluate(a, o)
        a = replace(a, state=result.state, status=result.status)
    result = host.evaluate(a, obs(6, 11.0))
    intent, = result.evaluation.intents
    assert intent.metadata['entry_selection']['upper'] == pytest.approx(10.97)
    assert intent.profit_target_price == pytest.approx(11.5)


def test_first_close_after_intrasecond_target_fill_can_reenter():
    host, a, _ = acquired()
    R.record_exit(a.state, NOW+timedelta(seconds=3.2), 'profit_target', 0.)
    a = replace(a, status=S.AssignmentStatus.WATCHING)
    intent, = host.evaluate(a, obs(4, 11.0)).evaluation.intents
    assert intent.metadata['entry_selection']['upper'] == pytest.approx(10.97)


def test_unrepresentable_actual_fill_stop_exits_only_remaining_position():
    host, a, _ = acquired()
    a.state['r1_stop_error'] = 'actual_fill_stop_not_representable'
    result = host.evaluate(a, obs(4, 10.6, position_quantity=100, pending_exit_quantity=30))
    intent, = result.evaluation.intents
    assert intent.action == 'exit'
    assert intent.quantity == 70
    assert intent.reason == 'unrepresentable_fill_stop'
    waiting = host.evaluate(a, obs(4, 10.6, position_quantity=100, pending_exit_quantity=100))
    assert not waiting.evaluation.intents


def test_partial_target_then_stop_does_not_qualify_as_full_target_exit():
    _, a, _ = acquired()
    assigned = S.AssignedLongMomentumStrategy([a])
    for role, remaining, second in [('profit_target', 50., 3.1), ('protective_stop', 0., 3.2)]:
        fill = SimpleNamespace(assignment_id=a.assignment_id, state='FILLED', action='exit',
            fill_incremental_quantity=50., filled_quantity=50.,
            updated_at=NOW+timedelta(seconds=second), fill_role=role, reentry_after_fill=True)
        asyncio.run(assigned.on_order_group_update(fill, aggregate_position_quantity=remaining))
    state = assigned.assignments()[0].state
    assert not (state.get('r1_exit') or {}).get('level')


def test_partial_target_position_cannot_buy_again():
    host, a, _ = acquired()
    assigned = S.AssignedLongMomentumStrategy([a])
    fill = SimpleNamespace(assignment_id=a.assignment_id, state='FILLED', action='exit',
        fill_incremental_quantity=50., filled_quantity=50.,
        updated_at=NOW+timedelta(seconds=3.1), fill_role='profit_target', reentry_after_fill=True)
    asyncio.run(assigned.on_order_group_update(fill, aggregate_position_quantity=50.))
    a = assigned.assignments()[0]
    result = host.evaluate(a, obs(4, 11.1, position_quantity=50.))
    assert not result.evaluation.intents
    assert 'r1_exit' not in result.state


def test_completed_5s_macd_must_be_fresh_and_strictly_bullish():
    for updates in ({'at': NOW.timestamp()-10}, {'line': 0., 'signal': 0.}):
        host, a, o = ready()
        a.state['r1_market']['macd'].update(updates)
        result = host.evaluate(a, o)
        assert result.evaluation.signals[0].reason == 'completed_5s_macd_not_bullish'


def test_unfilled_continuation_preserves_target_for_retry_until_actual_fill():
    host, a, _ = acquired()
    R.record_exit(a.state, NOW+timedelta(seconds=3.2), 'profit_target', 0.)
    saved_exit = deepcopy(a.state['r1_exit'])
    a = replace(a, status=S.AssignmentStatus.WATCHING)
    for o in (obs(5, 10.96, source_timeframe='5s'), obs(5, 10.96), obs(6, 11.0)):
        result = host.evaluate(a, o)
        a = replace(a, state=result.state, status=result.status)
    assert result.evaluation.intents[0].action == 'enter_long'
    assert a.state['r1_exit'] == saved_exit
    assigned = S.AssignedLongMomentumStrategy([a])
    # A rejected/unfilled notification must not consume the completed target.
    for state in ('rejected', 'submitted', 'cancelled'):
        snapshot = SimpleNamespace(assignment_id=a.assignment_id, state=state, action='enter_long',
            fill_incremental_quantity=0., filled_quantity=0., updated_at=NOW+timedelta(seconds=6.5))
        asyncio.run(assigned.on_order_group_update(snapshot, aggregate_position_quantity=0.))
        assert assigned.assignments()[0].state['r1_exit'] == saved_exit
    a = assigned.assignments()[0]
    assert a.status in (S.AssignmentStatus.WATCHING, S.AssignmentStatus.REENTRY_COOLDOWN)
    # Move current HOD/R1 elsewhere; the retry must still clear the saved target.
    for o in (replace(obs(7, 10.96), structural_session_high=12.), obs(8, 11.0)):
        result = host.evaluate(a, o)
        a = replace(a, state=result.state, status=result.status)
    intent, = result.evaluation.intents
    assert intent.metadata['entry_selection']['upper'] == pytest.approx(10.97)
    assert a.state['r1_exit'] == saved_exit
    assigned = S.AssignedLongMomentumStrategy([a])
    snapshot = SimpleNamespace(assignment_id=a.assignment_id, state='partially_filled', action='enter_long',
        fill_incremental_quantity=1., filled_quantity=1., updated_at=NOW+timedelta(seconds=8.1))
    asyncio.run(assigned.on_order_group_update(snapshot, aggregate_position_quantity=1.))
    assert 'r1_exit' not in assigned.assignments()[0].state
