from copy import deepcopy
from dataclasses import replace

import pytest

from src.market_engine.structural_detector import VERSION
from src.trading_runtime import early_squeeze_momentum as M
from src.trading_runtime.vwap_resistance_ladder import POLICY
from tests.test_early_squeeze_consistent import fixture
from tests.test_early_squeeze_price_episode import dual_macd_fixture
from tests.test_vwap_resistance_ladder import advance
from src.trading_runtime import strategy_engine as S


def level(key, lower, upper, role='resistance'):
    return dict(unified_level_id=key, lower=lower, upper=upper, role=role,
        input_policy=POLICY, seed_input_policy=POLICY)


def detector(now, *pivots):
    return dict(contract=VERSION, effective_at=now, local_swings=list(pivots))


def pivot(price, occurred, confirmed, side='support'):
    return dict(price=price, lower=price, upper=price, pivot_at=occurred,
        confirmed_at=confirmed, side=side)


def test_gap_uses_activation_book_and_excludes_entry_distance():
    rows = {r['unified_level_id']: r for r in [level('a', 11, 11),
        level('b', 13, 13), level('c', 40, 40), level('d', 41, 41),
        level('s', 15, 15, 'support')]}
    result = M.freeze_gap(rows, 10, 100)
    assert result['gaps'] == [2, 27]
    assert result['average'] == 14.5
    rows['a']['lower'] = 12
    assert result['levels'][0]['lower'] == 11
    assert M.freeze_gap({'a': rows['a']}, 10, 100)['average'] is None


@pytest.mark.parametrize('cutoff,last,valid', [(100, 100, True), (99, 98, True),
    (98.99, 98, False), (100.1, 100, False), (100, 100.1, False)])
def test_causal_v7_freshness(cutoff, last, valid):
    rows = {'a': level('a', 10, 11)}
    assert M.fresh_structure(rows, dict(as_of=cutoff, max_input_timestamp=last), 100) is valid
    rows['a']['seed_input_policy'] = 'retrospective'
    assert not M.fresh_structure(rows, dict(as_of=cutoff, max_input_timestamp=last), 100)


def test_swing_age_is_pivot_age_and_future_confirmation_is_rejected():
    rows = {'s': level('s', 9.8, 10.0, 'support')}
    low = pivot(9.9, 91, 95)
    result = M.supported_swing(detector(100, low), rows, 100)
    assert result['pivot'] == low
    assert M.supported_swing(detector(100, pivot(9.9, 89, 99)), rows, 100) is None
    assert M.supported_swing(detector(100, pivot(9.9, 99, 101)), rows, 100) is None
    assert M.supported_swing(detector(101, low), rows, 100) is None
    assert M.supported_swing(detector(100, pivot(9.7, 91, 95)), rows, 100) is None


def test_stop_fallbacks_and_below_lower_band():
    rows = {'s': level('s', 9.94, 9.96, 'support')}
    stop = M.initial_stop(detector(100, pivot(9.95, 95, 99)), rows, 100,
        10.05, 10, .01, distance_reference='vwap')
    assert stop['price'] == 9.94 and stop['reason'] == 'supported_swing_low_stop'
    stop = M.initial_stop(detector(100), rows, 100, 10.05, 10, .01, distance_reference='vwap')
    assert stop['price'] == 9.93 and stop['reason'] == 'below_vwap_support_stop'
    stop = M.initial_stop(detector(100), {}, 100, 10, 9.99, .01, distance_reference='entry')
    assert stop['price'] == 9.9 and stop['reason'] == 'one_percent_entry_stop'


def test_bos_uses_confirmed_high_not_resistance_and_latches_for_vwap():
    state = {}
    high = pivot(10.5, 95, 99, 'resistance')
    assert not M.observe_bos(state, detector(100, high), 100, 10.4, True, True).get('open')
    gate = M.observe_bos(state, detector(100, high), 100.1, 10.51, True, True)
    assert gate['open'] and gate['broken_pivot'] == high
    assert M.observe_bos(state, detector(100, high), 100.2, 10.6, True, True)['open']
    # Learning of a high after price was already above it is not a break.
    state = {}
    M.observe_bos(state, detector(100, high), 100, 10.6, True, True)
    assert not M.observe_bos(state, detector(100, high), 100.1, 10.7, True, True).get('open')


