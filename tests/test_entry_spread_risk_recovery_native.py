from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from uuid import UUID, NAMESPACE_URL, uuid5
import pytest
from test_entry_spread_risk_v2 import cost_source
from test_strategy_one_intent import _proposal
from src.backend.backtest_strategy_certified_price_break import CertifiedPriceReadbackAuthority, bind_certified_price_break_proposal
from src.backend.backtest_strategy_episode_activity_source import bind_episode_activity_proposal
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.trading_runtime.runtime import TradingRuntime, RunMode
from src.trading_runtime.arte_entry_spread_risk_v4 import project_entry_spread_risk, seal_certified_entry_spread_risk_rows


@pytest.mark.parametrize('number',[57,58])
def test_cost_readback_requires_activity_before_source_version_lookup(monkeypatch,number):
    episode,cost,_=cost_source(monkeypatch,number=number)
    with pytest.raises(ValueError,match='exact inherited activity parent'):
        CertifiedPriceReadbackAuthority(episode.run_id,episode.plan.parent,None,cost)


@pytest.mark.parametrize('number',[57,58])
def test_real_coordinator_rejects_cost_before_financial_lookup(monkeypatch,number):
    import asyncio
    from test_strategy_one_stateful import _facts
    from src.backend.backtest_strategy_one_scheduler import StrategyOneBoundaryScheduler
    from src.backend.backtest_strategy_one_coordinator import run_strategy_one_proposals
    from src.backend.backtest_entry_spread_risk import compile_entry_spread_risk_gate
    episode,cost,_=cost_source(monkeypatch,number=number,width=701)
    candidate,_,_,financial=_facts()
    later=replace(candidate,market_row={**candidate.market_row,'boundary_ms':41000},
        evidence=replace(candidate.evidence,boundary_ms=41000))
    calls=[]
    async def noop(*args):pass
    async def views(*args):
        calls.append('Portfolio')
        return (financial,)
    scheduler=StrategyOneBoundaryScheduler(session_date=episode.plan.market.sessions[0],
        candidate_rows=iter((candidate,later)),active_source=lambda *args:iter(()))
    counts=asyncio.run(run_strategy_one_proposals(scheduler,episode.plan.parent.entry,
        process_broker_boundary=noop,financial_views=views,on_entry_proposal=noop,on_management=noop,
        position_source_owned=lambda view:False,financially_active_tickers=lambda:(),finish_boundary=noop,
        observe_activation=noop,observe_completed_seconds=noop,strategy_number=number,
        momentum_plan=episode.plan.parent.momentum,initial_momentum_plan=episode.plan.parent,
        static_gate=compile_entry_spread_risk_gate(cost.plan),entry_spread_risk_source=cost))
    assert counts.entry_proposals==counts.candidate_decisions==0
    assert calls==[]


