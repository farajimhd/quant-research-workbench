"""Persistent single-use Keeper claim for a typed assignment-state snapshot.

The active assignment owner holder and epoch are checked in each claim CAS.
A started claim is never reused after ambiguous ClickHouse or Keeper results.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Mapping
from uuid import UUID

from src.backend.live_assignment_base_keeper import KeeperAssignmentHead
from src.trading_runtime.journal_contract import canonical_json


ROOT = "/trading/live-assignment-state-claim/v1"
_KEYS = frozenset({"run_id", "assignment_id", "revision", "snapshot_id", "session"})


@dataclass(frozen=True)
class StateClaim:
    owner_id: str
    owner_epoch: int
    state: str
    content_hash: str | None


def _identity(identity: Mapping[str, Any]) -> str:
    if not isinstance(identity, Mapping) or set(identity) != _KEYS:
        raise ValueError("state claim identity is incomplete")
    if (type(identity["run_id"]) is not str or not identity["run_id"]
            or type(identity["assignment_id"]) is not str or not identity["assignment_id"]
            or type(identity["revision"]) is not int or identity["revision"] < 1):
        raise ValueError("state claim identity is invalid")
    from datetime import date
    try:
        if str(UUID(identity["snapshot_id"])) != identity["snapshot_id"]:
            raise ValueError
        if date.fromisoformat(identity["session"]).isoformat() != identity["session"]:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError("state claim snapshot identity is invalid") from exc
    return canonical_json(dict(identity))


def _decode(raw: bytes) -> StateClaim:
    try:
        version, owner, epoch_raw, state, digest = raw.decode().split("\n")
        epoch = int(epoch_raw)
    except (UnicodeError, ValueError) as exc:
        raise ValueError("state Keeper claim is corrupt") from exc
    if (version != "1" or not owner or any(c in owner for c in ("\r", "\x00"))
            or epoch < 1 or str(epoch) != epoch_raw
            or state not in {"started", "committed"}
            or state == "started" and digest != "-"
            or state == "committed" and (len(digest) != 64 or
                any(c not in "0123456789abcdef" for c in digest))):
        raise ValueError("state Keeper claim has an unknown contract")
    return StateClaim(owner, epoch, state, None if digest == "-" else digest)


def _encode(owner: str, epoch: int, state: str, digest: str | None) -> bytes:
    return f"1\n{owner}\n{epoch}\n{state}\n{digest or '-'}".encode()


class KeeperStateSnapshotAdmission:
    def __init__(self, keeper: KeeperAssignmentHead, *, assignment_id: str,
                 owner_id: str, owner_epoch: int) -> None:
        if (not isinstance(keeper, KeeperAssignmentHead)
                or type(owner_id) is not str or not owner_id
                or any(c in owner_id for c in ("\r", "\n", "\x00"))
                or type(owner_epoch) is not int or owner_epoch < 1):
            raise ValueError("state Keeper owner is invalid")
        keeper.path(assignment_id)
        self._keeper = keeper
        self._client = keeper._client
        self._assignment_id = assignment_id
        self._owner_id = owner_id
        self._owner_epoch = owner_epoch
        keeper._connected()
        self._client.ensure_path(ROOT)

    def _path(self, identity: Mapping[str, Any]) -> str:
        if identity.get("assignment_id") != self._assignment_id:
            raise ValueError("state claim assignment differs from owner")
        return f"{ROOT}/{sha256(_identity(identity).encode()).hexdigest()}"

    def _fence(self) -> tuple[str, int, str, int]:
        self._keeper._connected()
        if not self._keeper.is_current(
                self._assignment_id, owner_id=self._owner_id,
                epoch=self._owner_epoch):
            raise RuntimeError("state Keeper owner fence lost")
        base = self._keeper.path(self._assignment_id)
        epoch_path, holder_path = f"{base}/epoch", f"{base}/holder"
        epoch_raw, epoch_stat = self._client.get(epoch_path)
        holder_raw, holder_stat = self._client.get(holder_path)
        if (epoch_raw != str(self._owner_epoch).encode()
                or holder_raw != f"1\n{self._owner_id}\n{self._owner_epoch}".encode()
                or holder_stat.ephemeralOwner != self._client.client_id[0]):
            raise RuntimeError("state Keeper holder or epoch changed")
        return epoch_path, epoch_stat.version, holder_path, holder_stat.version

    def begin_once(self, identity: Mapping[str, Any]) -> bool:
        path = self._path(identity)
        epoch_path, epoch_version, holder_path, holder_version = self._fence()
        txn = self._client.transaction()
        txn.check(epoch_path, version=epoch_version)
        txn.check(holder_path, version=holder_version)
        txn.create(path, _encode(self._owner_id, self._owner_epoch, "started", None))
        try:
            result = txn.commit()
        except BaseException as exc:
            raise RuntimeError("state Keeper claim creation is ambiguous") from exc
        errors = [item for item in result if isinstance(item, BaseException)]
        if errors:
            if any(type(item).__name__ == "NodeExistsError" for item in errors):
                return False
            raise RuntimeError("state Keeper owner CAS changed during claim")
        self._fence()
        return True

    def assert_current(self, identity: Mapping[str, Any]) -> None:
        self._fence()
        claim = self.read_claim(identity)
        if (claim is None or claim.owner_id != self._owner_id
                or claim.owner_epoch != self._owner_epoch or claim.state != "started"):
            raise RuntimeError("state Keeper claim is not owned and pending")

    def read_claim(self, identity: Mapping[str, Any]) -> StateClaim | None:
        path = self._path(identity)
        self._keeper._connected()
        try:
            raw, _ = self._client.get(path)
        except Exception as exc:
            if type(exc).__name__ == "NoNodeError":
                return None
            raise RuntimeError("state Keeper claim read failed") from exc
        return _decode(raw)

    def mark_committed(self, identity: Mapping[str, Any], content_hash: str) -> None:
        if (type(content_hash) is not str or len(content_hash) != 64
                or any(c not in "0123456789abcdef" for c in content_hash)):
            raise ValueError("state commit hash is invalid")
        path = self._path(identity)
        epoch_path, epoch_version, holder_path, holder_version = self._fence()
        raw, stat = self._client.get(path)
        claim = _decode(raw)
        if (claim.owner_id != self._owner_id or claim.owner_epoch != self._owner_epoch
                or claim.state != "started"):
            raise RuntimeError("state Keeper claim owner or state changed")
        txn = self._client.transaction()
        txn.check(epoch_path, version=epoch_version)
        txn.check(holder_path, version=holder_version)
        txn.set_data(path, _encode(self._owner_id, self._owner_epoch,
                                   "committed", content_hash), version=stat.version)
        try:
            result = txn.commit()
        except BaseException as exc:
            raise RuntimeError("state Keeper claim mark is ambiguous") from exc
        if any(isinstance(item, BaseException) for item in result):
            raise RuntimeError("state Keeper claim mark CAS failed")
        self._fence()
