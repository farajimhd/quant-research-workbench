from __future__ import annotations

import asyncio
from concurrent.futures import Future
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.trading_runtime import arte_command_dispatcher as dispatcher_module
from src.trading_runtime.arte_command_dispatcher import (
    ArteCommandDispatcher, CommandQueueFull,
)
from src.trading_runtime.arte_command_recovery import CommandRecoveryAudit
from src.trading_runtime.arte_journal_projection import order_command_batch
from src.trading_runtime.arte_order_cancel_v4 import order_cancel_batch_v4
from src.trading_runtime.ibkr_schema import OrderRequest
from src.trading_runtime.journal_contract import JournalRecord


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


def _strategy_one_command():
    batch, request = _command()
    command = {**batch.order_commands[0],
               "strategy_id": "early-squeeze-strategy", "strategy_revision": 1}
    lineage = {"parent_record_id": command["record_id"],
               "run_id": batch.run_id, "batch_id": batch.batch_id,
               "account_id": command["account_id"]}
    context = {**lineage, "order_group_id": "group-1",
               "strategy_intent_id": "intent-1", "policy_version": "policy-1"}
    intent_use = {**lineage, "intent_record_id": str(uuid4()),
                  "intent_content_hash": "a" * 64}
    return replace(batch, order_commands=(command,),
                   v4_command_lineages=(lineage,), order_contexts=(context,),
                   intent_uses=(intent_use,)), request


class _Writer:
    coalesce_batches = False

    def __init__(self) -> None:
        self.receipts: list[Future[str]] = []
        self.batches = []
        self.submitted = asyncio.Event()

    def submit(self, batch):
        receipt: Future[str] = Future()
        self.receipts.append(receipt)
        self.batches.append(batch)
        self.submitted.set()
        return receipt


class _LiveV4Writer(_Writer):
    journal_profile = "live_v4"

    class Lease:
        run_id = "live:DU1"
        current = True
        def assert_current(self):
            if not self.current:
                raise RuntimeError("Live V4 Keeper lease lost")

    def __init__(self):
        super().__init__()
        self.live_v4_lease = self.Lease()

    def submit(self, batch):
        raise AssertionError("Live V4 must not use legacy journal publication")

    def submit_base_v4(self, batch):
        return super().submit(batch)

    def submit_order_cancel_v4(self, unit):
        return super().submit(unit.base)


class _Broker:
    def __init__(self) -> None:
        self.calls = []

    async def place_orders(self, account_id, orders):
        self.calls.append((account_id, orders))
        return [{"order_id": "broker-1"}]

    async def cancel_order(self, account_id, order_id):
        self.calls.append(("cancel", account_id, order_id))
        return {"msg": "Request was submitted", "order_id": order_id}


def _strategy_one_cancel():
    now = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)
    record = JournalRecord(
        str(uuid4()), "live:DU1", 1, now, now, "command", "order_cancel",
        "broker-1", "DU1", {
            "strategy_id": "early-squeeze-strategy", "strategy_revision": 1,
            "correlation_id": "correlation", "causation_id": "intent-2",
            "reason": "replace_strategy_protection", "ticker": "TEST",
            "order_group_id": "group-1", "intent_id": "intent-2",
        })
    return order_cancel_batch_v4(
        record, run_month=date(2026, 8, 1), attempt_id=str(uuid4()),
        batch_id=str(uuid4()),
        prior_batch_id="00000000-0000-0000-0000-000000000000",
        source_cursor="cancel-1")


async def _seed_live_order(dispatcher, writer):
    batch, request = _strategy_one_command()
    ticket = dispatcher.submit(batch, "DU1", (request,))
    await asyncio.wait_for(writer.submitted.wait(), 1)
    writer.receipts[-1].set_result(batch.batch_id)
    await ticket
    writer.submitted.clear()


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


def test_live_v4_rejects_command_without_strategy_one_lineage(monkeypatch) -> None:
    _install_audit(monkeypatch)
    async def scenario() -> None:
        dispatcher = ArteCommandDispatcher(_LiveV4Writer(), _Broker())
        await dispatcher.start(None, "live:DU1")
        batch, request = _command()
        with pytest.raises(ValueError, match="Strategy 1 typed lineage"):
            dispatcher.submit(batch, "DU1", (request,))
        await dispatcher.close()
    asyncio.run(scenario())


def test_live_v4_command_uses_explicit_family_receipt(monkeypatch) -> None:
    _install_audit(monkeypatch)
    async def scenario() -> None:
        writer, broker = _LiveV4Writer(), _Broker()
        dispatcher = ArteCommandDispatcher(writer, broker)
        await dispatcher.start(None, "live:DU1")
        batch, request = _strategy_one_command()
        ticket = dispatcher.submit(batch, "DU1", (request,))
        await asyncio.wait_for(writer.submitted.wait(), 1)
        assert broker.calls == []
        writer.receipts[0].set_result(batch.batch_id)
        await ticket
        assert len(broker.calls) == 1
        await dispatcher.close()
    asyncio.run(scenario())


