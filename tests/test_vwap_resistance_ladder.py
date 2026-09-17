from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from src.trading_runtime import strategy_engine as S
from src.trading_runtime import vwap_resistance_ladder as V
from tests.test_v7_setup import prepared

NOW = datetime(2026, 8, 21, 8, 10, 30, tzinfo=timezone.utc)


def fixture():
    host, a, _ = prepared()
    p = deepcopy(a.parameters)
    p.update(vwap_ladder_contract=V.CONTRACT, vwap_ladder=dict(V.DEFAULTS))
    a = replace(a, parameters=p, state={})
    def observation(i=0, price=10., position=0., bearish=False):
        at = NOW+timedelta(seconds=i)
        sources = {}
        sources['market.spread_bps'] = dict(value=20., observed_at=at.isoformat())
        for tf, seconds in V.TIMEFRAMES.items():
            stamp = datetime.fromtimestamp(int(at.timestamp()/seconds)*seconds, timezone.utc)
            for name, value in [('line', -.1 if bearish and tf == '10s' else .1), ('signal', 0.)]:
                sources[f'indicator.macd.{name}@{tf}'] = dict(value=value, observed_at=stamp.isoformat())
        def level(key, low, side):
            return dict(unified_level_id=key, lower=low, upper=low+.02, price=low+.01,
                side=side, book_version='causal-level-book-v7-mle-1',
                input_policy=V.POLICY, seed_input_policy=V.POLICY,
                confirmed_at_ms=(NOW.timestamp()-60)*1000)
        return S.StrategyObservation(ticker=a.ticker, observed_at=at, price=price,
            bid=price-.01, ask=price+.01, execution_vwap=9., structural_session_high=10.5,
            source_values=sources, position_quantity=position, average_price=10.01 if position else 0.,
            source_timeframe='100ms', evaluation_events=('bar_close',),
            structural_support_levels=(level('support', 9.48, 1),),
            structural_resistance_levels=tuple(level(f'R{j}', 10+j*.2, -1) for j in range(1, 14)),
            structural_detector_state=dict(row=dict(local_swings=[dict(side='support', lower=9.48,
                upper=9.5, price=9.49, pivot_at=NOW.timestamp()-5, confirmed_at=NOW.timestamp()-3)])))
    return host, a, observation


def advance(a, result):
    return replace(a, state=deepcopy(result.state), status=result.status)


def entered():
    host, a, obs = fixture()
    result = host.evaluate(a, obs())
    assert result.evaluation.intents[0].action == 'enter_long'
    a = advance(a, result)
    a.state['vwap_ladder_entry']['slice_notional'] = 3000.
    return host, replace(a, status=S.AssignmentStatus.MANAGING), obs


def test_runnable_entry_uses_all_five_native_macds_and_third_resistance():
    host, a, obs = fixture()
    result = host.evaluate(a, obs())
    intent = result.evaluation.intents[0]
    assert intent.capital_request.value == pytest.approx(1/3)
    assert intent.invalidation_price == pytest.approx(9.47)
    assert intent.profit_target_price == pytest.approx(10.63)
    assert intent.metadata['unreserved_cash_slice']
    assert 'cash_tranche' not in intent.metadata
    assert not intent.resolved_execution_policy().envelope.persist_until_cancelled
    assert S.strategy_rule_timeframes(a.parameters) == set(V.TIMEFRAMES)


@pytest.mark.parametrize('tf', V.TIMEFRAMES)
@pytest.mark.parametrize('failure', ['missing', 'equal', 'stale', 'future'])
def test_macd_inputs_fail_closed(tf, failure):
    host, a, obs = fixture(); o = obs()
    key = f'indicator.macd.line@{tf}'
    if failure == 'missing': del o.source_values[key]
    elif failure == 'equal': o.source_values[key]['value'] = 0.
    else:
        at = o.observed_at+timedelta(seconds=30 if failure == 'future' else -60)
        o.source_values[key]['observed_at'] = at.isoformat()
    assert not host.evaluate(a, o).evaluation.intents


def test_two_additions_and_third_break_first_stop_advance_survive_restart():
    host, a, obs = entered()
    for index, price in enumerate([10.23, 10.43, 10.63, 10.83], 1):
        r = host.evaluate(a, obs(index, price, 300))
        actions = [i.action for i in r.evaluation.intents]
        assert ('add_long' in actions) == (index <= 2)
        assert ('replace_protective_stop' in actions) == (index >= 3)
        if index <= 2:
            add = next(i for i in r.evaluation.intents if i.action == 'add_long')
            assert add.capital_request.mode == 'fixed_notional' and add.capital_request.value == 3000
        if index >= 3:
            assert r.state['active_stop'] == pytest.approx(10+(index-2)*.2-.01)
        a = advance(a, r)
    assert len(a.state['vwap_ladder_market']['broken']) == 4
    for index, price in [(5, 10.75), (6, 10.83)]:
        r = host.evaluate(a, obs(index, price, 300)); a = advance(a, r)
    assert len(a.state['vwap_ladder_market']['broken']) == 4
    assert not any(i.action == 'add_long' for i in r.evaluation.intents)


def test_episode_is_not_reopened_by_flat_position_or_equality():
    host, a, obs = entered()
    a = replace(a, status=S.AssignmentStatus.WATCHING)
    assert not host.evaluate(a, obs(1)).evaluation.intents
    o = obs(10, bearish=True)
    a = advance(a, host.evaluate(a, o))
    assert host.evaluate(a, obs(20)).evaluation.intents[0].action == 'enter_long'


