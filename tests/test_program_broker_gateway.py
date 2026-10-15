import asyncio
from concurrent.futures import Future
from uuid import uuid4

import pytest

from tests.test_program_execution_journal import assembly, AT, ZERO
from src.backend.program_execution_journal import ProgramExecutionJournal
from src.trading_runtime.program_broker_gateway import ProgramBrokerGateway
from src.trading_runtime.ibkr_client import IbkrClientPortalAdapter
from src.trading_runtime.arte_order_cancel_v4 import order_cancel_batch_v4


def test_native_cancel_waits_for_receipt_but_cannot_dispatch_without_context_bound_assembly(monkeypatch):
    writer, lease, _ = assembly(monkeypatch)
    async def run():
        journal = await ProgramExecutionJournal.from_native(writer)
        broker = object.__new__(IbkrClientPortalAdapter)
        from src.trading_runtime.domain import TradingMode
        broker.mode = TradingMode.PAPER
        calls = []
        async def cancel(account, order):
            calls.append((account, order))
            return {'order_id': order}
        broker.cancel_order = cancel
        gateway = ProgramBrokerGateway(broker, journal)
        record = journal.append(run_id=journal.run_id, category='command', entity_type='order_cancel',
            entity_id='broker-1', account_id='account', event_time=AT,
            payload=dict(strategy_id=lease.registration.strategy_id, strategy_revision=lease.registration.revision,
                correlation_id=journal.run_id, causation_id='intent-1', intent_id='intent-1',
                order_group_id='group-1', ticker='ABC', reason='program_native_oms_cancel'))
        unit = order_cancel_batch_v4(record, run_month=AT.date().replace(day=1), attempt_id=str(uuid4()),
            batch_id=str(uuid4()), prior_batch_id=ZERO, source_cursor='actual-source',
            program_registration=lease.registration)
        future = Future()
        writer.submit_order_cancel_v4 = lambda _: future
        waiting = asyncio.create_task(gateway.prepare(unit, operation='cancel', account_id='account', order_id='broker-1'))
        await asyncio.sleep(0)
        assert not calls
        with pytest.raises(RuntimeError, match='context-bound'):
            await gateway.cancel_order('account', 'broker-1')
        future.set_result(unit.base.batch_id)
        await waiting
        with pytest.raises(RuntimeError, match='context-bound'):
            await gateway.cancel_order('account', 'broker-1')
        assert calls == [] and journal.fenced_sequence == 1
    try:
        asyncio.run(run())
    finally:
        lease.release()


def test_unprepared_native_mutations_and_warning_reply_fail_before_broker(monkeypatch):
    writer, lease, _ = assembly(monkeypatch)
    async def run():
        journal = await ProgramExecutionJournal.from_native(writer)
        broker = object.__new__(IbkrClientPortalAdapter)
        from src.trading_runtime.domain import TradingMode
        broker.mode = TradingMode.PAPER
        gateway = ProgramBrokerGateway(broker, journal)
        with pytest.raises(RuntimeError, match='context-bound'):
            await gateway.cancel_order('account', 'broker-1')
        with pytest.raises(RuntimeError, match='normalized policy'):
            await gateway.reply('warning-1', True)
        with pytest.raises(AttributeError):
            gateway.canonical_place_orders
    try:
        asyncio.run(run())
    finally:
        lease.release()
