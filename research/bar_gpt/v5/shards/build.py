"""Restartable concurrent shard campaign; model code is never imported here."""

import argparse
from collections import deque
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import date, datetime, timezone
import json
from itertools import islice
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time

import exchange_calendars as xcals
import numpy as np

from . import source
from .contract import CHANNELS, FRAMES, SAMPLE_DTYPE, VERSION, digest, file_hash, write_json
from .sql import clock, day_query
from .storage import BAR_DTYPE, Budget, coarse_samples, publish, unpack_bars, verify_receipt
from .recovery import resolve_binding, identity_query
from research.bar_gpt.v5 import bar_semantics_v1

# One enlarged query may run alongside the normal pool. Normal queries reserve
# at most 64 GiB collectively; this lane reserves at most 32 GiB, totaling 96.
HEAVY_QUERY = threading.Semaphore(1)


def query_with_memory_recovery(c, statement, query_id, stop, status, worker):
    """Return unchanged Arrow output, retrying only explicit CH memory errors.

    Each worker owns its client/settings. Enlarged retries serialize so rare
    dense sessions cannot exhaust server memory or silently lose source coverage.
    All limits are finite; failure at 32 GiB still drains the campaign visibly.
    """
    try:
        return source.arrow(c, statement, query_id)
    except RuntimeError as error:
        if "MEMORY_LIMIT_EXCEEDED" not in str(error):
            raise
    status.update(worker, phase="waiting for memory lane")
    while not HEAVY_QUERY.acquire(timeout=1):
        if stop.is_set():
            return None
    original = c.default_query_params["max_memory_usage"]
    original_threads = c.default_query_params["max_threads"]
    try:
        c.default_query_params["max_threads"] = 4  # Dense-session benchmark: 1.5x faster than two threads.
        for gib in (4, 8, 16, 32):
            if stop.is_set():
                return None
            c.default_query_params["max_memory_usage"] = gib * 1024**3
            with status.lock:
                status.retries += 1
            status.update(worker, phase=f"memory retry {gib} GiB")
            try:
                return source.arrow(c, statement, f"{query_id}-memory-{gib}")
            except RuntimeError as error:
                if "MEMORY_LIMIT_EXCEEDED" not in str(error) or gib == 32:
                    raise
    finally:
        c.default_query_params["max_memory_usage"] = original
        c.default_query_params["max_threads"] = original_threads
        HEAVY_QUERY.release()


class Status:
    """Small synchronized worker snapshots; no market arrays in progress state."""

    def __init__(self, workers):
        self.lock = threading.Lock()
        self.workers = [dict(id=i+1, phase="queued", done=0, total=0, ticker="", day="")
                        for i in range(workers)]
        self.samples, self.reused, self.retries = 0, 0, 0
        self.frames = {str(f): 0 for f in FRAMES}

    def update(self, worker, **values):
        with self.lock:
            self.workers[worker].update(values, updated=time.time())

    def accepted(self, receipt, reused=False):
        with self.lock:
            self.samples += receipt["samples"]
            self.reused += int(reused)
            for frame, count in receipt["frames"].items():
                self.frames[frame] += count

    def snapshot(self):
        with self.lock:
            return dict(workers=[dict(w) for w in self.workers], samples=self.samples,
                        reused=self.reused, retries=self.retries, frames=dict(self.frames))


def sessions(year):
    """Historical trading dates; extended bars span 04:00–20:00 New York."""
    calendar = xcals.get_calendar("XNYS", start=f"{year}-01-01", end=f"{year}-12-31")
    return [str(d.date()) for d in calendar.sessions]


