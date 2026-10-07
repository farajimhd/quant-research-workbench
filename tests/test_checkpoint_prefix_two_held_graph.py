"""Two-account causal broker/OMS flow for historical checkpoint ancestry probes."""
import asyncio
from dataclasses import asdict,replace
from datetime import date, timedelta

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
from test_simulated_broker_liquidity_bar import bar


async def simulated_two_holdings(checkpoint_callback):
    """Actual fills/exit/protection lifecycle; no assigned positions or mocked OMS."""
    c,run,p,i,s,state,request,*_=checkpoint_graph()
    day=date(2026,8,18)
    initial=market_day_boundary(day,0)
    broker=SimulatedBrokerAdapter(['DU1','DU2'],mode=TradingMode.BACKTEST,
        initial_time=initial,fixed_bar_mode=True)
    await broker.initialize()
    journal=BacktestMemoryJournal(run_id=run)
    risk=RiskAuthority()
    await risk.prime(broker,['DU1','DU2'])
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
        for account in ('DU1','DU2'):
            from src.backend.backtest_strategy_episode_activity_source import certified_episode_entry_intent
            assignment=p.assignment_id if account=='DU1' else f'assignment-{account}'
            core=certified_episode_entry_intent(s,replace(p,account_id=account,
                assignment_id=assignment),session_date=day)
            approved=approve(core,account)
            group=await manager.submit_intent(approved,account_id=account,event=None)
            entries.append(group)
        fills=await advance(42000,10.,10.01)
        assert len(fills)==2
        assert all(broker.position_quantity(account,123,'AAA')==10. for account in ('DU1','DU2'))
        held=broker.broker_match_snapshot_state()
        await advance(120000,9.98,9.99)
        # Actual causal risk exit factory; the source ID is tied to the first
        # entry. Native durability wiring is evaluated separately below.
        from src.trading_runtime.strategy_followthrough_exit import followthrough_exit_intent
        diagnostic=checkpoint_callback(c,run,p,i,s,broker,manager,journal,120000,'DU1')
        exit_core=followthrough_exit_intent(diagnostic.current,request.financial,session_date=day,
            source_entry_intent_id=entries[0].intent_id,diagnostic=diagnostic,strategy_number=68)
        exit_approved=approve(exit_core,'DU1')
        exited=await manager.submit_intent(exit_approved,account_id='DU1',event=None)
        fills=await advance(120100,9.98,9.99)
        assert len(fills)==1 and fills[0].side=='S'
        assert broker.position_quantity('DU1',123,'AAA')==0.
        assert broker.position_quantity('DU2',123,'AAA')==10.
        await advance(125000,9.98,9.99)
        later=broker.broker_match_snapshot_state()
        checkpoint_callback(c,run,p,i,s,broker,manager,journal,125000,'DU2')
        checkpoint_callback.order_outcomes=tuple(dict(account=o.request.acctId,
            side=o.request.side,order_type=o.request.orderType,status=str(o.status),
            filled=o.filled,oca_group=o.oca_group,parent_id=o.request.parentId,
            single_group=o.request.isSingleGroup) for o in broker._orders.values())
        return held,later,journal.records(run),tuple(manager._groups.values())
    finally:
        await manager.close()
        journal.close()


