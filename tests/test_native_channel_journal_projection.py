"""Actual memory records and typed projection, with controlled market/checkpoint I/O."""
from dataclasses import replace
from uuid import uuid4

import pytest

from tests.test_backtest_native_channel_qualification import installed, unit  # noqa: F401
from tests.test_native_channel_exit_memory_journal import journal_case
from src.backend.backtest_typed_projection import project_pending_backtest_v4_prefix
from src.backend.backtest_native_channel_journal_projection import project_native_channel_journal_exit
from src.trading_runtime.native_channel_exit_journal_context import NativeChannelExitJournalContext


def projection_case(installed,monkeypatch):
    journal,source=journal_case(installed,monkeypatch,certified_source=True)
    record=journal.append_native_channel_exit(**source)
    context=source['context']
    args=dict(attempt_id=context.attempt_id,batch_id=str(uuid4()),
        prior_batch_id=context.base.prior_batch_id,source_cursor=context.base.source_cursor,
        run_month=context.base.run_month,first_price_source=context.first_price_source,
        expected_config=dict(strategy_id=source['strategy_id'],strategy_revision=source['strategy_revision']))
    return journal,source,record,args


def test_actual_typed_projector_keeps_channel_exit_complete_and_isolated(installed,monkeypatch):
    journal,source,record,args=projection_case(installed,monkeypatch)
    context,=project_pending_backtest_v4_prefix(journal,prior_sequence=record.sequence-1,
        through_sequence=record.sequence,**{key:value for key,value in args.items() if key!='batch_id'})
    assert type(context) is NativeChannelExitJournalContext
    assert context.base.events[0]['record_id']==record.record_id
    assert context.base.events[0]['sequence']==record.sequence
    assert context.unit.decision['parent_record_id']==record.record_id
    assert context.batch_id!=source['context'].batch_id
    assert context.base.intents[0]['intent_id']==source['intent'].intent_id
    assert context.unit.decision['source_manager_snapshot_hash']==source['context'].unit.decision['source_manager_snapshot_hash']
    from src.backend.backtest_typed_publisher import _coalesce_v4_units
    assert _coalesce_v4_units((context,))==(context,)
    journal.close()


def test_reprojection_preserves_actual_journal_causal_lineage(installed,monkeypatch):
    journal,source,record,args=projection_case(installed,monkeypatch)
    record=replace(record,payload={**record.payload,'correlation_id':'owner-correlation','causation_id':'owner-cause'})
    context=project_native_channel_journal_exit(record,journal.native_channel_exit_for_record(record.record_id),**args)
    assert context.correlation_id==context.base.events[0]['correlation_id']=='owner-correlation'
    assert context.causation_id==context.base.events[0]['causation_id']=='owner-cause'
    with pytest.raises(ValueError): replace(context,correlation_id='foreign')
    journal.close()


@pytest.mark.parametrize('defect',['record_quantity','record_account','frontier','source','config','native_evidence'])
def test_projection_rejects_changed_pending_claims(installed,monkeypatch,defect):
    journal,source,record,args=projection_case(installed,monkeypatch)
    pending=journal.native_channel_exit_for_record(record.record_id)
    if defect=='record_quantity': record=replace(record,payload={**record.payload,'quantity':100})
    elif defect=='record_account': record=replace(record,account_id='foreign')
    elif defect=='frontier': args['prior_batch_id']=str(uuid4())
    elif defect=='source': args['first_price_source']=object()
    elif defect=='config': args['expected_config']['strategy_revision']=2
    elif defect=='native_evidence':
        context=source['context']
        contexts={key:dict(value) for key,value in context.contexts.items()}
        contexts[next(iter(contexts))]['executions']=()
        pending=(pending[0],replace(context,contexts=contexts),*pending[2:])
    with pytest.raises((ValueError,RuntimeError)):
        project_native_channel_journal_exit(record,pending,**args)
    journal.close()


