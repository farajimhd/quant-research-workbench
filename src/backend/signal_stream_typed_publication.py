"""Inactive bounded publisher joining Python occurrences to a typed cursor.

No active Signal Stream route invokes this worker. A failed or ambiguous write
is terminal for this lane: MergeTree has no uniqueness guarantee for retries.
"""
from __future__ import annotations

import queue
import threading
from concurrent.futures import Future, InvalidStateError
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from src.backend.signal_stream_typed_catalog import SignalFieldCatalog
from src.backend.signal_stream_typed_cursor import (
    ADMISSION_DELTA, COMMIT, OCCURRENCE_REF, STATE_DELTA,
    project_cursor_batch, recover_cursor_batch,
)
from src.backend.signal_stream_typed_occurrence import (
    COLUMN, FIELD, PARENT, RULE, project_typed_occurrence,
    restore_typed_occurrence,
)


@dataclass(frozen=True)
class SourceCatalog:
    catalog: SignalFieldCatalog
    stream: Mapping[str, Any]
    columns: Mapping[str, Mapping[str, Any]]


@dataclass(frozen=True)
class PublicationBatch:
    session_key: str
    batch_sequence: int
    cutoff_at: str
    configuration_revision: str
    source_revision: str
    previous_commit_hash: str
    before_states: Mapping[str, Any]
    after_states: Mapping[str, Any]
    before_admissions: Mapping[str, Any]
    after_admissions: Mapping[str, Any]
    occurrences: tuple[Mapping[str, Any], ...]
    catalogs: Mapping[str, SourceCatalog]


class TypedSignalStorage(Protocol):
    def insert_rows(self, table_name: str, rows: list[Mapping[str, Any]]) -> None: ...
    def read_occurrence_rows(self, table_name: str, *, event_id: str) -> list[Mapping[str, Any]]: ...
    def read_cursor_rows(self, table_name: str, *, session_key: str,
                         batch_sequence: int) -> list[Mapping[str, Any]]: ...
    def read_exact_prior_occurrence(self, event_id: str) -> Mapping[str, Any] | None: ...


class _Authority:
    def __init__(self, current: Mapping[str, Mapping[str, Any]], storage: TypedSignalStorage):
        self._current = current
        self._storage = storage

    def read_exact(self, event_id: str) -> Mapping[str, Any] | None:
        return self._current.get(event_id) or self._storage.read_exact_prior_occurrence(event_id)


_OCCURRENCE_FAMILIES = ((PARENT, "parent"), (RULE, "rules"),
                        (COLUMN, "columns"), (FIELD, "fields"))
_CURSOR_FAMILIES = ((STATE_DELTA, "state_delta"),
                    (OCCURRENCE_REF, "occurrence_ref"),
                    (ADMISSION_DELTA, "admission_delta"))


def _publish_one(storage: TypedSignalStorage, batch: PublicationBatch) -> str:
    if len(batch.occurrences) > 256:
        raise ValueError("typed occurrence batch exceeds 256 events")
    if (not batch.occurrences and batch.before_states == batch.after_states
            and batch.before_admissions == batch.after_admissions):
        raise ValueError("empty typed Signal Stream no-op batch")
    projected = []
    for occurrence in batch.occurrences:
        source = batch.catalogs.get(str(occurrence.get("signal_stream_id") or ""))
        if source is None or source.catalog.configuration_revision != batch.configuration_revision:
            raise ValueError("typed occurrence catalog is missing or mixed revision")
        projected.append((occurrence["event_id"], source,
                          project_typed_occurrence(occurrence, source.catalog,
                                                   source.stream, source.columns)))
    if len({item[0] for item in projected}) != len(projected):
        raise ValueError("duplicate typed occurrence event ID")
    restored = {}
    for event_id, source, rows in projected:
        # Child families precede the occurrence parent. Any insert exception
        # is ambiguous and stops publication; never blindly retry children.
        for table, family in _OCCURRENCE_FAMILIES[1:]:
            if rows[family]:
                storage.insert_rows(table.name, rows[family])
        storage.insert_rows(PARENT.name, [rows["parent"]])
        readback = {}
        for table, family in _OCCURRENCE_FAMILIES:
            returned = storage.read_occurrence_rows(table.name, event_id=event_id)
            readback[family] = returned[0] if family == "parent" and len(returned) == 1 else returned
        if readback != rows:
            raise ValueError("typed occurrence cold readback is incomplete or duplicated")
        restored[event_id] = restore_typed_occurrence(
            readback, source.catalog, source.stream, source.columns)
    authority = _Authority(restored, storage)
    cursor = project_cursor_batch(
        batch.before_states, batch.after_states, list(batch.occurrences),
        session_key=batch.session_key, batch_sequence=batch.batch_sequence,
        cutoff_at=batch.cutoff_at, configuration_revision=batch.configuration_revision,
        source_revision=batch.source_revision,
        previous_commit_hash=batch.previous_commit_hash,
        before_admissions=batch.before_admissions,
        after_admissions=batch.after_admissions,
        occurrence_authority=authority,
    )
    for table, family in _CURSOR_FAMILIES:
        if cursor[family]:
            storage.insert_rows(table.name, cursor[family])
    storage.insert_rows(COMMIT.name, [cursor["commit"]])
    readback_cursor = {
        family: storage.read_cursor_rows(table.name, session_key=batch.session_key,
                                         batch_sequence=batch.batch_sequence)
        for table, family in _CURSOR_FAMILIES
    }
    commits = storage.read_cursor_rows(COMMIT.name, session_key=batch.session_key,
                                       batch_sequence=batch.batch_sequence)
    if len(commits) != 1:
        raise ValueError("typed cursor commit fence missing or duplicated")
    readback_cursor["commit"] = commits[0]
    if readback_cursor != cursor:
        raise ValueError("typed cursor cold readback is incomplete or duplicated")
    states, admissions, occurrences, head = recover_cursor_batch(
        batch.before_states, readback_cursor, occurrence_authority=authority,
        session_key=batch.session_key, expected_sequence=batch.batch_sequence,
        previous_commit_hash=batch.previous_commit_hash,
        configuration_revision=batch.configuration_revision,
        source_revision=batch.source_revision,
        before_admissions=batch.before_admissions,
    )
    if (states != batch.after_states or admissions != batch.after_admissions
            or occurrences != list(batch.occurrences)):
        raise ValueError("typed Signal Stream recovered batch differs from staged source")
    return head