class TwoCheckpointGraph:
    """Actual immutable row producers and cold consumers over synthetic SQL transport."""
    def __init__(self):
        self.sequence=0;self.batch_id='00000000-0000-0000-0000-000000000000'
        self.entries={};self.checkpoints=[];self.liquidity={};self.failures={};self.queries=[]

    def append(self,families,*,cursor,at):
        from uuid import UUID
        from src.trading_runtime import arte_journal_commit_v4 as commits
        from src.trading_runtime import arte_journal_writer as writer
        from test_checkpoint_prefix_held_graph import normalized
        events=next(values for name,values in families if name=='trading_event_v1')
        batch_id=events[0]['batch_id'];attempt_id=events[0]['attempt_id']
        first=events[0]['sequence'];last=events[-1]['sequence']
        assert first==self.sequence+1
        for name,values in families:self.client.tables.setdefault(name,[]).extend(normalized(name,values))
        row,inventory=commits.prepare_commit_v4(run_id=self.run,run_month=date(2026,8,1),
            attempt_id=attempt_id,batch_id=batch_id,prior_batch_id=self.batch_id,first_sequence=first,
            last_sequence=last,source_cursor=cursor,status='running',sealed_families=families,committed_at=at)
        self.client.tables.setdefault('trading_commit_v4',[]).append(row)
        self.client.tables.setdefault('trading_commit_family_v4',[]).extend(inventory)
        self.sequence=last;self.batch_id=batch_id

    def identity(self):
        from uuid import UUID
        return dict(run_id=self.run,run_month=date(2026,8,1),attempt_id=str(UUID(int=200+self.sequence)),
            batch_id=str(UUID(int=300+self.sequence)),prior_batch_id=self.batch_id)

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
                'macd_line':.1,'macd_signal':.2} for b in (115000,120000,125000) if b<=cutoff],
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

    def append_failure(self,request,diagnostic):
        from src.trading_runtime.strategy_followthrough_exit import followthrough_exit_intent
        from src.trading_runtime.arte_intent_projection import strategy_intent_batch
        from src.trading_runtime.arte_followthrough_failure_v4 import project_followthrough_failure,FAILURE
        from src.trading_runtime.arte_original_risk_diagnostic_v4 import project_original_risk_diagnostic,DIAGNOSTIC
        from src.trading_runtime import arte_journal_writer as writer
        from uuid import UUID
        core=followthrough_exit_intent(diagnostic.current,request.financial,session_date=date(2026,8,18),
            source_entry_intent_id=request.source_entry_intent_id,diagnostic=diagnostic,strategy_number=68)
        base=strategy_intent_batch(core,account_id=request.financial.account_id,sequence=self.sequence+1,
            source_cursor=f'2026-08-18:{diagnostic.current.boundary_ms}',run_status='running',
            recorded_at=core.event_time,record_id=str(UUID(int=600+self.sequence)),**self.identity())
        row=project_followthrough_failure(diagnostic.current,core,request.source_entry_intent_id,run_id=self.run,
            batch_id=base.batch_id,parent_record_id=base.events[0]['record_id'],
            assignment_id=request.financial.assignment_id,strategy_number=68,diagnostic=diagnostic)
        companion=project_original_risk_diagnostic(diagnostic,row)
        self.append((*writer._sealed_families(base),(FAILURE.name,(writer.typed_row(FAILURE.name,row),)),
            (DIAGNOSTIC.name,(writer.typed_row(DIAGNOSTIC.name,companion),))),cursor=base.source_cursor,at=core.event_time)
        self.failures[request.financial.account_id]=(core,base,request)

    def append_first_exit_outcome(self,manager,journal):
        from src.trading_runtime import arte_journal_writer as writer
        from src.trading_runtime.arte_journal_projection import project_portfolio_admission_records
        from src.trading_runtime.arte_oms_projection import oms_group_state_batch,freeze_oms_group
        from src.trading_runtime.arte_oms_tactic_projection import tactic_rows,PARENT_TABLE,STEP_TABLE
        core,base,request=self.failures['DU1']
        group=next(g for g in manager._groups.values() if g.intent.intent_id==core.intent_id)
        reservation=journal.portfolio_admission_reservation('DU1',group.intent.metadata['portfolio_reservation_id'])
        metrics={k:0. for k in ('net_liquidation','available_funds','buying_power','gross_exposure','net_exposure',
            'reserved_notional','open_risk','daily_loss','drawdown','position_count')}
        decision=dict(event='portfolio_decision',ticker='AAA',action='exit',decision_id=reservation['decision_id'],
            request_id='component-exit',account_key='cash',account_id='DU1',policy_id='component',policy_revision=1,
            snapshot_id='component-exit-admission',status='approved',requested_quantity=10.,approved_quantity=10.,
            approved_notional=100.,planned_loss=1.,reservation_id=reservation['reservation_id'],reasons=(),
            metrics_before=metrics,metrics_after=metrics,decided_at=core.event_time,correlation_id='',causation_id='')
        admission=project_portfolio_admission_records((('portfolio_decision',reservation['decision_id'],'DU1',decision),
            ('portfolio_reservation',reservation['reservation_id'],'DU1',{**reservation,'event':'reservation_created'})),
            first_sequence=self.sequence+1,source_cursor='2026-08-18:120000',**self.identity())
        self.append(writer._sealed_families(admission),cursor=admission.source_cursor,at=core.event_time)
        for account,source_core,source_batch,live in (
                ('DU1',self.entries['DU1'][1],self.entries['DU1'][2],self.entries['DU1'][3]),
                ('DU1',core,base,group)):
            reservation=journal.portfolio_admission_reservation(account,live.intent.metadata['portfolio_reservation_id'])
            oms=oms_group_state_batch(freeze_oms_group(live),sequence=self.sequence+1,
                source_cursor='2026-08-18:120100',run_status='running',strategy_id=STRATEGY_ID,strategy_revision=68,
                recorded_at=live.updated_at,published_intent_batch=source_batch,committed_intent_batch_id=source_batch.batch_id,
                admission_source_intent=source_core,admission_reservation=reservation,**self.identity())
            tr,steps=tactic_rows(live.tactic,group_record_id=oms.events[0]['record_id'],run_id=self.run,
                event_month='2026-08-01',batch_id=oms.batch_id,account_id=account)
            self.append((*writer._sealed_families(oms),(PARENT_TABLE,(tr,)),(STEP_TABLE,steps)),
                cursor=oms.source_cursor,at=live.updated_at)

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
        for account in ('DU1','DU2'):
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
        for account in ('DU1','DU2'):
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
        for account in ('DU1','DU2'):
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
                'strategy_id':STRATEGY_ID,'strategy_revision':68},account_ids=('DU1','DU2'))
            c.tables.update(context.tables)
            self.initial_entries(p,i,s,manager,journal)
            self.producer_transport()
        else:self.append_first_exit_outcome(manager,journal)
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
            position_highs=tuple(sorted((keys[a],100100) for a,_ in held)),
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


