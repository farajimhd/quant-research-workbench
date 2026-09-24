"""Bounded nonblocking command lane gated by a typed ClickHouse receipt.

This transport is not the live cutover. Cold broker reconciliation and complete
typed strategy evidence are required before production may construct it.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import Future as ThreadFuture
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from src.trading_runtime.arte_journal_writer import TypedJournalBatch
from src.trading_runtime.ibkr_schema import OrderRequest


class _JournalWriter(Protocol):
    def submit(self, batch: TypedJournalBatch) -> ThreadFuture[str]: ...


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


class ArteCommandDispatcher:
    """One ordered broker lane; never await this from a market-data callback."""

    def __init__(self, writer: _JournalWriter, broker: _OrderBroker,
                 *, capacity: int = 64) -> None:
        if capacity < 1:
            raise ValueError("Command queue capacity must be positive")
        self._writer = writer
        self._broker = broker
        self._queue: asyncio.Queue[_PendingCommand | None] = asyncio.Queue(maxsize=capacity)
        self._task: asyncio.Task[None] | None = None
        self._error: BaseException | None = None
        self._admission_error: CommandQueueFull | None = None
        self._closed = False

    def start(self) -> None:
        if self._task is not None or self._closed:
            raise RuntimeError("Command dispatcher cannot start twice")
        self._task = asyncio.create_task(self._run(), name="arte-command-dispatcher")

    def submit(
        self, batch: TypedJournalBatch, account_id: str,
        orders: tuple[OrderRequest, ...],
    ) -> asyncio.Future[list[dict[str, Any]]]:
        """Enqueue without network or durability wait; queue saturation rejects."""
        if self._task is None or self._closed:
            raise RuntimeError("Command dispatcher is not accepting orders")
        if self._error is not None:
            raise RuntimeError("Command dispatcher requires broker reconciliation") from self._error
        if self._admission_error is not None:
            raise RuntimeError("Command admission stopped after queue saturation") from self._admission_error
        if (batch.status != "running" or not orders
                or len(batch.order_commands) != len(orders)
                or any(order.acctId != account_id for order in orders)
                or any(str(row["account_id"]) != account_id
                       or str(row["client_order_id"]) != order.cOID
                       for row, order in zip(batch.order_commands, orders))):
            raise ValueError("Typed command batch differs from broker requests")
        result: asyncio.Future[list[dict[str, Any]]] = asyncio.get_running_loop().create_future()
        try:
            self._queue.put_nowait(_PendingCommand(batch, account_id, orders, result))
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
                receipt = self._writer.submit(pending.batch)
                committed_id = await asyncio.wrap_future(receipt)
                UUID(str(committed_id))
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
