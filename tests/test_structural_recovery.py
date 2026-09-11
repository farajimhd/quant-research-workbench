from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.market_engine.structural_detector_checkpoint import checkpoint, restore
from src.trading_runtime import structural_recovery as R
from src.trading_runtime import strategy_engine as S

NOW = datetime(2026,8,21,14,tzinfo=timezone.utc)
BOOK = dict(id='structure_book_123456789abc',fingerprint='test-fingerprint',version=R.BOOK_VERSION)


def level(side,low,high):
    return dict(unified_level_id=f'{side}:{low}',side=side,lower=low,upper=high,price=(low+high)/2,
        created_at_ms=NOW.timestamp()*1000-10000,confirmed_at_ms=NOW.timestamp()*1000-1000,
        book_version=R.BOOK_VERSION)


def parameters():
    p=S.default_long_momentum_parameters(revision=47)
    p.update(structural_recovery_contract=R.CONTRACT,structural_recovery=dict(R.DEFAULTS),
        liquidity_admission=dict(R.LIQUIDITY),
        strategy_behavior=dict(eligible_sessions=['regular'],entry_cutoff_time='15:45:00',flatten_time='15:55:00'),
        protection_profile_catalog={'structural-single-target':dict(profile_id='structural-single-target',
            slices=[dict(slice_id='position',quantity_fraction=1.,strategy_profit_target_index=0,
                stop={'rule_type':'fixed_price'},trailing={'rule_type':'none'})])})
    return S.resolve_long_momentum_parameters(p,revision=47)


def observation(index,opened,close,low=None,high=None,saved=None):
    t=NOW+timedelta(seconds=index)
    levels=[level(1,10.24,10.26),level(-1,11,11.02)]
    bar=dict(time=t.timestamp()-1,end=t.timestamp(),open=opened,close=close,
        low=low if low is not None else min(opened,close),high=high if high is not None else max(opened,close),volume=1000.)
    stream=R.observe_market(bar,levels,BOOK,saved or {})
    values={'market.session_dollar_volume':2e6,'market.volume':2e5,
        'market.trade_rate_10s':10.,'market.trade_rate_60s':10.,'market.spread_bps':10.}
    o=S.StrategyObservation(ticker='TEST',observed_at=t,price=close,bid=close-.005,ask=close+.005,
        bar_open=opened,bar_low=bar['low'],bar_high=bar['high'],source_timeframe='1s',evaluation_events=('bar_close',),
        source_values={k:dict(value=v,observed_at=t.isoformat()) for k,v in values.items()},
        structural_support_levels=(levels[0],),structural_resistance_levels=(levels[1],),
        structural_detector_state={k:v for k,v in stream.items() if k!='checkpoint'})
    return o,stream


def ready():
    a=S.StrategyAssignment('test',S.STRATEGY_ID,47,'sim','TEST',123,S.AssignmentStatus.WATCHING,
        S.StrategyPermissions(enter=True,reenter=True),parameters())
    host=S.LongMomentumStrategyEngine(revision=47)
    saved={}
    for i in range(30):
        o,saved=observation(i+1,10+i*.01,10+(i+1)*.01,saved=saved)
        r=host.evaluate(a,o);a=replace(a,state=r.state,status=r.status)
    o,saved=observation(31,10.30,10.27,low=10.25,high=10.30,saved=saved)
    r=host.evaluate(a,o);a=replace(a,state=r.state,status=r.status)
    assert not r.evaluation.intents
    o,saved=observation(32,10.27,10.34,low=10.27,high=10.34,saved=saved)
    return host,a,o,saved


def test_real_detector_to_protected_entry_without_macd():
    host,a,o,_=ready()
    r=host.evaluate(a,o)
    assert r.evaluation.signals[0].reason=='v6_support_recovery_confirmed'
    intent,=r.evaluation.intents
    assert intent.capital_request.mode=='risk_fraction'
    assert intent.capital_request.value==.005
    assert intent.invalidation_price < 10.24
    assert intent.profit_target_price==10.99
    assert intent.protection_profile is not None
    assert len(intent.protection_profile.slices)==1
    assert intent.resolved_execution_policy().envelope.maximum_buy_price >= o.ask
    assert not intent.resolved_execution_policy().envelope.persist_until_cancelled
    assert intent.resolved_execution_policy().envelope.deadline_ms==1000
    assert o.macd_line is None


