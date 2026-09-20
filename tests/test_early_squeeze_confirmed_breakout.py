from copy import deepcopy
from dataclasses import replace

import pytest

from src.trading_runtime import early_squeeze_price as P, strategy_engine as S
from tests.test_early_squeeze_forming_episode import fixture as previous_fixture
from tests.test_vwap_resistance_ladder import advance


def fixture():
    h, a, trade, one, tenth = previous_fixture()
    a = replace(a, parameters={**a.parameters, 'early_squeeze_breakout_contract': P.CONFIRMED_BREAKOUT_CONTRACT})
    for o in [trade(), one(0, 10.39, 0., .001), one(1, 10.39, 0., .0008), tenth(1.01, 10.39)]:
        a = advance(a, h.evaluate(a, o))
    return h, a, trade, one, tenth


def test_real_engine_waits_for_completed_close_then_enters_with_proof():
    h, a, trade, _, tenth = fixture()
    crossed = h.evaluate(a, trade(1.02, 10.45))
    assert not crossed.evaluation.intents
    assert crossed.evaluation.signals[0].reason == 'waiting_for_completed_100ms_resistance_breakout'
    a = advance(a, crossed)
    a = advance(a, h.evaluate(a, tenth(1.1, 10.45)))
    result = h.evaluate(a, trade(1.11, 10.46))
    intent = next(i for i in result.evaluation.intents if i.action == 'enter_long')
    proof = intent.metadata['entry_confirmation']
    assert proof['close'] > proof['upper']
    assert proof['close'] > intent.metadata['breakout_boundary']['price']
    assert proof['closed_at'] < trade(1.11).observed_at.timestamp()
    assert intent.invalidation_price <= intent.reference_price - .1 + 1e-9
    assert intent.metadata['entry_selection']['unified_level_id'] == 'R2'


def band(key, lower, upper):
    return dict(unified_level_id=key, role='resistance', lower=lower, upper=upper)


def observe(d, rows, now, price, previous=None, *, closed=False, entries=1, hod=100.):
    P.observe_entry_breakout(d, rows, now=now, price=price, previous=previous, prior_hod=hod,
        entries=entries, price_event=not closed, closed_100ms=closed, structure_fresh=True)


@pytest.mark.parametrize('low,upper,price,next_mid', [
    (2.230277517, 2.261580823, 3.06, 3.190732808),  # ZNB
    (2.294631307, 2.304173806, 2.61, 2.660177959),  # MSS
])
def test_new_episode_never_selects_old_broken_band(low, upper, price, next_mid):
    rows = {'old': band('old', low, upper), 'top': band('top', next_mid-.02, next_mid+.02),
            'next': band('next', next_mid+.4, next_mid+.42)}
    d = dict(macd_1s=dict(open=True, episode_id=2), breakout_highs={'old': price+.5})
    observe(d, rows, 2, price, price-.01)
    observe(d, rows, 2.1, price, closed=True)
    assert d['entry_breakout']['anchor']['unified_level_id'] == 'top'
    assert not d['entry_breakout']['confirmation']


def test_axti_inside_band_cannot_confirm_even_above_gap_threshold():
    rows = {'top': band('top', 84.39156649, 84.74621206),
            'next': band('next', 85.07759776, 85.09759776)}
    d = dict(macd_1s=dict(open=True, episode_id=1))
    observe(d, rows, 1, 84.5, 84.4, entries=0, hod=84.7)
    observe(d, rows, 1.1, 84.64, closed=True, entries=0, hod=84.7)
    assert d['entry_breakout']['boundary']['price'] < 84.64
    assert not d['entry_breakout']['confirmation']
    observe(d, rows, 1.2, 84.75, closed=True, entries=0, hod=84.7)
    assert d['entry_breakout']['confirmation']['close'] == 84.75


