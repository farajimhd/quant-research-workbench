from __future__ import annotations

import asyncio
from concurrent.futures import Future
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

from src.trading_runtime.arte_command_dispatcher import (
    ArteCommandDispatcher, CommandQueueFull,
)
from src.trading_runtime.arte_journal_projection import order_command_batch
from src.trading_runtime.ibkr_schema import OrderRequest


def _command():
    now = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)
    request = OrderRequest(acctId="DU1", conid=123, cOID=str(uuid4()),
                           ticker="TEST", orderType="LMT", side="BUY",
                           quantity=5, price=12.34)
    batch = order_command_batch(
        request, run_id="live:DU1", run_month=date(2026, 8, 1),
        attempt_id=str(uuid4()), batch_id=str(uuid4()),
        prior_batch_id="00000000-0000-0000-0000-000000000000",
        sequence=1, source_cursor="command-1", run_status="running",
        command_id=str(uuid4()), created_at=now, recorded_at=now,
    )
    return batch, request


class _Writer:
    def __init__(self) -> None:
        self.receipts: list[Future[str]] = []
        self.submitted = asyncio.Event()

    def submit(self, _batch):
        receipt: Future[str] = Future()
        self.receipts.append(receipt)
        self.submitted.set()
        return receipt


class _Broker:
    def __init__(self) -> None:
        self.calls = []

    async def place_orders(self, account_id, orders):
        self.calls.append((account_id, orders))
        return [{"order_id": "broker-1"}]


def test_broker_send_waits_for_durable_receipt_without_blocking_submit() -> None:
    async def scenario() -> None:
        writer, broker = _Writer(), _Broker()
        dispatcher = ArteCommandDispatcher(writer, broker, capacity=1)
        dispatcher.start()
        batch, request = _command()
        ticket = dispatcher.submit(batch, "DU1", (request,))
        await asyncio.wait_for(writer.submitted.wait(), 1)
        assert not ticket.done() and broker.calls == []
        second, second_request = _command()
        waiting = dispatcher.submit(second, "DU1", (second_request,))
        third, third_request = _command()
        with pytest.raises(CommandQueueFull):
            dispatcher.submit(third, "DU1", (third_request,))
        with pytest.raises(RuntimeError, match="admission stopped"):
            dispatcher.submit(third, "DU1", (third_request,))
        writer.receipts[0].set_result(batch.batch_id)
        assert await asyncio.wait_for(ticket, 1) == [{"order_id": "broker-1"}]
        while len(writer.receipts) < 2:
            await asyncio.sleep(0)
        writer.receipts[1].set_result(second.batch_id)
        assert await asyncio.wait_for(waiting, 1) == [{"order_id": "broker-1"}]
        assert len(broker.calls) == 2
        with pytest.raises(RuntimeError, match="admission stopped"):
            await dispatcher.close()

    asyncio.run(scenario())


def test_failed_persistence_never_sends_and_poisons_lane() -> None:
    async def scenario() -> None:
        writer, broker = _Writer(), _Broker()
        dispatcher = ArteCommandDispatcher(writer, broker)
        dispatcher.start()
        batch, request = _command()
        ticket = dispatcher.submit(batch, "DU1", (request,))
        await asyncio.wait_for(writer.submitted.wait(), 1)
        writer.receipts[0].set_exception(OSError("ClickHouse unavailable"))
        with pytest.raises(OSError, match="unavailable"):
            await asyncio.wait_for(ticket, 1)
        assert broker.calls == []
        with pytest.raises(RuntimeError, match="reconciliation"):
            retry, retry_request = _command()
            dispatcher.submit(retry, "DU1", (retry_request,))
        with pytest.raises(RuntimeError, match="reconciliation"):
            await dispatcher.close()

    asyncio.run(scenario())


def test_cancelled_dispatcher_fails_pending_commands_without_broker_send() -> None:
    async def scenario() -> None:
        writer, broker = _Writer(), _Broker()
        dispatcher = ArteCommandDispatcher(writer, broker)
        dispatcher.start()
        first, first_request = _command()
        ticket = dispatcher.submit(first, "DU1", (first_request,))
        await asyncio.wait_for(writer.submitted.wait(), 1)
        second, second_request = _command()
        queued = dispatcher.submit(second, "DU1", (second_request,))
        assert dispatcher._task is not None
        dispatcher._task.cancel()
        for pending in (ticket, queued):
            with pytest.raises(RuntimeError, match="interrupted"):
                await asyncio.wait_for(pending, 1)
        assert broker.calls == []
        with pytest.raises(RuntimeError, match="reconciliation"):
            await dispatcher.close()

    asyncio.run(scenario())


def test_ambiguous_broker_error_never_retries_the_durable_command() -> None:
    class UncertainBroker(_Broker):
        async def place_orders(self, account_id, orders):
            self.calls.append((account_id, orders))
            raise TimeoutError("broker outcome unknown")

    async def scenario() -> None:
        writer, broker = _Writer(), UncertainBroker()
        dispatcher = ArteCommandDispatcher(writer, broker)
        dispatcher.start()
        batch, request = _command()
        ticket = dispatcher.submit(batch, "DU1", (request,))
        await asyncio.wait_for(writer.submitted.wait(), 1)
        writer.receipts[0].set_result(batch.batch_id)
        with pytest.raises(TimeoutError, match="outcome unknown"):
            await asyncio.wait_for(ticket, 1)
        assert len(broker.calls) == 1
        with pytest.raises(RuntimeError, match="reconciliation"):
            await dispatcher.close()

    asyncio.run(scenario())
