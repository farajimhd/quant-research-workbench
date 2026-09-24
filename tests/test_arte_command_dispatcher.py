from __future__ import annotations

import asyncio
from concurrent.futures import Future
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

from src.trading_runtime import arte_command_dispatcher as dispatcher_module
from src.trading_runtime.arte_command_dispatcher import (
    ArteCommandDispatcher, CommandQueueFull,
)
from src.trading_runtime.arte_command_recovery import CommandRecoveryAudit
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


def _fresh_audit(status="running", commands=0):
    return CommandRecoveryAudit(
        "live:DU1", status, commands, 0, 0, 0, 0, (),
    )


def _install_audit(monkeypatch, audit=None):
    async def audited(_client, _broker, _run_id):
        return audit or _fresh_audit()

    monkeypatch.setattr(dispatcher_module, "audit_committed_commands", audited)


def test_broker_send_waits_for_durable_receipt_without_blocking_submit(monkeypatch) -> None:
    _install_audit(monkeypatch)
    async def scenario() -> None:
        writer, broker = _Writer(), _Broker()
        dispatcher = ArteCommandDispatcher(writer, broker, capacity=1)
        await dispatcher.start(None, "live:DU1")
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


def test_failed_persistence_never_sends_and_poisons_lane(monkeypatch) -> None:
    _install_audit(monkeypatch)
    async def scenario() -> None:
        writer, broker = _Writer(), _Broker()
        dispatcher = ArteCommandDispatcher(writer, broker)
        await dispatcher.start(None, "live:DU1")
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


def test_cancelled_dispatcher_fails_pending_commands_without_broker_send(monkeypatch) -> None:
    _install_audit(monkeypatch)
    async def scenario() -> None:
        writer, broker = _Writer(), _Broker()
        dispatcher = ArteCommandDispatcher(writer, broker)
        await dispatcher.start(None, "live:DU1")
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


def test_ambiguous_broker_error_never_retries_the_durable_command(monkeypatch) -> None:
    _install_audit(monkeypatch)
    class UncertainBroker(_Broker):
        async def place_orders(self, account_id, orders):
            self.calls.append((account_id, orders))
            raise TimeoutError("broker outcome unknown")

    async def scenario() -> None:
        writer, broker = _Writer(), UncertainBroker()
        dispatcher = ArteCommandDispatcher(writer, broker)
        await dispatcher.start(None, "live:DU1")
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


def test_dispatcher_rejects_terminal_or_unrecovered_journal(monkeypatch) -> None:
    async def scenario() -> None:
        for audit in (_fresh_audit(status="completed"), _fresh_audit(commands=1)):
            _install_audit(monkeypatch, audit)
            dispatcher = ArteCommandDispatcher(_Writer(), _Broker())
            with pytest.raises(RuntimeError, match="OMS recovery"):
                await dispatcher.start(None, "live:DU1")
            assert dispatcher._task is None

    asyncio.run(scenario())


def test_dispatcher_rejects_command_from_different_audited_run(monkeypatch) -> None:
    _install_audit(monkeypatch)
    async def scenario() -> None:
        dispatcher = ArteCommandDispatcher(_Writer(), _Broker())
        await dispatcher.start(None, "live:DU1")
        batch, request = _command()
        from dataclasses import replace
        with pytest.raises(ValueError, match="audited journal prefix"):
            dispatcher.submit(replace(batch, run_id="other"), "DU1", (request,))
        await dispatcher.close()

    asyncio.run(scenario())


def test_close_during_recovery_audit_never_starts_order_lane(monkeypatch) -> None:
    async def scenario() -> None:
        entered, release = asyncio.Event(), asyncio.Event()

        async def audited(_client, _broker, _run_id):
            entered.set()
            await release.wait()
            return _fresh_audit()

        monkeypatch.setattr(dispatcher_module, "audit_committed_commands", audited)
        dispatcher = ArteCommandDispatcher(_Writer(), _Broker())
        starting = asyncio.create_task(dispatcher.start(None, "live:DU1"))
        await entered.wait()
        await dispatcher.close()
        release.set()
        with pytest.raises(RuntimeError, match="closed during recovery"):
            await starting
        assert dispatcher._task is None

    asyncio.run(scenario())
