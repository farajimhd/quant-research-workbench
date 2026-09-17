from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from src.trading_runtime import resistance_zones as Z
from src.trading_runtime import vwap_resistance_ladder as V
from tests.test_vwap_resistance_ladder import fixture, NOW


def zone(key, lower, upper=None):
    return dict(unified_level_id=key, lower=lower, upper=upper if upper is not None else lower+.1,
                price=lower, side=-1, confirmed_at_ms=0)


def observe(state, rows, price, second):
    return Z.observe(SimpleNamespace(observed_at=NOW+timedelta(seconds=second), price=price), state, rows)


def test_overlap_warmup_one_break_and_no_repeat():
    rows = {'A': zone('A', 10, 10.2), 'B': zone('B', 10.1, 10.3), 'C': zone('C', 11)}
    state = {}
    observe(state, rows, 9, 0)
    assert len(state['known']) == 2
    assert not observe(state, rows, 10.25, 1)
    assert len(observe(state, rows, 10.31, 2)) == 1
    observe(state, rows, 10, 3)
    assert not observe(state, rows, 10.4, 4)
    assert state['broken'] == ['zone:A']


def test_threshold_uses_prior_gaps_and_encountered_geometry_is_frozen():
    rows = {str(i): zone(str(i), i) for i in range(10, 14)}
    rows.update(A=zone('A', 15, 15.1), B=zone('B', 15.3, 15.4))
    state = {}
    observe(state, rows, 9, 0)
    for i in range(10, 14): observe(state, rows, i+.2, i)
    assert state['grouping_gap_samples'] == 2  # Fourth break is not used to group itself.
    observe(state, rows, 14, 20)
    assert state['grouping_threshold'] == pytest.approx(.45)
    assert state['known']['zone:A']['members'] == ['A', 'B']
    observe(state, rows, 15.05, 21)
    frozen = deepcopy(state['known']['zone:A'])
    rows['B'] = zone('B', 18, 19)
    observe(state, rows, 15.2, 22)
    assert state['known']['zone:A']['upper'] == frozen['upper']


def test_new_level_above_previous_price_cannot_create_retroactive_break():
    state = {}
    observe(state, {}, 9, 0)
    assert not observe(state, {'A': zone('A', 10)}, 11, 1)
    assert not state['broken']


def test_late_entry_requires_confirmed_retest_and_uses_band_stop():
    host, assignment, observations = fixture()
    assignment.parameters['vwap_ladder'].update(require_late_retest=1, allow_retest_stop_fallback=1)
    market = dict(session=NOW.date().isoformat(), known={}, broken=[str(i) for i in range(6)],
                  break_rows={str(i): zone(str(i), 8+i*.1) for i in range(6)}, at=NOW.timestamp())
    assignment = replace(assignment, state={'vwap_ladder_market': market})
    o = observations()
    result = host.evaluate(assignment, o)
    assert result.evaluation.signals[0].reason == 'late_pullback_retest_required'
    swing = o.structural_detector_state['row']['local_swings'][0]
    anchor = zone('retested', 9.45, 9.52)
    proof = dict(anchor=anchor, pivot_at=swing['pivot_at'], pivot_price=swing['price'],
                 break_count=6, recovered_at=NOW.timestamp()-1)
    o.structural_detector_state['row']['vwap_retests'] = [proof]
    market['known'] = {f'R{i}': zone(f'R{i}', 10+i*.2) for i in range(1,4)}
    result = host.evaluate(assignment, o)
    assert result.evaluation.intents[0].invalidation_price == pytest.approx(9.44)
    assert result.state['vwap_ladder_entry']['retest_anchor']['anchor']['unified_level_id'] == 'retested'
    proof['break_count'] = 5
    assert not host.evaluate(assignment, o).evaluation.intents


def test_retest_sequence_rejects_same_candle_and_expires_on_next_break():
    at = NOW.timestamp()
    anchor = dict(zone('A', 9.4, 9.6), broken_at=at-5)
    ladder = dict(broken=['A'], known={}, break_rows={'A':anchor})
    swing = dict(side='support', lower=9.4, price=9.45, upper=9.5, pivot_at=at, confirmed_at=at+1)
    first = dict(session='s', reset=False, row=dict(local_swings=[], developing_swings={'low':{'pivot_at':at}}))
    Z.observe_retests(first, {}, dict(time=at-1,end=at,low=9.45,high=9.7,close=9.65), {}, ladder)
    assert not first['row']['vwap_retests'][0].get('recovered_at')
    following = dict(session='s', reset=False, row=dict(local_swings=[swing]))
    Z.observe_retests(following, first, dict(time=at,end=at+1,low=9.65,high=9.8,close=9.7), {}, ladder)
    assert following['row']['vwap_retests'][0]['recovered_at'] == at+1
    ladder['broken'].append('B')
    final = dict(session='s', reset=False, row=dict(local_swings=[swing]))
    Z.observe_retests(final, following, dict(time=at+1,end=at+2,low=9.7,high=9.8,close=9.8), {}, ladder)
    assert not final['row']['vwap_retests']


