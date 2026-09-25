"""Inactive typed completion receipt for one committed dispatch delivery.

A missing completion after broker work is *uncertain*, never retryable. This
module defines no active writer or DDL execution path.
"""
from __future__ import annotations

from dataclasses import dataclass
from concurrent.futures import Future, InvalidStateError
from datetime import UTC, datetime
from hashlib import sha256
import queue
import threading
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from src.backend.signal_dispatch_typed_cursor import verify_dispatch_cursor
from src.backend.live_signal_completion_keeper import completion_resource
from src.backend.signal_stream_typed_cursor import TypedTable
from src.backend.signal_stream_typed_readback import canonical_row
from src.trading_runtime.journal_contract import canonical_json


COMPLETION = TypedTable(
    "live_signal_work_completion_typed_v1",
    (("schema_version", "UInt16"), ("session_key", "Date"),
     ("source_batch_sequence", "UInt64"), ("ordinal", "UInt32"),
     ("delivery_id", "String"), ("intent_content_hash", "FixedString(64)"),
     ("dispatch_ack_commit_hash", "FixedString(64)"),
     ("activation_receipt_hash", "FixedString(64)"),
     ("keeper_owner_id", "String"), ("keeper_epoch", "UInt64"),
     ("processed_at", "DateTime64(6, 'UTC')"),
     ("outcome", "LowCardinality(String)"),
     ("content_hash", "FixedString(64)")),
    "session_key, source_batch_sequence, ordinal",
)


@dataclass(frozen=True)
class CompletionProjection:
    row: Mapping[str, Any]


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if value is None or type(value) in (str, int, float, bool):
        return value
    raise ValueError("completion proof has unmodeled value")


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True, slots=True, init=False)
class CompletionProof:
    intents: Mapping[str, Any]
    acks: Mapping[str, Any]
    ordinal: int
    session_key: str
    source_batch_sequence: int
    delivery_id: str
    intent_content_hash: str

    @classmethod
    def _from_validated(cls, intents: Mapping[str, Any], acks: Mapping[str, Any],
                        ordinal: int, row: Mapping[str, Any]) -> CompletionProof:
        packet = object.__new__(cls)
        for name, value in (
            ("intents", _freeze(intents)), ("acks", _freeze(acks)),
            ("ordinal", ordinal), ("session_key", row["session_key"]),
            ("source_batch_sequence", row["source_batch_sequence"]),
            ("delivery_id", row["delivery_id"]),
            ("intent_content_hash", row["content_hash"]),
        ):
            object.__setattr__(packet, name, value)
        return packet

    def materialize(self) -> tuple[dict[str, Any], dict[str, Any]]:
        return _thaw(self.intents), _thaw(self.acks)


def prepare_completion_proof(
    intents: Mapping[str, Any], acks: Mapping[str, Any], *, ordinal: int,
) -> CompletionProof:
    """Control-plane validation/copy, never on realtime submit path."""
    verify_dispatch_cursor(intents, acks)
    rows = intents["intents"]
    if type(ordinal) is not int or ordinal < 0 or ordinal >= len(rows):
        raise ValueError("signal work completion ordinal is invalid")
    row = rows[ordinal]
    return CompletionProof._from_validated(intents, acks, ordinal, row)


class CompletionStorage(Protocol):
    def insert_completion_row(self, row: Mapping[str, Any]) -> None: ...
    def read_completion_rows(self, *, session_key: str, source_batch_sequence: int,
                             ordinal: int) -> list[Mapping[str, Any]]: ...


class CompletionKeeper(Protocol):
    def acquire_completion_claim(self, resource: str, *, owner_id: str) -> Mapping[str, Any] | None: ...
    def completion_claim_is_current(self, resource: str, *, owner_id: str, epoch: int) -> bool: ...
    def attest_completion(self, resource: str, *, owner_id: str, epoch: int,
                          content_hash: str) -> None: ...
    def completion_proof_matches(self, resource: str, *, owner_id: str, epoch: int,
                                 content_hash: str) -> bool: ...
    def completion_proof_exists(self, resource: str) -> bool: ...
    def release_completion_claim(self, resource: str, *, owner_id: str, epoch: int) -> bool: ...


def _hash(row: Mapping[str, Any]) -> str:
    return sha256(canonical_json(row).encode()).hexdigest()


