"""Plan, resume and publish phase-two streams without touching the GPU trainer.

Run with ``python -B -m research.bar_gpt.v5.phase2_shards.run --help``.
All generated files must be under the selected machine's TradingML runtimes.
The first execution cap is two decimal TB; the immutable inventory plans four.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import json
import os
from pathlib import Path
import shutil
import signal
import time

import psutil

from .contract import CAPACITY, write_json
from .plan import freeze
from .producer import Gate, Worker
from .storage import Catalog

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
    parser.add_argument("--target-bytes", type=int, default=2_000_000_000_000)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--io-workers", type=int, default=2)
    parser.add_argument("--query-memory-gib", type=float, default=3)
    parser.add_argument("--reserve-memory-gib", type=float, default=96)
    parser.add_argument("--reserve-disk-gib", type=float, default=150)
    parser.add_argument("--chunk-events", type=int, default=2_000_000)
    parser.add_argument("--max-units", type=int, default=0)
    parser.add_argument(
        "--only-stream",
        action="append",
        default=[],
        help="Optional pilot ticker:year; immutable plan remains complete",
    )
    parser.add_argument("--endpoint")
    return parser.parse_args()


def run(args):
    """Coordinate bounded producers and I/O; parent is the only ledger writer."""
    root = Path(args.output).resolve()
    authority = Path("D:/TradingML/runtimes").resolve()
    if not root.is_relative_to(authority) or not authority.exists():
        raise ValueError("Required D:/TradingML/runtimes authority is unavailable")
    if not 0 < args.target_bytes <= CAPACITY or min(args.workers, args.io_workers) < 1:
        raise ValueError("Invalid cap or worker count")
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
    layout = root / "layout.json"
    if layout.exists():
        if json.loads(layout.read_text())["chunk_events"] != args.chunk_events:
            raise ValueError(
                "Chunk layout is immutable; resume with the original chunk-events"
            )
    else:
        write_json(layout, dict(chunk_events=args.chunk_events, plan=plan["hash"]))
    write_json(
        root / "execution.json",
        dict(**vars(args), pid=os.getpid(), started=time.time(), plan=plan["hash"]),
    )
    catalog = Catalog(root)
    catalog.recover()
    gate = Gate(
        root, args.query_memory_gib, args.reserve_memory_gib, args.reserve_disk_gib
    )
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
    errors, completed = [], 0
    started, last_status = time.monotonic(), 0.0
    initial_bytes = catalog.stats()["bytes"]
    state = "running"

    def snapshot():
        stats = catalog.stats()
        elapsed = time.monotonic() - started
        write_json(
            root / "progress.json",
            dict(
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
                chunk_events=args.chunk_events,
                free_memory_gib=psutil.virtual_memory().available / 1024**3,
                process_memory_gib=psutil.Process().memory_info().rss / 1024**3,
                free_disk_gib=shutil.disk_usage(root).free / 1024**3,
                reserved_query_gib=gate.reserved / 1024**3,
                resource_waits=list(gate.waiting.values()),
                lanes=[
                    dict(
                        ticker=w.stream["ticker"], year=w.stream["year"], stage=w.label
                    )
                    for w in lanes.values()
                ],
                queued_streams=sum(len(queues[k]) - positions[k] for k in queues),
            ),
        )

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
                worker = Worker(
                    root, plan, stream, gate, args.endpoint, io, args.chunk_events
                )
                cursor = catalog.cursor(stream["key"]) + 1
                if cursor < len(worker.units):
                    return worker, cursor
        return None

    try:
        with ThreadPoolExecutor(
            args.io_workers, thread_name_prefix="phase2-io"
        ) as io, ThreadPoolExecutor(
            args.workers, thread_name_prefix="phase2-query"
        ) as pool:
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
                        task = new_lane(io)
                        if task is None:
                            break
                        worker, unit = task
                        lanes[worker.stream["key"]] = worker
                        active[pool.submit(worker.step, unit)] = (worker, unit)
                        if (
                            args.max_units
                            and completed + len(active) + len(publications)
                            >= args.max_units
                        ):
                            break
                for future in list(publications):
                    if not future.done():
                        continue
                    worker, unit = publications.pop(future)
                    try:
                        receipt = future.result()
                        catalog.register(receipt)
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
                            publications[future.result()] = (worker, unit)
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
                    time.sleep(0.1)
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
        catalog.db.close()
        lock.close()


if __name__ == "__main__":
    run(arguments())