def test_typed_projection_rejects_naked_channel_intent(installed,monkeypatch):
    journal,source,record,args=projection_case(installed,monkeypatch)
    journal._native_channel_exits.clear()
    with pytest.raises(RuntimeError,match='complete pending source'):
        project_pending_backtest_v4_prefix(journal,prior_sequence=record.sequence-1,
            through_sequence=record.sequence,**{key:value for key,value in args.items() if key!='batch_id'})
    journal.close()


def test_typed_projection_rejects_mixed_exit_authorities(installed,monkeypatch):
    journal,source,record,args=projection_case(installed,monkeypatch)
    journal._profit_giveback_exits[record.record_id]=object()
    with pytest.raises(RuntimeError,match='mix exit source authorities'):
        project_pending_backtest_v4_prefix(journal,prior_sequence=record.sequence-1,
            through_sequence=record.sequence,**{key:value for key,value in args.items() if key!='batch_id'})
    journal.close()


@pytest.mark.parametrize('with_lineage',[False,True])
def test_actual_memory_projection_issuance_and_writer_commit(installed,monkeypatch,with_lineage):
    from tests.test_native_channel_exit_commit_publication import publication_transport
    from src.backend.backtest_native_channel_exit_unit import (
        prepare_confirmed_native_channel_exit_request,native_channel_exit_intent,
    )
    from src.backend.backtest_journal_memory import BacktestMemoryJournal
    from src.trading_runtime.native_channel_exit_publication import issue_native_channel_exit_publication
    from src.trading_runtime import arte_journal_writer as module
    writer,live,history,original,_,tables,events=publication_transport(installed,monkeypatch)
    if with_lineage:
        monkeypatch.setattr('src.backend.backtest_journal_memory.current_request_identity',lambda:'trace')
        monkeypatch.setattr('src.backend.backtest_journal_memory.causal_identity',lambda **_:
            dict(correlation_id='owner-correlation',causation_id='owner-cause'))
    _,request=prepare_confirmed_native_channel_exit_request(**live)
    intent=native_channel_exit_intent(request,strategy_number=1,management_policy=original.policy)
    memory=BacktestMemoryJournal(run_id=original.base.run_id,initial_sequence=history['prefix'].last_sequence)
    record=memory.append_native_channel_exit(intent=intent,request=request,context=original,
        strategy_id='early-squeeze-strategy',strategy_revision=1)
    projected,=project_pending_backtest_v4_prefix(memory,attempt_id=original.attempt_id,
        run_month=original.base.run_month,prior_sequence=record.sequence-1,
        prior_batch_id=original.base.prior_batch_id,source_cursor=original.base.source_cursor,
        expected_config=dict(strategy_id='early-squeeze-strategy',strategy_revision=1),
        first_price_source=original.first_price_source,through_sequence=record.sequence)
    image=issue_native_channel_exit_publication(writer,live['client'],live['manager_keeper'],
        live['broker_keeper'],live['receipt'],projected,pending_record_id=live['pending_record_id'])
    monkeypatch.setattr(module,'_v4_preflight',lambda _client:None)
    monkeypatch.setattr(module,'_verify_run_identity',lambda _client,_run:
        dict(mode='backtest',account_ids=(record.account_id,)))
    journal=module.ArteJournalWriter(writer,run_id=image.run_id,
        journal_profile='backtest_v4',coalesce_batches=False)
    try:
        journal._last_commit_id=projected.base.prior_batch_id
        assert journal.submit_native_channel_exit_publication(image,
            verified_prior_prefix=history['prefix']).result(timeout=10)==projected.batch_id
        assert tables['trading_event_v1'][0]['record_id']==record.record_id
        assert tables['trading_event_v1'][0]['correlation_id']==('owner-correlation' if with_lineage else '')
        assert tables['trading_commit_v4'][0]['batch_id']==projected.batch_id
        assert events[-1][0]=='compact'
        memory.mark_fenced(record.sequence)
        assert memory.native_channel_exit_for_record(record.record_id) is None
    finally:
        journal.close()
        memory.close()
    assert not journal._thread.is_alive()
