import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.trading_runtime import hindsight_long as H, strategy_engine as S

NOW = datetime(2026, 8, 21, 14, 0, tzinfo=timezone.utc)


def fixture(**overrides):
    parameters = S.resolve_long_momentum_parameters(dict(
        hindsight_long_contract=H.CONTRACT, hindsight_long=overrides), revision=47)
    assignment = S.StrategyAssignment('hindsight-test', S.STRATEGY_ID, 47, 'sim', 'TEST', 123,
        S.AssignmentStatus.WATCHING, S.StrategyPermissions(enter=True, reenter=True), parameters)
    return assignment, observation()


def observation(seconds=0, price=10., bullish=True, quantity=0, **kwargs):
    at = NOW+timedelta(seconds=seconds)
    values = dict(ticker='TEST', observed_at=at, price=price, bid=price-.01, ask=price+.01,
        bar_low=price-.05, bar_high=price+.02, bar_open=price-.02,
        macd_line=.01 if bullish else -.01, macd_signal=0., source_timeframe='1s',
        evaluation_events=('bar_close',), position_quantity=quantity, average_price=10.01 if quantity else 0.,
        source_values={'market.spread_bps': dict(value=20., observed_at=at.isoformat())})
    values.update(kwargs)
    return S.StrategyObservation(**values)


def advance(assignment, obs):
    result = S.LongMomentumStrategyEngine(revision=47).evaluate(assignment, obs)
    return replace(assignment, state=result.state, status=result.status), result


def acquired():
    a, o = fixture()
    a, entry = advance(a, o)
    H.acquisition_update(a.state, terminal=True, filled=True)
    return replace(a, status=S.AssignmentStatus.MANAGING), entry


def test_entry_uses_current_ask_and_known_low_with_broker_stop():
    a, o = fixture()
    a, _ = advance(a, observation(-.1, bullish=False, source_timeframe='100ms', bar_low=9.9))
    _, result = advance(a, o)
    intent, = result.evaluation.intents
    assert intent.action == 'enter_long' and intent.reference_price == 10.01
    assert intent.invalidation_price == pytest.approx(9.89)
    assert intent.resolved_protection_profile().slices[0].stop.price == pytest.approx(9.89)
    assert intent.execution_policy.envelope.maximum_buy_price == 10.01
    assert not intent.execution_policy.envelope.persist_until_cancelled
    assert intent.profit_target_price is None


@pytest.mark.parametrize('updates', [
    dict(evaluation_events=('market_data_update',)), dict(source_timeframe='100ms'),
    dict(macd_line=None), dict(macd_line=0.), dict(macd_line=float('nan')),
    dict(source_values={}), dict(ask=11.), dict(bar_low=8.),
    dict(source_values={'market.spread_bps': dict(value=20, observed_at=(NOW+timedelta(seconds=1)).isoformat())}),
])
def test_no_entry_from_forming_invalid_stale_or_unprotected_input(updates):
    a, o = fixture()
    assert not advance(a, replace(o, **updates))[1].evaluation.intents


def test_negative_macd_can_start_long_and_quote_retry_is_time_bounded():
    a, o = fixture()
    a, r = advance(a, replace(o, macd_line=-.01, macd_signal=-.02, source_values={}))
    assert not r.evaluation.intents
    assert advance(a, observation(.2, evaluation_events=('market_data_update',)))[1].evaluation.intents
    assert not advance(a, observation(1.1, evaluation_events=('market_data_update',)))[1].evaluation.intents


def test_trailing_exit_is_causal_ratchets_and_episode_cannot_reenter():
    a, _ = acquired()
    a, _ = advance(a, observation(.1, quantity=100, evaluation_events=('market_data_update',)))
    a, r = advance(a, observation(.2, price=11., quantity=100, evaluation_events=('market_data_update',)))
    assert not r.evaluation.intents
    boundary = a.state['hindsight_long']['trailing_boundary']
    assert boundary == pytest.approx(10.745)
    a, r = advance(a, observation(.3, price=10.7, quantity=100, evaluation_events=('market_data_update',)))
    assert r.evaluation.intents[0].reason == 'peak_bid_pullback'
    assert r.evaluation.intents[0].quantity == 100
    a = replace(a, status=S.AssignmentStatus.REENTRY_COOLDOWN)
    a, r = advance(a, observation(.4))
    assert not r.evaluation.intents
    a, _ = advance(a, observation(1, bullish=False))
    _, r = advance(a, observation(2))
    assert r.evaluation.intents[0].action == 'enter_long'


