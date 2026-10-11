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


def test_actual_publisher_routes_frozen_native_checkpoint_with_financial_captures(installed):
    import asyncio
    from concurrent.futures import Future
    from tests.test_backtest_typed_publisher import FakeWriter,_publisher,_journal,RUN,DAY,AT
    from src.backend.backtest_native_channel_management_owner import NativeChannelManagementOwner,NativeChannelManagerCheckpoint
    from src.backend.backtest_strategy_one_management import OriginalRiskManagementState
    from src.trading_runtime.arte_portfolio_snapshot import CapturedPortfolioSnapshot
    policy=owner_case(installed)[0].policy
    owner=NativeChannelManagementOwner(run_id=RUN,session_date=DAY,policy=policy)
    state=NativeChannelManagerCheckpoint(OriginalRiskManagementState(300000,(),(),()),owner.freeze(boundary_ms=300000))
    class CheckpointWriter(FakeWriter):
        journal_profile='backtest_v4'
        def submit_base_v4(self,batch): return FakeWriter.submit(self,batch)
        def capture(self,kind,**kwargs):
            self.captures[kind]=kwargs
            receipt=Future();receipt.set_result(kwargs['journal_batch_id']);return receipt
        def submit_native_channel_manager_snapshot(self,**kwargs): return self.capture('native',**kwargs)
        def submit_broker_match_snapshot(self,**kwargs): return self.capture('broker',**kwargs)
        def submit_oms_observation_snapshot(self,**kwargs): return self.capture('oms',**kwargs)
        def submit_running_portfolio_snapshot(self,**kwargs): return self.capture('portfolio',**kwargs)
    async def exercise():
        writer=CheckpointWriter();writer.captures={}
        publisher=_publisher(_journal(),writer)
        # Reader/principal admission is covered separately; this verifies the
        # actual checkpoint coordinator dispatch with controlled writer receipts.
        publisher._native_channel_exit_source=SimpleNamespace(policy=policy,contexts=())
        portfolio=CapturedPortfolioSnapshot(run_id=RUN,account_id='DU1',state_revision=2,
            snapshot_at=AT,account_key='DU1',control_mode='enabled',sync_state='entries_blocked',
            broker_snapshot_id='',observed_at=None,stale_reason='',peak_net_liquidation=0.0,
            realized_pnl_baseline=None,selected_policy=None,disabled_strategies=(),commands=(),
            requests=(),reservations=(),allocations=(),reconciliation=())
        receipt=await publisher.enqueue_checkpoint(boundary_id=f'{DAY}:300000',manager_state=state,
            broker_state=(300000,{}),oms_observations={},portfolio_captures=(portfolio,))
        assert receipt.last_sequence==2
        assert set(writer.captures)=={'native','broker','oms','portfolio'}
        assert writer.captures['native']['capture'] is state.pending
        assert writer.captures['native']['state'] is state.inherited
        assert writer.captures['native']['native_channel_contexts']==()
        assert all(value['journal_batch_id']==receipt.last_batch_id for value in writer.captures.values())
    asyncio.run(exercise())