class TypedSignalPublicationQueue:
    """Single-worker bounded queue; submission performs no storage I/O.

    The caller transfers ownership of the batch and must not mutate any nested
    mapping/list until its receipt completes. The worker takes the full copy.
    Production callers must stage immutable inputs before this handoff.
    """

    def __init__(self, storage: TypedSignalStorage, *, capacity: int = 64) -> None:
        if capacity < 1:
            raise ValueError("typed Signal Stream publication capacity must be positive")
        self._storage = storage
        self._capacity = capacity
        self._queue: queue.Queue[tuple[PublicationBatch, Future[str]]] = queue.Queue()
        self._pending: dict[tuple[str, int], Future[str]] = {}
        self._last_completed: dict[str, tuple[int, str]] = {}
        self._lock = threading.Lock()
        self._fatal: BaseException | None = None
        self._closing = False
        self._thread = threading.Thread(target=self._work, name="typed-signal-publication", daemon=True)
        self._started = False

    def submit(self, batch: PublicationBatch) -> Future[str]:
        if not isinstance(batch, PublicationBatch):
            raise TypeError("typed Signal Stream batch is required")
        key = (batch.session_key, batch.batch_sequence)
        with self._lock:
            if self._closing or self._fatal is not None:
                raise RuntimeError("typed Signal Stream publication is unavailable")
            if key in self._pending:
                raise ValueError("typed Signal Stream batch is already pending")
            if any(session == batch.session_key for session, _ in self._pending):
                raise ValueError("typed Signal Stream session already has a pending batch")
            prior = self._last_completed.get(batch.session_key, (0, "0" * 64))
            if (batch.batch_sequence != prior[0] + 1
                    or batch.previous_commit_hash != prior[1]):
                raise ValueError("typed Signal Stream batch sequence or prior fence differs")
            if len(self._pending) >= self._capacity:
                raise RuntimeError("typed Signal Stream publication capacity is exhausted")
            receipt: Future[str] = Future()
            self._pending[key] = receipt
            self._queue.put_nowait((batch, receipt))
            if not self._started:
                self._thread.start()
                self._started = True
            return receipt

    def close(self, *, timeout: float = 10.0) -> None:
        with self._lock:
            self._closing = True
        if self._started:
            self._thread.join(timeout=timeout)
        if self._started and self._thread.is_alive():
            raise RuntimeError("typed Signal Stream publication worker did not drain")

    def _work(self) -> None:
        while True:
            try:
                batch, receipt = self._queue.get(timeout=0.1)
            except queue.Empty:
                with self._lock:
                    if self._closing:
                        return
                continue
            try:
                with self._lock:
                    failure = self._fatal
                if failure is not None:
                    raise RuntimeError("typed Signal Stream publication halted") from failure
                head = _publish_one(self._storage, deepcopy(batch))
            except BaseException as exc:
                with self._lock:
                    self._fatal = exc
                    del self._pending[(batch.session_key, batch.batch_sequence)]
                try:
                    receipt.set_exception(exc)
                except InvalidStateError:
                    pass
            else:
                with self._lock:
                    self._last_completed[batch.session_key] = (batch.batch_sequence, head)
                    del self._pending[(batch.session_key, batch.batch_sequence)]
                try:
                    receipt.set_result(head)
                except InvalidStateError:
                    pass
            finally:
                self._queue.task_done()