def test_completed_episode_exit_ignores_stale_quote_but_forming_macd_does_not_exit():
    a, _ = acquired()
    _, r = advance(a, observation(.1, bullish=False, quantity=100, evaluation_events=('market_data_update',)))
    assert not r.evaluation.intents
    _, r = advance(a, observation(1, bullish=False, quantity=100, source_values={}))
    assert r.evaluation.intents[0].reason == 'macd_episode_closed'


def test_partial_acquisition_cancel_and_pending_exit_do_not_duplicate_orders():
    a, o = fixture()
    a, _ = advance(a, o)
    a, r = advance(replace(a, status=S.AssignmentStatus.MANAGING), observation(.6, quantity=20,
        evaluation_events=('market_data_update',)))
    assert r.evaluation.intents[0].action == 'cancel_entry'
    assert not a.state['hindsight_long']['acquisition_open']
    a, r = advance(a, observation(.7, quantity=20, evaluation_events=('market_data_update',)))
    assert not r.evaluation.intents
    a, r = advance(a, observation(1, bullish=False, quantity=20))
    assert r.evaluation.intents[0].action == 'exit'
    _, r = advance(a, observation(1.1, quantity=20, pending_exit_quantity=20))
    assert not r.evaluation.intents


def test_prefix_invariance_checkpoint_and_no_future_hindsight_inputs():
    a, _ = fixture()
    prefix = [observation(0), observation(.1, quantity=100), observation(.2, price=10.5, quantity=100)]
    for obs in prefix:
        a, _ = advance(a, obs)
    checkpoint = deepcopy(a)
    import json
    restored = replace(a, state=json.loads(json.dumps(a.state)))
    for suffix in (observation(.3, price=11., quantity=100), observation(.3, price=9., quantity=100)):
        first = advance(checkpoint, suffix)[1]
        second = advance(restored, suffix)[1]
        assert first.state == second.state
        assert first.evaluation.signals[0].reason == second.evaluation.signals[0].reason
    assert a == checkpoint
    _, old = advance(a, observation(-1, price=100.))
    assert not old.evaluation.intents and old.state == a.state


@pytest.mark.parametrize('key,value', [('quantity', 1.5), ('quantity', True), ('minimum_trail_bps', float('inf')),
    ('lookback_seconds', 31), ('profit_giveback_fraction', 1), ('entry_deadline_ms', 5000)])
def test_settings_fail_closed(key, value):
    with pytest.raises(ValueError):
        fixture(**{key: value})


def test_session_flatten_and_permissions():
    a, _ = acquired()
    _, r = advance(a, observation(36000, quantity=100))  # 20:00 New York
    assert r.evaluation.intents[0].reason == 'session_end'
    blocked = replace(a, permissions=replace(a.permissions, exit=False))
    assert not advance(blocked, observation(1, quantity=100, bullish=False))[1].evaluation.intents


def test_real_runtime_protection_roundtrip_and_reentry(tmp_path):
    from tests import test_long_momentum_strategy as T
    from src.trading_runtime.journal import TradingJournal

    async def run():
        a, o = fixture()
        journal = TradingJournal(tmp_path/'hindsight.sqlite3')
        broker = T.SimulatedBrokerAdapter(['sim'], mode=T.TradingMode.BACKTEST)
        strategy = S.AssignedLongMomentumStrategy([a])
        runtime = T.TradingRuntime(T.RunConfig(mode=T.RunMode.BACKTEST, strategy_id=S.STRATEGY_ID,
            strategy_revision=47, account_ids=('sim',), anchor_date=NOW.date(), run_id='hindsight'),
            broker, strategy, journal, intent_planner=T.RuntimeIbkrStrategyOrderPlanner(
                {'TEST': T.InstrumentContract('ibkr:123',123,'TEST','STK','USD')},
                strategy_id=S.STRATEGY_ID, strategy_revision=47))

        async def tick(seconds, price):
            at = NOW+timedelta(seconds=seconds)
            await runtime.process_event(T.QuoteEvent(ask_exchange=11, ask_price=price+.01, ask_size=10000,
                bid_exchange=12, bid_price=price-.01, bid_size=10000, conditions=(), indicators=(),
                ingest_ts=at, raw={'conid':123}, sequence=int(seconds*1000)+1, source='test',
                tape=3, ticker='TEST', ts=at), evaluate_strategy=False)

        try:
            account = runtime.portfolio.states['sim']
            account.profile = replace(account.profile, policy=replace(account.profile.policy, allow_outside_rth=True))
            await runtime.initialize()
            await tick(0, 10.)
            await runtime.process_strategy_observation(o)
            await tick(.01, 10.)
            assert strategy.assignments()[0].status == S.AssignmentStatus.MANAGING
            assert not strategy.assignments()[0].state['hindsight_long']['acquisition_open']
            stops = [order for order in await broker.live_orders() if order.orderType == 'STP']
            assert len(stops) == 1 and stops[0].auxPrice == pytest.approx(9.94)
            await runtime.process_strategy_observation(observation(1, bullish=False, quantity=100))
            await tick(1.01, 10.)
            assert all(float(p.position) == 0 for p in await broker.positions('sim'))
            assert strategy.assignments()[0].status == S.AssignmentStatus.REENTRY_COOLDOWN
            await tick(2, 10.)
            await runtime.process_strategy_observation(observation(2))
            await tick(2.01, 10.)
            assert strategy.assignments()[0].status == S.AssignmentStatus.MANAGING
            await tick(2.1, 9.8)  # broker protection, no strategy exit observation
            assert all(float(p.position) == 0 for p in await broker.positions('sim'))
            assert strategy.assignments()[0].status == S.AssignmentStatus.REENTRY_COOLDOWN
            assert not any(r.payload.get('event') == 'protection_repair_failed' for r in journal.records('hindsight'))
        finally:
            await runtime.finish()
            journal.close()
    asyncio.run(run())


