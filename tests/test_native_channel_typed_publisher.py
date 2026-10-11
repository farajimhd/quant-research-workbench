"""Actual asynchronous publisher and journal pipeline; native I/O is controlled."""
import asyncio
from dataclasses import replace

import pytest

from tests.test_backtest_native_channel_qualification import installed, unit  # noqa: F401
from tests.test_native_channel_exit_commit_publication import publication_transport
from src.backend.backtest_native_channel_exit_unit import (
    prepare_confirmed_native_channel_exit_request,native_channel_exit_intent,
)
from src.backend.backtest_native_channel_publisher_source import NativeChannelExitPublisherSource
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_typed_publisher import BacktestTypedJournalPublisher


def test_declared_session_source_binds_actual_publisher_and_closes_failed_reader(installed, monkeypatch):
    from types import SimpleNamespace
    from src.backend.backtest_native_channel_publisher_source import bind_declared_native_channel_session_source
    from src.trading_runtime import original_risk_diagnostic_profile as profiles
    from src.trading_runtime import strategy_one_management_snapshot as managers
    from src.trading_runtime import strategy_one_broker_match_snapshot as brokers
    from src.trading_runtime import arte_journal_writer as module
    writer, live, history, context, _, _, _ = publication_transport(installed, monkeypatch)
    memory = BacktestMemoryJournal(run_id=context.base.run_id,
        initial_sequence=history['prefix'].last_sequence)
    transport = SimpleNamespace(run_id=context.base.run_id, run_mode='backtest',
        journal_profile='backtest_v4', coalesce_batches=False, max_events_per_commit=1000,
        _client=writer)
    publisher = BacktestTypedJournalPublisher(memory, transport,
        attempt_id=context.attempt_id, run_month=context.base.run_month,
        initial_sequence=history['prefix'].last_sequence,
        prior_batch_id=context.base.prior_batch_id, source_cursor=context.base.source_cursor,
        expected_config=dict(strategy_id='early-squeeze-strategy', strategy_revision=1))
    publisher._first_price_source = context.first_price_source
    configuration = {'declared-test-packet': True}
    calls, closed = [], []
    options = {'native_channel_management_policy': context.policy}
    monkeypatch.setattr(profiles, 'declared_fixed_runner_options',
        lambda value: options if value is configuration else None)
    def reader(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(native_channel_management_policy=context.policy,
            close=lambda: closed.append(True))
    monkeypatch.setattr(module, 'backtest_v4_operator_client_from_env', reader)
    monkeypatch.setattr(managers, 'ManagedManagerSnapshotHeadReader', lambda value: live['manager_keeper'])
    monkeypatch.setattr(brokers, 'ManagedBrokerMatchHeadReader', lambda value: live['broker_keeper'])
    try:
        client = bind_declared_native_channel_session_source(publisher, object(), configuration)
        source = publisher._native_channel_exit_source
        assert source.client is client and source.first_price_source is context.first_price_source
        assert source.manager_keeper is live['manager_keeper']
        assert source.broker_keeper is live['broker_keeper']
        assert calls == [options] and not closed and source.contexts == ()
        with pytest.raises(ValueError):
            bind_declared_native_channel_session_source(publisher, object(), configuration)
        assert len(calls) == 2 and closed == [True]
        client.close()
        options.clear()
        assert bind_declared_native_channel_session_source(publisher, object(), configuration) is None
        assert len(calls) == 2
    finally:
        memory.close()


@pytest.mark.parametrize('failure', [None, 'writer', 'native_reader'])
def test_actual_controller_closes_native_reader_even_after_writer_failure(failure):
    from types import SimpleNamespace
    from src.backend.replay_run_service import ReplayRunController
    controller = ReplayRunController.__new__(ReplayRunController)
    closed = []
    def resource(name):
        def close():
            closed.append(name)
            if failure == name:
                raise RuntimeError(name)
        return SimpleNamespace(close=close)
    controller._journal_writer = resource('writer')
    controller._journal_publisher = object()
    controller._fixed_terminal_authority = object()
    controller._native_channel_read_client = resource('native_reader')
    controller._ladder_writer_read_client = None
    controller._journal = resource('journal')
    controller._fixed_keeper_session = resource('keeper')
    controller._fixed_v4_lease = SimpleNamespace(release=lambda: closed.append('lease'))
    controller._record_stage_time = lambda *_args: None
    if failure:
        with pytest.raises(RuntimeError, match=failure):
            asyncio.run(controller._close_fixed_journal())
    else:
        asyncio.run(controller._close_fixed_journal())
    assert closed == ['writer', 'native_reader', 'journal', 'lease', 'keeper']
    assert controller._native_channel_read_client is None


@pytest.mark.parametrize('defect',[None,'unbound','lost_commit'])
def test_actual_typed_publisher_dispatch_and_fence(installed,monkeypatch,defect):
    from src.trading_runtime import arte_journal_writer as module
    writer,live,history,context,_,tables,events=publication_transport(installed,monkeypatch,
        'lost_commit' if defect=='lost_commit' else None)
    _,request=prepare_confirmed_native_channel_exit_request(**live)
    intent=native_channel_exit_intent(request,strategy_number=1,management_policy=context.policy)
    memory=BacktestMemoryJournal(run_id=context.base.run_id,initial_sequence=history['prefix'].last_sequence)
    record=memory.append_native_channel_exit(intent=intent,request=request,context=context,
        strategy_id='early-squeeze-strategy',strategy_revision=1)
    monkeypatch.setattr(module,'_v4_preflight',lambda _client:None)
    monkeypatch.setattr(module,'_verify_run_identity',lambda _client,_run:
        dict(mode='backtest',account_ids=(record.account_id,)))
    journal=module.ArteJournalWriter(writer,run_id=context.base.run_id,
        journal_profile='backtest_v4',coalesce_batches=False)
    journal._last_commit_id=context.base.prior_batch_id
    publisher=BacktestTypedJournalPublisher(memory,journal,attempt_id=context.attempt_id,
        run_month=context.base.run_month,initial_sequence=record.sequence-1,
        prior_batch_id=context.base.prior_batch_id,source_cursor=context.base.source_cursor,
        expected_config=dict(strategy_id='early-squeeze-strategy',strategy_revision=1))
    # Startup price/principal transports are controlled; dispatch and drain are real.
    publisher._first_price_source=context.first_price_source
    source=NativeChannelExitPublisherSource(context.base.run_id,live['client'],
        live['manager_keeper'],live['broker_keeper'],context.policy,context.first_price_source)
    if defect!='unbound': publisher.bind_native_channel_exit_source(source)
    async def drain(): return await publisher.enqueue_pending()
    try:
        if defect is None:
            receipt=asyncio.run(drain())
            assert receipt.last_sequence==record.sequence
            assert receipt.last_batch_id==tables['trading_commit_v4'][0]['batch_id']
            assert memory.native_channel_exit_for_record(record.record_id) is None
            assert intent.intent_id in publisher._committed_strategy_intents
            assert len(source.contexts)==1 and source.contexts[0].batch_id==receipt.last_batch_id
            assert publisher.fenced_sequence==record.sequence
            with pytest.raises(ValueError): publisher.bind_native_channel_exit_source(source)
            journal.close()
        else:
            with pytest.raises((ValueError,RuntimeError,TimeoutError)): asyncio.run(drain())
            assert publisher.fenced_sequence==record.sequence-1
            assert memory.native_channel_exit_for_record(record.record_id) is not None
            assert source.contexts==()
            if defect=='unbound':
                assert tables=={} and events==[]
                journal.close()
            else:
                with pytest.raises(RuntimeError): journal.close()
    finally:
        if journal._thread.is_alive():
            try: journal.close()
            except RuntimeError: pass
        memory.close()
    assert not journal._thread.is_alive()


def test_native_source_context_history_is_bounded_and_selected(installed,monkeypatch):
    writer,live,history,context,_,tables,events=publication_transport(installed,monkeypatch)
    source=NativeChannelExitPublisherSource(context.base.run_id,live['client'],
        live['manager_keeper'],live['broker_keeper'],context.policy,context.first_price_source,max_commits=1)
    source.remember(context)
    source.remember(context)
    assert source.contexts==(context,)
    with pytest.raises(ValueError): source.remember(replace(context,batch_id='11111111-1111-4111-8111-111111111111'))
    with pytest.raises(ValueError): replace(source,max_commits=0)
    with pytest.raises(ValueError): replace(source,first_price_source=object())
    assert tables=={} and events==[]


def test_native_history_restore_is_atomic_and_requires_committed_membership(installed,monkeypatch):
    from src.trading_runtime import arte_journal_commit_v4 as commits
    from src.backend.backtest_typed_publisher import TypedBacktestReceipt
    _,live,history,context,_,tables,events=publication_transport(installed,monkeypatch)
    source=NativeChannelExitPublisherSource(context.base.run_id,live['client'],
        live['manager_keeper'],live['broker_keeper'],context.policy,context.first_price_source)
    receipt=TypedBacktestReceipt(context.base.last_sequence,context.base.batch_id,context.base.source_cursor)
    prefix=replace(history['prefix'],last_sequence=receipt.last_sequence,
        last_batch_id=receipt.last_batch_id,source_cursor=receipt.source_cursor)
    observed=[]
    def cold_prefix(client,run_id,**kwargs):
        observed.append(kwargs)
        return prefix
    monkeypatch.setattr(commits,'load_verified_v4_prefix',cold_prefix)
    with pytest.raises(ValueError): source.restore_committed_contexts((context,),receipt)
    assert source.contexts==()
    prefix=replace(prefix,batch_ids=(*prefix.batch_ids,context.base.batch_id))
    assert source.restore_committed_contexts((context,),receipt) is prefix
    assert source.contexts==(context,)
    assert all(item['native_channel_contexts']==(context,) for item in observed)
    with pytest.raises(ValueError): source.restore_committed_contexts((context,),receipt)
    assert tables=={} and events==[]


def test_native_snapshot_writer_cold_verifies_complete_exit_history(installed,monkeypatch):
    from src.trading_runtime import arte_journal_commit_v4 as commits
    writer,_,_,context,_,tables,events=publication_transport(installed,monkeypatch)
    observed=[]
    result=object()
    def cold_prefix(client,run_id,**kwargs):
        observed.append((client,run_id,kwargs))
        return result
    monkeypatch.setattr(commits,'load_verified_v4_prefix',cold_prefix)
    contexts=(context,)
    assert commits.load_writer_v4_snapshot_prefix(writer,context.base.run_id,
        first_price_source=context.first_price_source,native_channel_contexts=contexts) is result
    assert observed==[(writer,context.base.run_id,dict(max_commits=100_000,
        first_price_source=context.first_price_source,native_channel_contexts=contexts))]
    with pytest.raises(ValueError):
        commits.load_writer_v4_snapshot_prefix(writer,context.base.run_id,
            first_price_source=object(),native_channel_contexts=contexts)
    assert len(observed)==1 and tables=={} and events==[]


@pytest.mark.parametrize('reader',['manager','broker'])
def test_checkpoint_readers_forward_complete_native_history(installed,monkeypatch,reader):
    from src.trading_runtime import arte_journal_commit_v4 as commits
    from src.trading_runtime.native_channel_manager_reader import load_attested_native_channel_manager_snapshot
    from src.trading_runtime.strategy_one_broker_match_snapshot import load_attested_broker_match_snapshot
    _,live,history,context,_,tables,events=publication_transport(installed,monkeypatch)
    contexts=(context,)
    observed=[]
    from types import SimpleNamespace
    client=SimpleNamespace(execute=lambda *_args,**_kwargs:pytest.fail('Unexpected detail read'))
    keeper=SimpleNamespace(read_head=lambda **_kwargs:pytest.fail('Unexpected head read'))
    class PrefixObserved(Exception): pass
    def prefix(client,run_id,**kwargs):
        observed.append((client,run_id,kwargs))
        raise PrefixObserved
    monkeypatch.setattr(commits,'load_verified_v4_prefix',prefix)
    with pytest.raises(PrefixObserved):
        if reader=='manager':
            load_attested_native_channel_manager_snapshot(client,keeper,
                run_id=context.base.run_id,checkpoint_sequence=history['prefix'].last_sequence,
                policy=context.policy,contexts=context.contexts,
                first_price_source=context.first_price_source,native_channel_contexts=contexts)
        else:
            load_attested_broker_match_snapshot(client,keeper,
                run_id=context.base.run_id,checkpoint_sequence=history['prefix'].last_sequence,
                first_price_source=context.first_price_source,native_channel_contexts=contexts)
    assert len(observed)==1
    assert observed[0][0] is client
    assert observed[0][1]==context.base.run_id
    assert observed[0][2]['native_channel_contexts'] is contexts
    assert observed[0][2]['first_price_source'] is context.first_price_source
    assert tables=={} and events==[]
