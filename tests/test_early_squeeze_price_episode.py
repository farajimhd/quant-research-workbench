from dataclasses import replace
import pytest
from src.trading_runtime import early_squeeze_price as P, early_squeeze_breakout as E, strategy_engine as S
from tests.test_early_squeeze_price_high import opened as old_opened
from tests.test_early_squeeze_price import fixture as old_fixture
from tests.test_vwap_resistance_ladder import advance


def opened():
    h, a, t = old_opened()
    return h, replace(a, parameters={**a.parameters, 'early_squeeze_breakout_contract': P.EPISODE_CONTRACT}), t


def resistance_ceiling_opened():
    h, a, t = opened()
    return h, replace(a, parameters={**a.parameters,
        'early_squeeze_breakout_contract': P.RESISTANCE_CEILING_CONTRACT}), t


def broken_resistance_ceiling_opened():
    h, a, t = opened()
    return h, replace(a, parameters={**a.parameters,
        'early_squeeze_breakout_contract': P.BROKEN_RESISTANCE_CEILING_CONTRACT}), t


def green_close_ceiling_opened():
    h, a, t = opened()
    return h, replace(a, parameters={**a.parameters,
        'early_squeeze_breakout_contract': P.GREEN_CLOSE_CEILING_CONTRACT}), t


def macd_episode_fixture():
    h, a, t = old_fixture()
    a = replace(a, parameters={**a.parameters,
        'early_squeeze_breakout_contract': P.MACD_EPISODE_REENTRY_CONTRACT})

    def frame(offset, price, line, signal, high=None):
        observation = t(offset, price)
        market = dict(observation.structural_detector_state)
        market['fast_squeeze_context'] = {
            **market['fast_squeeze_context'],
            'at': observation.observed_at.timestamp(),
            'close': price,
        }
        return replace(observation, source_timeframe='1s', evaluation_events=('bar_close',),
            changed_source_ids=(), source_signal_ids=('qmd-derived:test:1s',),
            bar_open=price-.01, bar_high=high or price, bar_low=price-.02,
            macd_line=line, macd_signal=signal, structural_detector_state=market)

    return h, a, t, frame


def dual_macd_fixture():
    h, a, t, frame_1s = macd_episode_fixture()
    a = replace(a, parameters={**a.parameters,
        'early_squeeze_breakout_contract': P.DUAL_MACD_REENTRY_CONTRACT})

    def frame_100ms(offset, price, line=.3, signal=.2, *, high=None, local_events=(), position=0.):
        observation = t(offset, price, position)
        market = dict(observation.structural_detector_state)
        market['fast_squeeze_context'] = {
            **market['fast_squeeze_context'],
            'at': observation.observed_at.timestamp(),
            'close': price,
        }
        market['local_events'] = tuple(local_events)
        return replace(observation, source_timeframe='100ms', evaluation_events=('bar_close',),
            changed_source_ids=(), source_signal_ids=('qmd-derived:test:100ms',),
            bar_open=price-.01, bar_high=high or price, bar_low=price-.02,
            macd_line=line, macd_signal=signal, structural_detector_state=market)

    return h, a, t, frame_1s, frame_100ms


def episode_target_fixture():
    h, a, t, frame_1s, frame_100ms = dual_macd_fixture()
    return h, replace(a, parameters={**a.parameters,
        'early_squeeze_breakout_contract': P.EPISODE_TARGET_CONTINUITY_CONTRACT}), t, frame_1s, frame_100ms


@pytest.mark.parametrize('low,enters', [(10.39, True), (10.42, False)])
def test_only_below_lower_band_resets_reentry_high(low, enters):
    h, a, t = opened()
    a = advance(a, h.evaluate(a, t(.02, 10.60, 100.)))
    E.record_exit(a.state, t(.03).observed_at, 'protective_stop', 0, contract=P.EPISODE_CONTRACT)
    a = replace(a, status=S.AssignmentStatus.WATCHING)
    a = advance(a, h.evaluate(a, t(.04, low)))
    result = h.evaluate(a, t(.05, 10.44))
    assert any(i.action == 'enter_long' for i in result.evaluation.intents) == enters


