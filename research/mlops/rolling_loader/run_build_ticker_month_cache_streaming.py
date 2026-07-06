from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import signal
import shutil
import sys
import threading
import time
import traceback
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import numpy as np

if __package__ in {None, ""}:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "research").is_dir():
            sys.path.insert(0, str(parent))
            break

from research.mlops.clickhouse import default_clickhouse_password, default_clickhouse_url, default_clickhouse_user, discover_clickhouse_env_files, quote_ident, sql_string
from research.mlops.env import load_env_files
from research.mlops.rolling_loader.run_build_ticker_month_cache import (
    DEFAULTS,
    FUTURE_CONDITION_GROUPS,
    INTRADAY_LABEL_GRID_RESOLUTIONS_US,
    SESSION_END_SECOND,
    SESSION_END_US,
    SESSION_START_SECOND,
    BuildStats,
    LaneState,
    PackageState,
    ProgressHeartbeat,
    TeeStream,
    TickerMonthDashboard,
    _bar_start_time_feature_sql,
    _build_corporate_action_daily_labels,
    _build_origins_and_windows,
    _byte_count,
    _cancel_active_work_with_grace,
    _client_options,
    _empty_frame,
    _events_source_table,
    _polars,
    _query_category_references,
    _query_corporate_actions,
    _query_daily_bars,
    _query_market_news,
    _query_sec_tokens,
    _query_ticker_news,
    _query_xbrl,
    _refresh,
    _row_count,
    _settings_sql,
    _write_parquet,
    cancel_active_clickhouse_queries,
    cancel_process_clickhouse_queries,
    current_rss_mib,
    query_polars,
)
from research.mlops.rolling_loader.ticker_month_cache import (
    BAR_START_TIME_FEATURE_COLUMNS,
    CONTEXT_AVAILABLE_TIME_FEATURE_COLUMNS,
    CONTEXT_EFFECTIVE_TIME_FEATURE_COLUMNS,
    DEFAULT_TICKER_MONTH_CACHE_ROOT,
    EVENT_PAYLOAD_COLUMNS,
    EVENT_TIME_FEATURE_COLUMNS,
    TICKER_MONTH_CACHE_FORMAT,
    TICKER_MONTH_CACHE_VERSION,
    build_config_from_args,
    cleanup_tmp_dirs,
    context_lags_from_args,
    directory_size,
    full_months_in_period,
    jsonable,
    month_dir_for,
    month_manifest_payload,
    month_window,
    month_window_dict,
    parse_day_horizons,
    parse_lags,
    replace_complete_dir,
    required_event_lookback_rows,
    ticker_package_dir,
    write_json_atomic,
)


@dataclass(frozen=True, slots=True)
class TickerMonthPlan:
    month: str
    ticker: str
    origin_first_ordinal: int
    origin_last_ordinal: int
    origin_event_count: int
    origin_first_date: str
    origin_last_date: str
    origin_first_timestamp_us: int
    origin_last_timestamp_us: int
    first_available_ordinal: int


@dataclass(frozen=True, slots=True)
class FetchChunk:
    month: str
    ticker: str
    part_id: int
    origin_ordinal_start: int
    origin_ordinal_end: int
    fetch_ordinal_start: int
    fetch_ordinal_end: int
    fetch_event_date_start: str
    fetch_event_date_end: str
    estimated_origin_rows: int


@dataclass(slots=True)
class PartBuildResult:
    month: str
    ticker: str
    part_id: int
    event_rows: int
    origin_rows: int
    skipped_history: int
    skipped_gap: int
    files: dict[str, str]
    counts: dict[str, int]
    bytes_written: int


@dataclass(slots=True)
class ContextBuildResult:
    month: str
    ticker: str
    files: dict[str, str] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    bytes_written: int = 0


STREAMING_TASK_STATE = threading.local()


def _parse_streaming_task_label(label: str) -> tuple[str, str]:
    parts = str(label or "").split(":")
    month = parts[0] if parts else ""
    ticker = parts[1] if len(parts) > 1 else ""
    return month, ticker


def _acquire_worker_state(stats: BuildStats, *, lane: str, label: str) -> PackageState:
    month, ticker = _parse_streaming_task_label(label)
    idle = [worker for worker in stats.workers.values() if worker.status != "running"]
    if idle:
        worker = min(idle, key=lambda item: item.worker_id)
    else:
        worker_id = max(stats.workers.keys(), default=-1) + 1
        worker = PackageState(worker_id=worker_id)
        stats.workers[worker_id] = worker
    worker.start_package(month=month, ticker=ticker)
    worker.stage = lane
    worker.message = label
    if lane == "fetch":
        worker.events_total = 1
    elif lane == "process":
        worker.cpu_total = 1
        worker.write_total = 1
    elif lane == "context":
        worker.context_total = 5
        worker.write_total = 5
    elif lane == "finalize":
        worker.cpu_total = 3
        worker.labels_total = 1
        worker.write_total = 2
    else:
        worker.cpu_total = 1
    return worker


def _task_update(
    *,
    stage: str | None = None,
    message: str | None = None,
    events_done: int | None = None,
    events_total: int | None = None,
    context_done: int | None = None,
    context_total: int | None = None,
    labels_done: int | None = None,
    labels_total: int | None = None,
    cpu_done: int | None = None,
    cpu_total: int | None = None,
    write_done: int | None = None,
    write_total: int | None = None,
) -> None:
    worker = getattr(STREAMING_TASK_STATE, "worker", None)
    if worker is None:
        return
    if stage is not None:
        worker.stage = str(stage)
    if message is not None:
        worker.message = str(message)
    for name, value in (
        ("events_done", events_done),
        ("events_total", events_total),
        ("context_done", context_done),
        ("context_total", context_total),
        ("labels_done", labels_done),
        ("labels_total", labels_total),
        ("cpu_done", cpu_done),
        ("cpu_total", cpu_total),
        ("write_done", write_done),
        ("write_total", write_total),
    ):
        if value is not None:
            setattr(worker, name, int(value))
    if worker.started_at:
        worker.seconds = time.perf_counter() - worker.started_at


def _finish_worker_state(worker: PackageState | None, *, status: str, message: str) -> None:
    if worker is None:
        return
    worker.status = status
    worker.stage = status
    worker.message = message
    if worker.started_at:
        worker.seconds = time.perf_counter() - worker.started_at


