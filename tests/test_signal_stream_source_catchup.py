"""A durable source may lead the last acknowledged journal consumer clock."""
from dataclasses import replace
from datetime import datetime,timedelta

import pytest

from src.backend.signal_stream_typed_publication import TypedSignalPublicationQueue
from src.backend.signal_stream_typed_attestation import SignalCursorAttestor
from src.backend.signal_stream_typed_readback import recover_committed_head
from src.backend.signal_stream_runtime_service import SignalStreamRuntime
from src.backend.signal_stream_session_head import SignalSessionHeadKeeper
from tests.test_live_signal_completion_keeper import FakeKazoo
from tests.test_signal_stream_typed_publication import FakeStorage,_batch


def seeded(*, tail_seconds=None,nonempty_tail=False):
    storage=FakeStorage();keeper=SignalSessionHeadKeeper(FakeKazoo(),endpoint='127.0.0.1:9181')
    first=_batch();keeper.acquire(first.session_key,owner_id='original')
    publisher=TypedSignalPublicationQueue(storage,attestor=SignalCursorAttestor(keeper,owner_id='original',epoch=1))
    scope=dict(session_key=first.session_key,configuration_revision=first.configuration_revision,
               source_revision=first.source_revision,catalogs=first.catalogs)
    publisher.bootstrap_session(**scope)
    batches=[first]
    try:
        head=publisher.submit(first).result(3)
        states=first.after_states
        for sequence in range(2,5):
            batch=replace(first,batch_sequence=sequence,previous_commit_hash=head,
                cutoff_at=(datetime.fromisoformat(first.cutoff_at)+timedelta(seconds=sequence-1)).isoformat(),
                before_states=states,after_states=states,occurrences=(),allow_clock_only=True)
            if sequence==2 and nonempty_tail:
                batch=replace(batch,after_states={'stream-1':{'ABC':{'matching':False,'definition_revision':'definition-1'}}},allow_clock_only=False)
            if sequence==4 and tail_seconds is not None:
                batch=replace(batch,cutoff_at=(datetime.fromisoformat(first.cutoff_at)+timedelta(seconds=tail_seconds)).isoformat(),
                    after_states={'stream-1':{'ABC':{'matching':False,'definition_revision':'definition-1'}}},allow_clock_only=False)
            batches.append(batch);head=publisher.submit(batch).result(3)
            states=batch.after_states
    finally:publisher.close()
    assert keeper.release(first.session_key,owner_id='original',epoch=1)
    assert keeper.acquire(first.session_key,owner_id='resume')==2
    return storage,keeper,scope,batches


def test_three_stored_clocks_replay_without_writes_or_cas_then_append_normally(monkeypatch):
    storage,keeper,scope,batches=seeded()
    durable=keeper.read_head(scope['session_key']);before_writes=list(storage.writes);cas=[]
    attest=keeper.attest_next
    def counted(*args,**kwargs):cas.append(True);return attest(*args,**kwargs)
    monkeypatch.setattr(keeper,'attest_next',counted)
    publisher=TypedSignalPublicationQueue(storage,attestor=SignalCursorAttestor(keeper,owner_id='resume',epoch=2))
    runtime=SignalStreamRuntime()
    head=publisher.bootstrap_runtime_session(runtime,**scope,anchor_cutoff_at=batches[0].cutoff_at,maximum_cutoff_at=batches[-1].cutoff_at,maximum_ahead_batches=3)
    assert head.sequence==4 and head.recovery_anchor.sequence==1
    assert runtime.typed_source_cursor()[0]==1
    assert runtime.typed_source_commit_hashes()==head.commit_hashes[:1]
    try:
        for batch in batches[1:]:
            assert publisher.submit(batch).result(3)==head.commit_hashes[batch.batch_sequence-1]
            assert storage.writes==before_writes and not cas
            assert keeper.read_head(scope['session_key'])==durable
        assert publisher.recovery_counts()==dict(resumed_stored=3,appended=0,failed=0,remaining_stored=0)
        fifth=replace(batches[-1],batch_sequence=5,previous_commit_hash=head.content_hash,
            cutoff_at=(datetime.fromisoformat(batches[-1].cutoff_at)+timedelta(seconds=1)).isoformat())
        publisher.submit(fifth).result(3)
        assert len(cas)==1 and len(storage.writes)==len(before_writes)+1
        assert keeper.read_head(scope['session_key']).batch_sequence==5
        assert publisher.recovery_counts()['appended']==1
    finally:publisher.close()


