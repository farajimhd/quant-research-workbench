"""Three real broker holdings exercise mixed selected exit-source ancestry.

Only SQL/producer transport is synthetic; native sealers, OMS, broker and
checkpoint consumers are unchanged. Fixture methods extend the existing
reviewed two-holding graph with one independent account and causal exit.
"""
import asyncio
from dataclasses import asdict,replace
from datetime import date
import pytest
from src.backend.backtest_market_data import market_day_boundary
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.trading_runtime.domain import InstrumentContract,TradingMode
from src.trading_runtime.execution_policies import ExecutionMarketSnapshot
from src.trading_runtime.order_management import OrderManagementEngine,BrokerCommunicationPolicy
from src.trading_runtime.risk import RiskAuthority
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter
from src.trading_runtime.strategy_orders import RuntimeIbkrStrategyOrderPlanner
from src.trading_runtime.strategy_one_contract import STRATEGY_ID
from test_checkpoint_prefix_held_graph import checkpoint_graph
from test_checkpoint_prefix_two_held_graph import TwoCheckpointGraph
from test_simulated_broker_liquidity_bar import bar


async def simulated_three_holdings(checkpoint_callback):
    """Actual fills/exit/protection lifecycle; no assigned positions or mocked OMS."""
    c,run,p,i,s,state,request,*_=checkpoint_graph()
    day=date(2026,8,18)
    initial=market_day_boundary(day,0)
    broker=SimulatedBrokerAdapter(['DU1','DU2','DU3'],mode=TradingMode.BACKTEST,
        initial_time=initial,fixed_bar_mode=True)
    await broker.initialize()
    journal=BacktestMemoryJournal(run_id=run)
    risk=RiskAuthority()
    await risk.prime(broker,['DU1','DU2','DU3'])
    planner=RuntimeIbkrStrategyOrderPlanner({'AAA':InstrumentContract(
        instrument_id='conid:123',conid=123,symbol='AAA',security_type='STK',exchange='SMART',currency='USD')},
        strategy_id=STRATEGY_ID,strategy_revision=68,run_id=run)
    manager=OrderManagementEngine(broker=broker,planner=lambda intent,account,event:planner.plan(
        intent=intent,account_id=account,event=event),risk=risk,journal=journal,run_id=run,
        strategy_id=STRATEGY_ID,strategy_revision=68,policy=BrokerCommunicationPolicy(),
        causal_execution_clock=True)
    def bucket(boundary,bid,ask):
        at=market_day_boundary(day,boundary)
        snapshot={**bar(at,bid=bid,ask=ask,low=bid,high=ask,bid_size=1000,ask_size=1000,
            execution_volume=1000,execution_price_levels=((ask,1000),)),'ticker':'AAA'}
        manager.on_market_snapshot(ExecutionMarketSnapshot('AAA',bid,ask,.01,at,'fixture'))
        return at,snapshot
    async def advance(boundary,bid,ask):
        at,snapshot=bucket(boundary,bid,ask)
        checkpoint_callback.liquidity[boundary]=snapshot
        fills=await broker.on_liquidity_bar(snapshot,at=at)
        for order in await broker.live_orders():await manager.on_order_update(order)
        return fills
    def approve(core,account):
        from src.trading_runtime.portfolio import PortfolioReservation,_intent_correlation
        decision=f'decision:{core.intent_id}';reservation=f'reservation:{core.intent_id}'
        creation=PortfolioReservation(reservation,decision,core.intent_id,'cash',account,
            STRATEGY_ID,p.assignment_id if account=='DU1' else f'assignment-{account}',core.ticker,core.action,10.,10.,
            core.reference_price,100.,1.,core.event_time)
        journal.append(run_id=run,category='portfolio_management',entity_type='portfolio_reservation',
            entity_id=reservation,account_id=account,event_time=core.event_time,
            payload={**asdict(creation),'event':'reservation_created'})
        journal.save_portfolio_state(account,{'reservations':[asdict(creation)]})
        return replace(core,quantity=10.,metadata=dict(assignment_id=creation.assignment_id,
            portfolio_account_key='cash',portfolio_decision_id=decision,
            unprotected_backtest_authorized=False,portfolio_policy='component@1',
            portfolio_reservation_id=reservation,requested_quantity=10.,portfolio_fx_to_base=1.,
            correlation_id=_intent_correlation(run,core),causation_id=decision))
    try:
        await advance(41000,10.,10.01)
        entries=[]
        for account in ('DU1','DU2','DU3'):
            from src.backend.backtest_strategy_episode_activity_source import certified_episode_entry_intent
            assignment=p.assignment_id if account=='DU1' else f'assignment-{account}'
            core=certified_episode_entry_intent(s,replace(p,account_id=account,
                assignment_id=assignment),session_date=day)
            approved=approve(core,account)
            group=await manager.submit_intent(approved,account_id=account,event=None)
            entries.append(group)
        fills=await advance(42000,10.,10.01)
        assert len(fills)==3
        assert all(broker.position_quantity(account,123,'AAA')==10. for account in ('DU1','DU2','DU3'))
        held=broker.broker_match_snapshot_state()
        if getattr(checkpoint_callback,'profit_exit',False):
            await advance(110000,10.13,10.14)
        await advance(120000,9.98,9.99)
        # Actual causal risk exit factory; the source ID is tied to the first
        # entry. Native durability wiring is evaluated separately below.
        from src.trading_runtime.strategy_followthrough_exit import followthrough_exit_intent
        diagnostic=checkpoint_callback(c,run,p,i,s,broker,manager,journal,120000,'DU1')
        exit_core=followthrough_exit_intent(diagnostic.current,request.financial,session_date=day,
            source_entry_intent_id=entries[0].intent_id,diagnostic=diagnostic,strategy_number=68)
        if getattr(checkpoint_callback,'profit_exit',False):
            await advance(125000,9.98,9.99)
            exit_core=checkpoint_callback.failures['DU1'][0]
        exit_approved=approve(exit_core,'DU1')
        exited=await manager.submit_intent(exit_approved,account_id='DU1',event=None)
        fills=await advance(125100 if getattr(checkpoint_callback,'profit_exit',False) else 120100,9.98,9.99)
        assert len(fills)==1 and fills[0].side=='S'
        assert broker.position_quantity('DU1',123,'AAA')==0.
        assert broker.position_quantity('DU2',123,'AAA')==10.
        second_boundary=130000 if getattr(checkpoint_callback,'profit_exit',False) else 125000
        await advance(second_boundary,9.98,9.99)
        later=broker.broker_match_snapshot_state()
        checkpoint_callback(c,run,p,i,s,broker,manager,journal,second_boundary,'DU2')
        from src.trading_runtime.strategy_followthrough_exit import followthrough_exit_intent
        second=checkpoint_callback.checkpoints[-1][1]
        second_core=followthrough_exit_intent(checkpoint_callback.checkpoints[-1][2].current,
            second.financial,session_date=day,source_entry_intent_id=entries[1].intent_id,
            diagnostic=checkpoint_callback.checkpoints[-1][2],strategy_number=68)
        await manager.submit_intent(approve(second_core,'DU2'),account_id='DU2',event=None)
        fills=await advance(second_boundary+100,9.98,9.99)
        assert len(fills)==1 and fills[0].side=='S'
        assert broker.position_quantity('DU2',123,'AAA')==0.
        assert broker.position_quantity('DU3',123,'AAA')==10.
        await advance(second_boundary+5000,9.98,9.99)
        checkpoint_callback(c,run,p,i,s,broker,manager,journal,second_boundary+5000,'DU3')
        checkpoint_callback.order_outcomes=tuple(dict(account=o.request.acctId,
            side=o.request.side,order_type=o.request.orderType,status=str(o.status),
            filled=o.filled,oca_group=o.oca_group,parent_id=o.request.parentId,
            single_group=o.request.isSingleGroup) for o in broker._orders.values())
        return held,later,journal.records(run),tuple(manager._groups.values())
    finally:
        await manager.close()
        journal.close()