@pytest.mark.parametrize('invalidate', ['inside', 'episode', 'geometry', 'higher_band', 'authority'])
def test_confirmation_is_invalidated(invalidate):
    rows = {'R': band('R', 10.4, 10.42), 'N': band('N', 10.6, 10.62), 'T': band('T', 10.8, 10.82)}
    d = dict(macd_1s=dict(open=True, episode_id=1))
    observe(d, rows, 1, 10.39, 10.38)
    observe(d, rows, 1.1, 10.45, closed=True)
    assert d['entry_breakout']['confirmation']
    if invalidate == 'inside':
        observe(d, rows, 1.2, 10.41, closed=True)
    elif invalidate == 'episode':
        d['macd_1s']['episode_id'] = 2
        observe(d, rows, 1.2, 10.45, 10.45)
    elif invalidate == 'geometry':
        rows['R']['upper'] = 10.43
        observe(d, rows, 1.2, 10.45, closed=True)
    elif invalidate == 'higher_band':
        observe(d, rows, 1.2, 10.61, 10.45)
        assert d['entry_breakout']['anchor']['unified_level_id'] == 'N'
    else:
        P.observe_entry_breakout(d, rows, now=1.2, price=10.45, previous=10.45, prior_hod=11.,
            entries=1, price_event=False, closed_100ms=True, structure_fresh=False)
    assert not (d.get('entry_breakout') or {}).get('confirmation')


def test_initial_already_above_band_needs_fresh_approach():
    rows = {'R': band('R', 10.4, 10.42), 'N': band('N', 10.6, 10.62)}
    d = dict(macd_1s=dict(open=True, episode_id=1))
    observe(d, rows, 1, 10.5, 10.49, entries=0, hod=10.5)
    observe(d, rows, 1.1, 10.51, closed=True, entries=0, hod=10.51)
    assert not d['entry_breakout']['confirmation']


def test_bearish_100ms_blocks_valid_confirmed_entry():
    h, a, trade, _, tenth = fixture()
    a = advance(a, h.evaluate(a, trade(1.02, 10.45)))
    a = advance(a, h.evaluate(a, tenth(1.1, 10.45, .1, .2)))
    result = h.evaluate(a, trade(1.11, 10.46))
    assert result.state['squeeze_breakout']['entry_breakout']['confirmation']
    assert not result.evaluation.intents
    assert result.evaluation.signals[0].reason == 'macd_100ms_line_above_signal_required'


def test_same_episode_review_high_and_target_continuity():
    h, a, trade, _, tenth = fixture()
    a = advance(a, h.evaluate(a, trade(1.02, 10.45)))
    a.state['entries'] = 1
    d = a.state['squeeze_breakout']
    d['last_entry_macd_episode'] = d['macd_1s']['episode_id']
    d['macd_1s']['broken_levels'] = list('ABCDE')
    a = replace(a, status=S.AssignmentStatus.WATCHING)
    a = advance(a, h.evaluate(a, trade(1.03, 10.45)))
    for t in (1.1, 1.2, 1.3):
        a = advance(a, h.evaluate(a, tenth(t, 10.45)))
    early = h.evaluate(a, trade(1.31, 10.46))
    assert not early.evaluation.intents
    assert early.evaluation.signals[0].reason == 'waiting_for_three_completed_100ms_reentry_candles'
    no_high = h.evaluate(a, trade(1.34, 10.45))
    assert no_high.evaluation.signals[0].reason == 'waiting_for_macd_1s_episode_high_break'
    result = h.evaluate(a, trade(1.34, 10.46))
    intent = next(i for i in result.evaluation.intents if i.action == 'enter_long')
    assert intent.metadata['profit_target_selection']['episode_resistance_breaks'] == 5
    assert intent.metadata['profit_target_selection']['ordinal'] == 2
    assert intent.invalidation_price == pytest.approx(10.39)
    assert intent.metadata['stop_selection']['unified_level_id'] == 'R2'


def test_candidate_compiles(monkeypatch):
    from src.backend import early_squeeze_confirmed_breakout_candidate as C
    from src.backend.trading_configuration_service import configuration_base, _build_configuration_release
    from tests.test_early_squeeze_candidate import baseline
    base = configuration_base()
    monkeypatch.setattr('src.backend.trading_configuration_service.configuration_base', lambda: deepcopy(base))
    payload, canvas, plan = C.build(base, baseline(base))
    _build_configuration_release(canvas_revision=canvas['revision'], canvas_profile=canvas['profile'],
        configuration=payload, run_plan_id=plan, strategy_profile_id=C.CONTRACT)
