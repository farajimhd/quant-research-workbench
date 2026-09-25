"""Inactive nonblocking activation/dispatch-ACK admission lane.

The caller must first durably publish a typed dispatch-intent fence. This
worker never performs storage I/O on submit and promotes no executable signal
until both Keeper-backed activation and dispatch-ACK receipts complete.
"""
from __future__ import annotations

import queue
import threading
from concurrent.futures import Future, InvalidStateError
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from src.backend.signal_dispatch_typed_cursor import (
    project_dispatch_ack, verify_dispatch_cursor,
)
from src.trading_runtime.arte_activation_projection import project_activation


class ActivationWriter(Protocol):
    def submit(self, projected: Any, *, owner_id: str) -> Future[str]: ...


class AckWriter(Protocol):
    def submit_ack(self, projected: Mapping[str, Any]) -> Future[str]: ...


@dataclass(frozen=True)
class AdmissionBatch:
    intents: Mapping[str, Any]
    deliveries: tuple[Mapping[str, Any], ...]
    owner_id: str
    acknowledged_at: str


class _Receipt(Future[str]):
    def cancel(self) -> bool:
        return False


def _receipt_hash(receipt: Future[str]) -> str:
    value = receipt.result()
    if (not isinstance(value, str) or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        raise ValueError("durable activation or ACK receipt hash is invalid")
    return value


class TypedActivationDispatchAdmission:
    """Single bounded control-plane worker; no live supervisor wiring yet."""

    def __init__(self, activation_writer: ActivationWriter, ack_writer: AckWriter,
                 on_admitted: Callable[[list[dict[str, Any]]], None], *,
                 capacity: int = 64) -> None:
        if type(capacity) is not int or capacity < 1:
            raise ValueError("activation dispatch admission capacity is invalid")
        self._activation_writer = activation_writer
        self._ack_writer = ack_writer
        self._on_admitted = on_admitted
        self._capacity = capacity
        self._queue: queue.Queue[tuple[AdmissionBatch, Future[str]]] = queue.Queue()
        self._pending: set[tuple[str, int]] = set()
        self._lock = threading.Lock()
        self._fatal: BaseException | None = None
        self._closing = False
        self._thread = threading.Thread(target=self._run, name="typed-activation-dispatch", daemon=True)
        self._started = False

    def submit(self, batch: AdmissionBatch) -> Future[str]:
        """O(1) ownership handoff; caller must not mutate nested inputs."""
        if not isinstance(batch, AdmissionBatch):
            raise TypeError("typed activation dispatch batch is required")
        commit = batch.intents.get("commit")
        if not isinstance(commit, Mapping):
            raise ValueError("typed dispatch intent commit is required")
        key = (str(commit.get("session_key") or ""), commit.get("source_batch_sequence"))
        with self._lock:
            if self._closing or self._fatal is not None:
                raise RuntimeError("typed activation dispatch admission is unavailable")
            if key in self._pending:
                raise ValueError("typed dispatch batch is already pending")
            if len(self._pending) >= self._capacity:
                raise RuntimeError("typed activation dispatch capacity is exhausted")
            receipt: Future[str] = _Receipt()
            self._pending.add(key)
            self._queue.put_nowait((batch, receipt))
            if not self._started:
                self._thread.start()
                self._started = True
            return receipt

    def close(self, *, timeout: float = 10.0) -> None:
        with self._lock:
            self._closing = True
        if self._started:
            self._thread.join(timeout)
            if self._thread.is_alive():
                raise RuntimeError("typed activation dispatch worker did not drain")

    def _publish(self, batch: AdmissionBatch) -> str:
        if not batch.owner_id or "\n" in batch.owner_id or "\r" in batch.owner_id:
            raise ValueError("typed activation owner identity is invalid")
        intents = batch.intents
        rows = intents.get("intents")
        if not isinstance(rows, list) or len(rows) != len(batch.deliveries):
            raise ValueError("typed dispatch delivery count differs from intent")
        receipts = []
        for intent, delivery in zip(rows, batch.deliveries, strict=True):
            if any(intent.get(key) != delivery.get(key)
                   for key in ("delivery_id", "run_plan_id", "ticker", "event_id")):
                raise ValueError("typed dispatch delivery differs from intent")
            projected = project_activation(delivery)
            activation_hash = _receipt_hash(self._activation_writer.submit(
                projected, owner_id=batch.owner_id))
            receipts.append({"delivery_id": delivery["delivery_id"],
                             "ack_kind": "activation_durable",
                             "activation_receipt_hash": activation_hash})
        ack = project_dispatch_ack(intents, receipts,
                                   acknowledged_at=batch.acknowledged_at)
        verify_dispatch_cursor(intents, ack)
        ack_hash = _receipt_hash(self._ack_writer.submit_ack(ack))
        if ack_hash != ack["commit"]["content_hash"]:
            raise ValueError("typed dispatch ACK receipt differs from projected fence")
        self._on_admitted([dict(delivery) for delivery in batch.deliveries])
        return ack_hash

    def _run(self) -> None:
        while True:
            try:
                batch, receipt = self._queue.get(timeout=0.1)
            except queue.Empty:
                with self._lock:
                    if self._closing:
                        return
                continue
            key = (str(batch.intents["commit"]["session_key"]),
                   batch.intents["commit"]["source_batch_sequence"])
            try:
                with self._lock:
                    failure = self._fatal
                if failure is not None:
                    raise RuntimeError("typed activation dispatch halted") from failure
                head = self._publish(deepcopy(batch))
            except BaseException as exc:
                with self._lock:
                    self._fatal = exc
                    self._pending.discard(key)
                try:
                    receipt.set_exception(exc)
                except InvalidStateError:
                    pass
            else:
                with self._lock:
                    self._pending.discard(key)
                try:
                    receipt.set_result(head)
                except InvalidStateError:
                    pass
            finally:
                self._queue.task_done()