class MixedCheckpointGraph(TwoCheckpointGraph):
    def initial_entries(self,p,i,s,manager,journal):
        from src.trading_runtime import arte_journal_writer as writer
        from src.trading_runtime.arte_intent_projection import strategy_intent_batch
        from src.backend.backtest_strategy_episode_activity_source import certified_episode_entry_intent
        from src.trading_runtime.arte_strategy_one_entry_journal import project_strategy_one_entry_evidence
        from src.trading_runtime.arte_strategy_one_entry_schema import ENTRY_EVIDENCE
        from src.trading_runtime.arte_rising_momentum_entry_v4 import project_rising_momentum_entry,MOMENTUM
        from src.trading_runtime.arte_initial_momentum_entry_v4 import project_initial_momentum_entry,INITIAL_MOMENTUM
        from src.trading_runtime.arte_first_price_entry_v4 import project_first_price_entry,FIRST_PRICE
        from src.trading_runtime.arte_entry_activity_v4 import project_entry_activity,ENTRY_ACTIVITY
        from src.trading_runtime.arte_journal_projection import project_portfolio_admission_records
        from src.trading_runtime.arte_oms_projection import oms_group_state_batch,freeze_oms_group
        from src.trading_runtime.arte_oms_tactic_projection import tactic_rows,PARENT_TABLE,STEP_TABLE
        from uuid import UUID
        for account in ('DU1','DU2','DU3'):
            proposal=replace(p,account_id=account,assignment_id=p.assignment_id if account=='DU1' else f'assignment-{account}')
            core=certified_episode_entry_intent(s,proposal,session_date=date(2026,8,18))
            group=next(g for g in manager._groups.values() if g.intent.intent_id==core.intent_id)
            batch=strategy_intent_batch(core,account_id=account,sequence=self.sequence+1,
                source_cursor='2026-08-18:41000',run_status='running',recorded_at=core.event_time,
                record_id=str(UUID(int=400+self.sequence)),**self.identity())
            kwargs=dict(run_id=self.run,batch_id=batch.batch_id,parent_record_id=batch.events[0]['record_id'],event_month='2026-08-01')
            raw=project_strategy_one_entry_evidence(proposal,core,session_date=date(2026,8,18),
                run_id=self.run,batch_id=batch.batch_id,parent_record_id=kwargs['parent_record_id'],first_price_source=s)
            extra=((ENTRY_EVIDENCE.name,(raw,)),(MOMENTUM.name,project_rising_momentum_entry(proposal,**kwargs)),
                (INITIAL_MOMENTUM.name,project_initial_momentum_entry(proposal,proposal.initial_momentum,**kwargs)),
                (FIRST_PRICE.name,project_first_price_entry(proposal.momentum,proposal.initial_momentum,
                    proposal.first_price,price_source_token=proposal.price_source_token,strategy_number=68,**kwargs)),
                (ENTRY_ACTIVITY.name,(project_entry_activity(s.entry_activity_source.witness('AAA',41000),strategy_number=68,**kwargs),)))
            families=(*writer._sealed_families(batch),*((name,tuple(writer.typed_row(name,r) for r in rows)) for name,rows in extra))
            self.append(families,cursor=batch.source_cursor,at=core.event_time)
            self.entries[account]=(proposal,core,batch,group)
        reservations={}
        for account in ('DU1','DU2','DU3'):
            proposal,core,batch,group=self.entries[account]
            reservation=journal.portfolio_admission_reservation(account,group.intent.metadata['portfolio_reservation_id'])
            metrics={k:0. for k in ('net_liquidation','available_funds','buying_power','gross_exposure','net_exposure',
                'reserved_notional','open_risk','daily_loss','drawdown','position_count')}
            at=core.event_time
            decision=dict(event='portfolio_decision',ticker='AAA',action='enter_long',
                decision_id=reservation['decision_id'],request_id='component-request',account_key='cash',account_id=account,
                policy_id='component',policy_revision=1,snapshot_id='component-admission',status='approved',
                requested_quantity=10.,approved_quantity=10.,approved_notional=100.,planned_loss=1.,
                reservation_id=reservation['reservation_id'],reasons=(),metrics_before=metrics,metrics_after=metrics,
                decided_at=at,correlation_id='',causation_id='')
            admission=project_portfolio_admission_records((('portfolio_decision',reservation['decision_id'],account,decision),
                ('portfolio_reservation',reservation['reservation_id'],account,{**reservation,'event':'reservation_created'})),
                first_sequence=self.sequence+1,source_cursor='2026-08-18:42000',**self.identity())
            self.append(writer._sealed_families(admission),cursor=admission.source_cursor,at=at)
            reservations[account]=reservation
        for account in ('DU1','DU2','DU3'):
            proposal,core,batch,group=self.entries[account]
            reservation=reservations[account]
            oms=oms_group_state_batch(freeze_oms_group(group),sequence=self.sequence+1,
                source_cursor='2026-08-18:42000',run_status='running',strategy_id=STRATEGY_ID,strategy_revision=68,
                recorded_at=group.updated_at,published_intent_batch=batch,committed_intent_batch_id=batch.batch_id,
                admission_source_intent=core,admission_reservation=reservation,**self.identity())
            tr,steps=tactic_rows(group.tactic,group_record_id=oms.events[0]['record_id'],run_id=self.run,
                event_month='2026-08-01',batch_id=oms.batch_id,account_id=account)
            self.append((*writer._sealed_families(oms),(PARENT_TABLE,(tr,)),(STEP_TABLE,steps)),cursor=oms.source_cursor,at=group.updated_at)
            self.entries[account]=(proposal,core,batch,group)

    def producer_transport(self):
        import json,re
        import polars as pl
        from src.backend.backtest_confirmed_original_risk_source import REQUIRED,SOURCE_KEYS
        from src.backend.backtest_market_data import SESSION_OPEN_OFFSET_MS
        market=self.source.plan.source.market;units={u.stage:u.attempt_id for u in market.units}
        identity=dict(source_build_id=market.build_id,source_market_plan_token=market.token,
            source_bars_attempt_id=units['bars'],source_indicators_attempt_id=units['technical'],
            source_liquidity_attempt_id=units['broker_100ms'],session_date='2026-08-18',ticker='AAA')
        def arrow(sql):
            self.queries.append(('producer',sql))
            assert 'FROM arte.bars_v1 b LEFT JOIN arte.indicators_v1' in sql
            cutoff=int(re.search(r'b.bucket_index<(\d+)',sql)[1])*5000-SESSION_OPEN_OFFSET_MS
            frame=pl.DataFrame([{**identity,'boundary_ms':b,'close_int':99800,'price_valid':True,
                'macd_line':.1,'macd_signal':.2} for b in (115000,120000,125000,130000,135000) if b<=cutoff],
                schema={**{k:pl.String for k in SOURCE_KEYS},'boundary_ms':pl.UInt64,'close_int':pl.UInt64,
                    'price_valid':pl.Boolean,'macd_line':pl.Float64,'macd_signal':pl.Float64}).select(REQUIRED)
            yield from frame.to_arrow().to_batches()
        self.client.iter_arrow_record_batches=arrow
        old=self.client.execute
        def execute(sql):
            self.queries.append(('sql',sql))
            if 'FROM arte.liquidity_100ms_v1' in sql:
                boundary=(int(re.search(r'bucket_index=(\d+)',sql)[1])+1)*100-SESSION_OPEN_OFFSET_MS
                row=self.liquidity[boundary]
                return json.dumps({k:row[k] for k in ('bid_int','ask_int','quote_valid','quote_timestamp_us')})
            return old(sql)
        self.client.execute=execute

    def append_first_exit_outcome(self,manager,journal):
        from src.trading_runtime import arte_journal_writer as writer
        from src.trading_runtime.arte_journal_projection import project_portfolio_admission_records
        from src.trading_runtime.arte_oms_projection import oms_group_state_batch,freeze_oms_group
        from src.trading_runtime.arte_oms_tactic_projection import tactic_rows,PARENT_TABLE,STEP_TABLE
        core,base,request=self.failures[self.exit_account]
        elapsed=core.event_time-market_day_boundary(date(2026,8,18),0)
        exit_boundary=(elapsed.days*86400+elapsed.seconds)*1000+elapsed.microseconds//1000
        group=next(g for g in manager._groups.values() if g.intent.intent_id==core.intent_id)
        reservation=journal.portfolio_admission_reservation(self.exit_account,group.intent.metadata['portfolio_reservation_id'])
        metrics={k:0. for k in ('net_liquidation','available_funds','buying_power','gross_exposure','net_exposure',
            'reserved_notional','open_risk','daily_loss','drawdown','position_count')}
        decision=dict(event='portfolio_decision',ticker='AAA',action='exit',decision_id=reservation['decision_id'],
            request_id='component-exit',account_key='cash',account_id=self.exit_account,policy_id='component',policy_revision=1,
            snapshot_id='component-exit-admission',status='approved',requested_quantity=10.,approved_quantity=10.,
            approved_notional=100.,planned_loss=1.,reservation_id=reservation['reservation_id'],reasons=(),
            metrics_before=metrics,metrics_after=metrics,decided_at=core.event_time,correlation_id='',causation_id='')
        admission=project_portfolio_admission_records((('portfolio_decision',reservation['decision_id'],self.exit_account,decision),
            ('portfolio_reservation',reservation['reservation_id'],self.exit_account,{**reservation,'event':'reservation_created'})),
            first_sequence=self.sequence+1,source_cursor=f'2026-08-18:{exit_boundary}',**self.identity())
        self.append(writer._sealed_families(admission),cursor=admission.source_cursor,at=core.event_time)
        for account,source_core,source_batch,live in (
                (self.exit_account,self.entries[self.exit_account][1],self.entries[self.exit_account][2],self.entries[self.exit_account][3]),
                (self.exit_account,core,base,group)):
            reservation=journal.portfolio_admission_reservation(account,live.intent.metadata['portfolio_reservation_id'])
            oms=oms_group_state_batch(freeze_oms_group(live),sequence=self.sequence+1,
                source_cursor=f'2026-08-18:{exit_boundary+100}',run_status='running',strategy_id=STRATEGY_ID,strategy_revision=68,
                recorded_at=live.updated_at,published_intent_batch=source_batch,committed_intent_batch_id=source_batch.batch_id,
                admission_source_intent=source_core,admission_reservation=reservation,**self.identity())
            tr,steps=tactic_rows(live.tactic,group_record_id=oms.events[0]['record_id'],run_id=self.run,
                event_month='2026-08-01',batch_id=oms.batch_id,account_id=account)
            self.append((*writer._sealed_families(oms),(PARENT_TABLE,(tr,)),(STEP_TABLE,steps)),
                cursor=oms.source_cursor,at=live.updated_at)

    def __call__(self,c,run,p,i,s,broker,manager,journal,boundary,account):
        if not self.entries:
            self.client=c;self.run=run;self.source=s
            c.tables.clear()
            old_execute=c.execute
            def execute(sql):
                import json,re,struct
                if 'reinterpretAsUInt64' not in sql:return old_execute(sql)
                from src.trading_runtime.arte_rising_momentum_entry_v4 import VALUES
                name=sql.split('FROM arte.',1)[1].split(' ',1)[0]
                batch=re.search(r"batch_id=toUUID\('([^']+)'\)",sql)
                rows=[r for r in c.tables.get(name,()) if batch is None or r['batch_id']==batch[1]]
                return '\n'.join(json.dumps({**r,**{key+'_bits':None if r[key] is None else
                    int.from_bytes(struct.pack('>d',float(r[key])),'big') for key in VALUES}}) for r in rows)
            c.execute=execute
            from tests.test_arte_journal_writer import MemoryClient,run_row,run_context
            from src.trading_runtime import arte_journal_writer as writer
            context=MemoryClient();writer.publish_typed_run(context,{**run_row(),'run_id':run})
            writer.publish_typed_run_context(context,run_id=run,config={**run_context(),
                'strategy_id':STRATEGY_ID,'strategy_revision':68},account_ids=('DU1','DU2','DU3'))
            c.tables.update(context.tables)
            self.initial_entries(p,i,s,manager,journal)
            self.producer_transport()
        else:
            self.exit_account='DU1' if account=='DU2' else 'DU2'
            self.append_first_exit_outcome(manager,journal)
        from src.backend.backtest_strategy_one_management import OriginalRiskManagementState
        from src.trading_runtime.confirmed_original_risk_failure import (CompletedRiskBucket,
            OriginalRiskDecisionDiagnostic,CONFIRMED_ORIGINAL_RISK_RULE)
        from src.trading_runtime.strategy_followthrough_failure import FollowThroughFailure
        from src.trading_runtime.strategy_one_stateful import StrategyOneFinancialView
        from src.trading_runtime.strategy_engine import AssignmentStatus,StrategyPermissions
        from src.trading_runtime.strategy_one_position import ProtectionState
        from src.trading_runtime.original_risk_checkpoint import OriginalRiskCheckpointRequest,confirm_original_risk_checkpoint_sources
        from src.trading_runtime.arte_journal_projection import backtest_cursor_batch
        from src.trading_runtime.journal_contract import JournalRecord
        from src.trading_runtime import arte_journal_writer as writer
        from src.trading_runtime import strategy_one_management_snapshot as ms
        from src.trading_runtime import strategy_one_broker_match_snapshot as bs
        from src.trading_runtime.strategy_one_protection_snapshot import TABLES as protections
        from src.trading_runtime.original_risk_pending_snapshot import PENDING,selected_parent_contract
        from src.backend.backtest_typed_publisher import TypedBacktestReceipt
        from uuid import UUID
        day=date(2026,8,18);at=market_day_boundary(day,boundary)
        proposal,core,_,_=self.entries[account]
        units={u.stage:u.attempt_id for u in s.plan.source.market.units}
        newest=CompletedRiskBucket(boundary,99800,True,.1,.2,s.plan.source.market.build_id,
            s.plan.source.market.token,units['bars'],units['technical'],day.isoformat(),'AAA',units['broker_100ms'])
        witness=FollowThroughFailure(boundary,42000,10.01,9.89,99800,.1,.2,9.98,9.99,10001)
        diagnostic=OriginalRiskDecisionDiagnostic(witness,newest,replace(newest,boundary_ms=boundary-5000),CONFIRMED_ORIGINAL_RISK_RULE)
        financial=StrategyOneFinancialView(proposal.assignment_id,account,'AAA',AssignmentStatus.MANAGING,
            StrategyPermissions(),10.,False,False,False,1)
        request=OriginalRiskCheckpointRequest(diagnostic,financial,core.intent_id)
        held=tuple((a,self.entries[a][0]) for a in self.entries if broker.position_quantity(a,123,'AAA')>0.)
        keys={a:(a,pp.assignment_id,'AAA') for a,pp in held}
        state=OriginalRiskManagementState(boundary,tuple(sorted((keys[a],pp) for a,pp in held)),
            tuple(sorted((keys[a],ProtectionState(42000,pp.initial_stop,pp.initial_target)) for a,pp in held)),(),
            position_highs=tuple(sorted((keys[a],getattr(self,'position_high_int',100100)) for a,_ in held)),
            first_held_boundaries=tuple(sorted((keys[a],42000) for a,_ in held)),original_risk_requests=(request,))
        cursor_record=JournalRecord(str(UUID(int=500+self.sequence)),run,self.sequence+1,at,at,
            'checkpoint','market_boundary',f'{day}:{boundary}','',dict(session_date=day.isoformat(),
            boundary_ms=boundary,market_sequence=self.sequence+1,frame_as_of=None,frame_ticker=None,
            frame_timeframe=None,frame_sequence=None))
        cursor=backtest_cursor_batch(cursor_record,source_cursor=cursor_record.entity_id,**{k:v for k,v in self.identity().items() if k!='run_id'})
        self.append(writer._sealed_families(cursor),cursor=cursor.source_cursor,at=at)
        mr=ms.project_manager_snapshot(run_id=run,session_date=day,checkpoint_sequence=self.sequence,state=state,first_price_source=s)
        br=bs.project_broker_match_snapshot(run_id=run,session_date=day,checkpoint_sequence=self.sequence,
            boundary_ms=boundary,state=broker.broker_match_snapshot_state())
        for contract,rows in ((selected_parent_contract(),(mr.snapshot,)),(ms.SOURCE,mr.sources),
                (ms.BREAK,mr.pending_breaks),(ms.HIGH,mr.position_highs),(ms.CLOSED,mr.closed_positions),
                (ms.FIRST_HELD,mr.first_held_boundaries),(PENDING,mr.original_risk_requests),
                *zip(protections,((mr.protection.snapshot,),mr.protection.states,mr.protection.resistances),strict=True),
                *zip(bs.TABLES,((br.snapshot,),br.accounts,br.positions,br.open_orders,br.tickers,br.marks),strict=True)):
            c.tables.setdefault(contract.name,[]).extend(rows)
        class Head:
            def __init__(self,value):self.value=value
            def read_head(self,*,run_id):assert run_id==run;return self.value
        mh=Head(ms.ManagerSnapshotHead(run,self.sequence,self.batch_id,mr.snapshot['content_hash'],0))
        bh=Head(bs.BrokerMatchHead(run,self.sequence,self.batch_id,br.snapshot['content_hash'],0))
        selected=confirm_original_risk_checkpoint_sources(c,mh,bh,(request,),
            TypedBacktestReceipt(self.sequence,self.batch_id,cursor.source_cursor),run_id=run,first_price_source=s)[0]
        self.checkpoints.append((state,request,selected))
        self.append_failure(request,selected)
        return selected