def event(key, at):
    return dict(level=level(key, 10, 11), opened_at=at, threshold=10.5)


def test_fast_triples_are_strictly_less_than_three_seconds_disjoint_and_capped():
    active = {}
    assert not M.record_breaks(active, [event('a', 100), event('b', 101), event('c', 103)])
    upgrades = M.record_breaks(active, [event('d', 103.1)])
    assert upgrades[0]['multiplier'] == 8
    assert not M.record_breaks(active, [event('e', 103.2), event('f', 103.3)])
    assert M.record_breaks(active, [event('g', 103.4)])[0]['multiplier'] == 10
    assert not M.record_breaks(active, [event('h', 104), event('i', 104.1), event('j', 104.2)])
    assert active['target_multiplier'] == 10
    count = len(active['broken_levels'])
    M.record_breaks(active, [event('a', 105)])
    assert len(active['broken_levels']) == count


def test_stop_advances_one_level_per_three_and_never_between():
    active = dict(stop=9.8, broken_levels=['a', 'b'])
    rows = {'r1': level('r1', 10, 10.1), 'r2': level('r2', 10.2, 10.3)}
    assert M.next_stop(active, rows, .01)['price'] == 9.8
    active['broken_levels'].append('c')
    first = M.next_stop(active, rows, .01)
    assert first['price'] == 9.99 and first['steps'] == 1
    active.update(stop=first['price'], stop_steps=1, stop_anchor_lower=10)
    assert M.next_stop(active, rows, .01)['price'] == 9.99
    active['broken_levels'] += ['d', 'e', 'f']
    assert M.next_stop(active, rows, .01)['price'] == 10.19


def test_target_uses_each_actual_fill_price():
    assert M.target_price(10.03, .2, 5, .01) == 11.03
    assert M.target_price(10.27, .2, 8, .01) == 11.87
    assert M.target_price(10.27, .2, 10, .01) == 12.27


@pytest.mark.parametrize('kind', ['valid', 'above_open', 'late', 'moved'])
def test_addition_acceptance_is_same_candle_cross_and_immediate_next_second(kind):
    _, _, trade, one, _ = fixture()
    row = level('R', 10.4, 10.42)
    rows = {'R': row}
    state = {}
    M.observe_resistances(state, one(1, 10.39, .01, .005), rows, True, False)
    candle = replace(one(2, 10.44, .02, .005), bar_open=10.42 if kind == 'above_open' else 10.4)
    M.observe_resistances(state, candle, rows, True, False)
    if kind == 'moved':
        rows = deepcopy(rows)
        rows['R']['lower'] += .01
    events, _, _ = M.observe_resistances(state,
        trade(3.01 if kind == 'late' else 2.01, 10.44), rows, True, True)
    assert bool(events) is (kind == 'valid')


def momentum_fixture():
    h, a, trade, one, tenth = dual_macd_fixture()
    a = replace(a, parameters={**a.parameters, 'early_squeeze_breakout_contract':M.CONTRACT,
        'momentum_fallback_stop_percent':5})
    origin_time = trade().observed_at
    origin = origin_time.timestamp()
    def wrap(o):
        now = o.observed_at.timestamp()
        source = deepcopy(o.source_values)
        source[M.E.SIGNAL]['observed_at'] = origin_time.isoformat()
        return replace(o, source_values=source, structural_detector_state={**o.structural_detector_state,
            'momentum_session':dict(session=o.observed_at.astimezone(M.H.NY).date().isoformat(),
                open=10.39, high=max(10.39, o.price), prior_high=10.39, observed_at=now, complete=True, late=False),
            'row':detector(now, pivot(10.4, origin-2, origin-1, 'resistance'))})
    t = lambda *args, **kwargs: wrap(trade(*args, **kwargs))
    s = lambda *args, **kwargs: wrap(one(*args, **kwargs))
    f = lambda *args, **kwargs: wrap(tenth(*args, **kwargs))
    a = advance(a, h.evaluate(a, t()))
    for k in range(1, 17):
        a = advance(a, h.evaluate(a, s(k, 10.39, .01, .005)))
        a = advance(a, h.evaluate(a, f(k+.005, 10.39)))
        a = advance(a, h.evaluate(a, t(k+.01, 10.39)))
    return h, a, t, s, f


