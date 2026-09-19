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
