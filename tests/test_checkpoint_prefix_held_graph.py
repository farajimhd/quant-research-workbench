"""Actual held checkpoint consumers; only SQL/Keeper/producer transport is synthetic."""
from dataclasses import asdict, replace
from datetime import date
import json
import re
import struct
from uuid import UUID

import pytest

from test_checkpoint_prefix_read import bit_projection_client
from test_original_risk_pending_snapshot import genuine_entry_graph
from src.trading_runtime import arte_journal_commit_v4 as commits
from src.trading_runtime import arte_journal_writer as writer


def normalized(name, rows):
    return [writer._wire_row(name, row) for row in rows]


def held_graph():
    """Project a genuine selected entry, normalized approval and actual OMS image."""
    from src.backend.backtest_market_data import market_day_boundary
    from src.trading_runtime.arte_intent_projection import strategy_intent_batch
    from src.trading_runtime.portfolio import _intent_correlation
    from src.trading_runtime.arte_journal_projection import project_portfolio_admission_records
    from src.trading_runtime.arte_oms_projection import oms_group_state_batch, freeze_oms_group
    from src.trading_runtime.arte_oms_tactic_projection import tactic_rows
    from src.trading_runtime.order_management import (_ManagedOrderGroup, OrderManagementState,
        ExecutionQuote, ExecutionTactic, ExecutionUrgency, PriceStep)
    from src.trading_runtime.strategy_orders import StrategyOrderPlan
    from src.trading_runtime.ibkr_schema import OrderRequest
    from src.trading_runtime.portfolio import PortfolioReservation
    from tests.test_arte_journal_writer import run_row, run_context
    from src.trading_runtime.strategy_one_contract import STRATEGY_ID
    run = 'held-checkpoint-component'
    day = date(2026, 8, 18)
    # Fixture producer certificates explicitly contain all three products before
    # any entry proposal/rows exist. This is not a native Keeper certificate.
    from test_arte_entry_activity_v4 import plan as fixture_plan
    from unittest.mock import patch
    activity=fixture_plan()
    market=activity.parent.source.market
    broker_unit=replace(next(u for u in market.units if u.stage=='bars'),
        stage='broker_100ms',attempt_id=str(UUID(int=123)))
    full_market=replace(market,units=(*market.units,broker_unit))
    full_activity=replace(activity,parent=replace(activity.parent,
        source=replace(activity.parent.source,market=full_market)))
    with patch('test_arte_entry_activity_v4.plan',return_value=full_activity):
        proposal, intent, source, tables = genuine_entry_graph(run)
    client = bit_projection_client()
    original_execute = client.execute
    def execute(sql):
        # Evaluate only the closed SELECT predicates used by actual cold readers.
        # This adapter does not create verified prefixes, financial views or OMS objects.
        if not isinstance(sql, str) or not sql.startswith('SELECT '):
            return original_execute(sql)
        if ('groupArray(' in sql or 'reinterpretAsUInt64' in sql):
            return original_execute(sql)
        if 'INNER JOIN arte.trading_event_v1' in sql:
            cursor_rows = client.tables.get('trading_backtest_cursor_v1', ())
            events = {r['record_id']:r for r in client.tables['trading_event_v1']}
            rows = [{**r, 'event_sequence':events[r['record_id']]['sequence'],
                'event_category':events[r['record_id']]['category'],
                'event_entity_type':events[r['record_id']]['entity_type'],
                'event_entity_id':events[r['record_id']]['entity_id']}
                for r in cursor_rows]
            rows.sort(key=lambda r:r['event_sequence'], reverse=True)
            bound = re.search(r'(?:e\.)?sequence<=(\d+)', sql)
            if bound: rows=[r for r in rows if r['event_sequence']<=int(bound[1])]
            return '\n'.join(json.dumps(r) for r in rows[:1])
        match = re.search(r'FROM arte\.([a-z0-9_]+)', sql)
        if not match: return original_execute(sql)
        rows = [dict(r) for r in client.tables.get(match[1], ())]
        for field, value in re.findall(r"(?:WHERE|AND)\s+([a-z_]+)=(?:toUUID\(|toDate\()?\s*'([^']*)'", sql):
            rows=[r for r in rows if str(r.get(field))==value]
        for field, value in re.findall(r'(?:WHERE|AND)\s+([a-z_]+)=(\d+)', sql):
            rows=[r for r in rows if r.get(field)==int(value)]
        for field, values in re.findall(r'(?:WHERE|AND)\s+([a-z_]+) IN \((.*?)\)\s*(?=AND|ORDER|LIMIT|FORMAT|$)', sql,re.S):
            ids=set(re.findall(r"'([^']*)'", values))
            if field=='sequence': ids=set(re.findall(r'\d+',values))
            if field=='batch_id' and values.startswith('SELECT batch_id FROM arte.trading_commit_v4'):
                ceiling=re.search(r'last_sequence<=(\d+)',values)
                ids={r['batch_id'] for r in client.tables['trading_commit_v4']
                     if ceiling is None or r['last_sequence']<=int(ceiling[1])}
            rows=[r for r in rows if str(r.get(field)) in ids]
        for field,operation,bound in re.findall(r'AND (sequence|first_sequence|last_sequence)(<=|>=|>)(\d+)',sql):
            if rows and field not in rows[0]:continue  # Already evaluated committed-batch subquery.
            rows=[r for r in rows if (r[field]<=int(bound) if operation=='<=' else
                r[field]>=int(bound) if operation=='>=' else r[field]>int(bound))]
        if 'ORDER BY sequence' in sql: rows.sort(key=lambda r:r['sequence'])
        columns=sql[7:].split(' FROM ',1)[0].strip()
        if columns!='*':
            projected=[]
            for r in rows:
                item={}
                for column in columns.split(','):
                    column=column.strip()
                    alias=re.fullmatch(r'toString\((\w+)\) AS (\w+)',column)
                    if alias:item[alias[2]]=None if r[alias[1]] is None else str(r[alias[1]])
                    else:item[column]=r[column]
                projected.append(item)
            rows=projected
        limit=re.search(r'LIMIT (\d+)',sql)
        if limit: rows=rows[:int(limit[1])]
        return '\n'.join(json.dumps(r) for r in rows)
    client.execute=execute
    tables={name:normalized(name, values) for name,values in tables.items()}
    entry_commit,entry_families=commits.prepare_commit_v4(run_id=run,run_month=day.replace(day=1),
        attempt_id=str(UUID(int=3)),batch_id=str(UUID(int=1)),prior_batch_id=str(UUID(int=0)),
        first_sequence=1,last_sequence=1,source_cursor='2026-08-18:41000',status='running',
        sealed_families=tuple((name,tuple(values)) for name,values in tables.items()),
        committed_at=intent.event_time)
    client.tables.update(tables)
    client.tables['trading_commit_v4']=[entry_commit]
    client.tables['trading_commit_family_v4']=list(entry_families)
    context={**run_context(),'strategy_id':STRATEGY_ID,'strategy_revision':68}
    from tests.test_arte_journal_writer import MemoryClient
    context_transport=MemoryClient()
    writer.publish_typed_run(context_transport,{**run_row(),'run_id':run})
    writer.publish_typed_run_context(context_transport,run_id=run,config=context,account_ids=(proposal.account_id,))
    client.tables.update(context_transport.tables)
    at=market_day_boundary(day,42000)
    decision_id,reservation_id='held-decision','held-reservation'
    reservation=PortfolioReservation(reservation_id,decision_id,intent.intent_id,'cash',
        proposal.account_id,STRATEGY_ID,proposal.assignment_id,proposal.ticker,'enter_long',
        10.,10.,intent.reference_price,100.1,1.2,at)
    metrics={k:0. for k in ('net_liquidation','available_funds','buying_power','gross_exposure',
        'net_exposure','reserved_notional','open_risk','daily_loss','drawdown','position_count')}
    decision=dict(event='portfolio_decision',ticker=proposal.ticker,action='enter_long',
        decision_id=decision_id,request_id='held-request',account_key='cash',account_id=proposal.account_id,
        policy_id='component',policy_revision=1,snapshot_id='held-portfolio',status='approved',
        requested_quantity=10.,approved_quantity=10.,approved_notional=100.1,planned_loss=1.2,
        reservation_id=reservation_id,reasons=(),metrics_before=metrics,metrics_after=metrics,
        decided_at=at,correlation_id='',causation_id='')
    admission=project_portfolio_admission_records((('portfolio_decision',decision_id,proposal.account_id,decision),
        ('portfolio_reservation',reservation_id,proposal.account_id,{**asdict(reservation),
            'event':'reservation_created','correlation_id':'','causation_id':''})),
        run_id=run,run_month=day.replace(day=1),attempt_id=str(UUID(int=4)),
        batch_id=str(UUID(int=5)),prior_batch_id=str(UUID(int=1)),first_sequence=2,
        source_cursor='2026-08-18:42000')
    commits._publish_typed_batch_v4(client,admission,first_price_source=source)
    metadata=dict(assignment_id=proposal.assignment_id,portfolio_account_key='cash',
        portfolio_decision_id=decision_id,unprotected_backtest_authorized=False,
        portfolio_policy='component@1',portfolio_reservation_id=reservation_id,
        requested_quantity=10.,portfolio_fx_to_base=1.,
        correlation_id=_intent_correlation(run,intent),causation_id=decision_id)
    approved=replace(intent,quantity=10.,metadata=metadata)
    order=OrderRequest(acctId=proposal.account_id,conid=123,cOID='held-entry',ticker=proposal.ticker,
        orderType='LMT',side='BUY',quantity=10.,price=intent.reference_price)
    group=_ManagedOrderGroup('held-group',approved,proposal.account_id,StrategyOrderPlan((order,)),
        OrderManagementState.WORKING,at,at,[order],remaining_quantity=0.)
    group.tactic=ExecutionTactic(ExecutionUrgency.URGENT,'BUY',(PriceStep(0,intent.reference_price),),
        ExecutionQuote(10.,intent.reference_price,at,.01),0)
    entry_batch=strategy_intent_batch(intent,run_id=run,run_month=day.replace(day=1),
        account_id=proposal.account_id,attempt_id=str(UUID(int=3)),batch_id=str(UUID(int=1)),
        prior_batch_id=str(UUID(int=0)),sequence=1,source_cursor='2026-08-18:41000',
        run_status='running',recorded_at=intent.event_time,record_id=str(UUID(int=2)))
    oms=oms_group_state_batch(freeze_oms_group(group),run_id=run,run_month=day.replace(day=1),
        attempt_id=str(UUID(int=6)),batch_id=str(UUID(int=7)),prior_batch_id=str(UUID(int=5)),
        sequence=4,source_cursor='2026-08-18:42000',run_status='running',strategy_id=STRATEGY_ID,
        strategy_revision=68,recorded_at=at,published_intent_batch=entry_batch,
        committed_intent_batch_id=str(UUID(int=1)),admission_source_intent=intent,
        admission_reservation=asdict(reservation))
    tactic=tactic_rows(group.tactic,run_id=run,event_month='2026-08-01',batch_id=oms.batch_id,
        group_record_id=oms.events[0]['record_id'],account_id=proposal.account_id)
    prepared=commits._publish_typed_batch_v4(client,oms,oms_tactic_rows=tactic,
        first_price_source=source,_prepare_only=True)
    # Store the actual prepared families; no source or financial validator is replaced.
    families=prepared[1]
    for name,values in families:
        client.tables.setdefault(name,[]).extend(normalized(name,values))
    commit,commit_families=commits.prepare_commit_v4(run_id=run,run_month=day.replace(day=1),
        attempt_id=oms.attempt_id,batch_id=oms.batch_id,prior_batch_id=oms.prior_batch_id,
        first_sequence=4,last_sequence=4,source_cursor=oms.source_cursor,status='running',
        sealed_families=families,committed_at=at)
    client.tables['trading_commit_v4'].append(commit)
    client.tables['trading_commit_family_v4'].extend(commit_families)
    return client,run,proposal,intent,source


