"""Workstation-local Kazoo session with a lock-free-of-I/O admission gate.

All Keeper operations are control-plane work. The realtime path only reads an
already-attested lease from memory; SUSPENDED or LOST immediately clears it.
Reconnect never rearms a claim without an explicit Keeper verification.
"""
from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
from time import monotonic
from typing import Any, Callable, Mapping

from src.trading_runtime.keeper_endpoint import discover_workstation_keeper_endpoint
from src.trading_runtime.keeper_ownership import KeeperUnavailable


def _writable(client: Any) -> bool:
    state = getattr(client, "client_state", None)
    return bool(getattr(client, "connected", False)) and (
        getattr(state, "name", str(state)) != "CONNECTED_RO")


class ManagedKeeperSession:
    """Own one Kazoo client and only cache explicitly verified claim epochs."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self._lock = Lock()
        self._generation = 0
        self._connected = False
        self._closed = False
        self._leases: dict[str, tuple[str, int, int, float, int, Any]] = {}
        client.add_listener(self._on_state)

    def _on_state(self, state: Any) -> None:
        # Kazoo listeners must never block or perform network I/O.
        connected = getattr(state, "name", str(state)) == "CONNECTED" and _writable(self.client)
        with self._lock:
            if not connected:
                self._generation += 1
                self._leases = {}
            self._connected = connected and not self._closed

    @property
    def writable(self) -> bool:
        """Read-only health gate for control-plane composition, not a lease."""
        with self._lock:
            return not self._closed and self._connected and _writable(self.client)

    def attest_lease(self, coordinator: Any, lease: Mapping[str, Any]) -> None:
        """Blocking control-plane proof; do not call from a market callback."""
        resource = lease.get("resource_id")
        owner = lease.get("owner_id")
        epoch = lease.get("epoch")
        raw_expiry = lease.get("expires_at")
        if (not isinstance(resource, str) or not resource
                or not isinstance(owner, str) or not owner
                or type(epoch) is not int or epoch < 1
                or not isinstance(raw_expiry, str)):
            raise ValueError("Keeper lease identity is invalid")
        expiry = datetime.fromisoformat(raw_expiry)
        if expiry.tzinfo is None:
            raise ValueError("Keeper lease expiry must be timezone-aware")
        with self._lock:
            generation = self._generation
            if self._closed or not self._connected or not _writable(self.client):
                raise KeeperUnavailable("Keeper session is not writable")
            session_id = self.client.client_id[0]
        if (session_id is None or not coordinator.portfolio_admission_lease_is_current(
                resource, owner_id=owner, epoch=epoch)):
            raise KeeperUnavailable("Keeper claim is not current")
        remaining = (expiry.astimezone(timezone.utc)
                     - datetime.now(timezone.utc)).total_seconds()
        remaining = min(remaining, coordinator.lease_remaining_seconds(
            resource, owner_id=owner, epoch=epoch))
        with self._lock:
            if (self._closed or not self._connected
                    or self._generation != generation
                    or not _writable(self.client)
                    or self.client.client_id[0] != session_id
                    or remaining <= 0):
                raise KeeperUnavailable("Keeper claim changed during attestation")
            updated = dict(self._leases)
            updated[resource] = (owner, epoch, generation,
                                 monotonic() + remaining, session_id, coordinator)
            self._leases = updated

    def admission_is_current(self, resource_id: str, *, owner_id: str,
                             epoch: int) -> bool:
        """Local-only hot-path check: no Keeper, ClickHouse, disk, or wait."""
        with self._lock:
            lease = self._leases.get(resource_id)
            return bool(
                not self._closed and self._connected and lease is not None
                and lease[0] == owner_id and lease[1] == epoch
                and lease[2] == self._generation
                and monotonic() < lease[3]
                and _writable(self.client)
                and self.client.client_id[0] == lease[4]
                and lease[5].lease_remaining_seconds(
                    resource_id, owner_id=owner_id, epoch=epoch) > 0
            )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._connected = False
            self._leases = {}
        self.client.remove_listener(self._on_state)
        self.client.stop()
        self.client.close()


def open_workstation_keeper_session(
    *, timeout_seconds: float = 5.0,
    client_factory: Callable[..., Any] | None = None,
    discover: Callable[..., Any] = discover_workstation_keeper_endpoint,
) -> ManagedKeeperSession:
    """Connect from native Windows to the current WSL-private Keeper address."""
    if not 0 < timeout_seconds <= 30:
        raise ValueError("Keeper connection timeout is invalid")
    if client_factory is None:
        try:
            from kazoo.client import KazooClient
        except ImportError as exc:
            raise RuntimeError("Kazoo 2.11.0 is required for Keeper ownership") from exc
        client_factory = KazooClient
    endpoint = discover()
    client = client_factory(hosts=f"{endpoint.host}:{endpoint.port}",
                            timeout=timeout_seconds)
    session = ManagedKeeperSession(client)
    try:
        client.start(timeout=timeout_seconds)
        if not _writable(client):
            raise KeeperUnavailable("Keeper started without a writable session")
        session._on_state("CONNECTED")
        return session
    except BaseException:
        session.close()
        raise
