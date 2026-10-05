"""Frozen grid, original anchors, native intent and independent cold witnesses."""
from copy import deepcopy
from dataclasses import replace
from datetime import date
import importlib
import numpy as np
import pyarrow as pa
import pytest
from test_backtest_strategy_first_price_source import authority,Bars
from test_backtest_strategy_initial_momentum import Source
from test_strategy_one_intent import _proposal
from test_strategy_fifty_two_release import source_fixture,reseal
from test_strategy_thirty_three_configuration import APPROVAL
from src.backend.backtest_strategy_rising_momentum import load_rising_momentum_plan
from src.backend.backtest_declared_initial_momentum import compile_declared_initial_momentum_plan
from src.backend.backtest_strategy_first_price_source import load_first_price_source
from src.backend.backtest_strategy_certified_price_break import compile_certified_price_break_plan,compile_certified_price_static_gate,bind_certified_price_break_proposal,certified_price_entry_intent,CertifiedPriceReadbackAuthority
from src.trading_runtime.entry_momentum_growth import declared_momentum_policy,declared_initial_entry

GRID=[(59,'fifty_nine'),(60,'sixty'),(61,'sixty_one')]
class FractionSource(Source):
    def __init__(self,first=1.06,current=1.12,missing=False):
        super().__init__();self.first,self.current,self.omit_prior=first,current,missing
    def iter_arrow_record_batches(self,sql):
        for batch in super().iter_arrow_record_batches(sql):
            rows=[]
            for row in batch.to_pylist():
                if self.omit_prior and row['resolution_ms']==10000 and row['boundary_ms']==20000:continue
                if row['resolution_ms']==10000:row['macd_line']={20000:1.,30000:self.first,40000:self.current}[row['boundary_ms']]
                # Intentionally falling1s must not become a new gate.
                else:row['macd_line']=-float(row['boundary_ms'])
                rows.append(row)
            yield pa.RecordBatch.from_pylist(rows,schema=batch.schema)

def native(number,first=1.06,current=1.12,missing=False):
    market,old=authority()
    row=old.candidates.prepared[0];n=len(row.boundary_ms)
    prepared=replace(row,source_rows=max(n,row.source_rows),row_index=np.arange(n),macd_boundary_ms=np.repeat(row.macd_boundary_ms[:1],n,axis=0),stop_bar_boundary_ms=np.repeat(row.stop_bar_boundary_ms[:1],n),stop_low_int=np.repeat(row.stop_low_int[:1],n))
    candidates=replace(old.candidates,prepared=(prepared,))
    momentum=load_rising_momentum_plan(market,candidates,client=FractionSource(first,current,missing))
    parent=compile_declared_initial_momentum_plan(candidates,old.entry,momentum,declared_momentum_policy(number))
    plan=compile_certified_price_break_plan(load_first_price_source(market,parent,client=Bars()))
    return market,parent,plan

@pytest.mark.parametrize('number,name',GRID)
def test_exact50_parent_only_growth_declaration_changes(number,name):
    module=importlib.import_module('src.trading_runtime.strategy_'+name+'_release')
    source=source_fixture();before=deepcopy(source.payload)
    result=getattr(module,'derive_strategy_'+name+'_configuration')(source,**APPROVAL)
    assert source.payload==before
    payload=result['payload'];manifest=payload['strategy']['numbered_release']
    assert manifest['early_original_risk_failure_policy']==before['strategy']['numbered_release']['early_original_risk_failure_policy']
    assert 'entry_spread_risk_policy' not in manifest and 'armed_profit_floor_policy' not in manifest
    for key in before.keys()-{'strategy','strategy_profile','run_plan'}:assert payload[key]==before[key]
    for key in before['strategy'].keys()-{'strategy_number','revision','name','profile_id','profile_revision','numbered_release'}:assert payload['strategy'][key]==before['strategy'][key]
    assert manifest['entry_momentum_growth_policy']==declared_momentum_policy(number).payload()
    changed=deepcopy(payload['strategy']);changed['numbered_release']['entry_momentum_growth_policy']['current_fraction']=[1,100];reseal(changed['numbered_release'])
    with pytest.raises(ValueError):getattr(module,'verify_prepared_strategy_'+name+'_manifest')(changed)
    with pytest.raises(ValueError):getattr(module,'derive_strategy_'+name+'_configuration')(replace(source,payload_hash='f'*64),**APPROVAL)
    cert=importlib.import_module('src.backend.backtest_strategy_'+name+'_certification')
    assert len(getattr(cert,'certify_strategy_'+name+'_source')()) == 64
    from pipelines.strategy_one.configuration_publisher import _verified_numbered_envelope
    assert _verified_numbered_envelope(result)[0]==payload

