"""Control-plane Keeper claim hold for asynchronous typed-journal receipts.

The execution caller only registers an already-acquired claim and a receipt.
One bounded worker renews claims while publication is pending and releases
them only after ClickHouse has acknowledged the typed journal commit. A failed
receipt leaves claims held for explicit reconciliation and stops new admission.
"""
from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass
from threading import Condition, Thread
from time import monotonic
from typing import Any, Mapping


class KeeperReceiptCapacity(RuntimeError):
    """The admission coordinator cannot accept another outstanding receipt."""


@dataclass(slots=True)
class _Pending:
    leases: tuple[tuple[str, str, int], ...]
    receipt: Future[str]
    completion: Future[str]
    next_renewal: float


class KeeperReceiptSupervisor:
    """Keep fenced admission claims until their journal receipts are durable.

    Keeper and ClickHouse operations happen only on the worker/control plane.
    The execution thread must acquire claims elsewhere and must stop admission
    if ``watch`` raises. A failed receipt is not a safe release authority.
    """

    def __init__(self, coordinator: Any, *, capacity: int = 128,
                 ttl_seconds: float = 30.0, renewal_interval_seconds: float = 10.0) -> None:
        if (capacity < 1 or ttl_seconds <= 0 or ttl_seconds > 300
                or renewal_interval_seconds <= 0
                or renewal_interval_seconds >= ttl_seconds):
            raise ValueError("Keeper receipt capacity or renewal interval is invalid")
        self._coordinator = coordinator
        self._capacity = capacity
        self._ttl = ttl_seconds
        self._interval = renewal_interval_seconds
        self._condition = Condition()
        self._pending: list[_Pending] = []
        self._error: BaseException | None = None
        self._closed = False
        self._thread = Thread(target=self._run, name="keeper-journal-receipts", daemon=False)
        self._thread.start()

    def watch(self, leases: tuple[Mapping[str, Any], ...],
              receipt: Future[str]) -> Future[str]:
        """Register without waiting for Keeper, ClickHouse, or queue space."""
        if not leases or not isinstance(receipt, Future):
            raise ValueError("Journal receipt and at least one Keeper claim are required")
        identities = tuple((str(row["resource_id"]), str(row["owner_id"]),
                            int(row["epoch"])) for row in leases)
        if (len(set(identities)) != len(identities)
                or any(not resource or not owner or epoch < 1
                       for resource, owner, epoch in identities)
                or len({owner for _, owner, _ in identities}) != 1):
            raise ValueError("Keeper receipt claims must be unique and share one owner")
        completion: Future[str] = Future()
        with self._condition:
            if self._closed:
                raise RuntimeError("Keeper receipt supervisor is closed")
            if self._error is not None:
                raise RuntimeError("Keeper receipt supervisor failed") from self._error
            if len(self._pending) >= self._capacity:
                raise KeeperReceiptCapacity("Keeper receipt capacity exhausted; stop admission")
            held = {claim for pending in self._pending for claim in pending.leases}
            if held.intersection(identities):
                raise ValueError("One Keeper claim requires one combined durable receipt")
            self._pending.append(_Pending(identities, receipt, completion,
                                          monotonic() + self._interval))
            receipt.add_done_callback(lambda _done: self._wake())
            self._condition.notify_all()
        return completion

    def _wake(self) -> None:
        with self._condition:
            self._condition.notify_all()

    def _fail(self, error: BaseException) -> None:
        with self._condition:
            self._error = error
            for pending in self._pending:
                if not pending.completion.done():
                    pending.completion.set_exception(error)
            self._condition.notify_all()

    def _run(self) -> None:
        while True:
            with self._condition:
                if self._error is not None:
                    return
                if self._closed and not self._pending:
                    return
                pending = tuple(self._pending)
                if not pending:
                    self._condition.wait()
                    continue
            now = monotonic()
            for item in pending:
                if item.receipt.done():
                    try:
                        committed = item.receipt.result()
                        if not committed:
                            raise RuntimeError("Typed journal receipt lacks a commit identity")
                        for resource, owner, epoch in item.leases:
                            if not self._coordinator.portfolio_admission_lease_is_current(
                                resource, owner_id=owner, epoch=epoch,
                            ):
                                raise RuntimeError("Keeper claim expired before journal release")
                        for resource, owner, epoch in reversed(item.leases):
                            if not self._coordinator.release_portfolio_admission_lease(
                                resource, owner_id=owner, epoch=epoch,
                            ):
                                raise RuntimeError("Keeper claim changed before journal release")
                    except BaseException as exc:
                        self._fail(exc)
                        return
                    with self._condition:
                        self._pending.remove(item)
                        item.completion.set_result(committed)
                        self._condition.notify_all()
                elif now >= item.next_renewal:
                    try:
                        for resource, owner, epoch in item.leases:
                            renewed = self._coordinator.renew_portfolio_admission_lease(
                                resource, owner_id=owner, epoch=epoch,
                                ttl_seconds=self._ttl,
                            )
                            if renewed is None:
                                raise RuntimeError("Keeper claim expired before journal commit")
                    except BaseException as exc:
                        self._fail(exc)
                        return
                    item.next_renewal = monotonic() + self._interval
            with self._condition:
                if self._pending and self._error is None:
                    if any(item.receipt.done() for item in self._pending):
                        continue
                    remaining = min(item.next_renewal for item in self._pending) - monotonic()
                    self._condition.wait(timeout=max(0.001, remaining))

    def close(self, *, timeout_seconds: float | None = None) -> None:
        """Drain at shutdown; never call this on the realtime execution path."""
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        self._thread.join(timeout=timeout_seconds)
        if self._thread.is_alive():
            raise TimeoutError("Keeper receipt supervisor still has pending commits")
        if self._error is not None:
            raise RuntimeError("Keeper receipt supervisor requires reconciliation") from self._error
