"""Native component receipt tests with isolated broker/ClickHouse transports."""
import asyncio
from concurrent.futures import Future
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from tests.test_program_execution_journal import assembly, AT
from tests.test_columnar_program_coordinator import inputs
from src.backend.program_execution_journal import ProgramExecutionJournal
from src.backend.program_runtime_assembly import ProgramRuntimeAssembly
from src.backend.program_broker_executions import ProgramBrokerExecutionIntake
from src.backend.program_pending_publication import commit_program_pending_records
from src.trading_runtime.columnar_program_actor import AssignedProgramStrategy
from src.trading_runtime.strategy_engine import StrategyAssignment, StrategyPermissions, AssignmentStatus
from src.trading_runtime.order_management import OrderManagementEngine, _ManagedOrderGroup, OrderManagementState
from src.trading_runtime.strategy_orders import StrategyOrderPlan
from src.trading_runtime.ibkr_schema import OrderRequest, Execution
from src.trading_runtime.ibkr_client import IbkrClientPortalAdapter
from src.trading_runtime.domain import TradingMode
from src.trading_runtime.signals import StrategyIntent


def test_actual_broker_fills_and_later_fee_project_only_after_receipt_and_deduplicate(monkeypatch):
    writer, lease, _ = assembly(monkeypatch)
    async def run():
        journal = await ProgramExecutionJournal.from_native(writer)
        release, *_ = inputs()
        assignment = StrategyAssignment('assignment', 'shared-program', 62, 'account', 'ABC', 1,
            AssignmentStatus.WATCHING, StrategyPermissions(enter=True), {})
        actor = AssignedProgramStrategy(release, (assignment,), {'assignment': 'listing'})
        request = OrderRequest('account', 1, 'LMT', 'BUY', quantity=5, price=10., cOID='client', ticker='ABC')
        intent = StrategyIntent('intent', 'ABC', AT, 'enter_long', 5., 10.,
            metadata={'assignment_id': 'assignment'})
        manager = object.__new__(OrderManagementEngine)
        manager._groups = {'group': _ManagedOrderGroup('group', intent, 'account', StrategyOrderPlan((request,)),
            OrderManagementState.WORKING, AT, AT, [request], broker_order_ids=['broker'],
            broker_order_roles={'broker': 'entry'}, broker_order_request_indexes={'broker': 0})}
        manager._group_by_broker_id, manager._group_by_client_id = {'broker': 'group'}, {'client': 'group'}
        owner = object.__new__(ProgramRuntimeAssembly)
        owner.journal = journal
        owner.runtime = SimpleNamespace(run_id=journal.run_id, config=SimpleNamespace(account_ids=('account',)),
            strategy=actor, order_manager=manager)
        broker = object.__new__(IbkrClientPortalAdapter)
        broker.mode = TradingMode.PAPER
        rows = [Execution('fill', 'ABC', 'B', 'client', AT, int(AT.timestamp()*1000), 5., 10., 'broker', 'account', 1)]
        async def trades(days): return list(rows)
        broker.trades = trades
        owner.broker = broker
        futures = []
        def submit(unit):
            future = Future()
            futures.append((unit, future))
            return future
        writer.submit_exact_execution_v4 = submit
        writer.submit_base_v4 = submit
        async def flush():
            return await commit_program_pending_records(journal, attempt_id='00000000-0000-0000-0000-000000000001',
                expected_config={'strategy_id': lease.registration.strategy_id, 'strategy_revision': lease.registration.revision},
                run_month=AT.date().replace(day=1), source_cursor='actual-broker-poll')
        owner.flush = flush
        intake = ProgramBrokerExecutionIntake(owner)
        async def await_submission(task):
            for _ in range(200):
                if futures:
                    return
                if task.done():
                    await task
                    raise AssertionError('No native publication')
                await asyncio.sleep(.01)
            raise AssertionError('Native publication did not arrive')
        waiting = asyncio.create_task(intake.poll(at=AT))
        await await_submission(waiting)
        assert not actor.position_for('assignment').position_open
        assert len(futures) == 1
        unit, receipt = futures.pop(0)
        receipt.set_result(unit.base.batch_id)
        result = await waiting
        assert result.committed_fills == 1 and float(actor.position_for('assignment').quantity) == 5.
        assert (await intake.poll(at=AT)).committed_fills == 0
        rows[0] = replace(rows[0], commission=1.)
        waiting = asyncio.create_task(intake.poll(at=AT))
        await await_submission(waiting)
        assert float(actor.position_for('assignment').fees) == 0.
        unit, receipt = futures.pop(0)
        receipt.set_result(getattr(unit, 'base', unit).batch_id)
        assert (await waiting).committed_commissions == 1
        assert float(actor.position_for('assignment').fees) == 1.
        assert (await intake.poll(at=AT)).committed_commissions == 0
        rows.append(replace(rows[0], execution_id='external', order_id='manual', order_ref='manual',
            trade_time=AT + timedelta(seconds=1), trade_time_r=int((AT + timedelta(seconds=1)).timestamp()*1000)))
        deferred = await intake.poll(at=AT)
        assert deferred.deferred == 1 and not futures
        waiting = asyncio.create_task(intake.poll(at=AT + timedelta(seconds=1)))
        await await_submission(waiting)
        # Account source retains this actual external fill; it cannot create
        # a programme-owned position without an exact native OMS attribution.
        while not waiting.done():
            if futures:
                unit, receipt = futures.pop(0)
                receipt.set_result(getattr(unit, 'base', unit).batch_id)
            await asyncio.sleep(.01)
        assert (await waiting).deferred == 0
        assert float(actor.position_for('assignment').quantity) == 5.
        rows[0] = replace(rows[0], price=11.)
        with pytest.raises(ValueError, match='changed its committed fill'):
            await intake.poll(at=AT + timedelta(seconds=1))
        with pytest.raises(RuntimeError, match='cold receipt recovery'):
            intake.require_current()
    try:
        asyncio.run(run())
    finally:
        lease.release()
