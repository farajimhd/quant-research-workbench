"""Real service/runtime journal routes; Portfolio and OMS producers are explicit facades.

These controls establish ordered cancellation and terminal rejection semantics,
not financial source certification or a real broker fill.
"""
import asyncio
from dataclasses import dataclass,replace
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from src.backend.replay_run_service import ReplayRunController
from src.trading_runtime.original_risk_checkpoint import OriginalRiskCheckpointRequest,OriginalRiskCheckpointReference
from src.trading_runtime.order_management import OrderManagementState
from src.trading_runtime.strategy_followthrough_exit import followthrough_exit_intent
from test_original_risk_diagnostic_native import diagnostic
from test_profit_giveback_runtime_route import runtime_fixture
from test_strategy_forty_two_management import prepared_manager


def native_drain_case():
    runtime,financial,_=runtime_fixture()
    runtime.config.strategy_revision=68
    runtime.config.anchor_date=date(2026,1,1)
    runtime.last_event_time=None
    financial=replace(financial,ticker='TEST')
    requests=tuple(OriginalRiskCheckpointRequest(diagnostic(),replace(financial,assignment_id=f'held-{n}'),
        str(UUID(int=100+n))) for n in (1,2))
    reference=OriginalRiskCheckpointReference(str(UUID(int=11)),9,'a'*64,str(UUID(int=12)),'b'*64)
    references=tuple(replace(request.diagnostic,checkpoint=reference) for request in requests)
    manager,_,_,_=prepared_manager(68)
    manager._original_risk_requests={(r.financial.account_id,r.financial.assignment_id,r.financial.ticker):r
                                    for r in requests}
    controller=object.__new__(ReplayRunController)
    controller._strategy_one_manager=manager;controller._runtime=runtime
    async def approve(intent,**kwargs):
        return SimpleNamespace(payload=lambda:{'status':'approved'}),replace(intent,metadata={
            'assignment_id':kwargs['assignment_id']})
    runtime.portfolio.approve=AsyncMock(side_effect=approve)
    @dataclass
    class Group:
        group_id:str
        intent_id:str
        account_id:str
        assignment_id:str
        ticker:str
        action:str
        state:OrderManagementState=OrderManagementState.WORKING
        filled_quantity:float=0.
    def group(intent,account_id,state=OrderManagementState.WORKING):
        return Group('group-'+intent.intent_id,intent.intent_id,account_id,
            intent.metadata['assignment_id'],intent.ticker,intent.action,state)
    return controller,runtime,manager,requests,references,group


def test_cancel_between_two_native_submissions_waits_for_ordered_clear():
    async def run():
        controller,runtime,manager,requests,references,group=native_drain_case()
        second=asyncio.Event();release=asyncio.Event();order=[]
        async def submit(intent,*,account_id,event):
            order.append(intent.intent_id)
            if len(order)==2:
                second.set();await release.wait()
            return group(intent,account_id)
        runtime.order_manager.submit_intent=AsyncMock(side_effect=submit)
        task=asyncio.create_task(controller._drain_original_risk_requests(requests,references,boundary=100000))
        await second.wait();task.cancel();await asyncio.sleep(0)
        assert not task.done()
        assert manager.original_risk_requests(boundary_ms=100000)==requests
        assert runtime.journal.pending_record_count==2
        release.set()
        with pytest.raises(asyncio.CancelledError):await task
        assert manager.original_risk_requests(boundary_ms=100000)==()
        assert runtime.order_manager.submit_intent.await_count==2
        assert order==[followthrough_exit_intent(r.witness,r.financial,session_date=date(2026,1,1),
            source_entry_intent_id=r.source_entry_intent_id,strategy_number=68,diagnostic=d).intent_id
            for r,d in zip(requests,references)]
        assert runtime.journal.pending_record_count==2
        runtime.journal.close()
    asyncio.run(run())


@pytest.mark.parametrize('state',[OrderManagementState.REJECTED,OrderManagementState.OUTCOME_UNKNOWN,
                                OrderManagementState.CANCELLED])
def test_unaccepted_second_native_exit_preserves_pending_for_terminal_failure(state):
    async def run():
        controller,runtime,manager,requests,references,group=native_drain_case()
        async def submit(intent,*,account_id,event):
            selected=OrderManagementState.WORKING if intent.metadata['assignment_id']=='held-1' else state
            return group(intent,account_id,selected)
        runtime.order_manager.submit_intent=AsyncMock(side_effect=submit)
        with pytest.raises(RuntimeError,match='exact accepted OMS result'):
            await controller._drain_original_risk_requests(requests,references,boundary=100000)
        assert manager.original_risk_requests(boundary_ms=100000)==requests
        assert runtime.order_manager.submit_intent.await_count==2
        runtime.journal.close()
    asyncio.run(run())


def test_actual_v4_publisher_frozen_target_excludes_new_native_exit_suffix():
    from test_backtest_typed_publisher import FakeWriter,_journal,_publisher,DAY,RUN
    async def run():
        controller,runtime,manager,requests,references,group=native_drain_case()
        runtime.journal.close();runtime.journal=_journal();runtime.run_id=RUN
        runtime.config.anchor_date=DAY
        def later(d):
            return replace(d,current=replace(d.current,boundary_ms=400000),
                newest=replace(d.newest,boundary_ms=400000,session_date=DAY.isoformat()),
                prior=replace(d.prior,boundary_ms=395000,session_date=DAY.isoformat()))
        requests=tuple(replace(r,diagnostic=later(r.diagnostic)) for r in requests)
        references=tuple(later(d) for d in references)
        manager._original_risk_requests={(r.financial.account_id,r.financial.assignment_id,r.financial.ticker):r
                                        for r in requests}
        started=asyncio.Event()
        class Writer(FakeWriter):
            journal_profile='backtest_v4'
            def submit_base_v4(self,batch):
                result=FakeWriter.submit(self,batch);started.set();return result
        writer=Writer(automatic=False);publisher=_publisher(runtime.journal,writer)
        task=publisher.enqueue_pending()
        await started.wait()
        async def submit(intent,*,account_id,event):return group(intent,account_id)
        runtime.order_manager.submit_intent=AsyncMock(side_effect=submit)
        await controller._drain_original_risk_requests(requests,references,boundary=400000)
        assert manager.original_risk_requests(boundary_ms=400000)==()
        assert len(writer.submitted)==1
        assert writer.submitted[0].last_sequence==2
        writer.receipts[0].set_result(writer.submitted[0].batch_id)
        receipt=await task
        assert receipt.last_sequence==2
        assert runtime.journal.pending_record_count==2
        assert len(writer.submitted)==1
        assert [r.entity_type for r in runtime.journal.unfenced_records()]==['strategy_intent']*2
        runtime.journal.close()
    asyncio.run(run())