def test_candidate_compiles_to_separate_backtest_policy(tmp_path):
    from tests.test_trading_configuration_service import TradingConfigurationServiceTests as T
    from src.backend.hindsight_long_candidate import build, PROFILE_ID
    from src.backend import trading_configuration_service as C
    from src.trading_runtime.journal import TradingJournal
    # Full configuration migration consults the registered QMD catalog even
    # for unrelated builtin discovery declarations. Do not fabricate authority.
    if not C._qmd_runtime_capabilities():
        pytest.skip('Full candidate compilation requires the QMD Live catalog; runtime order tests run offline')
    journal = TradingJournal(tmp_path/'candidate.sqlite3')
    try:
        base = T()._draft()
        original = deepcopy(base)
        base['canvas'] = dict(revision='test-canvas', profile={})
        payload, canvas, plan = build(base)
        with T._service_patches(journal):
            candidate = C.create_test_candidate(label='Hindsight test', canvas_revision=canvas['revision'],
                canvas_profile=canvas['profile'], configuration=payload, run_plan_id=plan,
                strategy_profile_id=PROFILE_ID)
            snapshot = C.backtest_configuration_snapshot(plan, candidate_id=candidate['candidate_id'])
            assert snapshot['payload']['strategy']['parameters']['hindsight_long_contract'] == H.CONTRACT
            assert S.strategy_rule_timeframes(snapshot['payload']['strategy']['parameters']) == {'100ms', '1s'}
            assert C.approved_configuration() is None
        assert base['strategy'] == original['strategy']
    finally:
        journal.close()


def test_candidate_builder_and_replay_require_only_its_causal_inputs():
    from src.backend.hindsight_long_candidate import build, PROFILE_ID
    from src.backend.replay_run_service import ReplayRunDefinition, _structural_recovery_projection_tickers
    from datetime import time
    base = dict(canvas=dict(revision='test', profile={}), market_discovery=dict(
        rule_sets=[], watchlists=[], signal_streams=[], core_scan={}),
        strategy=dict(profiles=[]), run_plans=dict(plans=[], universes=[]),
        accounts=dict(bindings=[dict(account_key='sim', modes=['backtest'])]),
        portfolio=dict(mandates=[dict(account_key='sim', mandate_id='base')]),
        oms=dict(profiles=[dict(profile_id='sim-oms')]))
    payload, _, plan = build(base)
    profile, = payload['strategy']['profiles']
    assert profile['profile_id'] == PROFILE_ID
    assert payload['run_plans']['plans'][0]['allowed_environments'] == ['backtest']
    assert payload['run_plans']['plans'][0]['run_plan_id'] == plan
    config = dict(strategy=dict(parameters=profile['parameters']))
    definition = ReplayRunDefinition(NOW.date(), time(4), tickers=('TEST',),
        configuration_revision=dict(revision_id='fixture', payload=config))
    assert definition.experimental_structure_book == ''
    assert _structural_recovery_projection_tickers(config, ('TEST',)) == ['TEST']


def test_replay_quote_updates_use_quote_clock_without_forming_macd():
    from src.backend.replay_run_service import ReplayRunController
    from src.market_engine.events import QuoteEvent
    async def run():
        a, base = fixture()
        controller = object.__new__(ReplayRunController)
        controller._runtime = object()
        controller._strategy = object()
        controller._strategy_engaged_tickers = {'TEST'}
        controller._latest_strategy_observations = {'TEST':base}
        controller._ticker_assignments = lambda ticker: (a,)
        controller._flush_passive_market_events = lambda: None
        observed = []
        async def consume(obs, assignments):
            observed.append(obs)
        controller._evaluate_strategy_observation = consume
        at = NOW+timedelta(milliseconds=100)
        event = QuoteEvent(ask_exchange=11, ask_price=10.5, ask_size=100,
            bid_exchange=12, bid_price=10.49, bid_size=100, conditions=(), indicators=(),
            ingest_ts=at, raw={}, sequence=1, source='test', tape=3, ticker='TEST', ts=at)
        assert await controller._process_strategy_market_event(event)
        obs, = observed
        assert obs.observed_at == at and obs.bid == 10.49
        assert obs.source_values['market.spread_bps']['observed_at'] == at.isoformat()
        assert 'bar_close' not in obs.evaluation_events
        assert not H.evaluate(a, obs).evaluation.intents
    asyncio.run(run())