def test_actual_two_holdings_first_risk_exit_fills_and_keeps_other_holding():
    graph=TwoCheckpointGraph()
    held,later,records,groups=asyncio.run(simulated_two_holdings(graph))
    assert later['positions']['DU1'][0]['quantity']==0.
    assert later['positions']['DU2'][0]['quantity']==10.
    assert len(graph.checkpoints)==2
    assert [item[0].boundary_ms for item in graph.checkpoints]==[120000,125000]
    cancelled=[o for o in graph.order_outcomes if o['account']=='DU1' and o['status']=='Cancelled']
    assert len(cancelled)==2 and {o['order_type'] for o in cancelled}=={'LMT','STP'}
    assert cancelled[0]['parent_id'] and cancelled[0]['parent_id']==cancelled[1]['parent_id']
    assert all(o['single_group'] for o in cancelled)
    # Native bracket sibling cancellation is real; this broker route has no
    # separately assigned OCA-group label, which must not be invented.
    assert all(o['filled']==0. for o in cancelled)
    assert len([o for o in graph.order_outcomes if o['account']=='DU1'
        and o['side']=='SELL' and o['status']=='Filled' and o['filled']==10.])==1
    assert len([o for o in graph.order_outcomes if o['account']=='DU2'
        and o['side']=='SELL' and o['status']=='Submitted'])==2


def test_actual_two_failure_ancestry_counts_and_uncached_output_parity(monkeypatch):
    from src.trading_runtime import arte_journal_commit_v4 as commits
    from src.trading_runtime import original_risk_checkpoint as checkpoint
    from src.trading_runtime import _checkpoint_prefix_read as memo
    graph=TwoCheckpointGraph()
    asyncio.run(simulated_two_holdings(graph))
    calls=[]
    original=checkpoint._load_original_risk_checkpoint
    def counted(client,prefix,failure,parent,event,diagnostic,**kw):
        result=original(client,prefix,failure,parent,event,diagnostic,**kw)
        calls.append(dict(checkpoint=diagnostic.checkpoint.source_manager_checkpoint_sequence,
            predecessor_sequence=prefix.last_sequence,predecessor_batches=prefix.batch_ids,
            event_sequence=event['sequence'],boundary=diagnostic.current.boundary_ms,state=result))
        return result
    monkeypatch.setattr(checkpoint,'_load_original_risk_checkpoint',counted)
    graph.queries.clear()
    active=commits.load_verified_v4_prefix(graph.client,graph.run,first_price_source=graph.source)
    active_calls=list(calls);active_queries=len(graph.queries)
    calls.clear();graph.queries.clear()
    monkeypatch.setattr(memo,'_reconstruct_original_risk_checkpoint',counted)
    base=commits.load_verified_v4_prefix(graph.client,graph.run,first_price_source=graph.source)
    assert active==base
    assert active_calls==[calls[0],calls[-1]]
    assert len(graph.queries)>active_queries
    assert len(active_calls)==2
    assert len(calls)==3
    assert [r['boundary'] for r in calls].count(120000)==2
    assert [r['boundary'] for r in calls].count(125000)==1
    assert active_queries>0


