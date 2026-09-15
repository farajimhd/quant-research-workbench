from dataclasses import replace
from datetime import timedelta
import json
import pytest

from src.market_engine.events import QuoteEvent, TradeEvent
from src.trading_runtime.market_pressure import CONTRACT, DEFAULT_POLICY, PressureTracker, evaluate
from src.trading_runtime import strategy_engine as S
from tests.test_fixed_support_trail import configured, market
from tests.test_long_momentum_strategy import NOW, assignment


def quote(ms=0, bid=100., ask=100.1, bs=100., ass=100.):
    return QuoteEvent(ask_exchange=1, ask_price=ask, ask_size=ass, bid_exchange=1,
        bid_price=bid, bid_size=bs, conditions=(), indicators=(), ingest_ts=NOW,
        ts=NOW+timedelta(milliseconds=ms), ticker="TEST")


def trade(ms=0, price=100., size=100.):
    return TradeEvent(conditions=(), event_id=str(ms), exchange=1, ingest_ts=NOW,
        participant_ts=None, price=price, size=size, ts=NOW+timedelta(milliseconds=ms), ticker="TEST")


def evidence(time=NOW, adverse=False):
    return dict(contract=CONTRACT, observed_at=time.isoformat(), ready=True,
        fast=dict(trades=10, classified_fraction=.8, quote_ready=True,
            trade_imbalance=-.8 if adverse else .8, quote_imbalance=-.6 if adverse else .4,
            retreat_spreads=2 if adverse else 0, progress_spreads=-2 if adverse else 2))


def test_trade_classification_uses_preceding_quote_and_unknown_is_explicit():
    tracker=PressureTracker()
    tracker.observe(trade(0, 100.1))
    tracker.observe(quote(10))
    tracker.observe(trade(20, 100.1))
    tracker.observe(trade(30, 100.))
    tracker.observe(trade(40, 100.05))
    tracker.observe(quote(50, bs=150))
    snap=tracker.snapshot(NOW+timedelta(milliseconds=60))
    assert snap['fast']['classified_fraction']==.5
    assert snap['fast']['trade_imbalance']==0
    assert snap['fast']['quote_imbalance']>0
    assert not tracker.snapshot(NOW+timedelta(seconds=2))['ready']


def test_checkpoint_equivalence_bounded_storage_and_noncausal_rejection():
    tracker=PressureTracker()
    for i in range(100):
        tracker.observe(quote(i*100))
        tracker.observe(trade(i*100+1))
    restored=PressureTracker(json.loads(json.dumps(tracker.checkpoint())))
    at=NOW+timedelta(seconds=10)
    assert restored.snapshot(at)==tracker.snapshot(at)
    assert len(tracker.buckets)<=31
    tracker.observe(trade(1))
    assert tracker.rejected==1
    assert not tracker.snapshot(NOW)['ready']


def test_extended_windows_accumulate_volume_without_guessing_unknown_trades():
    tracker = PressureTracker(extra_windows={'window_2s': 2, 'window_5s': 5})
    for event in (quote(), trade(50, 100.1, 100), quote(1000), trade(1050, 100., 40),
                  quote(3000), trade(3050, 100.05, 20), quote(4000), trade(4050, 100.1, 50), quote(5000)):
        tracker.observe(event)
    snap = tracker.snapshot(NOW+timedelta(seconds=5), include_totals=True)
    assert snap['fast']['total_volume'] == 50
    assert snap['window_2s']['total_volume'] == 70
    assert snap['window_2s']['unknown_volume'] == 20
    wide = snap['window_5s']
    assert (wide['buy_volume'], wide['sell_volume'], wide['unknown_volume'], wide['signed_volume']) == (150, 40, 20, 110)
    assert wide['total_volume'] == wide['buy_volume'] + wide['sell_volume'] + wide['unknown_volume']
    assert wide['body_to_range'] == 0 and wide['close_location'] == 1
    assert wide['classified_fraction'] == 190/210


def test_extended_checkpoint_restores_window_identity_and_bounded_history():
    tracker = PressureTracker(extra_windows={'window_5s': 5})
    for i in range(100):
        tracker.observe(quote(i*100))
        tracker.observe(trade(i*100+1))
    restored = PressureTracker(json.loads(json.dumps(tracker.checkpoint())))
    for event in (quote(10000), trade(10001, 100.1)):
        tracker.observe(event)
        restored.observe(event)
    at = NOW+timedelta(milliseconds=10100)
    assert restored.snapshot(at, include_totals=True) == tracker.snapshot(at, include_totals=True)
    assert len(restored.buckets) <= 51
    with pytest.raises(ValueError, match='restoring'):
        PressureTracker(tracker.checkpoint(), extra_windows={'window_5s': 6})
    legacy = PressureTracker().checkpoint()
    assert 'extra_windows' not in legacy
    assert PressureTracker(legacy).checkpoint() == legacy


