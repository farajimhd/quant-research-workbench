"""Preview or explicitly execute monthly AR shards; no model/GPU code is imported."""

import argparse
from collections import deque
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from contextlib import contextmanager
import json
import multiprocessing as mp
import os
from pathlib import Path
import signal
import time

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ.setdefault("POLARS_MAX_THREADS", "2")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import psutil

from .contract import Compiler, Geometry, VERSION
from .source import CanonicalReader, describe, freeze
from .storage import Catalog, MonthReader, publish, read_receipt
from ..shards.contract import digest, write_json
from ..phase2_shards.training_guard import TrainingGuard
from ..quote_repair_io_v1 import payload_io

STATE = None


def initialize(root, plan, settings, active, lock, stop, io_slots):
    global STATE
    STATE = dict(
        root=Path(root),
        plan=plan,
        settings=settings,
        active=active,
        lock=lock,
        stop=stop,
        io=io_slots,
        reader=CanonicalReader(
            root, plan, settings.get("endpoint"), settings["query_memory_gib"]
        ),
        guard=TrainingGuard(
            settings.get("training_campaign"),
            settings["workers"],
            settings["validation_lanes"],
        ),
    )


@contextmanager
def query_admission():
    """Cross-process resource/admission guard; never signal the trainer."""
    s = STATE
    budget = int(s["settings"]["query_memory_gib"] * 1024**3)
    reserve = int(s["settings"]["reserve_memory_gib"] * 1024**3)
    while True:
        if s["stop"].is_set() or (s["root"] / "STOP").exists():
            raise InterruptedError("Graceful stop before next canonical query")
        with s["lock"]:
            if (
                s["active"].value < s["guard"].refresh()
                and psutil.virtual_memory().available - s["active"].value * budget
                >= reserve + budget
            ):
                s["active"].value += 1
                break
        s["stop"].wait(0.25)
    try:
        yield
    finally:
        with s["lock"]:
            s["active"].value -= 1


def produce(job, month, previous):
    """One ticker-month at a time per ticker; resume from its cached predecessor."""
    s = STATE
    reader, plan, root = s["reader"], s["plan"], s["root"]
    geometry = Geometry(tuple(plan["geometry"]["history"]), plan["geometry"]["cutoff"])
    directory = root / "shards" / digest(job["ticker"])[:24] / month
    if (directory / "receipt.json").exists():
        return (
            str(directory),
            read_receipt(directory, plan["hash"]),
            dict(recovered=True),
        )
    if psutil.disk_usage(str(root)).free < s["settings"]["reserve_disk_gib"] * 1024**3:
        raise OSError("Disk reserve reached; published months remain resumable")
    compiler = (
        MonthReader(previous, plan["hash"]).restore()
        if previous
        else Compiler(geometry)
    )
    rows = dict(reader.months(job))[month]
    year = int(month[:4])
    liquid = next(int(y["liquid"]) for y in job["years"] if int(y["year"]) == year)
    arrays, timing = reader.fetch(
        job["ticker"], rows, query_admission, s["settings"]["chunk_events"]
    )
    began = time.monotonic()
    compiled = compiler.compile(arrays, liquid)
    timing["compile_seconds"] = time.monotonic() - began
    if s["stop"].is_set() or (root / "STOP").exists():
        raise InterruptedError("Stopped before monthly publication")
    began = time.monotonic()
    with s["io"], payload_io():
        payload_bytes = (
            sum(a.nbytes for a in compiled["views"])
            + compiled["blocks"].nbytes
            + compiled["pending"].nbytes
        )
        if (
            psutil.disk_usage(str(root)).free
            < s["settings"]["reserve_disk_gib"] * 1024**3 + payload_bytes
        ):
            raise OSError("Publication would consume the disk reserve")
        receipt = publish(
            root,
            job["ticker"],
            month,
            liquid,
            plan["hash"],
            digest(rows),
            compiled,
            geometry,
        )
    timing["publication_seconds"] = time.monotonic() - began
    return str(directory), receipt, timing