import pytest

@pytest.mark.parametrize('defect',['producer_pair','raw_failure','broker_child'])
def test_two_failure_graph_fresh_final_rejects_mutation(defect):
    import json
    import polars as pl
    from src.trading_runtime import _checkpoint_prefix_read as memo
    graph=TwoCheckpointGraph()
    asyncio.run(simulated_two_holdings(graph))
    entered=False
    with pytest.raises((ValueError,RuntimeError)):
        with memo._checkpoint_prefix_scope(graph.client,run_id=graph.run,
                checkpoint_sequence=graph.sequence,first_price_source=graph.source):
            entered=True
            if defect=='producer_pair':
                original=graph.client.iter_arrow_record_batches
                def changed(query):
                    for batch in original(query):
                        yield pl.from_arrow(batch).with_columns(pl.lit(99801,dtype=pl.UInt64)
                            .alias('close_int')).to_arrow().to_batches()[0]
                graph.client.iter_arrow_record_batches=changed
            elif defect=='raw_failure':
                graph.client.tables['trading_followthrough_failure_v4'][0]['completed_close_int']+=1
            else:
                from src.trading_runtime.strategy_one_broker_match_snapshot import POSITION
                graph.client.tables[POSITION.name][0]['quantity_f64_bits']+=1
    assert entered


@pytest.mark.parametrize('paired_final',[False,True])
@pytest.mark.parametrize('defect',['manager_child','broker_child','raw_failure','producer_pair','quote'])
def test_historical_hit_rejects_mutation_after_first_checkpoint_proof(monkeypatch,paired_final,defect):
    import json
    import polars as pl
    from src.trading_runtime import _checkpoint_prefix_read as memo
    from src.trading_runtime import arte_journal_commit_v4 as commits
    graph=TwoCheckpointGraph()
    asyncio.run(simulated_two_holdings(graph))
    original=memo._record_verified_commit
    starts=0;mutated=False;entered=False
    def record(client,source,commit):
        nonlocal starts,mutated
        original(client,source,commit)
        if commit['first_sequence']==1:starts+=1
        if (commit['last_sequence']!=10 or mutated
                or starts!=(2 if paired_final else 1)):
            return
        mutated=True
        if defect=='manager_child':
            from src.trading_runtime.strategy_one_protection_snapshot import TABLES
            row=graph.client.tables[TABLES[1].name][0]
            row['stop']=str(float(row['stop'])+.01)
        elif defect=='broker_child':
            from src.trading_runtime.strategy_one_broker_match_snapshot import POSITION
            graph.client.tables[POSITION.name][0]['quantity_f64_bits']+=1
        elif defect=='raw_failure':
            graph.client.tables['trading_followthrough_failure_v4'][0]['completed_close_int']+=1
        elif defect=='producer_pair':
            reader=graph.client.iter_arrow_record_batches
            def changed(query):
                for batch in reader(query):
                    yield pl.from_arrow(batch).with_columns(pl.lit(99801,dtype=pl.UInt64)
                        .alias('close_int')).to_arrow().to_batches()[0]
            graph.client.iter_arrow_record_batches=changed
        else:
            reader=graph.client.execute
            def changed(query):
                raw=reader(query)
                if 'FROM arte.liquidity_100ms_v1' in query:
                    row=json.loads(raw);row['bid_int']+=1;return json.dumps(row)
                return raw
            graph.client.execute=changed
    monkeypatch.setattr(memo,'_record_verified_commit',record)
    with pytest.raises((ValueError,RuntimeError)):
        if paired_final:
            with memo._checkpoint_prefix_scope(graph.client,run_id=graph.run,
                    checkpoint_sequence=graph.sequence,first_price_source=graph.source):
                entered=True
        else:
            commits.load_verified_v4_prefix(graph.client,graph.run,first_price_source=graph.source)
    assert mutated and entered==paired_final
    assert memo._FULL_OPERATION.get() is None
    assert memo._CHECKPOINT_OBSERVATION.get() is None