@pytest.mark.parametrize('number,first,current,expected',[(59,1.2,1.27,[True,True]),(60,1.06,1.2,[False,True]),(61,1.06,1.12,[True,True]),(59,1.06,1.2,[False,False]),(60,1.2,1.27,[True,False])])
def test_unequal_masks_freeze_original_first_before_pruning(number,first,current,expected):
    _,parent,plan=native(number,first,current)
    assert parent.first_indices.tolist()==[0,0]
    assert parent.eligible_mask.tolist()==expected
    assert compile_certified_price_static_gate(plan).eligible_indices.tolist()==np.flatnonzero(expected).tolist()
    for index,allowed in enumerate(expected):
        now=parent.momentum.lookup('AAA',(31000,41000)[index]);anchor=replace(parent.lookup('AAA',(31000,41000)[index]) if allowed else __import__('src.trading_runtime.strategy_initial_strong_momentum',fromlist=['InitialStrongMomentumWitness']).InitialStrongMomentumWitness(30000,parent.momentum.lookup('AAA',31000)))
        assert declared_initial_entry(now,anchor,parent.policy)==allowed
    with pytest.raises(ValueError):parent.first_indices.setflags(write=True)


def test_missing_original_anchor_rejects_without_shifting_to_later_strong():
    _,parent,_=native(61,missing=True)
    assert parent.first_indices.tolist()==[0,0] and parent.eligible_mask.tolist()==[False,False]
    with pytest.raises(ValueError):parent.lookup('AAA',41000)


def test_native_intent_and_typed_cold_policy_reproduction():
    from src.trading_runtime.arte_initial_momentum_entry_v4 import project_initial_momentum_entry,restore_initial_momentum
    from src.trading_runtime.arte_first_price_entry_v4 import project_first_price_entry,restore_first_price_entry
    _,parent,plan=native(61)
    proposal=replace(_proposal(),strategy_number=61,boundary_ms=41000,episode_start_ms=30000,
      momentum=parent.momentum.lookup('AAA',41000),initial_momentum=parent.selection_witness('AAA',41000))
    bound=bind_certified_price_break_proposal(plan,proposal,strategy_number=61)
    intent=certified_price_entry_intent(plan,bound,session_date=date(2026,8,18))
    assert intent.ticker=='AAA'
    kwargs=dict(run_id='cost-test',batch_id='00000000-0000-0000-0000-000000000002',parent_record_id='00000000-0000-0000-0000-000000000003',event_month='2026-08-01')
    initial=project_initial_momentum_entry(bound,bound.initial_momentum,**kwargs)
    restored=restore_initial_momentum(initial,ticker='AAA',boundary_ms=41000,episode_start_ms=30000,current_momentum=bound.momentum,strategy_number=61)
    assert restored==bound.initial_momentum
    price=project_first_price_entry(bound.momentum,bound.initial_momentum,bound.first_price,price_source_token=bound.price_source_token,strategy_number=61,**kwargs)
    assert restore_first_price_entry(price,bound.momentum,bound.initial_momentum,expected_price=bound.first_price,expected_price_source_token=bound.price_source_token,strategy_number=61)==bound.first_price
    with pytest.raises(ValueError):restore_initial_momentum(initial,ticker='AAA',boundary_ms=41000,episode_start_ms=30000,current_momentum=bound.momentum,strategy_number=50)
    with pytest.raises(ValueError):bind_certified_price_break_proposal(plan,proposal,strategy_number=59)
    with pytest.raises(ValueError):replace(parent,policy=declared_momentum_policy(59))