def test_reset_before_exit_callback_stays_reset():
    h, a, t = opened()
    a = advance(a, h.evaluate(a, t(.02, 10.60, 100.)))
    a = advance(a, h.evaluate(a, t(.03, 10.39, 100.)))
    E.record_exit(a.state, t(.04).observed_at, 'protective_stop', 0, contract=P.EPISODE_CONTRACT)
    assert 'R2' not in a.state['squeeze_breakout']['entered_levels']
    a = replace(a, status=S.AssignmentStatus.WATCHING)
    assert any(i.action == 'enter_long' for i in h.evaluate(a, t(.05, 10.44)).evaluation.intents)


def test_addition_keeps_original_distance_and_one_stop():
    h, a, t = opened()
    a.state['squeeze_entry'].update(trail_distance=.14, peak_price=10.44, structural_stop=10.30)
    a.state['active_stop'] = 10.30
    result = h.evaluate(a, t(.02, 10.84, 100.))
    additions = [i for i in result.evaluation.intents if i.action == 'add_long']
    assert len(additions) == 1
    assert additions[0].invalidation_price == pytest.approx(10.70)
    assert result.state['squeeze_entry']['trail_distance'] == .14
    assert not any(i.reason == 'new_resistance_protection' for i in result.evaluation.intents)
    a = advance(a, result)
    result = h.evaluate(a, replace(t(.03, 10.90, 200.), average_price=10.70))
    assert result.state['active_stop'] == pytest.approx(10.76)


def test_resistance_ceiling_holds_trail_inside_band_until_upper_edge_breaks():
    h, a, t = resistance_ceiling_opened()
    a.state['squeeze_entry'].update(trail_distance=.01, peak_price=10.44, structural_stop=10.39)
    a.state['active_stop'] = 10.39

    between = h.evaluate(a, t(.02, 10.55, 100.))
    assert between.state['active_stop'] == pytest.approx(10.54)
    assert next(i for i in between.evaluation.intents
                if i.action == 'replace_protective_stop').metadata['resistance_ceiling']['unified_level_id'] == 'R3'

    a = advance(a, between)
    inside_observation = t(.03, 10.61, 100.)
    r3 = next(r for r in inside_observation.structural_resistance_levels
              if r['unified_level_id'] == 'R3')
    inside_observation = replace(inside_observation,
        structural_resistance_levels=tuple(r for r in inside_observation.structural_resistance_levels
                                           if r['unified_level_id'] != 'R3'),
        structural_transition_levels=inside_observation.structural_transition_levels +
            (dict(r3, role='transition', transition_from='resistance'),))
    inside = h.evaluate(a, inside_observation)
    assert inside.state['active_stop'] == pytest.approx(10.60)
    a = advance(a, inside)
    held = h.evaluate(a, t(.04, 10.62, 100.))
    assert held.state['active_stop'] == pytest.approx(10.60)
    assert not any(i.action == 'replace_protective_stop' for i in held.evaluation.intents)

    a = advance(a, held)
    broken = h.evaluate(a, t(.05, 10.63, 100.))
    assert broken.state['active_stop'] == pytest.approx(10.62)
    move = next(i for i in broken.evaluation.intents if i.action == 'replace_protective_stop')
    assert move.metadata['resistance_ceiling']['unified_level_id'] == 'R4'


def test_candidate_328_retains_uncapped_trail_for_reproducibility():
    h, a, t = opened()
    a.state['squeeze_entry'].update(trail_distance=.01, peak_price=10.44, structural_stop=10.39)
    a.state['active_stop'] = 10.39
    result = h.evaluate(a, t(.02, 10.62, 100.))
    assert result.state['active_stop'] == pytest.approx(10.61)


def test_broken_resistance_ceiling_uses_lower_edge_until_next_band_fully_clears():
    h, a, t = broken_resistance_ceiling_opened()
    a.state['squeeze_entry'].update(trail_distance=.01, peak_price=10.44, structural_stop=10.39)
    a.state['active_stop'] = 10.39

    inside_next_band = h.evaluate(a, t(.02, 10.61, 100.))
    assert inside_next_band.state['active_stop'] == pytest.approx(10.40)
    ceiling = next(i for i in inside_next_band.evaluation.intents
                   if i.action == 'replace_protective_stop').metadata['resistance_ceiling']
    assert ceiling['unified_level_id'] == 'R2'
    assert ceiling['lower'] == pytest.approx(10.40)

    a = advance(a, inside_next_band)
    at_upper_edge = h.evaluate(a, t(.03, 10.62, 100.))
    assert at_upper_edge.state['active_stop'] == pytest.approx(10.40)
    assert not any(i.action == 'replace_protective_stop' for i in at_upper_edge.evaluation.intents)

    a = advance(a, at_upper_edge)
    fully_cleared = h.evaluate(a, t(.04, 10.63, 100.))
    assert fully_cleared.state['active_stop'] == pytest.approx(10.60)
    ceiling = next(i for i in fully_cleared.evaluation.intents
                   if i.action == 'replace_protective_stop').metadata['resistance_ceiling']
    assert ceiling['unified_level_id'] == 'R3'


