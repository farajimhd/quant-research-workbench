"""Coordinate saved Review reads without changing the sealed V4 verifier."""
from __future__ import annotations

import asyncio
from contextlib import closing, contextmanager
from threading import Lock

from fastapi import APIRouter, HTTPException
from uuid import UUID

from src.backend.workload_budget import WorkloadBudgetRejected, workload_budget_manager

_scopes: dict[tuple[str, str], tuple[Lock, int]] = {}
_scope_lock = Lock()


@contextmanager
def saved_review_read_scope(client, run_id):
    """One cold audit per authority/run, including history's background reader.

    Keep the sealed reader's context, release, head and TTL checks. Only its
    run-count capacity changes; the existing byte and entry budgets remain.
    The history page can warm up to 32 runs, so eight entries caused churn.
    """
    from src.backend.backtest_v4_saved_review import _V4_CACHE, _V4_PERFORMANCE_CACHE
    from src.backend.typed_backtest_review_core import _client_scope
    key = (_client_scope(client), str(UUID(run_id)))
    with _scope_lock:
        _V4_CACHE.max_sessions = 64
        _V4_PERFORMANCE_CACHE.max_sessions = 64
        lock, users = _scopes.get(key, (Lock(), 0))
        _scopes[key] = (lock, users + 1)
    try:
        with lock:
            yield
    finally:
        with _scope_lock:
            _, users = _scopes[key]
            if users == 1:
                del _scopes[key]
            else:
                _scopes[key] = (lock, users - 1)


def read_review_metadata(run_id):
    from src.backend import historical_runtime_versions as versions
    from src.backend.backtest_v4_saved_review import load_v4_terminal_review_page, _terminal_attestation
    from src.backend.typed_backtest_review_core import _client_scope
    from src.trading_runtime.arte_journal_writer import backtest_v4_operator_client_from_env
    if versions.backend_source_fingerprint() != versions.LOADED_BACKEND_FINGERPRINT:
        raise RuntimeError("Saved review reader source changed after startup; restart the backend")
    with closing(backtest_v4_operator_client_from_env()) as client, saved_review_read_scope(client, run_id):
        page = load_v4_terminal_review_page(client, run_id, metadata_only=True, limit=100)
        attestation = _terminal_attestation(client, run_id, None)
        return {"page": page, "context": attestation["context"], "prefix": attestation["prefix"],
                "scope": _client_scope(client)}


def metadata_still_current(result):
    """Recheck delivery against the exact authority audited by the job.

    This does not certify anything: a mismatch schedules the sealed reader
    again. Only the original reader's fully verified page can be delivered.
    """
    from src.backend import historical_runtime_versions as versions
    from src.backend.typed_backtest_review_core import _head_matches, _client_scope
    from src.trading_runtime.arte_journal_writer import backtest_v4_operator_client_from_env, load_typed_run_context
    if versions.backend_source_fingerprint() != versions.LOADED_BACKEND_FINGERPRINT:
        raise RuntimeError("Saved review reader source changed after startup; restart the backend")
    run_id = result["page"]["run"]["run_id"]
    with closing(backtest_v4_operator_client_from_env()) as client:
        return (_client_scope(client) == result["scope"]
                and load_typed_run_context(client, run_id) == result["context"]
                and _head_matches(client, run_id, result["prefix"]))


class ReviewLoading:
    """Bounded cancellation-safe work, independent of an HTTP request's lifetime.

    A completed page is delivered only after its exact context and terminal
    head are rechecked. Results are removed on delivery, never cached here.
    """
    def __init__(self, reader=read_review_metadata, validator=metadata_still_current, budget=workload_budget_manager,
                 *, wait_seconds=0.25, max_pending=8):
        self.reader, self.validator, self.budget = reader, validator, budget
        self.wait_seconds, self.max_pending = wait_seconds, max_pending
        self.jobs: dict[str, asyncio.Task] = {}
        self.closed = False

    async def _read(self, run_id):
        while not self.closed:
            try:
                async with self.budget.lease("simulation"):
                    # Shutdown must drain the actual thread before releasing
                    # capacity. Disconnecting a poll must not cancel this job.
                    work = asyncio.create_task(asyncio.to_thread(self.reader, run_id))
                    try:
                        return await asyncio.shield(work)
                    except asyncio.CancelledError:
                        await work
                        raise
            except WorkloadBudgetRejected:
                await asyncio.sleep(0.5)
        raise RuntimeError("Saved Review is shutting down")

    async def snapshot(self, run_id):
        if self.closed:
            raise RuntimeError("Saved Review is shutting down")
        job = self.jobs.get(run_id)
        # Drain exceptions rather than retaining stale completed results.
        for old, candidate in list(self.jobs.items()):
            if old != run_id and candidate.done():
                candidate.exception()
                del self.jobs[old]
        if job is not None and job.done():
            try:
                result = job.result()
                if await asyncio.to_thread(self.validator, result):
                    return {"status": "ready", "page": result["page"]}
            finally:
                if self.jobs.get(run_id) is job:
                    del self.jobs[run_id]
            job = None
        if job is None:
            if len(self.jobs) >= self.max_pending:
                return {"status": "queued"}
            job = asyncio.create_task(self._read(run_id))
            self.jobs[run_id] = job
        done, _ = await asyncio.wait((job,), timeout=self.wait_seconds)
        if not done:
            return {"status": "verifying"}
        if self.jobs.get(run_id) is job:
            del self.jobs[run_id]
        result = job.result()
        if await asyncio.to_thread(self.validator, result):
            return {"status": "ready", "page": result["page"]}
        return {"status": "verifying"}

    async def close(self):
        self.closed = True
        await asyncio.gather(*self.jobs.values(), return_exceptions=True)
        self.jobs.clear()


review_loading = ReviewLoading()
router = APIRouter()


@router.get("/api/trading/backtest/runs/{run_id}/v4-review-ready")
async def saved_review_ready(run_id: str):
    try:
        normalized = str(UUID(run_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid Backtest run id") from exc
    try:
        return await review_loading.snapshot(normalized)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
