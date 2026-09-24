"""Build corrected historical V7 directly into separate typed arte V2 tables.

One ticker is the restart unit. Books exist only in worker memory; levels and
observations are inserted first, with dated coverage published last.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from contextlib import nullcontext
from hashlib import sha256
import json
import multiprocessing
import os
from pathlib import Path
import signal
import sys
import time

from rich.console import Console
from rich.live import Live
from rich.table import Table

from research.level_book.v7 import campaign as c
from research.level_book.v7 import workstation
from research.level_book.v7.clickhouse_persistence import (
    DDL, EXPECTED_COLUMNS, POLICY, canonical_json, compact_checkpoints,
    datetime64_ns, epoch_ns,
)
from research.level_book.v7.campaign_source import (
    REPORTING_REVISION, bars_sql, decode, literal, reporting_coverage_sql,
    require_reporting_coverage, source_hash,
)
from research.mlops.clickhouse import (
    ClickHouseHttpClient, default_clickhouse_password, default_clickhouse_url,
    default_clickhouse_user,
)
from scripts.migrate_level_book_v7_to_clickhouse import insert
from src.backend.swing_book_source import session_bounds
from src.market_engine.reaction_band import CONFIG
from src.market_engine.streaming_level_book import EXTRACTION_VERSION
from src.market_engine.derived_trade_policy import POLICY as INPUT_POLICY

VERSION = "arte-structural-v7-direct-v2"
LEVELS = "arte.structural_levels_v7_v2"
OBSERVATIONS = "arte.structural_level_observations_v7_v2"
COVERAGE = "arte.structural_level_coverage_v7_v2"
_ON_WORKSTATION = os.environ.get("COMPUTERNAME", "").upper() == workstation.WORKSTATION_NAME
BASE = c.ROOT if _ON_WORKSTATION else c.WORKSTATION_RUNTIME_ROOT / "level-book-v7"
ROOT = BASE / "all-tradable-20250101-20260912-mle-reporting-v1"
_INSERT_GATE = None
_REPLACEMENTS = {
    "arte.structural_levels_v7": LEVELS,
    "arte.structural_level_observations_v7": OBSERVATIONS,
    "arte.structural_level_coverage_v7": COVERAGE,
}


def ddl() -> tuple[str, ...]:
    statements = []
    for sql in DDL:
        for old, new in _REPLACEMENTS.items():
            sql = sql.replace(old, new)
        if f"CREATE TABLE IF NOT EXISTS {COVERAGE}" in sql:
            sql = sql.replace("input_policy LowCardinality(String),",
                              "input_policy LowCardinality(String),\n        reporting_revision LowCardinality(String),")
        statements.append(sql)
    return tuple(statements)


def _rows(client: ClickHouseHttpClient, sql: str) -> list[dict]:
    return [json.loads(line) for line in client.execute(sql + " FORMAT JSONEachRow").splitlines() if line]


def _client(*, readonly: bool = False) -> ClickHouseHttpClient:
    params = {"max_threads": 2, "max_memory_usage": 2 * 1024**3}
    if readonly:
        params["readonly"] = 1
    return ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(),
                                default_clickhouse_password(), timeout_seconds=300,
                                persistent=True, default_query_params=params)


def storage_preflight(client: ClickHouseHttpClient, *, create: bool) -> None:
    policy = _rows(client, f"SELECT disks FROM system.storage_policies WHERE policy_name='{POLICY}'")
    if policy != [{"disks": [POLICY]}]:
        raise ValueError(f"Required SSD-only {POLICY} policy is unavailable")
    if create:
        for statement in ddl():
            client.execute(statement)
    names = tuple(table.split(".", 1)[1] for table in (LEVELS, OBSERVATIONS, COVERAGE))
    names_sql = ",".join(map(literal, names))
    tables = _rows(client, f"SELECT name,storage_policy,engine,partition_key,sorting_key FROM system.tables "
                    f"WHERE database='arte' AND name IN ({names_sql})")
    if {row["name"] for row in tables} != set(names) or any(row["storage_policy"] != POLICY for row in tables):
        raise ValueError("V2 structural tables or live_market_ssd placement policy are missing")
    expected_columns = {old + "_v2": tuple(columns) for old, columns in EXPECTED_COLUMNS.items()}
    expected_columns["structural_level_coverage_v7_v2"] = tuple(
        item for name in EXPECTED_COLUMNS["structural_level_coverage_v7"]
        for item in ((name, "reporting_revision") if name == "input_policy" else (name,)))
    columns = _rows(client, f"SELECT table,name,type FROM system.columns WHERE database='arte' "
                    f"AND table IN ({names_sql}) ORDER BY table,position")
    actual = {name: tuple(row["name"] for row in columns if row["table"] == name) for name in names}
    if actual != expected_columns:
        raise ValueError("V2 structural table columns differ from the typed contract")
    types = {(row["table"], row["name"]): row["type"] for row in columns}
    required_types = {
        (LEVELS.split(".")[1], "valid_from"): "DateTime64(9, 'UTC')",
        (LEVELS.split(".")[1], "valid_to"): "Nullable(DateTime64(9, 'UTC'))",
        (LEVELS.split(".")[1], "state_hash"): "FixedString(64)",
        (OBSERVATIONS.split(".")[1], "observation_id"): "FixedString(64)",
        (OBSERVATIONS.split(".")[1], "valid_from"): "DateTime64(9, 'UTC')",
        (OBSERVATIONS.split(".")[1], "valid_to"): "Nullable(DateTime64(9, 'UTC'))",
        (COVERAGE.split(".")[1], "source_plan_hash"): "FixedString(64)",
        (COVERAGE.split(".")[1], "reporting_revision"): "LowCardinality(String)",
    }
    if any(types.get(key) != value for key, value in required_types.items()):
        raise ValueError("V2 structural timestamp, hash, or reporting revision types differ")
    expected_layout = {
        LEVELS.split(".")[1]: ("ReplacingMergeTree", "cityHash64(ticker) % 64", "ticker, level_id, valid_from"),
        OBSERVATIONS.split(".")[1]: ("ReplacingMergeTree", "cityHash64(ticker) % 64", "ticker, observation_id, valid_from"),
        COVERAGE.split(".")[1]: ("ReplacingMergeTree", "toYYYYMM(session_date)", "ticker, session_date"),
    }
    if {row["name"]: (row["engine"], row["partition_key"], row["sorting_key"]) for row in tables} != expected_layout:
        raise ValueError("V2 structural table engine/partition/order differs from contract")
    misplaced = _rows(client, f"SELECT table,disk_name FROM system.parts WHERE active AND database='arte' "
                      f"AND table IN ({names_sql}) AND disk_name!='{POLICY}' LIMIT 1")
    if misplaced:
        raise ValueError(f"V2 structural parts outside {POLICY}: {misplaced[0]}")


def _source_plan_hash(root: Path) -> str:
    return sha256((root / "plan.json").read_bytes()).hexdigest()


def _completed(client: ClickHouseHttpClient, plan_hash: str) -> dict[str, tuple[int, str]]:
    other = _rows(client, f"SELECT source_plan_hash FROM {COVERAGE} FINAL "
                  f"WHERE source_plan_hash!={literal(plan_hash)} LIMIT 1")
    if other:
        raise ValueError("V2 structural tables contain a different source plan")
    return {row["ticker"]: (int(row["days"]), str(row["last_session"]))
            for row in _rows(client, f"SELECT ticker,count() days,max(session_date) last_session "
                             f"FROM {COVERAGE} FINAL GROUP BY ticker")}


def build_ticker(root: Path, ticker: str, *, threads: int = 1,
                 insert_gate=None) -> dict:
    """Compute a full corrected ticker history without a filesystem book copy."""
    plan = c.checked_plan(root)
    if plan.get("output_contract") != VERSION or plan.get("reporting_revision") != REPORTING_REVISION:
        raise ValueError("Not a corrected direct-publication V7 plan")
    row = next((item for item in plan["rows"] if item["ticker"] == ticker), None)
    if row is None or row["status"] != "queued":
        raise ValueError(f"Ticker is absent or deferred: {ticker}")
    expected = _source_plan_hash(root)
    def query(sql: str) -> list[dict]:
        return c.query(sql, threads)
    if query(c.coverage_sql(plan["start"], plan["end"], [ticker])) != [row["coverage"]]:
        raise ValueError(f"{ticker}: canonical coverage differs from plan")
    if query(c.RULE_SQL) != plan["rules"]:
        raise ValueError("Trade-condition rules changed")
    predicate = (f"ticker={literal(ticker)} AND source_date BETWEEN "
                 f"{literal(plan['start'])} AND {literal(plan['end'])}")
    days = query("SELECT * FROM market_sip_compact.events_ordinal_continuity FINAL WHERE "
                 + predicate + " ORDER BY source_date")
    if len(days) != int(row["coverage"]["days"]):
        raise ValueError(f"{ticker}: source day count changed")
    reporting_rows = query(reporting_coverage_sql(plan["start"], plan["end"]))
    require_reporting_coverage([d["source_date"] for d in days], reporting_rows)
    if c.digest(reporting_rows) != plan["reporting_coverage_hash"]:
        raise ValueError(f"{ticker}: trade-reporting certification differs from frozen plan")
    splits = c.canonical_splits(query(
        f"SELECT execution_date,split_from,split_to,inserted_at FROM q_live.market_stock_split_v1 FINAL "
        f"WHERE provider_ticker={literal(ticker)} AND execution_date BETWEEN "
        f"{literal(plan['start'])} AND {literal(plan['end'])} ORDER BY execution_date"))
    prior = None
    empty: set[str] = set()
    calculated = 0

    def books():
        nonlocal prior, calculated
        for metadata in days:
            day = metadata["source_date"]
            if (root / "STOP").exists():
                raise InterruptedError(f"{ticker}: stopped before {day}; ticker remains unpublished")
            bars, _ = decode(query(bars_sql(ticker, day, plan["rules"], metadata)))
            if query("SELECT * FROM market_sip_compact.events_ordinal_continuity FINAL WHERE "
                     f"ticker={literal(ticker)} AND source_date={literal(day)}") != [metadata]:
                raise ValueError(f"{ticker} {day}: canonical source changed during fitting")
            if not bars:
                empty.add(day)
                continue
            actions = [s for s in splits if prior and prior["session"] < s["execution_date"] <= day]
            engine = c.fit_day(prior, ticker, day, bars, actions)
            for bar in bars:
                engine.update(bar, observed_at=bar["t"])
            prior = engine.historical_checkpoint(c.digest(bars))
            calculated += 1
            yield prior

    try:
        levels, observations, coverage = compact_checkpoints(books(), expected)
    except ValueError as exc:
        if str(exc) != "No V7 checkpoints supplied":
            raise
        levels, observations, coverage = [], [], []
    coverage_by_day = {item["session_date"]: item for item in coverage}
    previous = None
    completed_coverage = []
    for metadata in days:
        day = metadata["source_date"]
        item = coverage_by_day.get(day)
        if item is None:
            if day not in empty:
                raise ValueError(f"{ticker} {day}: no V7 book or empty-day evidence")
            _, end = session_bounds(day)
            stamp = epoch_ns(str(end.timestamp()))
            item = {
                "ticker": ticker, "session_date": day, "available_at": datetime64_ns(stamp),
                "state": "empty", "level_count": int(previous["level_count"]) if previous else 0,
                "interval_count": int(previous["interval_count"]) if previous else 0,
                "observation_count": int(previous["observation_count"]) if previous else 0,
                "observation_interval_count": int(previous["observation_interval_count"]) if previous else 0,
                "input_policy": INPUT_POLICY, "source_extraction_version": EXTRACTION_VERSION,
                "band_config_hash": sha256(canonical_json({**CONFIG, "coverage": .8}).encode()).hexdigest(),
                "source_input_hash": source_hash(metadata, plan["rules"]),
                "source_checkpoint_hash": previous["source_checkpoint_hash"] if previous else "",
                "parent_checkpoint_hash": previous["source_checkpoint_hash"] if previous else "",
                "source_plan_hash": expected, "publication_revision": stamp,
            }
        item["reporting_revision"] = REPORTING_REVISION
        completed_coverage.append(item)
        previous = item
    if len(completed_coverage) != len(days):
        raise ValueError(f"{ticker}: incomplete V2 coverage")
    if query(c.coverage_sql(plan["start"], plan["end"], [ticker])) != [row["coverage"]]:
        raise ValueError(f"{ticker}: canonical source changed before publication")
    if query(c.RULE_SQL) != plan["rules"]:
        raise ValueError("Trade-condition rules changed before publication")
    reporting_rows = query(reporting_coverage_sql(plan["start"], plan["end"]))
    require_reporting_coverage([d["source_date"] for d in days], reporting_rows)
    if c.digest(reporting_rows) != plan["reporting_coverage_hash"]:
        raise ValueError(f"{ticker}: trade-reporting certification changed before publication")
    with insert_gate if insert_gate is not None else nullcontext():
        writer = _client()
        try:
            token = f"{VERSION}-{expected[:16]}-{ticker.encode().hex()}"
            insert(writer, LEVELS, levels, token + "-levels", batch_rows=5000, batch_bytes=16 * 1024**2)
            insert(writer, OBSERVATIONS, observations, token + "-observations", batch_rows=5000, batch_bytes=16 * 1024**2)
            published_at = datetime64_ns(time.time_ns())
            for item in completed_coverage:
                item["published_at"] = published_at
            insert(writer, COVERAGE, completed_coverage, token + "-coverage", batch_rows=5000,
                   batch_bytes=16 * 1024**2)
        finally:
            writer.close()
    return {"ticker": ticker, "state": "completed", "sessions": len(days),
            "fitted": calculated, "empty": len(empty), "levels": len(levels),
            "observations": len(observations)}


def _initialize_worker(gate):
    global _INSERT_GATE
    _INSERT_GATE = gate
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    c.load_env_files(c.discover_clickhouse_env_files(), verbose=False)


def _worker(root: str, ticker: str, threads: int):
    return build_ticker(Path(root), ticker, threads=threads, insert_gate=_INSERT_GATE)


def render(counts: dict[str, int], total: int, active: set[str], started: float) -> Table:
    table = Table(title="V7 corrected direct publication → arte V2", expand=True)
    table.add_column("State"); table.add_column("Tickers", justify="right")
    for state in ("active", "queued", "completed", "deferred", "failed"):
        table.add_row(state, str(counts.get(state, 0)))
    table.caption = (f"Durable {counts.get('completed', 0)}/{total} tickers · "
                     f"elapsed {c.duration(time.monotonic() - started)} · "
                     "Ctrl+C stops admission; active workers finish or discard their ticker")
    for ticker in sorted(active)[:12]:
        table.add_row("worker", ticker)
    return table


def run(root: Path, workers: int | None, threads: int, insert_workers: int) -> int:
    plan = c.checked_plan(root)
    if plan.get("output_contract") != VERSION:
        raise ValueError("The frozen V7 plan does not authorize direct V2 publication")
    budget = workstation.resource_budget(os.cpu_count() or 1,
                                         workstation.psutil.virtual_memory().available,
                                         workers, threads)
    if not 1 <= insert_workers <= budget["workers"]:
        raise ValueError("insert-workers must be within the admitted worker budget")
    with c.exclusive(root / "controller.lock"):
        writer = _client()
        try:
            storage_preflight(writer, create=True)
        finally:
            writer.close()
        reader = _client(readonly=True)
        try:
            published = _completed(reader, _source_plan_hash(root))
        finally:
            reader.close()
        rows = [row for row in plan["rows"] if row["status"] == "queued"]
        pending = [row for row in rows if published.get(row["ticker"]) !=
                   (int(row["coverage"]["days"]), str(row["coverage"]["last"]))]
        counts = Counter(queued=len(pending), completed=len(rows) - len(pending),
                         deferred=sum(row["status"] == "deferred" for row in plan["rows"]))
        active: dict = {}
        failures = []
        started = time.monotonic()
        console = Console()
        old_handler = signal.signal(signal.SIGINT, lambda *_: (root / "STOP").touch())
        (root / "STOP").unlink(missing_ok=True)
        context = multiprocessing.get_context("spawn")
        gate = context.BoundedSemaphore(insert_workers)
        iterator = iter(pending)
        interactive = console.is_terminal
        live = Live(render(counts, len(rows), set(), started), console=console,
                    refresh_per_second=2, transient=False) if interactive else None
        if live:
            live.start()
        try:
            with ProcessPoolExecutor(max_workers=budget["workers"], mp_context=context,
                                     initializer=_initialize_worker, initargs=(gate,)) as pool:
                def admit():
                    while not (root / "STOP").exists() and len(active) < budget["workers"]:
                        row = next(iterator, None)
                        if row is None:
                            return
                        future = pool.submit(_worker, str(root), row["ticker"], threads)
                        active[future] = row["ticker"]
                        counts["queued"] -= 1; counts["active"] += 1
                admit()
                while active:
                    done, _ = wait(tuple(active), timeout=2, return_when=FIRST_COMPLETED)
                    for future in done:
                        ticker = active.pop(future)
                        counts["active"] -= 1
                        try:
                            result = future.result()
                            counts["completed"] += 1
                            if not interactive:
                                print(json.dumps(result, sort_keys=True), flush=True)
                        except InterruptedError:
                            counts["queued"] += 1
                        except Exception as exc:
                            counts["failed"] += 1
                            failures.append({"ticker": ticker, "error": str(exc)})
                            print(json.dumps(failures[-1], sort_keys=True), file=sys.stderr, flush=True)
                    admit()
                    if live:
                        live.update(render(counts, len(rows), set(active.values()), started))
        finally:
            if live:
                live.update(render(counts, len(rows), set(active.values()), started)); live.stop()
            signal.signal(signal.SIGINT, old_handler)
        final = _client(readonly=True)
        try:
            storage_preflight(final, create=False)
            persisted = _completed(final, _source_plan_hash(root))
        finally:
            final.close()
        mismatches = [row["ticker"] for row in rows if persisted.get(row["ticker"]) !=
                      (int(row["coverage"]["days"]), str(row["coverage"]["last"]))]
        state = "interrupted" if (root / "STOP").exists() else "failed" if (failures or mismatches) else (
            "complete_with_gaps" if counts["deferred"] else "complete")
        c.write(root / "direct-v2-result.json", {
            "state": state, "version": VERSION, "source_plan_hash": _source_plan_hash(root),
            "counts": dict(counts), "expected_tickers": len(rows),
            "published_tickers": len(persisted), "mismatches": mismatches[:20],
            "mismatch_count": len(mismatches), "failures": failures[:20],
            "failure_count": len(failures), "elapsed_seconds": time.monotonic() - started,
        }, immutable=False)
        console.print(f"V7 direct V2 {state}: {counts['completed']}/{len(rows)} published, "
                      f"{counts['queued']} queued, {counts['failed']} failed, "
                      f"{len(mismatches)} coverage mismatches")
        return 130 if state == "interrupted" else 2 if state == "failed" else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "preflight", "run", "stop"))
    parser.add_argument("--runtime", type=Path, default=ROOT)
    parser.add_argument("--start", default="2025-01-01")
    parser.add_argument("--end", default="2026-09-12")
    parser.add_argument("--tickers", nargs="+")
    parser.add_argument("--workers", type=int)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--insert-workers", type=int, default=4)
    args = parser.parse_args(argv)
    if not BASE.is_dir():
        raise ValueError("Required workstation runtime root is unavailable")
    root = args.runtime.resolve()
    if not root.is_relative_to(BASE.resolve()):
        raise ValueError("V7 direct runtime must be under the workstation runtime root")
    if args.command == "stop":
        (root / "STOP").touch()
        print("Stop requested; active tickers reach the next session boundary.")
        return 0
    if args.command in {"plan", "run"} and not _ON_WORKSTATION:
        raise ValueError(f"V7 direct planning and publication must run on {workstation.WORKSTATION_NAME}")
    c.load_env_files(c.discover_clickhouse_env_files(), verbose=False)
    if args.command == "plan":
        args.runtime = root; args.output_contract = VERSION
        c.plan(args)
        return 0
    if args.command == "preflight":
        plan = c.checked_plan(root)
        if plan.get("output_contract") != VERSION:
            raise ValueError("Frozen plan is not a direct V2 campaign")
        client = _client()
        try:
            storage_preflight(client, create=False)
        finally:
            client.close()
        print(f"V7 V2 ready: {len(plan['rows'])} planned tickers; tables on {POLICY}")
        return 0
    return run(root, args.workers, args.threads, args.insert_workers)
