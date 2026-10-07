"""Native diagnostic consumers with external producer reads explicitly faked."""
import json
from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import pytest

from src.backend.backtest_market_data import SESSION_OPEN_OFFSET_MS, market_day_boundary
from src.trading_runtime.arte_original_risk_diagnostic_v4 import (
    _verify_current_quote_source, seal_original_risk_diagnostics,
)
from src.trading_runtime.confirmed_original_risk_failure import (
    CompletedRiskBucket, OriginalRiskDecisionDiagnostic, CONFIRMED_ORIGINAL_RISK_RULE,
)
from src.trading_runtime.strategy_followthrough_failure import FollowThroughFailure


def diagnostic():
    newest=CompletedRiskBucket(100000,97500,True,.1,.2,'build','a'*64,
        '00000000-0000-0000-0000-000000000001',
        '00000000-0000-0000-0000-000000000002','2026-01-01','TEST',
        '00000000-0000-0000-0000-000000000003')
    current=FollowThroughFailure(100000,20000,10.,9.,97500,.1,.2,9.75,9.76,1000000)
    return OriginalRiskDecisionDiagnostic(current,newest,replace(newest,boundary_ms=95000),
                                          CONFIRMED_ORIGINAL_RISK_RULE)


class ProducerRead:
    def __init__(self,rows):self.rows=rows;self.sql=[]
    def execute(self,sql):
        assert sql.lstrip().startswith('SELECT')
        self.sql.append(sql)
        return '\n'.join(json.dumps(row) for row in self.rows)


def quote_context():
    diag=diagnostic()
    market=SimpleNamespace(build_id='build',units=(SimpleNamespace(stage='broker_100ms',
        ticker='TEST',session_date='2026-01-01',
        attempt_id=diag.newest.source_liquidity_attempt_id),))
    clock=int(market_day_boundary(date(2026,1,1),100000).timestamp()*1000000)
    return diag,market,dict(bid_int=97500,ask_int=97600,quote_valid=1,
                          quote_timestamp_us=clock-1000000)


def test_native_quote_read_uses_exact_completed_100ms_bucket():
    diag,market,row=quote_context();client=ProducerRead([row])
    _verify_current_quote_source(client,diag,market)
    bucket=(SESSION_OPEN_OFFSET_MS+diag.current.boundary_ms)//100-1
    assert f'AND bucket_index={bucket} LIMIT 2' in client.sql[0]
    assert (bucket+1)*100-SESSION_OPEN_OFFSET_MS==diag.current.boundary_ms
    assert diag.newest.source_liquidity_attempt_id in client.sql[0]


@pytest.mark.parametrize('change',[{'bid_int':97501},{'ask_int':97601},
    {'quote_valid':0},{'quote_timestamp_us':0},{'bid_int':True}])
def test_native_quote_read_rejects_contemporaneous_fact_tamper(change):
    diag,market,row=quote_context();row.update(change)
    with pytest.raises(ValueError):_verify_current_quote_source(ProducerRead([row]),diag,market)


@pytest.mark.parametrize('count',[0,2])
def test_native_quote_read_requires_unique_source_row(count):
    diag,market,row=quote_context()
    with pytest.raises(ValueError,match='missing or ambiguous'):
        _verify_current_quote_source(ProducerRead([row]*count),diag,market)


def test_native_sealer_rejects_duplicate_selected_parent_before_mapping():
    failure={'strategy_number':68,'parent_record_id':'parent'}
    with pytest.raises(ValueError,match='repeats selected parent'):
        seal_original_risk_diagnostics(None,(),(failure,failure),())


def test_native_sealer_rejects_duplicate_intent_authority_before_mapping():
    with pytest.raises(ValueError,match='intent inventory repeats'):
        seal_original_risk_diagnostics(None,(),(),({'record_id':'parent'},{'record_id':'parent'}))