class StreamingLaneExecutors:
    def __init__(self, stats: BuildStats, workers: Mapping[str, int]) -> None:
        self.stats = stats
        self.executors: dict[str, ThreadPoolExecutor] = {}
        for name, count in workers.items():
            safe_count = max(1, int(count))
            self.executors[name] = ThreadPoolExecutor(max_workers=safe_count, thread_name_prefix=f"tmc-stream-{name}")
            stats.lanes[name] = LaneState(name=name, workers=safe_count)

    def submit(self, lane: str, label: str, fn: Callable[[], Any]) -> Future[Any]:
        state = self.stats.lanes[lane]
        with self.stats.lock:
            state.queued += 1

        def wrapped() -> Any:
            thread_id = threading.get_ident()
            started = time.perf_counter()
            worker: PackageState | None = None
            with self.stats.lock:
                state.queued = max(0, state.queued - 1)
                state.running += 1
                state.active[thread_id] = label
                worker = _acquire_worker_state(self.stats, lane=lane, label=label)
            STREAMING_TASK_STATE.worker = worker
            STREAMING_TASK_STATE.lane = lane
            self.stats.profile_event(f"{lane}_start", label=label)
            try:
                result = fn()
            except BaseException as exc:
                elapsed = time.perf_counter() - started
                with self.stats.lock:
                    state.running = max(0, state.running - 1)
                    state.failed += 1
                    state.seconds += elapsed
                    state.active.pop(thread_id, None)
                    _finish_worker_state(worker, status="failed", message=repr(exc))
                self.stats.profile_event(f"{lane}_error", label=label, seconds=elapsed, error=repr(exc))
                raise
            finally:
                STREAMING_TASK_STATE.worker = None
                STREAMING_TASK_STATE.lane = ""
            elapsed = time.perf_counter() - started
            rows = _row_count(result)
            bytes_count = _byte_count(result)
            with self.stats.lock:
                state.running = max(0, state.running - 1)
                state.done += 1
                state.rows += rows
                state.bytes += bytes_count
                state.seconds += elapsed
                state.active.pop(thread_id, None)
                _finish_worker_state(worker, status="done", message=f"{label} done in {elapsed:.1f}s")
            self.stats.profile_event(f"{lane}_done", label=label, seconds=elapsed, rows=rows, bytes=bytes_count)
            return result

        return self.executors[lane].submit(wrapped)

    def shutdown(self, *, wait_for_running: bool) -> None:
        for executor in self.executors.values():
            executor.shutdown(wait=wait_for_running, cancel_futures=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build ticker/month rolling cache with index-planned small event fetches and CPU-side vectorized processing.")
    parser.add_argument("--clickhouse-url", default="")
    parser.add_argument("--user", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--env-file", type=Path, default=None)
    parser.add_argument("--database", default=DEFAULTS["database"])
    parser.add_argument("--sec-context-database", default=DEFAULTS["sec_context_database"])
    parser.add_argument("--q-live-database", default=DEFAULTS["q_live_database"])
    parser.add_argument("--events-table", default=DEFAULTS["events_table"])
    parser.add_argument("--events-ticker-day-index-table", default="events_ticker_day_index")
    parser.add_argument("--condition-token-reference-table", default=DEFAULTS["condition_token_reference_table"])
    parser.add_argument("--macro-bars-table", default=DEFAULTS["macro_bars_table"])
    parser.add_argument("--news-token-table", default=DEFAULTS["news_token_table"])
    parser.add_argument("--sec-filing-text-token-table", default=DEFAULTS["sec_filing_text_token_table"])
    parser.add_argument("--news-embedding-table", default=DEFAULTS["news_embedding_table"])
    parser.add_argument("--sec-filing-text-embedding-table", default=DEFAULTS["sec_filing_text_embedding_table"])
    parser.add_argument("--sec-xbrl-context-table", default=DEFAULTS["sec_xbrl_context_table"])
    parser.add_argument("--category-reference-table", default=DEFAULTS["category_reference_table"])
    parser.add_argument("--stock-split-table", default=DEFAULTS["stock_split_table"])
    parser.add_argument("--cash-dividend-table", default=DEFAULTS["cash_dividend_table"])
    parser.add_argument("--cache-root", type=Path, default=Path(DEFAULTS["cache_root"]))
    parser.add_argument("--cache-id", default="")
    parser.add_argument("--split", default=DEFAULTS["split"])
    parser.add_argument("--month", default="", help="Single month, YYYY-MM.")
    parser.add_argument("--start-utc", default="", help="Inclusive period start. Only full months inside the period are built.")
    parser.add_argument("--end-utc", default="", help="Exclusive period end. Only full months inside the period are built.")
    parser.add_argument("--tickers", default="", help="Optional comma-separated ticker subset.")
    parser.add_argument("--ticker-limit", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--fetch-workers", type=int, default=8)
    parser.add_argument("--process-workers", type=int, default=48)
    parser.add_argument("--context-workers", type=int, default=24)
    parser.add_argument("--finalize-workers", type=int, default=16)
    parser.add_argument("--max-inflight-fetches", type=int, default=48)
    parser.add_argument("--max-inflight-process", type=int, default=128)
    parser.add_argument("--target-origin-events-per-fetch", type=int, default=500_000, help="Target origin ordinal span per event fetch. Large liquid tickers are split.")
    parser.add_argument("--max-cached-event-lookback-rows", type=int, default=DEFAULTS["max_cached_event_lookback_rows"])
    parser.add_argument("--events-per-chunk", type=int, default=DEFAULTS["events_per_chunk"])
    parser.add_argument("--short-context-chunks", type=int, default=DEFAULTS["short_context_chunks"])
    parser.add_argument("--context-chunk-stride-events", type=int, default=DEFAULTS["context_chunk_stride_events"])
    parser.add_argument("--short-context-stride-chunks", type=int, default=DEFAULTS["short_context_stride_chunks"])
    parser.add_argument("--long-context-lags", default=DEFAULTS["long_context_lags"])
    parser.add_argument("--sample-stride-events", type=int, default=DEFAULTS["sample_stride_events"])
    parser.add_argument("--max-threads", type=int, default=DEFAULTS["max_threads"])
    parser.add_argument("--max-memory-usage", default=DEFAULTS["max_memory_usage"])
    parser.add_argument("--clickhouse-query-retries", type=int, default=DEFAULTS["clickhouse_query_retries"])
    parser.add_argument("--clickhouse-query-retry-backoff-seconds", type=float, default=DEFAULTS["clickhouse_query_retry_backoff_seconds"])
    parser.add_argument("--macro-lookback-days", type=int, default=DEFAULTS["macro_lookback_days"])
    parser.add_argument("--label-lookahead-days", type=int, default=DEFAULTS["label_lookahead_days"])
    parser.add_argument("--news-lookback-days", type=int, default=DEFAULTS["news_lookback_days"])
    parser.add_argument("--sec-lookback-days", type=int, default=DEFAULTS["sec_lookback_days"])
    parser.add_argument("--xbrl-lookback-days", type=int, default=DEFAULTS["xbrl_lookback_days"])
    parser.add_argument("--ticker-news-items", type=int, default=DEFAULTS["ticker_news_items"])
    parser.add_argument("--market-news-items", type=int, default=DEFAULTS["market_news_items"])
    parser.add_argument("--sec-filing-items", type=int, default=DEFAULTS["sec_filing_items"])
    parser.add_argument("--ticker-news-prior-items", type=int, default=DEFAULTS["ticker_news_prior_items"])
    parser.add_argument("--market-news-prior-items", type=int, default=DEFAULTS["market_news_prior_items"])
    parser.add_argument("--sec-filing-prior-items", type=int, default=DEFAULTS["sec_filing_prior_items"])
    parser.add_argument("--xbrl-items", type=int, default=DEFAULTS["xbrl_items"])
    parser.add_argument("--xbrl-prior-rows", type=int, default=DEFAULTS["xbrl_prior_rows"])
    parser.add_argument("--corporate-action-items", type=int, default=DEFAULTS["corporate_action_items"])
    parser.add_argument("--corporate-action-lookback-days", type=int, default=DEFAULTS["corporate_action_lookback_days"])
    parser.add_argument("--corporate-action-label-days", default=DEFAULTS["corporate_action_label_days"])
    parser.add_argument("--intraday-label-horizons", default=DEFAULTS["intraday_label_horizons"])
    parser.add_argument("--intraday-context-horizons", default=DEFAULTS["intraday_context_horizons"])
    parser.add_argument("--skip-token-contexts", action="store_true")
    parser.add_argument("--skip-xbrl", action="store_true")
    parser.add_argument("--skip-corporate-actions", action="store_true")
    parser.add_argument("--skip-market-package", action="store_true")
    parser.add_argument("--refresh-seconds", type=float, default=1.0)
    parser.add_argument("--no-rich", action="store_true")
    parser.add_argument("--plain-status", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    env_files = discover_clickhouse_env_files()
    if args.env_file is not None:
        env_files.append(args.env_file)
    loaded_env = load_env_files(env_files, verbose=False)
    if not args.clickhouse_url:
        args.clickhouse_url = default_clickhouse_url()
    if not args.user:
        args.user = default_clickhouse_user()
    if not args.password:
        args.password = default_clickhouse_password()
    months = _resolve_months(args)
    if not months:
        raise SystemExit("No full month selected. Use --month YYYY-MM or a period containing full months.")
    cache_id = args.cache_id or f"{args.split}_{months[0].replace('-', '')}_{months[-1].replace('-', '')}_ticker_month_streaming"
    cache_root = Path(args.cache_root) / cache_id
    cache_root.mkdir(parents=True, exist_ok=True)
    log_handle = (cache_root / "terminal.log").open("a", encoding="utf-8")
    original_stdout, original_stderr = sys.stdout, sys.stderr
    sys.stdout = TeeStream(sys.stdout, log_handle)  # type: ignore[assignment]
    sys.stderr = TeeStream(sys.stderr, log_handle)  # type: ignore[assignment]
    stats = BuildStats(
        split=str(args.split),
        months_total=len(months),
        log_path=cache_root / "streaming_builder_events.jsonl",
        profile_path=cache_root / "streaming_builder_profile_events.jsonl",
        errors_path=cache_root / "streaming_errors.jsonl",
        progress_path=cache_root / f"{args.split}_streaming_progress.json",
    )
    lanes: StreamingLaneExecutors | None = None
    dashboard: TickerMonthDashboard | None = None
    heartbeat: ProgressHeartbeat | None = None
    stop_event = threading.Event()
    client_opts = _client_options(args)
    config = build_config_from_args(args)
    context_lags = context_lags_from_args(
        events_per_chunk=args.events_per_chunk,
        short_context_chunks=args.short_context_chunks,
        context_chunk_stride_events=args.context_chunk_stride_events,
        short_context_stride_chunks=args.short_context_stride_chunks,
        long_context_lags=parse_lags(args.long_context_lags),
    )
    if args.max_cached_event_lookback_rows < required_event_lookback_rows(context_lags, args.events_per_chunk):
        args.max_cached_event_lookback_rows = required_event_lookback_rows(context_lags, args.events_per_chunk)
    manifest = month_manifest_payload(args=args, cache_id=cache_id, cache_root=cache_root, loaded_env=loaded_env, months=months, context_lags=context_lags)
    manifest["config"]["builder_mode"] = "index_planned_streaming_cpu_vectorized"
    manifest["config"]["context_semantics"] = "package_level_time_ordered_streams_resolved_per_origin_by_loader"
    write_json_atomic(cache_root / "manifest.json", manifest)
    previous_sigint = signal.getsignal(signal.SIGINT)
    try:
        if loaded_env:
            print("Loaded .env files: " + ", ".join(str(path) for path in loaded_env), flush=True)
        cleanup_tmp_dirs(cache_root)
        stats.message(f"cache_root={cache_root}")
        stats.message(f"months={','.join(months)}")
        lanes = StreamingLaneExecutors(
            stats,
            {
                "plan": 1,
                "fetch": args.fetch_workers,
                "process": args.process_workers,
                "context": args.context_workers,
                "finalize": args.finalize_workers,
            },
        )
        dashboard = TickerMonthDashboard(enabled=not args.no_rich, live=not args.plain_status, refresh_seconds=args.refresh_seconds, stats=stats)
        worker_slots = max(12, int(args.fetch_workers) + int(args.process_workers) + int(args.context_workers) + int(args.finalize_workers))
        stats.workers = {idx: PackageState(worker_id=idx) for idx in range(worker_slots)}

        def request_stop(_signum: int, _frame: Any) -> None:
            stop_event.set()
            stats.stop_requested = True
            stats.interrupted = True
            stats.phase = "stopping"
            message = "Ctrl+C received; cancelling active ClickHouse queries and queued work"
            stats.message(message)
            try:
                original_stderr.write("\n" + message + "\n")
                original_stderr.flush()
            except Exception:
                pass
            cancel_active_clickhouse_queries(client_opts=client_opts, stats=stats)
            cancel_process_clickhouse_queries(client_opts=client_opts, stats=stats, reason="ctrl_c")
            raise KeyboardInterrupt

        signal.signal(signal.SIGINT, request_stop)
        dashboard.start()
        heartbeat = ProgressHeartbeat(cache_root=cache_root, stats=stats, dashboard=dashboard, refresh_seconds=args.refresh_seconds)
        heartbeat.start()
        for month in months:
            if stop_event.is_set():
                break
            _build_month(
                args=args,
                cache_root=cache_root,
                month=month,
                client_opts=client_opts,
                config=config,
                context_lags=context_lags,
                stats=stats,
                lanes=lanes,
                stop_event=stop_event,
            )
            stats.months_done += 1
        manifest["status"] = "complete" if not stop_event.is_set() else "interrupted"
        manifest["completed_at"] = dt.datetime.now(tz=dt.timezone.utc).isoformat()
        manifest["packages"] = sorted(str(path.relative_to(cache_root)).replace("\\", "/") for path in cache_root.rglob("manifest.json") if path.parent != cache_root)
        write_json_atomic(cache_root / "manifest.json", manifest)
        stats.phase = manifest["status"]
        _refresh(stats, dashboard, cache_root, force=True)
        return 130 if stop_event.is_set() else 0
    except KeyboardInterrupt:
        stop_event.set()
        stats.interrupted = True
        stats.phase = "interrupted"
        _cancel_active_work_with_grace(client_opts=client_opts, stats=stats, dashboard=dashboard, cache_root=cache_root, reason="interrupt")
        manifest["status"] = "interrupted"
        manifest["completed_at"] = dt.datetime.now(tz=dt.timezone.utc).isoformat()
        write_json_atomic(cache_root / "manifest.json", manifest)
        return 130
    except BaseException as exc:
        stats.phase = "error"
        stats.log_error("main", exc)
        _cancel_active_work_with_grace(client_opts=client_opts, stats=stats, dashboard=dashboard, cache_root=cache_root, reason="error")
        manifest["status"] = "error"
        manifest["error"] = repr(exc)
        manifest["completed_at"] = dt.datetime.now(tz=dt.timezone.utc).isoformat()
        write_json_atomic(cache_root / "manifest.json", manifest)
        raise
    finally:
        if lanes is not None:
            lanes.shutdown(wait_for_running=not stop_event.is_set())
        if heartbeat is not None:
            heartbeat.stop()
        if dashboard is not None:
            dashboard.stop()
        signal.signal(signal.SIGINT, previous_sigint)
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        log_handle.close()


def _resolve_months(args: argparse.Namespace) -> tuple[str, ...]:
    if args.month:
        return (str(args.month).strip(),)
    if args.start_utc and args.end_utc:
        return full_months_in_period(args.start_utc, args.end_utc)
    raise SystemExit("Use --month YYYY-MM or --start-utc/--end-utc.")


def _build_month(
    *,
    args: argparse.Namespace,
    cache_root: Path,
    month: str,
    client_opts: Mapping[str, str],
    config: Any,
    context_lags: tuple[int, ...],
    stats: BuildStats,
    lanes: StreamingLaneExecutors,
    stop_event: threading.Event,
) -> None:
    stats.phase = f"plan {month}"
    window = month_window(month)
    month_dir = month_dir_for(cache_root, args.split, month)
    month_dir.mkdir(parents=True, exist_ok=True)
    plans = _plan_month(args=args, client_opts=client_opts, config=config, window=window, month=month)
    if args.tickers:
        wanted = {item.strip().upper() for item in args.tickers.split(",") if item.strip()}
        plans = [plan for plan in plans if plan.ticker in wanted]
    if args.ticker_limit > 0:
        plans = plans[: int(args.ticker_limit)]
    if not args.resume:
        original_count = len(plans)
        plans = [plan for plan in plans if not ticker_package_dir(month_dir, plan.ticker).exists()]
        skipped = original_count - len(plans)
        if skipped:
            stats.message(f"{month}: skipped {skipped:,} existing ticker packages; pass --resume to rebuild")
    stats.packages_total += len(plans) + (0 if args.skip_market_package else 1)
    stats.message(f"{month}: planned {len(plans):,} ticker packages")
    if not args.skip_market_package:
        _write_global_package(args=args, client_opts=client_opts, config=config, cache_root=cache_root, month=month, window=window, stats=stats, lanes=lanes)
    stats.phase = f"build {month}"
    pending_fetch: set[Future[Any]] = set()
    pending_process: set[Future[Any]] = set()
    context_futures: dict[tuple[str, str], Future[ContextBuildResult]] = {}
    part_results: dict[str, list[PartBuildResult]] = {plan.ticker: [] for plan in plans}
    chunks_by_ticker: dict[str, list[FetchChunk]] = {}
    chunk_iter = iter(_iter_fetch_chunks(args=args, plans=plans, client_opts=client_opts, config=config, window=window))

    for plan in plans:
        context_futures[(month, plan.ticker)] = lanes.submit(
            "context",
            f"{month}:{plan.ticker}:context",
            lambda plan=plan: _build_ticker_context(args=args, client_opts=client_opts, config=config, cache_root=cache_root, window=window, plan=plan),
        )

    exhausted = False
    while not exhausted or pending_fetch or pending_process:
        if stop_event.is_set():
            raise KeyboardInterrupt
        while not exhausted and len(pending_fetch) < max(1, int(args.max_inflight_fetches)):
            try:
                chunk = next(chunk_iter)
            except StopIteration:
                exhausted = True
                break
            chunks_by_ticker.setdefault(chunk.ticker, []).append(chunk)
            pending_fetch.add(
                lanes.submit(
                    "fetch",
                    f"{chunk.month}:{chunk.ticker}:part{chunk.part_id:05d}:events",
                    lambda chunk=chunk: (chunk, _query_events_chunk(args=args, client_opts=client_opts, config=config, chunk=chunk)),
                )
            )
        if pending_fetch:
            done_fetch, pending_fetch = wait(pending_fetch, timeout=0.05, return_when=FIRST_COMPLETED)
            for future in done_fetch:
                chunk, events = future.result()
                while len(pending_process) >= max(1, int(args.max_inflight_process)):
                    done_process, pending_process = wait(pending_process, timeout=0.1, return_when=FIRST_COMPLETED)
                    for process_future in done_process:
                        result = process_future.result()
                        part_results[result.ticker].append(result)
                        _record_part_stats(stats, result)
                pending_process.add(
                    lanes.submit(
                        "process",
                        f"{chunk.month}:{chunk.ticker}:part{chunk.part_id:05d}:process",
                        lambda chunk=chunk, events=events: _process_event_chunk(
                            args=args,
                            cache_root=cache_root,
                            window=window,
                            context_lags=context_lags,
                            chunk=chunk,
                            events=events,
                        ),
                    )
                )
        if pending_process:
            done_process, pending_process = wait(pending_process, timeout=0.05, return_when=FIRST_COMPLETED)
            for future in done_process:
                result = future.result()
                part_results[result.ticker].append(result)
                _record_part_stats(stats, result)
        stats.current_rss_mib = current_rss_mib()
        stats.max_rss_mib = max(stats.max_rss_mib, stats.current_rss_mib)

    context_results = {key: future.result() for key, future in context_futures.items()}
    finalize_futures: list[Future[Any]] = []
    for plan in plans:
        finalize_futures.append(
            lanes.submit(
                "finalize",
                f"{month}:{plan.ticker}:finalize",
                lambda plan=plan: _finalize_package(
                    args=args,
                    cache_root=cache_root,
                    window=window,
                    plan=plan,
                    chunks=chunks_by_ticker.get(plan.ticker, []),
                    part_results=part_results.get(plan.ticker, []),
                    context_result=context_results[(month, plan.ticker)],
                    context_lags=context_lags,
                ),
            )
        )
    for future in finalize_futures:
        package_bytes = int(future.result())
        with stats.lock:
            stats.packages_done += 1
            stats.bytes_written += package_bytes
        stats.current_rss_mib = current_rss_mib()
        stats.max_rss_mib = max(stats.max_rss_mib, stats.current_rss_mib)
    stats.message(f"{month}: complete packages={len(plans):,}")


def _plan_month(*, args: argparse.Namespace, client_opts: Mapping[str, str], config: Any, window: Any, month: str) -> list[TickerMonthPlan]:
    table = f"{quote_ident(config.database)}.{quote_ident(args.events_ticker_day_index_table)}"
    query = f"""
SELECT
    upper(ticker) AS ticker,
    min(first_ordinal) AS origin_first_ordinal,
    max(last_ordinal) AS origin_last_ordinal,
    sum(row_count) AS origin_event_count,
    min(source_date) AS origin_first_date,
    max(source_date) AS origin_last_date,
    min(first_timestamp_us) AS origin_first_timestamp_us,
    max(last_timestamp_us) AS origin_last_timestamp_us
FROM
(
    SELECT
        upper(ticker) AS ticker,
        source_date,
        argMax(event_count, built_at) AS row_count,
        argMax(first_ordinal, built_at) AS first_ordinal,
        argMax(last_ordinal, built_at) AS last_ordinal,
        argMax(first_sip_timestamp_us, built_at) AS first_timestamp_us,
        argMax(last_sip_timestamp_us, built_at) AS last_timestamp_us
    FROM {table}
    WHERE source_date >= toDate({sql_string(window.first_date.isoformat())})
      AND source_date < toDate({sql_string(window.next_month_date.isoformat())})
    GROUP BY
        ticker,
        source_date
)
GROUP BY ticker
HAVING origin_event_count > 0
ORDER BY ticker
{_settings_sql(config)}
"""
    frame = query_polars(client_opts, query)
    if frame.height == 0:
        return []
    tickers = [str(value).upper() for value in frame.get_column("ticker").to_list()]
    first_ordinals = {str(row["ticker"]).upper(): int(row["origin_first_ordinal"]) for row in frame.iter_rows(named=True)}
    first_available = _query_first_available_ordinals(args=args, client_opts=client_opts, config=config, window=window, tickers=tickers)
    plans: list[TickerMonthPlan] = []
    for row in frame.iter_rows(named=True):
        ticker = str(row["ticker"]).upper()
        plans.append(
            TickerMonthPlan(
                month=month,
                ticker=ticker,
                origin_first_ordinal=int(row["origin_first_ordinal"]),
                origin_last_ordinal=int(row["origin_last_ordinal"]),
                origin_event_count=int(row["origin_event_count"]),
                origin_first_date=str(row["origin_first_date"]),
                origin_last_date=str(row["origin_last_date"]),
                origin_first_timestamp_us=int(row["origin_first_timestamp_us"]),
                origin_last_timestamp_us=int(row["origin_last_timestamp_us"]),
                first_available_ordinal=int(first_available.get(ticker, first_ordinals[ticker])),
            )
        )
    return plans


def _query_first_available_ordinals(*, args: argparse.Namespace, client_opts: Mapping[str, str], config: Any, window: Any, tickers: list[str]) -> dict[str, int]:
    if not tickers:
        return {}
    table = f"{quote_ident(config.database)}.{quote_ident(args.events_ticker_day_index_table)}"
    chunks: list[dict[str, int]] = []
    for offset in range(0, len(tickers), 2000):
        ticker_sql = ", ".join(sql_string(ticker) for ticker in tickers[offset : offset + 2000])
        query = f"""
SELECT
    upper(ticker) AS ticker,
    min(first_ordinal) AS first_available_ordinal
FROM
(
    SELECT
        upper(ticker) AS ticker,
        source_date,
        argMax(first_ordinal, built_at) AS first_ordinal
    FROM {table}
    WHERE source_date < toDate({sql_string(window.next_month_date.isoformat())})
      AND upper(ticker) IN ({ticker_sql})
    GROUP BY
        ticker,
        source_date
)
GROUP BY ticker
{_settings_sql(config)}
"""
        for row in query_polars(client_opts, query).iter_rows(named=True):
            chunks.append({"ticker": str(row["ticker"]).upper(), "first_available_ordinal": int(row["first_available_ordinal"])})
    return {row["ticker"]: int(row["first_available_ordinal"]) for row in chunks}


def _iter_fetch_chunks(*, args: argparse.Namespace, plans: Iterable[TickerMonthPlan], client_opts: Mapping[str, str], config: Any, window: Any) -> Iterable[FetchChunk]:
    for plan in plans:
        origin_start = int(plan.origin_first_ordinal)
        origin_end = int(plan.origin_last_ordinal)
        span = max(1, int(args.target_origin_events_per_fetch))
        part_id = 0
        current = origin_start
        while current <= origin_end:
            chunk_end = min(origin_end, current + span - 1)
            fetch_start = max(int(plan.first_available_ordinal), current - int(args.max_cached_event_lookback_rows))
            fetch_end = chunk_end
            date_start, date_end = _query_fetch_date_bounds(
                args=args,
                client_opts=client_opts,
                config=config,
                ticker=plan.ticker,
                fetch_ordinal_start=fetch_start,
                fetch_ordinal_end=fetch_end,
                default_start=window.first_date.isoformat(),
                default_end=window.next_month_date.isoformat(),
            )
            yield FetchChunk(
                month=plan.month,
                ticker=plan.ticker,
                part_id=part_id,
                origin_ordinal_start=current,
                origin_ordinal_end=chunk_end,
                fetch_ordinal_start=fetch_start,
                fetch_ordinal_end=fetch_end,
                fetch_event_date_start=date_start,
                fetch_event_date_end=date_end,
                estimated_origin_rows=chunk_end - current + 1,
            )
            current = chunk_end + 1
            part_id += 1


def _query_fetch_date_bounds(
    *,
    args: argparse.Namespace,
    client_opts: Mapping[str, str],
    config: Any,
    ticker: str,
    fetch_ordinal_start: int,
    fetch_ordinal_end: int,
    default_start: str,
    default_end: str,
) -> tuple[str, str]:
    table = f"{quote_ident(config.database)}.{quote_ident(args.events_ticker_day_index_table)}"
    query = f"""
SELECT
    min(source_date) AS start_date,
    max(source_date) AS end_date,
    count() AS rows
FROM
(
    SELECT
        source_date,
        argMax(first_ordinal, built_at) AS first_ordinal,
        argMax(last_ordinal, built_at) AS last_ordinal
    FROM {table}
    WHERE upper(ticker) = {sql_string(ticker.upper())}
    GROUP BY source_date
)
WHERE last_ordinal >= {int(fetch_ordinal_start)}
  AND first_ordinal <= {int(fetch_ordinal_end)}
{_settings_sql(config)}
"""
    frame = query_polars(client_opts, query)
    if frame.height and int(frame.get_column("rows")[0] or 0) > 0:
        return str(frame.get_column("start_date")[0]), str(frame.get_column("end_date")[0])
    return default_start, default_end


def _query_events_chunk(*, args: argparse.Namespace, client_opts: Mapping[str, str], config: Any, chunk: FetchChunk) -> Any:
    table = _events_source_table(config, chunk.fetch_event_date_start, chunk.fetch_event_date_end)
    _task_update(
        stage="fetch",
        events_done=0,
        events_total=1,
        message=f"{chunk.ticker} ord {chunk.fetch_ordinal_start:,}-{chunk.fetch_ordinal_end:,}",
    )
    query = f"""
WITH
    fromUnixTimestamp64Micro(sip_timestamp_us, 'UTC') AS ts_utc,
    toTimeZone(ts_utc, 'America/New_York') AS ts_local,
    dateDiff('second', toStartOfDay(ts_utc), ts_utc) AS utc_second,
    toDayOfWeek(ts_utc) AS utc_dow,
    toDayOfYear(ts_utc) AS utc_doy,
    dateDiff('second', toStartOfDay(ts_local), ts_local) AS local_second
SELECT
    cityHash64(ticker) AS ticker_id,
    upper(ticker) AS ticker,
    ordinal,
    event_meta,
    sip_timestamp_us AS timestamp_us,
    price_primary_int,
    price_secondary_int,
    toFloat32(size_primary) AS size_primary,
    toFloat32(size_secondary) AS size_secondary,
    exchange_primary,
    exchange_secondary,
    condition_token_1,
    condition_token_2,
    condition_token_3,
    condition_token_4,
    condition_token_5,
    toFloat32(sin(2 * pi() * utc_second / 86400.0)) AS utc_second_of_day_sin,
    toFloat32(cos(2 * pi() * utc_second / 86400.0)) AS utc_second_of_day_cos,
    toFloat32(sin(2 * pi() * (utc_dow - 1) / 7.0)) AS utc_day_of_week_sin,
    toFloat32(cos(2 * pi() * (utc_dow - 1) / 7.0)) AS utc_day_of_week_cos,
    toFloat32(sin(2 * pi() * (utc_doy - 1) / 366.0)) AS utc_day_of_year_sin,
    toFloat32(cos(2 * pi() * (utc_doy - 1) / 366.0)) AS utc_day_of_year_cos,
    toFloat32(toYear(ts_utc) - 2000 + (utc_doy - 1) / 366.0) AS years_since_2000,
    toDate(ts_local) AS local_date,
    toUInt64(dateDiff('microsecond', toStartOfDay(ts_local), ts_local)) AS local_session_us,
    toUInt32(local_second) AS session_second,
    toFloat32(greatest(0, least(57600, local_second - 14400)) / 57600.0) AS session_progress,
    toUInt8(local_second >= 34200 AND local_second < 57600) AS is_regular_hours,
    toUInt8(local_second >= 14400 AND local_second < 34200) AS is_premarket,
    toUInt8(local_second >= 57600 AND local_second < 72000) AS is_afterhours
FROM {table}
PREWHERE ticker = {sql_string(chunk.ticker)}
  AND ordinal >= {int(chunk.fetch_ordinal_start)}
  AND ordinal <= {int(chunk.fetch_ordinal_end)}
  AND event_date >= toDate({sql_string(chunk.fetch_event_date_start)})
  AND event_date <= toDate({sql_string(chunk.fetch_event_date_end)})
ORDER BY ticker, ordinal
{_settings_sql(config)}
"""
    frame = query_polars(client_opts, query)
    _task_update(events_done=1, message=f"fetched {frame.height:,} event rows")
    return frame


def _process_event_chunk(
    *,
    args: argparse.Namespace,
    cache_root: Path,
    window: Any,
    context_lags: tuple[int, ...],
    chunk: FetchChunk,
    events: Any,
) -> PartBuildResult:
    package_tmp = _package_tmp_dir(cache_root, args.split, chunk.month, chunk.ticker)
    package_tmp.mkdir(parents=True, exist_ok=True)
    _task_update(
        stage="window-index",
        cpu_done=0,
        cpu_total=1,
        write_done=0,
        write_total=4,
        message=f"building windows for {events.height:,} rows",
    )
    origins, windows, ranges, skipped_history, skipped_gap = _build_origins_and_windows(
        events,
        context_lags,
        int(args.events_per_chunk),
        int(args.sample_stride_events),
        window,
        origin_ordinal_start=int(chunk.origin_ordinal_start),
        origin_ordinal_end=int(chunk.origin_ordinal_end),
        month_min_ordinal=int(chunk.origin_ordinal_start),
    )
    _task_update(cpu_done=1, stage="write-parts", message=f"origins={origins.height:,} windows={windows.height:,}")
    files = {
        "events": f"events_part_{chunk.part_id:05d}.parquet",
        "origins": f"origins_part_{chunk.part_id:05d}.parquet",
        "event_window_index": f"event_window_index_part_{chunk.part_id:05d}.parquet",
        "ranges": f"ranges_part_{chunk.part_id:05d}.parquet",
    }
    writes = []
    for idx, (frame, key) in enumerate(
        (
            (events, "events"),
            (origins, "origins"),
            (windows, "event_window_index"),
            (ranges, "ranges"),
        ),
        start=1,
    ):
        writes.append(_write_parquet(frame, package_tmp / files[key]))
        _task_update(write_done=idx, message=f"wrote {key} rows={frame.height:,}")
    return PartBuildResult(
        month=chunk.month,
        ticker=chunk.ticker,
        part_id=chunk.part_id,
        event_rows=int(events.height),
        origin_rows=int(origins.height),
        skipped_history=int(skipped_history),
        skipped_gap=int(skipped_gap),
        files=files,
        counts={
            "events": int(events.height),
            "origins": int(origins.height),
            "event_window_index": int(windows.height),
            "ranges": int(ranges.height),
        },
        bytes_written=sum(int(item["bytes"]) for item in writes),
    )


def _build_ticker_context(*, args: argparse.Namespace, client_opts: Mapping[str, str], config: Any, cache_root: Path, window: Any, plan: TickerMonthPlan) -> ContextBuildResult:
    package_tmp = _package_tmp_dir(cache_root, args.split, plan.month, plan.ticker)
    package_tmp.mkdir(parents=True, exist_ok=True)
    query_steps: list[tuple[str, Callable[[], Any]]] = [
        ("ticker_news_embeddings", lambda: _query_ticker_news(args, client_opts, config, window, plan.ticker)),
        ("sec_filing_embeddings", lambda: _query_sec_tokens(args, client_opts, config, window, plan.ticker)),
        ("xbrl", lambda: _query_xbrl(args, client_opts, config, window, plan.ticker) if not args.skip_xbrl else _empty_frame()),
        ("daily_bars", lambda: _query_daily_bars(args, client_opts, config, window, symbols=(plan.ticker,))),
        ("corporate_actions", lambda: _query_corporate_actions(args, client_opts, config, window, plan.ticker) if not args.skip_corporate_actions else _empty_frame()),
    ]
    _task_update(stage="context-query", context_done=0, context_total=len(query_steps), write_done=0, write_total=len(query_steps), message="querying ticker context")
    frames: dict[str, Any] = {}
    for idx, (key, query_fn) in enumerate(query_steps, start=1):
        _task_update(stage=f"query-{key}", message=f"querying {key}")
        frames[key] = query_fn()
        _task_update(context_done=idx, message=f"{key} rows={frames[key].height:,}")
    files: dict[str, str] = {}
    counts: dict[str, int] = {}
    bytes_written = 0
    _task_update(stage="context-write", message="writing context files")
    for idx, (key, frame) in enumerate(frames.items(), start=1):
        filename = f"{key}.parquet"
        result = _write_parquet(frame, package_tmp / filename)
        files[key] = filename
        counts[key] = int(result["rows"])
        bytes_written += int(result["bytes"])
        _task_update(write_done=idx, message=f"wrote {key} rows={frame.height:,}")
    return ContextBuildResult(month=plan.month, ticker=plan.ticker, files=files, counts=counts, bytes_written=bytes_written)


def _finalize_package(
    *,
    args: argparse.Namespace,
    cache_root: Path,
    window: Any,
    plan: TickerMonthPlan,
    chunks: list[FetchChunk],
    part_results: list[PartBuildResult],
    context_result: ContextBuildResult,
    context_lags: tuple[int, ...],
) -> int:
    pl = _polars()
    package_tmp = _package_tmp_dir(cache_root, args.split, plan.month, plan.ticker)
    if not package_tmp.exists():
        package_tmp.mkdir(parents=True, exist_ok=True)
    part_results = sorted(part_results, key=lambda item: item.part_id)
    if not part_results:
        raise RuntimeError(f"{plan.month} {plan.ticker}: no event part results to finalize.")
    _task_update(
        stage="finalize-read",
        cpu_done=0,
        cpu_total=4,
        labels_done=0,
        labels_total=len(part_results),
        write_done=0,
        write_total=max(1, len(part_results) + 3),
        message=f"reading {len(part_results):,} event parts",
    )
    event_frames = [pl.read_parquet(package_tmp / item.files["events"]) for item in part_results]
    _task_update(stage="finalize-concat", cpu_done=1, message=f"concatenating {len(event_frames):,} event parts")
    events_all = pl.concat(event_frames, how="vertical").sort(["ticker", "ordinal"]).unique(subset=["ticker", "ordinal"], keep="first", maintain_order=True)
    _task_update(stage="build-bars", cpu_done=2, message=f"building bars from {events_all.height:,} events")
    intraday_bars = _build_intraday_base_bars(events_all)
    _task_update(stage="build-conditions", cpu_done=3, message=f"bars={intraday_bars.height:,}; building condition events")
    condition_events = _build_intraday_condition_events(events_all)
    _task_update(stage="write-bars", cpu_done=4, message=f"condition rows={condition_events.height:,}")
    intraday_write = _write_parquet(intraday_bars, package_tmp / "intraday_base_bars.parquet")
    _task_update(write_done=1, message=f"wrote intraday_base_bars rows={intraday_bars.height:,}")
    condition_write = _write_parquet(condition_events, package_tmp / "intraday_condition_events.parquet")
    _task_update(write_done=2, message=f"wrote condition_events rows={condition_events.height:,}")
    corporate_actions_path = package_tmp / context_result.files.get("corporate_actions", "corporate_actions.parquet")
    corporate_actions = pl.read_parquet(corporate_actions_path) if corporate_actions_path.exists() else _empty_frame()
    label_days = parse_day_horizons(args.corporate_action_label_days)
    finalized_parts: list[dict[str, Any]] = []
    total_origin_rows = 0
    total_event_rows = 0
    total_skipped_history = 0
    total_skipped_gap = 0
    for label_idx, result in enumerate(part_results, start=1):
        _task_update(stage="daily-labels", message=f"part {label_idx:,}/{len(part_results):,} corporate labels")
        origins = pl.read_parquet(package_tmp / result.files["origins"])
        labels = _build_corporate_action_daily_labels(origins, corporate_actions, label_days)
        label_file = f"corporate_action_daily_labels_part_{result.part_id:05d}.parquet"
        label_write = _write_parquet(labels, package_tmp / label_file)
        _task_update(labels_done=label_idx, write_done=2 + label_idx, message=f"wrote labels part {label_idx:,} rows={labels.height:,}")
        files = dict(result.files)
        files["corporate_action_daily_labels"] = label_file
        counts = dict(result.counts)
        counts["corporate_action_daily_labels"] = int(labels.height)
        finalized_parts.append(
            {
                "part_id": int(result.part_id),
                "origin_ordinal_start": int(chunks[result.part_id].origin_ordinal_start) if result.part_id < len(chunks) else None,
                "origin_ordinal_end": int(chunks[result.part_id].origin_ordinal_end) if result.part_id < len(chunks) else None,
                "fetch_ordinal_start": int(chunks[result.part_id].fetch_ordinal_start) if result.part_id < len(chunks) else None,
                "fetch_ordinal_end": int(chunks[result.part_id].fetch_ordinal_end) if result.part_id < len(chunks) else None,
                "files": files,
                "counts": counts,
                "skipped_not_enough_history": int(result.skipped_history),
                "skipped_window_gap": int(result.skipped_gap),
                "bytes": int(result.bytes_written + label_write["bytes"]),
            }
        )
        total_origin_rows += int(result.origin_rows)
        total_event_rows += int(result.event_rows)
        total_skipped_history += int(result.skipped_history)
        total_skipped_gap += int(result.skipped_gap)
    package_files = dict(context_result.files)
    package_files["intraday_base_bars"] = "intraday_base_bars.parquet"
    package_files["intraday_condition_events"] = "intraday_condition_events.parquet"
    package_counts = dict(context_result.counts)
    package_counts["intraday_base_bars"] = int(intraday_bars.height)
    package_counts["intraday_condition_events"] = int(condition_events.height)
    manifest = {
        "format": TICKER_MONTH_CACHE_FORMAT,
        "version": TICKER_MONTH_CACHE_VERSION,
        "status": "complete",
        "builder_mode": "index_planned_streaming_cpu_vectorized",
        "month": plan.month,
        "window": month_window_dict(window),
        "split": str(args.split),
        "ticker": plan.ticker,
        "ticker_id": int(events_all.get_column("ticker_id")[0]) if events_all.height else 0,
        "created_at": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
        "config": {
            "events_per_chunk": int(args.events_per_chunk),
            "context_lags": list(context_lags),
            "context_chunk_stride_events": int(args.context_chunk_stride_events),
            "sample_stride_events": int(args.sample_stride_events),
            "max_cached_event_lookback_rows": int(args.max_cached_event_lookback_rows),
            "context_fetch_mode": "package_level_streams_loader_asof",
            "event_payload_columns": list(EVENT_PAYLOAD_COLUMNS),
            "event_time_feature_columns": list(EVENT_TIME_FEATURE_COLUMNS),
            "context_available_time_feature_columns": list(CONTEXT_AVAILABLE_TIME_FEATURE_COLUMNS),
            "context_effective_time_feature_columns": list(CONTEXT_EFFECTIVE_TIME_FEATURE_COLUMNS),
            "bar_start_time_feature_columns": list(BAR_START_TIME_FEATURE_COLUMNS),
            "intraday_label_grid_resolutions_us": list(INTRADAY_LABEL_GRID_RESOLUTIONS_US),
        },
        "source_plan": jsonable(asdict(plan)),
        "files": package_files,
        "counts": package_counts,
        "parts": finalized_parts,
        "event_count": int(total_event_rows),
        "origin_count": int(total_origin_rows),
        "skipped_not_enough_history": int(total_skipped_history),
        "skipped_window_gap": int(total_skipped_gap),
        "byte_count": int(directory_size(package_tmp)),
    }
    _task_update(stage="manifest", message="writing package manifest")
    write_json_atomic(package_tmp / "manifest.json", manifest)
    final_dir = ticker_package_dir(month_dir_for(cache_root, args.split, plan.month), plan.ticker)
    _task_update(stage="replace", write_done=len(part_results) + 3, message="publishing package directory")
    replace_complete_dir(package_tmp, final_dir, resume=bool(args.resume))
    return int(directory_size(final_dir))


def _build_intraday_base_bars(events: Any) -> Any:
    pl = _polars()
    if events.height == 0:
        return _empty_frame()
    base = (
        events.filter((pl.col("session_second") >= SESSION_START_SECOND) & (pl.col("session_second") < SESSION_END_SECOND))
        .with_columns(
            [
                (pl.col("event_meta").cast(pl.UInt32) % 2).alias("_event_type"),
                pl.when(((pl.col("event_meta").cast(pl.UInt32) // 2) % 2) > 0).then(pl.lit(10000.0)).otherwise(pl.lit(100.0)).alias("_primary_scale"),
                pl.when(((pl.col("event_meta").cast(pl.UInt32) // 4) % 2) > 0).then(pl.lit(10000.0)).otherwise(pl.lit(100.0)).alias("_secondary_scale"),
            ]
        )
        .with_columns(
            [
                (pl.col("price_primary_int").cast(pl.Float64) / pl.col("_primary_scale")).cast(pl.Float32).alias("_price_primary"),
                (pl.col("price_secondary_int").cast(pl.Float64) / pl.col("_secondary_scale")).cast(pl.Float32).alias("_price_secondary"),
            ]
        )
    )
    trade = (
        base.filter(pl.col("_event_type") == 1)
        .select(
            [
                "ticker",
                "ticker_id",
                "local_date",
                "timestamp_us",
                "local_session_us",
                "ordinal",
                pl.lit("trade").alias("bar_family"),
                pl.col("_price_primary").alias("price"),
                pl.col("size_primary").cast(pl.Float32).alias("size"),
            ]
        )
        .filter(pl.col("price") > 0)
    )
    quote_bid = (
        base.filter(pl.col("_event_type") == 0)
        .select(
            [
                "ticker",
                "ticker_id",
                "local_date",
                "timestamp_us",
                "local_session_us",
                "ordinal",
                pl.lit("quote_bid").alias("bar_family"),
                pl.col("_price_secondary").alias("price"),
                pl.col("size_secondary").cast(pl.Float32).alias("size"),
            ]
        )
        .filter(pl.col("price") > 0)
    )
    quote_ask = (
        base.filter(pl.col("_event_type") == 0)
        .select(
            [
                "ticker",
                "ticker_id",
                "local_date",
                "timestamp_us",
                "local_session_us",
                "ordinal",
                pl.lit("quote_ask").alias("bar_family"),
                pl.col("_price_primary").alias("price"),
                pl.col("size_primary").cast(pl.Float32).alias("size"),
            ]
        )
        .filter(pl.col("price") > 0)
    )
    stacked = pl.concat([trade, quote_bid, quote_ask], how="vertical")
    if stacked.height == 0:
        return _empty_frame()
    frames = []
    for resolution_us in INTRADAY_LABEL_GRID_RESOLUTIONS_US:
        frame = (
            stacked.with_columns(
                [
                    pl.lit(int(resolution_us)).cast(pl.Int64).alias("label_resolution_us"),
                    (pl.col("local_session_us").cast(pl.Int64) // int(resolution_us)).cast(pl.Int64).alias("bucket_index"),
                ]
            )
            .filter((pl.col("bucket_index") >= int(SESSION_START_SECOND * 1_000_000 // resolution_us)) & (pl.col("bucket_index") < int(SESSION_END_SECOND * 1_000_000 // resolution_us)))
            .sort(["ticker", "local_date", "label_resolution_us", "bucket_index", "bar_family", "timestamp_us", "ordinal"])
            .group_by(["ticker", "ticker_id", "local_date", "label_resolution_us", "bucket_index", "bar_family"], maintain_order=True)
            .agg(
                [
                    pl.col("price").first().cast(pl.Float32).alias("open"),
                    pl.col("price").last().cast(pl.Float32).alias("close"),
                    pl.col("price").max().cast(pl.Float32).alias("high"),
                    pl.col("price").min().cast(pl.Float32).alias("low"),
                    pl.col("size").sum().cast(pl.Float32).alias("size_sum"),
                    pl.col("size").first().cast(pl.Float32).alias("size_open"),
                    pl.col("size").last().cast(pl.Float32).alias("size_close"),
                    pl.col("size").max().cast(pl.Float32).alias("size_high"),
                    pl.col("size").min().cast(pl.Float32).alias("size_low"),
                    pl.len().cast(pl.UInt32).alias("event_count"),
                    pl.col("timestamp_us").first().cast(pl.Int64).alias("first_event_timestamp_us"),
                    pl.col("timestamp_us").last().cast(pl.Int64).alias("last_event_timestamp_us"),
                ]
            )
            .with_columns(
                [
                    (pl.col("bucket_index") * int(resolution_us)).cast(pl.Int64).alias("bar_start_session_us"),
                    ((pl.col("bucket_index") + 1) * int(resolution_us)).cast(pl.Int64).alias("bar_end_session_us"),
                ]
            )
        )
        frames.append(frame)
    return pl.concat(frames, how="vertical").sort(["ticker", "local_date", "label_resolution_us", "bucket_index", "bar_family"])

def _build_intraday_condition_events(events: Any) -> Any:
    pl = _polars()
    if events.height == 0:
        return _empty_frame()
    token_columns = [f"condition_token_{idx}" for idx in range(1, 6)]
    out = events.select(["ticker", "ticker_id", "ordinal", "timestamp_us", "local_date", "local_session_us", *token_columns])
    flag_exprs = []
    for name, groups in FUTURE_CONDITION_GROUPS:
        tokens = sorted({int(token) for _family, values in groups for token in values})
        checks = [pl.col(column).is_in(tokens) for column in token_columns]
        flag_exprs.append(pl.any_horizontal(checks).cast(pl.UInt8).alias(name))
    out = out.with_columns(flag_exprs)
    flags = [name for name, _groups in FUTURE_CONDITION_GROUPS]
    return out.filter(pl.any_horizontal([pl.col(flag) > 0 for flag in flags])).select(["ticker", "ticker_id", "ordinal", "timestamp_us", "local_date", "local_session_us", *flags])


def _write_global_package(
    *,
    args: argparse.Namespace,
    client_opts: Mapping[str, str],
    config: Any,
    cache_root: Path,
    month: str,
    window: Any,
    stats: BuildStats,
    lanes: StreamingLaneExecutors,
) -> None:
    final_dir = month_dir_for(cache_root, args.split, month) / "global"
    if final_dir.exists() and not args.resume:
        stats.message(f"{month}: global package exists; keeping existing")
        with stats.lock:
            stats.packages_done += 1
        return
    package_tmp = final_dir.with_name("global.tmp")
    if package_tmp.exists():
        shutil.rmtree(package_tmp)
    package_tmp.mkdir(parents=True, exist_ok=True)
    market_news_future = lanes.submit("context", f"{month}:__MARKET__:news", lambda: _query_market_news(args, client_opts, config, window))
    daily_future = lanes.submit("context", f"{month}:__MARKET__:daily_bars", lambda: _query_daily_bars(args, client_opts, config, window, symbols=("SPY", "QQQ", "IWM", "DIA", "VIX")))
    refs_future = lanes.submit("context", f"{month}:__MARKET__:category_refs", lambda: _query_category_references(args, client_opts, config))
    files: dict[str, str] = {}
    counts: dict[str, int] = {}
    bytes_written = 0
    for key, future in (("market_news_embeddings", market_news_future), ("global_daily_bars", daily_future), ("category_references", refs_future)):
        frame = future.result()
        filename = f"{key}.parquet"
        result = _write_parquet(frame, package_tmp / filename)
        files[key] = filename
        counts[key] = int(result["rows"])
        bytes_written += int(result["bytes"])
    manifest = {
        "format": TICKER_MONTH_CACHE_FORMAT,
        "version": TICKER_MONTH_CACHE_VERSION,
        "status": "complete",
        "builder_mode": "index_planned_streaming_cpu_vectorized",
        "month": month,
        "split": str(args.split),
        "ticker": "__MARKET__",
        "ticker_id": 0,
        "created_at": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
        "window": month_window_dict(window),
        "files": files,
        "counts": counts,
        "parts": [],
        "byte_count": bytes_written,
    }
    write_json_atomic(package_tmp / "manifest.json", manifest)
    replace_complete_dir(package_tmp, final_dir, resume=bool(args.resume))
    with stats.lock:
        stats.packages_done += 1
        stats.bytes_written += int(directory_size(final_dir))


def _package_tmp_dir(cache_root: Path, split: str, month: str, ticker: str) -> Path:
    return ticker_package_dir(month_dir_for(cache_root, split, month), ticker).with_name(f"ticker={ticker.upper()}.tmp")


def _record_part_stats(stats: BuildStats, result: PartBuildResult) -> None:
    with stats.lock:
        stats.events_written += int(result.event_rows)
        stats.origins_written += int(result.origin_rows)
        stats.bytes_written += int(result.bytes_written)


if __name__ == "__main__":
    raise SystemExit(main())
