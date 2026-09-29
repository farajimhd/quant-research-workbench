"""Explicit worker and memory caps for the ticker-local compiler."""
from __future__ import annotations

from dataclasses import dataclass
import os


MAX_LISTING_WORKERS = 64
MAX_IN_FLIGHT_MULTIPLIER = 2
MIN_FREE_GIB_PER_WORKER = 1
HOST_RESERVE_GIB = 32


@dataclass(frozen=True)
class WorkerPlan:
    listing_workers: int
    query_threads_each: int
    max_in_flight: int
    required_free_gib: int


def worker_plan(*, requested: int = 64, available_gib: float,
                logical_cores: int | None = None) -> WorkerPlan:
    """Fail closed if requested parallelism cannot fit CPU and RAM bounds.

    A worker owns one read-only socket and one ticker-local Polars/V7 state.
    The launcher sets POLARS_MAX_THREADS=1 before importing Polars; process
    workers, not nested Polars pools, consume the workstation's logical cores.
    Production width is selected by a representative 16/32/64-worker canary.
    """
    logical = logical_cores or os.cpu_count() or 1
    if (type(requested) is not int or not 1 <= requested <= MAX_LISTING_WORKERS or
            type(logical) is not int or logical < requested or
            available_gib < HOST_RESERVE_GIB +
            requested * MIN_FREE_GIB_PER_WORKER):
        raise ValueError('Requested ticker workers exceed explicit CPU/RAM bounds')
    return WorkerPlan(requested, 1, requested * MAX_IN_FLIGHT_MULTIPLIER,
                      HOST_RESERVE_GIB + requested * MIN_FREE_GIB_PER_WORKER)