def test_market_stream_matches_candle_checkpoints_across_resets_and_recovery():
    import math
    stream = R.MarketStream()
    saved = {}
    published = []
    for index in range(120):
        # Exercise oscillating swings, a missing candle and a book revision.
        end = NOW.timestamp() + index + 1 + (index >= 60)
        opening = 10 + math.sin(index / 4) * .2
        close = 10 + math.sin((index + 1) / 4) * .2
        bar = dict(time=end-1,end=end,open=opening,close=close,
            low=min(opening,close)-.01,high=max(opening,close)+.01,volume=1000+index)
        book = dict(BOOK,fingerprint='successor' if index >= 90 else BOOK['fingerprint'])
        levels = [level(1,9.85,9.87),level(-1,10.13,10.15)]
        saved = R.observe_market(bar,levels,book,saved)
        row = stream.observe(bar,levels,book)
        assert row == {k:v for k,v in saved.items() if k!='checkpoint'}
        assert stream.checkpoint() == saved
        published.append((row,deepcopy(row)))
        if index == 45:
            stream = R.MarketStream(json.loads(json.dumps(stream.checkpoint())))
        assert stream.observe(bar,levels,book) == row
    assert all(row == frozen for row,frozen in published)


def test_no_future_or_non_v6_authority_and_no_chased_entry():
    host,a,o,saved=ready()
    bad=deepcopy(o.structural_detector_state);bad['book']['version']='causal-swing-closing-book-5'
    assert not host.evaluate(a,replace(o,structural_detector_state=bad)).evaluation.intents
    future=deepcopy(o.structural_detector_state);future['row']['effective_at']+=1
    assert not host.evaluate(a,replace(o,structural_detector_state=future)).evaluation.intents
    assert not host.evaluate(a,replace(o,bid=10.40,ask=10.41,price=10.405)).evaluation.intents
    near=level(-1,10.38,10.40)
    assert not host.evaluate(a,replace(o,structural_resistance_levels=(near,))).evaluation.intents
    with pytest.raises(ValueError,match='V6'):
        R.observe_market(saved['row']['candle'],[],dict(BOOK,version='v5'),{})


def test_partial_position_keeps_stop_after_acquisition_cancelled():
    host,a,o,_=ready();first=host.evaluate(a,o)
    held=replace(a,state=first.state,status=S.AssignmentStatus.MANAGING)
    o=replace(o,position_quantity=20,average_price=10.34,observed_at=o.observed_at+timedelta(seconds=2))
    cancelled=host.evaluate(held,o)
    assert [i.action for i in cancelled.evaluation.intents]==['cancel_entry']
    held=replace(held,state=cancelled.state)
    stop=cancelled.state['active_stop']
    stopped=host.evaluate(held,replace(o,price=stop-.01))
    assert stopped.evaluation.intents[0].action=='exit'
    assert stopped.evaluation.intents[0].quantity==20


@pytest.mark.parametrize('key,value', [('market.volume',1),('market.session_dollar_volume',1),
    ('market.trade_rate_10s',1),('market.trade_rate_60s',1),('market.spread_bps',100)])
def test_each_tradability_gate_blocks_entry(key,value):
    host,a,o,_=ready();values=deepcopy(o.source_values);values[key]['value']=value
    r=host.evaluate(a,replace(o,source_values=values))
    assert not r.evaluation.intents
    assert r.evaluation.signals[0].reason=='structural_tradability_incomplete'


def test_stale_quote_crossed_quote_and_no_volume_fail_closed():
    host,a,o,_=ready()
    values=deepcopy(o.source_values);values['market.spread_bps']['observed_at']=(o.observed_at-timedelta(seconds=2)).isoformat()
    assert not host.evaluate(a,replace(o,source_values=values)).evaluation.intents
    assert not host.evaluate(a,replace(o,bid=o.ask+.01)).evaluation.intents
    market=deepcopy(o.structural_detector_state);market['row']['candle']['volume']=0
    assert not host.evaluate(a,replace(o,structural_detector_state=market)).evaluation.intents


def test_pending_exit_blocks_entry_and_same_confirmation_cannot_reenter():
    host,a,o,_=ready()
    assert not host.evaluate(a,replace(o,pending_exit_quantity=1)).evaluation.intents
    first=host.evaluate(a,o)
    flat=replace(a,state=first.state,status=S.AssignmentStatus.WATCHING)
    assert not host.evaluate(flat,o).evaluation.intents


def test_recovery_can_enter_on_improved_quote_before_confirmation_expires():
    host,a,o,_=ready()
    chased=replace(o,bid=10.39,ask=10.40)
    rejected=host.evaluate(a,chased)
    assert rejected.evaluation.signals[0].metadata['entry_quality']['failed']==['chase']
    assert 'chase' in rejected.evaluation.signals[0].metadata['reason_detail']
    watching=replace(a,state=rejected.state,status=rejected.status)
    improved=replace(o,observed_at=o.observed_at+timedelta(milliseconds=250),
        evaluation_events=('market_data_update',))
    accepted=host.evaluate(watching,improved)
    intent,=accepted.evaluation.intents
    assert intent.resolved_execution_policy().envelope.deadline_ms==750
    expired=replace(improved,observed_at=o.observed_at+timedelta(seconds=1))
    assert not host.evaluate(watching,expired).evaluation.intents


