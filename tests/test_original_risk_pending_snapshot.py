"""Actual snapshot/dispatch consumers over synthetic compacted-prefix authority."""
import json
from dataclasses import replace
from datetime import date

import pytest

from src.backend.backtest_strategy_one_management import OriginalRiskManagementState
from src.trading_runtime import strategy_one_management_snapshot as subject
from src.trading_runtime.confirmed_original_risk_failure import ConfirmedOriginalRiskPolicy
from src.trading_runtime.original_risk_pending_snapshot import PENDING,selected_parent_contract
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
from src.trading_runtime.arte_typed_insert_dispatch import (
    TypedInsertDispatch,_Gate,_gate_path,_context_receipt_path,
)
from src.trading_runtime.keeper_session import ManagedKeeperSession
from tests.test_arte_typed_insert_dispatch import Keeper,Stat


def native_snapshot(monkeypatch):
    state=OriginalRiskManagementState(31000,(),(),())
    rows=subject.project_manager_snapshot(run_id='selected-empty',session_date=date(2026,8,18),
        checkpoint_sequence=42,state=state)
    batch='00000000-0000-0000-0000-000000000042'
    prefix=V4CommittedPrefix('selected-empty',42,batch,'2026-08-18:31000','running',(batch,))
    from src.trading_runtime import arte_journal_commit_v4,arte_journal_projection
    monkeypatch.setattr(arte_journal_commit_v4,'load_verified_v4_prefix',lambda *a,**k:prefix)
    monkeypatch.setattr(arte_journal_projection,'load_latest_backtest_cursor',lambda *a,**k:
        dict(run_id=prefix.run_id,event_sequence=42,batch_id=batch,boundary_ms=31000,session_date='2026-08-18'))
    keeper=Keeper();keeper.add_listener=lambda _:None;keeper.connected=True
    keeper.client_id=(101,b'synthetic-only');keeper.exists=lambda p:keeper.rows.get(p)
    session=ManagedKeeperSession(keeper);session._on_state('CONNECTED')
    dispatch=TypedInsertDispatch(keeper);dispatch.initialize_new_run(prefix.run_id)
    keeper.create(_context_receipt_path(prefix.run_id),b'1\n'+b'a'*64)
    gate,version=dispatch._read_gate(prefix.run_id)
    keeper.rows[_gate_path(prefix.run_id)]=(_Gate('open',0,gate.epoch,0,42,batch,
        'a'*64,'00000000-0000-0000-0000-000000000000').wire(),Stat(version+1))
    class Client:
        typed_insert_strict=True
        typed_insert_dispatch=dispatch
        confirmed_original_risk_policy=ConfirmedOriginalRiskPolicy()
        def __init__(self):self.tables={};self.statements=[]
        def execute(self,sql,*,query_id=None):
            self.statements.append(sql)
            table=sql.split('arte.',1)[1].split(' ',1)[0]
            if sql.startswith('INSERT INTO '):
                self.tables.setdefault(table,[]).extend(json.loads(line)
                    for line in sql.split('\n',1)[1].splitlines())
                return ''
            assert sql.startswith('SELECT ')
            return '\n'.join(json.dumps(row) for row in self.tables.get(table,()))
    return rows,prefix,Client(),session,dispatch


def test_selected_empty_root_actual_fence_readback_and_idempotent_recovery(monkeypatch):
    rows,prefix,client,session,dispatch=native_snapshot(monkeypatch)
    head=subject.publish_manager_snapshot(client,session,rows,journal_batch_id=prefix.last_batch_id)
    assert head.snapshot_hash==rows.snapshot['content_hash']
    assert set(client.tables)=={'trading_strategy_one_protection_snapshot_v1',
                               'trading_strategy_one_manager_snapshot_v4'}
    assert dispatch._read_gate(prefix.run_id)[0].registered==0
    assert any(PENDING.name in sql and sql.startswith('SELECT ') for sql in client.statements)
    loaded=subject.load_attested_manager_snapshot(client,subject.ManagedManagerSnapshotHeadReader(session),
        run_id=prefix.run_id,checkpoint_sequence=42)
    assert type(loaded) is OriginalRiskManagementState
    assert loaded.original_risk_requests==()
    assert subject.publish_manager_snapshot(client,session,rows,journal_batch_id=prefix.last_batch_id)==head
    assert all(len(values)==1 for values in client.tables.values())


@pytest.mark.parametrize('defect',['missing','hash','legacy_profile','mixed_v2','mixed_v3'])
def test_selected_root_cannot_fall_back_or_ignore_keeper_hash(monkeypatch,defect):
    rows,prefix,client,session,_=native_snapshot(monkeypatch)
    subject.publish_manager_snapshot(client,session,rows,journal_batch_id=prefix.last_batch_id)
    if defect=='missing':client.tables[selected_parent_contract().name]=[]
    elif defect=='hash':
        root=dict(client.tables[selected_parent_contract().name][0]);root['original_risk_pending_hash']='b'*64
        client.tables[selected_parent_contract().name]=[root]
    elif defect=='legacy_profile':client.confirmed_original_risk_policy=None
    else:
        legacy=subject.PARENT if defect=='mixed_v2' else subject.PARENT_V3
        client.tables[legacy.name]=[dict(client.tables[selected_parent_contract().name][0])]
    with pytest.raises((ValueError,RuntimeError)):
        subject.load_attested_manager_snapshot(client,subject.ManagedManagerSnapshotHeadReader(session),
            run_id=prefix.run_id,checkpoint_sequence=42)


