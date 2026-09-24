"""Inactive bounded control-plane admission for typed live assignments.

The serial market consumer may submit and poll without network I/O. Only a
durably acknowledged assignment may be installed into the executable strategy.
"""
from __future__ import annotations

import asyncio
import queue
import threading
from concurrent.futures import Future
from typing import Any, Protocol


class TypedAssignmentPublisher(Protocol):
    async def publish(self, assignment: Any, *, configuration_revision_id: str) -> None:
        """Return only after durable normalized publication and fencing."""


class AssignmentAdmissionLane:
    def __init__(self, publisher: TypedAssignmentPublisher, *, capacity: int = 256) -> None:
        if capacity < 1:
            raise ValueError("assignment admission capacity must be positive")
        self._publisher = publisher
        self._queue: queue.Queue[tuple[str, str, Any, Future[None]]] = queue.Queue()
        self._capacity = capacity
        self._pending: dict[str, tuple[str, Any, Future[None]]] = {}
        self._lock = threading.Lock()
        self._closing = False
        self._fatal: BaseException | None = None
        # A queued live command must not disappear merely because the process
        # is exiting; shutdown owns this non-daemon worker and its receipt.
        self._thread = threading.Thread(target=self._work, name="typed-assignment-admission", daemon=False)
        self._started = False

    def submit(self, assignment: Any, *, configuration_revision_id: str) -> None:
        """Bounded, local-only enqueue; never grant execution here."""
        key = str(getattr(assignment, "assignment_id", "") or "")
        if not key or not configuration_revision_id:
            raise ValueError("assignment admission identity is incomplete")
        with self._lock:
            if self._closing or self._fatal is not None:
                raise RuntimeError("assignment admission lane is unavailable")
            if key in self._pending:
                raise ValueError("assignment admission is already pending")
            if len(self._pending) >= self._capacity:
                raise RuntimeError("assignment admission capacity is exhausted")
            receipt: Future[None] = Future()
            self._pending[key] = (configuration_revision_id, assignment, receipt)
            self._queue.put_nowait((key, configuration_revision_id, assignment, receipt))
            if not self._started:
                self._thread.start()
                self._started = True

    def pending_revision(self, assignment_id: str) -> str | None:
        with self._lock:
            item = self._pending.get(assignment_id)
            return item[0] if item is not None else None

    def drain_acknowledged(self, *, configuration_revision_id: str,
                           allowed_assignment_ids: set[str] | None = None) -> list[Any]:
        """Return durable ACKs only; reject stale configuration or any error."""
        with self._lock:
            if self._fatal is not None:
                raise RuntimeError("typed assignment publication failed") from self._fatal
            ready = [(key, *item) for key, item in self._pending.items() if item[2].done()]
            for key, revision, _, receipt in ready:
                error = receipt.exception()
                if error is not None:
                    self._fatal = error
                    raise RuntimeError("typed assignment publication failed") from error
                if revision != configuration_revision_id:
                    self._fatal = ValueError("stale configuration revision acknowledgement")
                    raise RuntimeError("stale assignment publication acknowledgement") from self._fatal
                if allowed_assignment_ids is not None and key not in allowed_assignment_ids:
                    self._fatal = ValueError("acknowledged assignment absent from configuration")
                    raise RuntimeError("orphan assignment publication acknowledgement") from self._fatal
            for key, _, _, _ in ready:
                del self._pending[key]
            return [assignment for _, _, assignment, _ in ready]

    def close(self, *, timeout: float = 10.0) -> None:
        with self._lock:
            self._closing = True
        if self._started:
            self._thread.join(timeout=timeout)
        if self._started and self._thread.is_alive():
            raise RuntimeError("assignment admission worker did not drain")

    def _work(self) -> None:
        while True:
            try:
                key, revision, assignment, receipt = self._queue.get(timeout=0.1)
            except queue.Empty:
                with self._lock:
                    if self._closing:
                        return
                continue
            try:
                asyncio.run(self._publisher.publish(
                    assignment, configuration_revision_id=revision))
            except BaseException as exc:
                receipt.set_exception(exc)
            else:
                receipt.set_result(None)
            finally:
                self._queue.task_done()
