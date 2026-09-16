import asyncio
import json
from dataclasses import replace

import pytest

from src.trading_runtime.trade_volume import TradeVolumeTracker,confirm
from src.trading_runtime import strategy_engine as S
from tests.test_market_pressure import quote,trade
from tests.test_quote_geometry import at
from tests.test_v7_setup import prepared


def observe(tracker,seconds,size,*,volume=True,price=True):
    tracker.observe(replace(trade(size=size),ts=at(seconds),
        raw=dict(volume_eligible=volume,price_eligible=price)))


def test_completed_volume_includes_volume_only_trades_and_excludes_current_second():
    tracker=TradeVolumeTracker()
    tracker.observe(replace(quote(),ts=at(0)))
    observe(tracker,10,110,price=False)
    observe(tracker,55,10)
    observe(tracker,59.999999,10,price=False)
    observe(tracker,59.999999,1000,volume=False)
    observe(tracker,60,99999)  # New incomplete second is not available to the gate.
    evidence=tracker.snapshot(at(60))
    assert evidence['ready'] and evidence['prior_55s']==110 and evidence['recent_5s']==20
    assert evidence['ratio']==2 and evidence['observed_trade_seconds']==3
    assert confirm(evidence,now=60,minimum_ratio=2)['passed']
    assert not confirm(evidence,now=60,minimum_ratio=2.01)['passed']
    restored=TradeVolumeTracker(json.loads(json.dumps(tracker.checkpoint())))
    assert restored.snapshot(at(60))==evidence
    observe(tracker,61,5);observe(restored,61,5)
    assert tracker.snapshot(at(62))==restored.snapshot(at(62))
    assert not confirm(evidence,now=61,minimum_ratio=.5)['passed']


def test_missing_flags_or_out_of_order_events_fail_closed_and_retention_is_bounded():
    tracker=TradeVolumeTracker();tracker.observe(replace(quote(),ts=at(0)))
    observe(tracker,20,100);observe(tracker,59,10)
    tracker.observe(replace(trade(),ts=at(59.5)))
    assert tracker.snapshot(at(60))['reason']=='volume_eligibility_or_size_invalid'
    observe(tracker,58,100)
    assert tracker.snapshot(at(60))['reason']=='out_of_order_history'
    assert tracker.snapshot(at(58))['reason']=='future_source'
    for second in range(60,200):observe(tracker,second,10)
    assert len(tracker.buckets)<=61 and tracker.snapshot(at(200))['ready']
    assert not TradeVolumeTracker().snapshot(at(200))['ready']


def volume_for(o,ratio):
    now=o.observed_at.timestamp();tracker=TradeVolumeTracker()
    tracker.observe(replace(quote(),ts=at(now-61)))
    observe(tracker,now-10,110)
    observe(tracker,now-2,10*ratio)
    return dict(trade_volume=tracker.snapshot(o.observed_at))


@pytest.mark.parametrize('ratio',[.49,.5,2,None])
def test_initial_entries_and_pending_orders_require_current_volume(ratio):
    host,a,obs=prepared();o=obs(2,10.02)
    a.parameters['historical_hod']['setup_minimum_volume_ratio']=.5
    o=replace(o,market_pressure=volume_for(o,ratio) if ratio is not None else {})
    result=host.evaluate(a,o);passed=ratio is not None and ratio>=.5
    assert any(i.action=='enter_long' for i in result.evaluation.intents)==passed
    assert result.evaluation.signals[0].metadata['setup_volume_confirmation']['passed']==passed
    if not passed:return
    assert result.state['historical_hod_entry']['setup']['volume_confirmation']['passed']
    a=replace(a,state=json.loads(json.dumps(result.state)),status=S.AssignmentStatus.ENTRY_PENDING)
    pending=replace(o,evaluation_events=('market_data_update',),market_pressure={})
    cancelled=host.evaluate(a,pending)
    assert any(i.action=='cancel_entry' for i in cancelled.evaluation.intents)
    partial=host.evaluate(a,replace(pending,position_quantity=10,average_price=10.02))
    assert any(i.action=='cancel_entry' for i in partial.evaluation.intents)
    assert not any(i.action in ('enter_long','add_long') for i in partial.evaluation.intents)