@pytest.mark.parametrize('failure',['future_anchor','missing_anchor','too_many','foreign_revision','corrupt_ahead'])
def test_anchor_recovery_verifies_full_head_and_rejects_invalid_authority(failure):
    storage,keeper,scope,batches=seeded();anchor=batches[0].cutoff_at;bound=3
    if failure=='future_anchor':anchor='2026-09-24T15:00:00+00:00'
    elif failure=='missing_anchor':anchor='2026-09-24T14:00:00.500000+00:00'
    elif failure=='too_many':bound=2
    elif failure=='foreign_revision':scope={**scope,'configuration_revision':'foreign'}
    else:storage.rows['signal_stream_cursor_commit_typed_v1'][-1]['content_hash']='0'*64
    publisher=TypedSignalPublicationQueue(storage,attestor=SignalCursorAttestor(keeper,owner_id='resume',epoch=2))
    before=list(storage.writes)
    try:
        with pytest.raises(ValueError):publisher.bootstrap_runtime_session(SignalStreamRuntime(),**scope,anchor_cutoff_at=anchor,maximum_cutoff_at=batches[-1].cutoff_at,maximum_ahead_batches=bound)
        assert storage.writes==before
    finally:publisher.close()


@pytest.mark.parametrize('failure',['different_packet','lost_owner','changed_stored_commit'])
def test_consumer_replay_failure_never_writes_or_advances_keeper(failure):
    storage,keeper,scope,batches=seeded()
    publisher=TypedSignalPublicationQueue(storage,attestor=SignalCursorAttestor(keeper,owner_id='resume',epoch=2))
    publisher.bootstrap_runtime_session(SignalStreamRuntime(),**scope,anchor_cutoff_at=batches[0].cutoff_at,maximum_cutoff_at=batches[-1].cutoff_at)
    before=list(storage.writes);head=keeper.read_head(scope['session_key']);batch=batches[1]
    if failure=='different_packet':batch=replace(batch,after_states={})
    elif failure=='lost_owner':
        keeper.release(scope['session_key'],owner_id='resume',epoch=2);keeper.acquire(scope['session_key'],owner_id='foreign')
    else:storage.rows['signal_stream_cursor_commit_typed_v1'][1]['content_hash']='0'*64
    try:
        with pytest.raises((ValueError,RuntimeError)):publisher.submit(batch).result(3)
        assert storage.writes==before and keeper.read_head(scope['session_key'])==head
        assert publisher.recovery_counts()==dict(resumed_stored=0,appended=0,failed=1,remaining_stored=3)
    finally:publisher.close()


@pytest.mark.parametrize('bound',[-1,4097,True])
def test_ahead_recovery_bound_is_explicit_and_finite(bound):
    storage,_,scope,batches=seeded()
    with pytest.raises(ValueError):recover_committed_head(storage,**scope,anchor_cutoff_at=batches[0].cutoff_at,maximum_cutoff_at=batches[-1].cutoff_at,maximum_ahead_batches=bound)


@pytest.mark.parametrize('tail_seconds',[1,2])
def test_anchor_rejects_valid_hash_backward_or_duplicate_non_clock_only_tail(tail_seconds):
    storage,_,scope,batches=seeded(tail_seconds=tail_seconds)
    # Every scalar/chain seal is valid; the consumer runtime still requires
    # strict chronological cutoffs across the whole retained durable tail.
    assert recover_committed_head(storage,**scope).sequence==4
    with pytest.raises(ValueError,match='strictly ordered'):
        recover_committed_head(storage,**scope,anchor_cutoff_at=batches[0].cutoff_at,
            maximum_cutoff_at='2026-09-24T15:00:00+00:00')


def test_anchor_rejects_source_head_past_saved_session_horizon():
    storage,_,scope,batches=seeded()
    with pytest.raises(ValueError,match='anchor'):
        recover_committed_head(storage,**scope,anchor_cutoff_at=batches[0].cutoff_at,
            maximum_cutoff_at=batches[-2].cutoff_at)


