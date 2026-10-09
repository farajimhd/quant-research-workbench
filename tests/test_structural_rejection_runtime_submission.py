"""Actual broker/OMS financial reads; confirmation/source issuance are seams."""
import asyncio
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
import pytest

from src.trading_runtime import structural_rejection_runtime_submission as submission


@pytest.mark.parametrize('kind',('bare','mixed','account','altered-intent'))
def test_runtime_rejects_bad_exit_channel_before_journal_or_actors(monkeypatch,kind):
    from test_backtest_structural_rejection_journal import journal_fixture
    from src.trading_runtime.runtime import TradingRuntime
    from src.trading_runtime.signals import StrategyEvaluation
    journal,confirmation,intent,_=journal_fixture(monkeypatch)
    runtime=object.__new__(TradingRuntime)
    kwargs={};account=confirmation.request.financial.account_id
    if kind!='bare':kwargs['structural_rejection_confirmation']=confirmation
    if kind=='mixed':kwargs['numbered_exit_assignment_id']='A1'
    elif kind=='account':account='foreign'
    elif kind=='altered-intent':intent=replace(intent,quantity=999.)
    initial=journal.latest_sequence(journal.run_id)
    try:
        with pytest.raises(ValueError):
            asyncio.run(runtime._execute_intents(StrategyEvaluation(intents=(intent,)),account,None,**kwargs))
        assert journal.latest_sequence(journal.run_id)==initial
    finally:journal.close()


@pytest.mark.parametrize('kind',('valid','quantity','permission','status','identity','duplicate','portfolio','clock'))
def test_fresh_actor_state_is_required_after_confirmation(monkeypatch,kind):
    from src.backend.backtest_journal_memory import BacktestMemoryJournal
    from src.backend.backtest_market_data import market_day_boundary
    from src.backend.backtest_strategy_one_financial import read_strategy_one_financial_view
    from src.backend import backtest_profit_armed_structural_rejection_management as manager
    from src.trading_runtime.runtime import TradingRuntime,RunConfig,RunMode
    from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter,SimulationConfig,_Position
    from src.trading_runtime.domain import TradingMode
    from src.trading_runtime.strategy_engine import StrategyAssignment,StrategyPermissions,AssignmentStatus
    from src.trading_runtime import profit_armed_structural_rejection_financial_checkpoint as financial
    from src.trading_runtime import profit_armed_structural_rejection_profile as profiles
    from src.trading_runtime import profit_armed_structural_rejection_snapshot as snapshots
    from tests.test_trading_runtime import _NoopStrategy

    async def run():
        day=date(2026,8,18);run_id='00000000-0000-0000-0000-000000000010'
        journal=BacktestMemoryJournal(run_id=run_id)
        broker=SimulatedBrokerAdapter(['DU1'],SimulationConfig(initial_cash=10000.),
            mode=TradingMode.BACKTEST,initial_time=market_day_boundary(day,0),fixed_bar_mode=True)
        runtime=TradingRuntime(RunConfig(RunMode.BACKTEST,'noop',1,('DU1',),day,run_id=run_id),
            broker,_NoopStrategy(),journal,intent_planner=SimpleNamespace())
        await runtime.initialize()
        selected=StrategyAssignment('A1','noop',1,'DU1','AAA',123,AssignmentStatus.MANAGING,
            StrategyPermissions(exit=True),{})
        assignments=[selected];runtime.strategy.assignments=lambda:tuple(assignments)
        broker._positions['DU1'][123]=_Position(conid=123,ticker='AAA',quantity=10,avg_cost=10.)
        state=SimpleNamespace(boundary_ms=25000)
        runtime.last_event_time=market_day_boundary(day,25000)
        owner=SimpleNamespace(manager=SimpleNamespace(runtime=runtime));profile=SimpleNamespace(owner=owner)
        monkeypatch.setattr(profiles,'require_native_structural_rejection_profile',lambda *a,**k:profile)
        monkeypatch.setattr(manager,'require_structural_rejection_capture',lambda *a,**k:owner)
        monkeypatch.setattr(snapshots,'project_structural_rejection_snapshot',lambda **k:
            SimpleNamespace(snapshot={'content_hash':'a'*64}))
        captured=financial.issue_financial_capture(profile,state,sequence=9)
        expected=await read_strategy_one_financial_view(selected,broker,runtime.order_manager)
        confirmation=SimpleNamespace(request=SimpleNamespace(financial=expected))
        class Lookup:
            def __getitem__(self,key):return (owner,confirmation.request,SimpleNamespace(profile=profile),captured)
        monkeypatch.setattr(submission,'_ISSUED',Lookup())
        def require(cap,*,runtime):
            assert cap is confirmation
            if runtime.last_event_time!=market_day_boundary(day,25000):raise ValueError('changed frontier')
        monkeypatch.setattr(submission,'require_structural_rejection_confirmation',require)
        if kind=='quantity':broker._positions['DU1'][123].quantity=9
        elif kind=='permission':assignments[0]=replace(selected,permissions=StrategyPermissions(exit=False))
        elif kind=='status':assignments[0]=replace(selected,status=AssignmentStatus.WATCHING)
        elif kind=='identity':assignments[0]=replace(selected,ticker='BBB')
        elif kind=='duplicate':assignments.append(selected)
        elif kind=='portfolio':runtime.portfolio.states['DU1'].peak_net_liquidation+=1
        elif kind=='clock':runtime.last_event_time=market_day_boundary(day,25100)
        initial_sequence=journal.latest_sequence(run_id)
        try:
            if kind=='valid':
                assert await submission.require_runtime_structural_rejection_exit(runtime,confirmation,full_capture=True)==expected
            else:
                with pytest.raises(ValueError):
                    await submission.require_runtime_structural_rejection_exit(runtime,confirmation,full_capture=True)
            assert journal.latest_sequence(run_id)==initial_sequence
        finally:journal.close()
    asyncio.run(run())
