from copy import deepcopy
from dataclasses import replace

import pytest

from src.trading_runtime import early_squeeze_price as P, strategy_engine as S
from tests.test_early_squeeze_confirmed_breakout import fixture as entry_fixture
from tests.test_vwap_resistance_ladder import advance


def test_prior_fourteen_true_ranges_exclude_current_candle():
    d = {}
    for t in range(1, 15):
        assert P.observe_chop_volatility(d, t, 10.05, 9.95, 10.) is None
    prior = P.observe_chop_volatility(d, 15, 12., 8., 10.)
    assert prior['value'] == pytest.approx(.1)
    assert prior['first_close_at'] == 1 and prior['last_close_at'] == 14
    assert len(d['chop_volatility_1s']['samples']) == 14
    assert P.observe_chop_volatility(d, 16, 10.05, 9.95, 10.)['value'] > .1


def test_true_range_includes_gap_from_previous_close():
    d = {}
    P.observe_chop_volatility(d, 1, 10.05, 9.95, 10.)
    P.observe_chop_volatility(d, 2, 10.25, 10.15, 10.2)
    assert d['chop_volatility_1s']['samples'][-1][1] == pytest.approx(.25)


@pytest.mark.parametrize('failure', ['gap', 'invalid', 'duplicate'])
def test_history_never_uses_missing_or_duplicate_bars(failure):
    d = {}
    for t in range(1, 15):
        P.observe_chop_volatility(d, t, 10.05, 9.95, 10.)
    before = deepcopy(d)
    if failure == 'duplicate':
        assert P.observe_chop_volatility(d, 14, 11., 9., 10.) is None
        assert d == before
    elif failure == 'gap':
        assert P.observe_chop_volatility(d, 16, 10.05, 9.95, 10.) is None
        assert len(d['chop_volatility_1s']['samples']) == 1
    else:
        assert P.observe_chop_volatility(d, 15, float('nan'), 9.95, 10.) is None
        assert d['chop_volatility_1s'] == {}


@pytest.mark.parametrize('final,volatility,exits', [
    (10.49, .1, False), (10.55, .1, False), (10.45, .1, False),
    (10.449, .1, True), (10.449, .2, False), (10.449, None, False),
])
def test_chop_requires_close_below_half_volatility_buffer(final, volatility, exits):
    active = {}
    rows = {'R': dict(unified_level_id='R', role='resistance', lower=10.4, upper=10.6)}
    for t, close in enumerate([10.49, 10.51, 10.49, 10.51, final], 1):
        P.observe_midpoint_chop(active, rows, t, close, volatility_gate=True,
            volatility=dict(value=volatility) if volatility is not None else None)
        if t < 5:
            assert not active.get('midpoint_chop_exit')
    assert bool(active.get('midpoint_chop_exit')) == exits
    if exits:
        assert active['midpoint_chop_exit']['volatility_filter']['k'] == .5


def test_deep_dip_without_oscillation_does_not_trigger_chop_exit():
    active = {}
    rows = {'R': dict(unified_level_id='R', role='resistance', lower=10.4, upper=10.6)}
    for t, close in enumerate([10.6, 10.55, 10.5, 10.45, 10.4], 1):
        P.observe_midpoint_chop(active, rows, t, close, volatility_gate=True, volatility=dict(value=.1))
    assert not active.get('midpoint_chop_exit')


@pytest.mark.parametrize('final,exits', [(10.605, False), (10.54, True)])
def test_real_engine_volatility_exit_and_partial_liquidation(final, exits):
    h, a, trade, one, tenth = entry_fixture()
    a = replace(a, parameters={**a.parameters, 'early_squeeze_breakout_contract': P.VOLATILITY_CHOP_CONTRACT})
    for o in [trade(1.02, 10.45), tenth(1.1, 10.45), trade(1.11, 10.46)]:
        a = advance(a, h.evaluate(a, o))
    assert a.state['entries'] == 1
    # Warm through the real flat observation path; no current-bar volatility.
    a = replace(a, status=S.AssignmentStatus.ENTRY_PENDING)
    for t in range(2, 16):
        o = replace(one(t, 10.61, .3, .2), bar_high=10.66, bar_low=10.56)
        a = advance(a, h.evaluate(a, o))
    a = replace(a, status=S.AssignmentStatus.MANAGING)
    a.state['active_stop'] = 9.
    a.state['squeeze_entry'].update(trail_distance=2., peak_price=10.61, structural_stop=9.)
    for t, close in enumerate([10.605, 10.615, 10.605, 10.615, final], 16):
        o = replace(one(t, close, .3, .2), position_quantity=100.,
            bar_high=max(10.66, close), bar_low=min(10.56, close))
        result = h.evaluate(a, o)
        a = advance(a, result)
    intents = [i for i in result.evaluation.intents if i.action == 'exit']
    assert bool(intents) == exits
    if exits:
        assert intents[0].reason == 'resistance_midpoint_chop_exit'
        proof = intents[0].metadata['resistance_chop']['volatility_filter']
        assert proof['volatility']['last_close_at'] < o.observed_at.timestamp()
        assert proof['exit_threshold'] == pytest.approx(10.56)
        assert intents[0].metadata['reentry_after_fill']
        partial = h.evaluate(a, replace(trade(20.1, 10.54, 50.), pending_exit_quantity=25.))
        assert partial.evaluation.intents[0].reason == 'complete_position_liquidation'
        assert partial.evaluation.intents[0].metadata['reentry_after_fill']


def test_candidate_compiles(monkeypatch):
    from src.backend import early_squeeze_volatility_chop_candidate as C
    from src.backend.trading_configuration_service import configuration_base, _build_configuration_release
    from tests.test_early_squeeze_candidate import baseline
    base = configuration_base()
    monkeypatch.setattr('src.backend.trading_configuration_service.configuration_base', lambda: deepcopy(base))
    payload, canvas, plan = C.build(base, baseline(base))
    _build_configuration_release(canvas_revision=canvas['revision'], canvas_profile=canvas['profile'],
        configuration=payload, run_plan_id=plan, strategy_profile_id=C.CONTRACT)