def test_early_fallback_requires_reaction_and_selects_closest_retested_band():
    host, assignment, observations = fixture()
    assignment.parameters['vwap_ladder'].update(allow_retest_stop_fallback=1)
    o = replace(observations(), structural_support_levels=())
    assert host.evaluate(assignment, o).evaluation.signals[0].reason == 'level_retest_stop_required'
    swing = o.structural_detector_state['row']['local_swings'][0]
    proofs = [dict(anchor=zone(key, low, 9.6), pivot_at=swing['pivot_at'], pivot_price=swing['price'],
                   break_count=0, recovered_at=NOW.timestamp()-1) for key, low in [('far', 9.3), ('near', 9.45)]]
    o.structural_detector_state['row']['vwap_retests'] = proofs
    result = host.evaluate(assignment, o)
    assert result.evaluation.intents[0].invalidation_price == pytest.approx(9.44)
    assert result.state['vwap_ladder_entry']['retest_anchor']['anchor']['unified_level_id'] == 'near'
    proofs[1]['recovered_at'] = NOW.timestamp()+1
    assert host.evaluate(assignment, o).evaluation.intents[0].invalidation_price == pytest.approx(9.29)


def test_grouped_management_counts_zone_once_and_survives_json_restart():
    import json
    from tests.test_vwap_resistance_ladder import advance, S
    host, assignment, observations = fixture()
    assignment.parameters['vwap_ladder'].update(group_resistances=1)
    def observation(i=0, price=10., position=0.):
        o = observations(i, price, position)
        levels = list(o.structural_resistance_levels)
        duplicate = dict(levels[0], unified_level_id='R1-overlap', upper=10.24)
        return replace(o, structural_resistance_levels=tuple(levels+[duplicate]))
    result = host.evaluate(assignment, observation())
    assignment = advance(assignment, result)
    assignment.state['vwap_ladder_entry']['slice_notional'] = 3000.
    assignment = replace(assignment, status=S.AssignmentStatus.MANAGING)
    for i, price in enumerate([10.23, 10.25, 10.43, 10.63], 1):
        result = host.evaluate(assignment, observation(i, price, 300))
        actions = [intent.action for intent in result.evaluation.intents]
        assert actions.count('add_long') == (1 if i in (2, 3) else 0)
        assert ('replace_protective_stop' in actions) == (i == 4)
        assignment = advance(assignment, result)
        assignment.state.update(json.loads(json.dumps(assignment.state)))
    assert assignment.state['active_stop'] == pytest.approx(10.19)
    assert len(assignment.state['vwap_ladder_market']['broken']) == 3


def test_candidate_clones_baseline_without_mutating_it():
    from src.backend.vwap_retest_candidate import BASELINE_ID, BASELINE_HASH, PROFILE, PLAN, prepare_payload
    parent = dict(profile_id=V.CONTRACT, parameters={'vwap_ladder': dict(V.DEFAULTS)})
    baseline = dict(candidate_id=BASELINE_ID, content_hash=BASELINE_HASH,
                    payload=dict(strategy={'profiles':[parent]}, run_plans={'plans':[
                        dict(run_plan_id=PLAN, allowed_environments=['backtest'])]}))
    before = deepcopy(baseline)
    payload = prepare_payload(baseline)
    assert baseline == before
    assert payload['strategy']['profiles'][0] == parent
    assert payload['strategy']['profiles'][-1]['profile_id'] == PROFILE
    assert all(payload['strategy']['profiles'][-1]['parameters']['vwap_ladder'][k] == 1 for k in V.RETEST_DEFAULTS)
    baseline['content_hash'] = 'changed'
    with pytest.raises(ValueError, match='baseline changed'):
        prepare_payload(baseline)


@pytest.mark.parametrize('side', [0, -1])
def test_raw_transition_or_resistance_requires_prior_break_then_later_pivot(side):
    at = NOW.timestamp()
    band = dict(zone('A', 9.4, 9.6), side=side)
    ladder = dict(broken=[], known={}, break_rows={})
    previous = dict(session='s', vwap_retests=dict(levels={'A':band}, close=9.5))
    broken = dict(session='s', row={})
    Z.observe_retests(broken, previous, dict(time=at-1,end=at,low=9.45,high=9.7,close=9.7), {'A':band}, ladder)
    assert not broken['row']['vwap_retests']
    pivot = dict(session='s', row={})
    Z.observe_retests(pivot, broken, dict(time=at+1,end=at+2,low=9.5,high=9.7,close=9.55), {'A':band}, ladder)
    assert len(pivot['row']['vwap_retests']) == 1
    assert 'recovered_at' not in pivot['row']['vwap_retests'][0]
    recovery = dict(session='s', row={'local_swings':[dict(side='support', pivot_at=at+2)]})
    Z.observe_retests(recovery, pivot, dict(time=at+2,end=at+3,low=9.61,high=9.8,close=9.75), {'A':band}, ladder)
    assert recovery['row']['vwap_retests'][0]['recovered_at'] == at+3
    reset = dict(session='s', reset=True, row={})
    Z.observe_retests(reset, recovery, dict(time=at+3,end=at+4,low=9.61,high=9.8,close=9.75), {'A':band}, ladder)
    assert not reset['row']['vwap_retests']
