"""Shared real encoder/commit/readback protocol, with controlled persistence/fence."""
from dataclasses import replace
from types import SimpleNamespace
from hashlib import sha256
import re

import pytest

from tests.test_backtest_native_channel_qualification import installed, unit  # noqa: F401
from tests.test_backtest_native_channel_confirmed_exit_unit import confirmed_unit_case
from tests.test_native_channel_exit_journal_context import cold_case
from src.trading_runtime.native_channel_exit_publication import (
    issue_native_channel_exit_publication,publish_native_channel_exit_publication,
)
from src.trading_runtime.arte_journal_writer import _CONTRACTS,_wire_row,verify_typed_contract_row
from src.trading_runtime.arte_native_channel_exit_schema import TABLES
from src.trading_runtime.journal_contract import canonical_json


def publication_transport(installed,monkeypatch,defect=None):
    # Import the legacy consumer before replacing the fixture's strategy lookup:
    # its module-level alias must not retain this native-only test declaration.
    from src.trading_runtime import strategy_one_intent  # noqa: F401
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
    from src.trading_runtime.strategy_registry import initialize_numbered_fixed_strategies
    initialize_numbered_fixed_strategies()
    parent=numbered_fixed_strategy(115)
    history,context,saved,_=cold_case(installed,monkeypatch)
    live=confirmed_unit_case(installed,monkeypatch)
    live['first_price_source']=context.first_price_source
    writer=SimpleNamespace(native_channel_management_policy=context.policy,
        confirmed_original_risk_policy=parent.confirmed_original_risk_policy,
        entry_spread_risk_profile=True,typed_insert_strict=True)
    image=issue_native_channel_exit_publication(writer,live['client'],live['manager_keeper'],
        live['broker_keeper'],live['receipt'],context,pending_record_id=live['pending_record_id'])
    tables={}
    previous=dict(saved['trading_commit_v4'][0])
    previous.update(batch_id=context.unit.base.prior_batch_id,first_sequence=1,
        last_sequence=history['prefix'].last_sequence,event_count=history['prefix'].last_sequence)
    previous['content_hash']=sha256(canonical_json({key:value for key,value in previous.items()
        if key not in {'content_hash','committed_at'}}).encode()).hexdigest()
    events=[]
    from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch
    class Dispatch(TypedInsertDispatch):
        def __init__(self): pass  # Controlled fence; production dispatch is covered separately.
        def assert_next_batch(self,**kwargs): events.append(('reserve',kwargs['batch_id']))
        def execute_typed_insert(self,client,**kwargs):
            assert client is writer
            events.append(('insert',kwargs['table']))
        def operation_present(self,**kwargs): return False
        def seal_verified_operation(self,**kwargs): events.append(('seal',kwargs['table']))
        def compact_verified_batch(self,**kwargs): events.append(('compact',kwargs['batch_id']))
    writer.typed_insert_dispatch=Dispatch()
    contracts={table.name:table for table in TABLES}
    from src.trading_runtime import arte_journal_writer as module
    actual_insert=module._insert
    def insert(client,name,rows,token,**kwargs):
        actual_insert(client,name,rows,token,**kwargs)
        wire=[_wire_row(name,row) if name in _CONTRACTS else
            verify_typed_contract_row(contracts[name],row,stored_utc=True) for row in rows]
        tables.setdefault(name,[]).extend(wire)
        if defect=='lost_detail' and name==TABLES[0].name:
            raise TimeoutError('controlled detail response loss')
        if defect=='lost_commit' and name=='trading_commit_v4':
            raise TimeoutError('controlled commit response loss')
    def read(client,sql):
        name=re.search(r'FROM arte\.([a-z0-9_]+)',sql).group(1)
        if name=='trading_commit_v4' and 'AND first_sequence=' in sql: return []
        if name=='trading_commit_v4' and context.unit.base.prior_batch_id in sql: return [previous]
        rows=[dict(row) for row in tables.get(name,[])]
        if defect=='missing_child' and name==TABLES[1].name and 'SELECT record_id,content_hash' not in sql:
            return []
        if 'SELECT record_id,content_hash' in sql:
            return [{key:row[key] for key in ('record_id','content_hash')} for row in rows]
        return rows
    monkeypatch.setattr(module,'_insert',insert)
    monkeypatch.setattr(module,'_rows',read)
    return writer,live,history,context,image,tables,events


@pytest.mark.parametrize('defect',[None,'missing_child','lost_detail','lost_commit'])
def test_shared_exit_commit_is_children_first_and_failure_keeps_fence(installed,monkeypatch,defect):
    writer,live,history,context,image,tables,events=publication_transport(installed,monkeypatch,defect)
    if defect is None:
        assert publish_native_channel_exit_publication(writer,image,
            verified_prior_prefix=history['prefix'])==context.batch_id
        inserts=[name for action,name in events if action=='insert']
        assert set(inserts[:5])=={name for name,rows in context.unit.sealed_families() if rows}
        assert inserts[-1]=='trading_commit_v4'
        assert inserts[5:-1] and set(inserts[5:-1])=={'trading_commit_family_v4'}
        assert events[0][0]=='reserve' and events[-1][0]=='compact'
        before=len(inserts)
        # A committed retry must not require the former current Keeper heads.
        live['manager_keeper'].head=replace(live['manager_keeper'].head,
            checkpoint_sequence=live['manager_keeper'].head.checkpoint_sequence+1)
        publish_native_channel_exit_publication(writer,image,verified_prior_prefix=history['prefix'])
        assert len([event for event in events if event[0]=='insert'])==before
    else:
        with pytest.raises((ValueError,RuntimeError,TimeoutError)):
            publish_native_channel_exit_publication(writer,image,verified_prior_prefix=history['prefix'])
        assert not any(action=='compact' for action,_ in events)
        if defect!='lost_commit': assert not tables.get('trading_commit_v4')
        else:
            before=len([event for event in events if event[0]=='insert'])
            publish_native_channel_exit_publication(writer,image,verified_prior_prefix=history['prefix'])
            assert len([event for event in events if event[0]=='insert'])==before
            assert events[-1][0]=='compact'