def test_activity_and_chart_preserve_structural_rejection_evidence(tmp_path):
    from src.backend.trading_runtime_service import strategy_activity_payload
    from src.backend.replay_run_service import _compact_strategy_chart_plan
    from src.trading_runtime.journal import TradingJournal
    host,a,o,_=ready()
    rejected=host.evaluate(a,replace(o,bid=10.39,ask=10.40)).evaluation.signals[0]
    journal=TradingJournal(tmp_path/'evidence.sqlite3')
    try:
        journal.append(run_id='recovery-evidence',category='strategy_decision',
            entity_type='signal',entity_id=rejected.signal_id,event_time=o.observed_at,
            payload=dict(ticker=o.ticker,strategy_id=a.strategy_id,action='wait',
                reason=rejected.reason,metadata=rejected.metadata))
        activity=strategy_activity_payload(journal=journal,run_id='recovery-evidence')
        gate=activity['rows'][0]['gate_snapshot']
        assert gate['structural_recovery']['entry_quality']['failed']==['chase']
        assert _compact_strategy_chart_plan(gate)['structural_recovery']==gate['structural_recovery']
    finally:
        journal.close()


def test_cancel_acquisition_on_deterioration_and_exit_on_support_failure():
    host,a,o,saved=ready();first=host.evaluate(a,o)
    pending=replace(a,state=first.state,status=S.AssignmentStatus.ENTRY_PENDING)
    poor=replace(o,observed_at=o.observed_at+timedelta(milliseconds=50),ask=o.bid+.50)
    cancelled=host.evaluate(pending,poor)
    assert [i.action for i in cancelled.evaluation.intents]==['cancel_entry']
    next_o,_=observation(33,10.34,10.23,low=10.22,high=10.34,saved=saved)
    held=replace(pending,status=S.AssignmentStatus.MANAGING)
    failed=host.evaluate(held,replace(next_o,position_quantity=50,average_price=10.34))
    assert failed.evaluation.intents[0].action=='exit'
    assert failed.evaluation.intents[0].reason=='v6_support_failed'


def test_checkpoint_json_roundtrip_and_gap_warmup():
    _,_,_,saved=ready()
    encoded=json.loads(json.dumps(saved,allow_nan=False))
    a,next_a=observation(33,10.34,10.40,saved=saved)
    b,next_b=observation(33,10.34,10.40,saved=encoded)
    assert next_a==next_b
    assert checkpoint(restore(encoded['checkpoint']))==encoded['checkpoint']
    _,gap=observation(35,10.4,10.41,saved=saved)
    assert gap['reset'] and gap['row']['sequence']==1


def test_replay_passive_stream_populates_independent_detector():
    import asyncio
    from src.backend.replay_run_service import ReplayRunController
    # Exercise the real adapter without launching a historical campaign.
    frame=SimpleNamespace(timeframe='1s',ticker='TEST',as_of=NOW+timedelta(seconds=1),
        bar={'open':10.,'high':10.1,'low':10.,'close':10.1,'volume':1000})
    fake=SimpleNamespace(definition=SimpleNamespace(configuration_revision={'payload':{'strategy':{'parameters':parameters()}}},
        experimental_structure_book=BOOK['id']),_candle_detector_states={},_structural_market_streams={},
        _experimental_structure_snapshot=AsyncMock(return_value={'unified_levels':[level(1,9.9,10.),level(-1,11.,11.1)]}))
    with patch('src.backend.experimental_structure_book.resolve',return_value=BOOK):
        asyncio.run(ReplayRunController._observe_episode_candle(fake,frame))
    assert fake._candle_detector_states['TEST']['structural_recovery']['row']['contract']=='structural-candle-detector-10'