def _time(value: Any) -> str:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(
        str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("signal work completion time must be timezone-aware")
    return parsed.astimezone(UTC).isoformat(timespec="microseconds")


def _hex(value: Any) -> str:
    if (not isinstance(value, str) or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        raise ValueError("signal work completion proof must be SHA-256")
    return value


def project_completion(
    intents: Mapping[str, Any], acks: Mapping[str, Any], *,
    ordinal: int, processed_at: Any, keeper_owner_id: str, keeper_epoch: int,
) -> CompletionProjection:
    """Require a committed dispatch ACK and its exact activation receipt."""
    verify_dispatch_cursor(intents, acks)
    rows = intents["intents"]
    if type(ordinal) is not int or ordinal < 0 or ordinal >= len(rows):
        raise ValueError("signal work completion ordinal is invalid")
    intent, ack = rows[ordinal], acks["acks"][ordinal]
    if (not isinstance(keeper_owner_id, str) or not keeper_owner_id
            or any(char in keeper_owner_id for char in ("\r", "\n", "\x00"))
            or type(keeper_epoch) is not int or keeper_epoch < 1):
        raise ValueError("signal work completion Keeper ownership is invalid")
    base = dict(
        schema_version=1, session_key=intent["session_key"],
        source_batch_sequence=intent["source_batch_sequence"], ordinal=ordinal,
        delivery_id=intent["delivery_id"],
        intent_content_hash=_hex(intent["content_hash"]),
        dispatch_ack_commit_hash=_hex(acks["commit"]["content_hash"]),
        activation_receipt_hash=_hex(ack["activation_receipt_hash"]),
        keeper_owner_id=keeper_owner_id, keeper_epoch=keeper_epoch,
        processed_at=_time(processed_at), outcome="completed",
    )
    return CompletionProjection({**base, "content_hash": _hash(base)})


def read_exact_completion(
    storage: CompletionStorage, intents: Mapping[str, Any],
    acks: Mapping[str, Any], *, ordinal: int, keeper: CompletionKeeper,
) -> CompletionProjection | None:
    """Cold verifier: zero rows is uncertain, duplicates/corruption fail closed."""
    verify_dispatch_cursor(intents, acks)
    rows = intents["intents"]
    if type(ordinal) is not int or ordinal < 0 or ordinal >= len(rows):
        raise ValueError("signal work completion ordinal is invalid")
    intent = rows[ordinal]
    found = storage.read_completion_rows(
        session_key=intent["session_key"],
        source_batch_sequence=intent["source_batch_sequence"], ordinal=ordinal)
    if not found:
        return None
    if len(found) != 1:
        raise ValueError("duplicate signal work completion receipt")
    row = canonical_row(COMPLETION, found[0])
    expected = project_completion(intents, acks, ordinal=ordinal,
                                  processed_at=row["processed_at"],
                                  keeper_owner_id=row["keeper_owner_id"],
                                  keeper_epoch=row["keeper_epoch"])
    if row != expected.row:
        raise ValueError("signal work completion receipt differs from dispatch ACK")
    resource = completion_resource(row["session_key"], row["source_batch_sequence"],
                                   row["ordinal"], row["delivery_id"])
    if not keeper.completion_proof_matches(
            resource, owner_id=row["keeper_owner_id"],
            epoch=row["keeper_epoch"], content_hash=row["content_hash"]):
        raise ValueError("signal work completion lacks Keeper attestation")
    return expected


class _Receipt(Future[CompletionProjection]):
    def cancel(self) -> bool:
        return False


class CompletionPublicationQueue:
    """Bounded independent worker; ambiguous INSERT is terminal, never retried."""

    def __init__(self, storage: CompletionStorage, keeper: CompletionKeeper, *,
                 owner_id: str, capacity: int = 128) -> None:
        if type(capacity) is not int or capacity < 1:
            raise ValueError("completion publication capacity is invalid")
        self._storage = storage
        self._keeper = keeper
        if not isinstance(owner_id, str) or not owner_id or any(
                char in owner_id for char in ("\r", "\n", "\x00")):
            raise ValueError("completion publisher Keeper owner is invalid")
        self._owner_id = owner_id
        self._capacity = capacity
        self._queue: queue.Queue[tuple[CompletionProof, Any, Future[CompletionProjection]]] = queue.Queue()
        self._pending: set[tuple[str, int, int]] = set()
        self._lock = threading.Lock()
        self._fatal: BaseException | None = None
        self._closing = False
        self._thread = threading.Thread(target=self._run, name="signal-work-completion", daemon=True)
        self._started = False

    def submit(
        self, proof: CompletionProof, *, processed_at: Any,
    ) -> Future[CompletionProjection]:
        """O(1) handoff of recursively immutable, prevalidated proof."""
        if not isinstance(proof, CompletionProof):
            raise TypeError("prevalidated completion proof is required")
        key = (proof.session_key, proof.source_batch_sequence, proof.ordinal)
        with self._lock:
            if self._closing or self._fatal is not None:
                raise RuntimeError("signal work completion publication is unavailable")
            if key in self._pending:
                raise ValueError("signal work completion is already pending")
            if len(self._pending) >= self._capacity:
                raise RuntimeError("signal work completion publication capacity is exhausted")
            receipt: Future[CompletionProjection] = _Receipt()
            self._pending.add(key)
            self._queue.put_nowait((proof, processed_at, receipt))
            if not self._started:
                self._thread.start()
                self._started = True
            return receipt

    def close(self, *, timeout: float = 10.0) -> None:
        with self._lock:
            self._closing = True
        if self._started:
            self._thread.join(timeout)
            if self._thread.is_alive():
                raise RuntimeError("signal work completion worker did not drain")

    def _run(self) -> None:
        while True:
            try:
                proof, processed_at, receipt = self._queue.get(timeout=0.1)
            except queue.Empty:
                with self._lock:
                    if self._closing:
                        return
                continue
            key = (proof.session_key, proof.source_batch_sequence, proof.ordinal)
            try:
                with self._lock:
                    failure = self._fatal
                if failure is not None:
                    raise RuntimeError("signal work completion publication halted") from failure
                frozen_intents, frozen_acks = proof.materialize()
                resource = completion_resource(
                    proof.session_key, proof.source_batch_sequence,
                    proof.ordinal, proof.delivery_id)
                lease = self._keeper.acquire_completion_claim(
                    resource, owner_id=self._owner_id)
                if (lease is None or lease.get("resource_id") != resource
                        or lease.get("owner_id") != self._owner_id
                        or type(lease.get("epoch")) is not int or lease["epoch"] < 1):
                    raise RuntimeError("completion Keeper claim is contended or invalid")
                epoch = lease["epoch"]
                prior = read_exact_completion(
                    self._storage, frozen_intents, frozen_acks,
                    ordinal=proof.ordinal, keeper=self._keeper)
                if prior is not None:
                    projected = prior
                else:
                    if self._keeper.completion_proof_exists(resource):
                        raise RuntimeError("completion Keeper proof exists without CH row")
                    projected = project_completion(
                        frozen_intents, frozen_acks, ordinal=proof.ordinal,
                        processed_at=processed_at, keeper_owner_id=self._owner_id,
                        keeper_epoch=epoch)
                    if not self._keeper.completion_claim_is_current(
                            resource, owner_id=self._owner_id, epoch=epoch):
                        raise RuntimeError("completion Keeper claim was lost before INSERT")
                    self._storage.insert_completion_row(projected.row)
                    # Do not use attested cold read until the Keeper proof exists.
                    found = self._storage.read_completion_rows(
                        session_key=proof.session_key,
                        source_batch_sequence=proof.source_batch_sequence,
                        ordinal=proof.ordinal)
                    if len(found) != 1 or canonical_row(COMPLETION, found[0]) != projected.row:
                        raise ValueError("signal work completion cold readback differs")
                    self._keeper.attest_completion(
                        resource, owner_id=self._owner_id, epoch=epoch,
                        content_hash=projected.row["content_hash"])
                    confirmed = read_exact_completion(
                        self._storage, frozen_intents, frozen_acks,
                        ordinal=proof.ordinal, keeper=self._keeper)
                    if confirmed != projected:
                        raise RuntimeError("completion attested readback differs")
                if not self._keeper.release_completion_claim(
                        resource, owner_id=self._owner_id, epoch=epoch):
                    raise RuntimeError("completion Keeper release failed")
            except BaseException as exc:
                with self._lock:
                    self._fatal = exc
                    self._pending.discard(key)
                try:
                    receipt.set_exception(exc)
                except InvalidStateError:
                    pass
            else:
                with self._lock:
                    self._pending.discard(key)
                try:
                    receipt.set_result(projected)
                except InvalidStateError:
                    pass
            finally:
                self._queue.task_done()
