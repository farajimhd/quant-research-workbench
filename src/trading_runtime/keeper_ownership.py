"""Fenced ownership primitives for a ClickHouse Keeper (ZooKeeper) client.

This is a staged coordinator, not a journal or a live-runtime cutover.  A
connected client implementing the Kazoo API is injected by the operator-owned
bootstrap.  Journal transitions and recovery evidence still belong in typed
``arte`` tables; no market or journal table is modified here.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from threading import Lock
from typing import Any


_ROOT = "/trading/ownership/v1"
_MAX_RETRIES = 8


class KeeperUnavailable(RuntimeError):
    """Coordination is unhealthy; callers must reject new trading admission."""


def _identity(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or "\n" in value or "\x00" in value:
        raise ValueError(f"{label} must be a nonempty single-line string")
    return value


def _path(kind: str, *parts: str) -> str:
    key = "\x00".join(_identity(part, kind) for part in parts)
    return f"{_ROOT}/{kind}/{sha256(key.encode('utf-8')).hexdigest()}"


def _encode(owner_id: str, epoch: int, state: str) -> bytes:
    return f"1\n{_identity(owner_id, 'owner')}\n{epoch}\n{state}".encode("utf-8")


def _decode(value: bytes) -> tuple[str, int, str]:
    try:
        version, owner, raw_epoch, state = value.decode("utf-8").split("\n")
        epoch = int(raw_epoch)
    except (UnicodeError, ValueError) as exc:
        raise KeeperUnavailable("Keeper ownership state is corrupt") from exc
    if version != "1" or not owner or epoch < 1 or state not in {
        "portfolio", "reserved", "confirmed"
    }:
        raise KeeperUnavailable("Keeper ownership state has an unknown contract")
    return owner, epoch, state


def _contention(exc: BaseException) -> bool:
    # Kazoo transactions return errors as result objects rather than always
    # raising them.  Match only the three optimistic-concurrency conditions;
    # transport, ACL, and session errors must fail closed, never retry forever.
    return type(exc).__name__ in {"BadVersionError", "NodeExistsError", "NoNodeError"}


def _committed(results: Any) -> bool:
    if not isinstance(results, list):
        raise KeeperUnavailable("Keeper transaction returned an invalid result")
    errors = [value for value in results if isinstance(value, BaseException)]
    if not errors:
        return True
    if all(_contention(error) or type(error).__name__ == "RolledBackError"
           for error in errors) and any(_contention(error) for error in errors):
        return False
    raise KeeperUnavailable("Keeper ownership transaction failed") from errors[0]


class KeeperOwnershipCoordinator:
    """Atomic portfolio claims and persistent campaign claims.

    Portfolio claims are ephemeral and carry a monotonic persistent epoch.
    Suspended/lost sessions cannot admit work; an old owner cannot release a
    newer claim.  Confirmed campaign claims are persistent across restart and
    must be reconciled against the typed journal before trading resumes.
    Methods are blocking control-plane operations; never call them from a
    market-data callback or a Backtest simulation loop.
    """

    def __init__(self, client: Any) -> None:
        self._client = client
        self._lock = Lock()
        self._deadlines: dict[tuple[str, str, int], datetime] = {}
        self._require_connected()
        for path in (f"{_ROOT}/portfolio", f"{_ROOT}/campaign"):
            self._client.ensure_path(path)

    def _require_connected(self) -> None:
        client_state = getattr(self._client, "client_state", None)
        if (not self._client.connected
                or getattr(client_state, "name", str(client_state)) == "CONNECTED_RO"):
            raise KeeperUnavailable("Keeper session is not connected")

    def acquire_portfolio_admission_lease(
        self, resource_id: str, *, owner_id: str, ttl_seconds: float = 30.0,
    ) -> dict[str, Any] | None:
        if ttl_seconds <= 0 or ttl_seconds > 300:
            raise ValueError("Portfolio admission TTL must be in (0, 300] seconds")
        _identity(owner_id, "owner")
        base = _path("portfolio", resource_id)
        counter, holder = f"{base}/epoch", f"{base}/holder"
        with self._lock:
            self._require_connected()
            self._client.ensure_path(base)
            try:
                self._client.create(counter, b"0")
            except Exception as exc:
                if type(exc).__name__ != "NodeExistsError":
                    raise KeeperUnavailable("Could not initialize Keeper fence") from exc
            for _ in range(_MAX_RETRIES):
                self._require_connected()
                if self._client.exists(holder) is not None:
                    return None
                value, stat = self._client.get(counter)
                try:
                    epoch = int(value) + 1
                except ValueError as exc:
                    raise KeeperUnavailable("Keeper portfolio epoch is corrupt") from exc
                if epoch < 1:
                    raise KeeperUnavailable("Keeper portfolio epoch is invalid")
                txn = self._client.transaction()
                txn.set_data(counter, str(epoch).encode("ascii"), version=stat.version)
                txn.create(holder, _encode(owner_id, epoch, "portfolio"), ephemeral=True)
                if not _committed(txn.commit()):
                    continue
                self._require_connected()
                deadline = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
                self._deadlines[(resource_id, owner_id, epoch)] = deadline
                if not self.portfolio_admission_lease_is_current(
                    resource_id, owner_id=owner_id, epoch=epoch
                ):
                    raise KeeperUnavailable("Keeper portfolio claim was lost before admission")
                return {
                    "resource_id": resource_id, "owner_id": owner_id,
                    "epoch": epoch, "expires_at": deadline.isoformat(),
                }
        raise KeeperUnavailable("Keeper portfolio claim contended beyond retry budget")

    def portfolio_admission_lease_is_current(
        self, resource_id: str, *, owner_id: str, epoch: int,
    ) -> bool:
        self._require_connected()
        deadline = self._deadlines.get((resource_id, owner_id, int(epoch)))
        if deadline is None or datetime.now(timezone.utc) >= deadline:
            return False
        try:
            value, stat = self._client.get(_path("portfolio", resource_id) + "/holder")
        except Exception as exc:
            if type(exc).__name__ == "NoNodeError":
                return False
            raise KeeperUnavailable("Could not verify Keeper portfolio fence") from exc
        self._require_connected()
        session = self._client.client_id
        return (_decode(value) == (owner_id, int(epoch), "portfolio")
                and session is not None and stat.ephemeralOwner == session[0])

    def release_portfolio_admission_lease(
        self, resource_id: str, *, owner_id: str, epoch: int,
    ) -> bool:
        self._require_connected()
        path = _path("portfolio", resource_id) + "/holder"
        with self._lock:
            try:
                value, stat = self._client.get(path)
            except Exception as exc:
                if type(exc).__name__ == "NoNodeError":
                    return False
                raise KeeperUnavailable("Could not read Keeper portfolio fence") from exc
            session = self._client.client_id
            if (_decode(value) != (owner_id, int(epoch), "portfolio")
                    or session is None or stat.ephemeralOwner != session[0]):
                return False
            try:
                self._client.delete(path, version=stat.version)
            except Exception as exc:
                if _contention(exc):
                    return False
                raise KeeperUnavailable("Could not release Keeper portfolio fence") from exc
            self._deadlines.pop((resource_id, owner_id, int(epoch)), None)
            return True

    def acquire_campaign_session_ownership(
        self, resource_id: str, *, session_key: str, owner_id: str,
        state: str,
    ) -> dict[str, Any] | None:
        if state not in {"reserved", "confirmed"}:
            raise ValueError("Campaign ownership state must be reserved or confirmed")
        _identity(owner_id, "owner")
        path = _path("campaign", resource_id, session_key)
        with self._lock:
            for _ in range(_MAX_RETRIES):
                self._require_connected()
                try:
                    value, stat = self._client.get(path)
                except Exception as exc:
                    if type(exc).__name__ != "NoNodeError":
                        raise KeeperUnavailable("Could not read Keeper campaign owner") from exc
                    try:
                        self._client.create(path, _encode(owner_id, 1, state))
                    except Exception as create_exc:
                        if _contention(create_exc):
                            continue
                        raise KeeperUnavailable("Could not create Keeper campaign owner") from create_exc
                    epoch, resolved = 1, state
                else:
                    current_owner, previous_epoch, previous_state = _decode(value)
                    if current_owner != owner_id:
                        return None
                    epoch = previous_epoch + 1
                    resolved = "confirmed" if previous_state == "confirmed" else state
                    try:
                        self._client.set(path, _encode(owner_id, epoch, resolved),
                                         version=stat.version)
                    except Exception as set_exc:
                        if _contention(set_exc):
                            continue
                        raise KeeperUnavailable("Could not update Keeper campaign owner") from set_exc
                self._require_connected()
                observed = self.campaign_session_ownership(
                    resource_id, session_key=session_key
                )
                if observed is None or any(observed[key] != expected for key, expected in (
                    ("owner_id", owner_id), ("epoch", epoch), ("state", resolved),
                )):
                    raise KeeperUnavailable("Keeper campaign claim changed before admission")
                return {
                    "resource_id": resource_id, "session_key": session_key,
                    "owner_id": owner_id, "state": resolved, "epoch": epoch,
                }
        raise KeeperUnavailable("Keeper campaign claim contended beyond retry budget")

    def campaign_session_ownership(
        self, resource_id: str, *, session_key: str,
    ) -> dict[str, Any] | None:
        self._require_connected()
        try:
            value, stat = self._client.get(_path("campaign", resource_id, session_key))
        except Exception as exc:
            if type(exc).__name__ == "NoNodeError":
                return None
            raise KeeperUnavailable("Could not read Keeper campaign owner") from exc
        self._require_connected()
        owner_id, epoch, state = _decode(value)
        return {
            "resource_id": resource_id, "session_key": session_key,
            "owner_id": owner_id, "state": state, "epoch": epoch,
            "updated_at": datetime.fromtimestamp(stat.mtime / 1000, timezone.utc).isoformat(),
        }

    def release_campaign_session_reservation(
        self, resource_id: str, *, session_key: str, owner_id: str,
    ) -> bool:
        self._require_connected()
        path = _path("campaign", resource_id, session_key)
        with self._lock:
            try:
                value, stat = self._client.get(path)
            except Exception as exc:
                if type(exc).__name__ == "NoNodeError":
                    return False
                raise KeeperUnavailable("Could not read Keeper campaign reservation") from exc
            current_owner, _, state = _decode(value)
            if current_owner != owner_id or state != "reserved":
                return False
            try:
                self._client.delete(path, version=stat.version)
            except Exception as exc:
                if _contention(exc):
                    return False
                raise KeeperUnavailable("Could not release Keeper campaign reservation") from exc
            return True
