"""Seeded ticker-month sampling with CPU owners and a separate I/O pool."""

import argparse
from concurrent.futures import (
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    wait,
    FIRST_COMPLETED,
)
from contextlib import contextmanager, ExitStack
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
from .contract import Geometry, BAR, ORIGIN, BLOCK
from .assignment import assign
from .source import CanonicalReader, describe, freeze
from .sampling import compile_sessions
from .storage import Catalog, publish, read_receipt
from ..shards.contract import digest, write_json
from ..phase2_shards.training_guard import TrainingGuard
from ..quote_repair_io_v1 import payload_io
from .telemetry import cpu_telemetry, ObservedReader, PublicationTelemetry

STATE = None


def capacity_status(used, reserved, caps, rejected, deferred, rejection_limit=16):
    """Operational byte admission only; never change selection or payloads."""
    result = {}
    for split, name in ((0, "training"), (1, "validation")):
        occupied = used.get(split, 0) + reserved[split]
        minimum = 1_000_000 + BLOCK * (BAR.itemsize + ORIGIN.itemsize)
        reason = ("byte_headroom" if occupied >= caps[split] - 1_000_000
                  else "minimum_block_fit" if caps[split] - occupied < minimum
                  else "session_fit_limit" if rejected[split] >= rejection_limit else None)
        result[name] = dict(used_bytes=used.get(split, 0), reserved_bytes=reserved[split],
                            limit_bytes=caps[split], remaining_bytes=max(0, caps[split]-occupied),
                            blocked=reason is not None, reason=reason, minimum_block_bytes=minimum,
                            deferred_units=deferred[split])
    return result


def initialize(root, plan, settings, active, lock, stop, reserved, dense, dense_active, dense_attempts):
    global STATE
    if STATE and STATE.get("telemetry") and mp.current_process().name == "MainProcess":
        STATE["telemetry"].close()  # Thread-pool integration fixtures reuse this process.
    reporter = cpu_telemetry(root, settings)
    STATE = dict(
        root=Path(root),
        plan=plan,
        settings=settings,
        active=active,
        reserved=reserved,dense=dense,dense_active=dense_active,dense_attempts=dense_attempts,
        lock=lock,
        stop=stop,
        telemetry=reporter,
        jobs={j["ticker"]: j for j in plan["jobs"]},
        reader=CanonicalReader(
            root, plan, settings.get("endpoint"), settings["query_memory_gib"],
            settings.get('query_max_memory_gib',4),settings.get('eligibility_cache')
        ),
        guard=TrainingGuard(
            settings.get("training_campaign"),
            settings["workers"],
            settings["validation_lanes"],
        ),
    )
    STATE["reader"] = ObservedReader(STATE["reader"], reporter, plan["seed"])
    from .continuation import validate_parents
    STATE['prior_roots']=validate_parents(plan)


@contextmanager
def query_admission(memory_gib=None, *, query=True):
    s = STATE
    initial=s['settings']['query_memory_gib']
    requested=initial if memory_gib is None else memory_gib
    if not initial<=requested<=s['settings'].get('query_max_memory_gib',4):
        raise ValueError('Requested query budget is outside configured bounds')
    budget = int(requested * 1024**3)
    reserve = int(s["settings"]["reserve_memory_gib"] * 1024**3)
    dense=requested>initial;permit=False;admitted=False
    try:
        if dense:
            while not s['dense'].acquire(timeout=.25):
                if s['stop'].is_set() or (s['root']/'STOP').exists():raise InterruptedError('Graceful stop awaiting dense query slot')
                if s.get('telemetry'):s['telemetry'].emit(state='Wait',detail='Dense memory retries: two slots')
            permit=True
        while True:
            if s["stop"].is_set() or (s["root"] / "STOP").exists():
                raise InterruptedError("Graceful stop before canonical query")
            with s["lock"]:
                limit = s["guard"].refresh()
                held=s['reserved'].value
                if s["active"].value < limit and psutil.virtual_memory().available-held>=reserve+budget:
                    s["active"].value += 1
                    s['reserved'].value+=budget
                    if dense:s['dense_active'].value+=1;s['dense_attempts'].value+=1
                    admitted=True;break
                reason=s['guard'].reason if s['active'].value>=limit else 'RAM reserve + weighted query budgets'
            if s.get('telemetry'):s['telemetry'].emit(state='Wait',detail=reason,admission_limit=s['guard'].limit)
            s['stop'].wait(.25)
        try:
            if s.get('telemetry'):
                s['telemetry'].emit(query_memory_gib=requested,admission_limit=limit)
                if query:s['telemetry'].query(True)
        except Exception:pass
        yield
    finally:
        if admitted:
            try:
                if query:s['telemetry'].query(False)
            except Exception:pass
            with s['lock']:
                s['active'].value-=1
                s['reserved'].value-=budget
                if dense:s['dense_active'].value-=1
        if permit:s['dense'].release()


