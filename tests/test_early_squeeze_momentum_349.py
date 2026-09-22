from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.trading_runtime import early_squeeze_momentum as M
from src.trading_runtime import early_squeeze_breakout as E
from src.trading_runtime import strategy_engine as S
from src.trading_runtime.momentum_session_policy import aged_action
from tests.test_early_squeeze_candidate import baseline
from tests.test_early_squeeze_momentum import level, pivot, detector
from tests.test_early_squeeze_momentum import momentum_fixture
from tests.test_vwap_resistance_ladder import advance


def bar(at, *, timeframe='1s', price=10., high=10.02, low=9.98, line=.2, signal=.1):
    from datetime import datetime, timezone
    return SimpleNamespace(source_timeframe=timeframe, evaluation_events=('bar_close',),
        observed_at=datetime.fromtimestamp(at, timezone.utc), price=price,
        bar_high=high, bar_low=low, macd_line=line, macd_signal=signal)


def test_adaptive_stop_uses_causal_ranges_and_reconciled_five_percent_cap():
    state = {}
    for second in range(10):
        M.observe_noise(state, bar(100+second, high=10.04+second*.001, low=9.96))
    evidence = M.adaptive_initial_distance(state, 10.)
    assert evidence['distance'] >= .10
    assert evidence['maximum'] == .50
    assert evidence['session_range_sample_count'] == 6
    penny = M.adaptive_initial_distance({}, 1.50)
    assert penny['distance'] == penny['maximum'] == .10


def test_forming_macd_is_independent_for_all_four_timeframes():
    state = {}
    for timeframe, seconds in [('1s', 1), ('5s', 5), ('10s', 10), ('30s', 30)]:
        M.forming_macd(bar(100, timeframe=timeframe, line=.1), state, timeframe, False)
        M.forming_macd(bar(100+seconds, timeframe=timeframe, line=.12), state, timeframe, False)
        preview = M.forming_macd(bar(100+seconds+.1, timeframe='', price=10.1), state, timeframe, True)
        assert preview['kind'] == 'forming'
        assert preview['timeframe'] == timeframe
        assert preview['line'] > preview['signal']


def test_supported_bos_requires_supported_low_or_reclaimed_resistance():
    bos = dict(broken_pivot=pivot(10.5, 95, 99, 'resistance'))
    row = detector(100, pivot(9.9, 90, 94), bos['broken_pivot'])
    support = M.supported_bos(row, {'s':level('s', 9.8, 10., 'support')}, bos, 100)
    assert support['kind'] == 'support'
    reclaimed = M.supported_bos(detector(100, bos['broken_pivot']),
        {'r':level('r', 10.1, 10.2)}, bos, 100)
    assert reclaimed['kind'] == 'reclaimed_resistance'
    assert M.supported_bos(detector(100, bos['broken_pivot']), {}, bos, 100) is None


def test_addition_identity_is_consumed_by_fill_and_resets_with_position():
    state = dict(squeeze_breakout=dict(momentum_requests={
        'add':dict(filled=False, terminal=False, keys=['R'])}),
        squeeze_entry=dict(successor=True))
    M.purchase_update(state, 'add', filled=True, terminal=True)
    assert state['squeeze_entry']['successor_added_levels'] == ['R']
    state['squeeze_entry'] = dict(successor=True)
    assert not state['squeeze_entry'].get('successor_added_levels')


def test_complete_exit_preserves_successor_reentry_context():
    from datetime import datetime, timezone
    state = dict(squeeze_breakout={}, squeeze_entry=dict(successor=True, first_fill_at=90,
        successor_entry_resistance_id='R', successor_position_high=10.75))
    E.record_exit(state, datetime.fromtimestamp(100, timezone.utc), 'protective_stop', 0,
        contract=M.CONTRACT)
    assert 'squeeze_entry' not in state
    assert state['squeeze_breakout']['successor_last_position'] == dict(
        closed_at=100, entry_resistance_id='R', resistance_high=10.75)


def test_recovered_green_position_can_receive_bracket_instead_of_forced_exit():
    active = dict(first_fill_at=0, age_mode='red')
    rows = {'below':level('below', 10.1, 10.2), 'above':level('above', 10.8, 10.9)}
    net = dict(status='verified', net_pnl=5., break_even=10.)
    policy = dict(age_seconds=120., red_grace_seconds=60.)
    action = aged_action(active, rows, now=130, bid=10.5, tick=.01, net=net,
        policy=policy, target_floor=10.5, recover_with_bracket=True)
    assert action['action'] == 'protect' and action['stop'] > net['break_even']


