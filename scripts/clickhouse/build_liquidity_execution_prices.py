"""Build certified eligible-trade price rows for passive 100 ms Backtest fills.

Plan-only by default. --apply is workstation-only and never changes the
existing bars, indicators, or liquidity buckets. Progress counts only units
whose coverage-last row has been verified. Rerun after interruption to skip
verified units and use fresh attempt IDs for uncommitted ones.
"""
from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import closing, nullcontext
from datetime import date
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import sys
from threading import Semaphore, local
from time import monotonic
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from pipelines.market_sip.events import liquidity_execution_price_sql as price_sql
from pipelines.market_sip.events.liquidity_execution_price_producer import publish_unit
from scripts.build_market_day import digest
from scripts.clickhouse.install_market_day_certificate_layout import workstation_clickhouse_url
from scripts.clickhouse.plan_market_day_certificate import DEFAULT_RUNTIME, prepare_saved_build
from scripts.clickhouse.provision_trading_journal import _admin_client
from src.trading_runtime.arte_market_day_cold_preflight import (
    audit_attested_market_day_certificate,
)
from src.trading_runtime.arte_market_day_keeper import MarketDayKeeperReader
from src.trading_runtime.keeper_session import open_workstation_keeper_session


def _rows(client, query: str) -> list[dict]:
    return [json.loads(line) for line in client.execute(
        query + " FORMAT JSONEachRow").splitlines() if line.strip()]


def _producer_client(url: str):
    """One bounded ClickHouse query lane per ticker worker."""
    client = _admin_client(url)
    client.timeout_seconds = 630
    client.default_query_params = {
        "max_threads": "4", "max_insert_threads": "1",
        "max_memory_usage": str(2 * 1024**3),
        "max_execution_time": "600", "max_result_rows": "100000",
        "result_overflow_mode": "throw",
    }
    return client


def _layout(client, *, apply: bool) -> tuple[int, int]:
    """Check exact SSD policy and physical placement before any INSERT."""
    policies = _rows(client, "SELECT disks FROM system.storage_policies "
                     "WHERE policy_name='live_market_ssd'")
    if policies != [{"disks": ["live_market_ssd"]}]:
        raise RuntimeError("Eligible-price product requires SSD-only live_market_ssd")
    if client.execute("SELECT count() FROM system.databases WHERE name='arte'").strip() != "1":
        raise RuntimeError("Existing arte database is required")
    names = ("liquidity_execution_price_100ms_v1",
             "liquidity_execution_price_coverage_v1")
    catalog = _rows(client, "SELECT name,engine,storage_policy,partition_key,sorting_key "
                    "FROM system.tables WHERE database='arte' AND name IN "
                    f"('{names[0]}','{names[1]}')")
    if len(catalog) != len({row["name"] for row in catalog}):
        raise RuntimeError("Eligible-price table catalog is ambiguous")
    expected_order = {
        names[0]: "source_build_id,session_date,ticker,source_attempt_id,"
                  "derivation_attempt_id,bucket_index,price_int",
        names[1]: "source_build_id,session_date,ticker,source_attempt_id,"
                  "derivation_attempt_id",
    }
    for row in catalog:
        if (row["name"] not in expected_order or row["engine"] != "MergeTree"
                or row["storage_policy"] != "live_market_ssd"
                or row["partition_key"].replace(" ", "") != "toYYYYMM(session_date)"
                or row["sorting_key"].replace(" ", "") != expected_order[row["name"]]):
            raise RuntimeError(f"Incompatible eligible-price layout: {row['name']}")
    expected_columns = {
        names[0]: (("source_build_id", "String"), ("session_date", "Date"),
                   ("ticker", "LowCardinality(String)"),
                   ("source_attempt_id", "UUID"), ("derivation_attempt_id", "UUID"),
                   ("bucket_index", "UInt32"), ("price_int", "UInt64"),
                   ("execution_volume", "Float64")),
        names[1]: (("source_build_id", "String"), ("session_date", "Date"),
                   ("ticker", "LowCardinality(String)"),
                   ("source_attempt_id", "UUID"), ("derivation_attempt_id", "UUID"),
                   ("price_row_count", "UInt64"),
                   ("eligible_bucket_count", "UInt32"),
                   ("total_execution_volume", "Float64"),
                   ("content_hash", "FixedString(64)"),
                   ("certified_at", "DateTime64(6, 'UTC')")),
    }
    columns = _rows(client, "SELECT table,name,type FROM system.columns "
                    "WHERE database='arte' AND table IN "
                    f"('{names[0]}','{names[1]}') ORDER BY table,position")
    for name in {row["name"] for row in catalog}:
        actual = tuple((row["name"], row["type"]) for row in columns
                       if row["table"] == name)
        if actual != expected_columns[name]:
            raise RuntimeError(f"Incompatible eligible-price columns: {name}")
    misplaced = _rows(client, "SELECT table,disk_name FROM system.parts "
        "WHERE active AND database='arte' AND table IN "
        f"('{names[0]}','{names[1]}') AND disk_name!='live_market_ssd' LIMIT 1")
    if misplaced:
        raise RuntimeError("Eligible-price product has misplaced active parts")
    present = {row["name"] for row in catalog}
    if apply:
        for name, ddl in zip(names, price_sql.ddl()):
            if name not in present:
                client.execute(ddl)
        return _layout(client, apply=False)
    return len(present), len(names) - len(present)


