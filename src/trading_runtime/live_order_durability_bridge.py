"""Ordered, non-blocking handoff from typed journal receipts to live broker calls.

The market callback only enqueues an already-registered command and receipt.
The worker waits for ClickHouse durability before touching the broker. After
an uncertain broker result it stops; cold recovery must reconcile the durable
command before any further order admission. This component grants no live
admission by itself and persists no state outside the typed journal.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import Future as ThreadFuture
from typing import Awaitable, Callable, Generic, TypeVar


Command = TypeVar("Command")
Result = TypeVar("Result")


class LiveOrderDurabilityBridge(Generic[Command, Result]):
    """One ordered broker lane; never wait for persistence on the caller path."""

    def __init__(self, submit_broker: Callable[[Command], Awaitable[Result]], *,
                 capacity: int = 256) -> None:
        if not callable(submit_broker) or type(capacity) is not int or capacity < 1:
            raise ValueError("Live order bridge needs a broker and positive bound")
        self._submit_broker = submit_broker
        self._queue: asyncio.Queue[
            tuple[Command, ThreadFuture[str], asyncio.Future[Result]] | None
        ] = asyncio.Queue(maxsize=capacity)
        self._worker = asyncio.create_task(self._run(), name="live-order-durability")
        self._failure: BaseException | None = None
        self._closed = False

    def offer(self, command: Command, receipt: ThreadFuture[str]) -> asyncio.Future[Result]:
        """Accept immediately or fail closed; no network or durability wait."""
        if not isinstance(receipt, ThreadFuture):
            raise TypeError("Live order needs its typed journal receipt")
        if self._closed or self._failure is not None or self._worker.done():
            raise RuntimeError("Live order bridge is closed or failed") from self._failure
        result: asyncio.Future[Result] = asyncio.get_running_loop().create_future()
        try:
            self._queue.put_nowait((command, receipt, result))
        except asyncio.QueueFull as exc:
            self._failure = RuntimeError("Live order queue is full; stop new admission")
            raise self._failure from exc
        return result

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                if item is None:
                    return
                command, receipt, result = item
                if self._failure is not None:
                    if not result.done():
                        result.set_exception(self._failure)
                    continue
                try:
                    commit_id = await asyncio.wrap_future(receipt)
                    if not isinstance(commit_id, str) or not commit_id:
                        raise RuntimeError("Live order lacks a durable journal commit")
                    if self._failure is not None:
                        raise self._failure
                    broker_result = await self._submit_broker(command)
                except asyncio.CancelledError:
                    if self._failure is None:
                        self._failure = RuntimeError(
                            "Live order handoff was interrupted; broker reconciliation is required")
                    if not result.done():
                        result.set_exception(self._failure)
                    raise
                except Exception as exc:
                    self._failure = exc
                    if not result.done():
                        result.set_exception(exc)
                else:
                    if not result.done():
                        result.set_result(broker_result)
            finally:
                self._queue.task_done()

    async def close(self, *, timeout_seconds: float = 30.0) -> None:
        """Control-plane drain; never call from the realtime market callback."""
        if not 0 < timeout_seconds <= 300:
            raise ValueError("Live order drain timeout is invalid")
        if not self._closed:
            self._closed = True
            async def drain() -> None:
                await self._queue.put(None)
                await self._worker
            try:
                await asyncio.wait_for(drain(), timeout=timeout_seconds)
            except TimeoutError as exc:
                self._failure = RuntimeError(
                    "Live order drain timed out; reconcile durable commands")
                self._worker.cancel()
                try:
                    await self._worker
                except asyncio.CancelledError:
                    pass
                while not self._queue.empty():
                    item = self._queue.get_nowait()
                    if item is not None and not item[2].done():
                        item[2].set_exception(self._failure)
                    self._queue.task_done()
                raise self._failure from exc
        else:
            await self._worker
        if self._failure is not None:
            raise RuntimeError("Live order bridge stopped; reconcile durable commands") from self._failure