def test_strategy_349_candidate_is_separate_and_requests_all_macd_timeframes():
    from src.backend import early_squeeze_momentum_349_candidate as candidate
    from src.backend.trading_configuration_service import configuration_base
    base = configuration_base()
    payload, _, plan_id = candidate.build(base, baseline(base))
    profile = next(p for p in payload['strategy']['profiles'] if p['profile_id'] == M.CONTRACT)
    parameters = profile['parameters']
    assert parameters['momentum_successor'] == M.SUCCESSOR
    assert parameters['momentum_full_session']['age_seconds'] == 120
    assert S.strategy_rule_timeframes(parameters) == {'100ms', '1s', '5s', '10s', '30s'}
    plan = next(p for p in payload['run_plans']['plans'] if p['run_plan_id'] == plan_id)
    rule = next(r for r in payload['market_discovery']['rule_sets']
        if r['rule_set_id'] == M.CONTRACT+'-tradability')
    assert plan['name'] == candidate.LABEL
    assert not any(c['left_source_id'] == 'market.previous_close' for c in rule['conditions'])
    assert not any(c['left_source_id'] == 'market.last_price' and c['comparator'] == 'greater_or_equal'
        for c in rule['conditions'])


def test_strategy_350_historical_watchlist_plan_needs_no_previous_close_interval():
    from datetime import datetime, timezone
    from src.backend import early_squeeze_momentum_350_candidate as candidate
    from src.backend.historical_watchlist_plan import compile_historical_watchlist_plan
    from src.backend.trading_configuration_service import configuration_base
    base = configuration_base()
    payload, _, plan_id = candidate.build(base, baseline(base))
    plan = next(p for p in payload['run_plans']['plans'] if p['run_plan_id'] == plan_id)
    compiled = compile_historical_watchlist_plan(payload, plan['watchlist_ids'][0],
        start=datetime(2026, 8, 21, 8, tzinfo=timezone.utc),
        end=datetime(2026, 8, 21, 9, tzinfo=timezone.utc))
    assert 'market.previous_close' not in compiled['qmd_sources']
    assert all(row['field_id'] != 'market.previous_close' for row in compiled['external_features'])


def test_real_engine_successor_entry_uses_supported_bos_multi_macd_and_adaptive_stop():
    h, assignment, trade, _, _ = momentum_fixture()
    parameters = deepcopy(assignment.parameters)
    parameters['momentum_successor'] = M.SUCCESSOR
    assignment = replace(assignment, parameters=parameters, state=deepcopy(assignment.state))
    observation = trade(16.02, 10.44)
    now = observation.observed_at.timestamp()
    d = assignment.state['squeeze_breakout']
    for timeframe in ('1s', '5s', '10s', '30s'):
        d['successor_completed_macd_'+timeframe] = dict(
            at=now-.1, line=.02, signal=.01, slow=10.39)
    market = deepcopy(observation.structural_detector_state)
    high = pivot(10.4, now-2, now-1, 'resistance')
    low = pivot(10.30, now-4, now-3, 'support')
    market['row'] = detector(now, low, high)
    support = dict(level('support', 10.25, 10.35, 'support'), price=10.30, side=1,
        confirmed_at_ms=(now-3)*1000, book_version='causal-level-book-v7-mle-1')
    observation = replace(observation, previous_close=10., structural_detector_state=market,
        structural_support_levels=tuple(observation.structural_support_levels)+(support,))
    result = h.evaluate(assignment, observation)
    assert result.evaluation.intents, result.evaluation.signals[0].reason
    intent = next(i for i in result.evaluation.intents if i.action == 'enter_long')
    assert intent.metadata['bos_support']['kind'] == 'support'
    adaptive = intent.metadata['momentum_initial_stop']['adaptive']
    assert adaptive['distance'] == .10
    assert observation.ask-intent.invalidation_price >= .10