@pytest.mark.parametrize('number',[57,58])
def test_native_runtime_intent_and_independent_cold_graph_reproduce_quote_cost(monkeypatch,number):
    episode,cost,_=cost_source(monkeypatch,number=number)
    source=CertifiedPriceReadbackAuthority(episode.run_id,episode.plan.parent,episode,cost)
    day=date.fromisoformat(episode.plan.market.sessions[0])
    original=replace(_proposal(),strategy_number=18,boundary_ms=41000,
        momentum=episode.plan.parent.momentum.lookup('AAA',41000),
        initial_momentum=episode.plan.parent.source.parent.selection_witness('AAA',41000))
    proposal=bind_episode_activity_proposal(source,
        bind_certified_price_break_proposal(source.plan,original,strategy_number=36),session_date=day)
    runtime=SimpleNamespace(config=SimpleNamespace(mode=RunMode.BACKTEST,strategy_id='early-squeeze-strategy',strategy_revision=number,anchor_date=day),
        run_id=source.run_id,journal=BacktestMemoryJournal(run_id=source.run_id),_strategy_one_price_source=None)
    TradingRuntime.bind_strategy_one_price_source(runtime,source)
    intent=TradingRuntime._strategy_one_entry_intent(runtime,proposal)
    assert intent.capital_request.value == pytest.approx(1/3)
    record=runtime.journal.append_strategy_one_intent(intent=intent,proposal=proposal,session_date=day,
        account_id=proposal.account_id,strategy_id=runtime.config.strategy_id,strategy_revision=number,first_price_source=source)
    assert runtime.journal.strategy_one_entry_for_record(record.record_id)==(proposal,day)
    from src.backend.backtest_typed_projection import project_pending_backtest_v4_prefix
    from src.trading_runtime.arte_journal_writer import V4StrategyOneEntryBatch
    from src.trading_runtime.arte_journal_compound_v4 import coalesce_v4_units
    units=project_pending_backtest_v4_prefix(runtime.journal,
        attempt_id=str(UUID(int=100)),run_month=day.replace(day=1),prior_sequence=0,
        through_sequence=record.sequence,expected_config={'mode':'backtest','strategy_id':runtime.config.strategy_id,'strategy_revision':number},
        first_price_source=source)
    unit=next(unit for unit in units if type(unit) is V4StrategyOneEntryBatch)
    assert unit.entry_spread_risk_evidence[0]['ask_int']==100100
    with pytest.raises(ValueError):replace(unit,entry_spread_risk_evidence=())
    with pytest.raises(TypeError):unit.entry_spread_risk_evidence[0]['ask_int']=1
    next_batch=str(UUID(int=102))
    following=replace(unit.base,batch_id=next_batch,prior_batch_id=unit.base.batch_id,
        first_sequence=unit.base.last_sequence+1,last_sequence=unit.base.last_sequence+1,
        events=(dict(record_id=str(UUID(int=103)),run_id=source.run_id,batch_id=next_batch,
            sequence=unit.base.last_sequence+1,category='risk',entity_type='continuous_risk_state'),))
    compound=coalesce_v4_units((unit,following))
    assert compound.children['entry_spread_risk_evidence'][0]['source_plan_token']==cost.plan.token
    witness=cost.check_proposal(proposal)
    parent,batch=str(UUID(int=36)),str(UUID(int=37))
    month=intent.event_time.date().replace(day=1).isoformat()
    entry=dict(parent_record_id=parent,strategy_number=number,run_id=source.run_id,batch_id=batch,event_month=month,
        boundary_ms=proposal.boundary_ms,episode_start_ms=proposal.episode_start_ms,assignment_id=proposal.assignment_id)
    typed_intent=dict(record_id=parent,intent_id=intent.intent_id,run_id=source.run_id,batch_id=batch,event_month=month,
        ticker=proposal.ticker,account_id=proposal.account_id,action='enter_long',reason='strategy_one_entry',
        reference_price=str(intent.reference_price),invalidation_price=str(intent.invalidation_price))
    event=dict(record_id=parent,run_id=source.run_id,batch_id=batch,event_month=month,category='strategy',entity_type='strategy_intent',
        entity_id=intent.intent_id,account_id=proposal.account_id,event_time=intent.event_time.isoformat())
    row=project_entry_spread_risk(witness,run_id=source.run_id,batch_id=batch,parent_record_id=parent,event_month=month,strategy_number=number)
    def seal(rows=(row,),entry=entry,authority=cost):
        return seal_certified_entry_spread_risk_rows(rows,(entry,),(typed_intent,),(event,),run_id=source.run_id,source=authority)
    assert seal()[0]['ask_int']==100100
    for rows in [(),(dict(row,ask_int=row['ask_int']+1),),(row,row)]:
        with pytest.raises(ValueError):seal(rows)
    with pytest.raises(ValueError):seal(entry=dict(entry,strategy_number=50))
    with pytest.raises(ValueError):seal(entry=dict(entry,strategy_number=58 if number==57 else 57))
    with pytest.raises(ValueError):cost.check_proposal(replace(proposal,reference_ask=10.02))
    missing=replace(source,entry_spread_risk_source=None)
    with pytest.raises(ValueError):bind_episode_activity_proposal(missing,
        bind_certified_price_break_proposal(source.plan,original,strategy_number=36),session_date=day)

