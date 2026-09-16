from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from src.trading_runtime import historical_hod as H, strategy_engine as S
from src.trading_runtime import v7_setup as V
from tests.test_v7_setup import prepared


def candidate():
    host, assignment, obs = prepared()
    assignment.parameters['historical_hod'].update(
        setup_early_base_enabled=1, setup_below_vwap_base_enabled=1)
    assignment.state['historical_hod_state']['completed_macd']['signal'] = .1
    assignment.state['historical_hod_state']['hod'] = 10.3
    observation = obs(2, 10.11)
    now = observation.observed_at.timestamp()
    market = deepcopy(observation.structural_detector_state)
    market['row']['local_swings'] = [dict(side='support', state='active',
        lower=9.99, price=9.995, upper=10., pivot_at=now-3, confirmed_at=now-1)]
    sources = deepcopy(observation.source_values)
    sources['market.trade_rate_10s']['value'] = 20.
    return host, assignment, replace(observation, structural_detector_state=market, source_values=sources, execution_vwap=10.2)


def test_alternative_enters_without_inventing_bullish_episode():
    host, assignment, observation = candidate()
    H.configure(assignment.parameters)
    result = host.evaluate(assignment, observation)
    signal = result.evaluation.signals[0]
    assert signal.action == 'enter_long'
    assert signal.metadata['macd']['episode'] is None
    assert signal.metadata['macd']['line'] < signal.metadata['macd']['signal']
    assert signal.metadata['below_vwap_base']['passed']
    assert result.state['historical_hod_entry']['episode'] is None
    assignment.parameters['historical_hod']['setup_below_vwap_base_enabled'] = 0
    assert host.evaluate(assignment, observation).evaluation.signals[0].action == 'wait'


def test_experiment_is_opt_in_and_validates_configuration():
    assert H.DEFAULTS['setup_below_vwap_base_enabled'] == 0
    _, assignment, _ = candidate()
    for value in (True, -1, 2, float('nan')):
        p = deepcopy(assignment.parameters)
        p['historical_hod']['setup_below_vwap_base_enabled'] = value
        with pytest.raises(ValueError):
            H.configure(p)
    for name in ('v7_setup_enabled', 'setup_early_base_enabled'):
        p = deepcopy(assignment.parameters)
        p['historical_hod'][name] = 0
        with pytest.raises(ValueError):
            H.configure(p)


@pytest.mark.parametrize('atr,allowed', [(.04, True), (.02, False), (0., False),
    (None, False), (float('nan'), False)])
def test_below_vwap_distance_uses_completed_candle_atr(atr, allowed):
    host, assignment, observation = candidate()
    assignment.parameters['historical_hod']['setup_below_vwap_maximum_distance_atr'] = 3.
    H.configure(assignment.parameters)
    result = host.evaluate(assignment, replace(observation, volatility=atr))
    assert (result.evaluation.signals[0].action == 'enter_long') is allowed


@pytest.mark.parametrize('change', ['missing', 'future', 'stale', 'wrong_bar'])
def test_distance_does_not_use_unavailable_or_wrong_clock_atr(change):
    market = dict(closed_atr=.1, vwap=10.2, closed_at=100., bar=dict(end=100.))
    if change == 'missing': market.pop('closed_atr')
    if change == 'future': market['closed_at'] = 101.
    if change == 'stale': market.update(closed_at=97., bar=dict(end=97.))
    if change == 'wrong_bar': market['bar']['end'] = 99.
    assert not V.below_vwap_distance(market, 10., now=100., maximum_atr=3., maximum_age=2.)['passed']


@pytest.mark.parametrize('value', [True, -1., float('nan'), float('inf')])
def test_distance_parameter_is_finite_nonnegative(value):
    _, assignment, _ = candidate()
    assignment.parameters['historical_hod']['setup_below_vwap_maximum_distance_atr'] = value
    with pytest.raises(ValueError):
        H.configure(assignment.parameters)


def test_pending_entry_uses_persisted_completed_atr_not_intrabar_volatility():
    host, assignment, observation = candidate()
    assignment.parameters['historical_hod']['setup_below_vwap_maximum_distance_atr'] = 3.
    entered = host.evaluate(assignment, replace(observation, volatility=.04))
    assert entered.evaluation.signals[0].action == 'enter_long'
    state = deepcopy(entered.state)
    state['pending_capital_request'] = {'request_id': 'test-request',
        'requested_at': observation.observed_at.isoformat()}
    assignment = replace(assignment, state=state, status=S.AssignmentStatus.ENTRY_PENDING)
    observation = replace(observation, observed_at=observation.observed_at+timedelta(seconds=.1),
        evaluation_events=('market_data_update',), volatility=999.)
    result = host.evaluate(assignment, observation)
    evidence = result.evaluation.signals[0].metadata['below_vwap_base']['distance']
    assert evidence['atr'] == .04
    assert evidence['passed']


