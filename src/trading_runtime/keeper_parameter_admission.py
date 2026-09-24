"""Persistent single-use Keeper admission for staged typed parameter snapshots.

This control-plane adapter does not bootstrap Keeper or start any live writer.
The injected client must already be connected to operator-approved local Keeper.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Mapping

from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.keeper_ownership import KeeperUnavailable, _decode as _decode_owner, _path


_ROOT = "/trading/assignment_parameter_claims/v1"
_IDENTITY_KEYS = frozenset({
    "assignment_id", "strategy_id", "strategy_revision", "snapshot_id", "session",
})
_EMPTY_HASH = "-"


@dataclass(frozen=True, slots=True)
class ParameterClaim:
    owner_id: str
    owner_epoch: int
    state: str
    content_hash: str | None


def _identity(identity: Mapping[str, Any]) -> str:
    if not isinstance(identity, Mapping) or set(identity) != _IDENTITY_KEYS:
        raise ValueError("parameter claim identity is incomplete")
    for key in ("assignment_id", "strategy_id", "snapshot_id", "session"):
        if not isinstance(identity[key], str) or not identity[key]:
            raise ValueError(f"parameter claim {key} is invalid")
    if type(identity["strategy_revision"]) is not int or identity["strategy_revision"] < 1:
        raise ValueError("parameter claim strategy revision is invalid")
    return canonical_json(dict(identity))


def _encode(owner_id: str, owner_epoch: int, state: str, content_hash: str | None) -> bytes:
    return f"1\n{owner_id}\n{owner_epoch}\n{state}\n{content_hash or _EMPTY_HASH}".encode("utf-8")


def _decode(value: bytes) -> ParameterClaim:
    try:
        version, owner, raw_epoch, state, digest = value.decode("utf-8").split("\n")
        epoch = int(raw_epoch)
    except (UnicodeError, ValueError) as exc:
        raise KeeperUnavailable("parameter claim is corrupt") from exc
    if (version != "1" or not owner or "\x00" in owner or epoch < 1
            or state not in {"started", "committed"}
            or (state == "started" and digest != _EMPTY_HASH)
            or (state == "committed" and (len(digest) != 64 or
                any(ch not in "0123456789abcdef" for ch in digest)))):
        raise KeeperUnavailable("parameter claim has an unknown contract")
    return ParameterClaim(owner, epoch, state, None if digest == _EMPTY_HASH else digest)


class KeeperParameterSnapshotAdmission:
    """One persistent claim per assignment/revision/snapshot, never released.

    A failed or ambiguous child publication consumes the claim. Operators may
    inspect the claim and typed ClickHouse commit, but no automatic retry is
    authorized. `owner_epoch` must be supplied by an external fenced owner.
    """

    def __init__(self, client: Any, coordinator: Any, *, resource_id: str,
                 owner_id: str, owner_epoch: int) -> None:
        if (not isinstance(owner_id, str) or not owner_id or "\n" in owner_id
                or "\r" in owner_id or "\x00" in owner_id
                or not isinstance(resource_id, str) or not resource_id
                or type(owner_epoch) is not int or owner_epoch < 1):
            raise ValueError("parameter admission owner/epoch is invalid")
        self._client = client
        self._coordinator = coordinator
        self._resource_id = resource_id
        self._owner_id = owner_id
        self._owner_epoch = owner_epoch
        self._require_connected()
        try:
            client.ensure_path(_ROOT)
        except Exception as exc:
            raise KeeperUnavailable("could not initialize parameter claim path") from exc

    def _require_connected(self) -> None:
        state = getattr(self._client, "client_state", None)
        if (not getattr(self._client, "connected", False)
                or getattr(state, "name", str(state)) == "CONNECTED_RO"):
            raise KeeperUnavailable("Keeper parameter admission session is not writable")

    def _path(self, identity: Mapping[str, Any]) -> str:
        return f"{_ROOT}/{sha256(_identity(identity).encode()).hexdigest()}"

    def _attestation(self) -> tuple[str, int, str, int]:
        """Read current holder/counter; transactions must check both versions."""
        self._require_connected()
        if not self._coordinator.portfolio_admission_lease_is_current(
            self._resource_id, owner_id=self._owner_id, epoch=self._owner_epoch,
        ):
            raise KeeperUnavailable("parameter owner lease is not current")
        base = _path("portfolio", self._resource_id)
        holder_path, counter_path = f"{base}/holder", f"{base}/epoch"
        try:
            holder, holder_stat = self._client.get(holder_path)
            counter, counter_stat = self._client.get(counter_path)
            session = self._client.client_id
            valid = (_decode_owner(holder) == (self._owner_id, self._owner_epoch, "portfolio")
                     and int(counter) == self._owner_epoch
                     and session is not None and holder_stat.ephemeralOwner == session[0])
        except Exception as exc:
            raise KeeperUnavailable("parameter owner attestation could not be read") from exc
        if not valid:
            raise KeeperUnavailable("parameter owner holder/epoch is stale")
        self._require_connected()
        return holder_path, holder_stat.version, counter_path, counter_stat.version

    @staticmethod
    def _transaction_result(results: Any) -> bool:
        if not isinstance(results, list):
            raise KeeperUnavailable("parameter claim transaction result is invalid")
        errors = [item for item in results if isinstance(item, BaseException)]
        if not errors:
            return True
        if any(type(item).__name__ == "NodeExistsError" for item in errors):
            return False
        raise KeeperUnavailable("parameter owner fence changed during transaction") from errors[0]

    def begin_once(self, identity: Mapping[str, Any]) -> bool:
        """Atomically create a persistent claim; never adopt an existing one."""
        path = self._path(identity)
        holder_path, holder_version, counter_path, counter_version = self._attestation()
        try:
            txn = self._client.transaction()
            txn.check(holder_path, version=holder_version)
            txn.check(counter_path, version=counter_version)
            txn.create(path, _encode(self._owner_id, self._owner_epoch, "started", None))
            created = self._transaction_result(txn.commit())
        except Exception as exc:
            # An uncertain create may have succeeded. Fail closed; a later
            # attempt sees the persistent node and cannot publish children.
            raise KeeperUnavailable("parameter claim create failed or is ambiguous") from exc
        if not created:
            return False
        self._attestation()
        return True

    def assert_current(self, identity: Mapping[str, Any]) -> None:
        """Check the active external lease before another child/commit INSERT."""
        path = self._path(identity)
        self._attestation()
        claim = self.read_claim(identity)
        if (claim is None or claim.owner_id != self._owner_id
                or claim.owner_epoch != self._owner_epoch or claim.state != "started"):
            raise KeeperUnavailable(f"parameter claim {path} is not owned and pending")

    def read_claim(self, identity: Mapping[str, Any]) -> ParameterClaim | None:
        """Read-only reconciliation; never reopens a started claim."""
        path = self._path(identity)
        self._require_connected()
        try:
            value, _stat = self._client.get(path)
        except Exception as exc:
            if type(exc).__name__ == "NoNodeError":
                return None
            raise KeeperUnavailable("could not read parameter claim") from exc
        self._require_connected()
        return _decode(value)

    def diagnose_commit(self, storage: Any, identity: Mapping[str, Any]) -> dict[str, Any] | None:
        """Read-only diagnostic; result is not trading recovery authority.

        A `started` claim with a committed CH fence is observable after an
        ambiguous Keeper mark, but this method never changes claim state or
        authorizes another publication attempt.
        """
        if self.read_claim(identity) is None:
            return None
        from src.trading_runtime.arte_long_momentum_parameter_journal import (
            COMMIT_TABLE, load_diagnostic_parameters,
        )

        parameters = load_diagnostic_parameters(storage, **dict(identity))
        claim = self.read_claim(identity)
        if claim is None:
            raise KeeperUnavailable("parameter claim disappeared during reconciliation")
        if claim.state == "committed":
            commits = storage.read(COMMIT_TABLE.name, identity)
            if (parameters is None or len(commits) != 1
                    or commits[0]["content_hash"] != claim.content_hash):
                raise KeeperUnavailable("Keeper committed claim differs from typed journal fence")
        return parameters

    def mark_committed(self, identity: Mapping[str, Any], content_hash: str) -> None:
        """CAS the same owner/epoch to committed after typed cold verification."""
        if (not isinstance(content_hash, str) or len(content_hash) != 64
                or any(ch not in "0123456789abcdef" for ch in content_hash)):
            raise ValueError("parameter commit hash is invalid")
        path = self._path(identity)
        holder_path, holder_version, counter_path, counter_version = self._attestation()
        try:
            value, stat = self._client.get(path)
            claim = _decode(value)
            if (claim.owner_id != self._owner_id or claim.owner_epoch != self._owner_epoch
                    or claim.state != "started"):
                raise KeeperUnavailable("parameter claim owner/epoch/state changed")
            txn = self._client.transaction()
            txn.check(holder_path, version=holder_version)
            txn.check(counter_path, version=counter_version)
            txn.set_data(path, _encode(self._owner_id, self._owner_epoch,
                                       "committed", content_hash), version=stat.version)
            if not self._transaction_result(txn.commit()):
                raise KeeperUnavailable("parameter claim commit transaction failed")
        except KeeperUnavailable:
            raise
        except Exception as exc:
            raise KeeperUnavailable("parameter claim commit CAS failed or is ambiguous") from exc
        self._attestation()