def test_green_1s_close_and_trade_break_are_both_required_to_advance_ceiling():
    h, a, t = green_close_ceiling_opened()
    a.state['squeeze_entry'].update(trail_distance=.10, peak_price=10.44, structural_stop=10.39)
    a.state['active_stop'] = 10.39

    trade_break = h.evaluate(a, t(.02, 10.63, 100.))
    assert trade_break.state['active_stop'] == pytest.approx(10.40)
    a = advance(a, trade_break)

    red_close = replace(t(.03, 10.63, 100.), source_timeframe='1s',
        evaluation_events=('bar_close',), changed_source_ids=(), source_signal_ids=('qmd-derived:test:1s',),
        bar_open=10.64)
    result = h.evaluate(a, red_close)
    assert result.state['active_stop'] == pytest.approx(10.40)
    assert not any(i.action == 'replace_protective_stop' for i in result.evaluation.intents)
    a = advance(a, result)

    green_close = replace(red_close, observed_at=t(.04).observed_at, price=10.64, bar_open=10.61)
    result = h.evaluate(a, green_close)
    assert result.state['active_stop'] == pytest.approx(10.53)
    ceiling = next(i for i in result.evaluation.intents
                   if i.action == 'replace_protective_stop').metadata['resistance_ceiling']
    assert ceiling['unified_level_id'] == 'R3'


def test_green_close_contract_enforces_ten_cent_minimum_trail_distance():
    h, a, t = green_close_ceiling_opened()
    result = h.evaluate(a, replace(t(.02, 10.45, 100.), average_price=10.45))
    assert result.state['squeeze_entry']['trail_distance'] == pytest.approx(.10)


def test_all_entries_require_bullish_completed_1s_macd():
    h, a, t, frame = macd_episode_fixture()
    a = advance(a, h.evaluate(a, t()))
    a = advance(a, h.evaluate(a, frame(.005, 10.39, .1, .2)))
    result = h.evaluate(a, t(.01, 10.44))
    assert not result.evaluation.intents
    assert result.evaluation.signals[0].reason == 'macd_1s_line_above_signal_required'

    a = advance(a, h.evaluate(a, frame(.015, 10.39, .3, .2)))
    result = h.evaluate(a, t(.02, 10.44))
    assert any(intent.action == 'enter_long' for intent in result.evaluation.intents)


def test_same_macd_episode_reentry_breaks_prior_high_and_uses_resistance_stop_without_ten_cent_floor():
    h, a, t, frame = macd_episode_fixture()
    a = advance(a, h.evaluate(a, t()))
    a = advance(a, h.evaluate(a, frame(.005, 10.39, .3, .2, high=10.40)))
    a = advance(a, h.evaluate(a, t(.01, 10.44)))
    a.state['squeeze_entry'].update(first_fill_at=t().observed_at.timestamp(), slice_notional=3000.)
    a = replace(a, status=S.AssignmentStatus.MANAGING)
    a = advance(a, h.evaluate(a, t(.02, 10.62, 100.)))
    E.record_exit(a.state, t(.025).observed_at, 'protective_stop', 0,
                  contract=P.MACD_EPISODE_REENTRY_CONTRACT)
    a = replace(a, status=S.AssignmentStatus.WATCHING)

    below_high = h.evaluate(a, t(.03, 10.62))
    assert not below_high.evaluation.intents
    assert below_high.evaluation.signals[0].reason == 'waiting_for_macd_1s_episode_high_break'
    a = advance(a, below_high)

    reentry = h.evaluate(a, t(.04, 10.63))
    intent = next(intent for intent in reentry.evaluation.intents if intent.action == 'enter_long')
    assert intent.invalidation_price == pytest.approx(10.59)
    assert intent.metadata['stop_source'] == 'reentry_resistance_below_lower'
    assert reentry.state['squeeze_entry']['same_macd_episode_reentry']

    managing = replace(advance(a, reentry), status=S.AssignmentStatus.MANAGING)
    managed = h.evaluate(managing, replace(t(.05, 10.64, 100.), average_price=10.63))
    assert managed.state['squeeze_entry']['trail_distance'] == pytest.approx(.04)