def successor_ready():
    h, assignment, trade, one, fast = momentum_fixture()
    parameters = deepcopy(assignment.parameters)
    parameters['momentum_successor'] = M.SUCCESSOR
    parameters['momentum_full_session'] = {**parameters.get('momentum_full_session', {}),
        'age_seconds':120., 'red_grace_seconds':60.}
    assignment = replace(assignment, parameters=parameters, state=deepcopy(assignment.state))
    observation = trade(16.02, 10.44)
    now = observation.observed_at.timestamp()
    d = assignment.state['squeeze_breakout']
    for timeframe in ('1s', '5s', '10s', '30s'):
        d['successor_completed_macd_'+timeframe] = dict(at=now-.1, line=.02, signal=.01, slow=10.39)
    high = pivot(10.4, now-2, now-1, 'resistance')
    low = pivot(10.30, now-4, now-3, 'support')
    support = dict(level('support', 10.25, 10.35, 'support'), price=10.30, side=1,
        confirmed_at_ms=(now-3)*1000, book_version='causal-level-book-v7-mle-1')
    market = deepcopy(observation.structural_detector_state)
    market['row'] = detector(now, low, high)
    observation = replace(observation, previous_close=10., structural_detector_state=market,
        structural_support_levels=tuple(observation.structural_support_levels)+(support,))
    return h, assignment, trade, observation, support


def test_successor_price_filters_watch_but_block_purchases():
    h, assignment, _, observation, _ = successor_ready()
    expensive = h.evaluate(assignment, replace(observation, previous_close=20.))
    assert expensive.evaluation.signals[0].reason == 'prior_regular_close_below_20_required'
    sources = deepcopy(observation.source_values)
    sources['market.last_price']['value'] = .99
    penny = h.evaluate(assignment, replace(observation, price=.99, bid=.98, ask=.99,
        source_values=sources))
    assert penny.evaluation.signals[0].reason == 'current_price_at_least_1_required'
    assert not any(i.action == 'exit' for i in penny.evaluation.intents)


def test_successor_rapid_reentry_crosses_saved_position_high():
    h, assignment, trade, observation, _ = successor_ready()
    now = observation.observed_at.timestamp()
    d = assignment.state['squeeze_breakout']
    d['successor_last_position'] = dict(closed_at=now-5,
        entry_resistance_id='support', resistance_high=10.50)
    blocked = h.evaluate(assignment, observation)
    assert blocked.evaluation.signals[0].reason == 'reentry_requires_prior_resistance_high_break'
    assignment = advance(assignment, blocked)
    sources = deepcopy(observation.source_values)
    sources['market.last_price']['value'] = 10.51
    crossed = replace(observation, price=10.51, bid=10.50, ask=10.51,
        source_values=sources)
    result = h.evaluate(assignment, crossed)
    assert any(i.action == 'enter_long' for i in result.evaluation.intents)


def test_successor_five_second_trail_and_ten_second_vwap_floor_emit_protection():
    h, assignment, trade, observation, _ = successor_ready()
    now = observation.observed_at.timestamp()
    assignment = replace(assignment, status=S.AssignmentStatus.MANAGING,
        state=deepcopy(assignment.state))
    assignment.state.update(active_stop=10., squeeze_entry=dict(successor=True,
        requested_at=now-10, first_fill_at=now-5.1, entry_price=10.2, average_gap=.1,
        stop=10., stop_reason='five_percent_entry_stop', stop_selection=dict(price=10.),
        stop_steps=0, stop_anchor_lower=10., target_multiplier=5, submitted_multiplier=5,
        broken_levels=[], target_session_step=0, successor_position_high=10.44))
    first = h.evaluate(assignment, replace(observation, position_quantity=100., average_price=10.2))
    assignment = advance(assignment, first)
    raised = replace(trade(16.12, 10.54, 100.), previous_close=10., average_price=10.2,
        structural_detector_state=observation.structural_detector_state,
        structural_support_levels=observation.structural_support_levels)
    second = h.evaluate(assignment, raised)
    trail = next(i for i in second.evaluation.intents if i.action == 'replace_protective_stop')
    assert trail.reason == 'five_second_price_trailing_stop'
    assert trail.invalidation_price > 10.

    assignment = replace(assignment, state=deepcopy(assignment.state))
    assignment.state['squeeze_entry']['first_fill_at'] = raised.observed_at.timestamp()-10.1
    assignment.state['squeeze_entry'].pop('successor_trail_started_at', None)
    assignment.state['active_stop'] = assignment.state['squeeze_entry']['stop'] = 10.
    sources = deepcopy(raised.source_values)
    sources['indicator.vwap.execution_value@100ms']['value'] = 10.30
    floor_result = h.evaluate(assignment, replace(raised, source_values=sources))
    floor_intent = next(i for i in floor_result.evaluation.intents if i.action == 'replace_protective_stop')
    assert floor_intent.reason == 'ten_second_vwap_floor'
