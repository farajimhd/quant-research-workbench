"""Bounded nonblocking handoff for the registered typed dispatch cursor.

This worker is inactive until the live source, activation, ACK, completion,
assignment and broker recovery contracts are wired together. The caller must
provide a verifier for the exact Keeper-attested source commit being dispatched.
"""
from __future__ import annotations

from concurrent.futures import Future
from queue import Queue
from threading import Lock, Thread
from typing import Any, Callable, Mapping

from src.backend.live_signal_journal_preflight import (
    staged_live_signal_storage_preflight,
)
from src.backend.signal_dispatch_insert_dispatch import (
    SignalDispatchInsertDispatch, dispatch_run_id,
)
from src.backend.signal_dispatch_registered_publication import (
    publish_registered_ack, publish_registered_intents,
)
from src.backend.signal_dispatch_typed_cursor import (
    verify_dispatch_cursor, verify_dispatch_intents,
)
from src.trading_runtime.arte_journal_schema import journal_permission_preflight


class _Receipt(Future[str]):
    def cancel(self) -> bool:
        return False


class RegisteredDispatchCursorWriter:
    """One ordered cursor lane; no storage I/O in either submit method."""

    def __init__(
        self, client: Any, dispatch: SignalDispatchInsertDispatch, *,
        session_key: str, configuration_revision_id: str,
        source_commit_verifier: Callable[[Mapping[str, Any]], None],
        preflight: Callable[[Any], None] | None = None,
    ) -> None:
        if not callable(source_commit_verifier):
            raise ValueError("Registered dispatch needs source commit authority")
        self.run_id = dispatch_run_id(session_key, configuration_revision_id)
        if preflight is None:
            staged_live_signal_storage_preflight(client)
            journal_permission_preflight(client)
        else:
            preflight(client)
        gate, _ = dispatch._read(self.run_id)
        if (gate.mode != "open" or gate.active or gate.phase != "intent"
                or gate.sequence != 1 or gate.count != 0):
            raise RuntimeError("Registered dispatch writer needs a fresh session")
        self._client, self._dispatch = client, dispatch
        self._session_key = session_key
        self._configuration_revision_id = configuration_revision_id
        self._source_commit_verifier = source_commit_verifier
        self._lock = Lock()
        self._queue: Queue[tuple[str, Mapping[str, Any], Future[str]] | None] = Queue()
        self._phase = "intent"
        self._sequence = 1
        self._intents: Mapping[str, Any] | None = None
        self._fatal: BaseException | None = None
        self._closed = False
        self._client_closed = False
        self._thread = Thread(target=self._run,
                              name="registered-dispatch-cursor-writer",
                              daemon=False)
        self._thread.start()

    def submit_intents(self, projected: Mapping[str, Any]) -> Future[str]:
        commit = projected.get("commit") if isinstance(projected, Mapping) else None
        if (not isinstance(commit, Mapping)
                or commit.get("session_key") != self._session_key
                or commit.get("configuration_revision_id")
                != self._configuration_revision_id):
            raise ValueError("Registered intent scope differs")
        with self._lock:
            if self._closed or self._fatal is not None:
                raise RuntimeError("Registered dispatch writer is unavailable")
            if self._phase != "intent":
                raise RuntimeError("Registered dispatch intent phase is unavailable")
            if commit.get("source_batch_sequence") != self._sequence:
                raise ValueError("Registered intent sequence differs")
            self._phase = "intent-pending"
            receipt: Future[str] = _Receipt()
            self._queue.put_nowait(("intent", projected, receipt))
            return receipt

    def submit_ack(self, projected: Mapping[str, Any]) -> Future[str]:
        commit = projected.get("commit") if isinstance(projected, Mapping) else None
        with self._lock:
            if self._closed or self._fatal is not None:
                raise RuntimeError("Registered dispatch writer is unavailable")
            if (self._phase != "ack" or self._intents is None
                    or not isinstance(commit, Mapping)
                    or commit.get("session_key") != self._session_key
                    or commit.get("source_batch_sequence") != self._sequence
                    or commit.get("intent_commit_hash")
                    != self._intents["commit"]["content_hash"]):
                raise ValueError("Registered ACK lacks its committed intent predecessor")
            self._phase = "ack-pending"
            receipt: Future[str] = _Receipt()
            self._queue.put_nowait(("ack", projected, receipt))
            return receipt

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                self._queue.task_done()
                return
            kind, projected, receipt = item
            try:
                with self._lock:
                    failure = self._fatal
                    intents = self._intents
                if failure is not None:
                    raise RuntimeError("Registered dispatch needs reconciliation") from failure
                if kind == "intent":
                    verify_dispatch_intents(projected)
                    self._source_commit_verifier(projected)
                    digest = publish_registered_intents(
                        self._client, self._dispatch, run_id=self.run_id,
                        projected=projected)
                elif kind == "ack" and intents is not None:
                    verify_dispatch_cursor(intents, projected)
                    digest = publish_registered_ack(
                        self._client, self._dispatch, run_id=self.run_id,
                        intents=intents, projected=projected)
                else:
                    raise RuntimeError("Registered dispatch worker phase differs")
            except BaseException as exc:
                with self._lock:
                    self._fatal = exc
                receipt.set_exception(exc)
            else:
                with self._lock:
                    if kind == "intent":
                        self._intents = projected
                        self._phase = "ack"
                    else:
                        self._intents = None
                        self._sequence += 1
                        self._phase = "intent"
                receipt.set_result(digest)
            finally:
                self._queue.task_done()

    def close(self, *, timeout_seconds: float | None = None) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                self._queue.put_nowait(None)
        self._thread.join(timeout=timeout_seconds)
        if self._thread.is_alive():
            raise TimeoutError("Registered dispatch writer did not drain")
        if not self._client_closed:
            self._client_closed = True
            close_client = getattr(self._client, "close", None)
            if close_client is not None:
                close_client()
        if self._fatal is not None:
            raise RuntimeError("Registered dispatch writer needs reconciliation") from self._fatal
