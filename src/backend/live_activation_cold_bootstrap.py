"""Inactive exact cold join of dispatch, completion and activation watches.

The source hash prefix must come from a separately sealed Signal Stream head.
This audit performs only control-plane reads and does not install watches or
authorize trading: assignment/configuration and broker recovery remain absent.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Mapping

from src.backend.live_signal_work_completion import (
    CompletionKeeper, CompletionStorage, read_completed_dispatch_prefix,
)
from src.backend.signal_dispatch_typed_cursor import DispatchColdStorage
from src.backend.signal_stream_typed_readback import canonical_row, recover_committed_head
from src.backend.signal_stream_typed_cursor import (
    ADMISSION_DELTA, COMMIT as SOURCE_COMMIT, OCCURRENCE_REF, STATE_DELTA,
)
from src.backend.signal_stream_typed_occurrence import (
    COLUMN as SOURCE_COLUMN, FIELD as SOURCE_FIELD,
    PARENT as SOURCE_PARENT, RULE as SOURCE_RULE,
)
from src.backend.signal_stream_session_head import SignalSessionHeadKeeper
from src.backend.live_activation_session_fence import ActivationSessionFence
from src.trading_runtime.arte_journal_writer import _literal, _rows
from src.trading_runtime.arte_activation_projection import (
    load_activation, load_day_activations, prepare_activation_rows,
    project_activation,
)


class _BoundedSourceCommits:
    """Use exact-column LIMIT before typed cursor hydration, never unbounded list."""

    def __init__(self, storage: Any, client: Any, *, limit: int) -> None:
        if not callable(getattr(client, "execute", None)):
            raise TypeError("source commit read requires ClickHouse client")
        self._storage, self._client, self._limit = storage, client, limit
        self._commits: list[dict[str, Any]] | None = None

    def list_cursor_commits(self, *, session_key: str) -> list[dict[str, Any]]:
        if self._commits is None:
            columns = ",".join(name for name, _ in SOURCE_COMMIT.columns)
            rows = _rows(self._client,
                f"SELECT {columns} FROM arte.{SOURCE_COMMIT.name} "
                f"WHERE session_key={_literal(session_key)} "
                f"ORDER BY batch_sequence LIMIT {self._limit} FORMAT JSONEachRow")
            if len(rows) >= self._limit:
                raise ValueError("Signal Stream commit prefix exceeds recovery bound")
            self._commits = [canonical_row(SOURCE_COMMIT, row) for row in rows]
        return list(self._commits)

    def read_cursor_rows(self, *args: Any, **kwargs: Any) -> Any:
        return self._storage.read_cursor_rows(*args, **kwargs)

    def read_occurrence_rows(self, *args: Any, **kwargs: Any) -> Any:
        return self._storage.read_occurrence_rows(*args, **kwargs)


def _distinct_source_values(client: Any, table: Any, *, session_key: str,
                            column: str, limit: int) -> set[Any]:
    if column not in {name for name, _ in table.columns}:
        raise ValueError("source audit column is not in typed table")
    rows = _rows(client,
        f"SELECT DISTINCT {column} FROM arte.{table.name} "
        f"WHERE session_key={_literal(session_key)} "
        f"ORDER BY {column} LIMIT {limit} FORMAT JSONEachRow")
    if len(rows) >= limit:
        raise ValueError("source table inventory exceeds cold bound")
    if any(set(row) != {column} for row in rows):
        raise ValueError("source table inventory has unexpected columns")
    values = [row[column] for row in rows]
    if len(set(values)) != len(values):
        raise ValueError("source table inventory is duplicate")
    return set(values)


def _audit_source_orphans(client: Any, *, session_key: str,
                          head_sequence: int, max_occurrences: int) -> None:
    """Reject any source row outside committed sequence/event identities."""
    sequences = set(range(1, head_sequence + 1))
    for table in (SOURCE_COMMIT, STATE_DELTA, OCCURRENCE_REF, ADMISSION_DELTA):
        observed = _distinct_source_values(
            client, table, session_key=session_key,
            column="batch_sequence", limit=head_sequence + 2)
        if not observed <= sequences:
            raise ValueError("source table has uncommitted or invalid batch")
    referenced = _distinct_source_values(
        client, OCCURRENCE_REF, session_key=session_key,
        column="event_id", limit=max_occurrences + 1)
    parents = _distinct_source_values(
        client, SOURCE_PARENT, session_key=session_key,
        column="event_id", limit=max_occurrences + 1)
    if parents != referenced:
        raise ValueError("source occurrence parents differ from committed references")
    for table in (SOURCE_RULE, SOURCE_COLUMN, SOURCE_FIELD):
        children = _distinct_source_values(
            client, table, session_key=session_key,
            column="event_id", limit=max_occurrences + 1)
        if not children <= referenced:
            raise ValueError("source occurrence has orphan child rows")


def cold_audit_activation_watches(
    activation_client: Any, dispatch_storage: DispatchColdStorage,
    completion_storage: CompletionStorage, completion_keeper: CompletionKeeper, *,
    session_date: date, source_commit_hashes: tuple[str, ...],
    configuration_revision_id: str,
) -> tuple[dict[str, Any], ...]:
    """Require exact one-to-one durable ACK, completion and activation proof."""
    if type(session_date) is not date:
        raise ValueError("activation cold audit requires a session date")
    proofs = read_completed_dispatch_prefix(
        dispatch_storage, completion_storage, completion_keeper,
        session_key=session_date.isoformat(),
        source_commit_hashes=source_commit_hashes,
        configuration_revision_id=configuration_revision_id)
    expected: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    watches: set[tuple[str, str]] = set()
    for proof in proofs:
        intents, acks = proof.materialize()
        intent, ack = intents["intents"][proof.ordinal], acks["acks"][proof.ordinal]
        delivery_id = intent["delivery_id"]
        watch = (intent["run_plan_id"], intent["ticker"])
        if delivery_id in expected or watch in watches:
            raise ValueError("cold dispatch has duplicate delivery or activation watch")
        expected[delivery_id] = (intent, ack)
        watches.add(watch)
    activations = load_day_activations(
        activation_client, session_date=session_date,
        page_size=min(1024, len(expected) + 1),
        max_inventory_rows_per_family=len(expected))
    if len(activations) != len(expected):
        raise ValueError("cold activation inventory differs from completed dispatch")
    observed: set[str] = set()
    result = []
    for activation in activations:
        delivery_id = activation["delivery_id"]
        if delivery_id in observed or delivery_id not in expected:
            raise ValueError("cold activation is duplicate or outside completed dispatch")
        observed.add(delivery_id)
        intent, ack = expected[delivery_id]
        if any(activation.get(key) != intent[key] for key in (
                "run_plan_id", "profile_id", "book_id", "ticker",
                "signal_stream_id", "event_id")):
            raise ValueError("cold activation identity differs from dispatch")
        parent = prepare_activation_rows(project_activation(activation))[
            "trading_activation_v1"][0]
        if (parent["event_time"] != intent["event_time"]
                or parent["content_hash"] != ack["activation_receipt_hash"]):
            raise ValueError("cold activation content differs from durable ACK")
        result.append(activation)
    if observed != set(expected):
        raise ValueError("cold activation delivery coverage is incomplete")
    return tuple(result)


def read_attested_activation_prefix(
    activation_client: Any, dispatch_storage: DispatchColdStorage,
    completion_storage: CompletionStorage, completion_keeper: CompletionKeeper, *,
    session_date: date, source_commit_hashes: tuple[str, ...],
    configuration_revision_id: str,
) -> tuple[dict[str, Any], ...]:
    """Restore only completed, Keeper-attested ACK identities, in source order.

    Unreferenced late MergeTree rows are not admitted. They remain diagnostic
    orphans; this does not authorize startup without a separately stable source
    prefix, assignment/broker recovery, and execution fencing.
    """
    if type(session_date) is not date:
        raise ValueError("activation receipt read requires a session date")
    proofs = read_completed_dispatch_prefix(
        dispatch_storage, completion_storage, completion_keeper,
        session_key=session_date.isoformat(),
        source_commit_hashes=source_commit_hashes,
        configuration_revision_id=configuration_revision_id)
    seen_delivery: set[str] = set()
    seen_watch: set[tuple[str, str]] = set()
    restored = []
    for proof in proofs:
        intents, acks = proof.materialize()
        intent = intents["intents"][proof.ordinal]
        ack = acks["acks"][proof.ordinal]
        delivery_id = intent["delivery_id"]
        watch = (intent["run_plan_id"], intent["ticker"])
        if delivery_id in seen_delivery or watch in seen_watch:
            raise ValueError("attested activation prefix has duplicate delivery or watch")
        seen_delivery.add(delivery_id)
        seen_watch.add(watch)
        activation = load_activation(
            activation_client, session_date=session_date,
            run_plan_id=intent["run_plan_id"], ticker=intent["ticker"],
            event_id=intent["event_id"])
        if (activation["delivery_id"] != delivery_id
                or any(activation.get(key) != intent[key] for key in (
                    "run_plan_id", "profile_id", "book_id", "ticker",
                    "signal_stream_id", "event_id"))):
            raise ValueError("attested activation differs from dispatch identity")
        parent = prepare_activation_rows(project_activation(activation))[
            "trading_activation_v1"][0]
        if (parent["event_time"] != intent["event_time"]
                or parent["content_hash"] != ack["activation_receipt_hash"]):
            raise ValueError("attested activation differs from ACK receipt")
        restored.append(activation)
    return tuple(restored)


def _cold_recover_activation_checkpoint_under_fence(
    activation_client: Any, source_storage: Any, source_commit_client: Any,
    source_keeper: SignalSessionHeadKeeper,
    dispatch_storage: DispatchColdStorage,
    completion_storage: CompletionStorage, completion_keeper: CompletionKeeper, *,
    session_date: date, configuration_revision_id: str,
    source_revision_id: str, catalogs: Mapping[str, Any],
    max_source_batches: int = 100_000,
    max_source_occurrences: int = 100_000,
    receipt_defined: bool = False,
) -> tuple[dict[str, Any], ...]:
    """Read-only typed replacement prerequisite for the SQLite watch checkpoint.

    A stable Keeper source head freezes the exact committed source prefix. This
    must run on the control plane; it neither installs watches nor permits live
    startup while assignment, execution, and broker recovery remain gated.
    """
    if (type(session_date) is not date or type(max_source_batches) is not int
            or not 1 <= max_source_batches <= 100_000
            or type(max_source_occurrences) is not int
            or not 1 <= max_source_occurrences <= 100_000):
        raise ValueError("activation checkpoint session or bound is invalid")
    session_key = session_date.isoformat()
    first = source_keeper.read_head(session_key)
    if (first.session_key != session_key or first.batch_sequence < 1
            or first.batch_sequence > max_source_batches
            or first.configuration_revision != configuration_revision_id
            or first.source_revision != source_revision_id):
        raise ValueError("Signal Stream Keeper head differs from recovery scope")
    bounded_storage = _BoundedSourceCommits(
        source_storage, source_commit_client, limit=max_source_batches + 1)
    recovered = recover_committed_head(
        bounded_storage, session_key=session_key,
        configuration_revision=configuration_revision_id,
        source_revision=source_revision_id, catalogs=catalogs)
    if (recovered.sequence != first.batch_sequence
            or recovered.content_hash != first.cursor_commit_hash):
        raise ValueError("typed Signal Stream cursor differs from Keeper head")
    commits = bounded_storage.list_cursor_commits(session_key=session_key)
    commits.sort(key=lambda row: row["batch_sequence"])
    if (len(commits) != first.batch_sequence
            or [row.get("batch_sequence") for row in commits]
            != list(range(1, first.batch_sequence + 1))):
        raise ValueError("Signal Stream commit prefix is missing or duplicate")
    hashes = tuple(row.get("content_hash") for row in commits)
    if (any(type(value) is not str or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)
            for value in hashes)
            or hashes[-1] != first.cursor_commit_hash):
        raise ValueError("Signal Stream commit prefix hash differs")
    _audit_source_orphans(
        source_commit_client, session_key=session_key,
        head_sequence=first.batch_sequence,
        max_occurrences=max_source_occurrences)
    activation_reader = (read_attested_activation_prefix if receipt_defined
                         else cold_audit_activation_watches)
    watches = activation_reader(
        activation_client, dispatch_storage, completion_storage,
        completion_keeper, session_date=session_date,
        source_commit_hashes=hashes,
        configuration_revision_id=configuration_revision_id)
    if source_keeper.read_head(session_key) != first:
        raise RuntimeError("Signal Stream Keeper head changed during activation recovery")
    return watches


def audit_activation_checkpoint_under_cooperative_fences(
    activation_client: Any, source_storage: Any, source_commit_client: Any,
    source_keeper: SignalSessionHeadKeeper,
    dispatch_storage: DispatchColdStorage,
    completion_storage: CompletionStorage, completion_keeper: CompletionKeeper, *,
    session_date: date, configuration_revision_id: str,
    source_revision_id: str, catalogs: Mapping[str, Any],
    owner_id: str, activation_fence: ActivationSessionFence,
    max_source_batches: int = 100_000,
    max_source_occurrences: int = 100_000,
    receipt_defined: bool = False,
) -> tuple[dict[str, Any], ...]:
    """Diagnostic cold audit, not an admission or executable checkpoint.

    A ClickHouse INSERT already sent before a Keeper session loss may land
    after both cooperative locks were reacquired and this inventory finished.
    No current MergeTree table atomically enforces the Keeper epoch.
    """
    if (type(session_date) is not date or type(owner_id) is not str
            or not owner_id or any(char in owner_id for char in "\r\n\x00")):
        raise ValueError("cold activation Keeper owner or session is invalid")
    session_key = session_date.isoformat()
    epoch = source_keeper.acquire(session_key, owner_id=owner_id)
    if epoch is None:
        raise RuntimeError("Signal Stream source owner is held by another publisher")
    activation_epoch: int | None = None
    try:
        if not source_keeper.is_current(session_key, owner_id=owner_id, epoch=epoch):
            raise RuntimeError("Signal Stream source owner fence lost")
        activation_epoch = activation_fence.acquire(session_key, owner_id=owner_id)
        if activation_epoch is None:
            raise RuntimeError("activation session publication is in progress")
        if not activation_fence.is_current(
                session_key, owner_id=owner_id, epoch=activation_epoch):
            raise RuntimeError("activation session Keeper fence lost")
        watches = _cold_recover_activation_checkpoint_under_fence(
            activation_client, source_storage, source_commit_client,
            source_keeper, dispatch_storage, completion_storage,
            completion_keeper, session_date=session_date,
            configuration_revision_id=configuration_revision_id,
            source_revision_id=source_revision_id, catalogs=catalogs,
            max_source_batches=max_source_batches,
            max_source_occurrences=max_source_occurrences,
            receipt_defined=receipt_defined)
        if not source_keeper.is_current(session_key, owner_id=owner_id, epoch=epoch):
            raise RuntimeError("Signal Stream source owner fence lost during recovery")
        if not activation_fence.is_current(
                session_key, owner_id=owner_id, epoch=activation_epoch):
            raise RuntimeError("activation session Keeper fence lost during recovery")
        return watches
    finally:
        if activation_epoch is not None:
            activation_fence.release(session_key, owner_id=owner_id,
                                     epoch=activation_epoch)
        source_keeper.release(session_key, owner_id=owner_id, epoch=epoch)


def audit_receipt_defined_activation_prefix_under_fences(
    activation_client: Any, source_storage: Any, source_commit_client: Any,
    source_keeper: SignalSessionHeadKeeper,
    dispatch_storage: DispatchColdStorage,
    completion_storage: CompletionStorage, completion_keeper: CompletionKeeper, *,
    session_date: date, configuration_revision_id: str,
    source_revision_id: str, catalogs: Mapping[str, Any],
    owner_id: str, activation_fence: ActivationSessionFence,
    max_source_batches: int = 100_000,
    max_source_occurrences: int = 100_000,
) -> tuple[dict[str, Any], ...]:
    """Inactive causal-prefix audit; only attested receipts become visible."""
    return audit_activation_checkpoint_under_cooperative_fences(
        activation_client, source_storage, source_commit_client,
        source_keeper, dispatch_storage, completion_storage,
        completion_keeper, session_date=session_date,
        configuration_revision_id=configuration_revision_id,
        source_revision_id=source_revision_id, catalogs=catalogs,
        owner_id=owner_id, activation_fence=activation_fence,
        max_source_batches=max_source_batches,
        max_source_occurrences=max_source_occurrences,
        receipt_defined=True)


class ActivationRecoveryUnfenced(RuntimeError):
    """A delayed ClickHouse INSERT can invalidate a completed cold inventory."""


def cold_recover_activation_checkpoint(*args: Any, **kwargs: Any) -> None:
    """Fail-closed live admission gate until server-enforced write fencing exists.

    Cooperative Keeper claims and repeated reads cannot bound an old network
    request's arrival time. The diagnostic audit above is intentionally not a
    recovery authority and must never be used to install executable watches.
    """
    raise ActivationRecoveryUnfenced(
        "activation cold recovery lacks a server-enforced INSERT epoch or drain proof")