def test_live_v4_lost_owner_after_receipt_never_sends_order(monkeypatch) -> None:
    _install_audit(monkeypatch)
    async def scenario() -> None:
        writer, broker = _LiveV4Writer(), _Broker()
        dispatcher = ArteCommandDispatcher(writer, broker)
        await dispatcher.start(None, "live:DU1")
        batch, request = _strategy_one_command()
        ticket = dispatcher.submit(batch, "DU1", (request,))
        await asyncio.wait_for(writer.submitted.wait(), 1)
        writer.live_v4_lease.current = False
        writer.receipts[0].set_result(batch.batch_id)
        with pytest.raises(RuntimeError, match="lease lost"):
            await ticket
        assert broker.calls == []
        with pytest.raises(RuntimeError, match="broker reconciliation"):
            await dispatcher.close()
    asyncio.run(scenario())


def test_live_v4_lost_owner_while_queued_never_publishes_command(monkeypatch) -> None:
    _install_audit(monkeypatch)
    async def scenario() -> None:
        writer, broker = _LiveV4Writer(), _Broker()
        dispatcher = ArteCommandDispatcher(writer, broker)
        await dispatcher.start(None, "live:DU1")
        first, first_request = _strategy_one_command()
        first_ticket = dispatcher.submit(first, "DU1", (first_request,))
        await asyncio.wait_for(writer.submitted.wait(), 1)
        second, second_request = _strategy_one_command()
        second_ticket = dispatcher.submit(second, "DU1", (second_request,))
        writer.live_v4_lease.current = False
        writer.receipts[0].set_result(first.batch_id)
        with pytest.raises(RuntimeError, match="lease lost"):
            await first_ticket
        with pytest.raises(RuntimeError, match="broker reconciliation"):
            await second_ticket
        assert len(writer.batches) == 1
        assert broker.calls == []
        with pytest.raises(RuntimeError, match="broker reconciliation"):
            await dispatcher.close()
    asyncio.run(scenario())


def test_live_v4_cancel_waits_for_its_typed_receipt(monkeypatch) -> None:
    _install_audit(monkeypatch)
    async def scenario() -> None:
        writer, broker = _LiveV4Writer(), _Broker()
        dispatcher = ArteCommandDispatcher(writer, broker)
        await dispatcher.start(None, "live:DU1")
        await _seed_live_order(dispatcher, writer)
        unit = _strategy_one_cancel()
        ticket = dispatcher.submit_cancel(unit, "DU1", "broker-1")
        await asyncio.wait_for(writer.submitted.wait(), 1)
        assert len(broker.calls) == 1
        writer.receipts[1].set_result(unit.base.batch_id)
        assert await ticket == {"msg": "Request was submitted", "order_id": "broker-1"}
        assert broker.calls[-1] == ("cancel", "DU1", "broker-1")
        await dispatcher.close()
    asyncio.run(scenario())


def test_live_v4_cancel_rejects_missing_intent_lineage(monkeypatch) -> None:
    _install_audit(monkeypatch)
    async def scenario() -> None:
        writer, broker = _LiveV4Writer(), _Broker()
        dispatcher = ArteCommandDispatcher(writer, broker)
        await dispatcher.start(None, "live:DU1")
        await _seed_live_order(dispatcher, writer)
        unit = _strategy_one_cancel()
        missing = replace(unit, cancellation={**unit.cancellation, "intent_id": ""})
        with pytest.raises(ValueError, match="Strategy 1 lineage"):
            dispatcher.submit_cancel(missing, "DU1", "broker-1")
        assert len(writer.batches) == 1 and len(broker.calls) == 1
        await dispatcher.close()
    asyncio.run(scenario())


def test_live_v4_cancel_rejects_a_foreign_broker_order_group(monkeypatch) -> None:
    _install_audit(monkeypatch)
    async def scenario() -> None:
        writer, broker = _LiveV4Writer(), _Broker()
        dispatcher = ArteCommandDispatcher(writer, broker)
        await dispatcher.start(None, "live:DU1")
        await _seed_live_order(dispatcher, writer)
        unit = _strategy_one_cancel()
        foreign = replace(unit, cancellation={
            **unit.cancellation, "order_group_id": "foreign-group"})
        with pytest.raises(ValueError, match="Strategy 1 lineage"):
            dispatcher.submit_cancel(foreign, "DU1", "broker-1")
        assert len(writer.batches) == 1 and len(broker.calls) == 1
        await dispatcher.close()
    asyncio.run(scenario())


