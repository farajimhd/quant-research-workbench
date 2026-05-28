from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import shutil
import sys
import time
import traceback
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.inhouse_transformer.v22.config import DataConfig  # noqa: E402


LOG_RULE = "*" * 96


def parse_args() -> argparse.Namespace:
    defaults = DataConfig()
    parser = argparse.ArgumentParser(description="Prebuild v22 sparse quote/trade event chunks.")
    parser.add_argument("--flatfiles-root", default=str(defaults.flatfiles_root))
    parser.add_argument("--canonical-root", default=str(defaults.canonical_root))
    parser.add_argument("--cache-root", default=str(defaults.cache_root))
    parser.add_argument("--start-date", default=defaults.train_start_date)
    parser.add_argument("--end-date", default=defaults.test_end_date)
    parser.add_argument("--tickers", default="ALL")
    parser.add_argument("--chunk-ms", type=int, default=defaults.chunk_ms)
    parser.add_argument("--max-quote-events", type=int, default=defaults.max_quote_events)
    parser.add_argument("--max-trade-events", type=int, default=defaults.max_trade_events)
    parser.add_argument("--max-total-events", type=int, default=defaults.max_total_events)
    parser.add_argument("--processes", type=int, default=max(1, min(8, (os.cpu_count() or 4) // 2)))
    parser.add_argument("--polars-threads-per-process", type=int, default=2)
    parser.add_argument("--session-start-hour-utc", type=int, default=defaults.session_start_hour_utc)
    parser.add_argument("--session-end-hour-utc", type=int, default=defaults.session_end_hour_utc)
    parser.add_argument("--rebuild-cache", action="store_true")
    parser.add_argument("--keep-temp-normalized", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--heartbeat-seconds", type=float, default=30.0)
    parser.add_argument("--manifest-name", default="preprocess_event_chunks_manifest.jsonl")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    os.environ.setdefault("POLARS_MAX_THREADS", str(max(1, args.polars_threads_per_process)))
    import polars as pl

    from research.inhouse_transformer.v22.data import (
        available_sessions,
        discover_canonical_groups,
        discover_temp_canonical_groups,
        event_chunk_path,
        merge_temp_group_to_canonical,
        normalize_session_to_temp_parts,
        parse_ticker_list,
        temp_canonical_parts_root,
        year_month_range,
    )

    config = DataConfig(
        flatfiles_root=Path(args.flatfiles_root),
        canonical_root=Path(args.canonical_root),
        cache_root=Path(args.cache_root),
        train_start_date=args.start_date,
        train_end_date=args.end_date,
        validation_start_date=args.start_date,
        validation_end_date=args.end_date,
        test_start_date=args.start_date,
        test_end_date=args.end_date,
        tickers=parse_ticker_list(args.tickers),
        chunk_ms=args.chunk_ms,
        max_quote_events=args.max_quote_events,
        max_trade_events=args.max_trade_events,
        max_total_events=args.max_total_events,
        session_start_hour_utc=args.session_start_hour_utc,
        session_end_hour_utc=args.session_end_hour_utc,
        rebuild_cache=args.rebuild_cache,
    )
    sessions = available_sessions(config.flatfiles_root, args.start_date, args.end_date)
    months = year_month_range(args.start_date, args.end_date)
    print(LOG_RULE)
    print("v22 canonical events + event chunk preprocessing")
    print(f"flatfiles_root={config.flatfiles_root}")
    print(f"canonical_root={config.canonical_root}")
    print(f"cache_root={config.cache_root}")
    print(f"sessions={sessions[0]} -> {sessions[-1]} count={len(sessions)}")
    print(f"months={','.join(months)}")
    print(f"chunk_ms={config.chunk_ms} max_quote={config.max_quote_events} max_trade={config.max_trade_events} max_total={config.max_total_events}")
    print(f"processes={args.processes} polars_threads_per_process={args.polars_threads_per_process}")
    print(
        "polars_runtime="
        f"version={pl.__version__} thread_pool={pl.thread_pool_size()} "
        f"has_partition_by={hasattr(pl, 'PartitionBy')} has_sink_parquet={hasattr(pl.LazyFrame, 'sink_parquet')}",
        flush=True,
    )
    print(LOG_RULE)
    if args.dry_run:
        for ticker in ("<ticker>",):
            for year_month in months:
                print(f"canonical quotes: {config.canonical_root / 'quotes' / f'ticker={ticker}' / f'{year_month}.parquet'}")
                print(f"canonical trades: {config.canonical_root / 'trades' / f'ticker={ticker}' / f'{year_month}.parquet'}")
                print(f"event chunks: {event_chunk_path(config, ticker, year_month)}")
        return

    manifest_path = config.cache_root / args.manifest_name
    started = time.time()
    failed = 0
    worker_payload = {
        "config": config_to_payload(config),
        "tickers_raw": args.tickers,
        "polars_threads_per_process": args.polars_threads_per_process,
        "rebuild": args.rebuild_cache,
    }
    tickers = parse_ticker_list(args.tickers)
    existing_groups = discover_canonical_groups(
        config,
        start_date=args.start_date,
        end_date=args.end_date,
        tickers=tickers,
    )
    if existing_groups and not args.rebuild_cache:
        print(f"Layer 1 canonical events already present for {len(existing_groups):,} ticker-month groups; skipping raw CSV normalization.", flush=True)
    else:
        print(LOG_RULE)
        print("PHASE 1/3 normalize raw CSV.GZ quote/trade files into temporary ticker partitions", flush=True)
        normalize_items = [
            {"session": session, "kind": kind}
            for session in sessions
            for kind in ("quotes", "trades")
        ]
        failed += run_parallel(
            label="normalize",
            items=normalize_items,
            submit=lambda executor, item: executor.submit(normalize_session_kind_worker, item, worker_payload),
            manifest_path=manifest_path,
            processes=args.processes,
            started=started,
            fail_fast=args.fail_fast,
            heartbeat_seconds=args.heartbeat_seconds,
        )
        temp_groups = discover_temp_canonical_groups(config)
        print(LOG_RULE)
        print(f"PHASE 2/3 merge temporary partitions into canonical ticker-month events groups={len(temp_groups):,}", flush=True)
        merge_items = [
            {"kind": kind, "year_month": year_month, "ticker": ticker, "paths": paths}
            for (kind, year_month, ticker), paths in sorted(temp_groups.items())
        ]
        failed += run_parallel(
            label="canonical",
            items=merge_items,
            submit=lambda executor, item: executor.submit(merge_canonical_worker, item, worker_payload),
            manifest_path=manifest_path,
            processes=args.processes,
            started=started,
            fail_fast=args.fail_fast,
            heartbeat_seconds=args.heartbeat_seconds,
        )
        if not args.keep_temp_normalized and failed == 0:
            temp_root = temp_canonical_parts_root(config)
            if temp_root.exists():
                shutil.rmtree(temp_root)
                print(f"Deleted temporary normalized parts: {temp_root}", flush=True)
    canonical_groups = discover_canonical_groups(
        config,
        start_date=args.start_date,
        end_date=args.end_date,
        tickers=tickers,
    )
    print(LOG_RULE)
    print(f"PHASE 3/3 build model-specific event chunk cache from canonical events groups={len(canonical_groups):,}", flush=True)
    failed += run_parallel(
        label="chunks",
        items=[{"ticker": ticker, "year_month": year_month} for ticker, year_month in canonical_groups],
        submit=lambda executor, item: executor.submit(build_chunks_worker, item, worker_payload),
        manifest_path=manifest_path,
        processes=args.processes,
        started=started,
        fail_fast=args.fail_fast,
        heartbeat_seconds=args.heartbeat_seconds,
    )
    print(LOG_RULE)
    print(f"Done. sessions={len(sessions)} canonical_groups={len(canonical_groups):,} failed={failed}")
    print(f"Manifest: {manifest_path}")
    print(LOG_RULE)
    if failed:
        raise SystemExit(1)


def normalize_session_worker(session: str, payload: dict[str, Any]) -> dict[str, Any]:
    os.environ["POLARS_MAX_THREADS"] = str(max(1, int(payload["polars_threads_per_process"])))
    started = time.time()
    from research.inhouse_transformer.v22.data import normalize_session_to_temp_parts, parse_ticker_list

    try:
        config = payload_to_config(payload["config"])
        result = normalize_session_to_temp_parts(
            config,
            session,
            parse_ticker_list(payload["tickers_raw"]),
            rebuild=bool(payload.get("rebuild")),
        )
        rows = sum(int(item.get("rows") or 0) for item in result["kinds"].values())
        return result_row("normalize", session, result_status(result["kinds"].values()), rows, time.time() - started, result)
    except BaseException:
        return failed_row("normalize", session, time.time() - started)


def normalize_session_kind_worker(item: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    os.environ["POLARS_MAX_THREADS"] = str(max(1, int(payload["polars_threads_per_process"])))
    started = time.time()
    key = f"{item['kind']}:{item['session']}"
    print(f"START normalize {key}", flush=True)
    from research.inhouse_transformer.v22.data import normalize_session_kind_to_temp_parts, parse_ticker_list

    try:
        config = payload_to_config(payload["config"])
        result = normalize_session_kind_to_temp_parts(
            config,
            item["session"],
            item["kind"],
            parse_ticker_list(payload["tickers_raw"]),
            rebuild=bool(payload.get("rebuild")),
        )
        print(f"WRITER normalize {key} {result.get('writer', 'unknown')}", flush=True)
        return result_row("normalize", key, result["status"], int(result.get("rows") or 0), time.time() - started, result)
    except BaseException:
        return failed_row("normalize", key, time.time() - started)


def merge_canonical_worker(item: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    os.environ["POLARS_MAX_THREADS"] = str(max(1, int(payload["polars_threads_per_process"])))
    started = time.time()
    from research.inhouse_transformer.v22.data import merge_temp_group_to_canonical

    try:
        config = payload_to_config(payload["config"])
        print(f"START canonical {item['kind']}:{item['ticker']}:{item['year_month']} paths={len(item['paths'])}", flush=True)
        result = merge_temp_group_to_canonical(
            config,
            kind=item["kind"],
            year_month=item["year_month"],
            ticker=item["ticker"],
            paths=[Path(path) for path in item["paths"]],
            rebuild=bool(payload.get("rebuild")),
        )
        key = f"{item['kind']}:{item['ticker']}:{item['year_month']}"
        return result_row("canonical", key, result["status"], int(result.get("rows") or 0), time.time() - started, result)
    except BaseException:
        return failed_row("canonical", f"{item.get('kind')}:{item.get('ticker')}:{item.get('year_month')}", time.time() - started)


def build_chunks_worker(item: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    os.environ["POLARS_MAX_THREADS"] = str(max(1, int(payload["polars_threads_per_process"])))
    started = time.time()
    from research.inhouse_transformer.v22.data import build_event_chunks_from_canonical

    try:
        config = payload_to_config(payload["config"])
        print(f"START chunks {item['ticker']}:{item['year_month']}", flush=True)
        result = build_event_chunks_from_canonical(
            config,
            ticker=item["ticker"],
            year_month=item["year_month"],
            rebuild=bool(payload.get("rebuild")),
        )
        key = f"{item['ticker']}:{item['year_month']}"
        return result_row("chunks", key, result["status"], int(result.get("rows") or 0), time.time() - started, result)
    except BaseException:
        return failed_row("chunks", f"{item.get('ticker')}:{item.get('year_month')}", time.time() - started)


def run_parallel(
    *,
    label: str,
    items: list[Any],
    submit: Any,
    manifest_path: Path,
    processes: int,
    started: float,
    fail_fast: bool,
    heartbeat_seconds: float,
) -> int:
    if not items:
        print(f"{label}: no work items", flush=True)
        return 0
    failed = 0
    completed = 0
    submitted_at: dict[Any, float] = {}
    future_labels: dict[Any, str] = {}
    with concurrent.futures.ProcessPoolExecutor(max_workers=max(1, processes)) as executor:
        pending = set()
        for index, item in enumerate(items, start=1):
            future = submit(executor, item)
            pending.add(future)
            submitted_at[future] = time.time()
            future_labels[future] = item_label(item)
            print(f"[{index:,}/{len(items):,}] SUBMIT {label} {future_labels[future]}", flush=True)
        next_heartbeat = time.time() + max(1.0, heartbeat_seconds)
        while pending:
            done, pending = concurrent.futures.wait(
                pending,
                timeout=max(1.0, min(max(1.0, heartbeat_seconds), max(0.1, next_heartbeat - time.time()))),
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            now = time.time()
            if not done and now >= next_heartbeat:
                print_heartbeat(label, pending, future_labels, submitted_at, completed, len(items), started)
                next_heartbeat = now + max(1.0, heartbeat_seconds)
                continue
            for future in done:
                completed += 1
                item = future_labels[future]
                item_started = submitted_at[future]
                wait_seconds = time.time() - item_started
                if wait_seconds >= max(1.0, heartbeat_seconds):
                    print(f"FINISH {label} {item} after {wait_seconds:.1f}s", flush=True)
                else:
                    print(f"FINISH {label} {item}", flush=True)
                try:
                    result = future.result()
                except BaseException:
                    result = failed_row(label, str(item), time.time() - started)
                if result["status"] == "failed":
                    failed += 1
                append_jsonl(manifest_path, result)
                print(format_progress(result, completed, len(items), time.time() - started), flush=True)
                if fail_fast and result["status"] == "failed":
                    raise SystemExit(f"{label} failed for {result.get('key')}: {result.get('error')}")
            if now >= next_heartbeat and pending:
                print_heartbeat(label, pending, future_labels, submitted_at, completed, len(items), started)
                next_heartbeat = now + max(1.0, heartbeat_seconds)
    return failed


def item_label(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        if "kind" in item and "session" in item:
            return f"{item['kind']}:{item['session']}"
        if "kind" in item and "ticker" in item and "year_month" in item:
            return f"{item['kind']}:{item['ticker']}:{item['year_month']}"
        if "ticker" in item and "year_month" in item:
            return f"{item['ticker']}:{item['year_month']}"
    return str(item)


def print_heartbeat(
    label: str,
    pending: set[Any],
    future_labels: dict[Any, str],
    submitted_at: dict[Any, float],
    completed: int,
    total: int,
    started: float,
) -> None:
    now = time.time()
    longest = sorted(
        ((now - submitted_at[future], future_labels[future]) for future in pending),
        reverse=True,
    )[:5]
    in_flight = ", ".join(f"{name}={seconds:.0f}s" for seconds, name in longest)
    print(
        f"HEARTBEAT {label}: completed={completed:,}/{total:,} running={len(pending):,} "
        f"elapsed_minutes={(now - started) / 60.0:.1f} longest=[{in_flight}]",
        flush=True,
    )


def result_status(rows: Any) -> str:
    statuses = {str(row.get("status")) for row in rows}
    if "failed" in statuses:
        return "failed"
    if statuses and statuses <= {"skipped"}:
        return "skipped"
    return "ok"


def result_row(phase: str, key: str, status: str, rows: int, elapsed: float, details: dict[str, Any]) -> dict[str, Any]:
    return {
        "phase": phase,
        "key": key,
        "status": status,
        "rows": rows,
        "details": details,
        "elapsed_seconds": elapsed,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
    }


def failed_row(phase: str, key: str, elapsed: float) -> dict[str, Any]:
    return {
        "phase": phase,
        "key": key,
        "status": "failed",
        "rows": 0,
        "error": traceback.format_exc(),
        "elapsed_seconds": elapsed,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
    }


def config_to_payload(config: DataConfig) -> dict[str, Any]:
    payload = asdict(config)
    payload["flatfiles_root"] = str(payload["flatfiles_root"])
    payload["canonical_root"] = str(payload["canonical_root"])
    payload["cache_root"] = str(payload["cache_root"])
    return payload


def payload_to_config(payload: dict[str, Any]) -> DataConfig:
    clean = dict(payload)
    clean["flatfiles_root"] = Path(clean["flatfiles_root"])
    clean["canonical_root"] = Path(clean["canonical_root"])
    clean["cache_root"] = Path(clean["cache_root"])
    clean["tickers"] = tuple(clean.get("tickers") or ())
    return DataConfig(**clean)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def format_progress(result: dict[str, Any], completed: int, total: int, elapsed: float) -> str:
    return (
        f"[{completed:,}/{total:,}] {result.get('phase')} {result.get('key')} {result.get('status')} "
        f"rows={int(result.get('rows') or 0):,} item_seconds={float(result.get('elapsed_seconds') or 0):.1f} "
        f"elapsed_minutes={elapsed / 60.0:.1f}"
    )


if __name__ == "__main__":
    main()