def test_new_bullish_macd_episode_does_not_inherit_old_episode_high():
    h, a, t, frame = macd_episode_fixture()
    a = advance(a, h.evaluate(a, t()))
    a = advance(a, h.evaluate(a, frame(.005, 10.39, .3, .2, high=10.40)))
    a = advance(a, h.evaluate(a, t(.01, 10.44)))
    E.record_exit(a.state, t(.015).observed_at, 'protective_stop', 0,
                  contract=P.MACD_EPISODE_REENTRY_CONTRACT)
    a = replace(a, status=S.AssignmentStatus.WATCHING)
    a = advance(a, h.evaluate(a, frame(.02, 10.39, .1, .2, high=10.45)))
    a = advance(a, h.evaluate(a, frame(.03, 10.39, .3, .2, high=10.40)))
    a = advance(a, h.evaluate(a, t(.035, 10.39)))
    result = h.evaluate(a, t(.04, 10.44))
    assert any(intent.action == 'enter_long' for intent in result.evaluation.intents)


def test_dual_macd_contract_requires_bullish_completed_100ms_macd_for_entry():
    h, a, t, frame_1s, frame_100ms = dual_macd_fixture()
    a = advance(a, h.evaluate(a, t()))
    a = advance(a, h.evaluate(a, frame_1s(.005, 10.39, .3, .2)))
    a = advance(a, h.evaluate(a, frame_100ms(.01, 10.39, .1, .2)))
    blocked = h.evaluate(a, t(.015, 10.44))
    assert not blocked.evaluation.intents
    assert blocked.evaluation.signals[0].reason == 'macd_100ms_line_above_signal_required'

    a = advance(a, h.evaluate(a, frame_100ms(.02, 10.39)))
    entered = h.evaluate(a, t(.025, 10.44))
    assert any(intent.action == 'enter_long' for intent in entered.evaluation.intents)


def test_addition_waits_for_completed_100ms_close_above_resistance():
    h, a, t, frame_1s, frame_100ms = dual_macd_fixture()
    a = advance(a, h.evaluate(a, t()))
    a = advance(a, h.evaluate(a, frame_1s(.005, 10.39, .3, .2)))
    a = advance(a, h.evaluate(a, frame_100ms(.01, 10.39)))
    a = advance(a, h.evaluate(a, t(.015, 10.44)))
    a.state['squeeze_entry'].update(first_fill_at=t().observed_at.timestamp(), slice_notional=3000.)
    a = replace(a, status=S.AssignmentStatus.MANAGING)

    crossed = h.evaluate(a, t(.02, 10.84, 100.))
    assert not any(intent.action == 'add_long' for intent in crossed.evaluation.intents)
    a = advance(a, crossed)

    confirmed = h.evaluate(a, frame_100ms(.03, 10.84, position=100.))
    assert any(intent.action == 'add_long' for intent in confirmed.evaluation.intents)


def test_addition_close_still_requires_bullish_100ms_macd():
    h, a, t, frame_1s, frame_100ms = dual_macd_fixture()
    a = advance(a, h.evaluate(a, t()))
    a = advance(a, h.evaluate(a, frame_1s(.005, 10.39, .3, .2)))
    a = advance(a, h.evaluate(a, frame_100ms(.01, 10.39)))
    a = advance(a, h.evaluate(a, t(.015, 10.44)))
    a.state['squeeze_entry'].update(first_fill_at=t().observed_at.timestamp(), slice_notional=3000.)
    a = replace(a, status=S.AssignmentStatus.MANAGING)
    a = advance(a, h.evaluate(a, t(.02, 10.84, 100.)))

    bearish = h.evaluate(a, frame_100ms(.03, 10.84, .1, .2, position=100.))
    assert not any(intent.action == 'add_long' for intent in bearish.evaluation.intents)
    a = advance(a, bearish)
    bullish = h.evaluate(a, frame_100ms(.04, 10.84, .3, .2, position=100.))
    assert any(intent.action == 'add_long' for intent in bullish.evaluation.intents)


