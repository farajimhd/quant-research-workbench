"""Seeded ticker-month sampling with CPU owners and a separate I/O pool."""

import argparse
from concurrent.futures import (
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    wait,
    FIRST_COMPLETED,
)
from contextlib import contextmanager
from collections import deque
import ctypes
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
from .contract import Geometry
from .source import CanonicalReader, describe, freeze
from .sampling import compile_sessions
from .storage import Catalog, publish, read_receipt
from ..shards.contract import digest, write_json
from ..phase2_shards.training_guard import TrainingGuard
from ..quote_repair_io_v1 import payload_io

STATE = None


def initialize(root, plan, settings, active, lock, stop):
    global STATE
    STATE = dict(
        root=Path(root),
        plan=plan,
        settings=settings,
        active=active,
        lock=lock,
        stop=stop,
        jobs={j["ticker"]: j for j in plan["jobs"]},
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
    s = STATE
    budget = int(s["settings"]["query_memory_gib"] * 1024**3)
    reserve = int(s["settings"]["reserve_memory_gib"] * 1024**3)
    while True:
        if s["stop"].is_set() or (s["root"] / "STOP").exists():
            raise InterruptedError("Graceful stop before canonical query")
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


def produce(unit):
    """One CPU owner retains raw history across consecutive sessions in a month."""
    s = STATE
    geometry = Geometry(
        tuple(s["plan"]["geometry"]["history"]), s["plan"]["geometry"]["cutoff"]
    )
    actions = [
        a
        for a in s["plan"]["authority"]["corporate_actions"]["rows"]
        if a["provider_ticker"] == unit["ticker"]
    ]
    outputs = []
    for session, compiled, timing in compile_sessions(
        s["reader"],
        s["jobs"][unit["ticker"]],
        unit,
        geometry,
        gate=query_admission,
        actions=actions,
        chunk_events=s["settings"]["chunk_events"],
        seed=s["plan"]["seed"],
    ):
        directory = (
            s["root"] / "shards" / digest(unit["ticker"])[:24] / session["session"]
        )
        if (directory / "receipt.json").exists():
            receipt = read_receipt(directory, s["plan"]["hash"])
            if receipt["source_hash"] != digest(session):
                raise ValueError("Published session selection changed")
            result = dict(
                recovered=True, path=str(directory), receipt=receipt, timing=timing
            )
        else:
            result = dict(compiled=compiled, timing=timing)
        outputs.append((session, result))
    return outputs


def publication(root, plan, unit, result, reserve_disk_gib):
    """Dedicated coordinator I/O thread; CPU workers never perform shard writes."""
    root = Path(root)
    compiled = result["compiled"]
    if (
        not compiled.get("price_basis")
        or compiled["price_basis"]["session_date"] != unit["session"]
    ):
        raise ValueError(
            "Operational publication requires a frozen session split basis"
        )
    needed = (
        sum(a.nbytes for a in compiled["views"])
        + compiled["blocks"].nbytes
        + compiled["pending"].nbytes
    )
    if psutil.disk_usage(str(root)).free < reserve_disk_gib * 1024**3 + needed:
        raise OSError("Publication would consume disk reserve")
    began = time.monotonic()
    with payload_io():
        receipt = publish(
            root,
            unit["ticker"],
            unit["month"],
            unit["liquid"],
            plan["hash"],
            digest(unit),
            compiled,
            Geometry(tuple(plan["geometry"]["history"]), plan["geometry"]["cutoff"]),
        )
    timing = dict(result["timing"], publication_seconds=time.monotonic() - began)
    return (
        str(root / "shards" / digest(unit["ticker"])[:24] / unit["month"]),
        receipt,
        timing,
    )


def arguments(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--parent",
        type=Path,
        default=Path("D:/TradingML/runtimes/bar_gpt/shards-2019-2025-v6-quote-v4"),
    )
    p.add_argument(
        "--output",
        type=Path,
        default=Path("D:/TradingML/runtimes/bar_gpt/phase2-ar-sampled-v2"),
    )
    p.add_argument("--source-revision", required=True)
    p.add_argument("--execute", action="store_true")
    p.add_argument("--workers", type=int, default=32)
    p.add_argument("--io-workers", type=int, default=8)
    p.add_argument("--validation-lanes", type=int, default=4)
    p.add_argument(
        "--training-campaign",
        default="D:/TradingML/runtimes/bar_gpt/overnight-v6-20261108",
    )
    p.add_argument("--history-bars", default="64,64,64,64,64")
    p.add_argument("--seed", type=int, default=20261109)
    p.add_argument("--query-memory-gib", type=float, default=8)
    p.add_argument("--reserve-memory-gib", type=float, default=64)
    p.add_argument("--reserve-disk-gib", type=float, default=150)
    p.add_argument(
        "--chunk-events",
        type=int,
        default=0,
        help="0: v3 calendar-month pages; positive: measured session-bound event cap",
    )
    p.add_argument("--target-bytes", type=int, default=4_000_000_000_000)
    p.add_argument("--validation-bytes", type=int, default=250_000_000)
    p.add_argument("--only-ticker", action="append", default=[])
    p.add_argument("--max-months", type=int, default=0)
    p.add_argument("--endpoint")
    return p.parse_args(argv)


def ordered_units(root, plan, only=()):
    import polars as pl

    units = pl.read_parquet(Path(root) / plan["sample_units"]["name"])
    if only:
        units = units.filter(pl.col("ticker").is_in(only))
    if only and set(only) - set(units["ticker"].to_list()):
        raise ValueError("Unknown ticker selection")
    train = deque(units.filter(pl.col("split") == 0).to_dicts())
    validation = deque(units.filter(pl.col("split") == 1).to_dicts())
    result = deque()
    while train or validation:
        for _ in range(3):
            if train:
                result.append(train.popleft())
        if validation:
            result.append(validation.popleft())
    return result


def main(argv=None):
    args = arguments(argv)
    geometry = Geometry(tuple(map(int, args.history_bars.split(","))))
    if (
        not 1 <= args.workers <= 61
        or not 1 <= args.io_workers <= 16
        or not 1 <= args.validation_lanes <= args.workers
        or not 0 < args.query_memory_gib <= 8
        or args.reserve_memory_gib < 64
        or args.reserve_disk_gib < 150
        or args.chunk_events < 0
        or min(args.target_bytes, args.validation_bytes) <= 0
        or args.max_months < 0
    ):
        raise ValueError("Invalid bounded resource configuration")
    preview, _ = describe(args.parent, geometry)
    print(
        json.dumps(
            dict(
                state="preview",
                geometry=geometry.manifest(),
                train_origin_years=[2019, 2025],
                validation_origin_year=2026,
                validation_bytes=args.validation_bytes,
                tickers=len(preview["jobs"]),
                output=str(args.output),
                execute=args.execute,
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
            s.lower()
            in ("phase2-streams-v1", "phase2-pilot-v1", "phase2-ar-monthly-v1")
            for s in root.parts
        )
    ):
        raise ValueError("Require a fresh native sampled-v2 runtime root")
    if (root / "STOP").exists():
        raise InterruptedError("Preserve existing STOP")
    from ..quote_repair_v1 import require_complete

    if not (args.parent / "repair-contract.json").exists():
        raise ValueError("Require a released corrected parent")
    require_complete(args.parent)
    root.mkdir(parents=True, exist_ok=True)
    import msvcrt

    lease = (root / "controller.lock").open("a+b")
    lease.write(b"0")
    lease.flush()
    lease.seek(0)
    msvcrt.locking(lease.fileno(), msvcrt.LK_NBLCK, 1)
    catalog = None
    power = ctypes.windll.kernel32.SetThreadExecutionState
    power.argtypes, power.restype = [ctypes.c_uint], ctypes.c_uint
    awake = False
    try:
        plan = freeze(
            root, args.parent, geometry, args.source_revision, args.endpoint, args.seed
        )
        settings = vars(args).copy()
        settings.update(parent=str(args.parent), output=str(root))
        write_json(
            root / "execution.json",
            dict(settings, pid=os.getpid(), started=time.time(), binding=plan["hash"]),
        )
        queues = ordered_units(root, plan, args.only_ticker)
        catalog = Catalog(root)
        catalog.db.executescript(
            "CREATE TABLE IF NOT EXISTS deferrals(key TEXT PRIMARY KEY,report TEXT,hash TEXT); CREATE TABLE IF NOT EXISTS completed_months(key TEXT PRIMARY KEY,report TEXT,hash TEXT);"
        )
        catalog.db.commit()
        ctx = mp.get_context("spawn")
        active_query, lock, stop = ctx.Value("i", 0), ctx.Lock(), ctx.Event()
        handlers = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
        for sig in handlers:
            signal.signal(sig, lambda *_: stop.set())
        if not power(0x80000001):
            raise OSError("Cannot establish keep-awake request")
        awake = True
        cpus, ios, groups = {}, {}, {}
        staged = deque()
        reserved = {0: 0, 1: 0}
        caps = {0: args.target_bytes, 1: args.validation_bytes}
        rejected = {0: 0, 1: 0}
        counters = dict(
            admitted_months_this_run=0,
            completed_months_this_run=0,
            published_sessions_this_run=0,
            deferred_sessions=0,
            deferred_source_origins=0,
            capacity_deferred=0,
        )
        for report, sealed in catalog.db.execute("SELECT report,hash FROM deferrals"):
            prior = json.loads(report)
            if digest(prior) != sealed or prior["binding"] != plan["hash"]:
                raise ValueError("Deferred-session seal changed")
            counters["deferred_sessions"] += 1
            counters["deferred_source_origins"] += prior["stats"]["source_origins"]
        began, last_report = time.monotonic(), float("-inf")
        failure = None

        def month_key(unit):
            return digest([unit["ticker"], unit["month"]])

        def finish(group, session, kind, sealed=None):
            g = groups[group]
            g["remaining"] -= 1
            g["sessions"].append(
                dict(session=session["session"], kind=kind, seal=sealed)
            )
            if kind == "capacity":
                g["complete"] = False
            if not g["remaining"]:
                if g["complete"]:
                    body = dict(
                        binding=plan["hash"], unit=g["unit"], sessions=g["sessions"]
                    )
                    with catalog.db:
                        catalog.db.execute(
                            "INSERT INTO completed_months VALUES(?,?,?)",
                            (group, json.dumps(body), digest(body)),
                        )
                    counters["completed_months_this_run"] += 1
                del groups[group]

        def defer(unit, compiled):
            key = month_key(unit)
            body = dict(
                binding=plan["hash"],
                unit=unit,
                reason=compiled["sampling"]["reason"],
                stats=compiled["stats"],
                sampling=compiled["sampling"],
            )
            sealed = digest(body)
            prior = catalog.db.execute(
                "SELECT hash FROM deferrals WHERE key=?", (key,)
            ).fetchone()
            if prior:
                if prior[0] != sealed:
                    raise ValueError("Deferred session changed")
                return sealed
            with catalog.db:
                catalog.db.execute(
                    "INSERT INTO deferrals VALUES(?,?,?)",
                    (key, json.dumps(body), sealed),
                )
            counters["deferred_sessions"] += 1
            counters["deferred_source_origins"] += body["stats"]["source_origins"]
            return sealed

        with ProcessPoolExecutor(
            args.workers,
            mp_context=ctx,
            initializer=initialize,
            initargs=(str(root), plan, settings, active_query, lock, stop),
        ) as cpu, ThreadPoolExecutor(
            args.io_workers, thread_name_prefix="ar-shard-io"
        ) as io:
            while queues or cpus or ios or staged:
                if (root / "STOP").exists():
                    stop.set()
                while (
                    queues
                    and len(cpus) < args.workers
                    and len(staged) < args.io_workers * 2
                    and not stop.is_set()
                ):
                    if (
                        args.max_months
                        and counters["admitted_months_this_run"] >= args.max_months
                    ):
                        break
                    unit = queues.popleft()
                    key = month_key(unit)
                    split = int(unit["split"])
                    previous = catalog.db.execute(
                        "SELECT report,hash FROM completed_months WHERE key=?", (key,)
                    ).fetchone()
                    if previous:
                        report = json.loads(previous[0])
                        if (
                            digest(report) != previous[1]
                            or report["binding"] != plan["hash"]
                            or report["unit"] != unit
                        ):
                            raise ValueError("Completed month seal changed")
                        for record in report["sessions"]:
                            if record["kind"] == "published":
                                path = (
                                    root
                                    / "shards"
                                    / digest(unit["ticker"])[:24]
                                    / record["session"]
                                )
                                receipt = read_receipt(path, plan["hash"])
                                if receipt["hash"] != record["seal"]:
                                    raise ValueError("Completed session changed")
                                if any(
                                    (path / f["name"]).stat().st_size != f["bytes"]
                                    for f in receipt["files"]
                                ):
                                    raise ValueError(
                                        "Completed session payload changed"
                                    )
                            elif record["kind"] == "deferred":
                                proof = catalog.db.execute(
                                    "SELECT hash FROM deferrals WHERE key=?",
                                    (digest([unit["ticker"], record["session"]]),),
                                ).fetchone()
                                if not proof or proof[0] != record["seal"]:
                                    raise ValueError(
                                        "Completed session deferral changed"
                                    )
                        continue
                    if (
                        catalog.split_bytes.get(split, 0) + reserved[split]
                        >= caps[split] - 1_000_000
                        or rejected[split] >= args.io_workers * 2
                    ):
                        counters["capacity_deferred"] += 1
                        continue
                    cpus[cpu.submit(produce, unit)] = unit
                    counters["admitted_months_this_run"] += 1
                while staged and len(ios) < args.io_workers * 2:
                    key, unit, result = staged.popleft()
                    split = int(unit["split"])
                    try:
                        if result.get("recovered"):
                            catalog.register(result["path"], result["receipt"])
                            finish(key, unit, "published", result["receipt"]["hash"])
                            continue
                        compiled = result["compiled"]
                        if not len(compiled["blocks"]):
                            finish(key, unit, "deferred", defer(unit, compiled))
                            continue
                        reservation = (
                            sum(a.nbytes for a in compiled["views"])
                            + compiled["blocks"].nbytes
                            + compiled["pending"].nbytes
                            + 1_000_000
                        )
                        if (
                            catalog.split_bytes.get(split, 0)
                            + reserved[split]
                            + reservation
                            > caps[split]
                        ):
                            rejected[split] += 1
                            counters["capacity_deferred"] += 1
                            finish(key, unit, "capacity")
                            continue
                        if (
                            psutil.disk_usage(str(root)).free
                            < args.reserve_disk_gib * 1024**3
                            + sum(reserved.values())
                            + reservation
                        ):
                            raise OSError(
                                "Staged publications would consume disk reserve"
                            )
                        reserved[split] += reservation
                        ios[
                            io.submit(
                                publication,
                                root,
                                plan,
                                unit,
                                result,
                                args.reserve_disk_gib,
                            )
                        ] = (key, unit, reservation)
                    except Exception as error:
                        failure = (
                            failure
                            or f'{unit["ticker"]}:{unit["month"]} {type(error).__name__}: {error}'
                        )
                        stop.set()
                if not cpus and not ios:
                    if staged:
                        continue
                    break
                watched = (
                    tuple(ios)
                    if len(ios) >= args.io_workers * 2
                    else tuple(cpus) + tuple(ios)
                )
                ready, _ = wait(watched, timeout=2, return_when=FIRST_COMPLETED)
                for future in list(ready):
                    if future not in ios:
                        continue
                    key, unit, reservation = ios.pop(future)
                    reserved[int(unit["split"])] -= reservation
                    try:
                        path, receipt, timing = future.result()
                        catalog.register(path, receipt)
                        counters["published_sessions_this_run"] += 1
                        finish(key, unit, "published", receipt["hash"])
                        with (root / "timing.jsonl").open("a") as out:
                            out.write(json.dumps(dict(unit=unit, **timing)) + "\n")
                    except Exception as error:
                        failure = (
                            failure
                            or f'{unit["ticker"]}:{unit["month"]} {type(error).__name__}: {error}'
                        )
                        stop.set()
                for future in ready:
                    if future not in cpus:
                        continue
                    unit = cpus.pop(future)
                    key = month_key(unit)
                    try:
                        outputs = future.result()
                        with (root / "monthly-timing.jsonl").open("a") as out:
                            totals = {
                                k: sum(
                                    result.get("timing", {}).get(k, 0)
                                    for _, result in outputs
                                )
                                for k in (
                                    "queries",
                                    "query_seconds",
                                    "admission_wait_seconds",
                                    "fetch_seconds",
                                    "compile_seconds",
                                )
                            }
                            out.write(
                                json.dumps(
                                    dict(unit=unit, sessions=len(outputs), **totals)
                                )
                                + "\n"
                            )
                        groups[key] = dict(
                            unit=unit,
                            remaining=len(outputs),
                            sessions=[],
                            complete=True,
                        )
                        if not outputs:
                            raise ValueError("Sampled month has no session reports")
                        staged.extend(
                            (key, session, result) for session, result in outputs
                        )
                    except Exception as error:
                        failure = (
                            failure
                            or f'{unit["ticker"]}:{unit["month"]} {type(error).__name__}: {error}'
                        )
                        stop.set()
                snapshot = dict(
                    state=(
                        "failed"
                        if failure
                        else "draining" if stop.is_set() else "running"
                    ),
                    updated=time.time(),
                    active_cpu=len(cpus),
                    active_io=sum(f.running() for f in ios),
                    queued_io=sum(not f.running() and not f.done() for f in ios),
                    cpu_results_waiting=sum(f.done() for f in cpus),
                    staged_sessions=len(staged),
                    active_queries=active_query.value,
                    queued_units=len(queues),
                    elapsed_seconds=time.monotonic() - began,
                    failure=failure,
                    **counters,
                    **catalog.stats(),
                )
                write_json(root / "progress.json", snapshot)
                if time.monotonic() - last_report >= 15:
                    print(json.dumps(snapshot), flush=True)
                    last_report = time.monotonic()
                if stop.is_set() and not cpus and not ios and not staged:
                    break
        selection = catalog.export(plan["hash"])
        state = (
            "failed"
            if failure
            else (
                "stopped"
                if stop.is_set()
                else (
                    "pilot_complete"
                    if args.max_months or args.only_ticker
                    else (
                        "capacity_reached"
                        if counters["capacity_deferred"]
                        else "complete"
                    )
                )
            )
        )
        final = dict(
            state=state,
            binding=plan["hash"],
            training_ready=False,
            artifact_role="raw-ar-context-shards",
            failure=failure,
            selection=selection,
            validation_budget=args.validation_bytes,
            **counters,
            **catalog.stats(),
        )
        write_json(root / "result.json", final)
        write_json(
            root / "progress.json",
            dict(
                final, updated=time.time(), active_cpu=0, active_io=0, active_queries=0
            ),
        )
        for s, handler in handlers.items():
            signal.signal(s, handler)
        if failure:
            raise RuntimeError(failure)
        return 0
    finally:
        if awake:
            power(0x80000000)
        if catalog is not None:
            catalog.db.close()
        lease.close()


if __name__ == "__main__":
    raise SystemExit(main())
