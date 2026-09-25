from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.trading_runtime.keeper_ownership import KeeperUnavailable
from src.trading_runtime.keeper_session import (
    ManagedKeeperSession, open_workstation_keeper_session,
)


class Client:
    def __init__(self, *, hosts="", timeout=0):
        self.hosts = hosts
        self.timeout = timeout
        self.connected = False
        self.client_state = SimpleNamespace(name="CONNECTING")
        self.client_id = (11, b"")
        self.listeners = []
        self.stopped = False
        self.closed = False

    def add_listener(self, listener):
        self.listeners.append(listener)

    def remove_listener(self, listener):
        self.listeners.remove(listener)

    def emit(self, state):
        self.connected = state == "CONNECTED"
        self.client_state = SimpleNamespace(name=state)
        for listener in tuple(self.listeners):
            listener(SimpleNamespace(name=state))

    def start(self, *, timeout):
        assert timeout == self.timeout
        self.emit("CONNECTED")

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


class Coordinator:
    def __init__(self):
        self.calls = 0
        self.current = True

    def portfolio_admission_lease_is_current(self, resource, *, owner_id, epoch):
        assert (resource, owner_id, epoch) == ("portfolio-1", "owner-1", 3)
        self.calls += 1
        return self.current

    def lease_remaining_seconds(self, resource, *, owner_id, epoch):
        assert (resource, owner_id, epoch) == ("portfolio-1", "owner-1", 3)
        return 30.0


def lease():
    return {
        "resource_id": "portfolio-1", "owner_id": "owner-1", "epoch": 3,
        "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat(),
    }


def test_session_freezes_on_suspend_and_requires_new_attestation_after_reconnect():
    client = Client(timeout=5.0)
    session = open_workstation_keeper_session(
        client_factory=lambda **kwargs: client,
        discover=lambda: SimpleNamespace(host="172.25.158.41", port=9181),
    )
    coordinator = Coordinator()
    assert not session.admission_is_current("portfolio-1", owner_id="owner-1", epoch=3)
    session.attest_lease(coordinator, lease())
    assert session.admission_is_current("portfolio-1", owner_id="owner-1", epoch=3)
    assert coordinator.calls == 1
    client.emit("SUSPENDED")
    assert not session.admission_is_current("portfolio-1", owner_id="owner-1", epoch=3)
    client.emit("CONNECTED")
    assert not session.admission_is_current("portfolio-1", owner_id="owner-1", epoch=3)
    coordinator.current = False
    with pytest.raises(KeeperUnavailable, match="not current"):
        session.attest_lease(coordinator, lease())
    coordinator.current = True
    session.attest_lease(coordinator, lease())
    assert session.admission_is_current("portfolio-1", owner_id="owner-1", epoch=3)
    coordinator.lease_remaining_seconds = lambda *_a, **_k: 0.0
    assert not session.admission_is_current("portfolio-1", owner_id="owner-1", epoch=3)
    coordinator.lease_remaining_seconds = lambda *_a, **_k: 30.0
    client.client_id = (12, b"")
    assert not session.admission_is_current("portfolio-1", owner_id="owner-1", epoch=3)
    session.close()
    assert client.stopped and client.closed


def test_session_rejects_expired_lease_and_read_only_connection():
    client = Client(timeout=5.0)
    client.start(timeout=5)
    session = ManagedKeeperSession(client)
    session._on_state("CONNECTED")
    expired = {**lease(), "expires_at": (
        datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()}
    with pytest.raises(KeeperUnavailable, match="during attestation"):
        session.attest_lease(Coordinator(), expired)
    coordinator = Coordinator()
    coordinator.lease_remaining_seconds = lambda *_a, **_k: 0.0
    with pytest.raises(KeeperUnavailable, match="during attestation"):
        session.attest_lease(coordinator, lease())
    client.client_state = SimpleNamespace(name="CONNECTED_RO")
    assert not session.admission_is_current("portfolio-1", owner_id="owner-1", epoch=3)
    session.close()


def test_startup_failure_closes_unwritable_session():
    class ReadOnlyClient(Client):
        def start(self, *, timeout):
            self.connected = True
            self.client_state = SimpleNamespace(name="CONNECTED_RO")

    client = ReadOnlyClient()
    with pytest.raises(KeeperUnavailable, match="writable session"):
        open_workstation_keeper_session(
            client_factory=lambda **kwargs: client,
            discover=lambda: SimpleNamespace(host="172.25.158.41", port=9181),
        )
    assert client.stopped and client.closed


def test_session_change_during_blocking_attestation_never_arms_admission():
    client = Client(timeout=5.0)
    session = open_workstation_keeper_session(
        client_factory=lambda **kwargs: client,
        discover=lambda: SimpleNamespace(host="172.25.158.41", port=9181),
    )

    class RacyCoordinator(Coordinator):
        def portfolio_admission_lease_is_current(self, resource, *, owner_id, epoch):
            client.emit("SUSPENDED")
            client.emit("CONNECTED")
            return True

    with pytest.raises(KeeperUnavailable, match="during attestation"):
        session.attest_lease(RacyCoordinator(), lease())
    assert not session.admission_is_current("portfolio-1", owner_id="owner-1", epoch=3)
    session.close()