def test_bearish_episode_does_not_exit_existing_position_or_add():
    host, a, obs = entered()
    r = host.evaluate(a, obs(10, 10.23, 300, bearish=True))
    assert not any(i.action in ('exit', 'add_long') for i in r.evaluation.intents)


def test_support_must_exist_at_swing_and_midpoint_gate():
    host, a, obs = fixture()
    o = obs(price=9.7)
    assert not host.evaluate(a, o).evaluation.intents
    o = obs()
    o.structural_support_levels[0]['confirmed_at_ms'] = (NOW.timestamp()-1)*1000
    assert not host.evaluate(a, o).evaluation.intents


def test_late_entry_target_moves_only_twice_and_stop_stays_two_levels_behind():
    host, a, obs = fixture()
    market = {}
    V.observe_market(obs(price=9.8), market)
    for j in range(1, 7): V.observe_market(obs(j, 10+j*.2+.03), market)
    a.state['vwap_ladder_market'] = market
    r = host.evaluate(a, obs(7, 11.23)); a = advance(a, r)
    assert a.state['vwap_ladder_entry']['late']
    assert a.state['structural_profit_targets'] == pytest.approx([11.43])
    a = replace(a, status=S.AssignmentStatus.MANAGING)
    for j, price in enumerate([11.425, 11.625, 11.825], 8):
        r = host.evaluate(a, obs(j, price, 300))
        assert any(i.action == 'replace_profit_target' for i in r.evaluation.intents) == (j < 10)
        if j == 9:
            assert r.state['active_stop'] == pytest.approx(11.19)
        a = advance(a, r)
    assert a.state['vwap_ladder_entry']['target_moves'] == 2
    assert a.state['structural_profit_targets'] == pytest.approx([11.83])
    assert host.evaluate(a, obs(11, 11.84, 300)).evaluation.intents[0].action == 'exit'


def test_unfiltered_predecessor_book_blocks_entry():
    host, a, obs = fixture(); o = obs()
    o.structural_support_levels[0]['seed_input_policy'] = 'legacy-unfiltered'
    assert host.evaluate(a, o).evaluation.signals[0].reason == 'filtered_v7_seed_rebuild_required'


def test_approved_cash_cap_and_rejected_target_are_preserved_by_runtime_callbacks():
    import asyncio
    host, a, obs = entered()
    assigned = S.AssignedLongMomentumStrategy([a], revision=a.strategy_revision)
    from src.trading_runtime.signals import StrategyIntent
    funded = StrategyIntent('funded', a.ticker, NOW, 'enter_long', 300, 10.,
        metadata=dict(assignment_id=a.assignment_id, unreserved_cash_slice=True, unreserved_slice_notional=3000.))
    assigned.on_capital_request_funded(funded)
    a = assigned.assignments()[0]
    assert a.state['vwap_ladder_entry']['slice_notional'] == 3000.
    r = host.evaluate(a, obs(1, 10.23, 300)); a = advance(a, r)
    assigned.upsert_assignment(a)
    target = next(i for i in r.evaluation.intents if i.action == 'replace_profit_target')
    asyncio.run(assigned.on_intent_rejected(target, reasons=('broker_replacement_not_confirmed',), event_time=obs(1).observed_at))
    a = assigned.assignments()[0]
    assert a.state['structural_profit_targets'] == pytest.approx([10.63])
    retry = host.evaluate(a, obs(2, 10.24, 300))
    assert any(i.action == 'replace_profit_target' for i in retry.evaluation.intents)
    assert not any(i.action == 'add_long' for i in retry.evaluation.intents)


def test_unfilled_rejection_releases_episode_but_a_fill_does_not():
    import asyncio
    host, a, obs = fixture()
    r = host.evaluate(a, obs()); a = advance(a, r)
    assigned = S.AssignedLongMomentumStrategy([a], revision=a.strategy_revision)
    asyncio.run(assigned.on_intent_rejected(r.evaluation.intents[0], reasons=('no_cash',), event_time=NOW))
    assert not assigned.assignments()[0].state['vwap_ladder_episode']['used']
    a.state['vwap_ladder_entry']['first_fill_at'] = NOW.timestamp()
    V.release_unfilled_episode(a.state)
    assert a.state['vwap_ladder_episode']['used']


def test_stale_quote_blocks_entry_and_projected_points_use_actual_bands():
    host, a, obs = fixture(); o = obs()
    o.source_values['market.spread_bps']['observed_at'] = (NOW-timedelta(seconds=2)).isoformat()
    assert not host.evaluate(a, o).evaluation.intents
    row = obs().structural_resistance_levels[0]
    row.update(band_lower=row['lower'], band_upper=row['upper'], lower=row['price'], upper=row['price'])
    projected = V.levels(replace(obs(), structural_resistance_levels=(row,)))['R1']
    assert projected['lower'] == row['band_lower'] and projected['upper'] == row['band_upper']


def test_passive_history_cannot_erase_trade_breaks():
    host, a, obs = entered()
    a = advance(a, host.evaluate(a, obs(1, 10.23, 300)))
    passive = deepcopy(a.state['vwap_ladder_market'])
    passive.update(at=obs(2).observed_at.timestamp(), broken=[], break_rows={})
    o = obs(2, 10.21, 300)
    o.structural_detector_state['historical_hod_observation'] = dict(vwap_ladder_market=passive)
    r = host.evaluate(a, o)
    assert r.state['vwap_ladder_market']['broken'] == ['R1']
    assert not any(i.action == 'add_long' for i in r.evaluation.intents)