def discover(job):
    """Load source metadata lazily on workers, not for the entire universe first."""
    s = STATE
    months = [m for m, _ in s["reader"].months(job)]
    completed = []
    while months:
        path = s["root"] / "shards" / digest(job["ticker"])[:24] / months[0]
        if not (path / "receipt.json").exists():
            break
        completed.append(str(path))
        months.pop(0)
    return None, dict(kind="discovery", months=months, completed=completed), {}


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--parent",
        type=Path,
        default=Path("D:/TradingML/runtimes/bar_gpt/shards-2019-2025-v6-quote-v4"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("D:/TradingML/runtimes/bar_gpt/phase2-ar-monthly-v1"),
    )
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--io-workers", type=int, default=8)
    parser.add_argument("--validation-lanes", type=int, default=4)
    parser.add_argument(
        "--training-campaign",
        default="D:/TradingML/runtimes/bar_gpt/overnight-v6-20261108",
    )
    parser.add_argument("--history-bars", default="1024,512,512,256,128")
    parser.add_argument("--query-memory-gib", type=float, default=3)
    parser.add_argument("--reserve-memory-gib", type=float, default=64)
    parser.add_argument("--reserve-disk-gib", type=float, default=150)
    parser.add_argument("--chunk-events", type=int, default=2_000_000)
    parser.add_argument("--target-bytes", type=int, default=4_000_000_000_000)
    parser.add_argument("--only-ticker", action="append", default=[])
    parser.add_argument(
        "--max-months",
        type=int,
        default=0,
        help="Bounded pilot; never marks a complete release",
    )
    parser.add_argument("--endpoint")
    return parser.parse_args(argv)


