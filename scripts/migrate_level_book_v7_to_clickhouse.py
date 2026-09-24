"""Migrate frozen V7 JSON checkpoints into compact arte ClickHouse tables."""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import contextmanager, nullcontext
import json
import multiprocessing
import os
from pathlib import Path
import signal
import sys
import threading
import time

os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from rich.console import Console
from rich.live import Live
from rich.table import Table

from research.level_book.v7.campaign import discover_clickhouse_env_files, load_env_files
from research.level_book.v7.campaign_store import read, verified_book
from research.level_book.v7.clickhouse_persistence import (
    OBSERVATIONS_TABLE, COVERAGE_TABLE, DATABASE, DDL, LEVELS_TABLE, POLICY,
    EXPECTED_COLUMNS, PERSISTENCE_VERSION,
    canonical_json, compact_checkpoints, datetime64_ns, epoch_ns,
    source_plan_digest,
)
from research.mlops.clickhouse import (
    ClickHouseHttpClient, default_clickhouse_password, default_clickhouse_url,
    default_clickhouse_user,
)
from src.backend.swing_book_source import session_bounds
from src.runtime_paths import WORKSTATION_RUNTIME_ROOT

DEFAULT_SOURCE = WORKSTATION_RUNTIME_ROOT / "level-book-v7" / "all-tradable-20250101-20260912-mle-v1"
DEFAULT_RUNTIME = WORKSTATION_RUNTIME_ROOT / "level-book-v7" / "arte-migration-v2"
STOP = threading.Event()
GIB = 1024 ** 3
INSERT_GATE = None