def test_actual_held_entry_admission_and_oms_graph_cold_reconstructs():
    from src.trading_runtime.arte_oms_projection import load_recovered_strategy_one_oms_lineage
    client,run,proposal,intent,source=held_graph()
    prefix=commits.load_verified_v4_prefix(client,run,first_price_source=source)
    lineage=load_recovered_strategy_one_oms_lineage(client,prefix,
        allowed_accounts=frozenset({proposal.account_id}),strategy_number=68,first_price_source=source)
    assert len(lineage)==1
    assert lineage[0].approved_intent.intent_id==intent.intent_id
    assert lineage[0].approved_intent.quantity==10.
    assert lineage[0].admission_reservation['assignment_id']==proposal.assignment_id


def checkpoint_graph():
    from src.backend.backtest_market_data import market_day_boundary
    from src.backend.backtest_strategy_one_management import OriginalRiskManagementState
    from src.trading_runtime.confirmed_original_risk_failure import (
        ConfirmedOriginalRiskPolicy,CompletedRiskBucket,OriginalRiskDecisionDiagnostic,
        CONFIRMED_ORIGINAL_RISK_RULE)
    from src.trading_runtime.strategy_followthrough_failure import FollowThroughFailure
    from src.trading_runtime.strategy_one_stateful import StrategyOneFinancialView
    from src.trading_runtime.strategy_engine import AssignmentStatus,StrategyPermissions
    from src.trading_runtime.strategy_one_position import ProtectionState
    from src.trading_runtime.original_risk_checkpoint import OriginalRiskCheckpointRequest
    from src.trading_runtime.arte_journal_projection import backtest_cursor_batch
    from src.trading_runtime.journal_contract import JournalRecord
    from src.trading_runtime import strategy_one_management_snapshot as manager
    from src.trading_runtime import strategy_one_broker_match_snapshot as broker
    from src.trading_runtime.strategy_one_protection_snapshot import TABLES as protections
    client,run,proposal,intent,source=held_graph()
    client.confirmed_original_risk_policy=ConfirmedOriginalRiskPolicy()
    day=date(2026,8,18);boundary=120000;at=market_day_boundary(day,boundary)
    market=source.plan.source.market
    units={u.stage:u.attempt_id for u in market.units}
    newest=CompletedRiskBucket(boundary,99800,True,.1,.2,market.build_id,market.token,
        units['bars'],units['technical'],day.isoformat(),proposal.ticker,units['broker_100ms'])
    witness=FollowThroughFailure(boundary,42000,10.01,9.89,99800,.1,.2,9.98,9.99,1000000)
    diagnostic=OriginalRiskDecisionDiagnostic(witness,newest,replace(newest,boundary_ms=115000),
        CONFIRMED_ORIGINAL_RISK_RULE)
    financial=StrategyOneFinancialView(proposal.assignment_id,proposal.account_id,proposal.ticker,
        AssignmentStatus.MANAGING,StrategyPermissions(),10.,False,False,False,1)
    request=OriginalRiskCheckpointRequest(diagnostic,financial,intent.intent_id)
    key=proposal.account_id,proposal.assignment_id,proposal.ticker
    state=OriginalRiskManagementState(boundary,((key,proposal),),
        ((key,ProtectionState(42000,proposal.initial_stop,proposal.initial_target)),),(),
        position_highs=((key,99800),),first_held_boundaries=((key,42000),),
        original_risk_requests=(request,))
    record=JournalRecord(str(UUID(int=8)),run,5,at,at,'checkpoint','market_boundary',
        f'{day}:{boundary}','',dict(session_date=day.isoformat(),boundary_ms=boundary,
        market_sequence=5,frame_as_of=None,frame_ticker=None,frame_timeframe=None,frame_sequence=None))
    cursor=backtest_cursor_batch(record,run_month=day.replace(day=1),attempt_id=str(UUID(int=9)),
        batch_id=str(UUID(int=10)),prior_batch_id=str(UUID(int=7)),source_cursor=record.entity_id)
    commits._publish_typed_batch_v4(client,cursor,first_price_source=source)
    mr=manager.project_manager_snapshot(run_id=run,session_date=day,checkpoint_sequence=5,state=state,first_price_source=source)
    from src.trading_runtime.original_risk_pending_snapshot import selected_parent_contract
    client.tables[selected_parent_contract().name]=[mr.snapshot]
    for contract,values in zip(protections,((mr.protection.snapshot,),mr.protection.states,mr.protection.resistances),strict=True):
        client.tables[contract.name]=list(values)
    from src.trading_runtime.original_risk_pending_snapshot import PENDING
    client.tables[PENDING.name]=list(mr.original_risk_requests)
    for contract,values in ((manager.SOURCE,mr.sources),(manager.BREAK,mr.pending_breaks),
            (manager.HIGH,mr.position_highs),(manager.CLOSED,mr.closed_positions),
            (manager.FIRST_HELD,mr.first_held_boundaries)):
        client.tables[contract.name]=list(values)
    broker_state=dict(schema_version=4,bar_mode=True,initial_time=market_day_boundary(day,0),
        account_ids=(proposal.account_id,),cash={proposal.account_id:9899.9},
        realized_pnl={proposal.account_id:0.},positions={proposal.account_id:[dict(conid=123,
        ticker=proposal.ticker,quantity=10.,avg_cost=10.01,realized_pnl=0.)]},orders=(),
        next_order_id=2,next_execution_id=2,performance_extrema=dict(complete=False,as_of=None,
        unrealized=0,market_value=0,peak_unrealized=0,worst_unrealized=0,equity_peak=0,maximum_drawdown=0))
    br=broker.project_broker_match_snapshot(run_id=run,session_date=day,
        checkpoint_sequence=5,boundary_ms=boundary,state=broker_state)
    for contract,values in zip(broker.TABLES,((br.snapshot,),br.accounts,br.positions,br.open_orders,br.tickers,br.marks),strict=True):
        client.tables[contract.name]=list(values)
    class Head:
        def __init__(self,value):self.value=value;self.reads=0
        def read_head(self,*,run_id):
            assert run_id==self.value.run_id
            self.reads+=1
            return self.value
    mh=Head(manager.ManagerSnapshotHead(run,5,cursor.batch_id,mr.snapshot['content_hash'],0))
    bh=Head(broker.BrokerMatchHead(run,5,cursor.batch_id,br.snapshot['content_hash'],0))
    return client,run,proposal,intent,source,state,request,mh,bh,mr,br