def produce(unit):
    reporter = STATE.get("telemetry")
    if reporter:
        reporter.begin(unit)
    try:
        result = _produce(unit)
    except BaseException as error:
        if reporter:
            reporter.end(error)
        raise
    if reporter:
        reporter.end()
    return result


def _produce(unit):
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
    overlap=s['plan'].get('overlap')
    if overlap:
        from .overlap import compile_cached, POLICY
        if overlap['policy']!=POLICY:raise ValueError('Unknown origin overlap policy')
        generated=compile_cached(unit,geometry,s['prior_roots'],overlap['stride'],
                                 s['plan']['seed'],query_admission,s.get('telemetry'))
    else:
        generated=compile_sessions(
        s["reader"],
        s["jobs"][unit["ticker"]],
        unit,
        geometry,
        gate=query_admission,
        actions=actions,
        chunk_events=s["settings"]["chunk_events"],
        seed=s["plan"]["seed"],
        prior_roots=s.get('prior_roots',()),
        )
    for session, compiled, timing in generated:
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
        if s.get("telemetry"):
            s["telemetry"].session()
    return outputs


def publication(root, plan, unit, result, reserve_disk_gib, observers=None, io_slot=None):
    reporter = observers.worker(io_slot) if observers is not None else None
    if reporter:reporter.begin(unit)
    try:
        output = _publication(root, plan, unit, result, reserve_disk_gib,
                              reporter.emit if reporter else None)
    except BaseException as error:
        if reporter:reporter.end(error)
        raise
    if reporter:reporter.end()
    return output


def _publication(root, plan, unit, result, reserve_disk_gib, observer=None):
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
            observer=observer,
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
    p.add_argument("--query-memory-gib", type=float, default=1)
    p.add_argument('--query-max-memory-gib',type=float,default=4)
    p.add_argument("--reserve-memory-gib", type=float, default=64)
    p.add_argument("--reserve-disk-gib", type=float, default=150)
    p.add_argument(
        "--chunk-events",
        type=int,
        default=0,
        help="0: month-aligned 8M-event pages; positive: explicit whole-session event cap",
    )
    p.add_argument("--target-bytes", type=int, default=4_000_000_000_000)
    p.add_argument("--validation-bytes", type=int, default=250_000_000)
    p.add_argument("--only-ticker", action="append", default=[])
    p.add_argument("--max-months", type=int, default=0)
    p.add_argument("--pilot-unit", action="append", default=[], help="Exact TICKER:YYYY-MM units for a bounded pilot only")
    p.add_argument("--endpoint")
    p.add_argument('--eligibility-cache',help='Sealed exact ancestor counts; misses use canonical queries')
    p.add_argument("--engineering-proof", type=Path, help="Sealed same-contract native parity proof for a reviewed physical optimization")
    return p.parse_args(argv)