def test_real_engine_enters_on_bos_without_resistance_acceptance():
    h, a, t, _, _ = momentum_fixture()
    result = h.evaluate(a, t(16.02, 10.44))
    intent = next(i for i in result.evaluation.intents if i.action == 'enter_long')
    assert intent.reason == 'bos_vwap_momentum_entry'
    assert intent.capital_request.value == pytest.approx(1/3)
    assert intent.metadata['momentum_target']['average_gap'] == result.state['squeeze_breakout']['frozen_gap']['average']
    assert intent.metadata['cash_fraction_of_unreserved']
    assert not result.state['squeeze_breakout']['resistance_1s']['accepted']


def test_bos_waits_for_vwap_and_rechecks_macd_at_actual_purchase():
    h, a, t, _, f = momentum_fixture()
    o = t(16.02, 10.44)
    source = deepcopy(o.source_values)
    source['indicator.vwap.execution_value@100ms']['value'] = 10.5
    result = h.evaluate(a, replace(o, source_values=source))
    assert not result.evaluation.intents
    assert result.state['squeeze_breakout']['bos']['open']
    a = advance(a, result)
    a = advance(a, h.evaluate(a, f(16.03, 10.44, .0, .1)))
    result = h.evaluate(a, t(16.04, 10.45))
    assert not result.evaluation.intents
    a = advance(a, result)
    a = advance(a, h.evaluate(a, f(16.05, 10.44)))
    result = h.evaluate(a, t(16.06, 10.45))
    assert any(i.action == 'enter_long' for i in result.evaluation.intents)


def test_late_gate_starts_at_twenty_percent_and_latches():
    from datetime import datetime
    at = datetime(2026, 9, 21, 4, tzinfo=M.H.NY)
    d = M.observe_session({}, at, 10.)
    assert not M.observe_session(d, at, 11.99)['late']
    d = M.observe_session(d, at, 12.)
    assert d['late'] and M.observe_session(d, at, 10.)['late']
    h, a, t, _, _ = momentum_fixture()
    o = t(16.02, 10.44)
    market = deepcopy(o.structural_detector_state)
    market['momentum_session'].update(late=True, open=8.5, high=10.6, prior_high=10.6)
    result = h.evaluate(a, replace(o, structural_detector_state=market))
    assert not result.evaluation.intents
    assert result.evaluation.signals[0].reason == 'late_mode_below_hod_resistance_required'


def test_session_end_exit_is_independent_of_missing_indicators_and_no_choch_exit():
    from datetime import timedelta
    h, a, t, one, _ = momentum_fixture()
    a = advance(a, h.evaluate(a, t(16.02, 10.44)))
    a = replace(a, status=S.AssignmentStatus.MANAGING)
    a.state['squeeze_entry']['first_fill_at'] = t(16.02).observed_at.timestamp()
    o = t(16.03, 10.45, 100)
    o = replace(o, structure_event='bearish_choch', macd_line=-1, macd_signal=1)
    assert not any(i.action == 'exit' for i in h.evaluate(a, o).evaluation.intents)
    cutoff = o.observed_at.astimezone(M.H.NY).replace(hour=20, minute=0, second=0, microsecond=0)
    result = h.evaluate(a, replace(o, observed_at=cutoff, structural_detector_state={}, source_values={}))
    exit_order = next(i for i in result.evaluation.intents if i.action == 'exit')
    assert exit_order.reason == 'session_flatten'
    assert exit_order.metadata['exit_reason'] == 'session_flatten'


