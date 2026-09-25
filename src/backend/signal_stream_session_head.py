"""Staged Signal Stream session-head fence; not wired to the live publisher.

ClickHouse's typed cursor commits hold journal facts; Keeper holds only the
exclusive owner epoch and the attested head pointer.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from typing import Any

ROOT = "/trading/signal-stream-session/v1"
GENESIS_HASH = "0" * 64


def _identity(value: str, name: str, *, empty: bool = False) -> str:
    if (not isinstance(value, str) or (not value and not empty)
            or any(char in value for char in ("\n", "\r", "\x00"))):
        raise ValueError(f"invalid {name}")
    return value


def _session(value: str) -> str:
    try:
        if type(value) is not str or date.fromisoformat(value).isoformat() != value:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid Signal Stream session key") from exc
    return value


def _hash(value: str) -> str:
    if (type(value) is not str or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        raise ValueError("invalid Signal Stream cursor hash")
    return value


@dataclass(frozen=True)
class SessionHead:
    session_key: str
    batch_sequence: int
    cursor_commit_hash: str
    configuration_revision: str
    source_revision: str
    keeper_owner_id: str
    keeper_epoch: int
    keeper_version: int


def _head_bytes(sequence: int, cursor_hash: str, configuration: str,
                source: str, owner: str, epoch: int) -> bytes:
    if (type(sequence) is not int or sequence < 0 or type(epoch) is not int
            or epoch < 0 or (sequence == 0) != (epoch == 0)):
        raise ValueError("invalid Signal Stream head sequence or epoch")
    _hash(cursor_hash)
    _identity(configuration, "configuration revision", empty=sequence == 0)
    _identity(source, "source revision", empty=sequence == 0)
    _identity(owner, "Keeper owner", empty=sequence == 0)
    if sequence == 0 and (cursor_hash != GENESIS_HASH or configuration or source or owner):
        raise ValueError("invalid Signal Stream genesis head")
    return f"1\n{sequence}\n{cursor_hash}\n{configuration}\n{source}\n{owner}\n{epoch}".encode()


class SignalSessionHeadKeeper:
    """Persistent CAS head and epoch plus ephemeral exclusive session holder."""

    def __init__(self, client: Any, *, endpoint: str) -> None:
        if endpoint != "127.0.0.1:9181":
            raise ValueError("Signal Stream Keeper requires local loopback endpoint")
        self._client = client

    def _connected(self) -> None:
        if not getattr(self._client, "connected", False) or self._client.client_id is None:
            raise RuntimeError("Signal Stream Keeper session is unavailable")

    def _base(self, session_key: str) -> str:
        return f"{ROOT}/{sha256(_session(session_key).encode()).hexdigest()}"

    def _initialize(self, session_key: str) -> str:
        self._connected()
        base = self._base(session_key)
        self._client.ensure_path(base)
        for path, value in (("epoch", b"0"),
                            ("head", _head_bytes(0, GENESIS_HASH, "", "", "", 0))):
            try:
                self._client.create(f"{base}/{path}", value)
            except Exception as exc:
                if type(exc).__name__ != "NodeExistsError":
                    raise RuntimeError("Signal Stream Keeper initialization failed") from exc
        return base

    def read_head(self, session_key: str) -> SessionHead:
        self._connected()
        base = self._base(session_key)
        raw, stat = self._client.get(f"{base}/head")
        try:
            version, sequence, digest, configuration, source, owner, epoch = raw.decode().split("\n")
            sequence, epoch = int(sequence), int(epoch)
            if version != "1" or raw != _head_bytes(
                    sequence, digest, configuration, source, owner, epoch):
                raise ValueError
        except (UnicodeError, ValueError) as exc:
            raise ValueError("Signal Stream Keeper head is corrupt") from exc
        return SessionHead(_session(session_key), sequence, digest, configuration,
                           source, owner, epoch, stat.version)

    def acquire(self, session_key: str, *, owner_id: str) -> int | None:
        owner = _identity(owner_id, "Keeper owner")
        base = self._initialize(session_key)
        for _ in range(8):
            self._connected()
            if self._client.exists(f"{base}/holder") is not None:
                return None
            raw, stat = self._client.get(f"{base}/epoch")
            try:
                epoch = int(raw) + 1
                if epoch < 1:
                    raise ValueError
            except ValueError as exc:
                raise ValueError("Signal Stream Keeper epoch is corrupt") from exc
            txn = self._client.transaction()
            txn.set_data(f"{base}/epoch", str(epoch).encode(), version=stat.version)
            txn.create(f"{base}/holder", f"1\n{owner}\n{epoch}".encode(), ephemeral=True)
            errors = [item for item in txn.commit() if isinstance(item, BaseException)]
            if errors:
                if all(type(item).__name__ in {
                        "BadVersionError", "NodeExistsError", "RolledBackError"} for item in errors):
                    continue
                raise RuntimeError("Signal Stream Keeper claim CAS failed")
            if not self.is_current(session_key, owner_id=owner, epoch=epoch):
                raise RuntimeError("Signal Stream Keeper claim lost after acquisition")
            return epoch
        raise RuntimeError("Signal Stream Keeper claim contention exceeded retry budget")

    def is_current(self, session_key: str, *, owner_id: str, epoch: int) -> bool:
        self._connected()
        try:
            raw, stat = self._client.get(f"{self._base(session_key)}/holder")
        except Exception as exc:
            if type(exc).__name__ == "NoNodeError":
                return False
            raise RuntimeError("Signal Stream Keeper holder read failed") from exc
        return (raw == f"1\n{_identity(owner_id, 'Keeper owner')}\n{epoch}".encode()
                and stat.ephemeralOwner == self._client.client_id[0])

    def attest_next(self, session_key: str, *, owner_id: str, epoch: int,
                    previous: SessionHead, cursor_commit_hash: str,
                    configuration_revision: str, source_revision: str) -> SessionHead:
        """CAS only the next durable head after external exact CH readback."""
        self._connected()
        base = self._base(session_key)
        if previous.session_key != session_key or not self.is_current(
                session_key, owner_id=owner_id, epoch=epoch):
            raise RuntimeError("Signal Stream Keeper owner fence was lost")
        head = self.read_head(session_key)
        if head != previous:
            raise RuntimeError("Signal Stream Keeper head changed")
        if head.batch_sequence and (head.configuration_revision != configuration_revision
                                    or head.source_revision != source_revision):
            raise ValueError("Signal Stream revision changed within session")
        epoch_raw, epoch_stat = self._client.get(f"{base}/epoch")
        holder_raw, holder_stat = self._client.get(f"{base}/holder")
        if (epoch_raw != str(epoch).encode()
                or holder_raw != f"1\n{owner_id}\n{epoch}".encode()
                or holder_stat.ephemeralOwner != self._client.client_id[0]):
            raise RuntimeError("Signal Stream Keeper owner epoch changed")
        next_raw = _head_bytes(head.batch_sequence + 1, cursor_commit_hash,
                               configuration_revision, source_revision, owner_id, epoch)
        txn = self._client.transaction()
        txn.check(f"{base}/epoch", version=epoch_stat.version)
        txn.check(f"{base}/holder", version=holder_stat.version)
        txn.set_data(f"{base}/head", next_raw, version=head.keeper_version)
        if any(isinstance(item, BaseException) for item in txn.commit()):
            raise RuntimeError("Signal Stream Keeper head attestation CAS failed")
        confirmed = self.read_head(session_key)
        if confirmed.batch_sequence != head.batch_sequence + 1 or confirmed.cursor_commit_hash != cursor_commit_hash:
            raise RuntimeError("Signal Stream Keeper head readback differs")
        return confirmed

    def release(self, session_key: str, *, owner_id: str, epoch: int) -> bool:
        if not self.is_current(session_key, owner_id=owner_id, epoch=epoch):
            return False
        path = f"{self._base(session_key)}/holder"
        raw, stat = self._client.get(path)
        if (raw != f"1\n{owner_id}\n{epoch}".encode()
                or stat.ephemeralOwner != self._client.client_id[0]):
            return False
        try:
            self._client.delete(path, version=stat.version)
        except Exception as exc:
            if type(exc).__name__ in {"NoNodeError", "BadVersionError"}:
                return False
            raise RuntimeError("Signal Stream Keeper release failed") from exc
        return True
