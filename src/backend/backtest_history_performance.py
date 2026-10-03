"""Bounded, single-flight history summaries from the existing cold V4 reader."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from contextlib import closing
from time import monotonic
from uuid import UUID

from fastapi import APIRouter, HTTPException

from src.backend.workload_budget import WorkloadBudgetRejected, workload_budget_manager


def read_summary(run_id: str) -> dict:
    from src.backend.backtest_review_loading import saved_review_read_scope
    from src.backend.backtest_v4_saved_review import load_cached_v4_performance_report
    from src.trading_runtime.arte_journal_writer import backtest_v4_operator_client_from_env
    with closing(backtest_v4_operator_client_from_env()) as client, saved_review_read_scope(client, run_id):
        page = load_cached_v4_performance_report(client, run_id)
    return {"run_id": page["run_id"], "verified_sequence": page["verified_sequence"],
            "report": {"summary": dict(page["report"]["summary"])}}


def current_heads(run_ids: list[str]) -> dict[str, int]:
    from src.backend import historical_runtime_versions as versions
    from src.trading_runtime.arte_journal_writer import (
        _literal, _rows, backtest_v4_operator_client_from_env,
    )
    if versions.backend_source_fingerprint() != versions.LOADED_BACKEND_FINGERPRINT:
        raise RuntimeError("Saved review reader source changed after startup; restart the backend")
    with closing(backtest_v4_operator_client_from_env()) as client:
        rows = _rows(client, "SELECT run_id,last_sequence,status FROM arte.trading_commit_v4 "
            f"WHERE run_id IN ({','.join(_literal(value) for value in run_ids)}) "
            "ORDER BY run_id,last_sequence DESC,batch_id DESC LIMIT 2 BY run_id FORMAT JSONEachRow")
    heads: dict[str, int] = {}
    for row in rows:
        run_id, sequence = str(row["run_id"]), int(row["last_sequence"])
        if run_id not in run_ids:
            raise RuntimeError("History performance returned an unrequested commit head")
        if run_id in heads:
            if heads[run_id] == sequence:
                raise RuntimeError("History performance has ambiguous commit heads")
            continue
        if row["status"] not in {"completed", "stopped", "failed"}:
            raise RuntimeError("History performance no longer has a terminal commit head")
        heads[run_id] = sequence
    return heads


class HistoryPerformance:
    """One cold reader and at most 32 queued jobs, shared across browser clients.

    Retain only bounded scalar summaries. Every served result rechecks
    its database head and loaded source; cached values never grant authority.
    """
    def __init__(self, reader=read_summary, heads=current_heads, budget=workload_budget_manager):
        self.reader, self.heads, self.budget = reader, heads, budget
        self.entries: OrderedDict[tuple[str, int], dict] = OrderedDict()
        self.worker: asyncio.Task | None = None
        self.closed = False

    async def snapshot(self, requested: list[tuple[str, int]], *, retry_failed=False) -> dict:
        now = monotonic()
        for key, entry in list(self.entries.items()):
            if entry["status"] in {"available", "unavailable"} and key not in requested and now - entry["updated"] > 300:
                del self.entries[key]
        output = []
        for key in requested:
            entry = self.entries.get(key)
            if entry and retry_failed and entry["status"] == "unavailable":
                del self.entries[key]
                entry = None
            if entry is None:
                pending = sum(value["status"] in {"queued", "verifying"} for value in self.entries.values())
                if pending >= 32:
                    output.append({"run_id": key[0], "status": "deferred"})
                    continue
                if len(self.entries) >= 100:
                    removable = next((old for old, value in self.entries.items()
                                      if value["status"] in {"available", "unavailable"}), None)
                    if removable is not None:
                        del self.entries[removable]
                entry = {"run_id": key[0], "status": "queued", "updated": now}
                self.entries[key] = entry
            output.append(entry)
        ready = [entry["run_id"] for entry in output if entry["status"] == "available"]
        if ready:
            heads = await asyncio.to_thread(self.heads, ready)
            for key in requested:
                entry = self.entries.get(key)
                if entry and entry["status"] == "available" and heads.get(key[0]) != key[1]:
                    entry.update(status="unavailable", error="Saved performance head changed; refresh runs.")
                    entry.pop("report", None)
        if not self.closed and (self.worker is None or self.worker.done()):
            self.worker = asyncio.create_task(self._drain())
        return {"rows": [{name: value for name, value in entry.items() if name != "updated"}
                          for entry in output]}

    async def _drain(self):
        while not self.closed:
            selected = next(((key, value) for key, value in self.entries.items()
                             if value["status"] == "queued"), None)
            if selected is None:
                return
            key, entry = selected
            try:
                async with self.budget.lease("simulation"):
                    entry["status"] = "verifying"
                    job = asyncio.create_task(asyncio.to_thread(self.reader, key[0]))
                    try:
                        result = await asyncio.shield(job)
                    except asyncio.CancelledError:
                        # Drain this read before releasing its workload lease.
                        await job
                        raise
                    if result["run_id"] != key[0] or result["verified_sequence"] != key[1]:
                        raise ValueError("Verified performance differs from the requested journal head")
                    entry.update(result, status="available", updated=monotonic())
            except WorkloadBudgetRejected:
                # Capacity is temporary; preserve the queued unit rather than
                # multiplying readers or reporting an absent database result.
                await asyncio.sleep(1)
            except Exception as exc:
                entry.update(status="unavailable", error=str(exc), updated=monotonic())

    async def close(self):
        self.closed = True
        if self.worker is not None:
            await self.worker


history_performance = HistoryPerformance()
router = APIRouter()


@router.get("/api/trading/backtest/history-performance")
async def history_performance_snapshot(run_ids: str, sequences: str, retry_failed: bool = False):
    try:
        ids = [str(UUID(value)) for value in run_ids.split(",")]
        heads = [int(value) for value in sequences.split(",")]
        if not 1 <= len(ids) <= 32 or len(set(ids)) != len(ids) or len(heads) != len(ids) or any(value < 1 for value in heads):
            raise ValueError("History performance requires 1–32 unique runs and positive journal sequences")
        return await history_performance.snapshot(list(zip(ids, heads)), retry_failed=retry_failed)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