def test_actual_held_manager_broker_scope_and_standalone_output_parity():
    from src.trading_runtime import strategy_one_management_snapshot as manager
    from src.trading_runtime import strategy_one_broker_match_snapshot as broker
    from src.trading_runtime import _checkpoint_prefix_read as memo
    c,run,p,i,s,state,request,mh,bh,mr,br=checkpoint_graph()
    with memo._checkpoint_prefix_scope(c,run_id=run,checkpoint_sequence=5,first_price_source=s) as scope:
        a=manager._load_attested_manager_snapshot(c,mh,run_id=run,checkpoint_sequence=5,
            first_price_source=s,_scope=scope)
        b=broker._load_attested_broker_match_snapshot(c,bh,run_id=run,checkpoint_sequence=5,
            first_price_source=s,_scope=scope)
    assert a==manager.load_attested_manager_snapshot(c,mh,run_id=run,checkpoint_sequence=5,first_price_source=s)
    assert b==broker.load_attested_broker_match_snapshot(c,bh,run_id=run,checkpoint_sequence=5,first_price_source=s)
    assert a==state and b==br


def failure_graph():
    from src.trading_runtime.original_risk_checkpoint import confirm_original_risk_checkpoint_sources
    from src.backend.backtest_typed_publisher import TypedBacktestReceipt
    from src.trading_runtime.strategy_followthrough_exit import followthrough_exit_intent
    from src.trading_runtime.arte_intent_projection import strategy_intent_batch
    from src.trading_runtime.arte_followthrough_failure_v4 import project_followthrough_failure,FAILURE
    from src.trading_runtime.arte_original_risk_diagnostic_v4 import project_original_risk_diagnostic,DIAGNOSTIC
    from src.backend.backtest_confirmed_original_risk_source import REQUIRED,SOURCE_KEYS
    import polars as pl
    c,run,p,i,s,state,request,mh,bh,mr,br=checkpoint_graph()
    receipt=TypedBacktestReceipt(5,str(UUID(int=10)),'2026-08-18:120000')
    diagnostic=confirm_original_risk_checkpoint_sources(c,mh,bh,(request,),receipt,
        run_id=run,first_price_source=s)[0]
    current=request.witness
    day=date(2026,8,18)
    core=followthrough_exit_intent(current,request.financial,session_date=day,
        source_entry_intent_id=i.intent_id,diagnostic=diagnostic,strategy_number=68)
    base=strategy_intent_batch(core,run_id=run,run_month=day.replace(day=1),account_id=p.account_id,
        attempt_id=str(UUID(int=11)),batch_id=str(UUID(int=12)),prior_batch_id=str(UUID(int=10)),
        sequence=6,source_cursor=f'{day}:120000',run_status='running',recorded_at=core.event_time,
        record_id=str(UUID(int=13)))
    row=project_followthrough_failure(current,core,i.intent_id,run_id=run,batch_id=base.batch_id,
        parent_record_id=base.events[0]['record_id'],assignment_id=p.assignment_id,
        strategy_number=68,diagnostic=diagnostic)
    companion=project_original_risk_diagnostic(diagnostic,row)
    families=list(writer._sealed_families(base))
    families.extend(((FAILURE.name,(writer.typed_row(FAILURE.name,row),)),
        (DIAGNOSTIC.name,(writer.typed_row(DIAGNOSTIC.name,companion),))))
    identity={key:getattr(diagnostic.newest,key) for key in SOURCE_KEYS}
    facts=pl.DataFrame([{**identity,'boundary_ms':boundary,'close_int':99800,'price_valid':True,
        'macd_line':.1,'macd_signal':.2} for boundary in (115000,120000)],
        schema={**{k:pl.String for k in SOURCE_KEYS},'boundary_ms':pl.UInt64,'close_int':pl.UInt64,
        'price_valid':pl.Boolean,'macd_line':pl.Float64,'macd_signal':pl.Float64}).select(REQUIRED)
    producer_reads=[]
    def arrow(query):
        assert 'FROM arte.bars_v1 b LEFT JOIN arte.indicators_v1' in query
        producer_reads.append(1)
        yield from facts.to_arrow().to_batches()
    c.iter_arrow_record_batches=arrow
    old=c.execute
    def quote(sql):
        if 'FROM arte.liquidity_100ms_v1' in sql:
            from src.backend.backtest_market_data import market_day_boundary
            at=market_day_boundary(day,current.boundary_ms)
            from datetime import datetime,timezone
            elapsed=at.astimezone(timezone.utc)-datetime(1970,1,1,tzinfo=timezone.utc)
            now=(elapsed.days*86400+elapsed.seconds)*1000000+elapsed.microseconds
            return json.dumps(dict(bid_int=99800,ask_int=99900,quote_valid=1,
                quote_timestamp_us=now-current.quote_age_us))
        return old(sql)
    c.execute=quote
    for name,values in families:c.tables.setdefault(name,[]).extend(normalized(name,values))
    commit,inventory=commits.prepare_commit_v4(run_id=run,run_month=day.replace(day=1),
        attempt_id=base.attempt_id,batch_id=base.batch_id,prior_batch_id=base.prior_batch_id,
        first_sequence=6,last_sequence=6,source_cursor=base.source_cursor,status='running',
        sealed_families=tuple(families),committed_at=core.event_time)
    c.tables['trading_commit_v4'].append(commit)
    c.tables['trading_commit_family_v4'].extend(inventory)
    return c,run,s,state,mh,bh,producer_reads