def ordered_units(root, plan, only=(), pilot_units=()):
    import polars as pl

    units = pl.read_parquet(Path(root) / plan["sample_units"]["name"])
    if only:
        units = units.filter(pl.col("ticker").is_in(only))
    if only and set(only) - set(units["ticker"].to_list()):
        raise ValueError("Unknown ticker selection")
    if pilot_units:
        keys = pl.concat_str("ticker", "month", separator=":")
        if set(pilot_units) - set(units.select(keys.alias("key"))["key"].to_list()):
            raise ValueError("Unknown frozen pilot unit")
        units = units.filter(keys.is_in(pilot_units))
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
        or not .25 <= args.query_memory_gib <= args.query_max_memory_gib <= 8
        or args.reserve_memory_gib < 64
        or args.reserve_disk_gib < 150
        or args.chunk_events < 0
        or min(args.target_bytes, args.validation_bytes) <= 0
        or args.max_months < 0
        or (args.pilot_unit and args.max_months < len(set(args.pilot_unit)))
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
    observers = None
    try:
        plan = freeze(
            root, args.parent, geometry, args.source_revision, args.endpoint, args.seed,
            engineering_proof=args.engineering_proof,
        )
        settings = vars(args).copy()
        settings.update(parent=str(args.parent), output=str(root),
                        engineering_proof=str(args.engineering_proof) if args.engineering_proof else None)
        settings["telemetry_run"] = f"{os.getpid()}-{time.time_ns()}"
        observers = PublicationTelemetry(root, settings["telemetry_run"])
        write_json(
            root / "execution.json",
            dict(settings, pid=os.getpid(), started=time.time(), binding=plan["hash"]),
        )
        queues = ordered_units(root, plan, args.only_ticker, args.pilot_unit)
        planned_units = len(queues)
        catalog = Catalog(root)
        remaining = [u for u in queues if not catalog.is_complete(u)]
        initially_complete = planned_units - len(remaining)
        worker_queues, assignments = assign(remaining, plan["jobs"], args.workers,
                                           heavy_first=bool(plan.get('continuation')))
        assignment = dict(version="ticker-affinity-v1", binding=plan["hash"],
                          attempt=settings["telemetry_run"], initially_complete=initially_complete,
                          planned_units=planned_units, workers=assignments)
        assignment["hash"] = digest(assignment)
        (root / "worker-assignments").mkdir(exist_ok=True)
        write_json(root / "worker-assignments" / (settings["telemetry_run"] + ".json"), assignment)
        queues = worker_queues
        worker_done = [0] * args.workers
        worker_deferred = [0] * args.workers
        cpu_slots = {}

        def queued_count():
            return sum(map(len, queues))

        def worker_progress():
            return [dict(slot=i, total=a["tasks"], tickers=len(a["tickers"]),
                         completed=worker_done[i], deferred=worker_deferred[i])
                    for i, a in enumerate(assignments)]

        def io_progress():
            cpu = worker_progress()
            return [dict(slot=i,
                         total=sum(a["total"] for a in cpu if a["slot"] % args.io_workers == i),
                         tickers=sum(a["tickers"] for a in cpu if a["slot"] % args.io_workers == i),
                         completed=sum(a["completed"] for a in cpu if a["slot"] % args.io_workers == i),
                         deferred=sum(a["deferred"] for a in cpu if a["slot"] % args.io_workers == i))
                    for i in range(args.io_workers)]

        ctx = mp.get_context("spawn")
        active_query, lock, stop = ctx.Value("i", 0), ctx.Lock(), ctx.Event()
        reserved_query=ctx.Value('q',0);dense=ctx.BoundedSemaphore(2)
        dense_active=ctx.Value('i',0);dense_attempts=ctx.Value('q',0)
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
        deferred_by_split = {0: 0, 1: 0}
        counters = dict(
            admitted_months_this_run=0,
            completed_months_this_run=0,
            published_sessions_this_run=0,
            deferred_sessions=0,
            deferred_source_origins=0,
            capacity_deferred=0,
        )
        counters['deferred_sessions'] = catalog.deferred_sessions
        counters['deferred_source_origins'] = catalog.deferred_source_origins
        began, last_report = time.monotonic(), float("-inf")
        panel_guard = TrainingGuard(args.training_campaign,args.workers,args.validation_lanes)
        last_index, indexed_units = float("-inf"), -1
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
                    catalog.complete_month(group, body)
                    counters["completed_months_this_run"] += 1
                worker_done[g["slot"]] += 1
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
            prior_count = catalog.deferred_sessions
            sealed = catalog.defer(key, body)
            if catalog.deferred_sessions != prior_count:
                counters['deferred_sessions'] += 1
                counters['deferred_source_origins'] += body['stats']['source_origins']
            return sealed

        with ExitStack() as pools:
            # Retain diagnostic handles: repeatedly opening the growing monthly
            # log can stall the coordinator while completed workers wait.
            # Line buffering preserves per-record visibility and close-on-exit.
            timing_log = pools.enter_context((root / "timing.jsonl").open("a", buffering=1))
            monthly_log = pools.enter_context((root / "monthly-timing.jsonl").open("a", buffering=1))
            # A dedicated single-process executor is the ownership guarantee;
            # a shared ProcessPoolExecutor does not offer worker affinity.
            owners = [pools.enter_context(ProcessPoolExecutor(
                1, mp_context=ctx, initializer=initialize,
                initargs=(str(root), plan, dict(settings, worker_slot=i,
                          planned_tasks=assignments[i]["tasks"],
                          planned_tickers=len(assignments[i]["tickers"])),
                          active_query, lock, stop,reserved_query,dense,dense_active,dense_attempts))) for i in range(args.workers)]
            io_owners = [pools.enter_context(ThreadPoolExecutor(
                1, thread_name_prefix=f"ar-shard-io-{i}")) for i in range(args.io_workers)]
            while queued_count() or cpus or ios or staged:
                if (root / "STOP").exists():
                    stop.set()
                busy = set(cpu_slots.values())
                for slot, queue in enumerate(queues):
                    if slot in busy or len(staged) >= args.io_workers * 2 or stop.is_set():
                        continue
                    while queue:
                        if args.max_months and counters["admitted_months_this_run"] >= args.max_months:
                            break
                        unit = queue.popleft()
                        split = int(unit["split"])
                        if (root / "STOP").exists():
                            stop.set()
                            queue.appendleft(unit)
                            break
                        if capacity_status(catalog.split_bytes, reserved, caps, rejected, deferred_by_split, args.io_workers * 2)["validation" if split else "training"]["blocked"]:
                            counters["capacity_deferred"] += 1
                            deferred_by_split[split] += 1
                            worker_deferred[slot] += 1
                            continue
                        future = owners[slot].submit(produce, unit)
                        cpus[future] = unit
                        cpu_slots[future] = slot
                        counters["admitted_months_this_run"] += 1
                        break
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
                            deferred_by_split[split] += 1
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
                        io_slot = groups[key]["slot"] % args.io_workers
                        ios[
                            io_owners[io_slot].submit(
                                publication,
                                root,
                                plan,
                                unit,
                                result,
                                args.reserve_disk_gib,
                                observers,
                                io_slot,
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
                    # Draining only short-session deferrals/capacity rejections
                    # may create no I/O future. Queued CPU work must still run.
                    if (queued_count() and not stop.is_set()
                        and not (args.max_months and counters["admitted_months_this_run"] >= args.max_months)):
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
                        timing_log.write(json.dumps(dict(unit=unit, **timing)) + "\n")
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
                    slot = cpu_slots.pop(future)
                    key = month_key(unit)
                    try:
                        outputs = future.result()
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
                                "resource_page_splits",
                                "coarse_cache_hits",
                                "context_memory_hits", "context_disk_hits", "context_source_sessions",
                                'memory_retries',
                                'cached_eligibility_sessions','inventory_empty_sessions','eligibility_unknown_sessions',
                                'eligibility_cache_fragment_fallbacks',
                            )
                        }
                        monthly_log.write(
                            json.dumps(
                                dict(unit=unit, sessions=len(outputs), **totals)
                            )
                            + "\n"
                        )
                        groups[key] = dict(
                            unit=unit, slot=slot,
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
                        if isinstance(error,InterruptedError) and stop.is_set():
                            continue  # Incomplete month stays absent from the resume ledger.
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
                    active_queries=0 if plan.get('overlap') else active_query.value,
                    active_cache_reads=active_query.value if plan.get('overlap') else 0,
                    source_mode='verified parent pools' if plan.get('overlap') else 'canonical queries',
                    queued_units=queued_count(),
                    planned_units=planned_units,
                    active_months=len(cpus)+len(groups),
                    accounted_months=initially_complete+sum(worker_done)+sum(worker_deferred),
                    worker_assignments=worker_progress(),
                    io_assignments=io_progress(),
                    query_limit=panel_guard.refresh(),
                    query_memory_gib_base=args.query_memory_gib,query_memory_gib_max=args.query_max_memory_gib,
                    reserved_query_gib=reserved_query.value/1024**3,active_dense_queries=dense_active.value,
                    dense_query_attempts=dense_attempts.value,ram_available_gib=psutil.virtual_memory().available/1024**3,
                    capacity=capacity_status(catalog.split_bytes, reserved, caps, rejected, deferred_by_split, args.io_workers * 2),
                    elapsed_seconds=time.monotonic() - began,
                    failure=failure,
                    **counters,
                    **catalog.stats(),
                )
                catalog.flush()
                write_json(root / "progress.json", snapshot)
                if snapshot["units"] != indexed_units and time.monotonic() - last_index >= 60:
                    catalog.inspection_catalog(plan)
                    last_index, indexed_units = time.monotonic(), snapshot["units"]
                if time.monotonic() - last_report >= 15:
                    print(json.dumps(snapshot), flush=True)
                    last_report = time.monotonic()
                if stop.is_set() and not cpus and not ios and not staged:
                    break
        selection = catalog.export(plan["hash"])
        catalog.inspection_catalog(plan)
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
                        ("capacity_reached" if all(v["blocked"] for v in capacity_status(
                            catalog.split_bytes, reserved, caps, rejected, deferred_by_split, args.io_workers * 2).values())
                         else "inventory_exhausted") if counters["capacity_deferred"]
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
            planned_units=planned_units,
            accounted_months=initially_complete+sum(worker_done)+sum(worker_deferred),
            worker_assignments=worker_progress(),
            io_assignments=io_progress(),
            active_months=0,
            queued_units=queued_count(),
            query_memory_gib_base=args.query_memory_gib,query_memory_gib_max=args.query_max_memory_gib,
            reserved_query_gib=reserved_query.value/1024**3,active_dense_queries=dense_active.value,
            dense_query_attempts=dense_attempts.value,
            capacity=capacity_status(catalog.split_bytes, reserved, caps, rejected, deferred_by_split, args.io_workers * 2),
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
        try:
            if catalog is not None:catalog.close()
        finally:
            if observers is not None:observers.close()
            if STATE and STATE.get("telemetry") and mp.current_process().name=="MainProcess":
                STATE["telemetry"].close()
        lease.close()


if __name__ == "__main__":
    raise SystemExit(main())
