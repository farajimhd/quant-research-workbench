from __future__ import annotations

from concurrent.futures import Future
from threading import Event

import pytest

from src.trading_runtime.keeper_receipts import (
    KeeperReceiptCapacity, KeeperReceiptSupervisor,
)


LEASE = {"resource_id": "portfolio-account:DU1", "owner_id": "run-1", "epoch": 3}


class Coordinator:
    def __init__(self) -> None:
        self.renewed = Event()
        self.released = Event()
        self.release_calls: list[tuple[str, str, int]] = []
        self.fail_renewal = False
        self.current = True

    def portfolio_admission_lease_is_current(self, resource, *, owner_id, epoch):
        return self.current

    def renew_portfolio_admission_lease(self, resource, *, owner_id, epoch, ttl_seconds):
        self.renewed.set()
        return None if self.fail_renewal else {
            "resource_id": resource, "owner_id": owner_id, "epoch": epoch,
        }

    def release_portfolio_admission_lease(self, resource, *, owner_id, epoch):
        self.release_calls.append((resource, owner_id, epoch))
        self.released.set()
        return True


def test_claim_is_renewed_off_caller_and_released_only_after_durable_receipt() -> None:
    coordinator = Coordinator()
    supervisor = KeeperReceiptSupervisor(
        coordinator, ttl_seconds=1, renewal_interval_seconds=0.02)
    receipt: Future[str] = Future()
    try:
        completion = supervisor.watch((LEASE,), receipt)
        assert coordinator.renewed.wait(1)
        assert not coordinator.released.is_set() and not completion.done()
        receipt.set_result("committed-batch")
        assert completion.result(timeout=1) == "committed-batch"
        assert coordinator.released.is_set()
        assert coordinator.release_calls == [
            (LEASE["resource_id"], LEASE["owner_id"], LEASE["epoch"])]
    finally:
        if not receipt.done():
            receipt.set_result("committed-batch")
        supervisor.close(timeout_seconds=1)


def test_failed_receipt_keeps_claim_and_stops_new_admission() -> None:
    coordinator = Coordinator()
    supervisor = KeeperReceiptSupervisor(coordinator)
    receipt: Future[str] = Future()
    completion = supervisor.watch((LEASE,), receipt)
    receipt.set_exception(OSError("ClickHouse unavailable"))
    with pytest.raises(OSError, match="ClickHouse unavailable"):
        completion.result(timeout=1)
    assert not coordinator.released.is_set()
    with pytest.raises(RuntimeError, match="failed"):
        supervisor.watch((LEASE,), Future())
    with pytest.raises(RuntimeError, match="requires reconciliation"):
        supervisor.close(timeout_seconds=1)


def test_pending_receipts_are_bounded_without_waiting() -> None:
    coordinator = Coordinator()
    supervisor = KeeperReceiptSupervisor(coordinator, capacity=1)
    receipt: Future[str] = Future()
    try:
        completion = supervisor.watch((LEASE,), receipt)
        with pytest.raises(KeeperReceiptCapacity):
            supervisor.watch((LEASE,), Future())
        receipt.set_result("committed-batch")
        assert completion.result(timeout=1) == "committed-batch"
    finally:
        if not receipt.done():
            receipt.set_result("committed-batch")
        supervisor.close(timeout_seconds=1)


def test_one_claim_cannot_be_released_by_first_of_two_receipts() -> None:
    coordinator = Coordinator()
    supervisor = KeeperReceiptSupervisor(coordinator, capacity=2)
    receipt: Future[str] = Future()
    try:
        completion = supervisor.watch((LEASE,), receipt)
        with pytest.raises(ValueError, match="one combined durable receipt"):
            supervisor.watch((LEASE,), Future())
        receipt.set_result("committed-batch")
        assert completion.result(timeout=1) == "committed-batch"
    finally:
        if not receipt.done():
            receipt.set_result("committed-batch")
        supervisor.close(timeout_seconds=1)


def test_already_committed_receipt_releases_without_waiting_for_renewal() -> None:
    coordinator = Coordinator()
    supervisor = KeeperReceiptSupervisor(
        coordinator, ttl_seconds=30, renewal_interval_seconds=10)
    receipt: Future[str] = Future()
    receipt.set_result("committed-batch")
    try:
        assert supervisor.watch((LEASE,), receipt).result(timeout=1) == "committed-batch"
        assert coordinator.release_calls
    finally:
        supervisor.close(timeout_seconds=1)


def test_receipt_does_not_release_a_lost_keeper_claim() -> None:
    coordinator = Coordinator()
    supervisor = KeeperReceiptSupervisor(coordinator)
    receipt: Future[str] = Future()
    completion = supervisor.watch((LEASE,), receipt)
    coordinator.current = False
    receipt.set_result("committed-batch")
    with pytest.raises(RuntimeError, match="expired before journal release"):
        completion.result(timeout=1)
    assert not coordinator.released.is_set()
    with pytest.raises(RuntimeError, match="requires reconciliation"):
        supervisor.close(timeout_seconds=1)


def test_failed_keeper_renewal_stops_admission_without_releasing() -> None:
    coordinator = Coordinator()
    coordinator.fail_renewal = True
    supervisor = KeeperReceiptSupervisor(
        coordinator, ttl_seconds=1, renewal_interval_seconds=0.02)
    receipt: Future[str] = Future()
    completion = supervisor.watch((LEASE,), receipt)
    with pytest.raises(RuntimeError, match="expired before journal commit"):
        completion.result(timeout=1)
    assert not coordinator.released.is_set()
    with pytest.raises(RuntimeError, match="requires reconciliation"):
        supervisor.close(timeout_seconds=1)
