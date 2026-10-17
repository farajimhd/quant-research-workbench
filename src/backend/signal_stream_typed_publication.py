"""Inactive bounded publisher joining Python occurrences to a typed cursor.

No active Signal Stream route invokes this worker. A failed or ambiguous write
is terminal for this lane: MergeTree has no uniqueness guarantee for retries.
"""
from __future__ import annotations

import asyncio
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
from src.backend.signal_stream_typed_readback import canonical_row, recover_committed_head


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
    allow_clock_only: bool = False


class TypedSignalStorage(Protocol):
    def insert_rows(self, table_name: str, rows: list[Mapping[str, Any]]) -> None: ...
    def read_occurrence_rows(self, table_name: str, *, event_id: str) -> list[Mapping[str, Any]]: ...
    def read_cursor_rows(self, table_name: str, *, session_key: str,
                         batch_sequence: int) -> list[Mapping[str, Any]]: ...
    def read_exact_prior_occurrence(self, event_id: str) -> Mapping[str, Any] | None: ...
    def list_cursor_commits(self, *, session_key: str) -> list[Mapping[str, Any]]: ...


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


def _publish_one(storage: TypedSignalStorage, batch: PublicationBatch, *, verify_existing=False) -> str:
    if type(verify_existing) is not bool:
        raise ValueError('Existing source verification mode must be explicit')
    if type(batch.allow_clock_only) is not bool:
        raise ValueError("typed Signal Stream clock-only selection must be explicit")
    if len(batch.occurrences) > 256:
        raise ValueError("typed occurrence batch exceeds 256 events")
    if (not batch.allow_clock_only and not batch.occurrences and batch.before_states == batch.after_states
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
            if rows[family] and not verify_existing:
                storage.insert_rows(table.name, rows[family])
        if not verify_existing:
            storage.insert_rows(PARENT.name, [rows["parent"]])
        readback = {}
        for table, family in _OCCURRENCE_FAMILIES:
            returned = storage.read_occurrence_rows(table.name, event_id=event_id)
            normalized = [canonical_row(table, row) for row in returned]
            if family != "parent":
                normalized.sort(key=lambda row: row["ordinal"])
            readback[family] = normalized[0] if family == "parent" and len(normalized) == 1 else normalized
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
        if cursor[family] and not verify_existing:
            storage.insert_rows(table.name, cursor[family])
    if not verify_existing:
        storage.insert_rows(COMMIT.name, [cursor["commit"]])
    readback_cursor = {}
    for table, family in _CURSOR_FAMILIES:
        raw = storage.read_cursor_rows(table.name, session_key=batch.session_key,
                                       batch_sequence=batch.batch_sequence)
        normalized = [canonical_row(table, row) for row in raw]
        if family == "state_delta":
            normalized.sort(key=lambda row: (row["signal_stream_id"], row["ticker"]))
        elif family == "occurrence_ref":
            normalized.sort(key=lambda row: row["ordinal"])
        else:
            normalized.sort(key=lambda row: (row["watchlist_id"], row["ticker"]))
        readback_cursor[family] = normalized
    commits = storage.read_cursor_rows(COMMIT.name, session_key=batch.session_key,
                                       batch_sequence=batch.batch_sequence)
    if len(commits) != 1:
        raise ValueError("typed cursor commit fence missing or duplicated")
    readback_cursor["commit"] = canonical_row(COMMIT, commits[0])
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

    def __init__(self, storage: TypedSignalStorage, *, capacity: int = 64,
                 attestor: Any | None = None) -> None:
        if capacity < 1:
            raise ValueError("typed Signal Stream publication capacity must be positive")
        if getattr(storage, "registered_transport", False) and attestor is None:
            raise ValueError("registered Signal Stream source requires Keeper head attestation")
        if (getattr(storage, "registered_transport", False)
                and getattr(storage, "strategy_one", False)
                is not getattr(attestor, "strategy_one", False)):
            raise ValueError("registered Signal Stream source and Keeper table scope differ")
        self._storage = storage
        self.source_context = getattr(storage, "source_context", None)
        if self.source_context is not None:
            from .signal_context_transport import require_context_components
            require_context_components(self.source_context, attestor._keeper)
        self._attestor = attestor
        self._capacity = capacity
        self._queue: queue.Queue[tuple[PublicationBatch, Future[str]]] = queue.Queue()
        self._pending: dict[tuple[str, int], Future[str]] = {}
        self._last_completed: dict[str, tuple[int, str]] = {}
        self._last_cutoff: dict[str, str | None] = {}
        self._bootstrapped: dict[str, tuple[str, str]] = {}
        self._bootstrapping: set[str] = set()
        self._lock = threading.Lock()
        self._fatal: BaseException | None = None
        self._closing = False
        self._consumer_recovery = {}
        self._recovery_counts = dict(resumed_stored=0, appended=0, failed=0)
        self._thread = threading.Thread(target=self._work, name="typed-signal-publication", daemon=True)
        self._started = False

    def bootstrap_session(self, *, session_key: str, configuration_revision: str,
                          source_revision: str, catalogs: Mapping[str, SourceCatalog],
                          anchor_cutoff_at=None, maximum_cutoff_at=None, maximum_ahead_batches=4096,
                          recovery_read_ahead=None):
        """Control-plane cold verification; never call on the realtime submit path."""
        if self.source_context is not None and (session_key, configuration_revision, source_revision) != (self.source_context.session_key, self.source_context.configuration_revision, self.source_context.source_revision):
            raise ValueError("Source bootstrap differs from its native context")
        if recovery_read_ahead is not None:
            from .completed_signal_cursor_reader import RunningResumeSignalCursorReadAhead
            from .signal_stream_typed_cursor import _time
            native = getattr(self._storage, '_read_storage', None)
            if (anchor_cutoff_at is None or type(recovery_read_ahead) is not RunningResumeSignalCursorReadAhead
                    or native is None or recovery_read_ahead._base._client is not native._client
                    or recovery_read_ahead._base._catalogs is not native._catalogs
                    or recovery_read_ahead._context != self.source_context
                    or self._attestor is None or recovery_read_ahead._source_keeper is not self._attestor._keeper
                    or recovery_read_ahead._owner_id != self._attestor._owner_id
                    or recovery_read_ahead._epoch != self._attestor._epoch
                    or _time(anchor_cutoff_at) != _time(recovery_read_ahead.anchor_cutoff_at)):
                raise ValueError('Anchored source read-ahead differs from native storage or owner')
        with self._lock:
            if self._started or self._closing or self._pending or self._fatal is not None:
                raise RuntimeError("typed Signal Stream bootstrap is unavailable")
            if session_key in self._bootstrapped or session_key in self._bootstrapping:
                raise ValueError("typed Signal Stream session is already bootstrapped")
            self._bootstrapping.add(session_key)
        try:
            acquire_barrier = getattr(self._storage, "acquire_bootstrap_barrier", None)
            fence = acquire_barrier() if acquire_barrier is not None else None
            head = recover_committed_head(
                recovery_read_ahead if recovery_read_ahead is not None else self._storage, session_key=session_key,
                configuration_revision=configuration_revision,
                source_revision=source_revision, catalogs=catalogs,
                anchor_cutoff_at=anchor_cutoff_at, maximum_cutoff_at=maximum_cutoff_at,
                maximum_ahead_batches=maximum_ahead_batches)
            if recovery_read_ahead is not None:
                recovery_read_ahead.verify_unchanged()
            if self._attestor is not None:
                self._attestor.bootstrap(
                    head, configuration_revision=configuration_revision,
                    source_revision=source_revision)
            if fence is not None:
                self._storage.release_bootstrap_barrier(head, fence)
            with self._lock:
                if self._started or self._closing or self._pending or self._fatal is not None:
                    raise RuntimeError("typed Signal Stream bootstrap raced publication")
                self._last_completed[session_key] = (head.sequence, head.content_hash)
                self._last_cutoff[session_key] = head.cutoff_at
                self._bootstrapped[session_key] = (configuration_revision, source_revision)
                if head.recovery_anchor is not None and head.recovery_anchor.sequence < head.sequence:
                    anchor = head.recovery_anchor
                    self._consumer_recovery[session_key] = dict(cursor=(anchor.sequence, anchor.content_hash),
                        cutoff=anchor.cutoff_at, first_sequence=anchor.sequence + 1,
                        hashes=head.commit_hashes[anchor.sequence:], cutoffs=head.commit_cutoffs[anchor.sequence:])
            return head
        finally:
            with self._lock:
                self._bootstrapping.discard(session_key)

    def bootstrap_runtime_session(self, runtime, **scope):
        """Hand the same verified cold head to its fresh stream runtime."""
        from .signal_stream_runtime_service import SignalStreamRuntime
        if type(runtime) is not SignalStreamRuntime:
            raise TypeError("Typed source bootstrap requires the shared Signal Stream runtime")
        with runtime._lock:
            if runtime._hydrated or runtime._generation or runtime._typed_pending is not None:
                raise RuntimeError("Typed source bootstrap requires a fresh Signal Stream runtime")
        head = self.bootstrap_session(**scope)
        runtime._install_committed_source(head.recovery_anchor or head,
            configuration_revision=scope["configuration_revision"], source_revision=scope["source_revision"])
        runtime.source_context = self.source_context
        return head

    async def bootstrap_runtime_session_async(self, runtime, **scope):
        """Own and drain the bounded cold SELECT/Keeper operation off-loop."""
        task = asyncio.create_task(asyncio.to_thread(self.bootstrap_runtime_session, runtime, **scope))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if task.done() and not task.cancelled():
                task.exception()
            raise

    def submit(self, batch: PublicationBatch) -> Future[str]:
        if not isinstance(batch, PublicationBatch):
            raise TypeError("typed Signal Stream batch is required")
        if type(batch.allow_clock_only) is not bool:
            raise ValueError("typed Signal Stream clock-only selection must be explicit")
        key = (batch.session_key, batch.batch_sequence)
        with self._lock:
            if self._closing or self._fatal is not None:
                raise RuntimeError("typed Signal Stream publication is unavailable")
            if key in self._pending:
                raise ValueError("typed Signal Stream batch is already pending")
            if any(session == batch.session_key for session, _ in self._pending):
                raise ValueError("typed Signal Stream session already has a pending batch")
            if self._bootstrapped.get(batch.session_key) != (batch.configuration_revision,
                                                              batch.source_revision):
                raise ValueError("typed Signal Stream session lacks matching cold bootstrap")
            prior = self._last_completed[batch.session_key]
            recovery = self._consumer_recovery.get(batch.session_key)
            if recovery is not None:
                prior = recovery['cursor']
                offset = batch.batch_sequence - recovery['first_sequence']
                from .signal_stream_typed_cursor import _time
                if (not 0 <= offset < len(recovery['hashes'])
                        or _time(recovery['cutoffs'][offset]) != _time(batch.cutoff_at)):
                    raise ValueError('Source consumer replay exceeds retained sealed clocks')
            if batch.allow_clock_only:
                from .signal_stream_typed_cursor import _time
                cutoff = _time(batch.cutoff_at)
                previous_cutoff = recovery['cutoff'] if recovery is not None else self._last_cutoff[batch.session_key]
                if previous_cutoff is not None and cutoff <= _time(previous_cutoff):
                    raise ValueError("typed Signal Stream clock-only cutoff must advance")
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

    def recovery_counts(self):
        """Bounded counts for stored consumer catch-up versus new publication."""
        with self._lock:
            return dict(self._recovery_counts, remaining_stored=sum(
                self._last_completed[key][0] - state['cursor'][0]
                for key, state in self._consumer_recovery.items()))

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
                staged = deepcopy(batch)
                with self._lock:
                    recovery = self._consumer_recovery.get(batch.session_key)
                    durable = self._last_completed[batch.session_key]
                if recovery is not None:
                    if self._attestor is not None:
                        self._attestor.verify_current_head(batch.session_key, sequence=durable[0], content_hash=durable[1])
                    head = _publish_one(self._storage, staged, verify_existing=True)
                    if head != recovery['hashes'][batch.batch_sequence - recovery['first_sequence']]:
                        raise ValueError('Source consumer replay differs from retained sealed head')
                    if self._attestor is not None:
                        self._attestor.verify_current_head(batch.session_key, sequence=durable[0], content_hash=durable[1])
                else:
                    begin = getattr(self._storage, "begin_batch", None)
                    if begin is not None:
                        begin(staged)
                    head = _publish_one(self._storage, staged)
                    finish = getattr(self._storage, "finish_batch", None)
                    if finish is not None:
                        finish(staged, head)
                    if self._attestor is not None:
                        head = self._attestor.attest(batch, head, self._storage)
            except BaseException as exc:
                with self._lock:
                    self._fatal = exc
                    self._recovery_counts['failed'] += 1
                    del self._pending[(batch.session_key, batch.batch_sequence)]
                try:
                    receipt.set_exception(exc)
                except InvalidStateError:
                    pass
            else:
                with self._lock:
                    from .signal_stream_typed_cursor import _time
                    if recovery is not None:
                        self._recovery_counts['resumed_stored'] += 1
                        recovery['cursor'] = (batch.batch_sequence, head)
                        recovery['cutoff'] = _time(batch.cutoff_at)
                        if batch.batch_sequence == durable[0]:
                            del self._consumer_recovery[batch.session_key]
                    else:
                        self._recovery_counts['appended'] += 1
                        self._last_completed[batch.session_key] = (batch.batch_sequence, head)
                        self._last_cutoff[batch.session_key] = _time(batch.cutoff_at)
                    del self._pending[(batch.session_key, batch.batch_sequence)]
                try:
                    receipt.set_result(head)
                except InvalidStateError:
                    pass
            finally:
                self._queue.task_done()