def test_purchase_entitlements_are_fill_owned_and_idempotent():
    state = dict(squeeze_breakout=dict(momentum_requests={'one':dict(filled=False, terminal=False)}))
    M.purchase_update(state, 'one', terminal=True)
    assert not state['squeeze_breakout']['momentum_requests']['one']['filled']
    M.purchase_update(state, 'one', filled=True)
    M.purchase_update(state, 'one', filled=True)
    assert state['squeeze_breakout']['momentum_requests']['one'] == dict(filled=True, terminal=True)


def test_actual_addition_gate_and_three_purchase_cap():
    h, a, t, one, fast = momentum_fixture()
    entry = h.evaluate(a, t(16.02, 10.44))
    a = replace(advance(a, entry), status=S.AssignmentStatus.MANAGING)
    request = next(i for i in entry.evaluation.intents if i.action == 'enter_long')
    M.purchase_update(a.state, request.intent_id, filled=True, terminal=True)
    a = advance(a, h.evaluate(a, replace(one(17, 10.64, .3, .1), bar_open=10.4, position_quantity=100.)))
    a = advance(a, h.evaluate(a, fast(17.005, 10.64, position=100.)))
    result = h.evaluate(a, t(17.01, 10.64, 100.))
    addition = next(i for i in result.evaluation.intents if i.action == 'add_long')
    assert addition.metadata['resistance_confirmation']['closed_at'] == t(17).observed_at.timestamp()
    assert addition.capital_request.value == pytest.approx(1/3)
    capped = deepcopy(a.state)
    requests = capped['squeeze_breakout']['momentum_requests']
    requests['second'] = dict(next(iter(requests.values())))
    requests['third'] = dict(next(iter(requests.values())))
    result = h.evaluate(replace(a, state=capped), t(17.01, 10.64, 100.))
    assert not any(i.action == 'add_long' for i in result.evaluation.intents)


def test_replay_cutoff_evaluates_without_inventing_market_event():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    from src.backend.replay_run_service import ReplayRunController
    _, a, t, _, _ = momentum_fixture()
    base = t()
    at = base.observed_at.astimezone(M.H.NY).replace(hour=20, minute=0, second=0, microsecond=0)
    run = SimpleNamespace(_runtime=object(), _strategy=SimpleNamespace(assignments=lambda:[a]),
        _flush_passive_market_events=Mock(), _latest_strategy_observations={a.ticker:base},
        _evaluate_strategy_observation=AsyncMock())
    asyncio.run(ReplayRunController._evaluate_momentum_session_cutoff(run, at))
    observation = run._evaluate_strategy_observation.call_args.args[0]
    assert observation.observed_at == at
    assert observation.evaluation_events == ('session_boundary',)
    assert not observation.changed_source_ids
    assert observation.source_values == base.source_values
    asyncio.run(ReplayRunController._evaluate_momentum_session_cutoff(run, at))
    assert run._evaluate_strategy_observation.call_count == 1


def test_replay_freezes_activation_snapshot_once_even_before_requested_start(tmp_path):
    import asyncio
    from datetime import time
    from unittest.mock import AsyncMock
    from src.backend.replay_run_service import ReplayRunController, ReplayRunDefinition, ReplaySignalEvent
    from tests.test_replay_run_service import approved_configuration
    _, assignment, trade, _, _ = momentum_fixture()
    observation = trade()
    configuration = approved_configuration()
    configuration['payload']['strategy']['parameters'] = assignment.parameters
    configuration['payload']['signal_activation'] = dict(signal_streams=[dict(signal_stream_id='price-squeeze-early',
        enabled=True, occurrence_source='qmd_squeeze_episode')])
    configuration['payload']['run_plan'] = dict(enabled=True, signal_stream_ids=['price-squeeze-early'],
        enablement=dict(state='enabled', scope='persistent'), watchlist_ids=[],
        activation=dict(event_policy='latest_session_occurrence', watchlist_policy='not_required', watch_duration='session'))
    run = ReplayRunController(ReplayRunDefinition(session_date=observation.observed_at.date(), start_time=time(9),
        tickers=(assignment.ticker,), configuration_revision=configuration), runtime_root=tmp_path)
    run._experimental_structure_snapshot = AsyncMock(return_value=dict(
        unified_levels=list(observation.structural_resistance_levels),
        as_of=observation.observed_at.timestamp(), max_input_timestamp=observation.observed_at.timestamp()))
    event = ReplaySignalEvent(available_at=observation.observed_at, ticker=assignment.ticker,
        occurrence=dict(ticker=assignment.ticker, signal_stream_id='price-squeeze-early', last_price=observation.price,
            effective_at=observation.observed_at.isoformat()),
        source_values={M.E.SIGNAL:dict(value=True, observed_at=observation.observed_at.isoformat())})
    asyncio.run(run._apply_external_signal_event(event))
    frozen = deepcopy(run._candle_detector_states[assignment.ticker]['structural_recovery']['momentum_activation'])
    asyncio.run(run._apply_external_signal_event(replace(event, occurrence={**event.occurrence, 'last_price':99.})))
    assert run._experimental_structure_snapshot.await_count == 1
    assert frozen['frozen_gap']['average'] > 0
    assert frozen == run._candle_detector_states[assignment.ticker]['structural_recovery']['momentum_activation']


