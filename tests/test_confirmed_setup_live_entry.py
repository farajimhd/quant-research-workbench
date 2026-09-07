from dataclasses import replace
from datetime import timedelta

import pytest

from src.trading_runtime import strategy_engine as S
from tests.test_hod_resistance_entry_stop import policy, observation
from tests.test_long_momentum_strategy import NOW, assignment


def parameters():
    p = policy()
    p.update(completed_macd_setup=True, strict_green_entry=True, require_breakout_reset=True)
    p['structural_entry']['accept_live_price_above_entry_level'] = True
    p['momentum_management']['downside_loss_guard']['below_vwap'] = False
    return p


def evaluate(obs, state=None):
    return S.LongMomentumStrategyEngine(revision=47).evaluate(
        assignment(strategy_revision=47, parameters=parameters(), state=state or {}), obs)


def tick(price=102.3, opening=102.2):
    return replace(observation(price), observed_at=NOW+timedelta(milliseconds=200),
                   bar_open=opening, evaluation_events=('market_data_update',), source_timeframe='',
                   macd_line=.5, macd_signal=.1)


def sample(line=.2, signal=.1):
    return {'completed_entry_macd': {'observed_at': NOW.isoformat(), 'line': line,
                                    'signal': signal, 'histogram': line-signal}}


def enters(result):
    return any(i.action=='enter_long' for i in result.evaluation.intents)


def test_live_flicker_cannot_override_closed_macd_and_missing_setup_blocks():
    assert not enters(evaluate(tick(), sample(.1,.2)))
    assert not enters(evaluate(tick()))
    assert enters(evaluate(replace(tick(),macd_line=.1,macd_signal=.2), sample()))
    assert not enters(evaluate(replace(tick(),observed_at=NOW+timedelta(seconds=3)),sample()))


def test_closed_bar_records_setup_and_strict_green_excludes_flat():
    result=evaluate(replace(observation(102.1),macd_line=.2,macd_signal=.1))
    assert result.state['completed_entry_macd']['line']==.2
    assert not enters(evaluate(tick(opening=102.3),result.state))
    assert enters(evaluate(tick(),result.state))


def test_reentry_requires_reset_below_used_resistance():
    state={**sample(),'breakout_reset_required':{'price':102,'band_upper':102.2}}
    blocked=evaluate(tick(),state)
    assert not enters(blocked)
    assert any(s.reason=='waiting_for_resistance_breakout_reset' for s in blocked.evaluation.signals)
    reset=evaluate(tick(102.1,102),blocked.state)
    assert 'breakout_reset_required' not in reset.state
    assert enters(evaluate(tick(),reset.state))


@pytest.mark.parametrize('gain',[-1,1])
def test_macd_exit_only_on_completed_bar(gain):
    p=parameters();state={'entry_at':NOW.isoformat()}
    obs=replace(observation(),macd_line=.1,macd_signal=.2)
    assert S._matching_momentum_management_route(p,obs,state,gain_pct=gain,side='long')
    assert S._matching_momentum_management_route(p,replace(obs,evaluation_events=('market_data_update',)),state,gain_pct=gain,side='long') is None


def test_stop_remains_immediate_and_position_records_reset_level():
    state={**sample(), 'entry_at':NOW.isoformat(),'entry_reference_price':103,
           'initial_stop':102.4,'active_stop':102.4,'high_water_price':103,
           'last_entry_resistance':{'price':102,'band_upper':102.2}}
    result=evaluate(replace(tick(),position_quantity=10),state)
    assert any(s.reason=='protective_stop' for s in result.evaluation.signals)
    assert result.state['breakout_reset_required']['band_upper']==102.2


@pytest.mark.parametrize('old_id,old_price,old_upper,allowed', [
    ('older',101,101.2,True),
    ('102',101,101.2,False),
    ('older',103,103.2,False),
    ('older',102,102.2,False),
    ('',101,101.2,False),
])
def test_new_higher_resistance_can_enter_without_old_reset(old_id,old_price,old_upper,allowed):
    p=parameters();p['breakout_reset_allow_higher_resistance']=True
    state={**sample(),'breakout_reset_required':{'unified_level_id':old_id,'price':old_price,'band_upper':old_upper}}
    # Keep price above both levels, so the old lock has not reset.
    obs=tick(103.4,103.3)
    engine=S.LongMomentumStrategyEngine(revision=47)
    result=engine.evaluate(assignment(strategy_revision=47,parameters=p,state=state),obs)
    assert enters(result)==allowed
    assert result.state['breakout_reset_required']==state['breakout_reset_required']
    assert not enters(evaluate(obs,state))  # Candidate 80 retains its original policy.
    if allowed:
        assert not enters(engine.evaluate(assignment(strategy_revision=47,parameters=p,state=sample(.1,.2)|{'breakout_reset_required':state['breakout_reset_required']}),obs))