def genuine_entry_graph(run_id):
    """Synthetic source facts compiled before constructing any native68 rows."""
    from datetime import datetime,timezone
    from uuid import UUID
    from test_arte_entry_activity_v4 import plan
    from test_strategy_one_intent import _proposal
    from src.backend.backtest_strategy_certified_price_break import (
        bind_certified_price_break_proposal,CertifiedPriceReadbackAuthority)
    from src.backend.backtest_strategy_episode_activity_gate import compile_episode_activity_static_gate
    from src.backend.backtest_strategy_episode_activity_source import (
        EpisodeActivityReadbackAuthority,certified_episode_entry_intent)
    from src.trading_runtime.arte_intent_projection import strategy_intent_batch
    from src.trading_runtime.arte_journal_writer import _sealed_families,typed_row
    from src.trading_runtime.arte_strategy_one_entry_journal import project_strategy_one_entry_evidence
    from src.trading_runtime.arte_rising_momentum_entry_v4 import project_rising_momentum_entry,MOMENTUM
    from src.trading_runtime.arte_initial_momentum_entry_v4 import project_initial_momentum_entry,INITIAL_MOMENTUM
    from src.trading_runtime.arte_first_price_entry_v4 import project_first_price_entry,FIRST_PRICE
    from src.trading_runtime.arte_entry_activity_v4 import project_entry_activity,ENTRY_ACTIVITY
    activity=plan();price=activity.parent
    proposal=replace(_proposal(),strategy_number=18,boundary_ms=41000,episode_start_ms=30000,
        momentum=price.source.parent.momentum.lookup('AAA',41000),
        initial_momentum=price.source.parent.selection_witness('AAA',41000))
    proposal=bind_certified_price_break_proposal(price,proposal,strategy_number=68)
    episode=EpisodeActivityReadbackAuthority(run_id,compile_episode_activity_static_gate(activity),68)
    authority=CertifiedPriceReadbackAuthority(run_id,price,entry_activity_source=episode)
    day=date(2026,8,18);intent=certified_episode_entry_intent(authority,proposal,session_date=day)
    batch_id=str(UUID(int=1));parent=str(UUID(int=2));month='2026-08-01'
    batch=strategy_intent_batch(intent,run_id=run_id,run_month=day.replace(day=1),
        account_id=proposal.account_id,attempt_id=str(UUID(int=3)),batch_id=batch_id,
        prior_batch_id=str(UUID(int=0)),sequence=1,source_cursor='2026-08-18:41000',
        run_status='running',recorded_at=intent.event_time,record_id=parent)
    tables={name:list(rows) for name,rows in _sealed_families(batch)}
    entry=project_strategy_one_entry_evidence(proposal,intent,session_date=day,run_id=run_id,
        batch_id=batch_id,parent_record_id=parent,first_price_source=authority)
    from src.trading_runtime.arte_strategy_one_entry_schema import ENTRY_EVIDENCE
    tables[ENTRY_EVIDENCE.name]=[typed_row(ENTRY_EVIDENCE.name,entry)]
    kwargs=dict(run_id=run_id,batch_id=batch_id,parent_record_id=parent,event_month=month)
    families={MOMENTUM.name:project_rising_momentum_entry(proposal,**kwargs),
        INITIAL_MOMENTUM.name:project_initial_momentum_entry(proposal,proposal.initial_momentum,**kwargs),
        FIRST_PRICE.name:project_first_price_entry(proposal.momentum,proposal.initial_momentum,
            proposal.first_price,price_source_token=proposal.price_source_token,strategy_number=68,**kwargs),
        ENTRY_ACTIVITY.name:(project_entry_activity(episode.witness('AAA',41000),strategy_number=68,**kwargs),)}
    for name,rows in families.items():tables[name]=[typed_row(name,row) for row in rows]
    return proposal,intent,authority,tables


