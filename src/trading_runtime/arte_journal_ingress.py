"""Bounded, disk-free ingress from runtime facts to the typed writer lane.

The market callback only snapshots and enqueues a record. Projection, writer
submission, and durability confirmation happen on this separate worker. This
is a transport primitive, not permission to enable an incomplete run mode.
"""
from __future__ import annotations

from collections import deque
from concurrent.futures import Future, InvalidStateError
from copy import deepcopy
from datetime import timezone
from queue import Empty, Full, Queue
from threading import Lock, Thread
from time import sleep
from typing import Any, Callable, Mapping
from uuid import NAMESPACE_URL, UUID, uuid5

from src.trading_runtime.arte_journal_projection import project_journal_record
from src.trading_runtime.arte_journal_writer import JournalQueueFull, TypedJournalBatch
from src.trading_runtime.journal_contract import JournalRecord


class JournalIngressFull(RuntimeError):
    """The producer must stop new admission; no accepted fact was dropped."""


def _settle(receipt: Future[str], *, result: str | None = None,
            error: BaseException | None = None) -> None:
    try:
        if error is None:
            assert result is not None
            receipt.set_result(result)
        else:
            receipt.set_exception(error)
    except InvalidStateError:
        # Consumers may cancel a receipt; cancellation cannot tear down the
        # journal lane or mask the underlying publication result.
        if not receipt.cancelled():
            raise


class TypedJournalIngress:
    """Assign retry-stable batches while keeping ClickHouse off the hot path."""

    def __init__(
        self, writer: Any, *, run_id: str, attempt_id: str,
        first_sequence: int, prior_batch_id: str,
        capacity: int = 4096,
        projection_context: Mapping[str, Any] | None = None,
        projector: Callable[..., TypedJournalBatch] = project_journal_record,
    ) -> None:
        if (not run_id or first_sequence < 1 or capacity < 1
                or writer.run_id != run_id):
            raise ValueError("Typed ingress run, sequence, or capacity is invalid")
        UUID(attempt_id)
        UUID(prior_batch_id)
        self._writer = writer
        self._run_id = run_id
        self._attempt_id = attempt_id
        self._next_sequence = first_sequence
        self._prior_batch_id = prior_batch_id
        self._projection_context = deepcopy(dict(projection_context or {}))
        self._projector = projector
        self._queue: Queue[tuple[JournalRecord, str, Future[str]] | None] = Queue(capacity)
        self._lock = Lock()
        self._error: BaseException | None = None
        self._closed = False
        self._thread = Thread(target=self._run, name="arte-journal-ingress", daemon=False)
        self._thread.start()

    def submit(self, record: JournalRecord, *, source_cursor: str) -> Future[str]:
        """Never wait on ClickHouse, writer capacity, or a durability receipt."""
        if (not isinstance(record, JournalRecord) or not isinstance(source_cursor, str)
                or not source_cursor or source_cursor.lstrip().startswith(("{", "["))
                or record.event_time.tzinfo is None
                or record.recorded_at.tzinfo is None):
            raise ValueError("Typed ingress needs a record and scalar source cursor")
        UUID(record.record_id)
        with self._lock:
            if self._closed or self._error is not None:
                raise RuntimeError("Typed journal ingress is unavailable") from self._error
            if record.run_id != self._run_id or record.sequence != self._next_sequence:
                raise ValueError("Typed journal ingress requires a contiguous run sequence")
            # Runtime payload dictionaries can be mutated after submission. The
            # bounded snapshot is the fact the asynchronous projector will see.
            frozen = JournalRecord(
                record.record_id, record.run_id, record.sequence,
                record.event_time, record.recorded_at, record.category,
                record.entity_type, record.entity_id, record.account_id,
                deepcopy(record.payload),
            )
            receipt: Future[str] = Future()
            try:
                self._queue.put_nowait((frozen, source_cursor, receipt))
            except Full as exc:
                raise JournalIngressFull("Typed ingress is full; stop new admission") from exc
            self._next_sequence += 1
            return receipt

    def _run(self) -> None:
        pending: deque[tuple[Future[str], Future[str]]] = deque()
        try:
            while True:
                self._settle_ready(pending)
                try:
                    item = self._queue.get(timeout=0.05)
                except Empty:
                    continue
                if item is None:
                    self._queue.task_done()
                    break
                record, cursor, receipt = item
                try:
                    batch_id = str(uuid5(
                        NAMESPACE_URL,
                        f"{self._run_id}:{self._attempt_id}:{record.sequence}:"
                        f"{record.record_id}:typed-journal",
                    ))
                    batch = self._projector(
                        record,
                        run_month=record.event_time.astimezone(timezone.utc).date().replace(day=1),
                        attempt_id=self._attempt_id, batch_id=batch_id,
                        prior_batch_id=self._prior_batch_id,
                        source_cursor=cursor, **self._projection_context,
                    )
                    if (not isinstance(batch, TypedJournalBatch)
                            or batch.run_id != self._run_id
                            or batch.first_sequence != record.sequence
                            or batch.last_sequence != record.sequence
                            or batch.batch_id != batch_id
                            or batch.prior_batch_id != self._prior_batch_id
                            or batch.source_cursor != cursor):
                        raise ValueError("Typed projection changed ingress identity")
                    while True:
                        try:
                            writer_receipt = self._writer.submit(batch)
                            break
                        except JournalQueueFull:
                            # Only this worker waits. The producer remains
                            # nonblocking and its queue is bounded.
                            if pending:
                                self._settle_oldest(pending)
                            else:
                                # Another producer can own every writer slot.
                                # No local receipt exists to await yet.
                                sleep(0.01)
                    pending.append((writer_receipt, receipt))
                    self._prior_batch_id = batch_id
                except BaseException as exc:
                    _settle(receipt, error=exc)
                    raise
                finally:
                    self._queue.task_done()
            while pending:
                self._settle_oldest(pending)
        except BaseException as exc:
            with self._lock:
                self._error = exc
            for _, receipt in pending:
                if not receipt.done():
                    _settle(receipt, error=exc)
            while True:
                try:
                    item = self._queue.get_nowait()
                except Empty:
                    break
                if item is not None and not item[2].done():
                    _settle(item[2], error=exc)
                self._queue.task_done()

    @staticmethod
    def _settle_oldest(pending: deque[tuple[Future[str], Future[str]]]) -> None:
        if not pending:
            raise RuntimeError("Typed writer is full without an ingress predecessor")
        writer_receipt, receipt = pending.popleft()
        try:
            result = writer_receipt.result()
        except BaseException as exc:
            _settle(receipt, error=exc)
            raise
        _settle(receipt, result=result)

    @classmethod
    def _settle_ready(cls, pending: deque[tuple[Future[str], Future[str]]]) -> None:
        while pending and pending[0][0].done():
            cls._settle_oldest(pending)

    def close(self) -> None:
        """Control-plane drain; never invoke on a realtime market callback."""
        with self._lock:
            enqueue_stop = not self._closed
            self._closed = True
        if enqueue_stop:
            while self._thread.is_alive():
                try:
                    self._queue.put(None, timeout=0.05)
                    break
                except Full:
                    continue
        self._thread.join()
        if self._error is not None:
            raise RuntimeError("Typed journal ingress did not drain durably") from self._error