@pytest.mark.parametrize('defect',['head','prefix','image','fills'])
def test_new_exit_publication_reproves_authority_before_reserving(installed,monkeypatch,defect):
    writer,live,history,context,image,tables,events=publication_transport(installed,monkeypatch)
    prefix=history['prefix']
    if defect=='head':
        live['manager_keeper'].head=replace(live['manager_keeper'].head,
            checkpoint_sequence=live['manager_keeper'].head.checkpoint_sequence+1)
    elif defect=='prefix': prefix=replace(prefix,last_sequence=prefix.last_sequence+1)
    elif defect=='image': image=replace(image)
    elif defect=='fills':
        monkeypatch.setattr('src.backend.backtest_v4_execution_restore.load_v4_broker_executions',
            lambda *_a,**_k:[])
    with pytest.raises(ValueError):
        publish_native_channel_exit_publication(writer,image,verified_prior_prefix=prefix)
    assert events==[] and tables=={}


@pytest.mark.parametrize('defect',[None,'ordering','lost_commit'])
def test_writer_queue_returns_receipt_only_after_verified_commit(installed,monkeypatch,defect):
    from src.trading_runtime import arte_journal_writer as module
    writer,live,history,context,image,tables,events=publication_transport(installed,monkeypatch,
        'lost_commit' if defect=='lost_commit' else None)
    monkeypatch.setattr(module,'_v4_preflight',lambda _client:None)
    monkeypatch.setattr(module,'_verify_run_identity',lambda _client,_run:
        dict(mode='backtest',account_ids=(context.unit.base.intents[0]['account_id'],)))
    journal=module.ArteJournalWriter(writer,run_id=image.run_id,
        journal_profile='backtest_v4',coalesce_batches=False)
    try:
        journal._last_commit_id='foreign' if defect=='ordering' else context.unit.base.prior_batch_id
        receipt=journal.submit_native_channel_exit_publication(image,verified_prior_prefix=history['prefix'])
        if defect is None:
            assert receipt.result(timeout=10)==context.batch_id
            assert journal._last_commit_id==context.batch_id
            assert journal.metrics()['committed_event_rows']==1
            assert journal.metrics()['publish_by_unit']['_NativeChannelExitPublicationUnit']['units']==1
            with pytest.raises(ValueError):
                journal.submit_native_channel_exit_publication(replace(image),verified_prior_prefix=history['prefix'])
            journal.close()
        else:
            with pytest.raises((RuntimeError,TimeoutError)): receipt.result(timeout=10)
            assert journal._last_commit_id!=context.batch_id
            assert journal.metrics()['failed_units']==1
            if defect=='ordering': assert tables=={} and events==[]
            with pytest.raises(RuntimeError): journal.close()
    finally:
        if journal._thread.is_alive():
            try: journal.close()
            except RuntimeError: pass
    assert not journal._thread.is_alive()


def test_exit_queue_is_bounded_and_publishes_off_submission_thread(installed,monkeypatch):
    from threading import Event,get_ident
    from src.trading_runtime import arte_journal_writer as module
    from src.trading_runtime import native_channel_exit_publication as publication
    writer,live,history,context,image,tables,events=publication_transport(installed,monkeypatch)
    monkeypatch.setattr(module,'_v4_preflight',lambda _client:None)
    monkeypatch.setattr(module,'_verify_run_identity',lambda _client,_run:
        dict(mode='backtest',account_ids=(context.unit.base.intents[0]['account_id'],)))
    actual=publication.publish_native_channel_exit_publication
    entered,release=Event(),Event()
    caller=get_ident()
    def publish(*args,**kwargs):
        assert get_ident()!=caller
        entered.set()
        assert release.wait(10)
        return actual(*args,**kwargs)
    monkeypatch.setattr(publication,'publish_native_channel_exit_publication',publish)
    journal=module.ArteJournalWriter(writer,run_id=image.run_id,capacity=1,
        journal_profile='backtest_v4',coalesce_batches=False)
    try:
        journal._last_commit_id=context.unit.base.prior_batch_id
        first=journal.submit_native_channel_exit_publication(image,verified_prior_prefix=history['prefix'])
        assert entered.wait(5)
        second=journal.submit_native_channel_exit_publication(image,verified_prior_prefix=history['prefix'])
        with pytest.raises(module.JournalQueueFull):
            journal.submit_native_channel_exit_publication(image,verified_prior_prefix=history['prefix'])
        assert not first.done() and not second.done() and tables=={} and events==[]
        release.set()
        assert first.result(timeout=10)==second.result(timeout=10)==context.batch_id
        assert len([event for event in events if event==('insert','trading_commit_v4')])==1
    finally:
        release.set()
        journal.close()
    assert not journal._thread.is_alive()