def test_actual_mixed_three_held_graph_full_proof_and_counts(monkeypatch):
    from src.trading_runtime import arte_journal_commit_v4 as commits
    from src.trading_runtime import original_risk_checkpoint as checkpoint
    from src.trading_runtime import _checkpoint_prefix_read as memo
    graph=MixedCheckpointGraph()
    asyncio.run(simulated_three_holdings(graph))
    assert len(graph.checkpoints)==3
    assert [s.boundary_ms for s,_,_ in graph.checkpoints]==[120000,125000,130000]
    original=checkpoint._load_original_risk_checkpoint
    calls=[];states=[]
    def counted(*args,**kwargs):
        result=original(*args,**kwargs)
        calls.append(args[5].checkpoint.source_manager_checkpoint_sequence)
        states.append((calls[-1],result))
        return result
    monkeypatch.setattr(checkpoint,'_load_original_risk_checkpoint',counted)
    graph.queries.clear()
    current=commits.load_verified_v4_prefix(graph.client,graph.run,first_price_source=graph.source)
    active_calls=tuple(calls);active_queries=len(graph.queries)
    active_states=tuple(states)
    calls.clear();states.clear();graph.queries.clear()
    monkeypatch.setattr(memo,'_reconstruct_original_risk_checkpoint',counted)
    baseline=commits.load_verified_v4_prefix(graph.client,graph.run,first_price_source=graph.source)
    assert current==baseline
    assert all(value==next(state for key,state in active_states if key==sequence)
        for sequence,value in states)
    assert {key for key,_ in states}=={key for key,_ in active_states}
    print(dict(active_calls=active_calls,baseline_calls=tuple(calls),active_queries=active_queries,
        baseline_queries=len(graph.queries)))
    assert len(active_calls)<len(calls)
    assert memo._FULL_OPERATION.get() is None
    assert memo._CHECKPOINT_OBSERVATION.get() is None