def test_nonempty_stored_state_delta_uses_read_verify_without_insert_or_cas():
    storage,keeper,scope,batches=seeded(nonempty_tail=True)
    publisher=TypedSignalPublicationQueue(storage,attestor=SignalCursorAttestor(keeper,owner_id='resume',epoch=2))
    head=publisher.bootstrap_runtime_session(SignalStreamRuntime(),**scope,
        anchor_cutoff_at=batches[0].cutoff_at,maximum_cutoff_at=batches[-1].cutoff_at)
    assert head.recovery_anchor.states!=head.states
    before=list(storage.writes);durable=keeper.read_head(scope['session_key'])
    try:
        for batch in batches[1:]:publisher.submit(batch).result(3)
        assert storage.writes==before and keeper.read_head(scope['session_key'])==durable
        assert publisher.recovery_counts()['resumed_stored']==3
    finally:publisher.close()


@pytest.mark.parametrize('failure',[None,'wrong_base','wrong_context','wrong_anchor','wrong_status','wrong_span','head_moved'])
def test_concrete_contextual_pending_transitions_promote_stored_clocks_without_republication(monkeypatch,failure):
    import asyncio
    from src.backend.program_signal_source import require_program_signal_publisher
    from src.backend.signal_execution_context import SignalExecutionContext
    from src.backend.signal_stream_runtime_service import StagedSignalTransition
    from tests.test_program_signal_source import executable_contextual_publisher
    from src.backend.completed_signal_cursor_reader import RunningResumeSignalCursorReadAhead
    from src.backend.backtest_market_data import market_day_boundary
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
    import json
    first=_batch();context=SignalExecutionContext('actual-run','backtest',first.session_key,first.configuration_revision,first.source_revision)
    original=executable_contextual_publisher(context,first.catalogs)
    scope=dict(session_key=first.session_key,configuration_revision=first.configuration_revision,
               source_revision=first.source_revision,catalogs=first.catalogs)
    original.bootstrap_session(**scope);batches=[first]
    try:
        head=original.submit(first).result(3)
        for sequence in range(2,5):
            batch=replace(first,batch_sequence=sequence,previous_commit_hash=head,
                cutoff_at=(datetime.fromisoformat(first.cutoff_at)+timedelta(seconds=sequence-1)).isoformat(),
                before_states=first.after_states,occurrences=(),allow_clock_only=True)
            batches.append(batch);head=original.submit(batch).result(3)
    finally:original.close()
    keeper=original._attestor._keeper
    assert keeper.release(first.session_key,owner_id='native-test-owner',epoch=1)
    assert keeper.acquire(first.session_key,owner_id='resume')==2
    queue=TypedSignalPublicationQueue(original._storage,attestor=SignalCursorAttestor(keeper,owner_id='resume',epoch=2))
    client=original._storage._read_storage._client;execute=client.execute
    elapsed=int((datetime.fromisoformat(first.cutoff_at)-market_day_boundary(first.session_key,0)).total_seconds()*1000)
    prefix=V4CommittedPrefix(context.run_id,12899,'00000000-0000-0000-0000-000000000001',
        f'{first.session_key}:{elapsed}','running',('00000000-0000-0000-0000-000000000001',))
    moved=[]
    def transport(sql,**kwargs):
        if sql=="SELECT getSetting('readonly')":return '1'
        if 'FROM arte.trading_commit_v4 ' in sql:return json.dumps(dict(last_sequence=12900 if moved else 12899,batch_id=prefix.last_batch_id,status='running',source_cursor=prefix.source_cursor))
        return execute(sql,**kwargs)
    monkeypatch.setattr(client,'execute',transport)
    options=dict(source_context=context,prefix=prefix,source_keeper=keeper,owner_id='resume',epoch=2)
    if failure=='wrong_context':options['source_context']=replace(context,run_id='foreign')
    if failure=='wrong_status':options['prefix']=replace(prefix,status='completed')
    if failure=='wrong_span':options['batch_span']=0
    if failure in ('wrong_context','wrong_status','wrong_span'):
        try:
            with pytest.raises(ValueError):RunningResumeSignalCursorReadAhead(client,first.catalogs,**options)
        finally:queue.close()
        return
    adapter=RunningResumeSignalCursorReadAhead(client,first.catalogs,**options)
    if failure=='wrong_base':monkeypatch.setattr(original._storage._read_storage,'_client',object())
    if failure=='head_moved':moved.append(True)
    if failure in ('wrong_base','wrong_anchor','head_moved'):
        try:
            with pytest.raises(ValueError):queue.bootstrap_runtime_session(SignalStreamRuntime(),**scope,
                anchor_cutoff_at=batches[1].cutoff_at if failure=='wrong_anchor' else first.cutoff_at,
                maximum_cutoff_at=batches[-1].cutoff_at,recovery_read_ahead=adapter)
        finally:queue.close()
        return
    runtime=SignalStreamRuntime();queue.bootstrap_runtime_session(runtime,**scope,
        anchor_cutoff_at=first.cutoff_at,maximum_cutoff_at=batches[-1].cutoff_at,recovery_read_ahead=adapter)
    assert adapter.read_ahead_counts()['family_packet_queries']==4
    assert require_program_signal_publisher(context,queue) is queue
    durable=keeper.read_head(first.session_key)
    try:
        for batch in batches[1:]:
            stage=StagedSignalTransition(runtime._generation,first.session_key,first.session_key,batch.cutoff_at,
                batch.before_states,batch.after_states,batch.before_admissions,batch.after_admissions,batch.occurrences,{})
            pending=runtime.submit_staged(stage,queue,batch_sequence=batch.batch_sequence,
                previous_commit_hash=batch.previous_commit_hash,configuration_revision=first.configuration_revision,
                source_revision=first.source_revision,catalogs=first.catalogs,allow_clock_only=True)
            asyncio.run(pending.wait_async());runtime.promote_staged(pending)
            assert runtime.typed_source_boundary(datetime.fromisoformat(batch.cutoff_at))[:2]==(batch.batch_sequence,pending.result())
            assert keeper.read_head(first.session_key)==durable
            assert original._storage._active_sequence is None
        assert runtime.typed_source_cursor()[0]==4
        assert queue.recovery_counts()==dict(resumed_stored=3,appended=0,failed=0,remaining_stored=0)
    finally:queue.close()


