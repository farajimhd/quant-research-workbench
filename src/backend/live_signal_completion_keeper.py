"""Persistent Keeper fence for staged live signal-work completion receipts.

Uses a connected Kazoo-compatible client supplied by operator bootstrap. No
client is created, no ACL is weakened, and no network call occurs at import.
"""
from __future__ import annotations

from hashlib import sha256
from typing import Any, Mapping


ROOT = "/trading/live-signal-completion/v1"


def completion_resource(session_key: str, sequence: int, ordinal: int,
                        delivery_id: str) -> str:
    if (not isinstance(session_key, str) or not session_key
            or type(sequence) is not int or sequence < 1
            or type(ordinal) is not int or ordinal < 0
            or not isinstance(delivery_id, str) or not delivery_id):
        raise ValueError("completion Keeper resource identity is invalid")
    return sha256(f"{session_key}\x00{sequence}\x00{ordinal}\x00{delivery_id}".encode()).hexdigest()


def _owner(value: str) -> str:
    if (not isinstance(value, str) or not value
            or any(char in value for char in ("\n", "\r", "\x00"))):
        raise ValueError("completion Keeper owner identity is invalid")
    return value


def _proof_bytes(resource: str, content_hash: str, owner_id: str, epoch: int) -> bytes:
    if (len(resource) != 64 or len(content_hash) != 64
            or any(char not in "0123456789abcdef" for char in resource + content_hash)
            or type(epoch) is not int or epoch < 1):
        raise ValueError("completion Keeper proof identity is invalid")
    return f"1\n{resource}\n{content_hash}\n{_owner(owner_id)}\n{epoch}".encode()


class CompletionKeeperFence:
    """CAS persistent epoch and holder; attest only the current owner epoch."""

    def __init__(self, client: Any, *, endpoint: str) -> None:
        if endpoint != "127.0.0.1:9181":
            raise ValueError("live completion Keeper requires local loopback endpoint")
        self._client = client

    def _connected(self) -> None:
        if not getattr(self._client, "connected", False) or self._client.client_id is None:
            raise RuntimeError("completion Keeper session is unavailable")

    @staticmethod
    def _base(resource: str) -> str:
        if len(resource) != 64 or any(char not in "0123456789abcdef" for char in resource):
            raise ValueError("completion Keeper resource hash is invalid")
        return f"{ROOT}/{resource}"

    def acquire_completion_claim(self, resource: str, *, owner_id: str) -> Mapping[str, Any] | None:
        self._connected()
        owner = _owner(owner_id)
        base = self._base(resource)
        self._client.ensure_path(base)
        try:
            self._client.create(f"{base}/epoch", b"0")
        except Exception as exc:
            if type(exc).__name__ != "NodeExistsError":
                raise RuntimeError("completion Keeper epoch initialization failed") from exc
        for _ in range(8):
            self._connected()
            if self._client.exists(f"{base}/holder") is not None:
                return None
            raw, stat = self._client.get(f"{base}/epoch")
            try:
                epoch = int(raw) + 1
            except ValueError as exc:
                raise RuntimeError("completion Keeper epoch is corrupt") from exc
            if epoch < 1:
                raise RuntimeError("completion Keeper epoch is invalid")
            txn = self._client.transaction()
            txn.set_data(f"{base}/epoch", str(epoch).encode(), version=stat.version)
            txn.create(f"{base}/holder", f"1\n{owner}\n{epoch}".encode(), ephemeral=True)
            result = txn.commit()
            if any(isinstance(item, BaseException) for item in result):
                errors = [item for item in result if isinstance(item, BaseException)]
                if (any(type(item).__name__ in {"BadVersionError", "NodeExistsError"}
                        for item in errors)
                        and all(type(item).__name__ in {"BadVersionError", "NodeExistsError",
                                                          "RolledBackError"} for item in errors)):
                    continue
                raise RuntimeError("completion Keeper acquisition transaction failed")
            if not self.completion_claim_is_current(resource, owner_id=owner, epoch=epoch):
                raise RuntimeError("completion Keeper claim was lost after acquisition")
            return {"resource_id": resource, "owner_id": owner, "epoch": epoch}
        raise RuntimeError("completion Keeper claim contention exceeded retry budget")

    def completion_claim_is_current(self, resource: str, *, owner_id: str, epoch: int) -> bool:
        self._connected()
        base = self._base(resource)
        try:
            holder, stat = self._client.get(f"{base}/holder")
        except Exception as exc:
            if type(exc).__name__ == "NoNodeError":
                return False
            raise RuntimeError("completion Keeper holder read failed") from exc
        self._connected()
        return (holder == f"1\n{_owner(owner_id)}\n{epoch}".encode()
                and stat.ephemeralOwner == self._client.client_id[0])

    def attest_completion(self, resource: str, *, owner_id: str,
                          epoch: int, content_hash: str) -> None:
        self._connected()
        base = self._base(resource)
        if not self.completion_claim_is_current(resource, owner_id=owner_id, epoch=epoch):
            raise RuntimeError("completion Keeper owner fence was lost")
        epoch_bytes, epoch_stat = self._client.get(f"{base}/epoch")
        holder_bytes, holder_stat = self._client.get(f"{base}/holder")
        if (epoch_bytes != str(epoch).encode()
                or holder_bytes != f"1\n{_owner(owner_id)}\n{epoch}".encode()
                or holder_stat.ephemeralOwner != self._client.client_id[0]):
            raise RuntimeError("completion Keeper owner epoch changed")
        proof = _proof_bytes(resource, content_hash, owner_id, epoch)
        txn = self._client.transaction()
        txn.check(f"{base}/epoch", version=epoch_stat.version)
        txn.check(f"{base}/holder", version=holder_stat.version)
        txn.create(f"{base}/proof", proof)
        result = txn.commit()
        if any(isinstance(item, BaseException) for item in result):
            raise RuntimeError("completion Keeper attestation CAS failed")
        if not self.completion_proof_matches(resource, content_hash=content_hash,
                                             owner_id=owner_id, epoch=epoch):
            raise RuntimeError("completion Keeper proof readback differs")

    def completion_proof_matches(self, resource: str, *, content_hash: str,
                                 owner_id: str, epoch: int) -> bool:
        self._connected()
        try:
            proof, _ = self._client.get(f"{self._base(resource)}/proof")
        except Exception as exc:
            if type(exc).__name__ == "NoNodeError":
                return False
            raise RuntimeError("completion Keeper proof read failed") from exc
        return proof == _proof_bytes(resource, content_hash, owner_id, epoch)

    def completion_proof_exists(self, resource: str) -> bool:
        self._connected()
        return self._client.exists(f"{self._base(resource)}/proof") is not None

    def release_completion_claim(self, resource: str, *, owner_id: str, epoch: int) -> bool:
        if not self.completion_claim_is_current(resource, owner_id=owner_id, epoch=epoch):
            return False
        base = self._base(resource)
        value, stat = self._client.get(f"{base}/holder")
        if (value != f"1\n{_owner(owner_id)}\n{epoch}".encode()
                or stat.ephemeralOwner != self._client.client_id[0]):
            return False
        try:
            self._client.delete(f"{base}/holder", version=stat.version)
        except Exception as exc:
            if type(exc).__name__ in {"NoNodeError", "BadVersionError"}:
                return False
            raise RuntimeError("completion Keeper release failed") from exc
        return True