def test_actual_mixed_graph_against_frozen_d66_memo(monkeypatch):
    import subprocess,sys,types
    from src.trading_runtime import arte_journal_commit_v4 as commits
    from src.trading_runtime import original_risk_checkpoint as checkpoint
    from src.trading_runtime import strategy_liquidity_fade_financial_checkpoint as financial_reader
    graph=MixedCheckpointGraph();asyncio.run(simulated_three_holdings(graph))
    original=checkpoint._load_original_risk_checkpoint
    calls=[];states=[];financials=[]
    financial_original=financial_reader.load_liquidity_fade_financial_checkpoint
    def financial_count(*a,**kw):
        value=financial_original(*a,**kw)
        financials.append(value)
        return value
    monkeypatch.setattr(financial_reader,'load_liquidity_fade_financial_checkpoint',financial_count)
    def count(*a,**kw):
        result=original(*a,**kw);calls.append(a[5].checkpoint.source_manager_checkpoint_sequence)
        states.append((calls[-1],result))
        return result
    monkeypatch.setattr(checkpoint,'_load_original_risk_checkpoint',count)
    graph.queries.clear()
    active=commits.load_verified_v4_prefix(graph.client,graph.run,first_price_source=graph.source)
    active_calls=tuple(calls);active_queries=len(graph.queries)
    active_states=tuple(states)
    active_financials=tuple(financials)
    calls.clear();states.clear();financials.clear();graph.queries.clear()
    name='src.trading_runtime._checkpoint_prefix_read'
    frozen=subprocess.run(['git','show','d66a2f151d9128145ed9ca2ea8dd07914b6120c2:src/trading_runtime/_checkpoint_prefix_read.py'],
        check=True,capture_output=True,text=True).stdout
    baseline=types.ModuleType(name);baseline.__package__='src.trading_runtime'
    exec(compile(frozen,'frozen-d66-checkpoint-prefix-read','exec'),baseline.__dict__)
    # Only adapt the new post-validation observation site to the original no-op.
    # No original consumer, sealer, ancestry guard or source input is substituted.
    baseline._observe_verified_exit_source=lambda *a,**kw:None
    monkeypatch.setitem(sys.modules,name,baseline)
    legacy=commits.load_verified_v4_prefix(graph.client,graph.run,first_price_source=graph.source)
    assert active==legacy
    assert all(value==next(state for key,state in active_states if key==sequence)
        for sequence,value in states)
    assert {key for key,_ in states}=={key for key,_ in active_states}
    assert all(value==next(item for item in active_financials
        if item.checkpoint_sequence==value.checkpoint_sequence) for value in financials)
    assert {item.checkpoint_sequence for item in financials}=={
        item.checkpoint_sequence for item in active_financials}
    print(dict(active_calls=active_calls,frozen_d66_calls=tuple(calls),
        active_queries=active_queries,frozen_d66_queries=len(graph.queries)))
    assert len(active_calls)<len(calls)
    assert active_queries<len(graph.queries)