def ordered_tail_fixture():
    from copy import deepcopy
    from src.backend.signal_stream_typed_publication import _publish_one
    from src.backend.signal_stream_runtime_service import _occurrence,StagedSignalTransition,_freeze_transition
    from tests.test_signal_stream_typed_catalog import _source
    storage=FakeStorage();first=_batch();head=_publish_one(storage,first)
    at=datetime.fromisoformat(first.cutoff_at)+timedelta(seconds=1)
    stream,columns,row=_source();catalog=first.catalogs['stream-1'].catalog
    events=tuple(_occurrence(stream,{**row,'ticker':ticker},columns,as_of=at,definition_revision='definition-1',typed_catalog=catalog) for ticker in ('NOK','RDW'))
    states={'stream-1':{**first.after_states['stream-1'],**{ticker:dict(matching=True,definition_revision='definition-1') for ticker in ('NOK','RDW')}}}
    tail=replace(first,batch_sequence=2,previous_commit_hash=head,cutoff_at=at.isoformat(),before_states=first.after_states,after_states=states,occurrences=events)
    _publish_one(storage,tail)
    scope=dict(session_key=first.session_key,configuration_revision=first.configuration_revision,source_revision=first.source_revision,catalogs=first.catalogs)
    publisher=TypedSignalPublicationQueue(storage);runtime=SignalStreamRuntime()
    publisher.bootstrap_runtime_session(runtime,**scope,anchor_cutoff_at=first.cutoff_at,maximum_cutoff_at=tail.cutoff_at)
    stage=StagedSignalTransition(base_generation=0,before_session_key=first.session_key,session_key=first.session_key,cutoff_at=tail.cutoff_at,before_states=_freeze_transition(tail.before_states),after_states=_freeze_transition(tail.after_states),before_admissions=_freeze_transition({}),after_admissions=_freeze_transition({}),occurrences=_freeze_transition(tuple(reversed(events))),diagnostics=_freeze_transition({}))
    return storage,publisher,runtime,stage,tail,scope


def test_recovered_order_alignment_promotes_original_sealed_order_without_writes():
    from copy import deepcopy
    from src.backend.signal_stream_typed_publication import _publish_one
    storage,publisher,runtime,stage,tail,scope=ordered_tail_fixture()
    rows=deepcopy(storage.rows);writes=list(storage.writes);cursor=runtime.typed_source_cursor()
    try:
        with pytest.raises(ValueError,match='typed cursor cold readback'):
            _publish_one(storage,replace(tail,occurrences=tuple(reversed(tail.occurrences))),verify_existing=True)
        aligned=publisher.align_recovered_transition(stage,batch_sequence=2)
        assert [row['ticker'] for row in aligned.occurrences]==['NOK','RDW']
        assert [row['ticker'] for row in stage.occurrences]==['RDW','NOK']
        assert all(any(value is original for original in stage.occurrences) for value in aligned.occurrences)
        assert aligned.before_states is stage.before_states and aligned.after_states is stage.after_states
        pending=runtime.submit_staged(aligned,publisher,batch_sequence=2,previous_commit_hash=cursor[1],configuration_revision=scope['configuration_revision'],source_revision=scope['source_revision'],catalogs=scope['catalogs'])
        import asyncio
        asyncio.run(pending.wait_async());promoted=runtime.promote_staged(pending)
        assert [row['ticker'] for row in promoted]==['NOK','RDW']
        assert runtime.typed_source_cursor()[0]==2 and runtime.typed_source_cursor()[1]==storage.rows['signal_stream_cursor_commit_typed_v1'][-1]['content_hash']
        assert storage.rows==rows and storage.writes==writes
    finally:publisher.close()


