"""Bounded asynchronous publication of Keeper-fenced typed activations."""
from __future__ import annotations

from concurrent.futures import Future
from datetime import datetime
from queue import Queue
from threading import Lock, Thread
from typing import Any, Callable
from zoneinfo import ZoneInfo

from src.trading_runtime.arte_activation_projection import (
    ActivationProjection, publish_activation,
)
from src.trading_runtime.arte_journal_schema import (
    journal_permission_preflight, storage_preflight,
)
from src.trading_runtime.keeper_receipts import KeeperReceiptSupervisor
from src.backend.live_activation_session_fence import ActivationSessionFence


class ActivationQueueFull(RuntimeError):
    """No more activation claims may be admitted until receipts complete."""


class _DurableReceipt(Future[str]):
    """A caller cannot cancel the claim-release reconciliation path."""

    def cancel(self) -> bool:
        return False


class ArteActivationWriter:
    """One ordered ClickHouse lane with claims held through durable receipts.

    ``submit`` performs no ClickHouse or Keeper network I/O. The worker obtains
    the claim before publication. Failed or uncertain publication retains its
    Keeper claim for reconciliation.
    """

    def __init__(self, client: Any, keeper: Any, *,
                 session_fence: ActivationSessionFence,
                 capacity: int = 128,
                 preflight: Callable[[Any], None] | None = None,
                 ttl_seconds: float = 30.0,
                 renewal_interval_seconds: float = 10.0) -> None:
        if type(capacity) is not int or capacity < 1:
            raise ValueError("Activation queue capacity must be positive")
        if (ttl_seconds <= 0 or ttl_seconds > 300
                or renewal_interval_seconds <= 0
                or renewal_interval_seconds >= ttl_seconds):
            raise ValueError("Activation Keeper renewal interval or TTL is invalid")
        if session_fence is None or any(not callable(getattr(session_fence, method, None))
                                        for method in ("acquire", "is_current", "release")):
            raise TypeError("Activation writer requires a session-wide Keeper fence")
        if preflight is None:
            storage_preflight(client)
            journal_permission_preflight(client)
        else:
            preflight(client)
        self._client = client
        self._keeper = keeper
        self._session_fence = session_fence
        self._ttl = ttl_seconds
        self._capacity = capacity
        self._lock = Lock()
        self._pending = 0
        self._pending_resources: set[str] = set()
        self._error: BaseException | None = None
        self._closed = False
        self._client_closed = False
        self._queue: Queue[
            tuple[ActivationProjection, str, str, Future[str]] | None
        ] = Queue()
        self._claims = KeeperReceiptSupervisor(
            keeper, capacity=capacity, ttl_seconds=ttl_seconds,
            renewal_interval_seconds=renewal_interval_seconds,
        )
        self._thread = Thread(target=self._run, name="arte-activation-writer", daemon=False)
        self._thread.start()

    @staticmethod
    def _resource(projected: ActivationProjection) -> str:
        delivery = dict(projected.delivery)
        at = datetime.fromisoformat(delivery["event_time"].replace("Z", "+00:00"))
        if at.tzinfo is None:
            raise ValueError("Activation time must be timezone-aware")
        day = at.astimezone(ZoneInfo("America/New_York")).date().isoformat()
        run_plan_id, ticker = delivery["run_plan_id"], delivery["ticker"]
        if not run_plan_id or not ticker:
            raise ValueError("Activation run plan and ticker are required")
        return f"activation:{day}:{run_plan_id}:{ticker}"

    def submit(self, projected: ActivationProjection, *, owner_id: str) -> Future[str]:
        """Return a receipt that resolves only after commit and claim release."""
        if not isinstance(projected, ActivationProjection):
            raise TypeError("Activation submission requires a frozen projection")
        resource = self._resource(projected)
        if not owner_id:
            raise ValueError("Activation requires a Keeper owner identity")
        with self._lock:
            if self._closed:
                raise RuntimeError("Activation writer is closed")
            if self._error is not None:
                raise RuntimeError("Activation writer requires reconciliation") from self._error
            if resource in self._pending_resources:
                raise ValueError("Activation resource is already queued")
            if self._pending >= self._capacity:
                raise ActivationQueueFull("Activation queue is full; stop admission")
            receipt: Future[str] = _DurableReceipt()
            self._pending += 1
            self._pending_resources.add(resource)
            self._queue.put_nowait((projected, resource, owner_id, receipt))
            return receipt

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                self._queue.task_done()
                return
            projected, resource, owner, receipt = item
            session_key = resource.split(":", 3)[1]
            session_epoch: int | None = None
            session_uncertain = False
            try:
                if self._error is not None:
                    raise RuntimeError("Activation writer requires reconciliation")
                session_epoch = self._session_fence.acquire(session_key, owner_id=owner)
                if session_epoch is None:
                    raise RuntimeError("Activation session Keeper fence is contended")
                if not self._session_fence.is_current(
                        session_key, owner_id=owner, epoch=session_epoch):
                    raise RuntimeError("Activation session Keeper fence was lost")
                lease = self._keeper.acquire_portfolio_admission_lease(
                    resource, owner_id=owner, ttl_seconds=self._ttl,
                )
                if lease is None:
                    raise RuntimeError("Activation Keeper claim is contended")
                if (str(lease.get("resource_id")) != resource
                        or str(lease.get("owner_id")) != owner
                        or type(lease.get("epoch")) is not int
                        or lease["epoch"] < 1):
                    raise RuntimeError("Activation Keeper returned a conflicting claim")
                durable: Future[str] = Future()
                completed = self._claims.watch((lease,), durable)
                try:
                    session_uncertain = True
                    commit = publish_activation(
                        self._client, projected, keeper=self._keeper,
                        owner_id=owner, epoch=lease["epoch"],
                    )
                    if not commit:
                        raise RuntimeError("Activation publication returned no commit hash")
                    if not self._session_fence.is_current(
                            session_key, owner_id=owner, epoch=session_epoch):
                        raise RuntimeError("Activation session Keeper fence lost during publication")
                except BaseException as exc:
                    durable.set_exception(exc)
                else:
                    durable.set_result(commit)
                committed_hash = completed.result()
                if not self._session_fence.release(
                        session_key, owner_id=owner, epoch=session_epoch):
                    raise RuntimeError("Activation session Keeper fence release is uncertain")
                session_epoch = None
                session_uncertain = False
                receipt.set_result(committed_hash)
            except BaseException as exc:
                with self._lock:
                    self._error = exc
                receipt.set_exception(exc)
            finally:
                if session_epoch is not None and not session_uncertain:
                    self._session_fence.release(
                        session_key, owner_id=owner, epoch=session_epoch)
                with self._lock:
                    self._pending -= 1
                    self._pending_resources.discard(resource)
                self._queue.task_done()

    def close(self, *, timeout_seconds: float | None = None) -> None:
        """Drain in the control plane; failed claims require reconciliation."""
        with self._lock:
            if not self._closed:
                self._closed = True
                self._queue.put_nowait(None)
        self._thread.join(timeout=timeout_seconds)
        if self._thread.is_alive():
            raise TimeoutError("Activation writer still has pending publication")
        try:
            self._claims.close(timeout_seconds=timeout_seconds)
        finally:
            if not self._client_closed:
                self._client_closed = True
                close_client = getattr(self._client, "close", None)
                if close_client is not None:
                    close_client()
        if self._error is not None:
            raise RuntimeError("Activation writer requires reconciliation") from self._error
