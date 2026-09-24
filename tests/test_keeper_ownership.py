"""Contention and failure semantics of the staged Keeper coordinator."""
from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from time import time

import pytest

from src.trading_runtime.keeper_ownership import (
    KeeperOwnershipCoordinator, KeeperUnavailable,
)


class NodeExistsError(Exception):
    pass


class NoNodeError(Exception):
    pass


class BadVersionError(Exception):
    pass


class RolledBackError(Exception):
    pass


@dataclass
class _Stat:
    version: int = 0
    ephemeralOwner: int = 0
    mtime: int = 0


class _Store:
    def __init__(self) -> None:
        self.nodes: dict[str, tuple[bytes, _Stat]] = {}
        self.lock = Lock()


class _Transaction:
    def __init__(self, client: "_Client") -> None:
        self.client = client
        self.actions: list[tuple] = []

    def set_data(self, path: str, data: bytes, *, version: int) -> None:
        self.actions.append(("set", path, data, version))

    def create(self, path: str, data: bytes, *, ephemeral: bool) -> None:
        self.actions.append(("create", path, data, ephemeral))

    def commit(self) -> list[object]:
        with self.client.store.lock:
            for action in self.actions:
                kind, path = action[:2]
                current = self.client.store.nodes.get(path)
                error = None
                if kind == "set" and current is None:
                    error = NoNodeError()
                elif kind == "set" and current[1].version != action[3]:
                    error = BadVersionError()
                elif kind == "create" and current is not None:
                    error = NodeExistsError()
                if error is not None:
                    return [error, *[RolledBackError() for _ in self.actions[1:]]]
            for action in self.actions:
                kind, path, data = action[:3]
                if kind == "set":
                    current = self.client.store.nodes[path][1]
                    self.client.store.nodes[path] = (data, _Stat(
                        current.version + 1, current.ephemeralOwner, int(time() * 1000)))
                else:
                    self.client.store.nodes[path] = (data, _Stat(
                        ephemeralOwner=self.client.client_id[0] if action[3] else 0,
                        mtime=int(time() * 1000)))
        return [None for _ in self.actions]


class _Client:
    def __init__(self, store: _Store, session: int) -> None:
        self.store = store
        self.client_id = (session, b"private-session-secret")
        self.connected = True

    def ensure_path(self, path: str) -> None:
        with self.store.lock:
            current = ""
            for part in path.strip("/").split("/"):
                current += "/" + part
                self.store.nodes.setdefault(current, (b"", _Stat(mtime=int(time() * 1000))))

    def create(self, path: str, value: bytes, *, ephemeral: bool = False) -> None:
        with self.store.lock:
            if path in self.store.nodes:
                raise NodeExistsError()
            self.store.nodes[path] = (value, _Stat(
                ephemeralOwner=self.client_id[0] if ephemeral else 0,
                mtime=int(time() * 1000)))

    def get(self, path: str) -> tuple[bytes, _Stat]:
        with self.store.lock:
            try:
                return self.store.nodes[path]
            except KeyError as exc:
                raise NoNodeError() from exc

    def exists(self, path: str) -> _Stat | None:
        with self.store.lock:
            row = self.store.nodes.get(path)
            return row[1] if row else None

    def set(self, path: str, value: bytes, *, version: int) -> None:
        with self.store.lock:
            old, stat = self.store.nodes[path]
            if stat.version != version:
                raise BadVersionError()
            self.store.nodes[path] = (value, _Stat(
                version + 1, stat.ephemeralOwner, int(time() * 1000)))

    def delete(self, path: str, *, version: int) -> None:
        with self.store.lock:
            row = self.store.nodes.get(path)
            if row is None:
                raise NoNodeError()
            if row[1].version != version:
                raise BadVersionError()
            del self.store.nodes[path]

    def transaction(self) -> _Transaction:
        return _Transaction(self)


def test_portfolio_claims_are_exclusive_and_fenced_across_clients() -> None:
    store = _Store()
    first_client, second_client = _Client(store, 11), _Client(store, 12)
    first = KeeperOwnershipCoordinator(first_client)
    second = KeeperOwnershipCoordinator(second_client)
    lease = first.acquire_portfolio_admission_lease("account:DU1", owner_id="run-a")
    assert lease is not None and lease["epoch"] == 1
    assert second.acquire_portfolio_admission_lease("account:DU1", owner_id="run-b") is None
    assert first.portfolio_admission_lease_is_current(
        "account:DU1", owner_id="run-a", epoch=1)
    assert first.release_portfolio_admission_lease(
        "account:DU1", owner_id="run-a", epoch=1)
    replacement = second.acquire_portfolio_admission_lease("account:DU1", owner_id="run-b")
    assert replacement is not None and replacement["epoch"] == 2
    assert not first.release_portfolio_admission_lease(
        "account:DU1", owner_id="run-a", epoch=1)
    assert not first.portfolio_admission_lease_is_current(
        "account:DU1", owner_id="run-a", epoch=1)


def test_portfolio_admission_fails_closed_when_keeper_session_suspends() -> None:
    client = _Client(_Store(), 11)
    coordinator = KeeperOwnershipCoordinator(client)
    lease = coordinator.acquire_portfolio_admission_lease("account:DU1", owner_id="run-a")
    assert lease is not None
    client.connected = False
    with pytest.raises(KeeperUnavailable, match="not connected"):
        coordinator.portfolio_admission_lease_is_current(
            "account:DU1", owner_id="run-a", epoch=lease["epoch"])
    with pytest.raises(KeeperUnavailable, match="not connected"):
        coordinator.acquire_portfolio_admission_lease("account:DU2", owner_id="run-a")


def test_campaign_confirmed_ownership_survives_new_client_and_rejects_competitor() -> None:
    store = _Store()
    first = KeeperOwnershipCoordinator(_Client(store, 11))
    second = KeeperOwnershipCoordinator(_Client(store, 12))
    reserved = first.acquire_campaign_session_ownership(
        "ticker:AAPL", session_key="2026-08-18", owner_id="campaign-a", state="reserved")
    assert reserved is not None and reserved["epoch"] == 1
    assert second.acquire_campaign_session_ownership(
        "ticker:AAPL", session_key="2026-08-18", owner_id="campaign-b", state="reserved") is None
    confirmed = first.acquire_campaign_session_ownership(
        "ticker:AAPL", session_key="2026-08-18", owner_id="campaign-a", state="confirmed")
    assert confirmed is not None and confirmed["epoch"] == 2
    assert not second.release_campaign_session_reservation(
        "ticker:AAPL", session_key="2026-08-18", owner_id="campaign-a")
    assert second.campaign_session_ownership(
        "ticker:AAPL", session_key="2026-08-18")["state"] == "confirmed"


def test_campaign_reservation_releases_only_exact_owner() -> None:
    store = _Store()
    first = KeeperOwnershipCoordinator(_Client(store, 11))
    second = KeeperOwnershipCoordinator(_Client(store, 12))
    first.acquire_campaign_session_ownership(
        "ticker:AAPL", session_key="2026-08-18", owner_id="campaign-a", state="reserved")
    assert not second.release_campaign_session_reservation(
        "ticker:AAPL", session_key="2026-08-18", owner_id="campaign-b")
    assert first.release_campaign_session_reservation(
        "ticker:AAPL", session_key="2026-08-18", owner_id="campaign-a")
    assert second.acquire_campaign_session_ownership(
        "ticker:AAPL", session_key="2026-08-18", owner_id="campaign-b", state="reserved") is not None