def test_runtime_portfolio_and_oms_create_full_protected_order(tmp_path):
    import asyncio
    from tests import test_long_momentum_strategy as T
    from src.trading_runtime.journal import TradingJournal
    async def run():
        _,assigned,o,_=ready()
        journal=TradingJournal(tmp_path/'journal.sqlite3')
        broker=T.SimulatedBrokerAdapter(['sim'],mode=T.TradingMode.BACKTEST)
        runtime=T.TradingRuntime(T.RunConfig(mode=T.RunMode.BACKTEST,strategy_id=S.STRATEGY_ID,
            strategy_revision=47,account_ids=('sim',),anchor_date=NOW.date(),run_id='structural-recovery-test'),
            broker,S.AssignedLongMomentumStrategy([assigned]),journal,
            intent_planner=T.RuntimeIbkrStrategyOrderPlanner(
                {'TEST':T.InstrumentContract('ibkr:123',123,'TEST','STK','USD')},
                strategy_id=S.STRATEGY_ID,strategy_revision=47))
        try:
            await runtime.initialize()
            stamp=o.observed_at-timedelta(milliseconds=1)
            await broker.on_market_event(T.QuoteEvent(ask_exchange=11,ask_price=o.ask,ask_size=10000,
                bid_exchange=12,bid_price=o.bid,bid_size=10000,conditions=(),indicators=(),
                ingest_ts=stamp,raw={'conid':123},sequence=1,source='test',tape=3,ticker='TEST',ts=stamp))
            await runtime.process_strategy_observation(o)
            orders=await broker.live_orders()
            targets=[order for order in orders if order.orderType=='LMT' and order.parentId]
            stops=[order for order in orders if order.orderType=='STP']
            assert len(targets)==1,[(order.orderType,order.parentId) for order in orders]
            assert targets[0].price==10.99
            assert len(stops)==1
            assert not any(order.orderType.startswith('TRAIL') for order in orders)
        finally:
            journal.close()
    asyncio.run(run())


def test_candidate_creation_uses_metadata_and_preserves_idempotent_payload(tmp_path):
    from src.backend import trading_configuration_service as C
    from src.trading_runtime.journal import TradingJournal
    journal = TradingJournal(tmp_path/'candidates.sqlite3')
    try:
        with patch.object(C, 'trading_journal', return_value=journal), patch.object(
                C, '_build_configuration_release', return_value=({}, {'strategy': 'test'}, 'content-hash')), patch.object(
                journal, 'trading_configuration_candidates', side_effect=AssertionError('Must not load historical payloads')):
            args = dict(label='test', canvas_revision='test', canvas_profile={}, configuration={})
            first = C.create_test_candidate(**args)
            second = C.create_test_candidate(**args)
            assert first == second
            assert second['payload'] == {'strategy': 'test'}
            assert len(journal.trading_configuration_candidate_summaries()) == 1
    finally:
        journal.close()


def test_historical_launch_requires_matching_certified_v6_book():
    from datetime import time
    from src.backend.replay_run_service import ReplayRunDefinition, RunMode
    args = dict(session_date=NOW.date(), start_time=time(9,30), mode=RunMode.BACKTEST,
        tickers=('TEST',), configuration_revision={'revision_id':'test-candidate', 'payload':{'strategy':{'parameters':parameters()}}})
    with pytest.raises(ValueError, match='explicitly selected certified V6'):
        ReplayRunDefinition(**args)
    args['experimental_structure_book'] = BOOK['id']
    book = dict(BOOK, ticker='TEST', start=NOW.date().isoformat(), end=NOW.date().isoformat())
    with patch('src.backend.experimental_structure_book.resolve', return_value=book):
        assert ReplayRunDefinition(**args).experimental_structure_fingerprint == BOOK['fingerprint']
        with pytest.raises(ValueError, match='single covered ticker'):
            ReplayRunDefinition(**dict(args, tickers=('OTHER',)))
    with patch('src.backend.experimental_structure_book.resolve', return_value=dict(book, version='causal-swing-closing-book-5')):
        with pytest.raises(ValueError, match='swing book V6'):
            ReplayRunDefinition(**args)


def test_179_admission_latches_but_current_execution_gates_remain_live():
    _, _, o, _ = ready()
    p = parameters()
    p['liquidity_admission'] = dict(R.LIQUIDITY_181)
    row = o.structural_detector_state['row']
    state = {}
    values = deepcopy(o.source_values)
    values['market.trade_rate_10s']['value'] = 1.
    values['market.trade_rate_60s']['value'] = .5
    admitted = replace(o,source_values=values)
    passed, evidence = R.tradability(admitted,p,row,state)
    assert not passed and state['recovery_admission']
    assert 'current_trade_rate_10s' in evidence['failed']
    wider = replace(o,bid=o.price*.996,ask=o.price*1.004)
    passed, evidence = R.tradability(wider,p,row,state)
    assert passed  # 80 bps is valid only after the <=60 bps admission.
    assert not R.tradability(wider,p,row,{})[0]
    assert not R.tradability(replace(o,bid=o.price*.994,ask=o.price*1.006),p,row,state)[0]
    assert not R.tradability(admitted,p,row,state)[0]
    tomorrow = o.observed_at+timedelta(days=1)
    fresh = {k:dict(v,observed_at=tomorrow.isoformat()) for k,v in o.source_values.items()}
    assert not R.tradability(replace(wider,observed_at=tomorrow,source_values=fresh),p,
        dict(row,effective_at=tomorrow.timestamp()),state)[0]
    assert 'recovery_admission' not in state