@pytest.mark.parametrize('failure',['missing','duplicate','foreign','row_hash','body','commit_hash','commit_count','wrong_sequence','wrong_cutoff'])
def test_recovered_order_alignment_rejects_unattested_changes_without_promotion(failure):
    from copy import deepcopy
    from src.backend.signal_stream_runtime_service import _freeze_transition,_thaw_transition
    storage,publisher,runtime,stage,tail,scope=ordered_tail_fixture()
    refs=storage.rows['signal_stream_occurrence_ref_typed_v1']
    tailrefs=[row for row in refs if row['batch_sequence']==2]
    if failure=='missing':refs.remove(tailrefs[-1])
    elif failure=='duplicate':refs.append(deepcopy(tailrefs[0]))
    elif failure=='foreign':tailrefs[0]['event_id']='f'*64
    elif failure=='row_hash':tailrefs[0]['content_hash']='f'*64
    elif failure=='body':
        events=_thaw_transition(stage.occurrences);events[0]['evidence']['price']=999
        stage=replace(stage,occurrences=_freeze_transition(events))
    elif failure=='commit_hash':storage.rows['signal_stream_cursor_commit_typed_v1'][-1]['content_hash']='f'*64
    elif failure=='commit_count':storage.rows['signal_stream_cursor_commit_typed_v1'][-1]['occurrence_count']=3
    elif failure=='wrong_cutoff':stage=replace(stage,cutoff_at='2026-09-24T14:00:02+00:00')
    before=deepcopy(storage.rows);writes=list(storage.writes);cursor=runtime.typed_source_cursor()
    try:
        with pytest.raises(ValueError):publisher.align_recovered_transition(stage,batch_sequence=3 if failure=='wrong_sequence' else 2)
        assert storage.rows==before and storage.writes==writes and runtime.typed_source_cursor()==cursor
        assert runtime._typed_pending is None and publisher.recovery_counts()['resumed_stored']==0
    finally:publisher.close()


def test_alignment_new_publication_and_single_event_are_identity_without_storage_reads(monkeypatch):
    storage,publisher,runtime,stage,tail,scope=ordered_tail_fixture()
    try:
        publisher._consumer_recovery.clear()
        monkeypatch.setattr(storage,'read_cursor_rows',lambda *a,**kw:pytest.fail('New publication read stored ordering'))
        assert publisher.align_recovered_transition(stage,batch_sequence=2) is stage
        assert publisher.align_recovered_transition(replace(stage,occurrences=stage.occurrences[:1]),batch_sequence=2).occurrences==stage.occurrences[:1]
    finally:publisher.close()

@pytest.mark.parametrize('change',['closing','consumer_moved'])
def test_order_alignment_rechecks_consumer_owner_without_promoting(monkeypatch,change):
    storage,publisher,runtime,stage,tail,scope=ordered_tail_fixture()
    cursor=runtime.typed_source_cursor();writes=list(storage.writes)
    if change=='closing':
        publisher._closing=True
        monkeypatch.setattr(storage,'read_cursor_rows',lambda *a,**kw:pytest.fail('Closing owner read storage'))
    else:
        read=storage.read_cursor_rows
        def changed(*args,**kwargs):
            rows=read(*args,**kwargs)
            if args[0]=='signal_stream_occurrence_ref_typed_v1':
                publisher._consumer_recovery[stage.session_key]['cursor']=(2,'f'*64)
            return rows
        monkeypatch.setattr(storage,'read_cursor_rows',changed)
    try:
        with pytest.raises(RuntimeError,match='consumer'):
            publisher.align_recovered_transition(stage,batch_sequence=2)
        assert runtime.typed_source_cursor()==cursor and storage.writes==writes
        assert runtime._typed_pending is None
    finally:publisher.close()
