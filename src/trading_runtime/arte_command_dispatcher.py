"""Bounded nonblocking command lane gated by a typed ClickHouse receipt.

This transport is not the live cutover. Cold broker reconciliation and complete
typed strategy evidence are required before production may construct it.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import Future as ThreadFuture
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from uuid import UUID

from src.trading_runtime.arte_command_recovery import audit_committed_commands
from src.trading_runtime.arte_journal_writer import TypedJournalBatch
from src.trading_runtime.ibkr_schema import OrderRequest
from src.trading_runtime.strategy_one_contract import STRATEGY_ID, STRATEGY_NUMBER


class _JournalWriter(Protocol):
    @property
    def coalesce_batches(self) -> bool: ...

    def submit(self, batch: TypedJournalBatch) -> ThreadFuture[str]: ...
    def submit_base_v4(self, batch: TypedJournalBatch) -> ThreadFuture[str]: ...


class _OrderBroker(Protocol):
    async def place_orders(
        self, account_id: str, orders: list[OrderRequest],
    ) -> list[dict[str, Any]]: ...


class CommandQueueFull(RuntimeError):
    """New order admission must stop; no command was silently dropped."""


@dataclass(slots=True)
class _PendingCommand:
    batch: TypedJournalBatch
    account_id: str
    orders: tuple[OrderRequest, ...]
    result: asyncio.Future[list[dict[str, Any]]]


def _command_matches_order(row: Any, order: OrderRequest) -> bool:
    """Match every broker-effective field to its committed typed command."""
    def amount(value: Any) -> Decimal | None:
        if value is None:
            return None
        try:
            result = Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
        return result if result.is_finite() else None

    fields = (
        ("account_id", order.acctId), ("client_order_id", order.cOID),
        ("conid", order.conid), ("ticker", order.ticker),
        ("side", order.side), ("order_type", order.orderType),
        ("time_in_force", order.tif), ("security_type", order.secType),
        ("listing_exchange", order.listingExchange),
        ("parent_broker_order_id", order.parentId or ""),
        ("trailing_type", order.trailingType or ""),
        ("external_operator", order.extOperator or ""),
        ("referrer", order.referrer or ""),
        ("broker_strategy", order.strategy or ""),
    )
    flags = (
        ("outside_rth", order.outsideRTH),
        ("single_group", order.isSingleGroup),
        ("manual_indicator", order.manualIndicator),
    )
    prices = (
        ("quantity", order.quantity), ("cash_quantity", order.cashQty),
        ("limit_price", order.price), ("aux_price", order.auxPrice),
        ("trailing_amount", order.trailingAmt),
    )
    return (not order.strategyParameters
            and all(key.startswith("canonical_") for key in order.raw)
            and all(str(row[key]) == str(value) for key, value in fields)
            and all(int(row[key]) == int(value) for key, value in flags)
            and all((value is None or amount(value) is not None)
                    and amount(row[key]) == amount(value)
                    and (row[key] is None) == (value is None)
                    for key, value in prices))


class ArteCommandDispatcher:
    """One ordered broker lane; never await this from a market-data callback."""

    def __init__(self, writer: _JournalWriter, broker: _OrderBroker,
                 *, capacity: int = 64) -> None:
        if capacity < 1:
            raise ValueError("Command queue capacity must be positive")
        if getattr(writer, "coalesce_batches", None) is not False:
            raise ValueError("Command lane requires exact, non-coalesced journal receipts")
        profile = getattr(writer, "journal_profile", None)
        if profile == "backtest_v4":
            raise ValueError("Live command lane cannot use a Backtest V4 writer")
        self._live_v4 = profile == "live_v4"
        if self._live_v4 and not callable(getattr(writer, "submit_base_v4", None)):
            raise ValueError("Live V4 command lane requires its explicit family writer")
        self._writer = writer
        self._broker = broker
        self._queue: asyncio.Queue[_PendingCommand | None] = asyncio.Queue(maxsize=capacity)
        self._task: asyncio.Task[None] | None = None
        self._error: BaseException | None = None
        self._admission_error: CommandQueueFull | None = None
        self._closed = False
        self._audited_run_id: str | None = None
        self._starting = False

    async def start(self, client: Any, run_id: str) -> None:
        """Audit committed state on the control plane before accepting orders."""
        if self._task is not None or self._closed or self._starting:
            raise RuntimeError("Command dispatcher cannot start twice")
        self._starting = True
        try:
            audit = await audit_committed_commands(client, self._broker, run_id)
            if self._closed:
                raise RuntimeError("Command dispatcher closed during recovery audit")
            if audit.run_id != run_id or not audit.admission_safe:
                raise RuntimeError("Command dispatcher requires complete OMS recovery")
            self._audited_run_id = run_id
            self._task = asyncio.create_task(self._run(), name="arte-command-dispatcher")
        finally:
            self._starting = False

    def submit(
        self, batch: TypedJournalBatch, account_id: str,
        orders: tuple[OrderRequest, ...],
    ) -> asyncio.Future[list[dict[str, Any]]]:
        """Enqueue without network or durability wait; queue saturation rejects."""
        if self._task is None or self._closed:
            raise RuntimeError("Command dispatcher is not accepting orders")
        if batch.run_id != self._audited_run_id:
            raise ValueError("Command run differs from its audited journal prefix")
        if self._error is not None:
            raise RuntimeError("Command dispatcher requires broker reconciliation") from self._error
        if self._admission_error is not None:
            raise RuntimeError("Command admission stopped after queue saturation") from self._admission_error
        if (batch.status != "running" or not orders
                or len(batch.order_commands) != len(orders)
                or any(order.acctId != account_id for order in orders)
                or any(not _command_matches_order(row, order)
                       for row, order in zip(batch.order_commands, orders))):
            raise ValueError("Typed command batch differs from broker requests")
        if self._live_v4:
            commands = {str(row.get("record_id")): row
                        for row in batch.order_commands}
            lineage = {str(row.get("parent_record_id")): row
                       for row in batch.v4_command_lineages}
            if (len(commands) != len(orders) or len(lineage) != len(orders)
                    or set(commands) != set(lineage)
                    or any((row.get("strategy_id"), row.get("strategy_revision"))
                           != (STRATEGY_ID, STRATEGY_NUMBER)
                           for row in commands.values())
                    or any(row.get("run_id") != batch.run_id
                           or row.get("batch_id") != batch.batch_id
                           or row.get("account_id") != commands[parent].get("account_id")
                           for parent, row in lineage.items())):
                raise ValueError("Live V4 command lacks Strategy 1 typed lineage")
        # TypedJournalBatch already freezes row mappings at construction.
        # OrderRequest.raw remains mutable, so own the broker request snapshot.
        sealed_orders = deepcopy(orders)
        result: asyncio.Future[list[dict[str, Any]]] = asyncio.get_running_loop().create_future()
        try:
            self._queue.put_nowait(_PendingCommand(
                batch, account_id, sealed_orders, result))
        except asyncio.QueueFull as exc:
            self._admission_error = CommandQueueFull(
                "Command queue is full; stop new order admission"
            )
            raise self._admission_error from exc
        return result

    async def _run(self) -> None:
        while True:
            pending = await self._queue.get()
            try:
                if pending is None:
                    return
                if self._error is not None:
                    raise RuntimeError("An earlier command requires broker reconciliation") from self._error
                receipt = (self._writer.submit_base_v4(pending.batch)
                           if self._live_v4 else self._writer.submit(pending.batch))
                committed_id = await asyncio.wrap_future(receipt)
                if UUID(str(committed_id)) != UUID(pending.batch.batch_id):
                    raise RuntimeError("Committed command receipt differs from submitted batch")
                response = await self._broker.place_orders(
                    pending.account_id, list(pending.orders),
                )
                pending.result.set_result(response)
            except asyncio.CancelledError as exc:
                self._error = RuntimeError(
                    "Command dispatcher interrupted; broker reconciliation required"
                )
                if pending is not None and not pending.result.done():
                    pending.result.set_exception(self._error)
                while True:
                    try:
                        queued = self._queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if queued is not None and not queued.result.done():
                        queued.result.set_exception(self._error)
                    self._queue.task_done()
                raise exc
            except Exception as exc:
                self._error = exc
                if pending is not None and not pending.result.done():
                    pending.result.set_exception(exc)
            finally:
                self._queue.task_done()

    async def close(self) -> None:
        """Control-plane drain only; a failed send remains unresolved."""
        if not self._closed:
            self._closed = True
            if self._task is not None:
                if not self._task.done():
                    await self._queue.put(None)
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
        if self._error is not None:
            raise RuntimeError("Command dispatcher requires broker reconciliation") from self._error
        if self._admission_error is not None:
            raise RuntimeError("Command admission stopped after queue saturation") from self._admission_error
