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
from typing import Awaitable, Callable, Generic, Protocol, TypeVar


Command = TypeVar("Command")
Result = TypeVar("Result")


class _KeeperLease(Protocol):
    def assert_current(self) -> None: ...


class LiveOrderDurabilityBridge(Generic[Command, Result]):
    """One ordered broker lane; never wait for persistence on the caller path."""

    def __init__(self, submit_broker: Callable[[Command], Awaitable[Result]], *,
                 keeper_lease: _KeeperLease, capacity: int = 256) -> None:
        if not callable(submit_broker) or type(capacity) is not int or capacity < 1:
            raise ValueError("Live order bridge needs a broker and positive bound")
        if not callable(getattr(keeper_lease, "assert_current", None)):
            raise ValueError("Live order bridge Keeper lease is invalid")
        self._submit_broker = submit_broker
        self._keeper_lease = keeper_lease
        self._queue: asyncio.Queue[
            tuple[Command, str, ThreadFuture[str], asyncio.Future[Result]] | None
        ] = asyncio.Queue(maxsize=capacity)
        self._worker = asyncio.create_task(self._run(), name="live-order-durability")
        self._failure: BaseException | None = None
        self._closed = False

    def offer(self, command: Command, receipt: ThreadFuture[str], *,
              expected_commit_id: str) -> asyncio.Future[Result]:
        """Accept immediately or fail closed; no network or durability wait."""
        if not isinstance(receipt, ThreadFuture):
            raise TypeError("Live order needs its typed journal receipt")
        if not isinstance(expected_commit_id, str) or not expected_commit_id:
            raise ValueError("Live order needs its exact normalized commit identity")
        if self._closed or self._failure is not None or self._worker.done():
            raise RuntimeError("Live order bridge is closed or failed") from self._failure
        result: asyncio.Future[Result] = asyncio.get_running_loop().create_future()
        try:
            self._queue.put_nowait((command, expected_commit_id, receipt, result))
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
                command, expected_commit_id, receipt, result = item
                if self._failure is not None:
                    if not result.done():
                        result.set_exception(self._failure)
                    continue
                try:
                    await asyncio.to_thread(self._keeper_lease.assert_current)
                    commit_id = await asyncio.wrap_future(receipt)
                    if commit_id != expected_commit_id:
                        raise RuntimeError(
                            "Live order receipt differs from its normalized commit")
                    if self._failure is not None:
                        raise self._failure
                    # A prior owner must not dispatch a committed command
                    # after a Keeper takeover while its receipt was pending.
                    await asyncio.to_thread(self._keeper_lease.assert_current)
                    broker_result = await self._submit_broker(command)
                except asyncio.CancelledError:
                    if self._failure is None:
                        self._failure = RuntimeError(
                            "Live order handoff was interrupted; broker reconciliation is required")
                    if not result.done():
                        result.set_exception(self._failure)
                    # A cancelled persistence receipt raises CancelledError
                    # without cancelling this worker task. Keep draining so
                    # every queued command receives the same fail-closed result.
                    # An actual worker cancellation (for example, timed close)
                    # must still propagate to the control-plane caller.
                    if asyncio.current_task().cancelling():
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
                # A timeout cancels drain(), not the worker; record the
                # reconciliation reason before explicitly interrupting it.
                await asyncio.shield(self._worker)
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
                    if item is not None and not item[3].done():
                        item[3].set_exception(self._failure)
                    self._queue.task_done()
                raise self._failure from exc
        else:
            await self._worker
        if self._failure is not None:
            raise RuntimeError("Live order bridge stopped; reconcile durable commands") from self._failure
