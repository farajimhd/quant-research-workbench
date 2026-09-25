"""Fenced ownership primitives for a ClickHouse Keeper (ZooKeeper) client.

This is a staged coordinator, not a journal or a live-runtime cutover.  A
connected client implementing the Kazoo API is injected by the operator-owned
bootstrap.  Journal transitions and recovery evidence still belong in typed
``arte`` tables; no market or journal table is modified here.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import re
from contextlib import asynccontextmanager
from threading import Lock
from time import monotonic
from typing import Any
from uuid import UUID, uuid4


_ROOT = "/trading/ownership/v1"
_MAX_RETRIES = 8
_SYNC_RECEIPTS = f"{_ROOT}/portfolio_sync_receipt"
_BACKTEST_TERMINAL_RECEIPTS = f"{_ROOT}/backtest_terminal_v2"


def _sync_resource_id(run_id: str, account_id: str) -> str:
    return "sync:" + sha256((
        _identity(run_id, "run") + "\x00" + _identity(account_id, "account")
    ).encode("utf-8")).hexdigest()


def _sync_receipt_path(run_id: str, account_id: str, revision: int) -> str:
    if type(revision) is not int or revision < 1:
        raise ValueError("Portfolio sync receipt needs a positive revision")
    return _path("portfolio_sync_receipt", run_id, account_id, str(revision))


def _sync_receipt_bytes(run_id: str, account_id: str, revision: int,
                        batch_id: str, snapshot_hash: str,
                        owner_id: str, epoch: int) -> bytes:
    _sync_receipt_path(run_id, account_id, revision)
    UUID(batch_id)
    if (not re.fullmatch(r"[0-9a-f]{64}", snapshot_hash)
            or type(epoch) is not int or epoch < 1):
        raise ValueError("Portfolio sync receipt has invalid digest or epoch")
    return (f"1\n{run_id}\n{account_id}\n{revision}\n{batch_id}\n"
            f"{snapshot_hash}\n{_identity(owner_id, 'owner')}\n{epoch}").encode("utf-8")


class KeeperUnavailable(RuntimeError):
    """Coordination is unhealthy; callers must reject new trading admission."""


def _identity(value: str, label: str) -> str:
    if (not isinstance(value, str) or not value
            or any(delimiter in value for delimiter in ("\n", "\r", "\x00"))):
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
        self._monotonic_deadlines: dict[tuple[str, str, int], float] = {}
        self._require_connected()
        for path in (f"{_ROOT}/portfolio", f"{_ROOT}/campaign", _SYNC_RECEIPTS,
                     _BACKTEST_TERMINAL_RECEIPTS):
            self._client.ensure_path(path)

    def attest_backtest_terminal_v2(
        self, leases: tuple[tuple[str, dict[str, Any]], ...], *, run_id: str,
        batch_id: str, seal_hash: str, accounts_hash: str,
    ) -> bytes:
        """CAS one persistent terminal proof against every held account epoch."""
        UUID(batch_id)
        if (not leases or len({account for account, _ in leases}) != len(leases)
                or any(re.fullmatch(r"[0-9a-f]{64}", digest) is None
                       for digest in (seal_hash, accounts_hash))):
            raise ValueError("Backtest terminal proof identity is invalid")
        ordered = sorted(leases)
        lines = ["2", _identity(run_id, "run"), batch_id, seal_hash,
                 accounts_hash, str(len(ordered))]
        for account_id, lease in ordered:
            if (lease.get("resource_id") != _sync_resource_id(run_id, account_id)
                    or type(lease.get("epoch")) is not int):
                raise ValueError("Backtest terminal claim differs from pinned account")
            lines.extend((_identity(account_id, "account"),
                          _identity(lease["owner_id"], "owner"), str(lease["epoch"])))
        payload = "\n".join(lines).encode("utf-8")
        path = _path("backtest_terminal_v2", run_id, batch_id)
        with self._lock:
            self._require_connected()
            txn = self._client.transaction()
            for account_id, lease in ordered:
                if not self.portfolio_snapshot_claim_is_current(lease):
                    raise KeeperUnavailable("Backtest terminal claim expired before CAS")
                base = _path("portfolio", lease["resource_id"])
                holder, holder_stat = self._client.get(f"{base}/holder")
                counter, counter_stat = self._client.get(f"{base}/epoch")
                if (_decode(holder) != (lease["owner_id"], lease["epoch"], "portfolio")
                        or int(counter) != lease["epoch"]
                        or holder_stat.ephemeralOwner != self._client.client_id[0]):
                    raise KeeperUnavailable("Backtest terminal owner epoch changed")
                txn.check(f"{base}/holder", version=holder_stat.version)
                txn.check(f"{base}/epoch", version=counter_stat.version)
            txn.create(path, payload, ephemeral=False)
            if not _committed(txn.commit()):
                if self.load_backtest_terminal_v2_attestation(run_id, batch_id) != payload:
                    raise KeeperUnavailable("Backtest terminal CAS lost or conflicts")
            self._require_connected()
        if self.load_backtest_terminal_v2_attestation(run_id, batch_id) != payload:
            raise KeeperUnavailable("Backtest terminal proof did not become durable")
        return payload

    def load_backtest_terminal_v2_attestation(self, run_id: str, batch_id: str) -> bytes | None:
        self._require_connected()
        try:
            value, _ = self._client.get(_path("backtest_terminal_v2", run_id, batch_id))
        except Exception as exc:
            if type(exc).__name__ == "NoNodeError":
                return None
            raise KeeperUnavailable("Could not read Backtest terminal proof") from exc
        self._require_connected()
        try:
            lines = value.decode("utf-8").split("\n")
            count = int(lines[5])
            if (len(lines) != 6 + 3 * count or lines[0] != "2"
                    or lines[1:3] != [run_id, batch_id] or count < 1
                    or any(re.fullmatch(r"[0-9a-f]{64}", item) is None
                           for item in lines[3:5])
                    or lines[6::3] != sorted(set(lines[6::3]))):
                raise ValueError("proof content differs")
            for epoch in lines[8::3]:
                if int(epoch) < 1:
                    raise ValueError("proof epoch invalid")
        except (UnicodeError, ValueError, IndexError) as exc:
            raise KeeperUnavailable("Backtest terminal proof is corrupt") from exc
        return value

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
                key = (resource_id, owner_id, epoch)
                self._monotonic_deadlines[key] = monotonic() + ttl_seconds
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
        key = (resource_id, owner_id, int(epoch))
        deadline = self._monotonic_deadlines.get(key)
        if deadline is None or monotonic() >= deadline:
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

    def renew_portfolio_admission_lease(
        self, resource_id: str, *, owner_id: str, epoch: int,
        ttl_seconds: float = 30.0,
    ) -> dict[str, Any] | None:
        """Extend a still-current claim while its durable receipt is pending.

        This is a blocking control-plane operation. A lost or expired claim is
        never revived; its holder must fail the pending admission instead.
        """
        if ttl_seconds <= 0 or ttl_seconds > 300:
            raise ValueError("Portfolio admission TTL must be in (0, 300] seconds")
        _identity(owner_id, "owner")
        with self._lock:
            if not self.portfolio_admission_lease_is_current(
                resource_id, owner_id=owner_id, epoch=epoch,
            ):
                return None
            deadline = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
            key = (resource_id, owner_id, int(epoch))
            self._monotonic_deadlines[key] = monotonic() + ttl_seconds
            return {
                "resource_id": resource_id, "owner_id": owner_id,
                "epoch": int(epoch), "expires_at": deadline.isoformat(),
            }

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
            self._monotonic_deadlines.pop((resource_id, owner_id, int(epoch)), None)
            return True

    @asynccontextmanager
    async def claim_portfolio_snapshot(self, run_id: str, account_id: str):
        """Account-scoped sync claim; callers retain it through the CAS receipt."""
        resource_id = _sync_resource_id(run_id, account_id)
        lease = await asyncio.to_thread(
            self.acquire_portfolio_admission_lease,
            resource_id, owner_id=f"portfolio-sync-{uuid4()}")
        if lease is None:
            raise KeeperUnavailable("Portfolio sync account is owned by another session")
        try:
            yield lease
        finally:
            await asyncio.to_thread(
                self.release_portfolio_admission_lease,
                resource_id, owner_id=lease["owner_id"], epoch=lease["epoch"])

    def portfolio_snapshot_claim_is_current(self, lease: Any) -> bool:
        if (not isinstance(lease, dict) or set(lease) != {
                "resource_id", "owner_id", "epoch", "expires_at"}):
            raise ValueError("Portfolio sync claim has invalid identity")
        return self.portfolio_admission_lease_is_current(
            lease["resource_id"], owner_id=lease["owner_id"], epoch=lease["epoch"])

    def attest_portfolio_snapshot_receipt(
        self, lease: Any, run_id: str, account_id: str,
        state_revision: int, batch_id: str, snapshot_hash: str,
    ):
        """Atomically bind one CH fence digest to the current owner epoch.

        Both ephemeral holder and persistent epoch counter are checked in the
        same Keeper transaction that creates the persistent proof. Checking
        the counter prevents a delete/recreate holder ABA at version zero.
        """
        from src.trading_runtime.arte_portfolio_sync import KeeperSyncAttestation

        resource_id = _sync_resource_id(run_id, account_id)
        if (not isinstance(lease, dict) or lease.get("resource_id") != resource_id
                or type(lease.get("epoch")) is not int
                or not isinstance(lease.get("owner_id"), str)):
            raise ValueError("Portfolio sync receipt claim differs from account")
        owner_id, epoch = lease["owner_id"], lease["epoch"]
        payload = _sync_receipt_bytes(
            run_id, account_id, state_revision, batch_id, snapshot_hash,
            owner_id, epoch)
        path = _sync_receipt_path(run_id, account_id, state_revision)
        base = _path("portfolio", resource_id)
        with self._lock:
            if not self.portfolio_snapshot_claim_is_current(lease):
                raise KeeperUnavailable("Portfolio sync claim expired before attestation")
            try:
                holder_value, holder_stat = self._client.get(f"{base}/holder")
                counter_value, counter_stat = self._client.get(f"{base}/epoch")
            except Exception as exc:
                raise KeeperUnavailable("Could not read Keeper sync attestation fence") from exc
            session = self._client.client_id
            if (_decode(holder_value) != (owner_id, epoch, "portfolio")
                    or session is None or holder_stat.ephemeralOwner != session[0]
                    or int(counter_value) != epoch):
                raise KeeperUnavailable("Portfolio sync owner epoch changed before attestation")
            txn = self._client.transaction()
            txn.check(f"{base}/holder", version=holder_stat.version)
            txn.check(f"{base}/epoch", version=counter_stat.version)
            txn.create(path, payload, ephemeral=False)
            if not _committed(txn.commit()):
                existing = self.load_portfolio_snapshot_receipt(
                    run_id, account_id, state_revision)
                if (existing is None or existing.owner_id != owner_id
                        or existing.epoch != epoch or existing.batch_id != batch_id
                        or existing.snapshot_hash != snapshot_hash
                        or not self.portfolio_snapshot_claim_is_current(lease)):
                    raise KeeperUnavailable("Portfolio sync CAS lost or conflicts")
            self._require_connected()
        proof = self.load_portfolio_snapshot_receipt(run_id, account_id, state_revision)
        if proof is None or proof != KeeperSyncAttestation(
                run_id, account_id, state_revision, batch_id,
                snapshot_hash, owner_id, epoch):
            raise KeeperUnavailable("Portfolio sync CAS receipt did not become durable")
        return proof

    def load_portfolio_snapshot_receipt(
        self, run_id: str, account_id: str, state_revision: int,
    ):
        """Read a historical proof without requiring its old owner still live."""
        from src.trading_runtime.arte_portfolio_sync import KeeperSyncAttestation

        self._require_connected()
        try:
            value, _ = self._client.get(
                _sync_receipt_path(run_id, account_id, state_revision))
        except Exception as exc:
            if type(exc).__name__ == "NoNodeError":
                return None
            raise KeeperUnavailable("Could not read Keeper sync receipt") from exc
        self._require_connected()
        try:
            parts = value.decode("utf-8").split("\n")
            if len(parts) != 8 or parts[0] != "1":
                raise ValueError("unknown proof version")
            _, stored_run, stored_account, revision, batch_id, digest, owner_id, epoch = parts
            proof = KeeperSyncAttestation(
                stored_run, stored_account, int(revision), batch_id,
                digest, owner_id, int(epoch))
            if (_sync_receipt_bytes(stored_run, stored_account, int(revision),
                                    batch_id, digest, owner_id, int(epoch)) != value
                    or proof.run_id != run_id or proof.account_id != account_id
                    or proof.state_revision != state_revision):
                raise ValueError("proof identity differs")
        except (UnicodeError, ValueError, TypeError) as exc:
            raise KeeperUnavailable("Keeper sync receipt is corrupt") from exc
        return proof

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
