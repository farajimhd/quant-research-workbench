from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from time import monotonic
from typing import AsyncIterator


WORKLOAD_BUDGET_SCHEMA_VERSION = 1


class WorkloadBudgetRejected(RuntimeError):
    def __init__(self, lane: str, limit: int) -> None:
        super().__init__(f"Backend {lane} workload capacity is exhausted (limit={limit}).")
        self.lane = lane
        self.limit = limit


@dataclass(slots=True)
class _LaneState:
    active: int
    completed: int
    limit: int
    rejected: int
    semaphore: asyncio.Semaphore
    total_wait_seconds: float


def _env_limit(name: str, default: int) -> int:
    try:
        return max(1, min(int(os.environ.get(name, default)), 256))
    except (TypeError, ValueError):
        return default


def workload_limits() -> dict[str, int]:
    return {
        "commands": _env_limit("BACKEND_COMMAND_CONCURRENCY", 8),
        "discovery": _env_limit("BACKEND_DISCOVERY_CONCURRENCY", 8),
        "charts": _env_limit("BACKEND_CHART_CONCURRENCY", 12),
        "simulation": _env_limit("BACKEND_SIMULATION_CONCURRENCY", 6),
        "offline": _env_limit("BACKEND_OFFLINE_CONCURRENCY", 2),
        "general": _env_limit("BACKEND_GENERAL_CONCURRENCY", 32),
    }


def classify_workload(method: str, path: str) -> str:
    normalized_method = method.strip().upper()
    normalized_path = "/" + path.strip().lower().lstrip("/")
    if any(token in normalized_path for token in ("/replay/", "/backtest/", "/simulation/")):
        return "simulation"
    if normalized_path.endswith("/market-discovery/configuration/materialize"):
        return "commands"
    if normalized_path.endswith("/market-discovery/signal-stream/runtime"):
        # This endpoint reads the already-materialized occurrence journal. It
        # must remain available while heavier scanner discovery work is using
        # the bounded discovery lane.
        return "general"
    if any(token in normalized_path for token in ("chart", "/canvas")):
        return "charts"
    if any(token in normalized_path for token in ("scanner", "market-discovery", "watchlist")):
        return "discovery"
    if any(token in normalized_path for token in ("/build", "/research", "/offline", "/artifact")):
        return "offline"
    if normalized_method in {"POST", "PUT", "PATCH", "DELETE"}:
        return "commands"
    return "general"


class WorkloadBudgetManager:
    def __init__(self, limits: dict[str, int], *, wait_seconds: float = 0.25) -> None:
        self._wait_seconds = max(0.01, min(float(wait_seconds), 30.0))
        self._lanes = {
            lane: _LaneState(
                active=0,
                completed=0,
                limit=max(1, int(limit)),
                rejected=0,
                semaphore=asyncio.Semaphore(max(1, int(limit))),
                total_wait_seconds=0.0,
            )
            for lane, limit in limits.items()
        }

    @asynccontextmanager
    async def lease(self, lane: str) -> AsyncIterator[None]:
        state = self._lanes.get(lane) or self._lanes["general"]
        started = monotonic()
        try:
            await asyncio.wait_for(state.semaphore.acquire(), timeout=self._wait_seconds)
        except TimeoutError as exc:
            state.rejected += 1
            raise WorkloadBudgetRejected(lane, state.limit) from exc
        state.total_wait_seconds += monotonic() - started
        state.active += 1
        try:
            yield
        finally:
            state.active -= 1
            state.completed += 1
            state.semaphore.release()

    def snapshot(self) -> dict[str, object]:
        return {
            "schema_version": WORKLOAD_BUDGET_SCHEMA_VERSION,
            "wait_timeout_seconds": self._wait_seconds,
            "lanes": {
                lane: {
                    "active": state.active,
                    "available": max(0, state.limit - state.active),
                    "completed": state.completed,
                    "limit": state.limit,
                    "rejected": state.rejected,
                    "total_wait_seconds": round(state.total_wait_seconds, 6),
                }
                for lane, state in sorted(self._lanes.items())
            },
        }


workload_budget_manager = WorkloadBudgetManager(workload_limits())
