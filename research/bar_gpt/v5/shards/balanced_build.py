"""Restartable balanced campaign over frozen ordinal and timestamp work units.

Fine units use variable, bounded ordinal chunks across sessions. Coarse units
independently aggregate timestamp spans and retain a 63-bar rolling context.
Every publication is immutable and receipt-sealed; restart rebuilds only transient
coarse tails, never adds a receipt twice or overwrites authoritative payloads.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import json
from itertools import islice
import os
from pathlib import Path
import shutil
import threading
import time

import numpy as np

from . import source
from .balanced_plan import VERSION, YEARS, freeze, ticker_bounds
from .build import Status, query_with_memory_recovery
from .phase1_contract import (
    FRAMES,
    SAMPLE_DTYPE,
    digest,
    file_hash,
    seed_for,
    write_json,
)
from .origin_samples import arrays, samples
from .range_sql import range_query
from .sql import clock
from .storage import BAR_DTYPE, Budget, publish, verify_receipt
from research.bar_gpt.v5 import bar_semantics_v1
from research.bar_gpt.v5 import quote_quality_v1
from pipelines.market_sip.events import market_day_sql

NY = ZoneInfo("America/New_York")


def execute_range(c, statement, query_id, stop, status, lane):
    """Retry transient transport failures, preserving the identical bounded query."""
    for attempt in range(3):
        try:
            return query_with_memory_recovery(
                c, statement, query_id, stop, status, lane, allow_empty=True
            )
        except (ConnectionError, TimeoutError, OSError):
            if attempt == 2:
                raise
            with status.lock:
                status.retries += 1
            status.update(lane, phase="retrying query connection")
            if stop.wait(2**attempt):
                return None


class Quotas:
    """Reserve saved sample counts atomically by timeframe and liquidity class.

    Daily may occupy less space. The other five frames receive equal caps.
    Insufficient source inventory is reported rather than filled with duplicates.
    Payload and receipt overhead share the separate hard byte budget.
    """

    def __init__(self, target, daily_fraction=0.03):
        capacity = (
            int(target * 0.98) // SAMPLE_DTYPE.itemsize
        )  # Reserve 2% for headers/receipts/plan.
        daily = int(capacity * daily_fraction)
        common = (capacity - daily) // 5
        self.targets = {str(f): (daily if f == FRAMES[-1] else common) for f in FRAMES}
        self.saved = {str(f): [0, 0] for f in FRAMES}  # Index 0=less liquid, 1=liquid.
        self.pending = {str(f): [0, 0] for f in FRAMES}
        self.lock = threading.RLock()

    def allocate(self, data, liquid):
        """Take a uniform subset fitting the remaining 25/75 class quota."""
        frame = str(int(data["timeframe_us"][0]))
        with self.lock:
            target = int(self.targets[frame] * (0.75 if liquid else 0.25))
            remaining = target - self.saved[frame][liquid] - self.pending[frame][liquid]
            count = min(len(data), max(0, remaining))
            self.pending[frame][liquid] += count
        if count == len(data):
            return data
        chosen = np.random.default_rng(
            seed_for(int(data["end_us"][0, 0]), frame)
        ).choice(len(data), count, replace=False)
        return data[np.sort(chosen)]

    def finish(self, frame, liquid, count, accepted):
        with self.lock:
            self.pending[str(frame)][liquid] -= count
            if accepted:
                self.saved[str(frame)][liquid] += count

    def needed(self, frames):
        with self.lock:
            return any(
                sum(self.saved[str(f)]) < self.targets[str(f)] - 2 for f in frames
            )

    def snapshot(self):
        with self.lock:
            return dict(
                targets=dict(self.targets),
                saved={k: list(v) for k, v in self.saved.items()},
            )


def write_samples(
    args, job, unit, data, binding, quotas, budget, status, disk, pending
):
    """Dispatch bounded asynchronous writes, split by final year and liquidity.

    The directory shape preserves the existing chart's ticker-year navigation.
    A deterministic unit ID is the restart key; data is never merged by a glob.
    """
    if not len(data):
        return
    frame = int(data["timeframe_us"][0])
    # A chunk may cross years. Cohorts use the sample-end ticker-year activity.
    years = np.asarray(
        [datetime.fromtimestamp(int(t) / 1e6, NY).year for t in data["end_us"][:, -1]]
    )
    activity = {r["year"]: r["liquid"] for r in job["years"]}
    for year in np.unique(years):
        liquid = activity.get(int(year), 0)
        path = args.output / "jobs" / f"{year}-{job['key']}" / f"{unit}-{frame}"
        existing = verify_receipt(path)
        if existing:
            continue
        selected = quotas.allocate(data[years == year], liquid)
        if not len(selected):
            continue
        metadata = dict(
            identity=digest([binding, job["ticker"], unit, frame, int(year)]),
            binding=binding,
            ticker=job["ticker"],
            year=int(year),
            liquid=liquid,
            unit=unit,
            day=datetime.fromtimestamp(int(selected["end_us"][0, -1]) / 1e6, NY)
            .date()
            .isoformat(),
            origin_fraction=1.0 if frame in FRAMES[4:] else 0.2,
        )

        def commit(path=path, data=selected, metadata=metadata, liquid=liquid):
            accepted = False
            try:
                receipt = publish(
                    path,
                    data,
                    np.empty(0, dtype=BAR_DTYPE),
                    metadata,
                    budget,
                    args.shard_mib * 1024**2,
                )
                accepted = receipt is not None
                if accepted:
                    status.accepted(receipt)
                return accepted
            finally:
                quotas.finish(frame, liquid, len(data), accepted)

        pending.append(disk.submit(commit))
        # Each extraction lane may queue two small publications; global disk
        # concurrency is separately bounded by --io-workers.
        if len(pending) >= 2 and not pending.pop(0).result():
            raise RuntimeError("Hard byte budget reached before sample quotas")


def worker(
    lane, job, args, plan, binding, authority, quotas, budget, status, disk, stop
):
    """Execute one ticker chronologically; raw market events never leave CH."""
    bounds = ticker_bounds(args.output, job)
    if not bounds:
        raise ValueError("Frozen ticker job has no ordinal bounds")
    rules = authority["rules"]
    actions = [
        r
        for r in authority["corporate_actions"]["rows"]
        if r["provider_ticker"] == job["ticker"]
    ]
    c = source.client(args.endpoint, args.query_memory_gib)
    pending = []
    checkpoint_path = args.output / "checkpoints" / f"{job['key']}.json"
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint = (
        json.loads(checkpoint_path.read_text())
        if checkpoint_path.exists()
        else dict(binding=binding, coarse_offset=0, consumed_until=0)
    )
    if checkpoint["binding"] != binding:
        raise ValueError("Ticker checkpoint belongs to a different campaign")
    if checkpoint.get("complete"):
        source.close(c)
        return True

    def checkpoint_commit():
        # Commit the cursor only after every corresponding payload receipt is
        # durable. Replaying a partially written unit is safe and deterministic.
        for future in pending:
            if not future.result():
                raise RuntimeError("Publication could not reserve its byte budget")
        pending.clear()
        checkpoint["tail"] = {
            str(f): [a.tolist() for a in row] for f, row in tail.items()
        }
        write_json(checkpoint_path, checkpoint)

    status.update(
        lane,
        ticker=job["ticker"],
        phase="coarse timestamp spans",
        done=0,
        total=len(bounds),
    )
    tail = {
        int(f): (
            np.asarray(row[0], dtype=np.float32),
            np.asarray(row[1], dtype=np.uint8),
            np.asarray(row[2], dtype=np.int64),
        )
        for f, row in checkpoint.get("tail", {}).items()
    }
    try:
        # Direct timestamp aggregation is separate from ordinal fine chunks.
        # A small date span bounds dense-ticker query latency and memory.
        for offset in range(checkpoint["coarse_offset"], len(bounds), args.coarse_days):
            if stop.is_set() or not quotas.needed(FRAMES[4:]):
                break
            group = bounds[offset : offset + args.coarse_days]
            unit = f"coarse-{group[0]['source_date']}-{group[-1]['source_date']}"
            statement = range_query(
                job["ticker"],
                bounds,
                rules,
                int(group[0]["first_ordinal"]),
                int(group[-1]["next_ordinal"]),
                begin_us=clock(group[0]["source_date"], 4),
                end_us=clock(group[-1]["source_date"], 20),
            )
            output = execute_range(
                c,
                statement,
                "bargpt-" + digest([job["ticker"], unit])[:24],
                stop,
                status,
                lane,
            )
            if output is None:
                return False
            for frame in FRAMES[4:]:
                bars = output.filter(output["resolution"] == frame)
                if not len(bars):
                    continue
                v, m, t = arrays(bars)
                data, tail[frame] = samples(
                    v,
                    m,
                    t,
                    frame,
                    seed_for(job["ticker"], unit, frame),
                    actions,
                    fraction=1.0,
                    previous=tail.get(frame, ()),
                )
                write_samples(
                    args,
                    job,
                    unit,
                    data,
                    binding,
                    quotas,
                    budget,
                    status,
                    disk,
                    pending,
                )
            status.update(
                lane,
                done=min(offset + len(group), len(bounds)),
                day=group[-1]["source_date"],
            )
            checkpoint["coarse_offset"] = offset + len(group)
            checkpoint_commit()
            if args.pilot_units and offset // args.coarse_days + 1 >= args.pilot_units:
                break

        # Each ticker-year has a frozen chunk layout. Adaptive size is based on
        # summary event density, not future returns, labels, or a scanner.
        for year in job["years"]:
            if stop.is_set() or not quotas.needed(FRAMES[:4]):
                break
            first, limit = int(year["first_ordinal"]), int(year["next_ordinal"])
            density = year["events"] / max(1, year["days"]) / (16 * 3600)
            size = min(
                args.max_chunk_events,
                max(args.chunk_events, int(density * args.chunk_minutes * 60)),
            )
            status.update(
                lane,
                phase=f"ordinal chunks ({size:,} events)",
                done=0,
                total=(limit - first + size - 1) // size,
            )
            # Random offset avoids always starting at the same session clock;
            # fixed nonoverlapping intervals prevent identical chunk work.
            origin = max(
                checkpoint["consumed_until"],
                first
                + seed_for(args.seed, job["ticker"], year["year"], "offset")
                % min(size, max(1, limit - first)),
            )
            count = 0
            while origin < limit and not stop.is_set() and quotas.needed(FRAMES[:4]):
                end = min(origin + size, max(int(r["next_ordinal"]) for r in bounds))
                unit = f"fine-{origin}-{end}"
                if end <= origin:
                    break
                statement = range_query(job["ticker"], bounds, rules, origin, end)
                output = execute_range(
                    c,
                    statement,
                    "bargpt-" + digest([job["ticker"], unit])[:24],
                    stop,
                    status,
                    lane,
                )
                if output is None:
                    return False
                for frame in FRAMES[:4]:
                    bars = output.filter(output["resolution"] == frame)
                    if not len(bars):
                        continue
                    data, _ = samples(
                        *arrays(bars),
                        frame,
                        seed_for(args.seed, job["ticker"], unit, frame),
                        actions,
                    )
                    write_samples(
                        args,
                        job,
                        unit,
                        data,
                        binding,
                        quotas,
                        budget,
                        status,
                        disk,
                        pending,
                    )
                count += 1
                status.update(lane, done=count, day=str(year["year"]))
                origin = end
                checkpoint["consumed_until"] = origin
                checkpoint_commit()
                if args.pilot_units and count >= args.pilot_units:
                    break
        for future in pending:
            if not future.result():
                raise RuntimeError("Publication could not reserve its byte budget")
        if not stop.is_set():
            checkpoint["complete"] = True
            checkpoint_commit()
    finally:
        source.close(c)
    return bool(checkpoint.get("complete"))


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("D:/TradingML/runtimes/bar_gpt/shards-2019-2025-v6"),
    )
    parser.add_argument("--target-bytes", type=int, default=10**12)
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--io-workers", type=int, default=16)
    parser.add_argument("--chunk-events", type=int, default=20000)
    parser.add_argument("--max-chunk-events", type=int, default=2000000)
    parser.add_argument("--chunk-minutes", type=int, default=128)
    parser.add_argument("--coarse-days", type=int, default=20)
    parser.add_argument("--query-memory-gib", type=float, default=1)
    parser.add_argument("--shard-mib", type=int, default=32)
    parser.add_argument("--daily-fraction", type=float, default=0.03)
    parser.add_argument("--seed", type=int, default=20261107)
    parser.add_argument("--endpoint")
    parser.add_argument("--source-revision", default="working-tree")
    parser.add_argument("--tickers", default="")
    parser.add_argument("--pilot-units", type=int, default=0)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Seal the contract for an audited migration; do not extract",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = arguments(argv)
    if not 1 <= args.workers <= 64 or not 1 <= args.io_workers <= 32:
        raise ValueError("Bounded query workers 1..64 and I/O workers 1..32 required")
    if (
        args.chunk_events < 1000
        or args.max_chunk_events < args.chunk_events
        or not 1 <= args.coarse_days <= 100
    ):
        raise ValueError("Invalid chunk/span limits")
    args.output.mkdir(parents=True, exist_ok=True)
    # An OS-held lock releases on process exit, including crashes. A stale PID
    # file must never allow two controllers to publish into the same dataset.
    import msvcrt

    owner = (args.output / "controller.lock").open("a+b")
    owner.seek(0)
    if owner.read(1) == b"":
        owner.write(b"0")
        owner.flush()
    owner.seek(0)
    msvcrt.locking(owner.fileno(), msvcrt.LK_NBLCK, 1)
    if shutil.disk_usage(args.output).free < 100 * 10**9:
        raise RuntimeError("Required 100 GB free-space reserve is unavailable")
    status, budget, quotas = (
        Status(args.workers),
        Budget(args.target_bytes),
        Quotas(args.target_bytes, args.daily_fraction),
    )
    stop = threading.Event()
    began = time.time()
    baseline_bytes, baseline_clock = 0, began
    failed, completed, active = [], 0, {}
    state = "planning"
    plan = None

    def snapshot(done=0, total=112):
        elapsed = time.time() - began
        if (args.output / "STOP").exists():
            stop.set()
        if shutil.disk_usage(args.output).free < 100 * 10**9:
            stop.set()
        snapshot = dict(
            status.snapshot(),
            state=state,
            bytes=budget.used,
            target_bytes=budget.limit,
            updated=time.time(),
            elapsed_seconds=elapsed,
            jobs_completed=completed,
            jobs_active=len(active),
            jobs_queued=max(
                0, (len(plan["jobs"]) if plan else 0) - completed - len(active)
            ),
            jobs_total=len(plan["jobs"]) if plan else 0,
            failed=list(failed),
            quotas=quotas.snapshot(),
            plan_done=done,
            plan_total=total,
            configuration=dict(
                workers=args.workers,
                io_workers=args.io_workers,
                chunk_events=args.chunk_events,
                max_chunk_events=args.max_chunk_events,
                chunk_minutes=args.chunk_minutes,
                coarse_days=args.coarse_days,
            ),
            bytes_per_second=max(0, budget.used - baseline_bytes)
            / max(1, time.time() - baseline_clock),
        )
        write_json(args.output / "progress.json", snapshot)

    snapshot()
    plan = freeze(
        args.output,
        args.endpoint,
        snapshot,
        tuple(filter(None, args.tickers.split(","))),
    )
    if args.plan_only:
        state = "plan_complete"
        snapshot(112, 112)
        return 0
    # Existing app navigation consumes this compact ticker-year list.
    write_json(
        args.output / "plan.json",
        [
            dict(ticker=j["ticker"], year=y["year"])
            for j in plan["jobs"]
            for y in j["years"]
        ],
    )
    c = source.client(args.endpoint)
    try:
        authority = source.preflight(c)
        authority["corporate_actions"] = source.split_authority(c, YEARS)
        args.endpoint = c.base_url  # Pin all lanes to the endpoint verified here.
    finally:
        source.close(c)
    contract = dict(
        version=VERSION,
        plan_hash=plan["hash"],
        years=YEARS,
        authority=authority,
        format="Float32[64,18] plus masks, UTC clocks, timeframe and split",
        policy="20% fine-frame sliding origins; hourly/daily sliding; cross-session; sample-end split basis",
        configuration={
            k: v
            for k, v in vars(args).items()
            if k
            not in (
                "output",
                "endpoint",
                "source_revision",
                "plan_only",
                "prepare_only",
            )
        },
        code={
            p.name: file_hash(p)
            for p in Path(__file__).parent.glob("*.py")
            if not p.name.startswith("test_")
        },
    )
    contract["code"].update(
        {
            module.__name__: file_hash(Path(module.__file__))
            for module in (bar_semantics_v1, market_day_sql, quote_quality_v1)
        }
    )
    binding = digest(contract)
    path = args.output / "contract.json"
    if path.exists() and json.loads(path.read_text())["binding"] != binding:
        raise ValueError("Campaign contract changed; use a new output root")
    write_json(path, dict(binding=binding, contract=contract))
    if args.prepare_only:
        state = "migration_ready"
        snapshot()
        return 0
    write_json(
        args.output / "launch.json",
        dict(source_revision=args.source_revision, pid=os.getpid(), started=began),
    )
    # Restore only sealed publications; counts are never inferred from stale logs.
    state = "resuming"
    paths = args.output.glob("jobs/*/*/receipt.json")
    with ThreadPoolExecutor(args.io_workers) as readers:
        while batch := list(islice(paths, 512)):
            for r in readers.map(
                lambda p: verify_receipt(p.parent, hashes=False), batch
            ):
                if r["binding"] != binding:
                    raise ValueError("Foreign receipt in campaign")
                status.accepted(r, reused=True)
                frame = str(next(int(f) for f, n in r["frames"].items() if n))
                quotas.saved[frame][r["liquid"]] += r["samples"]
            snapshot()
    budget.used = sum(p.stat().st_size for p in args.output.rglob("*") if p.is_file())
    baseline_bytes, baseline_clock = budget.used, time.time()
    state = "running"
    jobs = iter(plan["jobs"])
    with ThreadPoolExecutor(args.io_workers, thread_name_prefix="shard-io") as disk:
        with ThreadPoolExecutor(args.workers, thread_name_prefix="shard-query") as pool:

            def admit(lane):
                job = next(jobs, None)
                if job is not None and not stop.is_set() and quotas.needed(FRAMES):
                    active[
                        pool.submit(
                            worker,
                            lane,
                            job,
                            args,
                            plan,
                            binding,
                            authority,
                            quotas,
                            budget,
                            status,
                            disk,
                            stop,
                        )
                    ] = lane

            for lane in range(args.workers):
                admit(lane)
            while active:
                if (args.output / "STOP").exists():
                    state = "draining"
                    stop.set()
                try:
                    ready, _ = wait(active, timeout=1, return_when=FIRST_COMPLETED)
                except KeyboardInterrupt:
                    state = "draining"
                    stop.set()
                    continue
                for future in ready:
                    lane = active.pop(future)
                    try:
                        done = future.result()
                        completed += int(done)
                        status.update(lane, phase="completed" if done else "stopped")
                    except Exception as error:
                        failed.append(dict(worker=lane + 1, error=str(error)[:1600]))
                        status.update(lane, phase="failed")
                        stop.set()
                        state = "draining"
                    admit(lane)
                snapshot()
    state = (
        "failed"
        if failed
        else (
            "stopped"
            if stop.is_set()
            else (
                "scope_complete"
                if args.pilot_units
                else (
                    "quotas_reached"
                    if not quotas.needed(FRAMES)
                    else "inventory_exhausted"
                )
            )
        )
    )
    snapshot()
    write_json(
        args.output / "result.json",
        dict(
            state=state,
            bytes=budget.used,
            samples=status.samples,
            frames=status.frames,
            failed=failed,
        ),
    )
    return int(bool(failed))