def test_three_100ms_candles_with_unbroken_forming_resistance_stop_same_episode_reentry():
    h, a, t, frame_1s, frame_100ms = dual_macd_fixture()
    a = advance(a, h.evaluate(a, t()))
    a = advance(a, h.evaluate(a, frame_1s(.005, 10.39, .3, .2)))
    a = advance(a, h.evaluate(a, frame_100ms(.01, 10.39)))
    a = advance(a, h.evaluate(a, t(.015, 10.44)))
    a.state['squeeze_entry'].update(first_fill_at=t().observed_at.timestamp(), slice_notional=3000.)
    a = replace(a, status=S.AssignmentStatus.MANAGING)
    a = advance(a, h.evaluate(a, t(.02, 10.62, 100.)))
    E.record_exit(a.state, t(.025).observed_at, 'protective_stop', 0,
                  contract=P.DUAL_MACD_REENTRY_CONTRACT)
    a = replace(a, status=S.AssignmentStatus.WATCHING)

    waiting = h.evaluate(a, t(.03, 10.63))
    assert waiting.evaluation.signals[0].reason == 'waiting_for_three_completed_100ms_reentry_candles'
    a = advance(a, waiting)
    a = advance(a, h.evaluate(a, frame_100ms(.04, 10.60)))
    a = advance(a, h.evaluate(a, frame_100ms(.05, 10.61)))
    forming = dict(state='resistance_forming', level=dict(price=10.63, confirmed_at=None))
    a = advance(a, h.evaluate(a, frame_100ms(.06, 10.60, local_events=(forming,))))

    blocked = h.evaluate(a, t(.07, 10.64))
    assert not blocked.evaluation.intents
    assert blocked.evaluation.signals[0].reason == 'macd_episode_reentry_stopped_by_forming_resistance'
    assert blocked.state['squeeze_breakout']['reentry_100ms_review']['blocked']


def test_same_episode_reentry_inherits_resistance_count_for_initial_target():
    h, a, t, frame_1s, frame_100ms = episode_target_fixture()
    a = advance(a, h.evaluate(a, t()))
    a = advance(a, h.evaluate(a, frame_1s(.005, 10.39, .3, .2)))
    a = advance(a, h.evaluate(a, frame_100ms(.01, 10.39)))
    a = advance(a, h.evaluate(a, t(.015, 10.44)))
    a.state['squeeze_entry'].update(first_fill_at=t().observed_at.timestamp(), slice_notional=3000.)
    a.state['squeeze_breakout']['macd_1s'].update(
        high=10.62, broken_levels=['R2', 'R3', 'R4', 'R5', 'R6'])
    E.record_exit(a.state, t(.02).observed_at, 'protective_stop', 0,
                  contract=P.EPISODE_TARGET_CONTINUITY_CONTRACT)
    a = replace(a, status=S.AssignmentStatus.WATCHING)
    a = advance(a, h.evaluate(a, t(.025, 10.39)))
    a = advance(a, h.evaluate(a, frame_100ms(.03, 10.39)))
    a = advance(a, h.evaluate(a, frame_100ms(.04, 10.39)))
    a = advance(a, h.evaluate(a, frame_100ms(.05, 10.39)))

    reentry = h.evaluate(a, t(.06, 10.63))
    intent = next(intent for intent in reentry.evaluation.intents if intent.action == 'enter_long')
    selection = intent.metadata['profit_target_selection']
    assert selection['ordinal'] == 2
    assert selection['episode_resistance_breaks'] == 5
    assert selection['level']['unified_level_id'] == 'R5'


def test_new_1s_macd_episode_resets_resistance_target_count():
    h, a, t, frame_1s, frame_100ms = episode_target_fixture()
    a = advance(a, h.evaluate(a, t()))
    a = advance(a, h.evaluate(a, frame_1s(.005, 10.39, .3, .2)))
    a.state['squeeze_breakout']['macd_1s']['broken_levels'] = ['R2', 'R3', 'R4', 'R5', 'R6']
    a = advance(a, h.evaluate(a, frame_1s(.01, 10.39, .1, .2)))
    a = advance(a, h.evaluate(a, frame_1s(.02, 10.39, .3, .2)))
    assert a.state['squeeze_breakout']['macd_1s']['broken_levels'] == []