def run_job(worker, job, args, binding, rules, calendar, budget, status, disk, stop, split_rows):
    """One ticker-year, bounded day queries and at most two pending disk writes.

    Day receipts are restart boundaries. Coarse windows never cross a missing
    ticker-session, a year boundary, or the development split. Unknown halt
    state is masked until explicit evidence establishes it; no silence inference.
    """
    ticker, year = job["ticker"], job["year"]
    actions = [r for r in split_rows if r["provider_ticker"] == ticker]
    key = f"{year}-{digest(ticker)[:20]}"
    directory = args.output / "jobs" / key
    directory.mkdir(parents=True, exist_ok=True)
    c = source.client(args.endpoint, args.query_memory_gib)
    pending = deque()
    coarse_segment, segments = [], []
    initial_state, previous_split, previous_close = 2, None, None
    days = calendar[year]
    if args.max_days:
        days = days[:args.max_days]
    status.update(worker, ticker=ticker, year=year, total=len(days), done=0, phase="index")
    try:
        # Concurrent startup can briefly exhaust the endpoint's accept queue.
        # Retry transport failures only; integrity errors must still fail closed.
        for attempt in range(4):
            try:
                bounds = source.index(c, ticker, year)
                break
            except (ConnectionError, TimeoutError, OSError):
                if attempt == 3:
                    raise
                with status.lock:
                    status.retries += 1
                status.update(worker, phase="index connection retry")
                if stop.wait(2 ** attempt):
                    return False
        index_path = directory / "index.json"
        if index_path.exists():
            if json.loads(index_path.read_text()) != bounds:
                raise ValueError("Frozen ordinal inventory changed; use a new dataset version.")
        else:
            write_json(index_path, bounds)
        identity = digest(dict(binding=binding, ticker=ticker, year=year, bounds=bounds))
        complete = directory / "complete.json"
        if complete.exists():
            saved = json.loads(complete.read_text())
            if saved["identity"] != identity:
                raise ValueError("Completed ticker-year identity changed.")
            status.update(worker, phase="reused", done=len(days))
            return True

        def collect_one():
            receipt = pending.popleft().result()
            if receipt is None:
                stop.set()
                return False
            status.accepted(receipt)
            status.update(worker, done=status.workers[worker]["done"] + 1)
            return True

        skipped = []
        for day in days:
            if stop.is_set() or (args.output / "STOP").exists():
                stop.set()
                break
            if shutil.disk_usage(args.output).free < args.free_reserve_gb * 10**9:
                raise RuntimeError("Free-space reserve reached; draining campaign.")
            # Index intervals denote actual source coverage; an absent symbol-day
            # is recorded as unavailable rather than manufactured all-zero data.
            covered = any(int(r["first_sip_timestamp_us"]) < clock(day, 20)
                          and int(r["last_sip_timestamp_us"]) >= clock(day, 4) for r in bounds)
            if not covered:
                if coarse_segment:
                    segments.append(coarse_samples(coarse_segment, actions)); coarse_segment = []
                initial_state = 2
                previous_close = None
                skipped.append(dict(day=day, reason="ticker_session_absent_from_index"))
                status.update(worker, done=status.workers[worker]["done"] + 1)
                continue
            development = int(day >= args.development_start)
            if previous_split is not None and previous_split != development:
                segments.append(coarse_samples(coarse_segment, actions)); coarse_segment = []
            previous_split = development
            day_initial_state, day_previous_close = initial_state, previous_close
            day_id = digest(dict(job=identity, day=day, initial_state=initial_state,
                                 previous_close=previous_close))
            target = directory / day
            saved = verify_receipt(target, day_id)
            # Recompute transient coarse bars for a partial job. Fully completed
            # ticker-years still reuse their receipts without any event rescan.
            # Two writes per extraction worker bound queued arrays and disk load.
            if len(pending) >= 2 and not collect_one():
                break
            status.update(worker, day=day, phase="query", query_started=time.time())
            statement = day_query(ticker, day, bounds, rules, initial_state, args.seed,
                                  args.keep_percent, previous_close)
            began = time.perf_counter()
            result = None
            for attempt in range(3):
                try:
                    result = query_with_memory_recovery(
                        c, statement, f"bargpt-{key}-{day}-{attempt}", stop, status, worker)
                    break
                except (ConnectionError, TimeoutError, OSError):
                    if attempt == 2:
                        raise
                    with status.lock:
                        status.retries += 1
                    status.update(worker, phase="retrying")
                    if stop.wait(2 ** attempt):
                        break
            if result is None:
                break
            status.update(worker, phase="packing", query_seconds=time.perf_counter()-began)
            samples, coarse = unpack_bars(result, development)
            initial_state = int(result["final_state"][0])
            previous_close = clock(day, 20)
            if result["source_hash"].n_unique() != 1:
                raise ValueError("Source evidence changed inside query response.")
            # Existing publications keep their original logical SQL identity;
            # the execution hash below makes the optimized path independently auditable.
            logical_statement = (statement if args.identity_query is day_query else
                                 args.identity_query(ticker, day, bounds, rules, day_initial_state,
                                                     args.seed, args.keep_percent, day_previous_close))
            metadata = dict(identity=day_id, ticker=ticker, year=year, day=day,
                            source_hash=result["source_hash"][0],
                            query_hash=digest(logical_statement),
                            execution_query_hash=digest(statement),
                            reporting_counts=result["reporting_counts"][0],
                            query_seconds=time.perf_counter()-began,
                            final_state=initial_state, binding=binding)
            coarse_segment.append(coarse)
            if saved:
                if any(saved[key] != metadata[key] for key in ("source_hash", "query_hash", "final_state")):
                    raise ValueError("Canonical replay differs from the saved day receipt.")
                status.update(worker, day=day, phase="reused", done=status.workers[worker]["done"] + 1)
                continue
            pending.append(disk.submit(publish, target, samples, coarse, metadata, budget,
                                       args.shard_mib * 1024**2))
            status.update(worker, phase="writing", io_pending=len(pending))
        while pending:
            collect_one()
        if stop.is_set():
            status.update(worker, phase="stopped", io_pending=0)
            return False
        segments.append(coarse_samples(coarse_segment, actions))
        samples = np.concatenate(segments) if segments else np.empty(0, dtype=SAMPLE_DTYPE)
        metadata = dict(identity=digest(dict(job=identity, kind="coarse")), ticker=ticker,
                        year=year, day="coarse", binding=binding)
        existing = verify_receipt(directory / "coarse", metadata["identity"])
        if not existing:
            saved = disk.submit(publish, directory / "coarse", samples,
                                np.empty(0, dtype=BAR_DTYPE), metadata, budget,
                                args.shard_mib * 1024**2).result()
            if saved is None:
                stop.set()
                return False
            status.accepted(saved)
        write_json(complete, dict(identity=identity, skipped=skipped, status="complete"))
        status.update(worker, phase="complete", io_pending=0)
        return True
    finally:
        # Do not leave a background writer alive after a failed query.
        while pending:
            pending.popleft().result()
        if getattr(c, "_bar_arrow_connection", None):
            c._bar_arrow_connection.close()
        c.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("D:/TradingML/runtimes/bar_gpt/shards-2019-2025-v3"))
    parser.add_argument("--target-bytes", type=int, default=10**12)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--io-workers", type=int, default=2)
    parser.add_argument("--query-memory-gib", type=float, default=3)
    parser.add_argument("--shard-mib", type=int, default=32)
    parser.add_argument("--free-reserve-gb", type=int, default=100)
    parser.add_argument("--keep-percent", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20261106)
    parser.add_argument("--endpoint")
    parser.add_argument("--source-revision", default="working-tree")
    parser.add_argument("--resume-code-receipt", type=Path,
                        help="Audited engineering-only migration; preserve existing data seals.")
    parser.add_argument("--tickers", default="")
    parser.add_argument("--years", default="2019,2020,2021,2022,2023,2024,2025")
    parser.add_argument("--max-days", type=int, default=0, help="Pilot only; separate output root.")
    parser.add_argument("--max-jobs", type=int, default=0, help="Pilot only; separate output root.")
    parser.add_argument("--development-start", default="2025-10-01")
    args = parser.parse_args(argv)
    years = sorted(set(map(int, args.years.split(","))))
    if not years or min(years) < 2019 or max(years) > 2025:
        parser.error("years must be within 2019–2025")
    # Reserve 32 GiB for the single heavy-query lane, within 96 GiB total.
    if not (1 <= args.workers <= 64 and 1 <= args.io_workers <= 16
            and 1 <= args.keep_percent <= 100 and 1 <= args.shard_mib <= 64
            and 0 < args.target_bytes <= 10**12 and 0 < args.query_memory_gib
            and args.workers * args.query_memory_gib <= 64):
        parser.error("Invalid bounded resource or byte-limit settings.")
    root = Path("D:/TradingML/runtimes").resolve()
    args.output = args.output.resolve()
    if not root.is_dir() or not args.output.is_relative_to(root):
        parser.error("Output must be beneath the available D:/TradingML/runtimes authority.")
    args.output.mkdir(parents=True, exist_ok=True)
    # OS byte-range locking automatically releases after a controller crash.
    import msvcrt
    with (args.output / "controller.lock").open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"0"); lock.flush()
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        return campaign(args, years)