@pytest.mark.parametrize('number', [49, 51])
def test_entry_cost_extension_preserves_existing_ladder_terminal_route(monkeypatch, number):
    from test_backtest_v4_saved_review import Client, RUN, _context
    from src.backend import backtest_v4_saved_review as review
    from src.backend import backtest_market_data as markets
    from src.backend import backtest_strategy_one_configuration as configurations
    from src.backend import backtest_ladder_source_authority as ladders
    from src.backend.typed_backtest_review_core import AuditedSessionCache

    context = {**_context(), 'strategy_revision': number, 'configuration_hash': 'a' * 64}
    release = SimpleNamespace(payload_hash='a' * 64)
    market = SimpleNamespace(close=lambda: None)
    source = object()
    calls = []
    monkeypatch.setattr(review, 'load_typed_run_context', lambda *_: context)
    monkeypatch.setattr(markets, 'readonly_clickhouse_client', lambda **_: market)
    def certified(client, actual_number):
        assert client is market and actual_number == number
        calls.append('release')
        return release
    monkeypatch.setattr(configurations, 'certify_numbered_configuration', certified)
    monkeypatch.setattr(ladders, 'declared_ladder_policy', lambda actual: source if actual is release else None)
    def native(client, run):
        assert run == RUN
        calls.append('ladder_source')
        return source
    monkeypatch.setattr(ladders.DeclaredLadderSourceAuthority, 'from_run', native)
    def prefix(client, run, *, automatic_ladder_sources):
        assert run == RUN and automatic_ladder_sources is source
        calls.append('prefix')
        return None
    monkeypatch.setattr(review, 'load_verified_v4_prefix', prefix)
    with pytest.raises(ValueError, match='cold-verified terminal'):
        review._terminal_attestation(Client(), RUN, AuditedSessionCache())
    assert calls == ['release', 'ladder_source', 'prefix']

@pytest.mark.parametrize('number',[57,58])
def test_selected_profile_reaches_real_native_assembly(monkeypatch,number):
    from test_backtest_fixed_journal_bootstrap import test_selected_entry_cost_publication_reaches_real_assembly
    test_selected_entry_cost_publication_reaches_real_assembly(monkeypatch,number)


def recovery_graph(monkeypatch,number):
    episode,cost,_=cost_source(monkeypatch,number=number)
    source=CertifiedPriceReadbackAuthority(episode.run_id,episode.plan.parent,episode,cost)
    day=date.fromisoformat(episode.plan.market.sessions[0])
    original=replace(_proposal(),strategy_number=18,boundary_ms=41000,
        momentum=episode.plan.parent.momentum.lookup('AAA',41000),
        initial_momentum=episode.plan.parent.source.parent.selection_witness('AAA',41000))
    proposal=bind_episode_activity_proposal(source,
        bind_certified_price_break_proposal(source.plan,original,strategy_number=36),session_date=day)
    runtime=SimpleNamespace(config=SimpleNamespace(mode=RunMode.BACKTEST,strategy_id='early-squeeze-strategy',strategy_revision=number,anchor_date=day),
        run_id=source.run_id,journal=BacktestMemoryJournal(run_id=source.run_id),_strategy_one_price_source=None)
    TradingRuntime.bind_strategy_one_price_source(runtime,source)
    intent=TradingRuntime._strategy_one_entry_intent(runtime,proposal)
    record=runtime.journal.append_strategy_one_intent(intent=intent,proposal=proposal,session_date=day,
        account_id=proposal.account_id,strategy_id=runtime.config.strategy_id,strategy_revision=number,first_price_source=source)
    assert runtime.journal.strategy_one_entry_for_record(record.record_id)==(proposal,day)
    from src.backend.backtest_typed_projection import project_pending_backtest_v4_prefix
    from src.trading_runtime.arte_journal_writer import V4StrategyOneEntryBatch
    from src.trading_runtime.arte_journal_compound_v4 import coalesce_v4_units
    units=project_pending_backtest_v4_prefix(runtime.journal,
        attempt_id=str(UUID(int=100)),run_month=day.replace(day=1),prior_sequence=0,
        through_sequence=record.sequence,expected_config={'mode':'backtest','strategy_id':runtime.config.strategy_id,'strategy_revision':number},
        first_price_source=source)
    unit=next(unit for unit in units if type(unit) is V4StrategyOneEntryBatch)
    return proposal,intent,source,unit,record


