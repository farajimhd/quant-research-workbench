"""Skip-only operational optimization around the immutable v6 bar producer.

Canonical SQL files, aggregation rules, quotas, source ranges and sample schemas
remain pinned by the existing dataset contract. This process-local adapter skips
work only when Quotas.allocate cannot accept any resulting sample, and restores
completed checkpoints before reading their Parquet metadata again. It also omits
an independently grouped coarse resolution once that job cannot publish it.
Each owned launch records the adapter hash separately from the canonical binding.
"""

from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace

from .shards.phase1_contract import FRAMES, file_hash, write_json

COARSE_LITERAL = "[3600000000,86400000000]"


def job_has_room(quota, job, frames, minimum_year=None):
    """Exactly mirror per-class allocation caps; pending writes never justify skipping.

    minimum_year is the last processed coarse end-year, inclusive. All historical
    warm-up ranges are retained when any later year can still publish samples.
    Fine extraction must always pass None because it restarts at earlier years.
    """
    classes = {
        int(row["liquid"])
        for row in job["years"]
        if minimum_year is None or int(row["year"]) >= minimum_year
    }
    with quota.lock:
        return any(
            quota.saved[str(frame)][group]
            < int(quota.targets[str(frame)] * (0.75 if group else 0.25))
            for frame in frames
            for group in classes
        )


def coarse_resolutions(statement, keep):
    """Prune only independent frame groups; kept-frame expressions are byte-for-byte unchanged."""
    keep = tuple(frame for frame in FRAMES[4:] if frame in keep)
    if not keep or keep == FRAMES[4:]:
        return statement  # Preserve the conservative boundary query if neither frame can be proven useful here.
    if statement.count(COARSE_LITERAL) != 1:
        raise ValueError(
            "Canonical coarse frame declaration changed; refuse an unverified SQL rewrite"
        )
    return statement.replace(COARSE_LITERAL, "[" + ",".join(map(str, keep)) + "]")


@contextmanager
def scheduling_runtime(builder, output):
    """Install bounded scheduling hooks in this controller process, then restore them.

    Hooks intentionally live outside the fingerprinted canonical producer. They
    never change a bar expression, alter a target, relabel liquidity, or discard
    an admissible sample. The original OS lock is acquired before Status exists,
    so a duplicate launcher cannot replace the active controller's audit record.
    """
    original_worker, original_query = builder.worker, builder.range_query
    original_quota, original_status = builder.Quotas, builder.Status
    local, lock = threading.local(), threading.Lock()
    counters = dict(
        restored_complete_jobs=0,
        skipped_full_cohort_jobs=0,
        trailing_history_prunes=0,
        pruned_resolution_queries=0,
    )
    adapter_hash = file_hash(Path(__file__))

    def increment(name):
        with lock:
            counters[name] += 1

    class RuntimeStatus(original_status):
        def __init__(self, workers):
            super().__init__(workers)
            path = output / "contract.json"
            wrapper = json.loads(path.read_text()) if path.exists() else {}
            record = dict(
                version="quota-aware-runtime-v1",
                pid=os.getpid(),
                started=time.time(),
                adapter_sha256=adapter_hash,
                canonical_binding=wrapper.get("binding"),
                canonical_builder_sha256=file_hash(Path(builder.__file__)),
                policy="skip only zero-admissible work; preserve warm-up history before any eligible future year",
            )
            write_json(
                output / f"runtime-optimization-{time.time_ns()}-{os.getpid()}.json",
                record,
            )

        def snapshot(self):
            result = super().snapshot()
            with lock:
                result["optimization"] = dict(counters, adapter_sha256=adapter_hash)
            return result

    class RuntimeQuota(original_quota):
        def needed(self, frames):
            if not super().needed(frames):
                return False
            context = getattr(local, "job", None)
            if context is None:
                return True  # Controller admission still obeys the original global quota policy.
            minimum = (
                context.last_year if all(f in FRAMES[4:] for f in frames) else None
            )
            needed = job_has_room(self, context.job, frames, minimum)
            if not needed and minimum is not None and not context.trailing_recorded:
                increment("trailing_history_prunes")
                context.trailing_recorded = True
            return needed

    def worker(
        lane, job, args, plan, binding, authority, quota, budget, status, disk, stop
    ):
        if stop.is_set():
            return False
        path = args.output / "checkpoints" / f"{job['key']}.json"
        if path.exists():
            checkpoint = json.loads(path.read_text())
            if checkpoint["binding"] != binding:
                raise ValueError("Ticker checkpoint belongs to a different campaign")
            if checkpoint.get("complete"):
                increment("restored_complete_jobs")
                return True  # Avoid reloading all seven annual Parquet partitions for a finished ticker.
        if not job_has_room(quota, job, FRAMES):
            increment("skipped_full_cohort_jobs")
            status.update(
                lane, ticker=job["ticker"], phase="skipped: cohort quotas full"
            )
            return True
        local.job = SimpleNamespace(
            job=job, quota=quota, last_year=None, trailing_recorded=False
        )
        try:
            return original_worker(
                lane,
                job,
                args,
                plan,
                binding,
                authority,
                quota,
                budget,
                status,
                disk,
                stop,
            )
        finally:
            local.job = None

    def query(ticker, bounds, rules, first, stop, *, begin_us=None, end_us=None):
        statement = original_query(
            ticker, bounds, rules, first, stop, begin_us=begin_us, end_us=end_us
        )
        context = getattr(local, "job", None)
        if begin_us is not None and context is not None:
            begin_year = datetime.fromtimestamp(begin_us / 1e6, builder.NY).year
            keep = [
                f
                for f in FRAMES[4:]
                if job_has_room(context.quota, context.job, (f,), begin_year)
            ]
            pruned = coarse_resolutions(statement, keep)
            if pruned != statement:
                increment("pruned_resolution_queries")
            context.last_year = datetime.fromtimestamp(end_us / 1e6, builder.NY).year
            return pruned
        return statement

    builder.worker, builder.range_query = worker, query
    builder.Quotas, builder.Status = RuntimeQuota, RuntimeStatus
    try:
        yield
    finally:
        builder.worker, builder.range_query = original_worker, original_query
        builder.Quotas, builder.Status = original_quota, original_status
