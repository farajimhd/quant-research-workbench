from copy import deepcopy
from dataclasses import replace
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.trading_runtime import early_squeeze_price as P
from src.trading_runtime import strategy_engine as S
from tests.test_early_squeeze_confirmed_breakout import band, fixture
from tests.test_vwap_resistance_ladder import advance


def test_midpoint_confirmation_inside_upper_band_is_versioned():
    rows = {'R': band('R', 10.4, 10.6), 'N': band('N', 10.8, 10.82)}
    for midpoint_only in (False, True):
        d = dict(macd_1s=dict(open=True, episode_id=1))
        for now, price, closed in [(1., 10.45, False), (1.1, 10.55, True)]:
            P.observe_entry_breakout(d, rows, now=now, price=price, previous=10.4,
                prior_hod=10.6, entries=0, price_event=not closed, closed_100ms=closed,
                structure_fresh=True, midpoint_only=midpoint_only)
        assert bool(d['entry_breakout']['confirmation']) == midpoint_only


def test_next_ceiling_advances_only_with_green_close_proof():
    rows = {'R': band('R', 10.4, 10.42), 'N': band('N', 10.6, 10.62)}
    active = dict(anchor=rows['R'])
    assert P.next_resistance_ceiling(active, {}, rows, 10.3) == rows['R']
    active['green_resistance_closes'] = {'R': dict(upper=10.42, close=10.43)}
    assert P.next_resistance_ceiling(active, {}, rows, 10.3) == rows['N']
    active['green_resistance_closes']['N'] = dict(upper=10.62, close=10.62)
    assert P.next_resistance_ceiling(active, {}, rows, 10.3) == rows['N']
    active['green_resistance_closes']['N']['close'] = 10.63
    assert P.next_resistance_ceiling(active, {}, rows, 10.3) is None


@pytest.mark.parametrize('filled', [False, True])
def test_add_reservation_fill_and_episode_lifecycle(filled):
    state = dict(squeeze_breakout=dict(midpoint_add_requests={
        'request': dict(keys=['R'], episode_id=1, terminal=False, filled=False)}))
    assert not P.midpoint_add_available(state['squeeze_breakout'], 'R', 1)
    assert not P.midpoint_add_available(state['squeeze_breakout'], 'R', 2)
    P.update_midpoint_add(state, 'request', filled=filled)
    P.update_midpoint_add(state, 'request', terminal=True)
    P.update_midpoint_add(state, 'request', terminal=True)
    assert P.midpoint_add_available(state['squeeze_breakout'], 'R', 1) == (not filled)
    assert P.midpoint_add_available(state['squeeze_breakout'], 'R', 2)


def test_sparse_macd_reconstructs_actual_bar_ema_and_requires_prefix_for_old_base():
    _, _, trade, one, _ = fixture()
    state = {}
    slow, fast, signal = 10., 10.1, .05
    P.forming_macd_1s(one(0, 10., fast-slow, signal), state, False, sparse=True)
    price = 10.2
    slow = 2/27*price + 25/27*slow
    fast = 2/13*price + 11/13*fast
    signal = .2*(fast-slow)+.8*signal
    completed = one(3, price, fast-slow, signal)
    P.forming_macd_1s(completed, state, False, sparse=True)
    assert state['completed_macd_1s']['slow'] == pytest.approx(slow)
    o = trade(6, 10.3)
    assert not P.forming_macd_1s(o, state, True, sparse=True)
    proof = dict(authority='latest-completed-qmd-1s', as_of=o.observed_at.isoformat(),
        source_observed_at=completed.observed_at.isoformat(), line=fast-slow, signal=signal)
    o = replace(o, structural_detector_state={'macd_1s_evidence': proof})
    expected = 2/13*10.3+11/13*fast-(2/27*10.3+25/27*slow)
    before = deepcopy(state)
    assert P.forming_macd_1s(o, state, True, sparse=True)['line'] == pytest.approx(expected)
    assert state == before
    assert not P.forming_macd_1s(o, state, True)


def test_v21_real_engine_entry_contract():
    h, a, trade, _, tenth = fixture()
    a = replace(a, parameters={**a.parameters, 'early_squeeze_breakout_contract': P.MIDPOINT_EXECUTION_CONTRACT})
    for o in [trade(1.02, 10.45), tenth(1.1, 10.45)]:
        a = advance(a, h.evaluate(a, o))
    result = h.evaluate(a, trade(1.11, 10.46))
    intent = next(i for i in result.evaluation.intents if i.action == 'enter_long')
    assert intent.metadata['entry_confirmation']['threshold'] == intent.metadata['breakout_boundary']['price']


