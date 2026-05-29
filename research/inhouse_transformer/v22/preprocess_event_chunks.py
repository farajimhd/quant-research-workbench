from __future__ import annotations

if __name__ == "__main__":
    print("*" * 96, flush=True)
    print("v22 preprocess python process started; importing modules...", flush=True)

import argparse
import concurrent.futures
import contextlib
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
    parser.add_argument("--normalize-processes", type=int, default=0, help="Worker count for raw CSV normalization. Defaults to --processes.")
    parser.add_argument("--quote-normalize-processes", type=int, default=0, help="Worker count for quote CSV normalization. Defaults to --normalize-processes.")
    parser.add_argument("--trade-normalize-processes", type=int, default=0, help="Worker count for trade CSV normalization. Defaults to --normalize-processes.")
    parser.add_argument("--canonical-processes", type=int, default=0, help="Worker count for canonical ticker-month merge. Defaults to --processes.")
    parser.add_argument("--chunk-processes", type=int, default=0, help="Worker count for chunk materialization. Defaults to --processes.")
    parser.add_argument("--polars-threads-per-process", type=int, default=2)
    parser.add_argument("--session-filter-mode", choices=["market_time", "utc_hour"], default=defaults.session_filter_mode)
    parser.add_argument("--session-timezone", default=defaults.session_timezone)
    parser.add_argument("--session-start-time-market", default=defaults.session_start_time_market)
    parser.add_argument("--session-end-time-market", default=defaults.session_end_time_market)
    parser.add_argument("--session-start-hour-utc", type=int, default=defaults.session_start_hour_utc)
    parser.add_argument("--session-end-hour-utc", type=int, default=defaults.session_end_hour_utc)
    parser.add_argument("--rebuild-cache", action="store_true")
    parser.add_argument("--keep-temp-normalized", action="store_true")
    parser.add_argument("--build-chunks", action="store_true", help="Also materialize dense event chunk tensors. Use only for small ticker subsets.")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--heartbeat-seconds", type=float, default=30.0)
    parser.add_argument("--max-pending", type=int, default=0, help="Maximum queued worker futures. Default is 2x processes.")
    parser.add_argument("--manifest-name", default="preprocess_event_chunks_manifest.jsonl")
    parser.add_argument("--verbose-worker-steps", action="store_true", help="Allow worker processes to print detailed internal step logs.")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    os.environ.setdefault("POLARS_MAX_THREADS", str(max(1, args.polars_threads_per_process)))
    import polars as pl

    from research.inhouse_transformer.v22.data import (
        available_sessions,
        canonical_event_path,
        discover_canonical_groups,
        discover_temp_canonical_groups,
        event_chunk_path,
        merge_temp_group_to_canonical,
        normalize_session_to_temp_parts,
        parse_ticker_list,
        temp_canonical_parts_root,
        year_month_range,
    )

    print(LOG_RULE, flush=True)
    print("v22 preprocessing startup", flush=True)
    print(f"flatfiles_root={args.flatfiles_root}", flush=True)
    print(f"date_range={args.start_date} -> {args.end_date}", flush=True)
    print("Discovering available quote/trade session pairs...", flush=True)
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
        session_filter_mode=args.session_filter_mode,
        session_timezone=args.session_timezone,
        session_start_time_market=args.session_start_time_market,
        session_end_time_market=args.session_end_time_market,
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
    print(
        f"session_filter_mode={config.session_filter_mode} timezone={config.session_timezone} "
        f"market_window={config.session_start_time_market}-{config.session_end_time_market} "
        f"utc_hour_fallback={config.session_start_hour_utc}-{config.session_end_hour_utc}"
    )
    normalize_processes = args.normalize_processes if args.normalize_processes > 0 else args.processes
    quote_normalize_processes = args.quote_normalize_processes if args.quote_normalize_processes > 0 else normalize_processes
    trade_normalize_processes = args.trade_normalize_processes if args.trade_normalize_processes > 0 else normalize_processes
    canonical_processes = args.canonical_processes if args.canonical_processes > 0 else args.processes
    chunk_processes = args.chunk_processes if args.chunk_processes > 0 else args.processes
    quote_normalize_max_pending = args.max_pending if args.max_pending > 0 else max(1, quote_normalize_processes) * 2
    trade_normalize_max_pending = args.max_pending if args.max_pending > 0 else max(1, trade_normalize_processes) * 2
    canonical_max_pending = args.max_pending if args.max_pending > 0 else max(1, canonical_processes) * 2
    chunk_max_pending = args.max_pending if args.max_pending > 0 else max(1, chunk_processes) * 2
    print(
        f"processes={args.processes} normalize_processes={normalize_processes} "
        f"quote_normalize_processes={quote_normalize_processes} "
        f"trade_normalize_processes={trade_normalize_processes} "
        f"canonical_processes={canonical_processes} chunk_processes={chunk_processes} "
        f"polars_threads_per_process={args.polars_threads_per_process} "
        f"max_pending(quote_norm/trade_norm/canonical/chunks)="
        f"{quote_normalize_max_pending}/{trade_normalize_max_pending}/{canonical_max_pending}/{chunk_max_pending}"
    )
    print(f"worker_step_logging={'on' if args.verbose_worker_steps else 'off'}")
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
        "verbose_worker_steps": args.verbose_worker_steps,
    }
    tickers = parse_ticker_list(args.tickers)
    existing_groups = discover_canonical_groups(
        config,
        start_date=args.start_date,
        end_date=args.end_date,
        tickers=tickers,
    )
    temp_groups = discover_temp_canonical_groups(config) if not args.rebuild_cache else {}
    if temp_groups and not args.rebuild_cache:
        print(f"Temporary normalized parts found for {len(temp_groups):,} ticker-month groups; resuming canonical merge.", flush=True)
    elif existing_groups and not args.rebuild_cache:
        print(f"Layer 1 canonical events already present for {len(existing_groups):,} ticker-month groups; skipping raw CSV normalization.", flush=True)
    else:
        print(LOG_RULE)
        print("PHASE 1/3 normalize raw CSV.GZ quote/trade files into temporary ticker partitions", flush=True)
        trade_normalize_items = [{"session": session, "kind": "trades"} for session in sessions]
        quote_normalize_items = [{"session": session, "kind": "quotes"} for session in sessions]
        failed += run_parallel(
            label="normalize trades",
            items=trade_normalize_items,
            submit=lambda executor, item: executor.submit(normalize_session_kind_worker, item, worker_payload),
            manifest_path=manifest_path,
            processes=trade_normalize_processes,
            started=started,
            fail_fast=args.fail_fast,
            heartbeat_seconds=args.heartbeat_seconds,
            max_pending=trade_normalize_max_pending,
        )
        failed += run_parallel(
            label="normalize quotes",
            items=quote_normalize_items,
            submit=lambda executor, item: executor.submit(normalize_session_kind_worker, item, worker_payload),
            manifest_path=manifest_path,
            processes=quote_normalize_processes,
            started=started,
            fail_fast=args.fail_fast,
            heartbeat_seconds=args.heartbeat_seconds,
            max_pending=quote_normalize_max_pending,
        )
        if failed:
            print(LOG_RULE)
            print(f"PHASE 1/3 failed={failed}; aborting before canonical/chunk phases to avoid partial derived data.", flush=True)
            print(f"Manifest: {manifest_path}", flush=True)
            print(LOG_RULE)
            raise SystemExit(1)
        temp_groups = discover_temp_canonical_groups(config)
    if temp_groups:
        print(LOG_RULE)
        print(f"PHASE 2/3 merge temporary ticker buckets into canonical ticker-month events groups={len(temp_groups):,}", flush=True)
        merge_items = [
            {"kind": kind, "year_month": year_month, "ticker_bucket": ticker_bucket, "paths": paths}
            for (kind, year_month, ticker_bucket), paths in sorted(temp_groups.items())
        ]
        failed += run_parallel(
            label="canonical",
            items=merge_items,
            submit=lambda executor, item: executor.submit(merge_canonical_worker, item, worker_payload),
            manifest_path=manifest_path,
            processes=canonical_processes,
            started=started,
            fail_fast=args.fail_fast,
            heartbeat_seconds=args.heartbeat_seconds,
            max_pending=canonical_max_pending,
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
    if not args.build_chunks:
        print(LOG_RULE)
        print(
            f"PHASE 3/3 dense event chunk cache skipped for {len(canonical_groups):,} canonical ticker-month groups.",
            flush=True,
        )
        print("Reason: full-market 500ms dense chunk materialization is very large; training can build needed session chunks lazily.", flush=True)
        print("Use --build-chunks only for small ticker subsets or profiling runs.", flush=True)
        print(LOG_RULE)
        print(f"Done. sessions={len(sessions)} canonical_groups={len(canonical_groups):,} failed={failed}")
        print(f"Manifest: {manifest_path}")
        print(LOG_RULE)
        if failed:
            raise SystemExit(1)
        return
    print(LOG_RULE)
    print(f"PHASE 3/3 build model-specific event chunk cache from canonical events groups={len(canonical_groups):,}", flush=True)
    failed += run_parallel(
        label="chunks",
        items=[{"ticker": ticker, "year_month": year_month} for ticker, year_month in canonical_groups],
        submit=lambda executor, item: executor.submit(build_chunks_worker, item, worker_payload),
        manifest_path=manifest_path,
        processes=chunk_processes,
        started=started,
        fail_fast=args.fail_fast,
        heartbeat_seconds=args.heartbeat_seconds,
        max_pending=chunk_max_pending,
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
    with worker_output_context(payload):
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
    with worker_output_context(payload):
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
    with worker_output_context(payload):
        from research.inhouse_transformer.v22.data import merge_temp_group_to_canonical

        try:
            config = payload_to_config(payload["config"])
            print(f"START canonical {item['kind']}:bucket={item['ticker_bucket']}:{item['year_month']} paths={len(item['paths'])}", flush=True)
            result = merge_temp_group_to_canonical(
                config,
                kind=item["kind"],
                year_month=item["year_month"],
                ticker_bucket=item["ticker_bucket"],
                paths=[Path(path) for path in item["paths"]],
                rebuild=bool(payload.get("rebuild")),
            )
            key = f"{item['kind']}:bucket={item['ticker_bucket']}:{item['year_month']}"
            return result_row("canonical", key, result["status"], int(result.get("rows") or 0), time.time() - started, result)
        except BaseException:
            return failed_row("canonical", f"{item.get('kind')}:bucket={item.get('ticker_bucket')}:{item.get('year_month')}", time.time() - started)


def build_chunks_worker(item: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    os.environ["POLARS_MAX_THREADS"] = str(max(1, int(payload["polars_threads_per_process"])))
    started = time.time()
    with worker_output_context(payload):
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


@contextlib.contextmanager
def worker_output_context(payload: dict[str, Any]) -> Any:
    if payload.get("verbose_worker_steps"):
        yield
        return
    with open(os.devnull, "w", encoding="utf-8") as sink:
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            yield


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
    max_pending: int,
) -> int:
    if not items:
        print(f"{label}: no work items", flush=True)
        return 0
    failed = 0
    completed = 0
    submitted = 0
    stop_submitting = False
    pending_limit = max(1, max_pending)
    submitted_at: dict[Any, float] = {}
    future_labels: dict[Any, str] = {}
    item_iter = iter(enumerate(items, start=1))

    def submit_next(executor: concurrent.futures.ProcessPoolExecutor, pending: set[Any]) -> bool:
        nonlocal completed, failed, stop_submitting, submitted
        if stop_submitting:
            return False
        try:
            index, item = next(item_iter)
        except StopIteration:
            return False
        item_name = item_label(item)
        try:
            future = submit(executor, item)
        except BaseException:
            submitted += 1
            completed += 1
            failed += 1
            stop_submitting = True
            result = failed_row(label, item_name, time.time() - started)
            append_jsonl(manifest_path, result)
            print(format_progress(result, completed, len(items), time.time() - started), flush=True)
            print(format_error(result), flush=True)
            return False
        pending.add(future)
        submitted += 1
        submitted_at[future] = time.time()
        future_labels[future] = item_name
        print(f"[{index:,}/{len(items):,}] SUBMIT {label} {future_labels[future]}", flush=True)
        return True

    with concurrent.futures.ProcessPoolExecutor(max_workers=max(1, processes)) as executor:
        pending = set()
        while len(pending) < min(pending_limit, len(items)):
            if not submit_next(executor, pending):
                break
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
                timing_summary = format_timing_summary(result)
                if timing_summary:
                    print(timing_summary, flush=True)
                if result["status"] == "failed":
                    print(format_error(result), flush=True)
                if fail_fast and result["status"] == "failed":
                    raise SystemExit(f"{label} failed for {result.get('key')}: {result.get('error')}")
                while not stop_submitting and len(pending) < pending_limit and submitted < len(items):
                    if not submit_next(executor, pending):
                        break
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


def format_timing_summary(result: dict[str, Any]) -> str:
    details = result.get("details")
    if not isinstance(details, dict):
        return ""
    timings = details.get("timings")
    if not isinstance(timings, list) or not timings:
        return ""
    wanted_steps = {
        "read_quotes_done",
        "read_trades_done",
        "attach_quote_state_done",
        "quote_aggregation_done",
        "trade_aggregation_done",
        "target_cache_done",
        "write_done",
    }
    parts: list[str] = []
    for row in timings:
        if not isinstance(row, dict) or row.get("step") not in wanted_steps:
            continue
        step = str(row["step"]).removesuffix("_done")
        elapsed = float(row.get("elapsed_seconds") or 0.0)
        rows = row.get("rows")
        extra = f":{int(rows):,}" if isinstance(rows, (int, float)) else ""
        if row.get("dense_grid_rows") is not None:
            extra += f":grid={int(row['dense_grid_rows']):,}"
        parts.append(f"{step}={elapsed:.1f}s{extra}")
    if not parts:
        return ""
    return f"TIMINGS {result.get('phase')} {result.get('key')}: " + " ".join(parts)


def format_error(result: dict[str, Any]) -> str:
    error = str(result.get("error") or "").strip()
    if len(error) > 4000:
        error = error[-4000:]
    return f"ERROR {result.get('phase')} {result.get('key')}\n{error}"


if __name__ == "__main__":
    main()