def native_unit():
    from uuid import UUID
    from src.trading_runtime.strategy_one_stateful import StrategyOneFinancialView
    from src.trading_runtime.strategy_engine import AssignmentStatus,StrategyPermissions
    from src.trading_runtime.strategy_followthrough_exit import followthrough_exit_intent
    from src.trading_runtime.arte_intent_projection import strategy_intent_batch
    from src.trading_runtime.arte_followthrough_failure_v4 import project_followthrough_failure,V4FollowThroughFailureBatch
    from src.trading_runtime.arte_original_risk_diagnostic_v4 import project_original_risk_diagnostic
    from src.trading_runtime.arte_journal_writer import _sealed_families
    diag=diagnostic();w=diag.current
    financial=StrategyOneFinancialView('assignment-1','DU1','TEST',AssignmentStatus.WATCHING,
        StrategyPermissions(observe=True,enter=True),10.,False,False,False,1)
    source_id=str(UUID(int=77))
    intent=followthrough_exit_intent(w,financial,session_date=date(2026,1,1),
        source_entry_intent_id=source_id,strategy_number=68,diagnostic=diag)
    base=strategy_intent_batch(intent,run_id=str(UUID(int=1)),run_month=date(2026,1,1),
        account_id='DU1',attempt_id=str(UUID(int=2)),batch_id=str(UUID(int=3)),
        prior_batch_id=str(UUID(int=0)),sequence=2,source_cursor='cursor',
        run_status='running',recorded_at=intent.event_time)
    row=project_followthrough_failure(w,intent,source_id,run_id=base.run_id,batch_id=base.batch_id,
        parent_record_id=base.events[0]['record_id'],assignment_id='assignment-1',strategy_number=68,diagnostic=diag)
    companion=project_original_risk_diagnostic(diag,row)
    families=dict(_sealed_families(base))
    source={**families['trading_strategy_intent_v1'][0],'record_id':str(UUID(int=10)),
        'intent_id':source_id,'action':'enter_long','reason':'strategy_one_entry',
        'reference_price':w.reference_ask,'invalidation_price':w.initial_stop}
    source_event={**base.events[0],'record_id':source['record_id'],'sequence':1}
    entry=dict(parent_record_id=source['record_id'],strategy_number=68,
        assignment_id='assignment-1',boundary_ms=w.first_held_boundary_ms-100)
    return V4FollowThroughFailureBatch(base,row,companion),diag,source,source_event,entry,intent


class NativeProducerRead(ProducerRead):
    def __init__(self,diag,frame):
        _,_,quote=quote_context()
        super().__init__([quote]);self.frame=frame
    def iter_arrow_record_batches(self,sql):
        self.sql.append(sql)
        assert 'FROM arte.bars_v1' in sql and 'FORMAT ArrowStream' in sql
        yield from self.frame.to_arrow().to_batches()


def cold_source(diag):
    from test_confirmed_original_risk_source import plan,frame
    from src.backend.backtest_confirmed_original_risk_source import REQUIRED
    p=replace(plan(),token=diag.newest.source_market_plan_token)
    f=frame((95000,100000)).with_columns(
        __import__('polars').lit(p.token).alias('source_market_plan_token'))
    authority=SimpleNamespace(plan=SimpleNamespace(source=SimpleNamespace(market=p)))
    return NativeProducerRead(diag,f.select(REQUIRED)),authority


def test_native_sealer_recomputes_completed_pair_and_preserves_original_anchors():
    from src.trading_runtime.arte_followthrough_failure_v4 import seal_followthrough_rows
    from src.trading_runtime.arte_journal_writer import _sealed_families
    unit,diag,source,event,entry,_=native_unit()
    client,authority=cold_source(diag);families=dict(_sealed_families(unit.base))
    sealed=seal_followthrough_rows(client,(unit.failure,),
        (source,*families['trading_strategy_intent_v1']),(event,*unit.base.events),(entry,),
        diagnostic_rows=(unit.diagnostic,),first_price_source=authority)
    assert sealed[0]['reference_ask']==10. and sealed[0]['initial_stop']==9.
    assert len(client.sql)==2


