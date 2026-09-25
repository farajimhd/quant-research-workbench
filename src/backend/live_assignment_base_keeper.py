"""Inactive persistent Keeper CAS head for typed base-assignment revisions.

Head advancement is permitted only after the caller has exactly read back the
base row and both typed child commits. This module performs no CH writes.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Mapping, Protocol

from src.backend.live_assignment_base_revision import recover_base_revision
from src.backend.live_assignment_state_snapshot import recover_attested_assignment
from src.trading_runtime.keeper_session import ManagedKeeperSession
from src.trading_runtime.strategy_engine import StrategyAssignment


ROOT = "/trading/live-assignment-base-head/v1"


def _text(value: str) -> str:
    if (type(value) is not str or not value
            or any(char in value for char in ("\n", "\r", "\x00"))):
        raise ValueError("assignment Keeper identity is invalid")
    return value


def _hash(value: str) -> str:
    if (type(value) is not str or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        raise ValueError("assignment Keeper hash is invalid")
    return value


@dataclass(frozen=True)
class AssignmentHead:
    assignment_id: str
    sequence: int
    content_hash: str
    keeper_version: int


class BaseRowReader(Protocol):
    def read_base_rows(self, assignment_id: str) -> list[Mapping[str, Any]]: ...


class KeeperAssignmentHead:
    def __init__(self, session: ManagedKeeperSession) -> None:
        if not isinstance(session, ManagedKeeperSession):
            raise TypeError("assignment Keeper requires managed workstation session")
        self._session = session
        self._client = session.client

    @staticmethod
    def path(assignment_id: str) -> str:
        return f"{ROOT}/{sha256(_text(assignment_id).encode()).hexdigest()}"

    def _connected(self) -> None:
        if (not getattr(self._session, "_connected", False)
                or getattr(self._session, "_closed", True)
                or not getattr(self._client, "connected", False)
                or getattr(getattr(self._client, "client_state", None), "name", "CONNECTED")
                   == "CONNECTED_RO"
                or self._client.client_id is None):
            raise RuntimeError("assignment Keeper session is unavailable")

    def read_head(self, assignment_id: str) -> AssignmentHead:
        self._connected()
        try:
            raw, stat = self._client.get(f"{self.path(assignment_id)}/head")
            fields = raw.decode("utf-8").split("\n")
            if (len(fields) != 4 or fields[0] != "1" or fields[1] != assignment_id
                    or str(int(fields[2])) != fields[2] or int(fields[2]) < 1
                    or type(stat.version) is not int or stat.version < 0):
                raise ValueError
            return AssignmentHead(assignment_id, int(fields[2]),
                                  _hash(fields[3]), stat.version)
        except Exception as exc:
            raise ValueError("assignment Keeper head missing or corrupt") from exc

    def acquire(self, assignment_id: str, *, owner_id: str) -> int | None:
        self._connected()
        base, owner = self.path(assignment_id), _text(owner_id)
        self._client.ensure_path(base)
        try:
            self._client.create(f"{base}/epoch", b"0")
        except Exception as exc:
            if type(exc).__name__ != "NodeExistsError":
                raise RuntimeError("assignment Keeper epoch initialization failed") from exc
        for _ in range(8):
            self._connected()
            if self._client.exists(f"{base}/holder") is not None:
                return None
            raw, stat = self._client.get(f"{base}/epoch")
            try:
                previous_epoch = int(raw)
                if previous_epoch < 0 or str(previous_epoch).encode() != raw:
                    raise ValueError
            except ValueError as exc:
                raise ValueError("assignment Keeper epoch corrupt") from exc
            epoch = previous_epoch + 1
            txn = self._client.transaction()
            txn.set_data(f"{base}/epoch", str(epoch).encode(), version=stat.version)
            txn.create(f"{base}/holder", f"1\n{owner}\n{epoch}".encode(), ephemeral=True)
            errors = [item for item in txn.commit() if isinstance(item, BaseException)]
            if errors:
                if all(type(item).__name__ in {
                        "BadVersionError", "NodeExistsError", "RolledBackError"} for item in errors):
                    continue
                raise RuntimeError("assignment Keeper owner CAS failed")
            if not self.is_current(assignment_id, owner_id=owner, epoch=epoch):
                raise RuntimeError("assignment Keeper owner lost after acquisition")
            return epoch
        raise RuntimeError("assignment Keeper owner contention exceeded")

    def is_current(self, assignment_id: str, *, owner_id: str, epoch: int) -> bool:
        self._connected()
        try:
            raw, stat = self._client.get(f"{self.path(assignment_id)}/holder")
        except Exception as exc:
            if type(exc).__name__ == "NoNodeError":
                return False
            raise RuntimeError("assignment Keeper holder read failed") from exc
        return (raw == f"1\n{_text(owner_id)}\n{epoch}".encode()
                and stat.ephemeralOwner == self._client.client_id[0])

    def attest(self, row: Mapping[str, Any], *, owner_id: str, epoch: int,
               previous: AssignmentHead | None, base_rows: BaseRowReader,
               state_storage: Any, state_admission: Any,
               parameter_storage: Any,
               parameter_admission: Any) -> AssignmentHead:
        """CAS only after exact CH row and both attested child snapshots read back."""
        assignment_id, sequence = row["assignment_id"], row["revision_sequence"]
        base = self.path(assignment_id)
        if not self.is_current(assignment_id, owner_id=owner_id, epoch=epoch):
            raise RuntimeError("assignment Keeper owner fence lost")
        if sequence != (1 if previous is None else previous.sequence + 1):
            raise ValueError("assignment Keeper sequence differs")
        prior_hash = "0" * 64 if previous is None else previous.content_hash
        recover_base_revision(
            [row], expected_assignment_id=assignment_id, expected_sequence=sequence,
            expected_hash=row["content_hash"],
            parameter_content_hash=row["parameter_content_hash"],
            state_content_hash=row["state_content_hash"],
            previous_revision_hash=prior_hash)
        all_rows = base_rows.read_base_rows(assignment_id)
        if len(all_rows) != sequence:
            raise ValueError("assignment base readback has gap, duplicate, or orphan")
        by_sequence = {actual.get("revision_sequence"): actual for actual in all_rows}
        if len(by_sequence) != sequence or set(by_sequence) != set(range(1, sequence + 1)):
            raise ValueError("assignment base readback has gap, duplicate, or orphan")
        actual_prior = "0" * 64
        for index in range(1, sequence + 1):
            actual = by_sequence[index]
            recover_base_revision(
                [actual], expected_assignment_id=assignment_id,
                expected_sequence=index, expected_hash=actual["content_hash"],
                parameter_content_hash=actual["parameter_content_hash"],
                state_content_hash=actual["state_content_hash"],
                previous_revision_hash=actual_prior)
            actual_prior = actual["content_hash"]
        if actual_prior != row["content_hash"] or dict(by_sequence[sequence]) != dict(row):
            raise ValueError("assignment base readback differs")
        if previous is not None and by_sequence[sequence - 1]["content_hash"] != previous.content_hash:
            raise ValueError("assignment base prior differs from Keeper head")
        recover_attested_assignment(
            base_rows=[by_sequence[sequence]], state_storage=state_storage,
            state_admission=state_admission,
            parameter_storage=parameter_storage,
            parameter_admission=parameter_admission, assignment_id=assignment_id,
            revision_sequence=sequence, expected_base_hash=row["content_hash"],
            previous_revision_hash=prior_hash)
        epoch_raw, epoch_stat = self._client.get(f"{base}/epoch")
        holder_raw, holder_stat = self._client.get(f"{base}/holder")
        if (epoch_raw != str(epoch).encode()
                or holder_raw != f"1\n{owner_id}\n{epoch}".encode()
                or holder_stat.ephemeralOwner != self._client.client_id[0]):
            raise RuntimeError("assignment Keeper owner epoch changed")
        raw = f"1\n{assignment_id}\n{sequence}\n{row['content_hash']}".encode()
        txn = self._client.transaction()
        txn.check(f"{base}/epoch", version=epoch_stat.version)
        txn.check(f"{base}/holder", version=holder_stat.version)
        if previous is None:
            if self._client.exists(f"{base}/head") is not None:
                raise RuntimeError("assignment Keeper head already exists")
            txn.create(f"{base}/head", raw)
        else:
            if self.read_head(assignment_id) != previous:
                raise RuntimeError("assignment Keeper head changed")
            txn.set_data(f"{base}/head", raw, version=previous.keeper_version)
        if any(isinstance(item, BaseException) for item in txn.commit()):
            raise RuntimeError("assignment Keeper head CAS failed")
        confirmed = self.read_head(assignment_id)
        if confirmed.sequence != sequence or confirmed.content_hash != row["content_hash"]:
            raise RuntimeError("assignment Keeper head readback differs")
        return confirmed

    def release(self, assignment_id: str, *, owner_id: str, epoch: int) -> bool:
        if not self.is_current(assignment_id, owner_id=owner_id, epoch=epoch):
            return False
        path = f"{self.path(assignment_id)}/holder"
        _, stat = self._client.get(path)
        try:
            self._client.delete(path, version=stat.version)
        except Exception as exc:
            if type(exc).__name__ in {"NoNodeError", "BadVersionError"}:
                return False
            raise RuntimeError("assignment Keeper holder release failed") from exc
        return True


def cold_read_attested_assignment(
    rows: BaseRowReader, keeper: KeeperAssignmentHead, *,
    assignment_id: str, state_storage: Any, parameter_storage: Any,
    parameter_admission: Any, state_admission: Any,
) -> StrategyAssignment:
    """Verify the entire base chain and stable head before returning latest state."""
    first = keeper.read_head(assignment_id)
    all_rows = rows.read_base_rows(assignment_id)
    if len(all_rows) != first.sequence:
        raise ValueError("assignment base chain has a gap or duplicate")
    by_sequence = {row.get("revision_sequence"): row for row in all_rows}
    if len(by_sequence) != first.sequence:
        raise ValueError("assignment base chain has duplicate revisions")
    prior_hash = "0" * 64
    for sequence in range(1, first.sequence + 1):
        row = by_sequence.get(sequence)
        if row is None:
            raise ValueError("assignment base chain has a gap")
        recover_base_revision(
            [row], expected_assignment_id=assignment_id, expected_sequence=sequence,
            expected_hash=row["content_hash"],
            parameter_content_hash=row["parameter_content_hash"],
            state_content_hash=row["state_content_hash"],
            previous_revision_hash=prior_hash)
        prior_hash = row["content_hash"]
    if prior_hash != first.content_hash:
        raise ValueError("assignment base chain differs from Keeper head")
    recovered = recover_attested_assignment(
        base_rows=[by_sequence[first.sequence]], state_storage=state_storage,
        state_admission=state_admission,
        parameter_storage=parameter_storage, parameter_admission=parameter_admission,
        assignment_id=assignment_id, revision_sequence=first.sequence,
        expected_base_hash=first.content_hash,
        previous_revision_hash=("0" * 64 if first.sequence == 1 else
                                by_sequence[first.sequence - 1]["content_hash"]),
    )
    if keeper.read_head(assignment_id) != first:
        raise RuntimeError("assignment Keeper head changed during cold read")
    return recovered