def test_review_counts_missed_losing_unmatched_and_open_positions():
    from src.market_engine.hindsight_long_review import compare_positions
    from scripts.evaluate_hindsight_long import completed_positions
    def fill(at, side, quantity, price, fee):
        return SimpleNamespace(event_time=NOW+timedelta(seconds=at),
            payload=dict(side=side,size=quantity,price=price,commission=fee))
    positions, opened = completed_positions([
        fill(1,'B',20,10.,.1), fill(1.1,'B',80,10.1,.4),
        fill(3,'S',30,9.9,.2),fill(3.1,'S',70,9.8,.3),fill(6,'B',10,9.,.1)])
    assert len(positions) == 1 and opened['quantity'] == 10
    assert positions[0]['entry_price'] == pytest.approx(10.08)
    assert positions[0]['exit_price'] == pytest.approx(9.83)
    assert positions[0]['fees'] == 1
    epoch = NOW.timestamp()
    labels = [dict(direction='long',macd_open=epoch+i,macd_close=epoch+i+4,
        entry_time=epoch+i-.1,exit_time=epoch+i+3,entry_price=10.,exit_price=11.) for i in (0,5)]
    report = compare_positions(labels,positions)
    assert report['missed_episode_count'] == 1
    assert report['comparisons'][0]['attempts'][0]['net_pnl'] == pytest.approx(-26)
    assert report['comparisons'][0]['attempts'][0]['gross_capture_fraction'] < 0
    assert compare_positions([],positions)['unmatched_position_count'] == 1
    with pytest.raises(ValueError,match='Sell exceeds'):
        completed_positions([fill(0,'S',10,10.,.1)])


def test_oms_market_clock_is_not_freshened_by_a_later_bar():
    from src.trading_runtime.runtime import TradingRuntime
    from src.trading_runtime.execution_policies import ExecutionMarketDataProvider
    a, o = fixture()
    runtime = object.__new__(TradingRuntime)
    runtime.strategy = S.AssignedLongMomentumStrategy([a])
    runtime.execution_market_data = ExecutionMarketDataProvider()
    runtime.order_manager = None
    runtime._update_execution_market_from_observation(replace(o,observed_at=NOW+timedelta(seconds=20)))
    assert runtime.execution_market_data.snapshot('TEST').observed_at == NOW
    runtime._update_execution_market_from_observation(replace(o,source_values={},bid=99,ask=100))
    assert runtime.execution_market_data.snapshot('TEST').bid == o.bid


def test_flat_manual_exit_does_not_open_a_new_position():
    a, o = fixture()
    _, result = advance(replace(a,state={'manual_exit_requested':True}),o)
    assert not result.evaluation.intents
    assert result.evaluation.signals[0].reason == 'manual_exit_already_flat'


def test_fill_to_flat_releases_partial_acquisition_without_reentering_same_episode():
    async def run():
        a, _ = acquired()
        a.state['hindsight_long']['acquisition_open'] = True
        strategy = S.AssignedLongMomentumStrategy([a])
        await strategy.on_order_group_update(SimpleNamespace(
            assignment_id=a.assignment_id, action='exit', state='filled',
            fill_incremental_quantity=50., fill_role='protective_stop',
            reentry_after_fill=False, updated_at=NOW+timedelta(seconds=.5)),
            aggregate_position_quantity=0.)
        updated, = strategy.assignments()
        assert not updated.state['hindsight_long']['acquisition_open']
        assert updated.status == S.AssignmentStatus.REENTRY_COOLDOWN
        assert not (await strategy.on_observation(observation(.6), 'sim')).intents
        await strategy.on_observation(observation(1, bullish=False), 'sim')
        assert (await strategy.on_observation(observation(2), 'sim')).intents[0].action == 'enter_long'
    asyncio.run(run())


def test_review_accepts_ordered_fills_at_the_same_timestamp():
    from src.market_engine.hindsight_long_review import compare_positions
    result = compare_positions([], [dict(entry_time=1.,exit_time=1.,entry_price=10.,exit_price=9.99)])
    assert result['completed_position_count'] == 1