@pytest.mark.parametrize('change',['missing','duplicate','prior','source','anchor','assignment'])
def test_native_sealer_rejects_missing_foreign_tampered_companion_or_original_parent(change):
    from src.trading_runtime.arte_followthrough_failure_v4 import seal_followthrough_rows
    from src.trading_runtime.arte_journal_writer import _sealed_families
    unit,diag,source,event,entry,_=native_unit();client,authority=cold_source(diag)
    companion=dict(unit.diagnostic);rows=(companion,)
    if change=='missing':rows=()
    elif change=='duplicate':rows=(companion,companion)
    elif change=='prior':companion['prior_close_int']-=1
    elif change=='source':companion['source_market_plan_token']='b'*64
    elif change=='anchor':source={**source,'reference_price':10.01}
    else:entry={**entry,'assignment_id':'foreign'}
    families=dict(_sealed_families(unit.base))
    with pytest.raises((ValueError,RuntimeError)):
        seal_followthrough_rows(client,(unit.failure,),
            (source,*families['trading_strategy_intent_v1']),(event,*unit.base.events),(entry,),
            diagnostic_rows=rows,first_price_source=authority)


def test_oms_scalar_requires_completed_diagnostic_and_preserves_assignment_quantity():
    from src.trading_runtime.arte_oms_projection import _approved_strategy_one_oms_intent
    unit,diag,_,_,_,intent=native_unit()
    group=SimpleNamespace(sequence=2,group=dict(account_id='DU1',strategy_revision=68,group_id='group'))
    source=SimpleNamespace(intent=intent,record_id=unit.failure['parent_record_id'],batch_id=unit.base.batch_id)
    history=SimpleNamespace(run_id=unit.base.run_id,records=(),through_sequence=2)
    reservation=dict(account_id='DU1',intent_id=intent.intent_id,reservation_id='r',decision_id='d',
        account_key='cash',assignment_id='assignment-1',quantity=10.)
    decision=dict(reservation_id='r',decision_id='d',account_key='cash',status='approved',
        policy_id='policy',policy_revision=1,requested_quantity=10.)
    approved,_=_approved_strategy_one_oms_intent(group,source,history,reservation,decision,
        followthrough_row=unit.failure,original_risk_diagnostic=diag)
    assert approved.quantity==10. and approved.metadata['assignment_id']=='assignment-1'
    for bad in (None,replace(diag,newest=replace(diag.newest,boundary_ms=105000))):
        with pytest.raises(ValueError):
            _approved_strategy_one_oms_intent(group,source,history,reservation,decision,
                followthrough_row=unit.failure,original_risk_diagnostic=bad)


@pytest.mark.parametrize('change',[None,'missing','duplicate','foreign_batch','tamper'])
def test_cold_scalar_reads_exact_committed_companion_hash(change):
    from src.trading_runtime.arte_followthrough_failure_v4 import FAILURE,load_followthrough_failure
    from src.trading_runtime.arte_original_risk_diagnostic_v4 import DIAGNOSTIC
    from src.trading_runtime.arte_journal_writer import typed_row
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
    unit,diag,*_=native_unit()
    failure=typed_row(FAILURE.name,dict(unit.failure))
    companion=typed_row(DIAGNOSTIC.name,dict(unit.diagnostic))
    companions=[companion]
    if change=='missing':companions=[]
    elif change=='duplicate':companions*=2
    elif change=='foreign_batch':companion['batch_id']='00000000-0000-0000-0000-000000000099'
    elif change=='tamper':companion['prior_close_int']-=1
    class Read(ProducerRead):
        def execute(self,sql):
            self.sql.append(sql)
            rows=companions if DIAGNOSTIC.name in sql else [failure]
            return '\n'.join(json.dumps(row) for row in rows)
    prefix=V4CommittedPrefix(unit.base.run_id,2,unit.base.batch_id,'cursor','running',(unit.base.batch_id,))
    client=Read([])
    if change:
        with pytest.raises((ValueError,RuntimeError)):
            load_followthrough_failure(client,prefix,unit.failure['parent_record_id'],include_diagnostic=True)
    else:
        _,restored,observed=load_followthrough_failure(client,prefix,unit.failure['parent_record_id'],include_diagnostic=True)
        assert restored==diag.current and observed==diag
