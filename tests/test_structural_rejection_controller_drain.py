"""Actual controller drain with a controlled OMS response, no financial claim."""
import asyncio
import pytest


@pytest.mark.parametrize('kind',('valid','intent','account','assignment','ticker','action','state','resized'))
def test_controller_only_retires_exact_accepted_exit(monkeypatch,kind):
    from test_backtest_structural_rejection_journal import journal_fixture
    from src.backend.replay_run_service import ReplayRunController
    from src.trading_runtime.order_management import OrderManagementState
    from src.trading_runtime import structural_rejection_runtime_submission as submission
    journal,confirmation,intent,context=journal_fixture(monkeypatch)
    owner=context.profile.owner;runtime=owner.manager.runtime
    controller=object.__new__(ReplayRunController)
    controller._runtime=runtime;controller._strategy_one_manager=owner.manager
    requests=owner.requests(boundary_ms=confirmation.boundary_ms)
    completed=[]
    monkeypatch.setattr(submission,'complete_runtime_structural_rejection_exit',
        lambda actual,cap:completed.append((actual,cap)))
    async def submit(cap):
        assert cap is confirmation
        group=dict(group_id='G2',state=OrderManagementState.WORKING,intent_id=intent.intent_id,
            account_id=confirmation.request.financial.account_id,
            assignment_id=confirmation.request.financial.assignment_id,ticker=intent.ticker,action='exit')
        if kind in {'intent','account','assignment','ticker','action'}:
            field={'intent':'intent_id','account':'account_id','assignment':'assignment_id'}.get(kind,kind)
            group[field]='foreign'
        elif kind=='state':group['state']=OrderManagementState.REJECTED
        return [dict(decision={'status':'resized' if kind=='resized' else 'approved'},order_group=group)]
    runtime.submit_structural_rejection_exit=submit
    try:
        if kind=='valid':
            asyncio.run(controller._drain_structural_rejection_requests(requests,(confirmation,),
                boundary=confirmation.boundary_ms))
            assert owner.requests(boundary_ms=confirmation.boundary_ms)==()
            assert completed==[(runtime,confirmation)]
        else:
            with pytest.raises(RuntimeError,match='exact accepted OMS'):
                asyncio.run(controller._drain_structural_rejection_requests(requests,(confirmation,),
                    boundary=confirmation.boundary_ms))
            assert owner.requests(boundary_ms=confirmation.boundary_ms)==requests and not completed
    finally:journal.close()
