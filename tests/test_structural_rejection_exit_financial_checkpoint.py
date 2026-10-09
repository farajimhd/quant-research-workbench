"""Actual Broker/OMS projection hashes; source/context/lineage readers are seams."""
from dataclasses import replace
from hashlib import sha256
import json

import pytest

from test_arte_structural_rejection_exit_v1 import prepared,PARENT
from test_profit_armed_structural_rejection_publication import BATCH
from src.backend.backtest_market_data import market_day_boundary
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
from src.trading_runtime.arte_intent_projection import RecoveredIntent
from src.trading_runtime.arte_oms_projection import RecoveredOmsGroupState,RecoveredStrategyOneOmsLineage
from src.trading_runtime.ibkr_schema import OrderRequest
from src.trading_runtime.order_management import OrderManagementState
from src.trading_runtime.strategy_one_broker_match_snapshot import project_broker_match_snapshot
from src.trading_runtime.strategy_one_oms_observation_snapshot import project_oms_observation_snapshot
from src.trading_runtime.structural_rejection_exit_financial_checkpoint import load_structural_rejection_exit_financial_checkpoint


def seal(row):
    row['content_hash']=sha256(json.dumps({k:v for k,v in row.items() if k!='content_hash'},
        sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def case(monkeypatch):
    evidence,confirmed,intent,context=prepared(monkeypatch)
    from src.trading_runtime.profit_armed_structural_rejection_publication import _bound
    from src.trading_runtime.signals import StrategyIntent
    owner=context.profile.owner;client=_bound(context)[1]
    financial=confirmed.request.financial;day=owner.manager.runtime.config.anchor_date
    row=dict(evidence.row);prefix=V4CommittedPrefix(row['run_id'],9,BATCH,'cursor','running',(BATCH,))
    state=dict(schema_version=4,bar_mode=True,initial_time=market_day_boundary(day,0),
        account_ids=('DU1',),cash={'DU1':10000},realized_pnl={'DU1':0},
        positions={'DU1':[dict(conid=123,ticker='AAA',quantity=10,avg_cost=10,realized_pnl=0)]},
        orders=(),next_order_id=1,next_execution_id=1,
        performance_extrema=dict(complete=False,as_of=None,unrealized=0,market_value=0,
            peak_unrealized=0,worst_unrealized=0,equity_peak=0,maximum_drawdown=0))
    broker=project_broker_match_snapshot(run_id=row['run_id'],session_date=day,
        checkpoint_sequence=9,boundary_ms=25000,state=state)
    oms=project_oms_observation_snapshot(run_id=row['run_id'],session_date=day,
        checkpoint_sequence=9,boundary_ms=25000,groups={})
    row.update(source_broker_snapshot_id=broker.snapshot['snapshot_id'],
        source_broker_snapshot_hash=broker.snapshot['content_hash'],
        source_oms_snapshot_id=oms.root['snapshot_id'],source_oms_snapshot_hash=oms.root['content_hash'])
    seal(row)
    parent=dict(record_id=PARENT,run_id=row['run_id'],batch_id=BATCH,account_id='DU1',
        ticker='AAA',intent_id=intent.intent_id,action='exit',reason=intent.reason,
        quantity=10,reference_price=10.4)
    event=dict(record_id=PARENT,run_id=row['run_id'],batch_id=BATCH,account_id='DU1',
        sequence=10,event_time=intent.event_time.isoformat())
    # Typed original entry stands in for the cold lineage reader here; the
    # fixture owner's price cert deliberately supplies only its UUID.
    entry=StrategyIntent(intent_id=row['source_entry_intent_id'],ticker='AAA',
        event_time=market_day_boundary(day,100),action='enter_long',quantity=10,
        reference_price=10.,invalidation_price=9.,reason='strategy_one_entry')
    source=RecoveredIntent(1,'DU1',row['source_entry_intent_id'],BATCH,entry)
    order=OrderRequest(acctId='DU1',conid=123,cOID='entry',ticker='AAA',orderType='LMT',side='BUY',quantity=10,price=10)
    group=dict(run_id=row['run_id'],batch_id=BATCH,group_id='entry',account_id='DU1',
        strategy_id=owner.manager.runtime.config.strategy_id,strategy_revision=57,
        strategy_intent_id=entry.intent_id,state=OrderManagementState.WORKING.value)
    recovered=RecoveredOmsGroupState(2,row['source_entry_intent_id'],group,(order,),(0,),('entry',),(),(),())
    lineage=[RecoveredStrategyOneOmsLineage(recovered,source,(order,),9,
        replace(entry,metadata={'assignment_id':'A1'}),{'assignment_id':'A1'})]
    run_context=dict(run_id=row['run_id'],mode='backtest',strategy_id=group['strategy_id'],
        strategy_revision=57,account_ids=('DU1',),session_date=day.isoformat(),evaluation_interval_ms=100)
    cursor=dict(run_id=row['run_id'],batch_id=BATCH,event_sequence=9,boundary_ms=25000,session_date=day.isoformat())
    portfolio=dict(state_hash='d'*64,state_revision=9,snapshot_at=intent.event_time.isoformat())
    monkeypatch.setattr('src.trading_runtime.arte_journal_writer.load_typed_run_context',lambda *a:run_context)
    monkeypatch.setattr('src.trading_runtime.arte_journal_projection.load_latest_backtest_cursor',lambda *a:cursor)
    monkeypatch.setattr('src.trading_runtime.strategy_one_broker_match_snapshot.load_unattested_broker_match_snapshot',lambda *a,**k:broker)
    monkeypatch.setattr('src.trading_runtime.arte_oms_projection.load_recovered_strategy_one_oms_lineage',lambda *a,**k:tuple(lineage))
    monkeypatch.setattr('src.trading_runtime.strategy_one_oms_observation_snapshot.load_unattested_oms_observation_snapshot',lambda *a,**k:oms)
    monkeypatch.setattr('src.trading_runtime.arte_portfolio_snapshot.load_portfolio_snapshot',lambda *a,**k:portfolio)
    args=(client,prefix,row,parent,event,financial)
    return args,dict(first_price_source=owner.price_authority),row,parent,cursor,portfolio,lineage


def test_own_exit_joins_real_broker_bits_and_all_three_financial_roots(monkeypatch):
    args,kw,row,*_=case(monkeypatch)
    proof=load_structural_rejection_exit_financial_checkpoint(*args,**kw)
    assert proof.held_quantity==10 and proof.conid==123 and proof.checkpoint_sequence==9
    assert proof.broker_snapshot_hash==row['source_broker_snapshot_hash']
    assert proof.oms_snapshot_hash==row['source_oms_snapshot_hash']
    assert proof.portfolio_state_hash=='d'*64


@pytest.mark.parametrize('kind',('quantity','reason','bid','row-hash','oms-hash','portfolio-hash',
    'portfolio-clock','portfolio-naive','portfolio-revision','cursor','entry-conid','pending-exit','foreign-price'))
def test_own_financial_join_rejects_claims_that_differ_from_native_truth(monkeypatch,kind):
    args,kw,row,parent,cursor,portfolio,lineage=case(monkeypatch)
    if kind=='quantity': parent['quantity']=11
    elif kind=='reason': parent['reason']='foreign'
    elif kind=='bid': parent['reference_price']=11
    elif kind=='row-hash': row['content_hash']='0'*64
    elif kind=='oms-hash': row['source_oms_snapshot_hash']='0'*64;seal(row)
    elif kind=='portfolio-hash': portfolio['state_hash']='0'*64
    elif kind=='portfolio-clock': portfolio['snapshot_at']=portfolio['snapshot_at'].replace('25','26')
    elif kind=='portfolio-naive': portfolio['snapshot_at']=portfolio['snapshot_at'][:19]
    elif kind=='portfolio-revision': portfolio['state_revision']=9.0
    elif kind=='cursor': cursor['boundary_ms']=25100
    elif kind=='entry-conid': lineage[0]=replace(lineage[0],orders=(replace(lineage[0].orders[0],conid=456),))
    elif kind=='foreign-price': kw['first_price_source']=object()
    else:
        old=lineage[0];exit_intent=replace(old.source_intent.intent,intent_id='pending',action='exit')
        group={**old.state.group,'group_id':'exit','strategy_intent_id':'pending'}
        lineage.append(replace(old,state=replace(old.state,group=group),
            source_intent=replace(old.source_intent,record_id='pending',intent=exit_intent),
            approved_intent=replace(exit_intent,metadata={'assignment_id':'A1'})))
    with pytest.raises((ValueError,RuntimeError)):
        load_structural_rejection_exit_financial_checkpoint(*args,**kw)