def _plan(runtime: Path, build_id: str, day: date):
    prepared, requested = prepare_saved_build(runtime, build_id)
    if day.isoformat() not in requested:
        raise ValueError("Session is outside the certified market-day request")
    manifest = json.loads((runtime.resolve() / "market-day" /
                           f"{build_id}.json").read_text(encoding="utf-8"))
    definition = manifest["definition"]
    rules = definition["plan"]["rules"]
    if (not isinstance(rules, list)
            or digest(rules) != definition["rules_hash"]):
        raise ValueError("Archived eligibility rules differ from the market-day build")
    event_counts = {row["ticker"]: int(row["source_event_count"])
                    for row in prepared["market_day_planned_scope_v1"]
                    if row["session_date"] == day.isoformat()}
    scopes = sorted((row["ticker"], row["attempt_id"],
                     event_counts[row["ticker"]])
                    for row in prepared["market_day_stage_certificate_v1"]
                    if row["session_date"] == day.isoformat()
                    and row["stage"] == "broker_100ms")
    if not scopes or len(scopes) != len({ticker for ticker, _, _ in scopes}):
        raise ValueError("Session has missing or duplicate certified liquidity units")
    return scopes, rules


def run(*, runtime: Path, build_id: str, day: date, workers: int,
        apply: bool) -> dict[str, int]:
    scopes, rules = _plan(runtime, build_id, day)
    counts = {"total": len(scopes), "completed": 0, "skipped": 0,
              "failed": 0, "created_tables": 0}
    print(f"Eligible prices | {day} | {len(scopes)} certified ticker-days | "
          f"{workers} workers", flush=True)
    if not apply:
        print("Plan only: no ClickHouse connection or write", flush=True)
        return counts
    if platform.node().upper() != "DESKTOP-SAAI85T":
        raise RuntimeError("Eligible-price publication is workstation-only")
    url = workstation_clickhouse_url()
    with closing(_admin_client(url)) as control, closing(
            open_workstation_keeper_session()) as keeper:
        control.timeout_seconds = 180
        audit_attested_market_day_certificate(
            control, MarketDayKeeperReader(keeper.client), build_id,
            sessions=(day.isoformat(),))
        _existing, missing = _layout(control, apply=False)
        if missing:
            _layout(control, apply=True)
            counts["created_tables"] = missing
        lock = ("/trading/ownership/v1/liquidity_execution_price/" +
                sha256(f"{build_id}:{day.isoformat()}".encode()).hexdigest())
        try:
            keeper.client.create(lock, uuid4().hex.encode(), ephemeral=True,
                                 makepath=True)
        except Exception as exc:
            if type(exc).__name__ == "NodeExistsError":
                raise RuntimeError("Another eligible-price producer owns this session") from exc
            raise
        worker_local = local()
        clients = []
        dense_slot = Semaphore(1)

        def task(item):
            if not keeper.client.connected:
                raise RuntimeError("Eligible-price Keeper claim was lost")
            client = getattr(worker_local, "client", None)
            if client is None:
                client = _producer_client(url)
                clients.append(client)
                worker_local.client = client
            ticker, source_attempt, event_count = item
            dense = event_count >= 4_000_000
            with dense_slot if dense else nullcontext():
                if dense:
                    client.default_query_params["max_memory_usage"] = str(8 * 1024**3)
                try:
                    status = publish_unit(client, build_id=build_id, day=day,
                                          ticker=ticker, source_attempt_id=source_attempt,
                                          rules=rules)
                finally:
                    if dense:
                        client.default_query_params["max_memory_usage"] = str(2 * 1024**3)
            if not keeper.client.connected:
                raise RuntimeError("Eligible-price Keeper claim was lost")
            return ticker, status

        started = monotonic()
        next_report = started + 15
        iterator = iter(scopes)
        failure = None
        try:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                pending = {}
                for item in iterator:
                    pending[pool.submit(task, item)] = item
                    if len(pending) == workers:
                        break
                while pending:
                    done, _ = wait(pending, return_when=FIRST_COMPLETED)
                    for future in done:
                        item = pending.pop(future)
                        try:
                            ticker, status = future.result()
                            counts["completed" if status == "published" else "skipped"] += 1
                        except Exception as exc:
                            counts["failed"] += 1
                            print(f"Failed {day} {item[0]}: {exc}", file=sys.stderr,
                                  flush=True)
                            failure = exc
                        if failure is None:
                            successor = next(iterator, None)
                            if successor is not None:
                                pending[pool.submit(task, successor)] = successor
                    if monotonic() >= next_report or not pending:
                        done_count = counts["completed"] + counts["skipped"]
                        print(f"Coverage {done_count}/{counts['total']} | "
                              f"published {counts['completed']} | skipped {counts['skipped']} | "
                              f"active {len(pending)} | failed {counts['failed']} | "
                              f"elapsed {monotonic()-started:.0f}s", flush=True)
                        next_report = monotonic() + 15
        finally:
            for client in clients:
                client.close()
        if failure is not None:
            raise RuntimeError("Eligible-price session incomplete; rerun to resume") from failure
        if counts["completed"] + counts["skipped"] != counts["total"]:
            raise RuntimeError("Eligible-price session ended without complete coverage")
        _layout(control, apply=False)
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--date", type=date.fromisoformat, required=True)
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-eligible-price-publication", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 16:
        parser.error("--workers must be between 1 and 16")
    if args.apply and not args.confirm_eligible_price_publication:
        parser.error("--apply requires --confirm-eligible-price-publication")
    try:
        run(runtime=args.runtime, build_id=args.build_id,
            day=args.date, workers=args.workers, apply=args.apply)
    except KeyboardInterrupt:
        print("Interrupted; admitted workers drained, rerun to resume.",
              file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Eligible-price publication blocked: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