def test_real_engine_add_crossing_reserves_identity_and_does_not_repeat():
    h, a, trade, _, tenth = fixture()
    a = replace(a, parameters={**a.parameters, 'early_squeeze_breakout_contract': P.MIDPOINT_EXECUTION_CONTRACT})
    for o in [trade(1.02, 10.45), tenth(1.1, 10.45), trade(1.11, 10.46)]:
        a = advance(a, h.evaluate(a, o))
    a.state['squeeze_entry']['slice_notional'] = 1000.
    a = advance(a, h.evaluate(a, trade(1.12, 10.60, 100.)))
    result = h.evaluate(a, trade(1.13, 10.615, 100.))
    assert not any(i.action == 'add_long' for i in result.evaluation.intents)
    a = advance(a, result)
    result = h.evaluate(a, tenth(1.2, 10.615, position=100.))
    adds = [i for i in result.evaluation.intents if i.action == 'add_long']
    assert len(adds) == 1
    intent = adds[0]
    proof = intent.metadata['midpoint_add_crossing']
    assert proof['previous_price'] <= proof['midpoint'] < proof['price'] < proof['level']['upper']
    assert proof['timeframe'] == '100ms' and proof['closed_at'] > proof['previous_closed_at']
    assert intent.intent_id in result.state['squeeze_breakout']['midpoint_add_requests']
    a = advance(a, result)
    for offset, price in [(1.3, 10.60), (1.4, 10.615)]:
        result = h.evaluate(a, tenth(offset, price, position=100.))
        assert not any(i.action == 'add_long' for i in result.evaluation.intents)
        a = advance(a, result)


@pytest.mark.parametrize('filled', [0., 5.])
def test_real_adapter_partial_fill_then_cancel_preserves_consumption(filled):
    _, a, trade, _, _ = fixture()
    a = replace(a, parameters={**a.parameters, 'early_squeeze_breakout_contract': P.MIDPOINT_EXECUTION_CONTRACT})
    a.state['squeeze_breakout']['midpoint_add_requests'] = {
        'request': dict(keys=['R'], episode_id=1, terminal=False, filled=False)}
    adapter = S.AssignedLongMomentumStrategy([a], revision=47)
    for status in ['partially_filled', 'cancelled', 'cancelled']:
        snapshot = SimpleNamespace(assignment_id=a.assignment_id, state=status, action='add_long',
            fill_incremental_quantity=filled if status == 'partially_filled' else 0.,
            filled_quantity=filled, updated_at=trade(2).observed_at, intent_id='request')
        asyncio.run(adapter.on_order_group_update(snapshot, aggregate_position_quantity=100.+filled))
    current = adapter._assignments[(a.account_id, a.ticker.upper())].state
    assert P.midpoint_add_available(current['squeeze_breakout'], 'R', 1) == (filled == 0.)


def test_entry_closed_callback_releases_pending_reservation_for_next_episode():
    _, a, trade, _, _ = fixture()
    a = replace(a, parameters={**a.parameters, 'early_squeeze_breakout_contract': P.MIDPOINT_EXECUTION_CONTRACT})
    a.state['squeeze_breakout']['midpoint_add_requests'] = {
        'request': dict(keys=['R'], episode_id=1, terminal=False, filled=True)}
    adapter = S.AssignedLongMomentumStrategy([a], revision=47)
    snapshot = SimpleNamespace(assignment_id=a.assignment_id, state='partially_filled', action='add_long',
        fill_incremental_quantity=0., filled_quantity=5., entry_submission_closed=True,
        updated_at=trade(2).observed_at, intent_id='request')
    asyncio.run(adapter.on_order_group_update(snapshot, aggregate_position_quantity=105.))
    d = adapter._assignments[(a.account_id, a.ticker.upper())].state['squeeze_breakout']
    assert not P.midpoint_add_available(d, 'R', 1)
    assert P.midpoint_add_available(d, 'R', 2)


def test_bearish_100ms_close_cannot_add_and_later_bullish_above_is_not_new_cross():
    h, a, trade, _, tenth = fixture()
    a = replace(a, parameters={**a.parameters, 'early_squeeze_breakout_contract': P.MIDPOINT_EXECUTION_CONTRACT})
    for o in [trade(1.02, 10.45), tenth(1.1, 10.45), trade(1.11, 10.46)]:
        a = advance(a, h.evaluate(a, o))
    a.state['squeeze_entry']['slice_notional'] = 1000.
    for o in [tenth(1.2, 10.615, line=.1, signal=.2, position=100.),
              tenth(1.3, 10.616, line=.3, signal=.2, position=100.)]:
        result = h.evaluate(a, o)
        assert not any(i.action == 'add_long' for i in result.evaluation.intents)
        a = advance(a, result)


def test_runtime_forwards_entry_closure_without_releasing_campaign_reservation():
    from src.trading_runtime.runtime import TradingRuntime
    from src.trading_runtime.order_management import OrderManagementState
    assignment = SimpleNamespace(parameters={'early_squeeze_breakout_contract': P.MIDPOINT_EXECUTION_CONTRACT}, conid=1)
    handler, release = AsyncMock(), Mock()
    runtime = SimpleNamespace(_assignment_for_snapshot=lambda snapshot: assignment,
        control_plane=SimpleNamespace(campaigns=SimpleNamespace(release_reservation=release)),
        strategy=SimpleNamespace(on_order_group_update=handler),
        broker=SimpleNamespace(positions=AsyncMock(return_value=[])),
        _persist_strategy_assignments=Mock(), portfolio=SimpleNamespace(on_order_group_update=Mock()),
        risk_supervisor=SimpleNamespace(evaluate=AsyncMock()))
    snapshot = SimpleNamespace(state=OrderManagementState.PARTIALLY_FILLED, action='add_long',
        entry_submission_closed=True, account_id='a', updated_at=None,
        protection_required_quantity=0., protection_coverage_quantity=0., internal_reaction_ms=0.)
    asyncio.run(TradingRuntime._on_order_group_state(runtime, snapshot))
    handler.assert_awaited_once()
    release.assert_not_called()