@pytest.mark.parametrize('defect',['failure_child','diagnostic_child','manager_child','broker_child','producer_pair','quote'])
@pytest.mark.parametrize('paired_final',[False,True])
def test_mixed_historical_hit_rejects_fresh_source_and_children(defect,paired_final,monkeypatch):
    import json,polars as pl
    from src.trading_runtime import _checkpoint_prefix_read as memo
    from src.trading_runtime import arte_journal_commit_v4 as commits
    graph=MixedCheckpointGraph();asyncio.run(simulated_three_holdings(graph))
    target=graph.checkpoints[1][2].checkpoint.source_manager_checkpoint_sequence
    original=memo._historical_digest
    mutated=False;outer_starts=0
    record=memo._record_verified_commit
    def recorded(client,source,commit):
        nonlocal outer_starts
        record(client,source,commit)
        if commit['first_sequence']==1:outer_starts+=1
    monkeypatch.setattr(memo,'_record_verified_commit',recorded)
    def historical(operation,client,prefix,event):
        nonlocal mutated
        digest=original(operation,client,prefix,event)
        if (prefix.last_sequence!=target or digest is None or mutated
                or outer_starts!=(2 if paired_final else 1)):
            return digest
        # Mutate only after this historical subset was genuinely proven and
        # resolved. Stored hashes/commit roots remain unchanged.
        mutated=True
        if defect=='failure_child':
            graph.client.tables['trading_followthrough_failure_v4'][0]['completed_close_int']+=1
        elif defect=='diagnostic_child':
            graph.client.tables['trading_original_risk_diagnostic_v4'][0]['prior_close_int']+=1
        elif defect=='manager_child':
            from src.trading_runtime.strategy_one_protection_snapshot import TABLES
            graph.client.tables[TABLES[1].name][0]['stop']='9.90'
        elif defect=='broker_child':
            from src.trading_runtime.strategy_one_broker_match_snapshot import POSITION
            graph.client.tables[POSITION.name][0]['quantity_f64_bits']+=1
        elif defect=='producer_pair':
            prior=graph.client.iter_arrow_record_batches
            def changed(sql):
                for batch in prior(sql):
                    yield pl.from_arrow(batch).with_columns(pl.lit(99801,dtype=pl.UInt64)
                        .alias('close_int')).to_arrow().to_batches()[0]
            graph.client.iter_arrow_record_batches=changed
        else:
            prior=graph.client.execute
            def changed(sql):
                raw=prior(sql)
                if 'FROM arte.liquidity_100ms_v1' in sql:
                    row=json.loads(raw);row['bid_int']+=1;return json.dumps(row)
                return raw
            graph.client.execute=changed
        return digest
    monkeypatch.setattr(memo,'_historical_digest',historical)
    with pytest.raises((ValueError,RuntimeError)):
        if paired_final:
            with memo._checkpoint_prefix_scope(graph.client,run_id=graph.run,
                    checkpoint_sequence=graph.sequence,first_price_source=graph.source):
                pass
        else:
            commits.load_verified_v4_prefix(graph.client,graph.run,first_price_source=graph.source)
    assert mutated
    assert memo._FULL_OPERATION.get() is None
    assert memo._CHECKPOINT_OBSERVATION.get() is None

