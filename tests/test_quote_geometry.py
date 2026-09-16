import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from src.trading_runtime.quote_geometry import QuoteGeometryTracker, confirm_base
from src.trading_runtime import strategy_engine as S
from tests.test_market_pressure import quote, trade
from tests.test_v7_setup import prepared


def at(seconds):
    return datetime.fromtimestamp(seconds, UTC)


def observe(tracker, seconds, mid):
    tracker.observe(replace(quote(bid=mid-.005, ask=mid+.005), ts=at(seconds)))


def test_exact_base_excludes_current_candle_and_restart_matches():
    tracker=QuoteGeometryTracker()
    observe(tracker, 100, 10)
    observe(tracker, 104.999, 10.2)
    observe(tracker, 105, 100)  # Current entry candle starts here.
    base=dict(start=100,end=105,low=10,high=10.2)
    result=confirm_base(tracker.snapshot(at(106)),base,now=106,minimum_fraction=.5)
    assert result['passed'] and result['range_fraction']==pytest.approx(1)
    assert result['quote_count']==2 and result['quote_high']==10.2
    restored=QuoteGeometryTracker(json.loads(json.dumps(tracker.checkpoint())))
    assert restored.snapshot(at(106))==tracker.snapshot(at(106))
    observe(tracker, 106, 10.1); observe(restored, 106, 10.1)
    assert restored.snapshot(at(107))==tracker.snapshot(at(107))
    # No current-candle rescue, even with enormous midpoint progress.
    base['high']=11
    assert not confirm_base(tracker.snapshot(at(106)),base,now=106,minimum_fraction=.5)['passed']


def test_truncation_future_clock_invalid_quotes_and_bounded_memory():
    tracker=QuoteGeometryTracker()
    for second in range(100, 200):
        observe(tracker, second, 10+second/1000)
    assert len(tracker.buckets)<=65
    base=dict(start=100,end=130,low=10,high=11)
    assert confirm_base(tracker.snapshot(at(200)),base,now=200,minimum_fraction=.5)['reason']=='quote_history_truncated'
    assert confirm_base(tracker.snapshot(at(201)),base,now=200,minimum_fraction=.5)['reason']=='quote_history_clock_invalid'
    saved=tracker.checkpoint()
    observe(tracker, 110, 1000)
    assert tracker.rejected==1 and tracker.checkpoint()['buckets']==saved['buckets']
    tracker.observe(replace(quote(bid=10,ask=11,bs=0),ts=at(200)))
    assert tracker.checkpoint()['buckets']==saved['buckets'][1:]  # Retention advances; zero-size quote adds nothing.


def geometry_for(o, *, confirmed=True):
    now=o.observed_at.timestamp()
    tracker=QuoteGeometryTracker()
    observe(tracker, now-10, 10)
    observe(tracker, now-6, 9.99)
    observe(tracker, now-2, 10.1 if confirmed else 10.)
    tracker.observe(replace(trade(),ts=o.observed_at))
    return dict(quote_geometry=tracker.snapshot(o.observed_at))


@pytest.mark.parametrize('confirmed',[False,True,None])
def test_entry_guard_and_pending_use_frozen_base(confirmed):
    host,a,obs=prepared();o=obs(2,10.02)
    a.parameters['historical_hod']['setup_quote_confirmation_enabled']=1
    o=replace(o,market_pressure=geometry_for(o,confirmed=confirmed) if confirmed is not None else {})
    result=host.evaluate(a,o)
    assert any(i.action=='enter_long' for i in result.evaluation.intents)==bool(confirmed)
    evidence=result.evaluation.signals[0].metadata['setup_quote_confirmation']
    assert evidence['passed']==bool(confirmed)
    if not confirmed:
        assert 'historical_hod_entry' not in result.state
        return
    frozen=result.state['historical_hod_entry']['setup']['range']
    assert result.state['historical_hod_entry']['setup']['quote_confirmation']==evidence
    a=replace(a,state=json.loads(json.dumps(result.state)),status=S.AssignmentStatus.ENTRY_PENDING)
    a.state['pending_capital_request']=dict(request_id='test',requested_at=o.observed_at.isoformat())
    a.state['v7_setup']['range']=dict(start=0,end=1,low=1,high=1000)
    pending=replace(o,evaluation_events=('market_data_update',))
    retried=host.evaluate(a,pending)
    assert retried.evaluation.signals[0].metadata['setup_quote_confirmation']['base']==frozen
    assert any(i.action=='enter_long' for i in retried.evaluation.intents)
    invalid=host.evaluate(a,replace(pending,market_pressure={}))
    assert any(i.action=='cancel_entry' for i in invalid.evaluation.intents)
    assert 'pending_capital_request' not in invalid.state
    partially_filled=host.evaluate(a,replace(pending,market_pressure={},position_quantity=10,average_price=10.02))
    assert any(i.action=='cancel_entry' for i in partially_filled.evaluation.intents)
    assert not any(i.action in ('enter_long','add_long') for i in partially_filled.evaluation.intents)


