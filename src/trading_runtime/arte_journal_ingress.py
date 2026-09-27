"""Bounded, disk-free ingress from runtime facts to the typed writer lane.

The market callback only snapshots and enqueues a record. Projection, writer
submission, and durability confirmation happen on this separate worker. This
is a transport primitive, not permission to enable an incomplete run mode.
"""
from __future__ import annotations

from collections import deque
from concurrent.futures import Future, InvalidStateError
from copy import deepcopy
from datetime import date, timezone
from queue import Empty, Full, Queue
from threading import Lock, Thread
from time import sleep
from typing import Any, Callable, Mapping
from uuid import NAMESPACE_URL, UUID, uuid5

from src.trading_runtime.arte_journal_projection import project_journal_record
from src.trading_runtime.arte_journal_writer import (
    JournalQueueFull, TypedJournalBatch, V4StrategyOneEntryBatch,
)
from src.trading_runtime.journal_contract import JournalRecord, canonical_json


class JournalIngressFull(RuntimeError):
    """The producer must stop new admission; no accepted fact was dropped."""


_LIVE_V4_BASE_KINDS = frozenset({
    ("lifecycle", "run"),
    ("broker", "connection_state"),
    ("risk", "risk_snapshot"),
    ("risk", "continuous_risk_state"),
})


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
        context = deepcopy(dict(projection_context or {}))
        live_v4 = getattr(writer, "journal_profile", None) == "live_v4"
        if live_v4 and (getattr(writer, "run_mode", None) not in {"live", "paper"}
                        or context.get("expected_mode") != writer.run_mode
                        or not callable(getattr(writer, "submit_base_v4", None))):
            raise ValueError("Live V4 ingress needs its pinned mode and base writer")
        self._writer = writer
        self._live_v4 = live_v4
        self._run_id = run_id
        self._attempt_id = attempt_id
        self._next_sequence = first_sequence
        self._prior_batch_id = prior_batch_id
        self._projection_context = context
        self._projector = projector
        self._queue: Queue[tuple[JournalRecord, str, Future[str],
                                 tuple[Any, date] | None] | None] = Queue(capacity)
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
        if self._live_v4 and (record.category, record.entity_type) not in _LIVE_V4_BASE_KINDS:
            raise ValueError("Live V4 base ingress requires a specialized typed source")
        if (self._live_v4 and (record.category, record.entity_type) == ("lifecycle", "run")
                and record.payload.get("status") != "running"):
            raise ValueError("Live V4 terminal lifecycle requires its recovery anchor")
        # Runtime payload dictionaries can be mutated after submission. Snapshot
        # caller-owned data before taking the admission lock so large evidence
        # payloads do not serialize unrelated realtime producers.
        frozen = JournalRecord(
            record.record_id, record.run_id, record.sequence,
            record.event_time, record.recorded_at, record.category,
            record.entity_type, record.entity_id, record.account_id,
            deepcopy(record.payload),
        )
        return self._enqueue(frozen, source_cursor, None)

    def submit_strategy_one_entry(
        self, record: JournalRecord, *, proposal: Any,
        session_date: date, source_cursor: str,
    ) -> Future[str]:
        """Queue an entry and its scalar child; projection stays off the actor."""
        from src.trading_runtime.strategy_one_stateful import StrategyOneEntryProposal

        if (not self._live_v4 or not isinstance(record, JournalRecord)
                or not isinstance(proposal, StrategyOneEntryProposal)
                or type(session_date) is not date
                or not callable(getattr(self._writer,
                                        "submit_strategy_one_entry_v4", None))
                or (record.category, record.entity_type) !=
                   ("strategy", "strategy_intent")
                or not isinstance(source_cursor, str) or not source_cursor
                or source_cursor.lstrip().startswith(("{", "["))
                or record.event_time.tzinfo is None
                or record.recorded_at.tzinfo is None):
            raise ValueError("Live Strategy 1 entry requires its typed causal source")
        UUID(record.record_id)
        frozen = JournalRecord(
            record.record_id, record.run_id, record.sequence,
            record.event_time, record.recorded_at, record.category,
            record.entity_type, record.entity_id, record.account_id,
            deepcopy(record.payload),
        )
        return self._enqueue(frozen, source_cursor,
                             (deepcopy(proposal), session_date))

    def _enqueue(self, frozen: JournalRecord, source_cursor: str,
                 entry_source: tuple[Any, date] | None) -> Future[str]:
        with self._lock:
            if self._closed or self._error is not None:
                raise RuntimeError("Typed journal ingress is unavailable") from self._error
            if frozen.run_id != self._run_id or frozen.sequence != self._next_sequence:
                raise ValueError("Typed journal ingress requires a contiguous run sequence")
            receipt: Future[str] = Future()
            try:
                self._queue.put_nowait((frozen, source_cursor, receipt, entry_source))
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
                record, cursor, receipt, entry_source = item
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
                    if entry_source is not None:
                        from src.trading_runtime.arte_strategy_one_entry_journal import (
                            project_strategy_one_entry_evidence,
                        )
                        from src.trading_runtime.strategy_one_intent import (
                            strategy_one_entry_intent,
                        )

                        proposal, session_date = entry_source
                        intent = strategy_one_entry_intent(
                            proposal, session_date=session_date)
                        payload = {key: value for key, value in record.payload.items()
                                   if key not in {"strategy_id", "strategy_revision",
                                                  "correlation_id", "causation_id"}}
                        if (record.account_id != proposal.account_id
                                or record.entity_id != intent.intent_id
                                or canonical_json(payload) != canonical_json(intent.payload())
                                or len(batch.intents) != 1
                                or batch.intents[0]["intent_id"] != intent.intent_id):
                            raise ValueError("Live Strategy 1 entry differs from its typed intent")
                        evidence = project_strategy_one_entry_evidence(
                            proposal, intent, session_date=session_date,
                            run_id=batch.run_id, batch_id=batch.batch_id,
                            parent_record_id=record.record_id)
                        unit = V4StrategyOneEntryBatch(batch, (evidence,))
                    else:
                        unit = batch
                    while True:
                        try:
                            writer_receipt = (
                                self._writer.submit_strategy_one_entry_v4(unit)
                                if entry_source is not None else
                                self._writer.submit_base_v4(unit)
                                if self._live_v4 else self._writer.submit(unit))
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