def test_unbroken_forming_resistance_over_five_seconds_exits_position():
    h, a, t, frame_1s, frame_100ms = episode_target_fixture()
    a = advance(a, h.evaluate(a, t()))
    a = advance(a, h.evaluate(a, frame_1s(.005, 10.39, .3, .2)))
    a = advance(a, h.evaluate(a, frame_100ms(.01, 10.39)))
    a = advance(a, h.evaluate(a, t(.015, 10.44)))
    a.state['squeeze_entry'].update(first_fill_at=t().observed_at.timestamp(), slice_notional=3000.)
    a = replace(a, status=S.AssignmentStatus.MANAGING)
    forming = dict(state='resistance_forming', level=dict(price=10.61, confirmed_at=None))
    a = advance(a, h.evaluate(a, frame_100ms(.03, 10.60, local_events=(forming,), position=100.)))

    result = h.evaluate(a, t(5.04, 10.60, 100.))
    intent = next(intent for intent in result.evaluation.intents if intent.action == 'exit')
    assert intent.reason == 'forming_resistance_dwell_exit'
    assert intent.metadata['forming_resistance']['unified_level_id'] == 'R3'
    assert intent.metadata['dwell_seconds'] > 5.


def test_reset_does_not_retry_consumed_addition():
    h, a, t = opened()
    a.state['squeeze_entry'].update(trail_distance=1., peak_price=10.44, structural_stop=9.44)
    a.state['active_stop'] = 9.44
    result = h.evaluate(a, t(.02, 10.84, 100.))
    a = advance(a, result)
    keys = next(i.metadata['squeeze_add_levels'] for i in result.evaluation.intents if i.action == 'add_long')
    E.release_add(a.state, keys)
    a = advance(a, h.evaluate(a, t(.03, 10.70, 100.)))
    result = h.evaluate(a, t(.04, 10.85, 100.))
    assert not any(i.action == 'add_long' for i in result.evaluation.intents)


def test_offset_entry_keeps_actual_stop_as_trail_anchor():
    h, a, t = old_fixture()
    a = replace(a, parameters={**a.parameters, 'early_squeeze_breakout_contract': P.EPISODE_CONTRACT})
    a = advance(a, h.evaluate(a, t()))
    a = advance(a, h.evaluate(a, replace(t(.01, 10.44), bid=10.37, ask=10.39)))
    assert a.state['active_stop'] == 10.38
    a = replace(a, status=S.AssignmentStatus.MANAGING)
    result = h.evaluate(a, replace(t(.02, 10.395, 100.), average_price=10.39))
    assert result.state['active_stop'] == 10.38
    assert result.state['squeeze_entry']['trail_distance'] == pytest.approx(.01)


def test_candidate_compiles(monkeypatch):
    from copy import deepcopy
    from src.backend import early_squeeze_price_episode_candidate as C
    from src.backend.trading_configuration_service import configuration_base, _build_configuration_release
    from tests.test_early_squeeze_candidate import baseline
    base = configuration_base()
    base['strategy']['profiles'] = [p for p in base['strategy']['profiles'] if p['profile_id'] != C.CONTRACT]
    monkeypatch.setattr('src.backend.trading_configuration_service.configuration_base', lambda: deepcopy(base))
    payload, canvas, plan = C.build(base, baseline(base))
    _build_configuration_release(canvas_revision=canvas['revision'], canvas_profile=canvas['profile'], configuration=payload, run_plan_id=plan, strategy_profile_id=C.CONTRACT)


def test_resistance_ceiling_candidate_compiles(monkeypatch):
    from copy import deepcopy
    from src.backend import early_squeeze_resistance_ceiling_candidate as C
    from src.backend.trading_configuration_service import configuration_base, _build_configuration_release
    from tests.test_early_squeeze_candidate import baseline
    base = configuration_base()
    base['strategy']['profiles'] = [p for p in base['strategy']['profiles'] if p['profile_id'] != C.CONTRACT]
    monkeypatch.setattr('src.backend.trading_configuration_service.configuration_base', lambda: deepcopy(base))
    payload, canvas, plan = C.build(base, baseline(base))
    _build_configuration_release(canvas_revision=canvas['revision'], canvas_profile=canvas['profile'], configuration=payload, run_plan_id=plan, strategy_profile_id=C.CONTRACT)