def test_partial_target_keeps_other_tranches_but_stop_liquidates_remainder():
    import asyncio
    from types import SimpleNamespace
    h, a, t, _, _ = momentum_fixture()
    a = advance(a, h.evaluate(a, t(16.02, 10.44)))
    a = replace(a, status=S.AssignmentStatus.MANAGING)
    a.state['squeeze_entry']['first_fill_at'] = t(16.02).observed_at.timestamp()
    adapter = S.AssignedLongMomentumStrategy([a], revision=47)
    for role, expected in [('profit_target', S.AssignmentStatus.MANAGING), ('protective_stop', S.AssignmentStatus.EXIT_PENDING)]:
        asyncio.run(adapter.on_order_group_update(SimpleNamespace(assignment_id=a.assignment_id,
            action='exit', fill_role=role, fill_incremental_quantity=10., state='partially_filled',
            updated_at=t(16.03).observed_at), aggregate_position_quantity=90.))
        current = adapter.assignments()[0]
        assert current.status == expected
        assert bool(current.state.get('entry_acquisition_exit_latched')) == (role == 'protective_stop')
        if role == 'protective_stop':
            assert current.state['last_exit_reason'] == a.state['squeeze_entry']['stop_reason']
        if role == 'profit_target':
            assert not current.state.get('profit_target_liquidation_required')
            assert not any(i.action == 'exit' for i in h.evaluate(current, t(16.04, 10.45, 90.)).evaluation.intents)


def test_candidate_compiles_separate_contract_and_session_behavior(monkeypatch):
    from src.backend import early_squeeze_momentum_candidate as candidate
    from src.backend.trading_configuration_service import configuration_base, _build_configuration_release
    from tests.test_early_squeeze_candidate import baseline
    base = configuration_base()
    monkeypatch.setattr('src.backend.trading_configuration_service.configuration_base', lambda: deepcopy(base))
    payload, canvas, plan = candidate.build(base, baseline(base))
    profile = next(p for p in payload['strategy']['profiles'] if p['profile_id'] == M.CONTRACT)
    assert profile['parameters']['strategy_behavior']['flatten_time'] == '20:00:00'
    assert S.strategy_rule_timeframes(profile['parameters']) == {'100ms', '1s'}
    assert M.CONTRACT in S.supported_custom_execution_contracts()
    compiled, _, _ = _build_configuration_release(canvas_revision=canvas['revision'], canvas_profile=canvas['profile'],
        configuration=payload, run_plan_id=plan, strategy_profile_id=M.CONTRACT)
    compiled_profile = next(p for p in compiled['strategy']['profiles'] if p['profile_id'] == M.CONTRACT)
    assert S.resolve_long_momentum_parameters(compiled_profile['parameters'])['early_squeeze_breakout_contract'] == M.CONTRACT
    assert S.resolve_long_momentum_parameters(compiled_profile['parameters'])['momentum_fallback_stop_percent'] == 5