def test_actual_both_diagnostic_sealers_reconstruct_once_per_fresh_full_proof(monkeypatch):
    from src.trading_runtime import original_risk_checkpoint as checkpoint
    c,run,s,state,mh,bh,producer_reads=failure_graph()
    original=checkpoint._load_original_risk_checkpoint
    reconstructed=[]
    def counted(*a,**k):
        result=original(*a,**k)
        reconstructed.append(result)
        return result
    monkeypatch.setattr(checkpoint,'_load_original_risk_checkpoint',counted)
    prefix=commits.load_verified_v4_prefix(c,run,first_price_source=s)
    assert prefix.last_sequence==6
    assert reconstructed==[state]
    assert len(producer_reads)==1  # Followthrough invokes the original-risk sealer once.
    assert commits.load_verified_v4_prefix(c,run,first_price_source=s)==prefix
    assert reconstructed==[state,state]  # New full proof never inherits first proof's memo.
    assert len(producer_reads)==2


@pytest.mark.parametrize('defect',['source_token','producer_pair','quote','manager_child','broker_child','raw_failure'])
def test_real_held_graph_fresh_final_rejects_actual_underlying_mutation(defect):
    from src.trading_runtime import _checkpoint_prefix_read as memo
    c,run,s,state,mh,bh,producer_reads=failure_graph()
    entered=False
    with pytest.raises((ValueError,RuntimeError)):
        with memo._checkpoint_prefix_scope(c,run_id=run,checkpoint_sequence=6,first_price_source=s):
            entered=True
            if defect=='source_token':object.__setattr__(s.plan,'token','f'*64)
            elif defect=='producer_pair':
                original=c.iter_arrow_record_batches
                def changed(query):
                    import polars as pl
                    for batch in original(query):
                        yield pl.from_arrow(batch).with_columns(pl.lit(99801,dtype=pl.UInt64)
                            .alias('close_int')).to_arrow().to_batches()[0]
                c.iter_arrow_record_batches=changed
            elif defect=='quote':
                original=c.execute
                def changed(sql):
                    raw=original(sql)
                    if 'FROM arte.liquidity_100ms_v1' in sql:
                        row=json.loads(raw);row['bid_int']+=1;return json.dumps(row)
                    return raw
                c.execute=changed
            elif defect=='manager_child':
                from src.trading_runtime.strategy_one_protection_snapshot import TABLES
                row=c.tables[TABLES[1].name][0]
                row['stop']=str(float(row['stop'])+.01)
            elif defect=='broker_child':
                from src.trading_runtime.strategy_one_broker_match_snapshot import POSITION
                c.tables[POSITION.name][0]['quantity_f64_bits']+=1
            else:c.tables['trading_followthrough_failure_v4'][0]['completed_close_int']+=1
    assert entered, 'The unmodified genuine held graph must pass before corruption.'


def test_real_held_graph_uncached_base_body_parity_without_replacing_consumer(monkeypatch):
    from src.trading_runtime import original_risk_checkpoint as checkpoint
    from src.trading_runtime import _checkpoint_prefix_read as memo
    c,run,s,state,*_=failure_graph()
    before=commits.load_verified_v4_prefix(c,run,first_price_source=s)
    calls=[]
    original=checkpoint._load_original_risk_checkpoint
    def uncached(*a,**k):
        result=original(*a,**k)  # Entire unchanged base reconstruction body executes.
        calls.append(result)
        return result
    monkeypatch.setattr(memo,'_reconstruct_original_risk_checkpoint',uncached)
    after=commits.load_verified_v4_prefix(c,run,first_price_source=s)
    assert before==after and calls==[state]
