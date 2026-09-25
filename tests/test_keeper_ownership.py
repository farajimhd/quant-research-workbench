"""Contention and failure semantics of the staged Keeper coordinator."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from threading import Lock, get_ident
from time import monotonic, time

import pytest

from src.trading_runtime.keeper_ownership import (
    KeeperOwnershipCoordinator, KeeperUnavailable,
)
from src.trading_runtime import keeper_ownership as ownership
from src.trading_runtime.arte_portfolio_sync import KeeperSyncAttestation


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

    def check(self, path: str, *, version: int) -> None:
        self.actions.append(("check", path, version))

    def create(self, path: str, data: bytes, *, ephemeral: bool) -> None:
        self.actions.append(("create", path, data, ephemeral))

    def commit(self) -> list[object]:
        with self.client.store.lock:
            for action in self.actions:
                kind, path = action[:2]
                current = self.client.store.nodes.get(path)
                error = None
                if kind in {"set", "check"} and current is None:
                    error = NoNodeError()
                elif kind == "set" and current[1].version != action[3]:
                    error = BadVersionError()
                elif kind == "check" and current[1].version != action[2]:
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
                elif kind == "create":
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


def test_sync_attestation_cas_survives_owner_change_without_live_authority() -> None:
    store = _Store()
    first = KeeperOwnershipCoordinator(_Client(store, 11))
    second = KeeperOwnershipCoordinator(_Client(store, 12))
    run, account = "run-a", "DU1"
    batch = "00000000-0000-0000-0000-000000000012"
    async def attest():
        async with first.claim_portfolio_snapshot(run, account) as lease:
            proof = first.attest_portfolio_snapshot_receipt(
                lease, run, account, 7, batch, "a" * 64)
            assert proof == KeeperSyncAttestation(
                run, account, 7, batch, "a" * 64,
                lease["owner_id"], lease["epoch"])
            return lease, proof
    lease, proof = asyncio.run(attest())
    assert not first.portfolio_snapshot_claim_is_current(lease)
    async def new_owner():
        async with second.claim_portfolio_snapshot(run, account) as newer:
            assert newer["epoch"] > proof.epoch
            assert second.load_portfolio_snapshot_receipt(run, account, 7) == proof
            with pytest.raises(KeeperUnavailable, match="expired before attestation"):
                first.attest_portfolio_snapshot_receipt(
                    lease, run, account, 8, batch, "b" * 64)
    asyncio.run(new_owner())


def test_sync_attestation_rejects_holder_aba_during_cas(monkeypatch) -> None:
    store = _Store()
    client = _Client(store, 11)
    coordinator = KeeperOwnershipCoordinator(client)
    run, account = "run-a", "DU1"
    batch = "00000000-0000-0000-0000-000000000012"
    original_transaction = client.transaction
    async def race():
        async with coordinator.claim_portfolio_snapshot(run, account) as lease:
            base = ownership._path("portfolio", lease["resource_id"])
            def racing_transaction():
                txn = original_transaction()
                original_commit = txn.commit
                def commit():
                    # Simulate expiration and reacquisition between the read
                    # and CAS. The new holder also has version zero (ABA).
                    with store.lock:
                        counter, stat = store.nodes[f"{base}/epoch"]
                        store.nodes[f"{base}/epoch"] = (b"2", _Stat(
                            stat.version + 1, 0, int(time() * 1000)))
                        store.nodes[f"{base}/holder"] = (
                            ownership._encode("new-owner", 2, "portfolio"),
                            _Stat(0, 12, int(time() * 1000)))
                    return original_commit()
                txn.commit = commit
                return txn
            monkeypatch.setattr(client, "transaction", racing_transaction)
            with pytest.raises(KeeperUnavailable, match="CAS lost or conflicts"):
                coordinator.attest_portfolio_snapshot_receipt(
                    lease, run, account, 7, batch, "a" * 64)
            assert coordinator.load_portfolio_snapshot_receipt(run, account, 7) is None
    asyncio.run(race())


def test_sync_attestation_is_idempotent_only_for_exact_receipt() -> None:
    store = _Store()
    coordinator = KeeperOwnershipCoordinator(_Client(store, 11))
    run, account = "run-a", "DU1"
    batch = "00000000-0000-0000-0000-000000000012"
    async def publish():
        async with coordinator.claim_portfolio_snapshot(run, account) as lease:
            first = coordinator.attest_portfolio_snapshot_receipt(
                lease, run, account, 7, batch, "a" * 64)
            assert coordinator.attest_portfolio_snapshot_receipt(
                lease, run, account, 7, batch, "a" * 64) == first
            with pytest.raises(KeeperUnavailable, match="CAS lost or conflicts"):
                coordinator.attest_portfolio_snapshot_receipt(
                    lease, run, account, 7, batch, "b" * 64)
    asyncio.run(publish())


def test_v2_sync_transition_head_is_atomic_ordered_and_historical() -> None:
    store = _Store()
    first = KeeperOwnershipCoordinator(_Client(store, 11))
    second = KeeperOwnershipCoordinator(_Client(store, 12))
    run, account = "run-v2", "DU1"
    batch = "00000000-0000-0000-0000-000000000012"
    async def publish():
        async with first.claim_portfolio_snapshot(run, account) as lease:
            receipt = first.attest_portfolio_snapshot_receipt(
                lease, run, account, 1, batch, "a" * 64,
                marker_hash="b" * 64, fence_hash="c" * 64)
            assert receipt.marker_hash == "b" * 64
            assert first.attest_portfolio_snapshot_receipt(
                lease, run, account, 1, batch, "a" * 64,
                marker_hash="b" * 64, fence_hash="c" * 64) == receipt
            with pytest.raises(KeeperUnavailable, match="transition proof conflicts"):
                first.attest_portfolio_snapshot_receipt(
                    lease, run, account, 1, batch, "a" * 64,
                    marker_hash="d" * 64, fence_hash="c" * 64)
            later = first.attest_portfolio_snapshot_receipt(
                lease, run, account, 3, batch, "e" * 64,
                marker_hash="f" * 64, fence_hash="1" * 64)
            head = first.load_portfolio_sync_transition_head(run, account)[0]
            assert (head.proof_count, head.last_revision) == (2, 3)
            assert first.load_portfolio_sync_transition_head(run, None)[0].proof_count == 2
            with pytest.raises(KeeperUnavailable, match="revision is stale"):
                first.attest_portfolio_snapshot_receipt(
                    lease, run, account, 2, batch, "a" * 64,
                    marker_hash="b" * 64, fence_hash="c" * 64)
            return lease, receipt, later
    old_lease, first_proof, last_proof = asyncio.run(publish())
    async def new_owner():
        async with second.claim_portfolio_snapshot(run, account) as lease:
            assert lease["epoch"] > old_lease["epoch"]
            assert second.load_portfolio_snapshot_receipt(run, account, 1) == first_proof
            assert second.load_portfolio_snapshot_receipt(run, account, 3) == last_proof
            with pytest.raises(KeeperUnavailable, match="expired before attestation"):
                first.attest_portfolio_snapshot_receipt(
                    old_lease, run, account, 4, batch, "a" * 64,
                    marker_hash="b" * 64, fence_hash="c" * 64)
    asyncio.run(new_owner())


def test_sync_attestation_rejects_corrupt_historical_proof() -> None:
    store = _Store()
    coordinator = KeeperOwnershipCoordinator(_Client(store, 11))
    path = ownership._sync_receipt_path("run-a", "DU1", 7)
    store.nodes[path] = (b"1\nwrong", _Stat())
    with pytest.raises(KeeperUnavailable, match="corrupt"):
        coordinator.load_portfolio_snapshot_receipt("run-a", "DU1", 7)


def test_async_sync_claim_acquires_and_releases_off_event_loop(monkeypatch) -> None:
    coordinator = KeeperOwnershipCoordinator(_Client(_Store(), 11))
    loop_thread = get_ident()
    calls = []
    acquire = coordinator.acquire_portfolio_admission_lease
    release = coordinator.release_portfolio_admission_lease
    def tracked_acquire(*args, **kwargs):
        calls.append(("acquire", get_ident()))
        return acquire(*args, **kwargs)
    def tracked_release(*args, **kwargs):
        calls.append(("release", get_ident()))
        return release(*args, **kwargs)
    monkeypatch.setattr(coordinator, "acquire_portfolio_admission_lease", tracked_acquire)
    monkeypatch.setattr(coordinator, "release_portfolio_admission_lease", tracked_release)
    async def claim():
        async with coordinator.claim_portfolio_snapshot("run-a", "DU1") as lease:
            assert coordinator.portfolio_snapshot_claim_is_current(lease)
    asyncio.run(claim())
    assert [name for name, _ in calls] == ["acquire", "release"]
    assert all(thread_id != loop_thread for _, thread_id in calls)


def test_sync_proof_identity_rejects_delimiters() -> None:
    batch = "00000000-0000-0000-0000-000000000012"
    with pytest.raises(ValueError, match="single-line"):
        ownership._sync_receipt_bytes("run\nother", "DU1", 7, batch,
                                      "a" * 64, "owner-a", 1)
    with pytest.raises(ValueError, match="single-line"):
        ownership._sync_receipt_bytes("run-a", "DU1", 7, batch,
                                      "a" * 64, "owner\nother", 1)
    with pytest.raises(ValueError, match="single-line"):
        ownership._sync_receipt_bytes("run-a", "DU1\rother", 7, batch,
                                      "a" * 64, "owner-a", 1)


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
    assert first.lease_remaining_seconds(
        "account:DU1", owner_id="run-a", epoch=1) > 0
    assert first.release_portfolio_admission_lease(
        "account:DU1", owner_id="run-a", epoch=1)
    assert first.lease_remaining_seconds(
        "account:DU1", owner_id="run-a", epoch=1) == 0
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


def test_portfolio_claim_renews_only_while_same_fence_is_current() -> None:
    store = _Store()
    first = KeeperOwnershipCoordinator(_Client(store, 11))
    second = KeeperOwnershipCoordinator(_Client(store, 12))
    lease = first.acquire_portfolio_admission_lease(
        "account:DU1", owner_id="run-a", ttl_seconds=1)
    assert lease is not None
    renewed = first.renew_portfolio_admission_lease(
        "account:DU1", owner_id="run-a", epoch=lease["epoch"], ttl_seconds=30)
    assert renewed is not None
    assert datetime.fromisoformat(renewed["expires_at"]) > datetime.fromisoformat(
        lease["expires_at"])
    assert second.renew_portfolio_admission_lease(
        "account:DU1", owner_id="run-a", epoch=lease["epoch"]) is None
    first._monotonic_deadlines[("account:DU1", "run-a", lease["epoch"])] = monotonic() - 1
    assert first.renew_portfolio_admission_lease(
        "account:DU1", owner_id="run-a", epoch=lease["epoch"]) is None
    assert not first.portfolio_admission_lease_is_current(
        "account:DU1", owner_id="run-a", epoch=lease["epoch"])
    assert first.release_portfolio_admission_lease(
        "account:DU1", owner_id="run-a", epoch=lease["epoch"])
    replacement = second.acquire_portfolio_admission_lease(
        "account:DU1", owner_id="run-b")
    assert replacement is not None and replacement["epoch"] > lease["epoch"]
    assert first.renew_portfolio_admission_lease(
        "account:DU1", owner_id="run-a", epoch=lease["epoch"]) is None


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
