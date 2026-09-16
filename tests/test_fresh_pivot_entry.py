from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.market_engine.structural_detector import VERSION
from src.market_engine.swing_pivot_witness import CONTRACT
from src.trading_runtime import historical_hod as H, v7_setup as V
from tests.test_below_vwap_base import candidate


def merged_candidate():
    host, assignment, observation = candidate()
    now = observation.observed_at.timestamp()
    market = deepcopy(observation.structural_detector_state)
    level = dict(market['row']['local_swings'][0], level_id=7, scale='local',
                 pivot_at=now-100, confirmed_at=now-90)
    level['fresh_pivot'] = dict(contract=CONTRACT, clock='event_time', level_id=7,
        side='support', scale='local', price=9.995, pivot_at=now-3,
        confirmed_at=now-1, reversal_distance=.04)
    market['row'].update(contract=VERSION, effective_at=now, local_swings=[deepcopy(level)],
                         confirmed_swings=[], pivot_swings=[level])
    assignment.parameters['historical_hod']['setup_fresh_pivot_enabled'] = 1
    return host, assignment, replace(observation, structural_detector_state=market)


def test_fresh_merged_support_enters_only_when_enabled_and_preserves_anchor():
    host, assignment, observation = merged_candidate()
    H.configure(assignment.parameters)
    frozen = deepcopy(observation.structural_detector_state)
    result = host.evaluate(deepcopy(assignment), observation)
    assert result.evaluation.signals[0].action == 'enter_long'
    swing = result.state['historical_hod_entry']['initial_stop_selection']
    assert swing['pivot_at'] > swing['anchored_level']['pivot_at']
    assert swing['lower'] == swing['anchored_level']['lower']
    assert observation.structural_detector_state == frozen
    assignment.parameters['historical_hod']['setup_fresh_pivot_enabled'] = 0
    assert host.evaluate(assignment, observation).evaluation.signals[0].action == 'wait'


@pytest.mark.parametrize('change', ['future', 'stale', 'wrong_clock', 'wrong_role', 'wrong_id',
                                   'outside_band', 'inactive', 'missing', 'old_contract'])
def test_bad_or_stale_pivot_never_grants_entry(change):
    host, assignment, observation = merged_candidate()
    row = observation.structural_detector_state['row']
    level = row['pivot_swings'][0]; witness = level['fresh_pivot']
    now = observation.observed_at.timestamp()
    if change == 'future': witness['confirmed_at'] = now+1
    if change == 'stale': witness.update(pivot_at=now-22,confirmed_at=now-21)
    if change == 'wrong_clock': witness['clock'] = 'candle_sequence'
    if change == 'wrong_role': witness['side'] = 'resistance'
    if change == 'wrong_id': witness['level_id'] = 8
    if change == 'outside_band': witness['price'] = 9.5
    if change == 'inactive': level['state'] = 'awaiting_retest'
    if change == 'missing': level.pop('fresh_pivot')
    if change == 'old_contract': row['contract'] = 'structural-candle-detector-10'
    assert host.evaluate(assignment, observation).evaluation.signals[0].action == 'wait'


def test_fresh_pivot_does_not_bypass_atr_distance_or_risk():
    host, assignment, observation = merged_candidate()
    assignment.parameters['historical_hod']['setup_below_vwap_maximum_distance_atr'] = 3.
    assert host.evaluate(deepcopy(assignment), replace(observation, volatility=.001)).evaluation.signals[0].action == 'wait'
    assignment.parameters['historical_hod']['setup_below_vwap_maximum_distance_atr'] = 0.
    assignment.parameters['historical_hod']['setup_base_maximum_risk_pct'] = .01
    assert host.evaluate(assignment, observation).evaluation.signals[0].action == 'wait'


def test_breach_retires_witness_even_when_current_detector_no_longer_exposes_level():
    _, _, observation = merged_candidate()
    row = observation.structural_detector_state['row']; now = row['effective_at']
    state = {}; obs = SimpleNamespace(position_quantity=0, observed_at=observation.observed_at)
    support = V.fresh_pivot_supports(row, now)[0]
    V.recovery_observe(state,None,dict(bar=dict(end=now,low=10.)),obs,0,row,True,fresh_pivots=True)
    V.recovery_observe(state,None,dict(bar=dict(end=now+1,low=9.8)),obs,0,
                       dict(contract=VERSION,effective_at=now+1,pivot_swings=[]),True,fresh_pivots=True)
    assert V.swing_key(support) in state['retired_swings']
    assert state['pivot_supports'] == []


def test_retired_fresh_support_cannot_be_selected_before_any_position():
    host, assignment, observation = merged_candidate()
    row = observation.structural_detector_state['row']
    support = V.fresh_pivot_supports(row,row['effective_at'])[0]
    assignment.state['v7_setup']['retired_swings'] = {V.swing_key(support):row['effective_at']}
    assert host.evaluate(assignment,observation).evaluation.signals[0].action == 'wait'


@pytest.mark.parametrize('value', [True, -1, 2, float('nan')])
def test_new_setting_rejects_non_boolean_numeric_values(value):
    _, assignment, _ = merged_candidate()
    assignment.parameters['historical_hod']['setup_fresh_pivot_enabled'] = value
    with pytest.raises(ValueError): H.configure(assignment.parameters)


def test_new_pivot_cannot_revive_previously_retired_anchor():
    host, assignment, observation = merged_candidate()
    row = observation.structural_detector_state['row']
    level = row['pivot_swings'][0]
    anchor_key = V.swing_key(level)
    projected = V.fresh_pivot_supports(row, row['effective_at'])[0]
    assert V.swing_key(projected) == anchor_key
    assignment.state['v7_setup']['retired_swings'] = {anchor_key:row['effective_at']-20}
    assert host.evaluate(deepcopy(assignment), observation).evaluation.signals[0].action == 'wait'
    level['fresh_pivot'].update(pivot_at=row['effective_at']-2,confirmed_at=row['effective_at'])
    assert host.evaluate(assignment, observation).evaluation.signals[0].action == 'wait'