@pytest.mark.parametrize('number,pending',[(59,False),(60,False),(61,False),(61,True)])
def test_real_native_coordinator_uses_declared_source_and_financial_order(number,pending):
    import asyncio
    from src.backend.backtest_strategy_one_coordinator import run_strategy_one_proposals
    from src.backend.backtest_strategy_one_scheduler import StrategyOneBoundaryScheduler
    from test_strategy_one_stateful import _facts
    _,parent,plan=native(number,1.2,1.27)
    candidate,_,_,financial=_facts();financial=replace(financial,pending_entry=pending)
    order=[];proposals=[]
    async def noop(*args):pass
    async def broker(work):order.append(('broker',work.boundary_ms))
    async def views(*args):order.append(('financial',31000));return (financial,)
    async def proposed(value):proposals.append(value)
    scheduler=StrategyOneBoundaryScheduler(session_date='2026-08-18',candidate_rows=iter((candidate,)),active_source=lambda *_:iter(()))
    counts=asyncio.run(run_strategy_one_proposals(scheduler,parent.entry,process_broker_boundary=broker,financial_views=views,on_entry_proposal=proposed,on_management=noop,position_source_owned=lambda _:False,financially_active_tickers=lambda:(),finish_boundary=noop,observe_activation=noop,observe_completed_seconds=noop,strategy_number=number,momentum_plan=parent.momentum,initial_momentum_plan=plan))
    assert order==[('broker',31000),('financial',31000)]
    assert counts.entry_proposals==(0 if pending else 1)
    if proposals:assert proposals[0].strategy_number==number and proposals[0].initial_momentum==plan.selection_witness('AAA',31000)


def test_selected_fraction_overflow_and_exact_clocks_are_independent():
    from src.trading_runtime.entry_momentum_growth import growth_mask
    from src.trading_runtime.strategy_strong_ten_second_momentum import strong_ten_second_momentum_entry_mask
    boundaries=np.array([41000],dtype=np.int64)
    current=np.array([[41000,40000]],dtype=np.int64);prior=np.array([[40000,30000]],dtype=np.int64)
    line=np.array([[1.,1.79e308]],dtype=np.float64);signal=np.zeros((1,2),dtype=np.float64)
    old=np.array([[2.,1.7e308]],dtype=np.float64);old_signal=signal.copy()
    arrays=(current,prior,line,signal,old,old_signal)
    policy=declared_momentum_policy(61)
    before=tuple(value.copy() for value in arrays)
    assert growth_mask(policy,policy.current_fraction,boundaries,*arrays).tolist()==[True]
    for foreign in ((True,20),(1.0,20),(1,100),[1,20]):
        with pytest.raises(ValueError,match='declared fraction'):
            growth_mask(policy,foreign,boundaries,*arrays)
    with pytest.raises(ValueError,match='overflows'):strong_ten_second_momentum_entry_mask(boundaries,*arrays)
    assert all(np.array_equal(a,b) for a,b in zip(arrays,before))
    with pytest.raises(ValueError,match='forming'):growth_mask(policy,policy.current_fraction,boundaries,current+100,prior,line,signal,old,old_signal)
    with pytest.raises(ValueError,match='aligned'):growth_mask(policy,policy.current_fraction,boundaries,current,prior,np.ones((1,2),dtype=np.float32),signal,old,old_signal)


def test_future_tail_does_not_change_original_anchor_or_prefix_mask():
    market,parent,_=native(61)
    prepared=replace(parent.candidates.prepared[0],boundary_ms=parent.candidates.prepared[0].boundary_ms[:1],episode_start_ms=parent.candidates.prepared[0].episode_start_ms[:1])
    candidates=replace(parent.candidates,prepared=(prepared,))
    momentum=load_rising_momentum_plan(market,candidates,client=FractionSource())
    prefix=compile_declared_initial_momentum_plan(candidates,parent.entry,momentum,parent.policy)
    assert prefix.first_indices.tolist()==parent.first_indices[:1].tolist()
    assert prefix.eligible_mask.tolist()==parent.eligible_mask[:1].tolist()
    assert prefix.lookup('AAA',31000)==parent.lookup('AAA',31000)