class ProfitMixedCheckpointGraph(MixedCheckpointGraph):
    profit_exit=True
    position_high_int=101400
    def append_failure(self,request,diagnostic):
        if request.financial.account_id!='DU1':
            return super().append_failure(request,diagnostic)
        from uuid import UUID
        from src.trading_runtime.strategy_profit_giveback import ProfitGivebackWitness
        from src.trading_runtime.strategy_profit_giveback_exit import profit_giveback_exit_intent
        from src.trading_runtime.arte_profit_giveback_v4 import project_profit_giveback,PROFIT_GIVEBACK
        from src.trading_runtime.arte_intent_projection import strategy_intent_batch
        from src.trading_runtime import arte_journal_writer as writer
        from src.trading_runtime.original_risk_pending_snapshot import selected_parent_contract
        row=next(r for r in self.client.tables[selected_parent_contract().name]
                 if r['checkpoint_sequence']==self.sequence)
        witness=ProfitGivebackWitness(125000,42000,10.01,9.89,99800,.1,.2,9.98,9.99,10001,
            self.position_high_int,120000)
        core=profit_giveback_exit_intent(witness,request.financial,session_date=date(2026,8,18),
            source_entry_intent_id=request.source_entry_intent_id,strategy_number=68)
        batch=strategy_intent_batch(core,account_id='DU1',sequence=self.sequence+1,
            source_cursor='2026-08-18:125000',run_status='running',recorded_at=core.event_time,
            record_id=str(UUID(int=600+self.sequence)),**self.identity())
        child=project_profit_giveback(witness,core,request.financial,session_date=date(2026,8,18),
            source_entry_intent_id=request.source_entry_intent_id,run_id=self.run,batch_id=batch.batch_id,
            parent_record_id=batch.events[0]['record_id'],source_manager_snapshot_id=row['snapshot_id'],
            source_manager_checkpoint_sequence=self.sequence,strategy_number=68)
        self.append((*writer._sealed_families(batch),(PROFIT_GIVEBACK.name,(writer.typed_row(PROFIT_GIVEBACK.name,child),))),
            cursor=batch.source_cursor,at=core.event_time)
        self.failures['DU1']=(core,batch,request)