@pytest.mark.parametrize('number,mutation',[(55,None),(56,None)]+[(n,m)for n in (57,58)for m in (None,'reference','stop','missing_stop','foreign_source','future_clock')])
def test_actual_entry_page_and_manager_attachment(monkeypatch,number,mutation):
    import struct
    from src.trading_runtime import arte_strategy_one_entry_journal as reader
    from src.trading_runtime.arte_intent_projection import RecoveredIntent
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
    from src.trading_runtime.arte_rising_momentum_entry_v4 import MOMENTUM,VALUES
    from src.trading_runtime.arte_initial_momentum_entry_v4 import INITIAL_MOMENTUM
    from src.trading_runtime.arte_first_price_entry_v4 import FIRST_PRICE
    from src.trading_runtime.arte_entry_activity_v4 import ENTRY_ACTIVITY
    from src.trading_runtime.arte_entry_spread_risk_v4 import ENTRY_SPREAD_RISK
    from src.backend.backtest_strategy_one_management import StrategyOneManagementState
    from src.trading_runtime.strategy_one_management_snapshot import attach_committed_momentum_sources
    proposal,intent,source,unit,record=recovery_graph(monkeypatch,number)
    def bits(rows):
        return tuple(dict(row,**{name+'_bits':None if row[name]is None else int.from_bytes(struct.pack('>d',row[name]),'big')for name in VALUES})for row in rows)
    tables={reader.ENTRY_EVIDENCE.name:unit.entry_evidence,MOMENTUM.name:bits(unit.momentum_evidence),
        INITIAL_MOMENTUM.name:bits(unit.initial_momentum_evidence),FIRST_PRICE.name:unit.first_price_evidence,
        ENTRY_ACTIVITY.name:unit.entry_activity_evidence,ENTRY_SPREAD_RISK.name:unit.entry_spread_risk_evidence}
    from src.trading_runtime.arte_journal_writer import typed_row
    from decimal import DecimalException
    tables[reader.ENTRY_EVIDENCE.name]=tuple(reader.seal_strategy_one_entry_evidence(dict(row))for row in unit.entry_evidence)
    tables[ENTRY_ACTIVITY.name]=tuple(typed_row(ENTRY_ACTIVITY.name,dict(row))for row in unit.entry_activity_evidence)
    tables[ENTRY_SPREAD_RISK.name]=tuple(typed_row(ENTRY_SPREAD_RISK.name,dict(row))for row in unit.entry_spread_risk_evidence)
    if mutation=='reference':intent=replace(intent,reference_price=intent.reference_price+.01)
    elif mutation=='stop':intent=replace(intent,invalidation_price=intent.invalidation_price-.01)
    elif mutation=='missing_stop':intent=replace(intent,invalidation_price=None)
    elif mutation=='foreign_source':
        foreign_episode,foreign_cost,_=cost_source(monkeypatch,number=55 if number==57 else 56)
        source=CertifiedPriceReadbackAuthority(source.run_id,foreign_episode.plan.parent,foreign_episode,foreign_cost)
    elif mutation=='future_clock':intent=replace(intent,event_time=intent.event_time.replace(microsecond=100000))
    recovered=RecoveredIntent(record.sequence,proposal.account_id,record.record_id,unit.base.batch_id,intent)
    prefix=V4CommittedPrefix(source.run_id,record.sequence,unit.base.batch_id,'cursor','running',(unit.base.batch_id,))
    queries=[]
    def rows(client,sql):
        assert 'LIMIT' in sql and 'parent_record_id IN' in sql
        queries.append(sql)
        matches=[v for name,v in tables.items()if 'FROM arte.'+name+' 'in sql]
        assert len(matches)==1
        return matches[0]
    # Replace external retrieval only. Native projections, all graph sealers,
    # independent source lookup, recovery reader and manager attachment run.
    monkeypatch.setattr(reader,'load_committed_strategy_intent_page',lambda *a,**k:(recovered,))
    monkeypatch.setattr(reader,'_rows',rows)
    reference=replace(proposal,momentum=None,initial_momentum=None,first_price=None,price_source_token=None)
    key=(proposal.account_id,proposal.assignment_id,proposal.ticker)
    state=StrategyOneManagementState(proposal.boundary_ms,((key,reference),),(),())
    if number in (55,56):
        with pytest.raises(KeyError,match='reference_price'):
            reader.load_committed_strategy_one_entry_page(None,prefix,first_price_source=source)
        return
    if mutation is None:
        page=reader.load_committed_strategy_one_entry_page(None,prefix,first_price_source=source)
        assert page.entries[0].proposal==proposal and page.entries[0].intent==intent
        assert len(queries)==6
        restored=attach_committed_momentum_sources(None,prefix,state,first_price_source=source)
        assert restored.submitted[0][1]==proposal
        assert reference.momentum is None and restored.submitted[0][1].momentum is not None
    else:
        with pytest.raises((ValueError,RuntimeError,DecimalException)):
            reader.load_committed_strategy_one_entry_page(None,prefix,first_price_source=source)
        with pytest.raises((ValueError,RuntimeError,DecimalException)):
            attach_committed_momentum_sources(None,prefix,state,first_price_source=source)
