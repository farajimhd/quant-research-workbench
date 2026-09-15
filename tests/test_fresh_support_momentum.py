from copy import deepcopy
from dataclasses import replace
from datetime import timedelta

import pytest

from src.trading_runtime import historical_hod as H, strategy_engine as S
from tests.test_v7_setup import prepared


def candidate():
    host, assignment, obs = prepared()
    assignment.parameters['historical_hod'].update(
        setup_early_base_enabled=1, setup_fresh_support_momentum_enabled=1)
    assignment.state['historical_hod_state']['completed_macd']['signal'] = .1
    observation = obs(2, 10.11)
    now = observation.observed_at.timestamp()
    market = deepcopy(observation.structural_detector_state)
    market['row']['local_swings'] = [dict(side='support', state='active',
        lower=9.99, price=9.995, upper=10., pivot_at=now-3, confirmed_at=now-1)]
    sources = deepcopy(observation.source_values)
    sources['market.trade_rate_10s']['value'] = 20.
    return host, assignment, replace(observation, structural_detector_state=market, source_values=sources)


def test_alternative_enters_without_inventing_bullish_episode():
    host, assignment, observation = candidate()
    H.configure(assignment.parameters)
    result = host.evaluate(assignment, observation)
    signal = result.evaluation.signals[0]
    assert signal.action == 'enter_long'
    assert signal.metadata['macd']['episode'] is None
    assert signal.metadata['macd']['line'] < signal.metadata['macd']['signal']
    assert signal.metadata['fresh_support_momentum']['passed']
    assert result.state['historical_hod_entry']['episode'] is None
    assignment.parameters['historical_hod']['setup_fresh_support_momentum_enabled'] = 0
    assert host.evaluate(assignment, observation).evaluation.signals[0].action == 'wait'


def test_experiment_is_opt_in_and_validates_configuration():
    assert H.DEFAULTS['setup_fresh_support_momentum_enabled'] == 0
    _, assignment, _ = candidate()
    for value in (True, -1, 2, float('nan')):
        p = deepcopy(assignment.parameters)
        p['historical_hod']['setup_fresh_support_momentum_enabled'] = value
        with pytest.raises(ValueError):
            H.configure(p)
    for name in ('v7_setup_enabled', 'setup_early_base_enabled'):
        p = deepcopy(assignment.parameters)
        p['historical_hod'][name] = 0
        with pytest.raises(ValueError):
            H.configure(p)


@pytest.mark.parametrize('failure', ['acceleration', 'support_age', 'future_support', 'vwap', 'spread', 'stale_macd', 'intrabar'])
def test_alternative_preserves_entry_constraints(failure):
    host, assignment, observation = candidate()
    if failure == 'acceleration':
        observation.source_values['market.trade_rate_10s']['value'] = 10.
    elif failure in ('support_age', 'future_support'):
        observation.structural_detector_state['row']['local_swings'][0]['confirmed_at'] += -6 if failure == 'support_age' else 2
    elif failure == 'vwap':
        observation = replace(observation, execution_vwap=11.)
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
