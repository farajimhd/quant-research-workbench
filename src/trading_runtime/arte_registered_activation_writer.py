"""Bounded asynchronous Strategy 1 activation publication.

This is a control-plane writer, not a live-runtime enablement switch. The
execution thread only submits frozen inputs and receives a Future; it never
waits for ClickHouse or Keeper. The activation dispatch admission lane may
wait for the receipt on its own worker before publishing a durable ACK.
"""
from __future__ import annotations

from concurrent.futures import Future
from datetime import date, datetime
from queue import Queue
from threading import Lock, Thread
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

from src.trading_runtime.arte_activation_insert_dispatch import (
    ActivationInsertDispatch, publish_registered_activation,
)
from src.trading_runtime.arte_activation_projection import (
    ActivationProjection, strategy_one_activation_run_id,
)


_NEW_YORK = ZoneInfo("America/New_York")


class RegisteredActivationQueueFull(RuntimeError):
    """The bounded handoff is saturated; no activation may be dropped."""


class _Receipt(Future[str]):
    def cancel(self) -> bool:
        return False


class RegisteredActivationWriter:
    """One ordered I/O lane for previously initialized Strategy 1 run scopes."""

    def __init__(self, client: Any, dispatch: ActivationInsertDispatch, *,
                 session_date: date, mode: str,
                 run_plan_ids: Iterable[str], capacity: int = 128,
                 preflight: Callable[[Any], None] | None = None) -> None:
        if type(session_date) is not date or mode not in {"paper", "live"}:
            raise ValueError("Registered activation session or mode is invalid")
        if type(capacity) is not int or capacity < 1:
            raise ValueError("Registered activation capacity is invalid")
        if (not isinstance(dispatch, ActivationInsertDispatch)
                or not dispatch.strategy_one or not callable(preflight)):
            raise ValueError(
                "Strategy 1 activation needs isolated dispatch and exact preflight")
        plans = tuple(run_plan_ids)
        if not plans or len(plans) != len(set(plans)):
            raise ValueError("Registered activation plans are missing or duplicate")
        self._run_ids = {plan: strategy_one_activation_run_id(
            session_date, mode=mode, run_plan_id=plan) for plan in plans}
        preflight(client)
        for run_id in self._run_ids.values():
            dispatch.assert_open(run_id)
        self._client = client
        self._dispatch = dispatch
        self._session_date = session_date
        self._capacity = capacity
        self._queue: Queue[tuple[ActivationProjection, str, str, Future[str]] | None] = Queue()
        self._lock = Lock()
        self._pending = 0
        self._pending_watches: set[tuple[str, str]] = set()
        self._fatal: BaseException | None = None
        self._closed = False
        self._client_closed = False
        self._thread = Thread(target=self._run,
                              name="arte-registered-activation-writer",
                              daemon=False)
        self._thread.start()

    def submit(self, projected: ActivationProjection, *, owner_id: str) -> Future[str]:
        """Handoff only: no Keeper or ClickHouse calls on this path."""
        if not isinstance(projected, ActivationProjection):
            raise TypeError("Registered activation requires a frozen projection")
        if not owner_id or any(char in owner_id for char in "\r\n\x00"):
            raise ValueError("Registered activation owner identity is invalid")
        delivery = dict(projected.delivery)
        plan = delivery.get("run_plan_id", "")
        delivery_id = delivery.get("delivery_id", "")
        ticker = delivery.get("ticker", "")
        if plan not in self._run_ids or not delivery_id or not ticker:
            raise ValueError("Registered activation plan, ticker or delivery is not admitted")
        try:
            event_time = datetime.fromisoformat(
                delivery["event_time"].replace("Z", "+00:00"))
        except (KeyError, AttributeError, ValueError) as exc:
            raise ValueError("Registered activation event time is invalid") from exc
        if event_time.tzinfo is None or event_time.astimezone(
                _NEW_YORK).date() != self._session_date:
            raise ValueError("Registered activation is outside the writer session")
        key = (plan, ticker)
        with self._lock:
            if self._closed or self._fatal is not None:
                raise RuntimeError("Registered activation writer is unavailable")
            if key in self._pending_watches:
                raise ValueError("Registered activation watch is already queued")
            if self._pending >= self._capacity:
                raise RegisteredActivationQueueFull(
                    "Registered activation handoff capacity is exhausted")
            receipt: Future[str] = _Receipt()
            self._pending += 1
            self._pending_watches.add(key)
            self._queue.put_nowait((projected, self._run_ids[plan], delivery_id,
                                    receipt))
            return receipt

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                self._queue.task_done()
                return
            projected, run_id, delivery_id, receipt = item
            try:
                with self._lock:
                    failure = self._fatal
                if failure is not None:
                    raise RuntimeError(
                        "Registered activation writer requires reconciliation") from failure
                content_hash = publish_registered_activation(
                    self._client, self._dispatch, projected, run_id=run_id)
            except BaseException as exc:
                with self._lock:
                    self._fatal = exc
                receipt.set_exception(exc)
            else:
                receipt.set_result(content_hash)
            finally:
                with self._lock:
                    self._pending -= 1
                    delivery = dict(projected.delivery)
                    self._pending_watches.discard(
                        (delivery["run_plan_id"], delivery["ticker"]))
                self._queue.task_done()

    def close(self, *, timeout_seconds: float | None = None) -> None:
        """Control-plane drain; failed publication remains pending in Keeper."""
        with self._lock:
            if not self._closed:
                self._closed = True
                self._queue.put_nowait(None)
        self._thread.join(timeout=timeout_seconds)
        if self._thread.is_alive():
            raise TimeoutError("Registered activation writer has not drained")
        if not self._client_closed:
            self._client_closed = True
            close_client = getattr(self._client, "close", None)
            if close_client is not None:
                close_client()
        if self._fatal is not None:
            raise RuntimeError(
                "Registered activation writer requires reconciliation") from self._fatal