@pytest.mark.parametrize('field',['run_id','last_sequence','last_batch_id','status','source_cursor','batch_ids'])
def test_actual_historical_resolution_crossed_prefix_falls_back_to_original(field,monkeypatch):
    from src.trading_runtime import _checkpoint_prefix_read as memo
    from src.trading_runtime import arte_journal_commit_v4 as commits
    graph=TwoCheckpointGraph()
    asyncio.run(simulated_two_holdings(graph))
    original=memo._historical_digest
    observed=[]
    def resolve(operation,client,prefix,event):
        values={'run_id':'foreign','last_sequence':True,'last_batch_id':'foreign',
            'status':'completed','source_cursor':'foreign','batch_ids':prefix.batch_ids[::-1]}
        changed=replace(prefix,**{field:values[field]})
        assert original(operation,client,changed,event) is None
        observed.append(True)
        return original(operation,client,prefix,event)
    monkeypatch.setattr(memo,'_historical_digest',resolve)
    assert commits.load_verified_v4_prefix(graph.client,graph.run,
        first_price_source=graph.source).last_sequence==16
    assert observed


@pytest.mark.parametrize('kind',['entry_cap','byte_cap','no_observation'])
def test_actual_historical_budget_or_missing_observation_preserves_full_reader(kind,monkeypatch):
    from src.trading_runtime import _checkpoint_prefix_read as memo,original_risk_checkpoint as checkpoint
    from src.trading_runtime import arte_journal_commit_v4 as commits
    graph=TwoCheckpointGraph()
    asyncio.run(simulated_two_holdings(graph))
    if kind=='entry_cap':monkeypatch.setattr(memo,'_MEMO_ENTRY_LIMIT',1)
    elif kind=='byte_cap':monkeypatch.setattr(memo,'_MEMO_BYTE_LIMIT',1)
    else:monkeypatch.setattr(memo,'_observe_checkpoint_completed',lambda *a,**k:None)
    original=checkpoint._load_original_risk_checkpoint
    calls=[]
    def read(*a,**k):
        result=original(*a,**k);calls.append(result);return result
    monkeypatch.setattr(checkpoint,'_load_original_risk_checkpoint',read)
    assert commits.load_verified_v4_prefix(graph.client,graph.run,
        first_price_source=graph.source).last_sequence==16
    assert len(calls)==3
    assert memo._CHECKPOINT_OBSERVATION.get() is None


@pytest.mark.parametrize('defect',['prior_commit','source_commit','oms_child','admission','decision','manager_root','broker_root'])
def test_historical_hit_rejects_raw_mutation_after_source_batch_proof(defect,monkeypatch):
    from src.trading_runtime import _checkpoint_prefix_read as memo
    from src.trading_runtime import arte_journal_commit_v4 as commits
    graph=TwoCheckpointGraph();asyncio.run(simulated_two_holdings(graph))
    original=memo._record_verified_commit
    mutated=False
    def record(client,source,commit):
        nonlocal mutated
        original(client,source,commit)
        if commit['last_sequence']!=10 or mutated:return
        mutated=True
        if defect in ('prior_commit','source_commit'):
            seq=9 if defect=='prior_commit' else 10
            row=next(r for r in graph.client.tables['trading_commit_v4'] if r['last_sequence']==seq)
            row['source_cursor']='mutated'
        elif defect=='oms_child':graph.client.tables['trading_oms_order_state_v1'][0]['quantity']='11'
        elif defect=='admission':graph.client.tables['trading_portfolio_reservation_event_v1'][0]['quantity']='11'
        elif defect=='decision':graph.client.tables['trading_portfolio_decision_v1'][0]['approved_quantity']='11'
        elif defect=='manager_root':
            from src.trading_runtime.original_risk_pending_snapshot import selected_parent_contract
            graph.client.tables[selected_parent_contract().name][0]['boundary_ms']+=100
        else:
            from src.trading_runtime.strategy_one_broker_match_snapshot import ROOT
            graph.client.tables[ROOT.name][0]['boundary_ms']+=100
    monkeypatch.setattr(memo,'_record_verified_commit',record)
    with pytest.raises((ValueError,RuntimeError)):
        commits.load_verified_v4_prefix(graph.client,graph.run,first_price_source=graph.source)
    assert mutated
    assert memo._FULL_OPERATION.get() is None