@pytest.mark.parametrize('cold_defect',[None,'missing_pending','extra_pending','foreign_entry_source','missing_entry_source'])
def test_genuine68_nonempty_pending_actual_fence_and_cold_sources(monkeypatch,cold_defect):
    import struct
    from datetime import datetime
    from src.trading_runtime.confirmed_original_risk_failure import (
        CompletedRiskBucket,OriginalRiskDecisionDiagnostic,CONFIRMED_ORIGINAL_RISK_RULE)
    from src.trading_runtime.strategy_followthrough_failure import FollowThroughFailure
    from src.trading_runtime.original_risk_checkpoint import OriginalRiskCheckpointRequest
    from src.trading_runtime.strategy_one_stateful import StrategyOneFinancialView
    from src.trading_runtime.strategy_engine import AssignmentStatus,StrategyPermissions
    from src.trading_runtime.strategy_one_position import ProtectionState
    _,prefix,client,session,_=native_snapshot(monkeypatch)
    proposal,intent,authority,tables=genuine_entry_graph(prefix.run_id)
    client.tables.update(tables)
    prefix=replace(prefix,source_cursor='2026-08-18:120000',
        batch_ids=('00000000-0000-0000-0000-000000000001',prefix.last_batch_id))
    from src.trading_runtime import arte_journal_commit_v4,arte_journal_projection
    monkeypatch.setattr(arte_journal_commit_v4,'load_verified_v4_prefix',lambda *a,**k:prefix)
    monkeypatch.setattr(arte_journal_projection,'load_latest_backtest_cursor',lambda *a,**k:
        dict(run_id=prefix.run_id,event_sequence=42,batch_id=prefix.last_batch_id,
             boundary_ms=120000,session_date='2026-08-18'))
    original_execute=client.execute
    def execute(sql,**kwargs):
        raw=original_execute(sql,**kwargs)
        if not sql.startswith('SELECT '):return raw
        output=[]
        for line in raw.splitlines():
            row=json.loads(line)
            for name,value in tuple(row.items()):
                if f'AS {name}_bits' in sql:
                    row[name+'_bits']=None if value is None else int.from_bytes(struct.pack('>d',value),'big')
                if name in ('event_time','recorded_at') and isinstance(value,str) and 'T' in value:
                    row[name]=datetime.fromisoformat(value.replace('Z','+00:00')).strftime('%Y-%m-%d %H:%M:%S.%f')+'000'
            output.append(json.dumps(row))
        return '\n'.join(output)
    client.execute=execute
    financial=StrategyOneFinancialView(proposal.assignment_id,proposal.account_id,proposal.ticker,
        AssignmentStatus.MANAGING,StrategyPermissions(),10.,False,False,False,1)
    newest=CompletedRiskBucket(120000,99800,True,.1,.2,'build','a'*64,
        '00000000-0000-0000-0000-000000000001',
        '00000000-0000-0000-0000-000000000002','2026-08-18','AAA',
        '00000000-0000-0000-0000-000000000003')
    witness=FollowThroughFailure(120000,42000,10.01,9.89,99800,.1,.2,9.98,9.99,1000000)
    diagnostic=OriginalRiskDecisionDiagnostic(witness,newest,replace(newest,boundary_ms=115000),
        CONFIRMED_ORIGINAL_RISK_RULE)
    request=OriginalRiskCheckpointRequest(diagnostic,financial,intent.intent_id)
    key=financial.account_id,financial.assignment_id,financial.ticker
    state=OriginalRiskManagementState(120000,submitted=((key,proposal),),pending_breaks=(),
        positions=((key,ProtectionState(42000,proposal.initial_stop,proposal.initial_target)),),
        position_highs=((key,99800),),
        first_held_boundaries=((key,42000),),original_risk_requests=(request,))
    rows=subject._project_manager_snapshot_scalar(run_id=prefix.run_id,session_date=date(2026,8,18),
        checkpoint_sequence=42,state=state)
    head=subject.publish_manager_snapshot(client,session,rows,journal_batch_id=prefix.last_batch_id,
        first_price_source=authority)
    if cold_defect:
        if cold_defect=='missing_pending':client.tables[PENDING.name]=[]
        elif cold_defect=='extra_pending':client.tables[PENDING.name]*=2
        else:
            from src.trading_runtime.arte_strategy_one_entry_schema import ENTRY_EVIDENCE
            if cold_defect=='missing_entry_source':client.tables[ENTRY_EVIDENCE.name]=[]
            else:
                from src.trading_runtime.arte_journal_writer import typed_row
                row=dict(client.tables[ENTRY_EVIDENCE.name][0]);row.pop('content_hash')
                row['assignment_id']='foreign-assignment'
                client.tables[ENTRY_EVIDENCE.name]=[typed_row(ENTRY_EVIDENCE.name,row)]
        with pytest.raises((RuntimeError,ValueError)):
            subject.load_attested_manager_snapshot(client,subject.ManagedManagerSnapshotHeadReader(session),
                run_id=prefix.run_id,checkpoint_sequence=42,first_price_source=authority)
        return
    loaded=subject.load_attested_manager_snapshot(client,subject.ManagedManagerSnapshotHeadReader(session),
        run_id=prefix.run_id,checkpoint_sequence=42,first_price_source=authority)
    assert loaded==state
    assert loaded.original_risk_requests==(request,)
    assert head.snapshot_hash==rows.snapshot['content_hash']
    assert subject.publish_manager_snapshot(client,session,rows,journal_batch_id=prefix.last_batch_id,
        first_price_source=authority)==head