def declared_recovery_graph(number):
    from types import SimpleNamespace
    from uuid import UUID
    from src.backend.backtest_strategy_entry_activity_source import load_entry_activity_plan
    from src.backend.backtest_strategy_episode_activity_gate import compile_episode_activity_static_gate
    from src.backend.backtest_strategy_episode_activity_source import EpisodeActivityReadbackAuthority,bind_episode_activity_proposal
    from test_backtest_strategy_entry_activity_source import ActivityBars
    from src.trading_runtime.runtime import TradingRuntime,RunMode
    from src.backend.backtest_journal_memory import BacktestMemoryJournal
    from src.backend.backtest_typed_projection import project_pending_backtest_v4_prefix
    from src.trading_runtime.arte_journal_writer import V4StrategyOneEntryBatch
    market,parent,plan=native(number,1.2,1.5)
    market=replace(market,required_resolutions_ms=tuple(sorted(set(market.required_resolutions_ms)|{5000})))
    activity=load_entry_activity_plan(market,plan,client=ActivityBars())
    episode=EpisodeActivityReadbackAuthority('declared-recovery-run',compile_episode_activity_static_gate(activity),number)
    source=CertifiedPriceReadbackAuthority(episode.run_id,plan,episode)
    day=date(2026,8,18)
    original=replace(_proposal(),strategy_number=number,boundary_ms=41000,episode_start_ms=30000,momentum=parent.momentum.lookup('AAA',41000),initial_momentum=parent.selection_witness('AAA',41000))
    proposal=bind_episode_activity_proposal(source,bind_certified_price_break_proposal(plan,original,strategy_number=number),session_date=day)
    runtime=SimpleNamespace(config=SimpleNamespace(mode=RunMode.BACKTEST,strategy_id='early-squeeze-strategy',strategy_revision=number,anchor_date=day),run_id=source.run_id,journal=BacktestMemoryJournal(run_id=source.run_id),_strategy_one_price_source=None)
    TradingRuntime.bind_strategy_one_price_source(runtime,source)
    intent=TradingRuntime._strategy_one_entry_intent(runtime,proposal)
    record=runtime.journal.append_strategy_one_intent(intent=intent,proposal=proposal,session_date=day,account_id=proposal.account_id,strategy_id=runtime.config.strategy_id,strategy_revision=number,first_price_source=source)
    units=project_pending_backtest_v4_prefix(runtime.journal,attempt_id=str(UUID(int=100)),run_month=day.replace(day=1),prior_sequence=0,through_sequence=record.sequence,expected_config={'mode':'backtest','strategy_id':runtime.config.strategy_id,'strategy_revision':number},first_price_source=source)
    unit=next(unit for unit in units if type(unit) is V4StrategyOneEntryBatch)
    return proposal,intent,source,unit,record

@pytest.mark.parametrize('number,mutation',[(n,m) for n in (59,60,61) for m in (None,'foreign_source','future_clock')])
def test_real_cold_page_and_manager_attachment_reproduce_declared_policy(monkeypatch,number,mutation):
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
    proposal,intent,source,unit,record=declared_recovery_graph(number)
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
        _,_,source,_,_=declared_recovery_graph(60 if number==59 else 59)
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
    if mutation is None:
        page=reader.load_committed_strategy_one_entry_page(None,prefix,first_price_source=source)
        assert page.entries[0].proposal==proposal and page.entries[0].intent==intent
        assert len(queries)==5
        restored=attach_committed_momentum_sources(None,prefix,state,first_price_source=source)
        assert restored.submitted[0][1]==proposal
        assert reference.momentum is None and restored.submitted[0][1].momentum is not None
    else:
        with pytest.raises((ValueError,RuntimeError,DecimalException)):
            reader.load_committed_strategy_one_entry_page(None,prefix,first_price_source=source)
        with pytest.raises((ValueError,RuntimeError,DecimalException)):
            attach_committed_momentum_sources(None,prefix,state,first_price_source=source)