def test_live_v4_cancel_lost_keeper_owner_never_reaches_broker(monkeypatch) -> None:
    _install_audit(monkeypatch)
    async def scenario() -> None:
        writer, broker = _LiveV4Writer(), _Broker()
        dispatcher = ArteCommandDispatcher(writer, broker)
        await dispatcher.start(None, "live:DU1")
        await _seed_live_order(dispatcher, writer)
        unit = _strategy_one_cancel()
        ticket = dispatcher.submit_cancel(unit, "DU1", "broker-1")
        await asyncio.wait_for(writer.submitted.wait(), 1)
        writer.live_v4_lease.current = False
        writer.receipts[1].set_result(unit.base.batch_id)
        with pytest.raises(RuntimeError, match="lease lost"):
            await ticket
        assert len(broker.calls) == 1
        with pytest.raises(RuntimeError, match="broker reconciliation"):
            await dispatcher.close()
    asyncio.run(scenario())


def test_live_command_rejects_backtest_v4_writer() -> None:
    class BacktestWriter(_Writer):
        journal_profile = "backtest_v4"
    with pytest.raises(ValueError, match="Backtest V4"):
        ArteCommandDispatcher(BacktestWriter(), _Broker())


def test_live_v4_command_rejects_foreign_keeper_run(monkeypatch) -> None:
    _install_audit(monkeypatch)
    async def scenario() -> None:
        writer = _LiveV4Writer()
        writer.live_v4_lease.run_id = "live:other"
        dispatcher = ArteCommandDispatcher(writer, _Broker())
        with pytest.raises(ValueError, match="lease differs"):
            await dispatcher.start(None, "live:DU1")
        await dispatcher.close()
    asyncio.run(scenario())


def test_wrong_valid_receipt_never_sends_order(monkeypatch) -> None:
    _install_audit(monkeypatch)

    async def scenario() -> None:
        writer, broker = _Writer(), _Broker()
        dispatcher = ArteCommandDispatcher(writer, broker)
        await dispatcher.start(None, "live:DU1")
        batch, request = _command()
        ticket = dispatcher.submit(batch, "DU1", (request,))
        await asyncio.wait_for(writer.submitted.wait(), 1)
        writer.receipts[0].set_result(str(uuid4()))
        with pytest.raises(RuntimeError, match="receipt differs"):
            await asyncio.wait_for(ticket, 1)
        assert broker.calls == []
        with pytest.raises(RuntimeError, match="broker reconciliation"):
            await dispatcher.close()

    asyncio.run(scenario())


def test_command_lane_rejects_coalescing_writer() -> None:
    writer = _Writer()
    writer.coalesce_batches = True
    with pytest.raises(ValueError, match="non-coalesced"):
        ArteCommandDispatcher(writer, _Broker())


@pytest.mark.parametrize("change", [
    {"price": 12.35}, {"quantity": 6}, {"side": "SELL"},
    {"orderType": "MKT", "price": None}, {"outsideRTH": True},
    {"parentId": "unexpected-parent"}, {"price": float("nan")},
    {"raw": {"price": 1.0}},
])
def test_broker_request_must_match_every_durable_command_field(monkeypatch, change) -> None:
    _install_audit(monkeypatch)

    async def scenario() -> None:
        writer, broker = _Writer(), _Broker()
        dispatcher = ArteCommandDispatcher(writer, broker)
        await dispatcher.start(None, "live:DU1")
        batch, request = _command()
        with pytest.raises(ValueError, match="differs from broker requests"):
            dispatcher.submit(batch, "DU1", (replace(request, **change),))
        assert not writer.receipts and not broker.calls
        await dispatcher.close()

    asyncio.run(scenario())


def test_command_admission_owns_snapshot_of_mutable_broker_raw(monkeypatch) -> None:
    _install_audit(monkeypatch)

    async def scenario() -> None:
        writer, broker = _Writer(), _Broker()
        dispatcher = ArteCommandDispatcher(writer, broker)
        await dispatcher.start(None, "live:DU1")
        batch, request = _command()
        ticket = dispatcher.submit(batch, "DU1", (request,))
        with pytest.raises(TypeError):
            batch.order_commands[0]["limit_price"] = "99.99"
        request.raw["price"] = 99.99
        await asyncio.wait_for(writer.submitted.wait(), 1)
        assert Decimal(writer.batches[0].order_commands[0]["limit_price"]) == Decimal("12.34")
        writer.receipts[0].set_result(batch.batch_id)
        await asyncio.wait_for(ticket, 1)
        assert broker.calls[0][1][0].price == 12.34
        assert broker.calls[0][1][0].raw == {}
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
        await asyncio.sleep(0)
        assert writer.receipts[0].cancelled()
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