def main(argv=None):
    args = arguments(argv)
    geometry = Geometry(tuple(map(int, args.history_bars.split(","))))
    if (
        not 1 <= args.workers <= 32
        or not 1 <= args.io_workers <= 8
        or not 1 <= args.validation_lanes <= args.workers
        or not 0 < args.query_memory_gib <= 8
        or args.reserve_memory_gib < 64
        or args.reserve_disk_gib < 150
        or args.chunk_events < 1
        or args.target_bytes < 1
        or args.max_months < 0
    ):
        raise ValueError("Invalid bounded worker/resource configuration")
    plan, _ = describe(args.parent, geometry)
    jobs = [
        j
        for j in plan["jobs"]
        if not args.only_ticker or j["ticker"] in args.only_ticker
    ]
    if not jobs or (
        args.only_ticker and set(args.only_ticker) - {j["ticker"] for j in jobs}
    ):
        raise ValueError("Unknown or empty ticker selection")
    print(
        json.dumps(
            dict(
                state="preview",
                tickers=len(jobs),
                full_universe=len(plan["jobs"]),
                geometry=geometry.manifest(),
                output=str(args.output),
                binding=plan["hash"],
                execute=args.execute,
                sampling=plan["sampling"],
            )
        ),
        flush=True,
    )
    if not args.execute:
        return 0
    authority = Path("D:/TradingML/runtimes").resolve()
    root = args.output.resolve()
    if (
        not authority.exists()
        or not root.is_relative_to(authority)
        or any(
            p.lower() in ("phase2-streams-v1", "phase2-pilot-v1") for p in root.parts
        )
    ):
        raise ValueError("Require a fresh native TradingML runtime root")
    if (root / "STOP").exists():
        raise InterruptedError("Preserve existing STOP")
    # A growing or old-data parent cannot enter a new production campaign.
    from ..quote_repair_v1 import require_complete

    if not (args.parent / "repair-contract.json").exists():
        raise ValueError("Require a released corrected phase-one parent")
    require_complete(args.parent)
    root.mkdir(parents=True, exist_ok=True)
    import msvcrt

    lease = (root / "controller.lock").open("a+b")
    lease.write(b"0")
    lease.flush()
    lease.seek(0)
    msvcrt.locking(lease.fileno(), msvcrt.LK_NBLCK, 1)
    plan = freeze(root, args.parent, geometry, args.source_revision, args.endpoint)
    settings = vars(args).copy()
    settings["parent"], settings["output"] = str(args.parent), str(root)
    write_json(
        root / "execution.json",
        dict(settings, pid=os.getpid(), started=time.time(), binding=plan["hash"]),
    )
    queues = deque((job, None, None) for job in jobs)
    catalog = Catalog(root)
    context = mp.get_context("spawn")
    active_queries, lock, stop = context.Value("i", 0), context.Lock(), context.Event()
    slots = context.BoundedSemaphore(args.io_workers)
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    active, completed, began, failure = {}, 0, time.monotonic(), None
    last_report = float("-inf")
    state = "running"
    import ctypes

    power = ctypes.windll.kernel32.SetThreadExecutionState
    power.argtypes, power.restype = [ctypes.c_uint], ctypes.c_uint
    if not power(0x80000001):
        catalog.db.close()
        lease.close()
        raise OSError("Cannot establish workstation keep-awake request")
    try:
        with ProcessPoolExecutor(
            args.workers,
            mp_context=context,
            initializer=initialize,
            initargs=(str(root), plan, settings, active_queries, lock, stop, slots),
        ) as pool:
            while queues or active:
                if (root / "STOP").exists():
                    stop.set()
                capacity = catalog.stats()["bytes"] >= args.target_bytes
                limited = args.max_months and completed + len(active) >= args.max_months
                while (
                    queues
                    and len(active) < args.workers
                    and not stop.is_set()
                    and not capacity
                    and not limited
                ):
                    job, months, previous = queues.popleft()
                    future = (
                        pool.submit(discover, job)
                        if months is None
                        else pool.submit(produce, job, months[0], previous)
                    )
                    active[future] = (job, months, previous)
                    limited = (
                        args.max_months and completed + len(active) >= args.max_months
                    )
                if not active:
                    state = (
                        "stopped"
                        if stop.is_set()
                        else (
                            "pilot_complete"
                            if args.max_months or args.only_ticker
                            else "capacity_reached" if capacity else "complete"
                        )
                    )
                    break
                ready, _ = wait(active, timeout=2, return_when=FIRST_COMPLETED)
                for future in ready:
                    job, months, previous = active.pop(future)
                    try:
                        path, receipt, timing = future.result()
                        if receipt.get("kind") == "discovery":
                            for prior in receipt["completed"]:
                                catalog.register(
                                    prior, read_receipt(prior, plan["hash"])
                                )
                            if receipt["months"]:
                                queues.appendleft(
                                    (
                                        job,
                                        receipt["months"],
                                        (
                                            receipt["completed"][-1]
                                            if receipt["completed"]
                                            else None
                                        ),
                                    )
                                )
                            continue
                        catalog.register(path, receipt)
                        completed += 1
                        if len(months) > 1:
                            queues.appendleft((job, months[1:], path))
                        with (root / "timing.jsonl").open("a") as out:
                            out.write(
                                json.dumps(
                                    dict(
                                        ticker=job["ticker"], month=months[0], **timing
                                    )
                                )
                                + "\n"
                            )
                    except Exception as error:
                        failure = f"{job['ticker']}:{months[0] if months else 'metadata'} {type(error).__name__}: {error}"
                        stop.set()
                snapshot = dict(
                    state=(
                        "failed"
                        if failure
                        else "draining" if stop.is_set() else "running"
                    ),
                    updated=time.time(),
                    active=len(active),
                    queued_tickers=len(queues),
                    active_queries=active_queries.value,
                    completed_months_this_run=completed,
                    elapsed_seconds=time.monotonic() - began,
                    failure=failure,
                    **catalog.stats(),
                )
                write_json(root / "progress.json", snapshot)
                if time.monotonic() - last_report >= 15:
                    print(json.dumps(snapshot), flush=True)
                    last_report = time.monotonic()
                if failure and not active:
                    state = "failed"
                    break
        if state == "running":
            state = (
                "stopped"
                if stop.is_set()
                else (
                    "pilot_complete"
                    if args.max_months or args.only_ticker
                    else "complete"
                )
            )
        # A bounded pilot may have a useful selection index, never a full release.
        selection = catalog.export(plan["hash"])
        final = dict(
            artifact_role="raw-ar-context-shards",
            training_ready=False,
            state="failed" if failure else state,
            binding=plan["hash"],
            failure=failure,
            selection=selection,
            **catalog.stats(),
        )
        write_json(root / "result.json", final)
        write_json(
            root / "progress.json",
            dict(
                final,
                updated=time.time(),
                active=0,
                active_queries=0,
                queued_tickers=len(queues),
            ),
        )
        if failure:
            raise RuntimeError(failure)
        return 0
    finally:
        power(0x80000000)
        catalog.db.close()
        lease.close()


if __name__ == "__main__":
    raise SystemExit(main())