def test_actual_profit_and_risk_mixed_graph_full_native_source_recheck(monkeypatch):
    from src.trading_runtime import arte_journal_commit_v4 as commits
    from src.trading_runtime import original_risk_checkpoint as checkpoint
    from src.trading_runtime import _checkpoint_prefix_read as memo
    graph=ProfitMixedCheckpointGraph();asyncio.run(simulated_three_holdings(graph))
    assert len(graph.client.tables['trading_profit_giveback_v4'])==1
    assert len(graph.client.tables['trading_original_risk_diagnostic_v4'])==2
    original=checkpoint._load_original_risk_checkpoint;calls=[]
    def count(*a,**kw):
        result=original(*a,**kw);calls.append(a[5].checkpoint.source_manager_checkpoint_sequence)
        return result
    monkeypatch.setattr(checkpoint,'_load_original_risk_checkpoint',count)
    graph.queries.clear()
    current=commits.load_verified_v4_prefix(graph.client,graph.run,first_price_source=graph.source)
    active_calls=tuple(calls);active_queries=len(graph.queries)
    calls.clear();graph.queries.clear()
    monkeypatch.setattr(memo,'_reconstruct_original_risk_checkpoint',count)
    baseline=commits.load_verified_v4_prefix(graph.client,graph.run,first_price_source=graph.source)
    assert current==baseline
    print(dict(profit_active_calls=active_calls,profit_uncached_calls=tuple(calls),
        profit_active_queries=active_queries,profit_uncached_queries=len(graph.queries)))
    assert len(active_calls)<len(calls)

@pytest.mark.parametrize('defect',['profit_child','profit_high','entry_child'])
@pytest.mark.parametrize('paired_final',[False,True])
def test_profit_mixed_reuse_rejects_retained_hash_mutation(defect,paired_final,monkeypatch):
    from src.trading_runtime import _checkpoint_prefix_read as memo
    from src.trading_runtime import arte_journal_commit_v4 as commits
    graph=ProfitMixedCheckpointGraph();asyncio.run(simulated_three_holdings(graph))
    target=graph.checkpoints[1][2].checkpoint.source_manager_checkpoint_sequence
    original=memo._historical_digest;record=memo._record_verified_commit
    mutated=False;starts=0
    def recorded(client,source,commit):
        nonlocal starts
        record(client,source,commit)
        if commit['first_sequence']==1:starts+=1
    monkeypatch.setattr(memo,'_record_verified_commit',recorded)
    def historical(operation,client,prefix,event):
        nonlocal mutated
        digest=original(operation,client,prefix,event)
        if (prefix.last_sequence==target and digest is not None and not mutated
                and starts==(2 if paired_final else 1)):
            mutated=True
            if defect=='profit_child':
                graph.client.tables['trading_profit_giveback_v4'][0]['prior_high_int']+=1
            elif defect=='profit_high':
                from src.trading_runtime.strategy_one_management_snapshot import HIGH
                graph.client.tables[HIGH.name][0]['high_int']+=1
            else:
                graph.client.tables['trading_strategy_intent_v1'][0]['reference_price']='10.02'
        return digest
    monkeypatch.setattr(memo,'_historical_digest',historical)
    with pytest.raises((ValueError,RuntimeError)):
        if paired_final:
            with memo._checkpoint_prefix_scope(graph.client,run_id=graph.run,
                    checkpoint_sequence=graph.sequence,first_price_source=graph.source):
                pass
        else:
            commits.load_verified_v4_prefix(graph.client,graph.run,first_price_source=graph.source)
    assert mutated
    assert memo._FULL_OPERATION.get() is None
    assert memo._CHECKPOINT_OBSERVATION.get() is None