def test_broken_resistance_ceiling_candidate_compiles(monkeypatch):
    from copy import deepcopy
    from src.backend import early_squeeze_broken_resistance_ceiling_candidate as C
    from src.backend.trading_configuration_service import configuration_base, _build_configuration_release
    from tests.test_early_squeeze_candidate import baseline
    base = configuration_base()
    base['strategy']['profiles'] = [p for p in base['strategy']['profiles'] if p['profile_id'] != C.CONTRACT]
    monkeypatch.setattr('src.backend.trading_configuration_service.configuration_base', lambda: deepcopy(base))
    payload, canvas, plan = C.build(base, baseline(base))
    _build_configuration_release(canvas_revision=canvas['revision'], canvas_profile=canvas['profile'], configuration=payload, run_plan_id=plan, strategy_profile_id=C.CONTRACT)


def test_green_close_ceiling_candidate_compiles(monkeypatch):
    from copy import deepcopy
    from src.backend import early_squeeze_green_close_ceiling_candidate as C
    from src.backend.trading_configuration_service import configuration_base, _build_configuration_release
    from tests.test_early_squeeze_candidate import baseline
    base = configuration_base()
    base['strategy']['profiles'] = [p for p in base['strategy']['profiles'] if p['profile_id'] != C.CONTRACT]
    monkeypatch.setattr('src.backend.trading_configuration_service.configuration_base', lambda: deepcopy(base))
    payload, canvas, plan = C.build(base, baseline(base))
    _build_configuration_release(canvas_revision=canvas['revision'], canvas_profile=canvas['profile'], configuration=payload, run_plan_id=plan, strategy_profile_id=C.CONTRACT)


def test_macd_episode_reentry_candidate_compiles(monkeypatch):
    from copy import deepcopy
    from src.backend import early_squeeze_macd_episode_reentry_candidate as C
    from src.backend.trading_configuration_service import configuration_base, _build_configuration_release
    from tests.test_early_squeeze_candidate import baseline
    base = configuration_base()
    base['strategy']['profiles'] = [p for p in base['strategy']['profiles'] if p['profile_id'] != C.CONTRACT]
    monkeypatch.setattr('src.backend.trading_configuration_service.configuration_base', lambda: deepcopy(base))
    payload, canvas, plan = C.build(base, baseline(base))
    _build_configuration_release(canvas_revision=canvas['revision'], canvas_profile=canvas['profile'], configuration=payload, run_plan_id=plan, strategy_profile_id=C.CONTRACT)


def test_dual_macd_reentry_candidate_compiles(monkeypatch):
    from copy import deepcopy
    from src.backend import early_squeeze_dual_macd_reentry_candidate as C
    from src.backend.trading_configuration_service import configuration_base, _build_configuration_release
    from tests.test_early_squeeze_candidate import baseline
    base = configuration_base()
    base['strategy']['profiles'] = [p for p in base['strategy']['profiles'] if p['profile_id'] != C.CONTRACT]
    monkeypatch.setattr('src.backend.trading_configuration_service.configuration_base', lambda: deepcopy(base))
    payload, canvas, plan = C.build(base, baseline(base))
    _build_configuration_release(canvas_revision=canvas['revision'], canvas_profile=canvas['profile'], configuration=payload, run_plan_id=plan, strategy_profile_id=C.CONTRACT)


def test_episode_target_continuity_candidate_compiles(monkeypatch):
    from copy import deepcopy
    from src.backend import early_squeeze_episode_target_continuity_candidate as C
    from src.backend.trading_configuration_service import configuration_base, _build_configuration_release
    from tests.test_early_squeeze_candidate import baseline
    base = configuration_base()
    base['strategy']['profiles'] = [p for p in base['strategy']['profiles'] if p['profile_id'] != C.CONTRACT]
    monkeypatch.setattr('src.backend.trading_configuration_service.configuration_base', lambda: deepcopy(base))
    payload, canvas, plan = C.build(base, baseline(base))
    _build_configuration_release(canvas_revision=canvas['revision'], canvas_profile=canvas['profile'], configuration=payload, run_plan_id=plan, strategy_profile_id=C.CONTRACT)
