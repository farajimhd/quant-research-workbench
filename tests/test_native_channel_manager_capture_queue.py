from threading import get_ident
from types import SimpleNamespace
from dataclasses import replace

import pytest

from tests.test_backtest_native_channel_qualification import installed,unit  # noqa: F401
from tests.test_backtest_native_channel_management_owner import owner_case
from src.trading_runtime.strategy_one_management_snapshot import restore_manager_snapshot
from src.backend.backtest_native_channel_manager_recovery import reprove_native_channel_manager_snapshot


@pytest.mark.parametrize('failure',[False,True])
def test_frozen_capture_is_reproved_on_actual_journal_worker(installed,monkeypatch,failure):
    from src.trading_runtime import arte_journal_writer as writer
    from src.trading_runtime import strategy_one_management_snapshot as snapshots
    from src.trading_runtime import native_channel_manager_publication as publication
    owner,rows,context,quote,args=owner_case(installed)
    owner.stage(context['prepared'],quote,**args)
    frozen=owner.freeze(boundary_ms=args['boundary_ms'])
    state=restore_manager_snapshot(rows.inherited)
    from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch
    class ControlledDispatch(TypedInsertDispatch):
        def __init__(self): pass
    client=SimpleNamespace(native_channel_management_policy=owner.policy,manager_keeper_session=object(),
        execute=lambda *_a,**_k:'',typed_insert_strict=True,typed_insert_dispatch=ControlledDispatch())
    monkeypatch.setattr(writer,'_v4_preflight',lambda _client:None)
    monkeypatch.setattr(writer,'_verify_run_identity',lambda _client,_run:
        dict(mode='backtest',account_ids=(args['financial'].account_id,)))
    monkeypatch.setattr(snapshots,'project_manager_snapshot',lambda **_kwargs:rows.inherited)
    calls=[]
    def issue(_client,captured,*,policy,contexts,**_kwargs):
        assert len(reprove_native_channel_manager_snapshot(captured,policy,contexts))==1
        calls.append(('issue',get_ident(),captured.snapshot['content_hash']))
        return captured
    def publish(_client,_session,captured):
        calls.append(('publish',get_ident(),captured.snapshot['content_hash']))
        if failure: raise TimeoutError('Controlled lost publication response')
    monkeypatch.setattr(publication,'issue_native_channel_manager_publication',issue)
    monkeypatch.setattr(publication,'publish_native_channel_manager_publication',publish)
    journal=writer.ArteJournalWriter(client,run_id=owner.run_id,journal_profile='backtest_v4',coalesce_batches=False)
    journal._last_commit_id=context['batch_id']
    try:
        with pytest.raises(ValueError):
            journal.submit_native_channel_manager_snapshot(capture=replace(frozen,run_id='foreign'),state=state,
                checkpoint_sequence=rows.snapshot['checkpoint_sequence'],journal_batch_id=context['batch_id'])
        with pytest.raises(ValueError):
            journal.submit_native_channel_manager_snapshot(capture=frozen,state=state,
                checkpoint_sequence=rows.snapshot['checkpoint_sequence'],journal_batch_id=context['batch_id'],
                native_channel_contexts=(object(),))
        assert calls==[]
        receipt=journal.submit_native_channel_manager_snapshot(capture=frozen,state=state,
            checkpoint_sequence=rows.snapshot['checkpoint_sequence'],journal_batch_id=context['batch_id'])
        if failure:
            with pytest.raises(TimeoutError): receipt.result(timeout=5)
        else:
            assert receipt.result(timeout=5)==context['batch_id']
            assert journal.metrics()['publish_by_unit']['_ManagerSnapshotUnit']['units']==1
        assert [item[0] for item in calls]==['issue','publish']
        assert all(item[1]!=get_ident() for item in calls)
        assert calls[0][2]==calls[1][2]
    finally:
        if failure:
            with pytest.raises(RuntimeError): journal.close()
        else: journal.close()
    assert not journal._thread.is_alive()
