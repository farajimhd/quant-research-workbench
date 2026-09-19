from copy import deepcopy
from dataclasses import replace

import pytest

from src.trading_runtime import early_squeeze_price as P
from tests.test_early_squeeze_price_episode import dual_macd_fixture
from tests.test_vwap_resistance_ladder import advance


def fixture():
    h, a, trade, one, tenth = dual_macd_fixture()
    return h, replace(a, parameters={**a.parameters,
        'early_squeeze_breakout_contract': P.FORMING_EPISODE_CONTRACT}), trade, one, tenth


def test_forming_preview_is_causal_and_does_not_compound():
    _, _, trade, one, _ = fixture()
    state = {}
    P.forming_macd_1s(one(0, 10., 0., .01), state, False)
    P.forming_macd_1s(one(1, 10., 0., .008), state, False)
    first = P.forming_macd_1s(trade(1.1, 10.5), state, True)
    assert first['line'] > first['signal']
    P.forming_macd_1s(trade(1.2, 11.), state, True)
    repeat = P.forming_macd_1s(trade(1.3, 10.5), state, True)
    assert repeat['line'] == pytest.approx(first['line'])
    assert repeat['signal'] == pytest.approx(first['signal'])
    assert P.forming_macd_1s(trade(2.1, 10.5), state, True) == {}


def test_trade_enters_with_forming_bullish_before_completed_crossover():
    h, a, trade, one, tenth = fixture()
    for observation in [trade(), one(0, 10.39, 0., .01),
                        one(1, 10.39, 0., .008), tenth(1.01, 10.39)]:
        a = advance(a, h.evaluate(a, observation))
    result = h.evaluate(a, trade(1.02, 10.6))
    assert result.state['squeeze_breakout']['macd_1s']['open']
    assert any(i.action == 'enter_long' for i in result.evaluation.intents)
    assert result.state['active_stop'] <= 10.5


def test_bearish_100ms_still_blocks_forming_bullish_entry():
    h, a, trade, one, tenth = fixture()
    for observation in [trade(), one(0, 10.39, 0., .01),
                        one(1, 10.39, 0., .008), tenth(1.01, 10.39, .1, .2)]:
        a = advance(a, h.evaluate(a, observation))
    result = h.evaluate(a, trade(1.02, 10.6))
    assert result.evaluation.signals[0].reason == 'macd_100ms_line_above_signal_required'
    assert not result.evaluation.intents


@pytest.mark.parametrize('closes,exits', [
    ([10.49, 10.51, 10.49, 10.51, 10.49], True),
    ([10.49, 10.49, 10.51, 10.51, 10.49], True),
    ([10.49, 10.49, 10.49, 10.51, 10.51], False),
    ([10.49, 10.51, 10.61, 10.51, 10.49], True),
    ([10.49, 10.51, 10.61, 10.71, 10.81], False),
])
def test_five_second_midpoint_exit(closes, exits):
    active = {}
    rows = {'R': dict(unified_level_id='R', role='resistance', lower=10.4, upper=10.6)}
    for at, close in enumerate(closes, 1):
        P.observe_midpoint_chop(active, rows, at, close)
        if at < 5:
            assert not active.get('midpoint_chop_exit')
    assert bool(active.get('midpoint_chop_exit')) == exits


def test_candidate_compiles(monkeypatch):
    from src.backend import early_squeeze_forming_episode_candidate as C
    from src.backend.trading_configuration_service import configuration_base, _build_configuration_release
    from tests.test_early_squeeze_candidate import baseline
    base = configuration_base()
    monkeypatch.setattr('src.backend.trading_configuration_service.configuration_base', lambda: deepcopy(base))
    payload, canvas, plan = C.build(base, baseline(base))
    _build_configuration_release(canvas_revision=canvas['revision'], canvas_profile=canvas['profile'],
        configuration=payload, run_plan_id=plan, strategy_profile_id=C.CONTRACT)


def test_reentry_uses_current_lower_band_and_preserves_target_count():
    from src.trading_runtime import strategy_engine as S
    h, a, trade, one, tenth = fixture()
    a = advance(a, h.evaluate(a, trade()))
    at = trade().observed_at.timestamp()
    a.state.update(entries=1)
    a.state['squeeze_breakout'].update(
        completed_macd_1s=dict(at=at, line=.3, signal=.2, slow=10.),
        macd_1s=dict(open=True, available=True, episode_id=at, high=10.40,
                     broken_levels=['A', 'B', 'C', 'D', 'E']),
        last_entry_macd_episode=at, trade_hod=20.)
    a = replace(a, status=S.AssignmentStatus.WATCHING)
    a = advance(a, h.evaluate(a, trade(.01, 10.39)))
    for offset in (.1, .2, .3):
        a = advance(a, h.evaluate(a, tenth(offset, 10.39)))
    too_soon = h.evaluate(a, trade(.305, 10.44))
    assert too_soon.evaluation.signals[0].reason == 'waiting_for_three_completed_100ms_reentry_candles'
    result = h.evaluate(a, trade(.32, 10.44))
    intent = next(i for i in result.evaluation.intents if i.action == 'enter_long')
    assert intent.metadata['entry_selection']['unified_level_id'] == 'R2'
    assert intent.metadata['profit_target_selection']['ordinal'] == 2
    assert intent.metadata['profit_target_selection']['episode_resistance_breaks'] == 5
    assert intent.invalidation_price == pytest.approx(10.39)


def test_midpoint_exit_issues_real_liquidation_intent():
    from src.trading_runtime import strategy_engine as S
    h, a, trade, one, tenth = fixture()
    for observation in [trade(), one(0, 10.39, 0., .01), one(1, 10.39, 0., .008),
                        tenth(1.01, 10.39), trade(1.02, 10.6)]:
        a = advance(a, h.evaluate(a, observation))
    a = replace(a, status=S.AssignmentStatus.MANAGING)
    a.state['active_stop'] = 9.
    a.state['squeeze_entry'].update(trail_distance=2., peak_price=10.6)
    # R3 is 10.60--10.62; alternate completed closes across its midpoint.
    for offset, price in enumerate([10.605, 10.615, 10.605, 10.615, 10.605], 2):
        result = h.evaluate(a, replace(one(offset, price, .3, .2), position_quantity=100.))
        a = advance(a, result)
    assert any(i.action == 'exit' and i.reason == 'resistance_midpoint_chop_exit'
               for i in result.evaluation.intents)
    assert result.evaluation.intents[0].metadata['reentry_after_fill']
    partial = h.evaluate(a, replace(trade(6.1, 10.60, 50.), pending_exit_quantity=25.))
    assert partial.evaluation.intents[0].reason == 'complete_position_liquidation'
    assert partial.evaluation.intents[0].metadata['reentry_after_fill']