def campaign(args, years):
    """Own all worker lifetimes; publish status separately from completed data."""
    started = time.time()
    status = Status(args.workers)
    stop = threading.Event()
    state, completed, failed = "preflight", 0, []
    budget = Budget(args.target_bytes)
    total = 0
    active = {}
    interrupted = False
    status_publish_errors = 0

    def snapshot():
        nonlocal status_publish_errors
        payload = dict(
            status.snapshot(), state=state, pid=os.getpid(), updated=time.time(),
            elapsed=time.time()-started, bytes=budget.used, reserved=budget.reserved,
            target_bytes=budget.limit, jobs_total=total, jobs_completed=completed,
            jobs_active=len(active), jobs_queued=max(0,total-completed-len(active)-len(failed)),
            failed=failed, output=str(args.output), status_publish_errors=status_publish_errors)
        try:
            write_json(args.output / "progress.json", payload)
        except PermissionError:
            # A busy observer must not abort correctly committed data work.
            # Required contract/shard receipt failures still propagate normally.
            status_publish_errors += 1
            print(f"Progress publication blocked by a reader; retrying next update "
                  f"(publication failures={status_publish_errors}).", flush=True)

    snapshot()
    c = source.client(args.endpoint, args.query_memory_gib)
    try:
        authority = source.preflight(c)
        args.endpoint = c.base_url  # All workers use the endpoint actually verified by preflight.
        authority["corporate_actions"] = source.split_authority(c, years)
        if shutil.disk_usage(args.output).free < args.target_bytes + args.free_reserve_gb * 10**9:
            # Resume may already occupy much of the requested capacity.
            existing_size = sum(p.stat().st_size for p in args.output.rglob("*") if p.is_file())
            if shutil.disk_usage(args.output).free + existing_size < args.target_bytes + args.free_reserve_gb * 10**9:
                raise ValueError("Insufficient free space for target plus reserve.")
        source_files = [*Path(__file__).parent.glob("*.py"),
                        Path(bar_semantics_v1.__file__),
                        Path(source.__file__).parents[4] / "pipelines/market_sip/events/market_day_sql.py",
                        Path(source.__file__).parents[4] / "pipelines/market_sip/events/market_signals.py"]
        contract = dict(version=VERSION, channels=CHANNELS, frames=FRAMES, width=64,
                        bar_semantics=bar_semantics_v1.CONTRACT,
                        sample_bytes=SAMPLE_DTYPE.itemsize, seed=args.seed, keep_percent=args.keep_percent,
                        years=years, tickers=args.tickers, max_days=args.max_days, max_jobs=args.max_jobs,
                        development_start=args.development_start, target_bytes=args.target_bytes,
                        authority=authority, code={str(p.name): file_hash(p) for p in source_files},
                        session="XNYS dates; intraday 04:00–20:00, daily 09:30–16:00 America/New_York; coarse prices/sizes in sample-end split basis",
                        sampling="session-local fine windows with masked right padding; random-offset hash retention; full cross-session hourly/daily windows",
                        padding="end_us=0, values=0, valid=0; only a fine session's final selected block may be partial",
                        transform="unscaled Float32; masks separate; no fitted normalization or augmentation",
                        evidence="canonical finalized events; no assertion of causal correction-pair reconstruction")
        binding = digest(contract)
        path = args.output / "contract.json"
        if path.exists():
            binding = resolve_binding(json.loads(path.read_text()), contract, args.resume_code_receipt)
        else:
            write_json(path, dict(binding=binding, contract=contract))
        args.identity_query = identity_query(args.resume_code_receipt)
        write_json(args.output / "launch.json", dict(source_revision=args.source_revision,
                   workers=args.workers, io_workers=args.io_workers, query_memory_gib=args.query_memory_gib,
                   started=datetime.now(timezone.utc).isoformat(), pid=os.getpid(), binding=binding,
                   current_code=contract["code"], resume_code_receipt=str(args.resume_code_receipt)))
        state = "inventory"; snapshot()
        jobs = source.plan(c, args.output, args.seed, tuple(filter(None, args.tickers.split(","))), years)
        if args.max_jobs:
            jobs = jobs[:args.max_jobs]
        total = len(jobs)
        # Startup validates receipt integrity and file sizes. Per-job reuse also
        # verifies file SHA-256 before using any cached market aggregate.
        state = "resuming"; snapshot()
        # Parallelize metadata I/O with a bounded 512-receipt batch. Previously,
        # half a million tiny serialized reads stalled every restart for minutes.
        # Validation is identical; only the read scheduling changes.
        receipt_paths = args.output.glob("jobs/*/*/receipt.json")
        last_resume_update = time.monotonic()
        def restore_receipt(path):
            return verify_receipt(path.parent, hashes=False)
        with ThreadPoolExecutor(args.io_workers, thread_name_prefix="bar-resume") as readers:
            while batch := list(islice(receipt_paths, 512)):
                for receipt in readers.map(restore_receipt, batch):
                    status.accepted(receipt, reused=True)
                    budget.used += receipt["bytes"]  # Startup progress; exact all-file usage is reconciled below.
                if time.monotonic() - last_resume_update >= 1:
                    snapshot()
                    last_resume_update = time.monotonic()
        budget.used = sum(p.stat().st_size for p in args.output.rglob("*") if p.is_file())
        calendar = {year: sessions(year) for year in years}
        state = "running"; snapshot()
        iterator = iter(jobs)
        active = {}
        last_print = 0
        with ThreadPoolExecutor(args.io_workers, thread_name_prefix="bar-disk") as disk:
            with ThreadPoolExecutor(args.workers, thread_name_prefix="bar-query") as pool:
                def admit(worker):
                    job = next(iterator, None)
                    if job is not None and not stop.is_set():
                        active[pool.submit(run_job, worker, job, args, binding, authority["rules"],
                                           calendar, budget, status, disk, stop,
                                           authority["corporate_actions"]["rows"])] = worker
                for worker in range(args.workers):
                    admit(worker)
                while active:
                    try:
                        ready, _ = wait(active, timeout=1, return_when=FIRST_COMPLETED)
                    except KeyboardInterrupt:
                        interrupted = True; stop.set(); state = "draining"; continue
                    if (args.output / "STOP").exists():
                        stop.set(); state = "draining"
                    for future in ready:
                        worker = active.pop(future)
                        try:
                            completed += int(bool(future.result()))
                        except Exception as error:
                            failed.append(dict(worker=worker+1, error=str(error)[:1800]))
                            status.update(worker, phase="failed")
                            stop.set(); state = "draining"
                        admit(worker)
                    snapshot()
                    if time.time() - last_print >= 30:
                        print(f"{state}: {budget.used/10**9:.3f}/{budget.limit/10**9:.0f} GB; "
                              f"{status.samples:,} samples; {completed}/{total} jobs; failures={len(failed)}", flush=True)
                        last_print = time.time()
        if failed:
            state = "failed"
        elif stop.is_set():
            state = "stopped" if interrupted or (args.output / "STOP").exists() else "budget_reached"
        else:
            state = "scope_complete" if args.max_jobs or args.max_days else "inventory_exhausted"
        snapshot()
        write_json(args.output / "result.json", dict(state=state, bytes=budget.used,
                   target_bytes=budget.limit, samples=status.samples, frames=status.frames,
                   jobs_completed=completed, jobs_total=total, failed=failed))
        return int(bool(failed))
    except Exception as error:
        state = "failed"; failed.append(dict(error=str(error)[:1800])); snapshot()
        raise
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
