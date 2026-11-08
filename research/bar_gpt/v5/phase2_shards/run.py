"""Plan, resume and publish phase-two streams without touching the GPU trainer.

Run with ``python -B -m research.bar_gpt.v5.phase2_shards.run --help``.
All generated files must be under the selected machine's TradingML runtimes.
The execution cap and immutable inventory both default to four decimal TB.
"""

import argparse
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor, wait, FIRST_COMPLETED
import json
import multiprocessing as mp
import os
from pathlib import Path
import shutil
import signal
import sys
import time

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ.setdefault("POLARS_MAX_THREADS", "4")

import psutil

from .contract import CAPACITY, write_json
from .plan import freeze, inventory_jobs
from .producer import Gate, Worker, NoFullBlock, EndOfStream
from .ledger_process import LedgerProcess
from .process_pool import executor, produce, ProducedUnit
from .telemetry import Telemetry

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"


def arguments():
    """Complete defaults; pilot bounds affect execution, never source identity."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", default="D:/TradingML/runtimes/bar_gpt/phase2-streams-v1"
    )
    parser.add_argument(
        "--parent", default="D:/TradingML/runtimes/bar_gpt/shards-2019-2025-v6"
    )
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--target-bytes", type=int, default=4_000_000_000_000)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--io-workers", type=int, default=2)
    parser.add_argument("--process-workers", type=int, default=0)
    parser.add_argument("--query-memory-gib", type=float, default=3)
    parser.add_argument("--reserve-memory-gib", type=float, default=96)
    parser.add_argument("--reserve-disk-gib", type=float, default=150)
    parser.add_argument("--chunk-events", type=int, default=2_000_000)
    parser.add_argument("--query-threads", type=int, default=4)
    parser.add_argument("--python-switch-ms", type=float, default=1.0)
    parser.add_argument("--fine-warmup-events", type=int, default=250_000)
    parser.add_argument("--coarse-warmup-events", type=int, default=32_000_000)
    parser.add_argument("--fine-warmup-sessions", type=int, default=120)
    parser.add_argument("--max-units", type=int, default=0)
    parser.add_argument(
        "--only-stream",
        action="append",
        default=[],
        help="Optional pilot ticker:year; immutable plan remains complete",
    )
    parser.add_argument("--endpoint")
    parser.add_argument(
        "--wandb-mode", choices=("online", "offline", "disabled"), default="online"
    )
    parser.add_argument("--wandb-project", default="bargpt-phase2-shards-v1")
    return parser.parse_args()


def run(args):
    """Coordinate bounded producers and I/O; parent is the only ledger writer."""
    root = Path(args.output).resolve()
    authority = Path("D:/TradingML/runtimes").resolve()
    if not root.is_relative_to(authority) or not authority.exists():
        raise ValueError("Required D:/TradingML/runtimes authority is unavailable")
    if not 0 < args.target_bytes <= CAPACITY or min(args.workers, args.io_workers) < 1:
        raise ValueError("Invalid cap or worker count")
    if (
        not 1 <= args.query_threads <= 8
        or min(args.fine_warmup_events, args.coarse_warmup_events) < 1
    ):
        raise ValueError("Query threads must be 1..8 and warmup budgets positive")
    if not 0.1 <= args.python_switch_ms <= 10:
        raise ValueError("Python switch interval must be 0.1..10 milliseconds")
    if not 0 <= args.process_workers <= args.workers:
        raise ValueError("Process workers must be between zero and admitted lanes")
    if args.process_workers * (args.query_memory_gib + 0.25) > 128:
        raise ValueError("Extraction process query/response allowances exceed 128 GiB")
    # Shorter generator-only GIL slices prevent metadata/serialization threads
    # delaying the coordinator after each native I/O call. No trainer is changed.
    sys.setswitchinterval(args.python_switch_ms / 1000)
    root.mkdir(parents=True, exist_ok=True)
    # Exclusive file handle survives stale files but prevents duplicate producers.
    # Windows locks release on process exit; never infer ownership from a PID file.
    import msvcrt

    lock = (root / "producer.lock").open("a+b")
    if lock.tell() == 0:
        lock.write(b"0")
        lock.flush()
    lock.seek(0)
    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    plan = freeze(root, args.parent, args.source_revision, args.endpoint)
    # Prime the shared immutable index once; concurrent cache misses would each
    # parse the same large JSON before the first worker populated the LRU cache.
    inventory_jobs(str(root))
    layout = root / "layout.json"
    if layout.exists():
        if json.loads(layout.read_text())["chunk_events"] != args.chunk_events:
            raise ValueError(
                "Chunk layout is immutable; resume with the original chunk-events"
            )
    else:
        write_json(layout, dict(chunk_events=args.chunk_events, plan=plan["hash"]))
    execution = dict(
        **vars(args), pid=os.getpid(), started=time.time(), plan=plan["hash"]
    )
    history = root / "executions"
    history.mkdir(exist_ok=True)
    write_json(
        history / f"{int(execution['started'] * 1000)}-{os.getpid()}.json", execution
    )
    write_json(root / "execution.json", execution)
    catalog = LedgerProcess(root)
    catalog.recover()
    telemetry = Telemetry(root, execution, args.wandb_mode, args.wandb_project)
    gate = Gate(
        root,
        args.query_memory_gib,
        args.reserve_memory_gib,
        args.reserve_disk_gib,
        args.query_threads,
        args.fine_warmup_events,
        args.coarse_warmup_events,
        args.fine_warmup_sessions,
    )
    context = mp.get_context("spawn")
    # Process mode has at most one live query per process. Its aggregate bounded
    # allowance is checked above; each gate also preserves actual free headroom.
    gate.cancel = context.Event()
    io_slots = context.BoundedSemaphore(args.io_workers)
    signal.signal(signal.SIGINT, lambda *_: gate.cancel.set())
    signal.signal(signal.SIGTERM, lambda *_: gate.cancel.set())
    streams = [
        s
        for s in plan["streams"]
        if not args.only_stream or f"{s['ticker']}:{s['year']}" in args.only_stream
    ]
    # Reuse active lanes across adjacent ranges. Idle lane metadata is bounded
    # by worker count; do not retain the full market history in Python objects.
    queues = {
        liquid: [s for s in streams if s["liquid"] == liquid] for liquid in (0, 1)
    }
    positions = {0: 0, 1: 0}
    active, publications, lanes = {}, {}, {}
    reusable = OrderedDict()  # At most two lanes per worker, never the whole market.
    errors, completed = [], 0
    started, last_status = time.monotonic(), 0.0
    initial_bytes = catalog.stats()["bytes"]
    state = "running"
    coordinator_seconds = dict(dispatch=0.0, registration=0.0)

    def snapshot():
        stats = catalog.stats()
        elapsed = time.monotonic() - started
        data = dict(
            state=state,
            updated=time.time(),
            pid=os.getpid(),
            revision=args.source_revision,
            plan=plan["hash"],
            target_bytes=args.target_bytes,
            planned_bytes=CAPACITY,
            **stats,
            elapsed_seconds=elapsed,
            bytes_per_second=(stats["bytes"] - initial_bytes) / max(1, elapsed),
            active=len(active),
            saving=len(publications),
            failed=len(errors),
            errors=errors[-6:],
            workers=args.workers,
            io_workers=args.io_workers,
            process_workers=args.process_workers,
            chunk_events=args.chunk_events,
            free_memory_gib=psutil.virtual_memory().available / 1024**3,
            process_memory_gib=psutil.Process().memory_info().rss / 1024**3,
            ledger_pid=catalog.process.pid,
            free_disk_gib=shutil.disk_usage(root).free / 1024**3,
            reserved_query_gib=gate.reserved / 1024**3,
            resource_waits=list(gate.waiting.values()),
            coordinator_seconds=dict(coordinator_seconds),
            lanes=[
                dict(ticker=w.stream["ticker"], year=w.stream["year"], stage=w.label)
                for w in lanes.values()
            ],
            queued_streams=sum(len(queues[k]) - positions[k] for k in queues),
            wandb_url=telemetry.url,
            wandb_error=telemetry.error,
        )
        write_json(root / "progress.json", data)
        telemetry.update(data)

    def wanted():
        # Select by actual deferred block counts, not the number of tickers or
        # queries. Liquid ranges can yield far more decisions than sparse ones.
        pending = {0: 0, 1: 0}
        for row in catalog.stats()["blocks"]:
            if row["split"] != 2:
                pending[row["liquid"]] += row["total"] - row["selected"]
        return 1 if pending[1] / 3 <= pending[0] else 0

    def new_lane(io):
        preference = wanted()
        for liquid in (preference, 1 - preference):
            while positions[liquid] < len(queues[liquid]):
                stream = queues[liquid][positions[liquid]]
                positions[liquid] += 1
                proof, skipped, committed_unit = catalog.lane_status(stream["key"])
                if skipped:
                    continue
                worker = reusable.pop(stream["key"], None)
                if worker is None:
                    worker = Worker(
                        root,
                        plan,
                        stream,
                        gate,
                        args.endpoint,
                        io,
                        args.chunk_events,
                        defer_metadata=True,
                    )
                worker.trade_proof = proof
                reusable[stream["key"]] = worker
                while len(reusable) > 2 * args.workers:
                    reusable.popitem(last=False)
                cursor = committed_unit + 1
                if worker.units is None or cursor < len(worker.units):
                    return worker, cursor
                reusable.pop(stream["key"], None)
        return None

    # Keep this CPU-only campaign awake independently of the GPU trainer. This
    # request belongs to this coordinator thread and is released on its exit.
    import ctypes

    power = ctypes.windll.kernel32.SetThreadExecutionState
    power.argtypes, power.restype = [ctypes.c_uint], ctypes.c_uint
    if not power(0x80000001):
        raise OSError("Cannot establish workstation keep-awake request")
    try:
        with ThreadPoolExecutor(
            args.io_workers, thread_name_prefix="phase2-io"
        ) as io, executor(args, gate.cancel, io_slots) as pool:
            while True:
                if (root / "STOP").exists():
                    gate.cancel.set()
                stats = catalog.stats()
                # Reserve at most 128 MiB of final payload per admitted lane.
                # Disk and query-memory limits are enforced again at fetch time.
                cap = (
                    stats["bytes"]
                    + (len(active) + len(publications) + 1) * 128 * 1024**2
                    > args.target_bytes
                )
                limit = (
                    args.max_units
                    and completed + len(active) + len(publications) >= args.max_units
                )
                if not gate.cancel.is_set() and not cap and not limit:
                    while len(active) + len(publications) < args.workers:
                        if (
                            stats["bytes"]
                            + (len(active) + len(publications) + 1) * 128 * 1024**2
                            > args.target_bytes
                        ):
                            break
                        dispatch_started = time.monotonic()
                        task = new_lane(io)
                        coordinator_seconds["dispatch"] += (
                            time.monotonic() - dispatch_started
                        )
                        if task is None:
                            break
                        worker, unit = task
                        lanes[worker.stream["key"]] = worker
                        if args.process_workers:
                            worker.label = f"{worker.stream['ticker']} {worker.stream['year']} unit {unit}"
                        future = (
                            pool.submit(
                                produce, worker.stream, unit, worker.trade_proof
                            )
                            if args.process_workers
                            else pool.submit(worker.step, unit)
                        )
                        active[future] = (worker, unit)
                        if (
                            args.max_units
                            and completed + len(active) + len(publications)
                            >= args.max_units
                        ):
                            break
                ready = []
                for future in list(publications):
                    if not future.done():
                        continue
                    worker, unit = publications.pop(future)
                    try:
                        receipt = future.result()
                    except Exception as error:
                        errors.append(
                            dict(
                                stream=worker.stream["key"],
                                unit=unit,
                                reason=f"Publication failed: {error}"[:2000],
                            )
                        )
                        gate.cancel.set()
                        lanes.pop(worker.stream["key"], None)
                        continue
                    ready.append((worker, unit, receipt))
                registration_started = time.monotonic()
                try:
                    catalog.register_many([receipt for _, _, receipt in ready])
                except Exception as error:
                    errors.append(dict(reason=f"Receipt batch failed: {error}"[:2000]))
                    gate.cancel.set()
                    for worker, _, _ in ready:
                        lanes.pop(worker.stream["key"], None)
                    ready = []
                coordinator_seconds["registration"] += (
                    time.monotonic() - registration_started
                )
                for worker, unit, _ in ready:
                    completed += 1
                    lanes.pop(worker.stream["key"], None)
                    # Put this stream at the next class queue position, so its
                    # cached history is resumed while class balancing can adapt.
                    if unit + 1 < len(worker.units):
                        liquid = worker.stream["liquid"]
                        queues[liquid].insert(positions[liquid], worker.stream)
                if active:
                    done, _ = wait(active, timeout=0.25, return_when=FIRST_COMPLETED)
                    for future in done:
                        worker, unit = active.pop(future)
                        try:
                            result = future.result()
                            if isinstance(result, EndOfStream):
                                lanes.pop(worker.stream["key"], None)
                                reusable.pop(worker.stream["key"], None)
                            elif isinstance(result, NoFullBlock):
                                catalog.remember_check(result.proof, skipped=True)
                                lanes.pop(worker.stream["key"], None)
                                reusable.pop(worker.stream["key"], None)
                                completed += 1  # Bounded pilots count resolved no-block work too.
                            elif isinstance(result, ProducedUnit):
                                # The process already sealed its payload. Keep the
                                # original commit-before-advance coordinator path.
                                worker.units = range(result.unit_count)
                                publication = Future()
                                publication.set_result(result.receipt)
                                publications[publication] = (worker, unit)
                            else:
                                publications[result] = (worker, unit)
                        except InterruptedError:
                            lanes.pop(worker.stream["key"], None)
                        except Exception as error:
                            errors.append(
                                dict(
                                    stream=worker.stream["key"],
                                    unit=unit,
                                    reason=f"{type(error).__name__}: {error}"[:2000],
                                )
                            )
                            gate.cancel.set()
                elif publications:
                    # Wake on completion instead of imposing a 100ms dispatch
                    # delay whenever only publication work remains in flight.
                    wait(publications, timeout=0.05, return_when=FIRST_COMPLETED)
                if time.monotonic() - last_status >= 3:
                    state = (
                        "failed_draining"
                        if errors
                        else ("stopping" if gate.cancel.is_set() else "running")
                    )
                    snapshot()
                    last_status = time.monotonic()
                if not active and not publications:
                    if errors:
                        state = "failed"
                    elif gate.cancel.is_set():
                        state = "stopped"
                    elif cap:
                        state = "execution_cap_reached"
                    elif limit:
                        state = "pilot_complete"
                    elif all(positions[k] >= len(queues[k]) for k in queues):
                        state = "inventory_exhausted"
                    else:
                        continue
                    break
        release = catalog.export(plan["hash"])
        snapshot()
        print(json.dumps(dict(state=state, release=release, **catalog.stats())))
        if errors:
            raise RuntimeError(errors[-1]["reason"])
    finally:
        gate.cancel.set()
        gate.close()
        catalog.close()
        telemetry.finish()
        power(0x80000000)
        lock.close()


if __name__ == "__main__":
    run(arguments())