@contextmanager
def exclusive_controller(path: Path):
    """Hold one cross-process controller lock for this migration runtime."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0); stream.write(b"0"); stream.flush(); stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError(
                f"Another V7 arte migration controller is active for {path.parent}"
            ) from exc
        yield


def worker_budget(requested: int | None) -> dict[str, int | float]:
    """Use real CPU parallelism while retaining RAM for ClickHouse and the OS."""
    cpus = os.cpu_count() or 4
    available = available_memory_bytes()
    cpu_slots = max(1, cpus - max(2, (cpus + 7) // 8))
    # A worker streams gzip checkpoints, but retains compact intervals and the
    # terminal full book. Budget 1 GiB per process and reserve 25% of free RAM.
    memory_slots = max(1, int(available * .75 // GIB))
    maximum = min(60 if os.name == "nt" else 64, cpu_slots, memory_slots)
    chosen = maximum if requested is None else requested
    if not 1 <= chosen <= maximum:
        raise ValueError(
            f"Requested {chosen} workers; current CPU/RAM budget permits 1..{maximum}"
        )
    return {
        "workers": chosen,
        "maximum_workers": maximum,
        "logical_cpus": cpus,
        "available_gib": round(available / GIB, 2),
    }


def available_memory_bytes() -> int:
    if os.name == "nt":
        import ctypes
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong), ("memory_load", ctypes.c_ulong),
                ("total_physical", ctypes.c_ulonglong), ("available_physical", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong), ("available_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong), ("available_virtual", ctypes.c_ulonglong),
                ("available_extended_virtual", ctypes.c_ulonglong),
            ]
        status = MemoryStatus(); status.length = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise OSError("GlobalMemoryStatusEx failed")
        return int(status.available_physical)
    page_size = os.sysconf("SC_PAGE_SIZE")
    available_pages = os.sysconf("SC_AVPHYS_PAGES")
    return int(page_size * available_pages)


def initialize_worker(insert_gate) -> None:
    # The controller alone interprets Ctrl+C and drains already-admitted tickers.
    global INSERT_GATE
    INSERT_GATE = insert_gate
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    load_env_files(discover_clickhouse_env_files(), verbose=False)


def client(*, readonly: bool = False) -> ClickHouseHttpClient:
    params = {"max_threads": 2, "max_memory_usage": 2 * 1024**3}
    if readonly:
        params["readonly"] = 1
    return ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password(),
        timeout_seconds=300, persistent=True, default_query_params=params)


def query(c: ClickHouseHttpClient, sql: str) -> list[dict]:
    return [json.loads(line) for line in c.execute(sql + " FORMAT JSONEachRow").splitlines() if line]


def preflight(c: ClickHouseHttpClient) -> None:
    policy = query(c, f"SELECT disks FROM system.storage_policies WHERE policy_name='{POLICY}'")
    if policy != [{"disks": [POLICY]}]:
        raise ValueError(f"Required SSD-only {POLICY} policy is unavailable")
    legacy = query(c, f"SELECT name FROM system.tables WHERE database='{DATABASE}' AND name='structural_level_builder_checkpoint_v7'")
    if legacy:
        raise ValueError("Legacy V7 JSON checkpoint table still exists; retire it before running V2 migration")
    for statement in DDL:
        c.execute(statement)
    tables = query(c, f"SELECT name,engine,partition_key,sorting_key,storage_policy FROM system.tables WHERE database='{DATABASE}' ORDER BY name")
    expected = {name.split(".", 1)[1] for name in (LEVELS_TABLE, COVERAGE_TABLE, OBSERVATIONS_TABLE)}
    found = {row["name"] for row in tables if row["name"] in expected}
    if found != expected or any(row["storage_policy"] != POLICY for row in tables if row["name"] in expected):
        raise ValueError("V7 ClickHouse table policy or schema is incomplete")
    columns = query(c, f"SELECT table,name FROM system.columns WHERE database='{DATABASE}' AND table IN ({','.join(repr(x) for x in sorted(expected))}) ORDER BY table,position")
    actual_columns = {table: tuple(row["name"] for row in columns if row["table"] == table) for table in expected}
    if actual_columns != EXPECTED_COLUMNS:
        raise ValueError(f"V7 ClickHouse columns differ from {PERSISTENCE_VERSION}; use a reviewed schema migration")
    layouts = {row["name"]: (row["engine"], row["partition_key"], row["sorting_key"])
        for row in tables if row["name"] in expected}
    required_layouts = {
        "structural_levels_v7": ("ReplacingMergeTree", "cityHash64(ticker) % 64", "ticker, level_id, valid_from"),
        "structural_level_coverage_v7": ("ReplacingMergeTree", "toYYYYMM(session_date)", "ticker, session_date"),
        "structural_level_observations_v7": ("ReplacingMergeTree", "cityHash64(ticker) % 64", "ticker, observation_id, valid_from"),
    }
    if layouts != required_layouts:
        raise ValueError(f"V7 ClickHouse engine, partition or ordering differs from {PERSISTENCE_VERSION}")
    critical_types = {(row["table"], row["name"]): row["type"] for row in query(c,
        f"SELECT table,name,type FROM system.columns WHERE database='{DATABASE}' AND table IN ({','.join(repr(x) for x in sorted(expected))}) AND name IN ('valid_from','valid_to','available_at','published_at','state_hash','source_plan_hash','band_config_hash','observation_id','at','resolved_at')")}
    required_types = {
        ("structural_levels_v7", "valid_from"): "DateTime64(9, 'UTC')",
        ("structural_levels_v7", "valid_to"): "Nullable(DateTime64(9, 'UTC'))",
        ("structural_levels_v7", "state_hash"): "FixedString(64)",
        ("structural_level_coverage_v7", "available_at"): "DateTime64(9, 'UTC')",
        ("structural_level_coverage_v7", "published_at"): "DateTime64(9, 'UTC')",
        ("structural_level_coverage_v7", "source_plan_hash"): "FixedString(64)",
        ("structural_level_coverage_v7", "band_config_hash"): "FixedString(64)",
        ("structural_level_observations_v7", "observation_id"): "FixedString(64)",
        ("structural_level_observations_v7", "valid_from"): "DateTime64(9, 'UTC')",
        ("structural_level_observations_v7", "valid_to"): "Nullable(DateTime64(9, 'UTC'))",
        ("structural_level_observations_v7", "at"): "DateTime64(9, 'UTC')",
        ("structural_level_observations_v7", "resolved_at"): "DateTime64(9, 'UTC')",
    }
    if critical_types != required_types:
        raise ValueError(f"V7 ClickHouse timestamp or digest types differ from {PERSISTENCE_VERSION}")
    misplaced = query(c, f"SELECT table,disk_name FROM system.parts WHERE active AND database='{DATABASE}' AND table IN ({','.join(repr(x) for x in sorted(expected))}) AND disk_name!='{POLICY}' LIMIT 1")
    if misplaced:
        raise ValueError(f"V7 ClickHouse parts are misplaced: {misplaced[0]}")


def insert(c: ClickHouseHttpClient, table: str, rows: list[dict], token: str, *, batch_rows: int, batch_bytes: int) -> None:
    pending: list[str] = []
    size = 0
    part = 0
    for row in rows:
        encoded = canonical_json(row)
        if pending and (len(pending) >= batch_rows or size + len(encoded) > batch_bytes):
            c.execute(f"INSERT INTO {table} SETTINGS insert_deduplication_token='{token}-{part}' FORMAT JSONEachRow\n" + "\n".join(pending))
            pending = []; size = 0; part += 1
        pending.append(encoded); size += len(encoded)
    if pending:
        c.execute(f"INSERT INTO {table} SETTINGS insert_deduplication_token='{token}-{part}' FORMAT JSONEachRow\n" + "\n".join(pending))


def ticker_directories(source: Path) -> list[Path]:
    root = source / "tickers"
    if not root.is_dir():
        raise ValueError(f"V7 ticker root is unavailable: {root}")
    return sorted((path for path in root.iterdir() if path.is_dir()), key=lambda p: p.name)


def migrate_ticker(path: Path, plan_hash: str, batch_rows: int, batch_bytes: int) -> dict:
    if STOP.is_set():
        return {"state": "queued", "path": str(path)}
    books = sorted((path / "books").glob("*.json.gz"))
    source_plan = read(path / "source-plan.json")
    ready = read(path / "ready.json") if (path / "ready.json").is_file() else {}
    ticker = str(ready.get("ticker") or source_plan["days"][0]["ticker"])
    read_seconds = 0.0
    compact_started = time.monotonic()
    if books:
        def verified_books():
            nonlocal read_seconds
            for book in books:
                started = time.monotonic()
                value = verified_book(book)
                read_seconds += time.monotonic() - started
                yield value
        intervals, observations, coverage = compact_checkpoints(verified_books(), plan_hash)
    else:
        intervals, observations, coverage = [], [], []
    compact_total_seconds = time.monotonic() - compact_started
    receipts_started = time.monotonic()
    coverage_by_day = {row["session_date"]: row for row in coverage}
    level_count = 0
    interval_count = 0
    for day in sorted(source_plan["days"], key=lambda row: row["source_date"]):
        session = day["source_date"]
        if session in coverage_by_day:
            level_count = coverage_by_day[session]["level_count"]
            interval_count = coverage_by_day[session]["interval_count"]
            continue
        receipt_path = path / "receipts" / f"{session}.json"
        if not receipt_path.is_file():
            continue
        receipt = read(receipt_path)
        if receipt.get("state") != "empty":
            raise ValueError(f"{ticker} {session}: missing book for nonempty receipt")
        _, end = session_bounds(session)
        stamp = epoch_ns(str(end.timestamp()))
        coverage.append({
            "ticker": ticker, "session_date": session, "available_at": datetime64_ns(stamp),
            "state": "empty", "level_count": level_count, "interval_count": interval_count,
            "source_input_hash": str(receipt.get("source_hash") or receipt.get("bar_hash") or ""),
            "source_checkpoint_hash": str(receipt.get("checkpoint_hash") or receipt.get("parent_hash") or ""),
            "parent_checkpoint_hash": str(receipt.get("parent_hash") or ""),
            "source_plan_hash": plan_hash, "publication_revision": stamp,
        })
    coverage.sort(key=lambda row: row["session_date"])
    receipt_seconds = time.monotonic() - receipts_started
    if not coverage:
        return {"state": "skipped", "ticker": ticker, "reason": "no_completed_receipts"}
    insert_started = time.monotonic()
    with INSERT_GATE if INSERT_GATE is not None else nullcontext():
        c = client()
        token = f"{PERSISTENCE_VERSION}-{plan_hash[:12]}-{ticker.encode().hex()}"
        insert(c, LEVELS_TABLE, intervals, token + "-levels", batch_rows=batch_rows, batch_bytes=batch_bytes)
        insert(c, OBSERVATIONS_TABLE, observations, token + "-observations", batch_rows=batch_rows, batch_bytes=batch_bytes)
        now_ns = time.time_ns()
        published_at = datetime64_ns(now_ns)
        for row in coverage:
            row["published_at"] = published_at
        # Coverage is the publication fence and is deliberately acknowledged last.
        insert(c, COVERAGE_TABLE, coverage, token + "-coverage", batch_rows=batch_rows, batch_bytes=batch_bytes)
    return {"state": "completed", "ticker": ticker, "sessions": len(coverage), "intervals": len(intervals),
        "observation_intervals": len(observations), "timings": {
            "read_verify_seconds": read_seconds,
            "compact_seconds": max(0.0, compact_total_seconds - read_seconds),
            "receipt_seconds": receipt_seconds,
            "insert_seconds": time.monotonic() - insert_started,
        }}


def render(counts: dict[str, int], total: int, active: dict[str, str], started: float, budget: dict) -> Table:
    table = Table(title="V7 → arte compact persistence", expand=True)
    table.add_column("State"); table.add_column("Count", justify="right")
    for state in ("active", "queued", "completed", "skipped", "failed"):
        table.add_row(state, str(counts.get(state, 0)))
    elapsed = max(time.monotonic() - started, 0.001)
    done = counts.get("completed", 0) + counts.get("skipped", 0) + counts.get("failed", 0)
    table.caption = (f"durable {done}/{total} tickers · {done/elapsed*60:.1f}/min · "
        f"{budget['workers']} processes · Ctrl+C stops admission and drains active tickers")
    for slot, ticker in sorted(active.items()):
        table.add_row(f"worker {slot}", ticker)
    return table


def run(args: argparse.Namespace) -> int:
    load_env_files(discover_clickhouse_env_files())
    if not args.source.is_dir() or not (args.source / "plan.json").is_file():
        raise ValueError(f"Frozen V7 source is unavailable: {args.source}")
    if args.runtime.resolve() == args.source.resolve() or args.runtime.is_relative_to(REPO):
        raise ValueError("Migration runtime must be separate from source and outside the repository")
    args.runtime.mkdir(parents=True, exist_ok=True)
    with exclusive_controller(args.runtime / "controller.lock"):
        return run_locked(args)


def run_locked(args: argparse.Namespace) -> int:
    budget = worker_budget(args.workers)
    if not 1 <= args.insert_workers <= budget["workers"]:
        raise ValueError(f"insert-workers must be 1..{budget['workers']}")
    budget["insert_workers"] = args.insert_workers
    plan_hash = source_plan_digest(args.source / "plan.json")
    paths = ticker_directories(args.source)
    c = client()
    preflight(c)
    other_plans = query(client(readonly=True),
        f"SELECT DISTINCT source_plan_hash FROM {COVERAGE_TABLE} WHERE source_plan_hash!='{plan_hash}' LIMIT 1")
    if other_plans:
        raise ValueError("V7 arte tables already contain another source plan; mixing campaigns is forbidden")
    prior = {row["ticker"]: row["last_session"] for row in query(client(readonly=True),
        f"SELECT ticker,max(session_date) AS last_session FROM {COVERAGE_TABLE} FINAL WHERE source_plan_hash='{plan_hash}' GROUP BY ticker")}
    def complete(path: Path) -> bool:
        if not (path / "ready.json").is_file():
            return False
        ready = read(path / "ready.json")
        return prior.get(ready["ticker"]) == ready["last_session"]
    pending = [path for path in paths if not complete(path)]
    counts = {"queued": len(pending), "completed": len(paths) - len(pending), "active": 0, "skipped": 0, "failed": 0}
    active: dict[str, str] = {}
    failures: list[dict] = []
    timing_sums = {name: 0.0 for name in ("read_verify_seconds", "compact_seconds", "receipt_seconds", "insert_seconds")}
    started = time.monotonic()
    signal.signal(signal.SIGINT, lambda *_: STOP.set())
    interactive = sys.stdout.isatty()
    live = Live(render(counts, len(paths), active, started, budget), console=Console(), refresh_per_second=2, transient=False) if interactive else None
    if live: live.start()
    try:
        process_context = multiprocessing.get_context("spawn")
        insert_gate = process_context.BoundedSemaphore(args.insert_workers)
        with ProcessPoolExecutor(max_workers=budget["workers"], mp_context=process_context,
                initializer=initialize_worker, initargs=(insert_gate,)) as pool:
            futures = {}
            iterator = iter(pending)
            def admit() -> None:
                while not STOP.is_set() and len(futures) < budget["workers"]:
                    try: path = next(iterator)
                    except StopIteration: return
                    future = pool.submit(migrate_ticker, path, plan_hash, args.batch_rows, args.batch_bytes)
                    futures[future] = path
                    counts["queued"] -= 1; counts["active"] += 1; active[path.name] = path.name
            admit()
            while futures:
                done = next(as_completed(tuple(futures)))
                path = futures.pop(done); active.pop(path.name, None); counts["active"] -= 1
                try:
                    result = done.result(); state = result["state"]; counts[state] = counts.get(state, 0) + 1
                    for name, value in result.get("timings", {}).items():
                        timing_sums[name] += value
                    if not interactive: print(json.dumps(result, sort_keys=True), flush=True)
                except Exception as exc:
                    counts["failed"] += 1; failures.append({"path": str(path), "error": str(exc)})
                    if not interactive: print(json.dumps(failures[-1], sort_keys=True), file=sys.stderr, flush=True)
                admit()
                if live: live.update(render(counts, len(paths), active, started, budget))
    finally:
        if live: live.update(render(counts, len(paths), active, started, budget)); live.stop()
    expected = {}
    for path in paths:
        if (path / "ready.json").is_file():
            ready = read(path / "ready.json")
            expected[ready["ticker"]] = ready["last_session"]
    actual = {row["ticker"]: row["last_session"] for row in query(client(readonly=True),
        f"SELECT ticker,max(session_date) AS last_session FROM {COVERAGE_TABLE} FINAL WHERE source_plan_hash='{plan_hash}' GROUP BY ticker")}
    mismatches = [{"ticker": ticker, "expected": session, "actual": actual.get(ticker)}
        for ticker, session in sorted(expected.items()) if actual.get(ticker) != session]
    unexpected = sorted(set(actual) - set(expected))
    misplaced = query(client(readonly=True), f"SELECT table,disk_name,count() AS parts FROM system.parts WHERE active AND database='{DATABASE}' AND table IN ('structural_levels_v7','structural_level_coverage_v7','structural_level_observations_v7') AND disk_name!='{POLICY}' GROUP BY table,disk_name")
    audit = {"expected_tickers": len(expected), "published_tickers": len(actual), "coverage_mismatches": len(mismatches),
        "mismatch_examples": mismatches[:20], "unexpected_tickers": unexpected[:20], "misplaced_parts": misplaced}
    failed = bool(failures or mismatches or unexpected or misplaced)
    result = {"state": "interrupted" if STOP.is_set() else "failed" if failed else "complete", "source_plan_hash": plan_hash,
        "persistence_version": PERSISTENCE_VERSION, "counts": counts, "failures": failures, "audit": audit,
        "worker_budget": budget, "stage_worker_seconds": timing_sums,
        "elapsed_seconds": time.monotonic() - started}
    (args.runtime / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 130 if STOP.is_set() else 2 if failed else 0


def parse() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate frozen retrospective Level Book V7 checkpoints into arte")
    parser.add_argument("command", choices=("preflight", "run"))
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--workers", type=int, default=None,
        help="processes (default: fastest current CPU/RAM-safe budget)")
    parser.add_argument("--insert-workers", type=int, default=4,
        help="maximum concurrent ClickHouse publishers (default: 4)")
    parser.add_argument("--batch-rows", type=int, default=5000)
    parser.add_argument("--batch-bytes", type=int, default=16 * 1024**2)
    args = parser.parse_args()
    if ((args.workers is not None and not 1 <= args.workers <= 64) or
            not 1 <= args.insert_workers <= 16 or args.batch_rows < 1 or args.batch_bytes < 1024):
        parser.error("workers must be 1..64 and batches must be positive and bounded")
    return args


def main() -> int:
    args = parse()
    if args.command == "preflight":
        load_env_files(discover_clickhouse_env_files()); preflight(client())
        print(f"ready: {DATABASE} tables use {POLICY}; source={args.source}")
        return 0
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