def test_default_off_and_existing_position_management_are_preserved():
    host,a,obs=prepared();entered=host.evaluate(a,obs(2,10.02))
    assert any(i.action=='enter_long' for i in entered.evaluation.intents)
    a=replace(a,state=entered.state,status=S.AssignmentStatus.MANAGING)
    a.parameters['historical_hod']['setup_minimum_volume_ratio']=.5
    managed=host.evaluate(a,replace(obs(3,10.19),position_quantity=100,average_price=10.02))
    assert any(i.action=='add_long' for i in managed.evaluation.intents)


@pytest.mark.parametrize('value',[True,-.1,float('nan'),float('inf'),'0.5'])
def test_volume_floor_configuration_rejects_invalid_values(value):
    from src.trading_runtime.historical_hod import configure
    _,a,_=prepared();a.parameters['historical_hod']['setup_minimum_volume_ratio']=value
    with pytest.raises(ValueError):configure(a.parameters)


def test_recipe_preserves_generic_volume_floor(tmp_path):
    from scripts.run_strategy_222_refinement import load_recipe
    path=tmp_path/'recipe.json'
    path.write_text(json.dumps(dict(parameters=dict(historical_hod=dict(setup_minimum_volume_ratio=.5)))))
    assert load_recipe(path)==dict(historical_hod=dict(setup_minimum_volume_ratio=.5))


def test_controller_collects_passively_and_restores_volume_history(tmp_path):
    from datetime import date,time
    from src.backend.replay_run_service import ReplayRunController,ReplayRunDefinition,RunMode
    from src.trading_runtime.journal import TradingJournal
    from tests.test_replay_run_service import approved_configuration
    async def check():
        config=approved_configuration()
        config['payload'].setdefault('strategy_profile',{}).setdefault('parameters',{}).setdefault('historical_hod',{})['setup_minimum_volume_ratio']=.5
        c=ReplayRunController(ReplayRunDefinition(session_date=date(2026,7,28),start_time=time(9,45),mode=RunMode.BACKTEST,
            tickers=('AAPL',),configuration_revision=config),runtime_root=tmp_path)
        assert c._trade_volume_enabled
        c._navigation_skip_to_target=True;c._navigation_prerequisite_action={'event_time':at(110).isoformat()}
        assert c._navigation_skip_boundary() is None and not await c._can_skip_to_navigation_target()
        c.run_dir.mkdir(parents=True);c._journal=TradingJournal(c.run_dir/'journal.sqlite3')
        try:
            await c._initialize_runtime(record_configuration=False,record_lifecycle=False)
            now=c.definition.requested_start.timestamp()
            await c._process_market_event(replace(quote(),ts=at(now-61),ticker='AAPL'),evaluate_strategy=False)
            for age,size in ((10,110),(2,20)):
                e=replace(trade(size=size),ts=at(now-age),ticker='AAPL',raw=dict(price_eligible=False,volume_eligible=True))
                await c._process_market_event(e,evaluate_strategy=False)
            c.current_time=at(now)
            snap=c._market_pressure_snapshot('AAPL',c.current_time)['trade_volume']
            assert snap['ratio']==2
            saved=json.loads(json.dumps(c._restart_checkpoint_state()));c._trade_volume_trackers={};c._resume_state=saved
            c._restore_restart_checkpoint()
            assert c._market_pressure_snapshot('AAPL',at(now))['trade_volume']==snap
            del saved['controller']['trade_volume_trackers']
            with pytest.raises(ValueError,match='requires trade volume checkpoint history'):c._restore_restart_checkpoint()
        finally:c._journal.close()
    asyncio.run(check())