@pytest.mark.parametrize('windows', [{'fast': 2}, {'window_x': 0}, {'window_x': 61}, {'window_x': True}, {'window_x': 2.5}])
def test_invalid_research_windows_fail_closed(windows):
    with pytest.raises(ValueError, match='window'):
        PressureTracker(extra_windows=windows)


def test_pressure_requires_agreement_confirmation_and_freshness():
    state={}
    for ms, expected in ((0,False),(100,False),(201,True)):
        at=NOW+timedelta(milliseconds=ms)
        decision=evaluate(DEFAULT_POLICY,state,replace(market(), observed_at=at,market_pressure=evidence(at,True)))
        assert decision['exit_confirmed']==expected
    assert not evaluate(DEFAULT_POLICY,state,replace(market(),observed_at=NOW+timedelta(seconds=2),market_pressure=evidence(NOW,True)))['usable']
    weak=evidence(adverse=True); weak['fast']['quote_imbalance']=.2
    assert not evaluate(DEFAULT_POLICY,{},replace(market(),market_pressure=weak))['adverse']


def test_engine_entry_exit_reentry_and_pending_exit():
    engine=S.LongMomentumStrategyEngine(revision=47)
    p=configured();p['market_pressure']=DEFAULT_POLICY
    obs=replace(market(),market_pressure=evidence())
    entered=engine.evaluate(assignment(strategy_revision=47,parameters=p),obs)
    assert any(i.action=='enter_long' for i in entered.evaluation.intents)
    adverse=replace(obs,market_pressure=evidence(adverse=True))
    blocked=engine.evaluate(assignment(strategy_revision=47,parameters=p),adverse)
    assert not any(i.action=='enter_long' for i in blocked.evaluation.intents)
    missing=engine.evaluate(assignment(strategy_revision=47,parameters=p),market())
    assert not any(i.action=='enter_long' for i in missing.evaluation.intents)
    state=entered.state
    for ms in (100,200,301):
        at=NOW+timedelta(milliseconds=ms)
        pos=replace(adverse,observed_at=at,market_pressure=evidence(at,True),position_quantity=100,
                    average_price=obs.price,evaluation_events=('market_data_update',))
        result=engine.evaluate(assignment(strategy_revision=47,parameters=p,state=state,status=S.AssignmentStatus.MANAGING),pos)
        state=result.state
    assert any(i.action=='exit' and i.reason=='market_pressure_rejection' for i in result.evaluation.intents)
    assert state['pressure_exit_latched']
    gate_state={'pressure_exit_latched':True}
    adverse_result=engine.evaluate(assignment(strategy_revision=47,parameters=p,state=gate_state),adverse)
    assert not any(i.action=='enter_long' for i in adverse_result.evaluation.intents)
    recovered=engine.evaluate(assignment(strategy_revision=47,parameters=p,state=gate_state),obs)
    assert any(i.action=='enter_long' for i in recovered.evaluation.intents)
    assert not recovered.state.get('pressure_exit_latched')
    pending=engine.evaluate(assignment(strategy_revision=47,parameters=p,state=gate_state),replace(obs,pending_exit_quantity=10))
    assert not any(i.action=='enter_long' for i in pending.evaluation.intents)


def test_absorption_and_protective_stop_precedence():
    sample=evidence(adverse=True)
    sample['fast']['trade_imbalance']=.8
    decision=evaluate(DEFAULT_POLICY,{},replace(market(),market_pressure=sample))
    assert decision['reason']=='seller_absorption'
    engine=S.LongMomentumStrategyEngine(revision=47)
    p=configured();p['market_pressure']=DEFAULT_POLICY
    entered=engine.evaluate(assignment(strategy_revision=47,parameters=p),replace(market(),market_pressure=evidence()))
    stopped=engine.evaluate(assignment(strategy_revision=47,parameters=p,state=entered.state,status=S.AssignmentStatus.MANAGING),
        replace(market(101),position_quantity=100,average_price=103.3,market_pressure={}))
    assert any(i.action=='exit' and i.reason=='protective_stop' for i in stopped.evaluation.intents)
