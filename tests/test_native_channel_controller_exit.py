"""Controller ordering with real pending owner and controlled I/O transports."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.test_backtest_native_channel_qualification import installed, unit  # noqa: F401
from tests.test_backtest_native_channel_management_owner import owner_case
from tests.test_native_channel_runtime_exit import runtime_case
from src.backend.replay_run_service import ReplayRunController
from src.trading_runtime.runtime import RunMode


def test_controller_prepares_declared_features_once_with_actual_horizons(installed,monkeypatch):
    from tests.test_backtest_native_channel_session_source import session_case
    from src.backend.backtest_market_data import SESSION_OPEN_OFFSET_MS
    client,packet,_,args=session_case(installed,monkeypatch)
    owner,_,_,_,_=owner_case(installed,strategy_number=2)
    monkeypatch.setattr('src.market_engine.native_causal_channel_contract.producer_implementation_hash',
        lambda:args['producer_source_hash'])
    monkeypatch.setattr('src.backend.replay_run_service.ReplayRunController._fixed_through_boundary_ms',
        lambda self:packet.request.through_day_boundary_ms//2-SESSION_OPEN_OFFSET_MS)
    controller=ReplayRunController.__new__(ReplayRunController)
    controller._native_channel_read_client=client
    controller._fixed_keeper_session=SimpleNamespace(client=args['authority'].keeper)
    controller._strategy=SimpleNamespace(assignments=lambda:tuple(
        SimpleNamespace(ticker=ticker) for ticker in args['tickers']))
    manager=SimpleNamespace(_native_channel_owner=owner)
    asyncio.run(controller._prepare_native_channel_manager_features(manager,
        market=args['market'],start_after=0))
    assert owner._feature_session[1]==packet.request.through_day_boundary_ms//2
    reads=len(client.sql)
    with pytest.raises(ValueError):
        asyncio.run(controller._prepare_native_channel_manager_features(manager,
            market=args['market'],start_after=1))
    assert len(client.sql)==reads
    asyncio.run(controller._prepare_native_channel_manager_features(SimpleNamespace(),
        market=args['market'],start_after=0))


def controller_case(installed,monkeypatch,*,reject=False,changed=False):
    owner,_,evidence,quote,args=owner_case(installed,strategy_number=2)
    first=owner.stage(evidence['prepared'],quote,**args)
    other=args['financial'].assignment_id+'-other'
    second_args=dict(args,financial=replace(args['financial'],assignment_id=other),
        original_entry=replace(args['original_entry'],assignment_id=other),
        group=replace(args['group'],assignment_id=other))
    second=owner.stage(evidence['prepared'],quote,**second_args)
    runtime,_,execution,_=runtime_case(installed,monkeypatch)
    trace=[]
    assignments=tuple(SimpleNamespace(account_id=r.financial.account_id,
        assignment_id=r.financial.assignment_id,ticker=r.financial.ticker) for r in (first,second))
    runtime.strategy=SimpleNamespace(assignments=lambda:assignments)
    runtime.broker=object()
    async def read(assignment,*_args):
        trace.append(('read',assignment.assignment_id))
        view=next(r.financial for r in (first,second) if r.financial.assignment_id==assignment.assignment_id)
        return replace(view,pending_exit=True) if changed else view
    monkeypatch.setattr('src.backend.backtest_strategy_one_financial.read_strategy_one_financial_view',read)
    def prepare(_source,_receipt,capture,request,**_metadata):
        trace.append(('prepare',len(capture.items)))
        return request,execution
    monkeypatch.setattr('src.backend.backtest_native_channel_execution_source.prepare_native_channel_execution_source',prepare)
    async def submit(request,_source):
        trace.append(('submit',request.financial.assignment_id))
        return [dict(decision=dict(status='rejected' if reject else 'approved'),
                     order_group=None if reject else dict(filled_quantity=0))]
    runtime.submit_native_channel_exit=AsyncMock(side_effect=submit)
    publisher=SimpleNamespace(_native_channel_exit_source=execution.publisher_source,
        enqueue_pending=lambda:trace.append(('enqueue',None)))
    async def fence(): trace.append(('fence',None))
    publisher.await_fence=AsyncMock(side_effect=fence)
    controller=ReplayRunController.__new__(ReplayRunController)
    controller.run_id=owner.run_id
    controller.definition=SimpleNamespace(mode=RunMode.BACKTEST)
    controller._runtime=runtime
    controller._strategy_one_manager=SimpleNamespace(_native_channel_owner=owner,
        contract=SimpleNamespace(native_channel_management_policy=owner.policy))
    controller._journal_publisher=publisher
    controller._source_cursor={'boundary_ms':args['boundary_ms']}
    async def checkpoint(_time):
        trace.append(('checkpoint',len(owner.pending_requests(boundary_ms=args['boundary_ms']))))
        return execution.receipt
    controller._save_restart_checkpoint_responsive=AsyncMock(side_effect=checkpoint)
    return controller,owner,runtime,trace,args['boundary_ms']


def test_controller_rechecks_and_checkpoints_remaining_inventory_after_each_exit(installed,monkeypatch):
    controller,owner,runtime,trace,boundary=controller_case(installed,monkeypatch)
    try:
        asyncio.run(controller._confirm_native_channel_checkpoint(
            owner.pending_requests(boundary_ms=boundary),event_time=runtime.last_event_time))
        assert [n for op,n in trace if op=='checkpoint']==[2,1]
        assert [n for op,n in trace if op=='prepare']==[2,1]
        assert [op for op,_ in trace]==['read','read','checkpoint','prepare','submit','enqueue','fence',
                                     'read','checkpoint','prepare','submit','enqueue','fence']
        assert owner.pending_requests(boundary_ms=boundary)==()
    finally: runtime.journal.close()


@pytest.mark.parametrize('defect',['reject','changed','fence'])
def test_controller_preserves_pending_on_rejection_or_changed_financial_view(installed,monkeypatch,defect):
    controller,owner,runtime,trace,boundary=controller_case(installed,monkeypatch,
        reject=defect=='reject',changed=defect=='changed')
    pending=owner.pending_requests(boundary_ms=boundary)
    if defect=='fence':
        controller._journal_publisher.await_fence=AsyncMock(side_effect=RuntimeError('Publication failed'))
    try:
        with pytest.raises(RuntimeError):
            asyncio.run(controller._confirm_native_channel_checkpoint(pending,event_time=runtime.last_event_time))
        assert owner.pending_requests(boundary_ms=boundary)==pending
        assert not any(op=='fence' for op,_ in trace)
        if defect=='changed': runtime.submit_native_channel_exit.assert_not_awaited()
    finally: runtime.journal.close()