def test_passive_frame_preserves_one_second_atr_across_five_second_update():
    _, assignment, observation = candidate()
    frame = SimpleNamespace(as_of=observation.observed_at, timeframe='1s',
        indicator=dict(atr_14=.04, execution_vwap=10.2, macd_line=.01, macd_signal=.02),
        bar=dict(open=10., high=10.12, low=9.99, close=10.11, volume=100.))
    state = H.observe_frame(frame, {}, assignment.parameters)
    assert state['closed_atr'] == .04
    at = state['closed_at']
    frame.timeframe = '5s'
    frame.indicator['atr_14'] = 99.
    updated = H.observe_frame(frame, state, assignment.parameters)
    assert updated['closed_atr'] == .04
    assert updated['closed_at'] == at


@pytest.mark.parametrize('failure', ['acceleration', 'support_age', 'future_support', 'missing_vwap', 'above_vwap', 'spread', 'stale_macd', 'intrabar'])
def test_alternative_preserves_entry_constraints(failure):
    host, assignment, observation = candidate()
    if failure == 'acceleration':
        observation.source_values['market.trade_rate_10s']['value'] = 10.
    elif failure in ('support_age', 'future_support'):
        observation.structural_detector_state['row']['local_swings'][0]['confirmed_at'] += -6 if failure == 'support_age' else 2
    elif failure == 'missing_vwap':
        observation = replace(observation, execution_vwap=None)
    elif failure == 'above_vwap':
        observation = replace(observation, execution_vwap=10.)
    elif failure == 'spread':
        observation = replace(observation, bid=9., ask=11.)
    elif failure == 'stale_macd':
        assignment.state['historical_hod_state']['completed_macd']['at'] -= 10
    else:
        observation = replace(observation, evaluation_events=('market_data_update',))
    assert not host.evaluate(assignment, observation).evaluation.intents


@pytest.mark.parametrize('failure', [None, 'retreat', 'expired', 'acceleration'])
def test_pending_acquisition_rechecks_alternative_evidence(failure):
    host, assignment, observation = candidate()
    entered = host.evaluate(assignment, observation)
    assert entered.evaluation.signals[0].action == 'enter_long'
    state = deepcopy(entered.state)
    state['pending_capital_request'] = {'request_id': 'test-request',
        'requested_at': observation.observed_at.isoformat()}
    assignment = replace(assignment, state=state, status=S.AssignmentStatus.ENTRY_PENDING)
    observation = replace(observation, observed_at=observation.observed_at+timedelta(seconds=1 if failure == 'expired' else .1),
        evaluation_events=('market_data_update',))
    if failure == 'retreat':
        observation = replace(observation, price=10.10)
    if failure == 'acceleration':
        observation.source_values['market.trade_rate_10s']['value'] = 10.
    result = host.evaluate(assignment, observation)
    assert result.evaluation.signals[0].reason == ('entry_acquisition_invalidated' if failure else 'historical_hod_entry')


@pytest.mark.parametrize('failure', [None, 'protected', 'unknown_protection', 'old_base', 'failed_entry', 'disabled', 'profitable', 'unknown_outcome', 'future_outcome', 'wrong_entry'])
def test_independent_base_only_releases_unprotected_prior_attempt(failure):
    prior = dict(at=10., entry_at=1., initial_fill_price=11., stop_above_initial_fill=False,
        completed_trade_outcome=dict(status='verified', entry_at=1., closed_at=9., net_pnl=-1.),
        stop=10.8, body_high=12., setup=dict(phase='post_breakout', breakout_threshold=12.))
    state = dict(last_exit=prior, range=dict(start=11., end=18.))
    swing = dict(pivot_at=15., confirmed_at=19., lower=9.9)
    if failure == 'protected':
        prior['stop_above_initial_fill'] = True
    elif failure == 'unknown_protection':
        prior.pop('stop_above_initial_fill')
    elif failure == 'old_base':
        state['range']['start'] = 9.
    elif failure == 'failed_entry':
        prior['setup']['entry_failure_recovery'] = 11.
    elif failure == 'profitable':
        prior['completed_trade_outcome']['net_pnl'] = 1.
    elif failure == 'unknown_outcome':
        prior.pop('completed_trade_outcome')
    elif failure == 'future_outcome':
        prior['completed_trade_outcome']['closed_at'] = 11.
    elif failure == 'wrong_entry':
        prior['completed_trade_outcome']['entry_at'] = 2.
    reason, phase = V.recovery_permission(state, swing, dict(bar=dict(end=20., close=10.1)),
        independent_base=failure != 'disabled', stop_gain_guard=True,
        unprotected_reentry=True, entry_reclaim=True)
    assert bool(reason) == bool(failure)
    if not failure:
        assert phase == 'building'