def test_off_is_unchanged_and_existing_position_can_manage_without_history():
    host,a,obs=prepared();o=obs(2,10.02)
    result=host.evaluate(a,o)
    assert any(i.action=='enter_long' for i in result.evaluation.intents)
    assert 'setup_quote_confirmation' not in result.evaluation.signals[0].metadata
    a=replace(a,state=result.state,status=S.AssignmentStatus.MANAGING)
    a.parameters['historical_hod']['setup_quote_confirmation_enabled']=1
    managed=host.evaluate(a,replace(obs(3,10.19),position_quantity=100,average_price=10.02))
    assert any(i.action=='add_long' for i in managed.evaluation.intents)


@pytest.mark.parametrize('location',['strategy_profile','assignment'])
def test_replay_collects_geometry_independently_of_pressure_policy(tmp_path,location):
    from datetime import date,time
    from src.backend.replay_run_service import ReplayRunController,ReplayRunDefinition,RunMode
    from tests.test_replay_run_service import approved_configuration
    config=approved_configuration()
    for entry in [config['payload'].get('strategy_profile',{}),*config['payload'].get('assignments',[])]:
        entry.setdefault('parameters',{})['market_pressure']={'enabled':False}
        entry['parameters'].setdefault('historical_hod',{})['setup_reversal_enabled']=0
    if location=='strategy_profile':target=config['payload'].setdefault('strategy_profile',{})
    else:
        config['payload'].setdefault('assignments',[]).append({'parameters':{}})
        target=config['payload']['assignments'][-1]
    target.setdefault('parameters',{}).setdefault('historical_hod',{})['setup_quote_confirmation_enabled']=1
    controller=ReplayRunController(ReplayRunDefinition(session_date=date(2026,7,28),start_time=time(9,45),
        mode=RunMode.BACKTEST,configuration_revision=config),runtime_root=tmp_path)
    assert controller._quote_geometry_enabled and not controller._pressure_enabled
    import asyncio
    controller._navigation_skip_to_target=True
    controller._navigation_prerequisite_action={'event_time':at(110).isoformat()}
    assert not asyncio.run(controller._can_skip_to_navigation_target())
    assert controller._navigation_skip_boundary() is None
    controller._quote_geometry_enabled=False
    assert controller._navigation_skip_boundary()==at(110)
    controller._quote_geometry_enabled=True
    tracker=QuoteGeometryTracker();observe(tracker,100,10);observe(tracker,104,10.2)
    controller._quote_geometry_trackers['TEST']=tracker
    snapshot=controller._market_pressure_snapshot('TEST',at(106))
    assert not snapshot['ready']  # Pressure policy has no observations.
    assert confirm_base(snapshot['quote_geometry'],dict(start=100,end=105,low=10,high=10.2),
        now=106,minimum_fraction=.5)['passed']


def test_passive_market_collection_and_controller_checkpoint(tmp_path):
    import asyncio
    from datetime import date,time
    from src.backend.replay_run_service import ReplayRunController,ReplayRunDefinition,RunMode
    from src.trading_runtime.journal import TradingJournal
    from tests.test_replay_run_service import approved_configuration

    async def check():
        controller=ReplayRunController(ReplayRunDefinition(session_date=date(2026,7,28),start_time=time(9,45),
            mode=RunMode.BACKTEST,tickers=('AAPL',),configuration_revision=approved_configuration()),runtime_root=tmp_path)
        controller.run_dir.mkdir(parents=True)
        controller._journal=TradingJournal(controller.run_dir/'journal.sqlite3')
        try:
            await controller._initialize_runtime(record_configuration=False,record_lifecycle=False)
            controller._quote_geometry_enabled=True
            controller._pressure_enabled=False
            now=controller.definition.requested_start.timestamp()
            for seconds,mid in ((now,10),(now+4,10.2)):
                await controller._process_market_event(replace(quote(bid=mid-.005,ask=mid+.005),
                    ts=at(seconds),ticker='AAPL'),evaluate_strategy=False)
            controller.current_time=at(now+6)
            baseline=controller._market_pressure_snapshot('AAPL',controller.current_time)['quote_geometry']
            saved=json.loads(json.dumps(controller._restart_checkpoint_state()))
            assert 'AAPL' in saved['controller']['quote_geometry_trackers']
            controller._quote_geometry_trackers={}
            controller._resume_state=saved
            controller._restore_restart_checkpoint()
            assert controller._market_pressure_snapshot('AAPL',at(now+6))['quote_geometry']==baseline
            del saved['controller']['quote_geometry_trackers']
            with pytest.raises(ValueError,match='requires quote geometry checkpoint history'):
                controller._restore_restart_checkpoint()
        finally:
            controller._journal.close()
    asyncio.run(check())


@pytest.mark.parametrize('setting,value',[
    ('setup_quote_confirmation_enabled',True),('setup_quote_confirmation_enabled',2),
    ('setup_quote_range_minimum_fraction',0),('setup_quote_range_minimum_fraction',1.01),
    ('setup_quote_range_minimum_fraction',float('nan')),('setup_range_seconds',61),
])
def test_configuration_rejects_invalid_confirmation_settings(setting,value):
    from src.trading_runtime.historical_hod import configure
    _,a,_=prepared()
    a.parameters['historical_hod'].update(setup_quote_confirmation_enabled=1)
    a.parameters['historical_hod'][setting]=value
    with pytest.raises(ValueError):
        configure(a.parameters)