def test_invalid_source_batch_never_observed(monkeypatch):
    from src.trading_runtime import _checkpoint_prefix_read as memo
    from src.trading_runtime import arte_journal_commit_v4 as commits
    graph=ProfitMixedCheckpointGraph();asyncio.run(simulated_three_holdings(graph))
    profit=graph.client.tables['trading_profit_giveback_v4'][0]
    failed_batch=profit['batch_id'];profit['prior_high_int']+=1
    observed=[];original=memo._observe_verified_exit_source
    def observer(client,source,commit,*a):
        observed.append(commit['batch_id']);return original(client,source,commit,*a)
    monkeypatch.setattr(memo,'_observe_verified_exit_source',observer)
    with pytest.raises((ValueError,RuntimeError)):
        commits.load_verified_v4_prefix(graph.client,graph.run,first_price_source=graph.source)
    assert failed_batch not in observed
    assert memo._FULL_OPERATION.get() is None

@pytest.mark.parametrize('mode',['source_observer_disabled','entry_cap','byte_cap'])
def test_mixed_source_observation_fallback_has_frozen_output(mode,monkeypatch):
    from src.trading_runtime import _checkpoint_prefix_read as memo
    from src.trading_runtime import arte_journal_commit_v4 as commits
    graph=MixedCheckpointGraph();asyncio.run(simulated_three_holdings(graph))
    expected=commits.load_verified_v4_prefix(graph.client,graph.run,first_price_source=graph.source)
    if mode=='source_observer_disabled':
        monkeypatch.setattr(memo,'_observe_verified_exit_source',lambda *a,**kw:None)
    elif mode=='entry_cap':monkeypatch.setattr(memo,'_MEMO_ENTRY_LIMIT',1)
    else:monkeypatch.setattr(memo,'_MEMO_BYTE_LIMIT',1)
    actual=commits.load_verified_v4_prefix(graph.client,graph.run,first_price_source=graph.source)
    assert actual==expected
    assert memo._FULL_OPERATION.get() is None
    assert memo._CHECKPOINT_OBSERVATION.get() is None


def test_exit_source_observer_default_thread_budget_and_immutable_digest():
    import threading
    from contextvars import copy_context
    from src.trading_runtime import _checkpoint_prefix_read as memo
    client,source=object(),object()
    commit={'run_id':'unit','batch_id':'batch'}
    family=[{'family_name':'trading_profit_giveback_v4'}]
    details={'trading_profit_giveback_v4':[('record','digest')]}
    assert memo._observe_verified_exit_source(client,source,commit,family,details) is None
    with memo._full_prefix_operation(client,'unit',source):
        operation=memo._FULL_OPERATION.get()
        memo._observe_verified_exit_source(object(),source,commit,family,details)
        memo._observe_verified_exit_source(client,object(),commit,family,details)
        memo._observe_verified_exit_source(client,source,{**commit,'run_id':'foreign'},family,details)
        assert not operation.pool.exit_sources
        context=copy_context()
        thread=threading.Thread(target=lambda:context.run(
            memo._observe_verified_exit_source,client,source,commit,family,details))
        thread.start();thread.join()
        assert not operation.pool.exit_sources
        memo._observe_verified_exit_source(client,source,commit,family,details)
        captured=operation.pool.exit_sources['batch']
        family[0]['family_name']='changed'
        details['trading_profit_giveback_v4'][0]=('record','changed')
        assert operation.pool.exit_sources['batch']==captured
        assert type(captured) is tuple and type(captured[0]) is str
        assert operation.pool.bytes>0
    assert operation.pool.exit_sources=={} and operation.pool.bytes==0


def test_combined_pool_count_includes_exit_source_proofs(monkeypatch):
    from src.trading_runtime import _checkpoint_prefix_read as memo
    monkeypatch.setattr(memo,'_MEMO_ENTRY_LIMIT',3)
    client,source=object(),object()
    with memo._full_prefix_operation(client,'unit',source):
        op=memo._FULL_OPERATION.get()
        op.pool.memo['m']=None;op.pool.ledger['l']=None
        for index in range(4):
            memo._observe_verified_exit_source(client,source,{'run_id':'unit','batch_id':str(index)},
                ({'family_name':'trading_profit_giveback_v4'},),{'family':(('row','hash'),)})
        assert len(op.pool.exit_sources)==1
        assert len(op.pool.memo)+len(op.pool.ledger)+len(op.pool.exit_sources)==3

@pytest.mark.parametrize('reason',['strategy_75_session_exit','unrecognized_exit','strategy_75_liquidity_fade'])
def test_unsupported_exit_family_does_not_enable_checkpoint_capture(reason):
    from types import SimpleNamespace
    from src.trading_runtime import _checkpoint_prefix_read as memo
    client,source=object(),object()
    with memo._full_prefix_operation(client,'unit',source):
        memo._observe_verified_exit_source(client,source,{'run_id':'unit','batch_id':'batch'},
            ({'family_name':'trading_profit_giveback_v4'},),{'family':(('row','hash'),)})
        item=SimpleNamespace(source_intent=SimpleNamespace(intent=SimpleNamespace(action='exit',reason=reason),
            source_batch=SimpleNamespace(batch_id='batch')))
        assert memo._lineage_content((item,)) is None
