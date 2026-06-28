from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import signal
import shutil
import sys
import threading
import time
import traceback
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from urllib import request as url_request
from urllib.parse import urlparse

import numpy as np

if __package__ in {None, ""}:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "research").is_dir():
            sys.path.insert(0, str(parent))
            break

from research.mlops.clickhouse import (
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    discover_clickhouse_env_files,
    parse_size_bytes,
    quote_ident,
    sql_string,
)
from research.mlops.data.config import RollingMarketDataConfig, TimeBarHorizon
from research.mlops.env import load_env_files
from research.mlops.rolling_loader.streaming_training import NEWS_TOKEN_COLUMNS, SEC_TOKEN_COLUMNS, current_rss_mib, date_time64_from_us
from research.mlops.rolling_loader.ticker_month_cache import (
    DEFAULT_TICKER_MONTH_CACHE_ROOT,
    EVENT_PAYLOAD_COLUMNS,
    EVENT_TIME_FEATURE_COLUMNS,
    SESSION_TIMEZONE,
    TICKER_MONTH_CACHE_FORMAT,
    TICKER_MONTH_CACHE_VERSION,
    TickerMonthResult,
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
    parse_horizons,
    parse_lags,
    replace_complete_dir,
    required_event_lookback_rows,
    ticker_package_dir,
    timestamp_us_to_utc,
    write_json_atomic,
)


DEFAULTS: dict[str, Any] = {
    "database": "market_sip_compact",
    "sec_context_database": "market_sip_compact",
    "events_table": "events",
    "macro_bars_table": "macro_bars_by_time_symbol",
    "news_token_table": "news_text_tokens",
    "sec_filing_text_token_table": "sec_filing_text_tokens",
    "sec_xbrl_context_table": "sec_xbrl_context",
    "category_reference_table": "training_category_reference",
    "cache_root": str(DEFAULT_TICKER_MONTH_CACHE_ROOT),
    "split": "train",
    "workers": 8,
    "event_fetch_workers": 2,
    "context_fetch_workers": 4,
    "label_fetch_workers": 2,
    "cpu_workers": 2,
    "write_workers": 4,
    "audit_workers": 1,
    "max_inflight_packages": 16,
    "max_origin_events_per_part": 2_000_000,
    "events_per_chunk": 128,
    "short_context_chunks": 32,
    "context_chunk_stride_events": 64,
    "short_context_stride_chunks": 1,
    "long_context_lags": "",
    "sample_stride_events": 1,
    "max_threads": 8,
    "max_memory_usage": "80G",
    "macro_lookback_days": 400,
    "label_lookahead_days": 400,
    "news_lookback_days": 30,
    "sec_lookback_days": 365,
    "xbrl_lookback_days": 730,
    "ticker_news_items": 8,
    "market_news_items": 16,
    "sec_filing_items": 4,
    "xbrl_items": 512,
    "intraday_label_horizons": "100ms,250ms,500ms,750ms,1s,5s,10s,30s,60s,120s,180s,300s,600s,1200s,1800s,3600s,7200s,3h,4h,5h",
    "refresh_seconds": 1.0,
    "profile_slow_seconds": 10.0,
}


class ActiveQueryRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queries: dict[str, dict[str, Any]] = {}

    def register(self, query_id: str, *, label: str = "") -> None:
        with self._lock:
            self._queries[str(query_id)] = {
                "label": str(label),
                "started_at": time.time(),
                "thread_id": threading.get_ident(),
            }

    def unregister(self, query_id: str) -> None:
        with self._lock:
            self._queries.pop(str(query_id), None)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            now = time.time()
            out: dict[str, dict[str, Any]] = {}
            for key, value in self._queries.items():
                row = dict(value)
                row["seconds"] = max(0.0, now - float(row.get("started_at") or now))
                out[key] = row
            return out

    def clear(self) -> None:
        with self._lock:
            self._queries.clear()


ACTIVE_QUERIES = ActiveQueryRegistry()


class TeeStream:
    def __init__(self, primary: Any, log_handle: Any) -> None:
        self.primary = primary
        self.log_handle = log_handle

    def write(self, text: str) -> int:
        self.primary.write(text)
        self.log_handle.write(text)
        return len(text)

    def flush(self) -> None:
        self.primary.flush()
        self.log_handle.flush()

    def isatty(self) -> bool:
        return bool(getattr(self.primary, "isatty", lambda: False)())


@dataclass(slots=True)
class LaneState:
    name: str
    workers: int
    queued: int = 0
    running: int = 0
    done: int = 0
    failed: int = 0
    rows: int = 0
    bytes: int = 0
    seconds: float = 0.0
    active: dict[int, str] = field(default_factory=dict)


@dataclass(slots=True)
class PackageState:
    worker_id: int
    month: str = ""
    ticker: str = ""
    status: str = "idle"
    stage: str = ""
    events_done: int = 0
    events_total: int = 1
    labels_done: int = 0
    labels_total: int = 1
    context_done: int = 0
    context_total: int = 1
    cpu_done: int = 0
    cpu_total: int = 1
    write_done: int = 0
    write_total: int = 1
    started_at: float = 0.0
    seconds: float = 0.0
    message: str = ""


@dataclass(frozen=True, slots=True)
class OriginOrdinalPart:
    part_id: int
    origin_ordinal_start: int
    origin_ordinal_end: int
    fetch_ordinal_start: int
    fetch_ordinal_end: int


@dataclass(slots=True)
class BuildStats:
    started: float = field(default_factory=time.perf_counter)
    phase: str = "starting"
    split: str = "train"
    months_total: int = 0
    months_done: int = 0
    packages_total: int = 0
    packages_done: int = 0
    packages_failed: int = 0
    events_written: int = 0
    origins_written: int = 0
    labels_written: int = 0
    bytes_written: int = 0
    current_rss_mib: float = 0.0
    max_rss_mib: float = 0.0
    stop_requested: bool = False
    interrupted: bool = False
    log_path: Path | None = None
    progress_path: Path | None = None
    profile_path: Path | None = None
    errors_path: Path | None = None
    messages: list[str] = field(default_factory=list)
    workers: dict[int, PackageState] = field(default_factory=dict)
    lanes: dict[str, LaneState] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def message(self, text: str, **fields: Any) -> None:
        stamp = dt.datetime.now().strftime("%H:%M:%S")
        with self.lock:
            self.messages.append(f"{stamp} {text}")
            self.messages = self.messages[-10:]
        self.log_event("message", message=text, **fields)

    def log_event(self, event: str, **fields: Any) -> None:
        if self.log_path is None:
            return
        payload = {
            "timestamp": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
            "event": event,
            "phase": self.phase,
            **jsonable(fields),
        }
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")

    def profile_event(self, stage: str, **fields: Any) -> None:
        if self.profile_path is None:
            return
        payload = {
            "timestamp": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
            "stage": stage,
            **jsonable(fields),
        }
        with self.profile_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")

    def log_error(self, where: str, exc: BaseException) -> None:
        text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        if self.errors_path is not None:
            with self.errors_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"timestamp": dt.datetime.now(tz=dt.timezone.utc).isoformat(), "where": where, "error": repr(exc), "traceback": text}, sort_keys=True) + "\n")
        self.log_event("error", where=where, error=repr(exc))


class LaneExecutors:
    def __init__(self, args: argparse.Namespace, stats: BuildStats) -> None:
        self.stats = stats
        self.executors: dict[str, ThreadPoolExecutor] = {
            "event": ThreadPoolExecutor(max_workers=max(1, int(args.event_fetch_workers)), thread_name_prefix="tmc-event"),
            "context": ThreadPoolExecutor(max_workers=max(1, int(args.context_fetch_workers)), thread_name_prefix="tmc-context"),
            "label": ThreadPoolExecutor(max_workers=max(1, int(args.label_fetch_workers)), thread_name_prefix="tmc-label"),
            "cpu": ThreadPoolExecutor(max_workers=max(1, int(args.cpu_workers)), thread_name_prefix="tmc-cpu"),
            "write": ThreadPoolExecutor(max_workers=max(1, int(args.write_workers)), thread_name_prefix="tmc-write"),
            "audit": ThreadPoolExecutor(max_workers=max(1, int(args.audit_workers)), thread_name_prefix="tmc-audit"),
        }
        for name, executor in self.executors.items():
            stats.lanes[name] = LaneState(name=name, workers=int(executor._max_workers))  # type: ignore[attr-defined]

    def submit(self, lane: str, label: str, fn: Callable[[], Any]) -> Future[Any]:
        state = self.stats.lanes[lane]
        with self.stats.lock:
            state.queued += 1

        def wrapped() -> Any:
            thread_id = threading.get_ident()
            started = time.perf_counter()
            with self.stats.lock:
                state.queued = max(0, state.queued - 1)
                state.running += 1
                state.active[thread_id] = label
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
                self.stats.profile_event(f"{lane}_error", label=label, seconds=elapsed, error=repr(exc))
                raise
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
            self.stats.profile_event(f"{lane}_done", label=label, seconds=elapsed, rows=rows, bytes=bytes_count)
            return result

        return self.executors[lane].submit(wrapped)

    def shutdown(self, *, wait_for_running: bool) -> None:
        for executor in self.executors.values():
            executor.shutdown(wait=wait_for_running, cancel_futures=True)


class ProgressHeartbeat:
    def __init__(self, *, cache_root: Path, stats: BuildStats, dashboard: "TickerMonthDashboard", refresh_seconds: float) -> None:
        self.cache_root = Path(cache_root)
        self.stats = stats
        self.dashboard = dashboard
        self.refresh_seconds = max(0.25, float(refresh_seconds))
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="ticker-month-progress-heartbeat", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=max(1.0, self.refresh_seconds * 2.0))
        _refresh(self.stats, self.dashboard, self.cache_root, force=True)

    def _run(self) -> None:
        while not self._stop.wait(self.refresh_seconds):
            try:
                _refresh(self.stats, self.dashboard, self.cache_root)
            except Exception as exc:
                self.stats.log_event("progress_heartbeat_error", error=repr(exc))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build query-driven ticker/month SSD rolling cache packages.")
    parser.add_argument("--clickhouse-url", default="")
    parser.add_argument("--user", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--env-file", type=Path, default=None)
    parser.add_argument("--database", default=DEFAULTS["database"])
    parser.add_argument("--sec-context-database", default=DEFAULTS["sec_context_database"])
    parser.add_argument("--events-table", default=DEFAULTS["events_table"])
    parser.add_argument("--macro-bars-table", default=DEFAULTS["macro_bars_table"])
    parser.add_argument("--news-token-table", default=DEFAULTS["news_token_table"])
    parser.add_argument("--sec-filing-text-token-table", default=DEFAULTS["sec_filing_text_token_table"])
    parser.add_argument("--sec-xbrl-context-table", default=DEFAULTS["sec_xbrl_context_table"])
    parser.add_argument("--category-reference-table", default=DEFAULTS["category_reference_table"])
    parser.add_argument("--cache-root", type=Path, default=Path(DEFAULTS["cache_root"]))
    parser.add_argument("--cache-id", default="")
    parser.add_argument("--split", default=DEFAULTS["split"])
    parser.add_argument("--month", default="", help="Single full month to build, YYYY-MM.")
    parser.add_argument("--start-utc", default="", help="Inclusive period start. Only full months inside the period are built.")
    parser.add_argument("--end-utc", default="", help="Exclusive period end. Only full months inside the period are built.")
    parser.add_argument("--tickers", default="", help="Optional comma-separated tickers for test builds.")
    parser.add_argument("--ticker-limit", type=int, default=0, help="Optional first-N ticker cap for test builds; 0 means all tickers.")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--workers", type=int, default=DEFAULTS["workers"], help="Package coordinator workers.")
    parser.add_argument("--event-fetch-workers", type=int, default=DEFAULTS["event_fetch_workers"])
    parser.add_argument("--context-fetch-workers", type=int, default=DEFAULTS["context_fetch_workers"])
    parser.add_argument("--label-fetch-workers", type=int, default=DEFAULTS["label_fetch_workers"])
    parser.add_argument("--cpu-workers", type=int, default=DEFAULTS["cpu_workers"])
    parser.add_argument("--write-workers", type=int, default=DEFAULTS["write_workers"])
    parser.add_argument("--audit-workers", type=int, default=DEFAULTS["audit_workers"])
    parser.add_argument("--max-inflight-packages", type=int, default=DEFAULTS["max_inflight_packages"])
    parser.add_argument("--max-origin-events-per-part", type=int, default=DEFAULTS["max_origin_events_per_part"], help="Maximum origin ordinal span per physical part inside a ticker-month package.")
    parser.add_argument("--events-per-chunk", type=int, default=DEFAULTS["events_per_chunk"])
    parser.add_argument("--short-context-chunks", type=int, default=DEFAULTS["short_context_chunks"])
    parser.add_argument("--context-chunk-stride-events", type=int, default=DEFAULTS["context_chunk_stride_events"])
    parser.add_argument("--short-context-stride-chunks", type=int, default=DEFAULTS["short_context_stride_chunks"])
    parser.add_argument("--long-context-lags", default=DEFAULTS["long_context_lags"])
    parser.add_argument("--sample-stride-events", type=int, default=DEFAULTS["sample_stride_events"])
    parser.add_argument("--max-threads", type=int, default=DEFAULTS["max_threads"])
    parser.add_argument("--max-memory-usage", default=DEFAULTS["max_memory_usage"])
    parser.add_argument("--macro-lookback-days", type=int, default=DEFAULTS["macro_lookback_days"])
    parser.add_argument("--label-lookahead-days", type=int, default=DEFAULTS["label_lookahead_days"])
    parser.add_argument("--news-lookback-days", type=int, default=DEFAULTS["news_lookback_days"])
    parser.add_argument("--sec-lookback-days", type=int, default=DEFAULTS["sec_lookback_days"])
    parser.add_argument("--xbrl-lookback-days", type=int, default=DEFAULTS["xbrl_lookback_days"])
    parser.add_argument("--ticker-news-items", type=int, default=DEFAULTS["ticker_news_items"])
    parser.add_argument("--market-news-items", type=int, default=DEFAULTS["market_news_items"])
    parser.add_argument("--sec-filing-items", type=int, default=DEFAULTS["sec_filing_items"])
    parser.add_argument("--xbrl-items", type=int, default=DEFAULTS["xbrl_items"])
    parser.add_argument("--intraday-label-horizons", default=DEFAULTS["intraday_label_horizons"])
    parser.add_argument("--skip-token-contexts", action="store_true")
    parser.add_argument("--skip-xbrl", action="store_true")
    parser.add_argument("--skip-final-audit", action="store_true")
    parser.add_argument("--audit-source-checks", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--audit-samples-per-month", type=int, default=8)
    parser.add_argument("--refresh-seconds", type=float, default=DEFAULTS["refresh_seconds"])
    parser.add_argument("--profile-slow-seconds", type=float, default=DEFAULTS["profile_slow_seconds"])
    parser.add_argument("--no-rich", action="store_true")
    parser.add_argument("--plain-status", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    env_files = discover_clickhouse_env_files()
    if args.env_file is not None:
        env_files.append(args.env_file)
    loaded_env = load_env_files(env_files, verbose=False)

    months = _resolve_months(args)
    if not months:
        raise SystemExit("No full months selected. Use --month YYYY-MM or a period containing at least one complete month.")
    cache_id = args.cache_id or f"{args.split}_{months[0].replace('-', '')}_{months[-1].replace('-', '')}_ticker_month"
    cache_root = Path(args.cache_root) / cache_id
    cache_root.mkdir(parents=True, exist_ok=True)
    log_handle = (cache_root / "terminal.log").open("a", encoding="utf-8")
    original_stdout, original_stderr = sys.stdout, sys.stderr
    sys.stdout = TeeStream(sys.stdout, log_handle)  # type: ignore[assignment]
    sys.stderr = TeeStream(sys.stderr, log_handle)  # type: ignore[assignment]
    stats = BuildStats(
        split=str(args.split),
        months_total=len(months),
        log_path=cache_root / "builder_events.jsonl",
        profile_path=cache_root / "builder_profile_events.jsonl",
        errors_path=cache_root / "errors.jsonl",
        progress_path=cache_root / f"{args.split}_progress.json",
    )
    dashboard: TickerMonthDashboard | None = None
    heartbeat: ProgressHeartbeat | None = None
    lanes: LaneExecutors | None = None
    package_executor: ThreadPoolExecutor | None = None
    previous_sigint = signal.getsignal(signal.SIGINT)
    stop_event = threading.Event()
    manifest: dict[str, Any] = {}
    client_opts = _client_options(args)
    try:
        if loaded_env:
            print("Loaded .env files: " + ", ".join(str(path) for path in loaded_env), flush=True)
        cleanup_tmp_dirs(cache_root)
        context_lags = context_lags_from_args(
            events_per_chunk=args.events_per_chunk,
            short_context_chunks=args.short_context_chunks,
            context_chunk_stride_events=args.context_chunk_stride_events,
            short_context_stride_chunks=args.short_context_stride_chunks,
            long_context_lags=parse_lags(args.long_context_lags),
        )
        config = build_config_from_args(args)
        manifest = month_manifest_payload(args=args, cache_id=cache_id, cache_root=cache_root, loaded_env=loaded_env, months=months, context_lags=context_lags)
        write_json_atomic(cache_root / "manifest.json", manifest)
        stats.message(f"cache_root={cache_root}")
        stats.message(f"months={','.join(months)}")
        stats.workers = {idx: PackageState(worker_id=idx) for idx in range(max(1, int(args.workers)))}
        lanes = LaneExecutors(args, stats)
        dashboard = TickerMonthDashboard(enabled=not args.no_rich, live=not args.plain_status, refresh_seconds=args.refresh_seconds, stats=stats)

        def request_stop(signum: int, _frame: Any) -> None:
            stop_event.set()
            stats.stop_requested = True
            stats.interrupted = True
            stats.phase = "stopping"
            stats.message("stop requested; cancelling active ClickHouse queries and queued work")
            cancel_active_clickhouse_queries(client_opts=client_opts, stats=stats)
            raise KeyboardInterrupt

        signal.signal(signal.SIGINT, request_stop)
        dashboard.start()
        heartbeat = ProgressHeartbeat(cache_root=cache_root, stats=stats, dashboard=dashboard, refresh_seconds=args.refresh_seconds)
        heartbeat.start()
        for month in months:
            if stop_event.is_set():
                break
            stats.phase = f"month {month}"
            month_dir = month_dir_for(cache_root, args.split, month)
            month_dir.mkdir(parents=True, exist_ok=True)
            window = month_window(month)
            month_tickers = _resolve_tickers_for_month(args, client_opts, config, window)
            stats.packages_total += len(month_tickers)
            stats.message(f"{month}: tickers={len(month_tickers):,}")
            _write_global_month_package(args=args, client_opts=client_opts, config=config, cache_root=cache_root, month=month, window=window, lanes=lanes, stats=stats, stop_event=stop_event)
            package_executor = ThreadPoolExecutor(max_workers=max(1, int(args.workers)), thread_name_prefix="tmc-package")
            pending: dict[Future[TickerMonthResult], int] = {}
            ticker_iter = iter(month_tickers)
            next_worker = 0

            def submit_more() -> None:
                nonlocal next_worker
                while not stop_event.is_set() and len(pending) < max(1, int(args.max_inflight_packages)):
                    try:
                        ticker = next(ticker_iter)
                    except StopIteration:
                        return
                    worker_id = next_worker % max(1, int(args.workers))
                    next_worker += 1
                    future = package_executor.submit(
                        _build_ticker_month_package,
                        args,
                        client_opts,
                        config,
                        context_lags,
                        cache_root,
                        month,
                        window,
                        ticker,
                        worker_id,
                        stats,
                        lanes,
                        stop_event,
                    )
                    pending[future] = worker_id

            submit_more()
            while pending:
                if stop_event.is_set():
                    raise KeyboardInterrupt
                done, _ = wait(tuple(pending), timeout=0.5, return_when=FIRST_COMPLETED)
                for future in done:
                    worker_id = pending.pop(future)
                    try:
                        result = future.result()
                    except BaseException as exc:
                        stats.packages_failed += 1
                        stats.log_error(f"package_worker_{worker_id}", exc)
                        raise
                    manifest.setdefault("packages", []).append(_result_manifest(result))
                    stats.packages_done += 1
                    stats.events_written += int(result.event_count)
                    stats.origins_written += int(result.origin_count)
                    stats.labels_written += int(result.label_rows)
                    stats.bytes_written += int(result.byte_count)
                    _write_progress(cache_root, stats)
                    if stats.packages_done % 25 == 0:
                        write_json_atomic(cache_root / "manifest.json", manifest | {"status": "running"})
                submit_more()
                _refresh(stats, dashboard, cache_root)
            package_executor.shutdown(wait=True, cancel_futures=False)
            package_executor = None
            stats.months_done += 1
            write_json_atomic(month_dir / "month_manifest.json", _month_summary(month=month, window=window, tickers=month_tickers, stats=stats))
            write_json_atomic(cache_root / "manifest.json", manifest | {"status": "running"})
        if stop_event.is_set():
            raise KeyboardInterrupt
        stats.phase = "auditing"
        if not args.skip_final_audit:
            from research.mlops.rolling_loader.audit_ticker_month_cache import TickerMonthAuditConfig, run_audit

            audit = run_audit(
                TickerMonthAuditConfig(
                    cache_root=cache_root,
                    split=args.split,
                    sample_packages_per_month=max(0, int(args.audit_samples_per_month)),
                    source_checks=bool(args.audit_source_checks),
                    clickhouse_url=client_opts["clickhouse_url"],
                    clickhouse_user=client_opts["user"],
                    clickhouse_password=client_opts["password"],
                    database=args.database,
                    events_table=args.events_table,
                    max_threads=max(1, int(args.max_threads)),
                    max_memory_usage=str(args.max_memory_usage),
                )
            )
            manifest["audit"] = {"ok": audit.ok, "status": audit.status, "summary": audit.summary, "report_path": str(audit.report_path)}
            if not audit.ok:
                manifest["status"] = "audit_failed"
                write_json_atomic(cache_root / "manifest.json", manifest)
                raise RuntimeError(f"Final ticker/month cache audit failed; see {audit.report_path}")
        manifest["status"] = "complete"
        manifest["completed_at"] = dt.datetime.now(tz=dt.timezone.utc).isoformat()
        manifest["summary"] = _summary(stats)
        write_json_atomic(cache_root / "manifest.json", manifest)
        stats.phase = "complete"
        stats.message("ticker/month cache build complete")
        _refresh(stats, dashboard, cache_root, force=True)
        return 0
    except KeyboardInterrupt:
        stop_event.set()
        stats.interrupted = True
        stats.stop_requested = True
        stats.phase = "interrupted"
        cancel_active_clickhouse_queries(client_opts=client_opts, stats=stats)
        stats.message("interrupted by user; active ClickHouse queries were cancelled; completed package directories remain usable")
        if manifest:
            write_json_atomic(cache_root / "manifest.json", manifest | {"status": "interrupted", "summary": _summary(stats)})
        return 130
    except BaseException as exc:
        stats.phase = "error"
        stats.log_error("main", exc)
        if manifest:
            write_json_atomic(cache_root / "manifest.json", manifest | {"status": "error", "error": repr(exc), "summary": _summary(stats)})
        raise
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        if stats.interrupted or stats.stop_requested:
            cancel_active_clickhouse_queries(client_opts=client_opts, stats=stats)
        if package_executor is not None:
            package_executor.shutdown(wait=False, cancel_futures=True)
        if lanes is not None:
            lanes.shutdown(wait_for_running=not (stats.interrupted or stats.stop_requested))
        if heartbeat is not None:
            heartbeat.stop()
        else:
            _write_progress(cache_root, stats)
        if dashboard is not None:
            dashboard.stop()
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        log_handle.close()


def _resolve_months(args: argparse.Namespace) -> tuple[str, ...]:
    if args.month:
        return (month_window(args.month).month,)
    if not args.start_utc or not args.end_utc:
        raise SystemExit("Pass either --month YYYY-MM or both --start-utc and --end-utc.")
    return full_months_in_period(args.start_utc, args.end_utc)


def _client_options(args: argparse.Namespace) -> dict[str, str]:
    return {
        "clickhouse_url": args.clickhouse_url or default_clickhouse_url(),
        "user": args.user or default_clickhouse_user(),
        "password": args.password or default_clickhouse_password(),
    }


def _resolve_tickers_for_month(args: argparse.Namespace, client_opts: Mapping[str, str], config: RollingMarketDataConfig, window: Any) -> list[str]:
    if args.tickers:
        tickers = sorted({item.strip().upper() for item in args.tickers.split(",") if item.strip()})
        return tickers[: int(args.ticker_limit)] if int(args.ticker_limit) > 0 else tickers
    table = f"{quote_ident(config.database)}.{quote_ident(config.events_table)}"
    query = f"""
WITH
    fromUnixTimestamp64Micro(sip_timestamp_us, 'UTC') AS ts_utc,
    toTimeZone(ts_utc, {sql_string(SESSION_TIMEZONE)}) AS ts_local,
    dateDiff('second', toStartOfDay(ts_local), ts_local) AS local_second
SELECT DISTINCT upper(ticker) AS ticker
FROM {table}
PREWHERE event_date >= toDate({sql_string(window.first_date.isoformat())})
  AND event_date <= toDate({sql_string(window.next_month_date.isoformat())})
WHERE sip_timestamp_us >= {int(window.first_session_start_us)}
  AND sip_timestamp_us < {int(window.last_session_end_us)}
  AND toDate(ts_local) >= toDate({sql_string(window.first_date.isoformat())})
  AND toDate(ts_local) < toDate({sql_string(window.next_month_date.isoformat())})
  AND local_second >= 14400
  AND local_second < 72000
ORDER BY ticker
{_settings_sql(config)}
"""
    frame = query_polars(client_opts, query)
    tickers = [str(value).upper() for value in frame.get_column("ticker").to_list()] if frame.height else []
    return tickers[: int(args.ticker_limit)] if int(args.ticker_limit) > 0 else tickers


def _write_global_month_package(
    *,
    args: argparse.Namespace,
    client_opts: Mapping[str, str],
    config: RollingMarketDataConfig,
    cache_root: Path,
    month: str,
    window: Any,
    lanes: LaneExecutors,
    stats: BuildStats,
    stop_event: threading.Event,
) -> None:
    month_dir = month_dir_for(cache_root, args.split, month)
    final_dir = month_dir / "global"
    if final_dir.exists() and not args.resume:
        stats.message(f"{month}: global package exists; keeping existing")
        return
    tmp_dir = final_dir.with_name("global.tmp")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    futures = {
        "market_news": lanes.submit("context", f"{month}:global_market_news", lambda: _query_market_news(args, client_opts, config, window)),
        "global_bars": lanes.submit("context", f"{month}:global_daily_bars", lambda: _query_daily_bars(args, client_opts, config, window, symbols=tuple(config.global_symbols))),
        "categories": lanes.submit("context", f"{month}:categories", lambda: _query_category_references(args, client_opts, config)),
    }
    outputs = {name: future.result() for name, future in futures.items()}
    if stop_event.is_set():
        raise KeyboardInterrupt
    writes = [
        lanes.submit("write", f"{month}:write_market_news", lambda frame=outputs["market_news"]: _write_parquet(frame, tmp_dir / "market_news_tokens.parquet")),
        lanes.submit("write", f"{month}:write_global_bars", lambda frame=outputs["global_bars"]: _write_parquet(frame, tmp_dir / "global_daily_bars.parquet")),
        lanes.submit("write", f"{month}:write_categories", lambda frame=outputs["categories"]: _write_parquet(frame, tmp_dir / "category_references.parquet")),
    ]
    for future in writes:
        future.result()
    write_json_atomic(
        tmp_dir / "manifest.json",
        {
            "format": TICKER_MONTH_CACHE_FORMAT,
            "version": TICKER_MONTH_CACHE_VERSION,
            "status": "complete",
            "month": month,
            "window": month_window_dict(window),
            "files": {
                "market_news_tokens": "market_news_tokens.parquet",
                "global_daily_bars": "global_daily_bars.parquet",
                "category_references": "category_references.parquet",
            },
            "counts": {key: int(value.height) for key, value in outputs.items()},
            "completed_at": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
        },
    )
    replace_complete_dir(tmp_dir, final_dir, resume=True)


def _build_ticker_month_package(
    args: argparse.Namespace,
    client_opts: Mapping[str, str],
    config: RollingMarketDataConfig,
    context_lags: tuple[int, ...],
    cache_root: Path,
    month: str,
    window: Any,
    ticker: str,
    worker_id: int,
    stats: BuildStats,
    lanes: LaneExecutors,
    stop_event: threading.Event,
) -> TickerMonthResult:
    state = stats.workers[worker_id]
    state.month = month
    state.ticker = ticker
    state.status = "running"
    state.stage = "query"
    state.started_at = time.perf_counter()
    state.message = "submitting package tasks"
    package_dir = ticker_package_dir(month_dir_for(cache_root, args.split, month), ticker)
    if package_dir.exists() and not args.resume:
        state.status = "done"
        state.stage = "exists"
        state.message = "already exists"
        return TickerMonthResult(month=month, ticker=ticker, package_dir=package_dir, status="exists", byte_count=directory_size(package_dir))
    tmp_dir = package_dir.with_name(package_dir.name + ".tmp")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    try:
        required_lookback = required_event_lookback_rows(context_lags, args.events_per_chunk)
        origin_bounds = _query_origin_bounds(args, client_opts, config, window, ticker)
        parts = _origin_ordinal_parts(origin_bounds, required_lookback=required_lookback, max_origin_events_per_part=args.max_origin_events_per_part)
        futures: dict[str, Future[Any]] = {
            "ticker_news": lanes.submit("context", f"{month}:{ticker}:ticker_news", lambda: _query_ticker_news(args, client_opts, config, window, ticker)),
            "sec_filings": lanes.submit("context", f"{month}:{ticker}:sec", lambda: _query_sec_tokens(args, client_opts, config, window, ticker)),
            "daily_bars": lanes.submit("context", f"{month}:{ticker}:daily", lambda: _query_daily_bars(args, client_opts, config, window, symbols=(ticker,))),
        }
        if not args.skip_xbrl:
            futures["xbrl"] = lanes.submit("context", f"{month}:{ticker}:xbrl", lambda: _query_xbrl(args, client_opts, config, window, ticker))
        else:
            futures["xbrl"] = lanes.submit("context", f"{month}:{ticker}:xbrl_empty", lambda: _empty_frame())
        state.context_total = 4 if not args.skip_xbrl else 3
        state.events_total = max(1, len(parts))
        state.labels_total = max(1, len(parts))
        state.cpu_total = max(1, len(parts))
        state.write_total = max(1, len(parts) * 5 + state.context_total)
        total_events = 0
        total_origins = 0
        total_windows = 0
        total_labels = 0
        skipped_history = 0
        skipped_gap = 0
        part_manifests: list[dict[str, Any]] = []
        month_min_ordinal = int(origin_bounds[0]) if origin_bounds else 0
        for part in parts:
            if stop_event.is_set():
                raise KeyboardInterrupt
            part_name = f"part_{part.part_id:05d}"
            state.stage = f"query {part.part_id + 1}/{len(parts)}"
            state.message = f"{part_name} ordinals {part.origin_ordinal_start:,}-{part.origin_ordinal_end:,}"
            events_future = lanes.submit(
                "event",
                f"{month}:{ticker}:{part_name}:events",
                lambda part=part: _query_events_part(args, client_opts, config, window, ticker, part),
            )
            labels_future = lanes.submit(
                "label",
                f"{month}:{ticker}:{part_name}:intraday_labels",
                lambda part=part: _query_intraday_forward_labels(args, client_opts, config, window, ticker, part, month_min_ordinal),
            )
            events = events_future.result()
            state.events_done += 1
            state.stage = f"cpu {part.part_id + 1}/{len(parts)}"
            cpu_future = lanes.submit(
                "cpu",
                f"{month}:{ticker}:{part_name}:origins_windows",
                lambda events=events, part=part: _build_origins_and_windows(
                    events,
                    context_lags,
                    args.events_per_chunk,
                    args.sample_stride_events,
                    window,
                    origin_ordinal_start=part.origin_ordinal_start,
                    origin_ordinal_end=part.origin_ordinal_end,
                    month_min_ordinal=month_min_ordinal,
                ),
            )
            origins, windows, ranges, part_skipped_history, part_skipped_gap = cpu_future.result()
            state.cpu_done += 1
            labels = labels_future.result()
            state.labels_done += 1
            state.stage = f"write {part.part_id + 1}/{len(parts)}"
            part_files = {
                "events": f"events_{part_name}.parquet",
                "origins": f"origins_{part_name}.parquet",
                "event_window_index": f"event_window_index_{part_name}.parquet",
                "ranges": f"ranges_{part_name}.parquet",
                "intraday_forward_labels": f"intraday_forward_labels_{part_name}.parquet",
            }
            writes = {
                "events": lanes.submit("write", f"{month}:{ticker}:{part_name}:write_events", lambda events=events, path=tmp_dir / part_files["events"]: _write_parquet(events, path)),
                "origins": lanes.submit("write", f"{month}:{ticker}:{part_name}:write_origins", lambda origins=origins, path=tmp_dir / part_files["origins"]: _write_parquet(origins, path)),
                "windows": lanes.submit("write", f"{month}:{ticker}:{part_name}:write_windows", lambda windows=windows, path=tmp_dir / part_files["event_window_index"]: _write_parquet(windows, path)),
                "ranges": lanes.submit("write", f"{month}:{ticker}:{part_name}:write_ranges", lambda ranges=ranges, path=tmp_dir / part_files["ranges"]: _write_parquet(ranges, path)),
                "labels": lanes.submit("write", f"{month}:{ticker}:{part_name}:write_labels", lambda labels=labels, path=tmp_dir / part_files["intraday_forward_labels"]: _write_parquet(labels, path)),
            }
            for future in writes.values():
                future.result()
                state.write_done += 1
            total_events += int(events.height)
            total_origins += int(origins.height)
            total_windows += int(windows.height)
            total_labels += int(labels.height)
            skipped_history += int(part_skipped_history)
            skipped_gap += int(part_skipped_gap)
            part_manifests.append(
                {
                    "part_id": int(part.part_id),
                    "origin_ordinal_start": int(part.origin_ordinal_start),
                    "origin_ordinal_end": int(part.origin_ordinal_end),
                    "fetch_ordinal_start": int(part.fetch_ordinal_start),
                    "fetch_ordinal_end": int(part.fetch_ordinal_end),
                    "files": part_files,
                    "counts": {
                        "events": int(events.height),
                        "origins": int(origins.height),
                        "event_windows": int(windows.height),
                        "intraday_forward_labels": int(labels.height),
                        "skipped_not_enough_history": int(part_skipped_history),
                        "skipped_window_gap": int(part_skipped_gap),
                    },
                }
            )
        state.context_done = sum(1 for key in ("ticker_news", "sec_filings", "daily_bars", "xbrl") if futures[key].done())
        ticker_news = futures["ticker_news"].result()
        sec_filings = futures["sec_filings"].result()
        daily_bars = futures["daily_bars"].result()
        xbrl = futures["xbrl"].result()
        state.context_done = state.context_total
        if stop_event.is_set():
            raise KeyboardInterrupt
        state.stage = "write"
        context_writes = {
            "ticker_news": lanes.submit("write", f"{month}:{ticker}:write_news", lambda: _write_parquet(ticker_news, tmp_dir / "ticker_news_tokens.parquet")),
            "sec_filings": lanes.submit("write", f"{month}:{ticker}:write_sec", lambda: _write_parquet(sec_filings, tmp_dir / "sec_filing_tokens.parquet")),
            "xbrl": lanes.submit("write", f"{month}:{ticker}:write_xbrl", lambda: _write_parquet(xbrl, tmp_dir / "xbrl.parquet")),
            "daily_bars": lanes.submit("write", f"{month}:{ticker}:write_daily", lambda: _write_parquet(daily_bars, tmp_dir / "daily_bars.parquet")),
        }
        for future in context_writes.values():
            future.result()
            state.write_done += 1
        manifest = {
            "format": TICKER_MONTH_CACHE_FORMAT,
            "version": TICKER_MONTH_CACHE_VERSION,
            "status": "complete",
            "month": month,
            "ticker": ticker,
            "window": month_window_dict(window),
            "config": {
                "events_per_chunk": int(args.events_per_chunk),
                "context_lags": list(context_lags),
                "sample_stride_events": int(args.sample_stride_events),
                "required_event_lookback_rows": int(required_lookback),
                "max_origin_events_per_part": int(args.max_origin_events_per_part),
                "intraday_label_horizons": [h.name for h in parse_horizons(args.intraday_label_horizons)],
                "event_payload_columns": list(EVENT_PAYLOAD_COLUMNS),
                "event_time_feature_columns": list(EVENT_TIME_FEATURE_COLUMNS),
            },
            "counts": {
                "parts": int(len(part_manifests)),
                "events": int(total_events),
                "origins": int(total_origins),
                "event_windows": int(total_windows),
                "intraday_forward_labels": int(total_labels),
                "ticker_news_tokens": int(ticker_news.height),
                "sec_filing_tokens": int(sec_filings.height),
                "xbrl": int(xbrl.height),
                "daily_bars": int(daily_bars.height),
                "skipped_not_enough_history": int(skipped_history),
                "skipped_window_gap": int(skipped_gap),
            },
            "files": {
                "ticker_news_tokens": "ticker_news_tokens.parquet",
                "sec_filing_tokens": "sec_filing_tokens.parquet",
                "xbrl": "xbrl.parquet",
                "daily_bars": "daily_bars.parquet",
            },
            "parts": part_manifests,
            "completed_at": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
        }
        write_json_atomic(tmp_dir / "manifest.json", manifest)
        replace_complete_dir(tmp_dir, package_dir, resume=True)
        byte_count = directory_size(package_dir)
        state.status = "done"
        state.stage = "done"
        state.seconds = time.perf_counter() - state.started_at
        state.message = f"done parts={len(part_manifests):,} origins={total_origins:,}"
        return TickerMonthResult(
            month=month,
            ticker=ticker,
            package_dir=package_dir,
            status="complete",
            event_count=int(total_events),
            origin_count=int(total_origins),
            label_rows=int(total_labels),
            byte_count=byte_count,
            skipped_not_enough_history=int(skipped_history),
            skipped_window_gap=int(skipped_gap),
        )
    except BaseException as exc:
        state.status = "failed"
        state.stage = "failed"
        state.seconds = time.perf_counter() - state.started_at
        state.message = repr(exc)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        stats.log_error(f"{month}:{ticker}", exc)
        raise


def _origin_ordinal_parts(bounds: tuple[int, int] | None, *, required_lookback: int, max_origin_events_per_part: int) -> list[OriginOrdinalPart]:
    if not bounds:
        return []
    min_ordinal, max_ordinal = int(bounds[0]), int(bounds[1])
    if max_ordinal < min_ordinal:
        return []
    step = max(1, int(max_origin_events_per_part))
    parts: list[OriginOrdinalPart] = []
    start = min_ordinal
    while start <= max_ordinal:
        end = min(max_ordinal, start + step - 1)
        parts.append(
            OriginOrdinalPart(
                part_id=len(parts),
                origin_ordinal_start=int(start),
                origin_ordinal_end=int(end),
                fetch_ordinal_start=max(0, int(start) - int(required_lookback)),
                fetch_ordinal_end=int(end),
            )
        )
        start = end + 1
    return parts


def _query_events_part(args: argparse.Namespace, client_opts: Mapping[str, str], config: RollingMarketDataConfig, window: Any, ticker: str, part: OriginOrdinalPart) -> Any:
    table = f"{quote_ident(config.database)}.{quote_ident(config.events_table)}"
    query = f"""
WITH
    fromUnixTimestamp64Micro(sip_timestamp_us, 'UTC') AS ts_utc,
    toTimeZone(ts_utc, {sql_string(SESSION_TIMEZONE)}) AS ts_local,
    dateDiff('second', toStartOfDay(ts_utc), ts_utc) AS utc_second,
    toDayOfWeek(ts_utc) AS utc_dow,
    toDayOfYear(ts_utc) AS utc_doy,
    dateDiff('second', toStartOfDay(ts_local), ts_local) AS local_second
SELECT
    cityHash64(ticker) AS ticker_id,
    upper(ticker) AS ticker,
    ordinal,
    event_type,
    sip_timestamp_us AS timestamp_us,
    price_primary_int,
    price_secondary_int,
    toFloat32(size_primary) AS size_primary,
    toFloat32(size_secondary) AS size_secondary,
    exchange_primary,
    exchange_secondary,
    event_flags,
    conditions_packed,
    toFloat32(sin(2 * pi() * utc_second / 86400.0)) AS utc_second_of_day_sin,
    toFloat32(cos(2 * pi() * utc_second / 86400.0)) AS utc_second_of_day_cos,
    toFloat32(sin(2 * pi() * (utc_dow - 1) / 7.0)) AS utc_day_of_week_sin,
    toFloat32(cos(2 * pi() * (utc_dow - 1) / 7.0)) AS utc_day_of_week_cos,
    toFloat32(sin(2 * pi() * (utc_doy - 1) / 366.0)) AS utc_day_of_year_sin,
    toFloat32(cos(2 * pi() * (utc_doy - 1) / 366.0)) AS utc_day_of_year_cos,
    toFloat32(toYear(ts_utc) - 2000 + (utc_doy - 1) / 366.0) AS years_since_2000,
    toUInt32(local_second) AS session_second,
    toFloat32(greatest(0, least(57600, local_second - 14400)) / 57600.0) AS session_progress,
    toUInt8(local_second >= 34200 AND local_second < 57600) AS is_regular_hours,
    toUInt8(local_second >= 14400 AND local_second < 34200) AS is_premarket,
    toUInt8(local_second >= 57600 AND local_second < 72000) AS is_afterhours
FROM {table}
PREWHERE ticker = {sql_string(ticker)}
  AND ordinal >= {int(part.fetch_ordinal_start)}
  AND ordinal <= {int(part.fetch_ordinal_end)}
  AND event_date <= toDate({sql_string(window.next_month_date.isoformat())})
ORDER BY ticker, ordinal
{_settings_sql(config)}
"""
    return query_polars(client_opts, query)


def _query_origin_bounds(args: argparse.Namespace, client_opts: Mapping[str, str], config: RollingMarketDataConfig, window: Any, ticker: str) -> tuple[int, int] | None:
    table = f"{quote_ident(config.database)}.{quote_ident(config.events_table)}"
    query = f"""
WITH
    fromUnixTimestamp64Micro(sip_timestamp_us, 'UTC') AS ts_utc,
    toTimeZone(ts_utc, {sql_string(SESSION_TIMEZONE)}) AS ts_local,
    dateDiff('second', toStartOfDay(ts_local), ts_local) AS local_second
SELECT
    min(ordinal) AS min_ordinal,
    max(ordinal) AS max_ordinal,
    count() AS rows
FROM {table}
PREWHERE ticker = {sql_string(ticker)}
  AND event_date >= toDate({sql_string(window.first_date.isoformat())})
  AND event_date <= toDate({sql_string(window.next_month_date.isoformat())})
WHERE sip_timestamp_us >= {int(window.first_session_start_us)}
  AND sip_timestamp_us < {int(window.last_session_end_us)}
  AND toDate(ts_local) >= toDate({sql_string(window.first_date.isoformat())})
  AND toDate(ts_local) < toDate({sql_string(window.next_month_date.isoformat())})
  AND local_second >= 14400
  AND local_second < 72000
{_settings_sql(config)}
"""
    frame = query_polars(client_opts, query)
    if not frame.height or int(frame.get_column("rows")[0] or 0) <= 0:
        return None
    return int(frame.get_column("min_ordinal")[0]), int(frame.get_column("max_ordinal")[0])


def _query_intraday_forward_labels(
    args: argparse.Namespace,
    client_opts: Mapping[str, str],
    config: RollingMarketDataConfig,
    window: Any,
    ticker: str,
    part: OriginOrdinalPart,
    month_min_ordinal: int,
) -> Any:
    horizons = parse_horizons(args.intraday_label_horizons)
    if not horizons:
        return _empty_frame()
    frames = []
    for horizon in horizons:
        frames.append(_query_intraday_forward_label_horizon(args, client_opts, config, window, ticker, horizon, part, month_min_ordinal))
    pl = _polars()
    return pl.concat(frames, how="vertical") if frames else _empty_frame()


def _query_intraday_forward_label_horizon(
    args: argparse.Namespace,
    client_opts: Mapping[str, str],
    config: RollingMarketDataConfig,
    window: Any,
    ticker: str,
    horizon: TimeBarHorizon,
    part: OriginOrdinalPart,
    month_min_ordinal: int,
) -> Any:
    table = f"{quote_ident(config.database)}.{quote_ident(config.events_table)}"
    horizon_us = int(horizon.microseconds)
    # This is a set query for one ticker/month/horizon. It intentionally avoids per-origin round trips.
    query = f"""
WITH
    {int(horizon_us)} AS horizon_us,
    part_bounds AS
    (
        SELECT
            min(sip_timestamp_us) AS min_origin_timestamp_us,
            max(sip_timestamp_us) AS max_origin_timestamp_us
        FROM {table}
        PREWHERE ticker = {sql_string(ticker)}
          AND ordinal >= {int(part.origin_ordinal_start)}
          AND ordinal <= {int(part.origin_ordinal_end)}
          AND event_date >= toDate({sql_string(window.first_date.isoformat())})
          AND event_date <= toDate({sql_string(window.next_month_date.isoformat())})
        WHERE sip_timestamp_us >= {int(window.first_session_start_us)}
          AND sip_timestamp_us < {int(window.last_session_end_us)}
    ),
    origins AS
    (
        SELECT
            upper(ticker) AS ticker,
            cityHash64(ticker) AS ticker_id,
            ordinal AS origin_ordinal,
            sip_timestamp_us AS origin_timestamp_us,
            concat(upper(ticker), '|', toString(ordinal)) AS origin_key,
            toDate(toTimeZone(fromUnixTimestamp64Micro(sip_timestamp_us, 'UTC'), {sql_string(SESSION_TIMEZONE)})) AS origin_local_date,
            dateDiff('second', toStartOfDay(toTimeZone(fromUnixTimestamp64Micro(sip_timestamp_us, 'UTC'), {sql_string(SESSION_TIMEZONE)})), toTimeZone(fromUnixTimestamp64Micro(sip_timestamp_us, 'UTC'), {sql_string(SESSION_TIMEZONE)})) AS local_second
        FROM {table}
        PREWHERE ticker = {sql_string(ticker)}
          AND ordinal >= {int(part.origin_ordinal_start)}
          AND ordinal <= {int(part.origin_ordinal_end)}
          AND event_date >= toDate({sql_string(window.first_date.isoformat())})
          AND event_date <= toDate({sql_string(window.next_month_date.isoformat())})
        WHERE modulo(ordinal - {int(month_min_ordinal)}, {max(1, int(args.sample_stride_events))}) = 0
          AND sip_timestamp_us >= {int(window.first_session_start_us)}
          AND sip_timestamp_us < {int(window.last_session_end_us)}
          AND toDate(toTimeZone(fromUnixTimestamp64Micro(sip_timestamp_us, 'UTC'), {sql_string(SESSION_TIMEZONE)})) >= toDate({sql_string(window.first_date.isoformat())})
          AND toDate(toTimeZone(fromUnixTimestamp64Micro(sip_timestamp_us, 'UTC'), {sql_string(SESSION_TIMEZONE)})) < toDate({sql_string(window.next_month_date.isoformat())})
          AND dateDiff('second', toStartOfDay(toTimeZone(fromUnixTimestamp64Micro(sip_timestamp_us, 'UTC'), {sql_string(SESSION_TIMEZONE)})), toTimeZone(fromUnixTimestamp64Micro(sip_timestamp_us, 'UTC'), {sql_string(SESSION_TIMEZONE)})) >= 14400
          AND dateDiff('second', toStartOfDay(toTimeZone(fromUnixTimestamp64Micro(sip_timestamp_us, 'UTC'), {sql_string(SESSION_TIMEZONE)})), toTimeZone(fromUnixTimestamp64Micro(sip_timestamp_us, 'UTC'), {sql_string(SESSION_TIMEZONE)})) < 72000
    ),
    future_events AS
    (
        SELECT
            ticker,
            ordinal,
            sip_timestamp_us,
            price_primary_int,
            price_secondary_int,
            toFloat32(size_primary) AS size_primary,
            toFloat32(size_secondary) AS size_secondary,
            event_type,
            toDate(toTimeZone(fromUnixTimestamp64Micro(sip_timestamp_us, 'UTC'), {sql_string(SESSION_TIMEZONE)})) AS local_date
        FROM {table}
        PREWHERE ticker = {sql_string(ticker)}
          AND event_date >= toDate({sql_string(window.first_date.isoformat())})
          AND event_date <= toDate({sql_string(window.next_month_date.isoformat())})
        WHERE sip_timestamp_us > (SELECT min_origin_timestamp_us FROM part_bounds)
          AND sip_timestamp_us <= least({int(window.last_session_end_us)}, (SELECT max_origin_timestamp_us FROM part_bounds) + horizon_us)
    )
SELECT
    o.origin_key,
    o.ticker_id,
    o.ticker,
    o.origin_ordinal,
    o.origin_timestamp_us,
    {sql_string(horizon.name)} AS horizon,
    horizon_us,
    argMax(e.price_primary_int, e.sip_timestamp_us) AS price_primary_int,
    argMax(e.price_secondary_int, e.sip_timestamp_us) AS price_secondary_int,
    sum(e.size_primary) AS size_primary_sum,
    sum(e.size_secondary) AS size_secondary_sum,
    count(e.ordinal) AS event_count,
    max(e.sip_timestamp_us) AS last_event_timestamp_us,
    toUInt8((o.local_second * 1000000 + horizon_us) <= 72000000000 AND count(e.ordinal) > 0) AS available
FROM origins AS o
LEFT JOIN future_events AS e
    ON e.ticker = o.ticker
   AND e.local_date = o.origin_local_date
   AND e.sip_timestamp_us > o.origin_timestamp_us
   AND e.sip_timestamp_us <= o.origin_timestamp_us + horizon_us
GROUP BY
    o.origin_key,
    o.ticker_id,
    o.ticker,
    o.origin_ordinal,
    o.origin_timestamp_us,
    o.origin_local_date,
    o.local_second
ORDER BY origin_ordinal
{_settings_sql(config, extra={"allow_experimental_join_condition": 1})}
"""
    return query_polars(client_opts, query)


def _query_ticker_news(args: argparse.Namespace, client_opts: Mapping[str, str], config: RollingMarketDataConfig, window: Any, ticker: str) -> Any:
    if args.skip_token_contexts:
        return _empty_frame()
    table = f"{quote_ident(config.database)}.{quote_ident(config.news_token_table)}"
    columns = ",\n    ".join(quote_ident(column) for column in NEWS_TOKEN_COLUMNS)
    start_us = max(0, int(window.first_session_start_us) - int(config.news_lookback_days) * 86_400_000_000)
    query = f"""
SELECT
    {columns}
FROM {table}
WHERE ticker = {sql_string(ticker)}
  AND timestamp_us >= {int(start_us)}
  AND timestamp_us < {int(window.last_session_end_us)}
  AND published_at_utc >= {date_time64_from_us(start_us)}
  AND published_at_utc < {date_time64_from_us(window.last_session_end_us)}
ORDER BY ticker, timestamp_us, source_id, token_chunk_index
{_settings_sql(config)}
"""
    return query_polars(client_opts, query)


def _query_market_news(args: argparse.Namespace, client_opts: Mapping[str, str], config: RollingMarketDataConfig, window: Any) -> Any:
    if args.skip_token_contexts:
        return _empty_frame()
    table = f"{quote_ident(config.database)}.{quote_ident(config.news_token_table)}"
    source_columns = ",\n        ".join(f"t.{quote_ident(column)}" for column in NEWS_TOKEN_COLUMNS if column != "ticker")
    start_us = max(0, int(window.first_session_start_us) - int(config.news_lookback_days) * 86_400_000_000)
    query = f"""
SELECT
    '__MARKET__' AS ticker,
    {source_columns}
FROM
(
    SELECT *
    FROM {table}
    WHERE timestamp_us >= {int(start_us)}
      AND timestamp_us < {int(window.last_session_end_us)}
      AND published_at_utc >= {date_time64_from_us(start_us)}
      AND published_at_utc < {date_time64_from_us(window.last_session_end_us)}
    ORDER BY source_id, token_chunk_index, ticker
    LIMIT 1 BY source_id, token_chunk_index
) AS t
ORDER BY timestamp_us, source_id, token_chunk_index
{_settings_sql(config)}
"""
    return query_polars(client_opts, query)


def _query_sec_tokens(args: argparse.Namespace, client_opts: Mapping[str, str], config: RollingMarketDataConfig, window: Any, ticker: str) -> Any:
    if args.skip_token_contexts:
        return _empty_frame()
    table = f"{quote_ident(config.sec_context_database)}.{quote_ident(config.sec_filing_text_token_table)}"
    columns = ",\n    ".join(quote_ident(column) for column in SEC_TOKEN_COLUMNS)
    start_us = max(0, int(window.first_session_start_us) - int(config.sec_lookback_days) * 86_400_000_000)
    query = f"""
SELECT
    {columns}
FROM {table}
WHERE ticker = {sql_string(ticker)}
  AND timestamp_us >= {int(start_us)}
  AND timestamp_us < {int(window.last_session_end_us)}
  AND accepted_at_utc >= {date_time64_from_us(start_us)}
  AND accepted_at_utc < {date_time64_from_us(window.last_session_end_us)}
ORDER BY ticker, timestamp_us, accession_number, text_rank, document_id, source_id, token_chunk_index
{_settings_sql(config)}
"""
    return query_polars(client_opts, query)


def _query_xbrl(args: argparse.Namespace, client_opts: Mapping[str, str], config: RollingMarketDataConfig, window: Any, ticker: str) -> Any:
    table = f"{quote_ident(config.sec_context_database)}.{quote_ident(config.sec_xbrl_context_table)}"
    start_us = max(0, int(window.first_session_start_us) - int(config.xbrl_lookback_days) * 86_400_000_000)
    query = f"""
SELECT
    ticker,
    timestamp_us,
    source_id,
    cik,
    issuer_id,
    taxonomy,
    tag,
    unit_code,
    fiscal_year,
    fiscal_period,
    form_type,
    accepted_at_source,
    accession_number,
    period_end_date,
    value,
    calendar_period_code,
    location_code,
    xbrl_row_kind,
    bridge_id,
    mapping_confidence AS mapping_confidence_score
FROM {table}
WHERE ticker = {sql_string(ticker)}
  AND timestamp_us >= {int(start_us)}
  AND timestamp_us < {int(window.last_session_end_us)}
ORDER BY ticker, timestamp_us, xbrl_row_kind, taxonomy, tag, unit_code, period_end_date
{_settings_sql(config)}
"""
    return query_polars(client_opts, query)


def _query_daily_bars(args: argparse.Namespace, client_opts: Mapping[str, str], config: RollingMarketDataConfig, window: Any, *, symbols: tuple[str, ...]) -> Any:
    if not symbols:
        return _empty_frame()
    table = f"{quote_ident(config.database)}.{quote_ident(config.macro_bars_table)}"
    symbol_sql = ", ".join(sql_string(str(symbol).upper()) for symbol in symbols)
    start = window.first_date - dt.timedelta(days=max(0, int(config.macro_lookback_days)))
    end = window.next_month_date + dt.timedelta(days=max(0, int(config.label_lookahead_days)))
    query = f"""
SELECT
    upper(sym) AS sym,
    timeframe,
    toUnixTimestamp64Milli(bar_start) AS bar_start_ms,
    open,
    high,
    low,
    close,
    volume,
    dollar_volume,
    trade_count,
    quote_count,
    vwap
FROM {table}
WHERE timeframe = '1d'
  AND upper(sym) IN ({symbol_sql})
  AND bar_start >= toDateTime64({sql_string(start.isoformat() + " 00:00:00")}, 3, 'UTC')
  AND bar_start < toDateTime64({sql_string(end.isoformat() + " 00:00:00")}, 3, 'UTC')
ORDER BY sym, timeframe, bar_start
{_settings_sql(config)}
"""
    return query_polars(client_opts, query)


def _query_category_references(args: argparse.Namespace, client_opts: Mapping[str, str], config: RollingMarketDataConfig) -> Any:
    table = f"{quote_ident(config.sec_context_database)}.{quote_ident(config.category_reference_table)}"
    query = f"""
SELECT
    domain,
    field_name,
    category_value,
    argMax(category_id, updated_at) AS category_id,
    argMax(one_hot_index, updated_at) AS one_hot_index
FROM {table}
GROUP BY
    domain,
    field_name,
    category_value
ORDER BY
    domain,
    field_name,
    category_id
{_settings_sql(config)}
"""
    return query_polars(client_opts, query)


def _build_origins_and_windows(
    events: Any,
    context_lags: tuple[int, ...],
    events_per_chunk: int,
    sample_stride: int,
    window: Any,
    *,
    origin_ordinal_start: int,
    origin_ordinal_end: int,
    month_min_ordinal: int,
) -> tuple[Any, Any, Any, int, int]:
    pl = _polars()
    if events.height == 0 or not context_lags:
        return _empty_origins(), _empty_windows(context_lags), _empty_ranges(), 0, 0
    events = events.sort(["ticker", "ordinal"]).with_row_index("row_offset")
    ranges = (
        events.group_by(["ticker", "ticker_id"], maintain_order=True)
        .agg(
            pl.col("row_offset").min().alias("first_row_offset"),
            pl.col("row_offset").max().alias("last_row_offset"),
            pl.col("ordinal").min().alias("first_ordinal"),
            pl.col("ordinal").max().alias("last_ordinal"),
            pl.col("timestamp_us").min().alias("first_timestamp_us"),
            pl.col("timestamp_us").max().alias("last_timestamp_us"),
            pl.len().alias("row_count"),
        )
        .sort("ticker")
    )
    lags = list(context_lags)
    origin_frames = []
    window_frames = []
    skipped_history = 0
    skipped_gap = 0
    origin_id = 0
    for key, part in events.partition_by("ticker", as_dict=True, maintain_order=True).items():
        ticker = key[0] if isinstance(key, tuple) else key
        ordinals = part.get_column("ordinal").to_numpy().astype(np.int64, copy=False)
        timestamps = part.get_column("timestamp_us").to_numpy().astype(np.int64, copy=False)
        row_offsets = part.get_column("row_offset").to_numpy().astype(np.int64, copy=False)
        ticker_ids = part.get_column("ticker_id").to_numpy()
        positions = np.flatnonzero(
            (ordinals >= int(origin_ordinal_start))
            & (ordinals <= int(origin_ordinal_end))
            & (timestamps >= int(window.first_session_start_us))
            & (timestamps < int(window.last_session_end_us))
        )
        if positions.size == 0:
            continue
        stride = max(1, int(sample_stride))
        if stride > 1:
            positions = positions[((ordinals[positions] - int(month_min_ordinal)) % stride) == 0]
            if positions.size == 0:
                continue
        candidates = positions
        lag_array = np.asarray(lags, dtype=np.int64)
        ends = candidates[:, None] - lag_array[None, :]
        starts = ends - int(events_per_chunk) + 1
        history_ok = (starts >= 0) & (ends >= 0) & (ends < len(ordinals))
        history_all = np.all(history_ok, axis=1)
        skipped_history += int(np.count_nonzero(~history_all))
        if not history_all.any():
            continue
        valid_idx = np.flatnonzero(history_all)
        valid_starts = starts[valid_idx]
        valid_ends = ends[valid_idx]
        contiguous = np.all((ordinals[valid_ends] - ordinals[valid_starts]) == (int(events_per_chunk) - 1), axis=1)
        skipped_gap += int(np.count_nonzero(~contiguous))
        if not contiguous.any():
            continue
        final_idx = valid_idx[np.flatnonzero(contiguous)]
        final_positions = candidates[final_idx]
        final_starts = valid_starts[np.flatnonzero(contiguous)]
        count = int(final_positions.shape[0])
        ids = np.arange(origin_id, origin_id + count, dtype=np.int64)
        origin_ordinals = ordinals[final_positions]
        origin_frames.append(
            pl.DataFrame(
                {
                    "origin_id": ids,
                    "origin_key": [f"{str(ticker).upper()}|{int(value)}" for value in origin_ordinals],
                    "ticker": [str(ticker).upper()] * count,
                    "ticker_id": ticker_ids[final_positions],
                    "origin_ordinal": origin_ordinals,
                    "origin_timestamp_us": timestamps[final_positions],
                    "event_row_offset": row_offsets[final_positions],
                }
            )
        )
        columns: dict[str, Any] = {"origin_id": ids, "origin_key": [f"{str(ticker).upper()}|{int(value)}" for value in origin_ordinals]}
        start_offsets = row_offsets[final_starts]
        for context_index in range(len(lags)):
            columns[f"window_start_{context_index:03d}"] = start_offsets[:, context_index]
        window_frames.append(pl.DataFrame(columns))
        origin_id += count
    origins = pl.concat(origin_frames, how="vertical") if origin_frames else _empty_origins()
    windows = pl.concat(window_frames, how="vertical") if window_frames else _empty_windows(context_lags)
    return origins, windows, ranges, skipped_history, skipped_gap


def query_polars(client_opts: Mapping[str, str], query: str) -> Any:
    try:
        import clickhouse_connect  # type: ignore
    except ModuleNotFoundError as exc:
        raise RuntimeError("Install clickhouse-connect in this environment to query ClickHouse Arrow results.") from exc
    parsed = urlparse(str(client_opts["clickhouse_url"]))
    secure = parsed.scheme == "https"
    query_id = f"rolling_ticker_month_{os.getpid()}_{threading.get_ident()}_{uuid.uuid4().hex}"
    ACTIVE_QUERIES.register(query_id)
    client = clickhouse_connect.get_client(
        host=parsed.hostname or "localhost",
        port=parsed.port or (8443 if secure else 8123),
        username=str(client_opts.get("user") or "default"),
        password=str(client_opts.get("password") or ""),
        secure=secure,
    )
    try:
        table = _query_arrow_with_id(client, query=query, query_id=query_id)
    finally:
        ACTIVE_QUERIES.unregister(query_id)
        try:
            client.close()
        except Exception:
            pass
    return _polars().from_arrow(table)


def _query_arrow_with_id(client: Any, *, query: str, query_id: str) -> Any:
    try:
        return client.query_arrow(query, query_id=query_id)
    except TypeError:
        try:
            return client.query_arrow(query, settings={"query_id": query_id})
        except TypeError:
            return client.query_arrow(f"/* query_id={query_id} */\n{query}")


def cancel_active_clickhouse_queries(*, client_opts: Mapping[str, str], stats: BuildStats | None = None) -> int:
    active = ACTIVE_QUERIES.snapshot()
    if not active:
        return 0
    ids = sorted(active)
    if stats is not None:
        stats.log_event("clickhouse_cancel_start", active_query_ids=ids, active_queries=active)
    try:
        quoted = ", ".join(sql_string(query_id) for query_id in ids)
        _execute_clickhouse_cancel(
            client_opts=client_opts,
            sql=f"KILL QUERY WHERE query_id IN ({quoted}) SYNC",
            timeout_seconds=5.0,
        )
    except Exception as exc:
        if stats is not None:
            stats.log_event("clickhouse_cancel_error", error=repr(exc), active_query_ids=ids)
        return 0
    ACTIVE_QUERIES.clear()
    if stats is not None:
        stats.log_event("clickhouse_cancel_done", cancelled=len(ids), active_query_ids=ids)
    return len(ids)


def _execute_clickhouse_cancel(*, client_opts: Mapping[str, str], sql: str, timeout_seconds: float) -> str:
    url = str(client_opts["clickhouse_url"]).rstrip("/") + "/"
    req = url_request.Request(url, data=sql.encode("utf-8"), method="POST")
    user = str(client_opts.get("user") or "default")
    password = str(client_opts.get("password") or "")
    if user:
        req.add_header("X-ClickHouse-User", user)
    if password:
        req.add_header("X-ClickHouse-Key", password)
    with url_request.urlopen(req, timeout=max(1.0, float(timeout_seconds))) as response:
        return response.read().decode("utf-8", errors="replace")


def _settings_sql(config: RollingMarketDataConfig, *, extra: Mapping[str, Any] | None = None) -> str:
    settings: dict[str, Any] = {}
    if int(config.max_threads) > 0:
        settings["max_threads"] = int(config.max_threads)
    if str(config.max_memory_usage) != "0":
        settings["max_memory_usage"] = parse_size_bytes(str(config.max_memory_usage))
    settings.update(dict(extra or {}))
    if not settings:
        return ""
    parts = []
    for key, value in settings.items():
        if isinstance(value, str):
            parts.append(f"{key} = {sql_string(value)}")
        else:
            parts.append(f"{key} = {value}")
    return "SETTINGS " + ", ".join(parts)


def _write_parquet(frame: Any, path: Path) -> dict[str, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    if int(getattr(frame, "width", 0) or 0) == 0:
        frame = _polars().DataFrame({"__empty__": []})
    frame.write_parquet(tmp, compression="zstd")
    tmp.replace(path)
    return {"rows": int(getattr(frame, "height", 0)), "bytes": int(path.stat().st_size)}


def _empty_frame() -> Any:
    return _polars().DataFrame()


def _empty_events_frame() -> Any:
    pl = _polars()
    data: dict[str, Any] = {column: [] for column in (*EVENT_PAYLOAD_COLUMNS, *EVENT_TIME_FEATURE_COLUMNS)}
    return pl.DataFrame(data)


def _empty_origins() -> Any:
    return _polars().DataFrame({"origin_id": [], "origin_key": [], "ticker": [], "ticker_id": [], "origin_ordinal": [], "origin_timestamp_us": [], "event_row_offset": []})


def _empty_windows(context_lags: tuple[int, ...]) -> Any:
    columns: dict[str, Any] = {"origin_id": [], "origin_key": []}
    for context_index in range(len(context_lags)):
        columns[f"window_start_{context_index:03d}"] = []
    return _polars().DataFrame(columns)


def _empty_ranges() -> Any:
    return _polars().DataFrame({"ticker": [], "ticker_id": [], "first_row_offset": [], "last_row_offset": [], "first_ordinal": [], "last_ordinal": [], "first_timestamp_us": [], "last_timestamp_us": [], "row_count": []})


def _polars() -> Any:
    try:
        import polars as pl  # type: ignore
    except ModuleNotFoundError as exc:
        raise RuntimeError("Install polars in this environment to build ticker/month caches.") from exc
    return pl


def _row_count(result: Any) -> int:
    if isinstance(result, Mapping):
        return int(result.get("rows", 0) or 0)
    return int(getattr(result, "height", 0) or 0)


def _byte_count(result: Any) -> int:
    if isinstance(result, Mapping):
        return int(result.get("bytes", 0) or 0)
    try:
        return int(result.estimated_size())
    except Exception:
        return 0


def _result_manifest(result: TickerMonthResult) -> dict[str, Any]:
    return {
        "month": result.month,
        "ticker": result.ticker,
        "path": str(result.package_dir),
        "status": result.status,
        "events": int(result.event_count),
        "origins": int(result.origin_count),
        "intraday_forward_labels": int(result.label_rows),
        "bytes": int(result.byte_count),
        "skipped_not_enough_history": int(result.skipped_not_enough_history),
        "skipped_window_gap": int(result.skipped_window_gap),
        "error": result.error,
    }


def _summary(stats: BuildStats) -> dict[str, Any]:
    return {
        "months": int(stats.months_done),
        "packages": int(stats.packages_done),
        "failed_packages": int(stats.packages_failed),
        "events": int(stats.events_written),
        "origins": int(stats.origins_written),
        "intraday_forward_label_rows": int(stats.labels_written),
        "bytes": int(stats.bytes_written),
        "elapsed_seconds": time.perf_counter() - stats.started,
        "max_rss_mib": float(stats.max_rss_mib),
    }


def _month_summary(*, month: str, window: Any, tickers: list[str], stats: BuildStats) -> dict[str, Any]:
    return {
        "format": TICKER_MONTH_CACHE_FORMAT,
        "version": TICKER_MONTH_CACHE_VERSION,
        "status": "complete",
        "month": month,
        "window": month_window_dict(window),
        "ticker_count": len(tickers),
        "tickers": tickers,
        "updated_at": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
        "summary": _summary(stats),
    }


def _write_progress(cache_root: Path, stats: BuildStats) -> None:
    stats.current_rss_mib = current_rss_mib()
    stats.max_rss_mib = max(float(stats.max_rss_mib), float(stats.current_rss_mib))
    active_queries = ACTIVE_QUERIES.snapshot()
    if stats.progress_path is None:
        return
    lanes = {
        name: {
            "queued": lane.queued,
            "running": lane.running,
            "done": lane.done,
            "failed": lane.failed,
            "rows": lane.rows,
            "bytes": lane.bytes,
            "seconds": lane.seconds,
            "active": list(lane.active.values()),
        }
        for name, lane in stats.lanes.items()
    }
    workers = {str(idx): jsonable(asdict(worker)) for idx, worker in stats.workers.items()}
    write_json_atomic(
        stats.progress_path,
        {
            "summary": _summary(stats),
            "phase": stats.phase,
            "lanes": lanes,
            "workers": workers,
            "active_clickhouse_queries": active_queries,
            "messages": list(stats.messages),
        },
    )


def _refresh(stats: BuildStats, dashboard: "TickerMonthDashboard", cache_root: Path, *, force: bool = False) -> None:
    _write_progress(cache_root, stats)
    dashboard.refresh(force=force)


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "estimating"
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def _format_bytes(value: float) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    amount = float(value)
    for unit in units:
        if abs(amount) < 1024.0 or unit == units[-1]:
            return f"{amount:,.2f} {unit}"
        amount /= 1024.0
    return f"{amount:,.2f} B"


class TickerMonthDashboard:
    def __init__(self, *, enabled: bool, live: bool, refresh_seconds: float, stats: BuildStats) -> None:
        self.enabled = bool(enabled)
        self.live = bool(live)
        self.refresh_seconds = max(0.25, float(refresh_seconds))
        self.stats = stats
        self._last = 0.0
        self._live: Any | None = None
        self._rich: dict[str, Any] = {}
        self._printed_messages = 0
        self._status_width = 0
        self._lock = threading.Lock()

    def start(self) -> None:
        if not self.enabled or not self.live:
            return
        try:
            from rich import box
            from rich.console import Console, Group
            from rich.live import Live
            from rich.panel import Panel
            from rich.table import Table
            from rich.text import Text
        except Exception as exc:
            self.enabled = False
            self.stats.message(f"Rich dashboard unavailable; using compact status line: {exc!r}")
            return
        self._rich = {"box": box, "Console": Console, "Group": Group, "Live": Live, "Panel": Panel, "Table": Table, "Text": Text}
        console = Console(force_terminal=True, color_system="auto", soft_wrap=False)
        self._live = Live(
            self._render(),
            console=console,
            refresh_per_second=max(0.5, 1.0 / self.refresh_seconds),
            auto_refresh=False,
            screen=False,
            transient=False,
            vertical_overflow="crop",
        )
        try:
            self._live.start()
        except Exception as exc:
            self._live = None
            self.enabled = False
            self.stats.message(f"Rich dashboard failed to start; using compact status line: {exc!r}")
            return
        self.stats.message("Rich dashboard active")

    def refresh(self, *, force: bool = False) -> None:
        now = time.perf_counter()
        if not (force or now - self._last >= self.refresh_seconds):
            return
        with self._lock:
            if self._live is not None:
                self._live.update(self._render(), refresh=True)
            else:
                self._print_status_line(final=False)
            self._last = now

    def stop(self) -> None:
        with self._lock:
            if self._live is not None:
                self._live.update(self._render(), refresh=True)
                self._live.stop()
                self._live = None
            else:
                self._print_status_line(final=True)

    def _print_status_line(self, *, final: bool) -> None:
        while self._printed_messages < len(self.stats.messages):
            if self._status_width:
                sys.stdout.write("\r" + " " * self._status_width + "\r")
            print(self.stats.messages[self._printed_messages], flush=True)
            self._printed_messages += 1
            self._status_width = 0
        elapsed = time.perf_counter() - self.stats.started
        rate = self.stats.packages_done / max(elapsed, 1e-9)
        remaining = max(0, self.stats.packages_total - self.stats.packages_done)
        eta = remaining / rate if rate > 0 else None
        active_queries = ACTIVE_QUERIES.snapshot()
        longest_query_seconds = max((float(row.get("seconds") or 0.0) for row in active_queries.values()), default=0.0)
        status = (
            f"[{self.stats.phase}] packages={self.stats.packages_done}/{self.stats.packages_total} "
            f"origins={self.stats.origins_written:,} labels={self.stats.labels_written:,} "
            f"size={_format_bytes(self.stats.bytes_written)} rss={self.stats.current_rss_mib:.1f}MiB "
            f"activeQ={len(active_queries)}/{_format_duration(longest_query_seconds)} "
            f"elapsed={_format_duration(elapsed)} eta={_format_duration(eta)}"
        )
        width = shutil.get_terminal_size((120, 40)).columns - 1
        if len(status) > width:
            status = status[: max(0, width - 1)] + "."
        padding = max(0, self._status_width - len(status))
        sys.stdout.write("\r" + status + (" " * padding) + ("\n" if final else ""))
        sys.stdout.flush()
        self._status_width = 0 if final else len(status)

    def _render(self) -> Any:
        box = self._rich["box"]
        Group = self._rich["Group"]
        Panel = self._rich["Panel"]
        Table = self._rich["Table"]
        Text = self._rich["Text"]
        elapsed = time.perf_counter() - self.stats.started
        package_rate = self.stats.packages_done / max(elapsed, 1e-9)
        remaining_packages = max(0, self.stats.packages_total - self.stats.packages_done)
        eta = remaining_packages / package_rate if package_rate > 0 else None
        active_queries = ACTIVE_QUERIES.snapshot()
        longest_query_seconds = max((float(row.get("seconds") or 0.0) for row in active_queries.values()), default=0.0)
        terminal_size = shutil.get_terminal_size((140, 40))
        compact = terminal_size.columns < 145 or terminal_size.lines < 28
        summary = Table.grid(expand=False)
        pair_count = 2 if compact else 3
        for idx in range(pair_count):
            if idx:
                summary.add_column(width=3)
            summary.add_column(justify="right", style="dim", no_wrap=True)
            summary.add_column(no_wrap=True)
        rows = [
            (("Phase", self.stats.phase), ("Elapsed", _format_duration(elapsed)), ("ETA", _format_duration(eta))),
            (("Months", f"{self.stats.months_done}/{self.stats.months_total}"), ("Packages", f"{self.stats.packages_done}/{self.stats.packages_total}"), ("Failed", f"{self.stats.packages_failed}")),
            (("Events", f"{self.stats.events_written:,}"), ("Origins", f"{self.stats.origins_written:,}"), ("Labels", f"{self.stats.labels_written:,}")),
            (("Size", _format_bytes(self.stats.bytes_written)), ("RSS", f"{self.stats.current_rss_mib:.1f}/{self.stats.max_rss_mib:.1f} MiB"), ("ActiveQ", f"{len(active_queries)} / {_format_duration(longest_query_seconds)}")),
            (("Logs", str(self.stats.log_path or "")), ("Progress", str(self.stats.progress_path or "")), ("Errors", str(self.stats.errors_path or ""))),
        ]
        for row in rows:
            cells: list[str] = []
            for idx, (key, value) in enumerate(row[:pair_count]):
                if idx:
                    cells.append("")
                cells.extend([f"{key}:", f" {value}"])
            summary.add_row(*cells)

        lanes = Table(expand=True, box=box.ASCII)
        lanes.add_column("Lane", width=9, no_wrap=True)
        lanes.add_column("Workers", width=7, justify="right", no_wrap=True)
        lanes.add_column("Queue", width=7, justify="right", no_wrap=True)
        lanes.add_column("Run", width=5, justify="right", no_wrap=True)
        lanes.add_column("Done", width=8, justify="right", no_wrap=True)
        lanes.add_column("Rows", width=12, justify="right", no_wrap=True)
        lanes.add_column("Bytes", width=11, justify="right", no_wrap=True)
        lanes.add_column("Seconds", width=8, justify="right", no_wrap=True)
        lanes.add_column("Active", overflow="ellipsis", ratio=2, no_wrap=True)
        for lane in self.stats.lanes.values():
            lanes.add_row(lane.name, str(lane.workers), str(lane.queued), str(lane.running), str(lane.done), f"{lane.rows:,}", _format_bytes(lane.bytes), f"{lane.seconds:.1f}", "; ".join(list(lane.active.values())[:4]))

        workers = Table(expand=True, box=box.ASCII)
        workers.add_column("W", width=4, no_wrap=True)
        workers.add_column("Month", width=8, no_wrap=True)
        workers.add_column("Ticker", width=8, no_wrap=True)
        workers.add_column("Stage", width=9, no_wrap=True)
        workers.add_column("Events", ratio=1, no_wrap=True)
        workers.add_column("Ctx", ratio=1, no_wrap=True)
        workers.add_column("Lbl", ratio=1, no_wrap=True)
        workers.add_column("CPU", ratio=1, no_wrap=True)
        workers.add_column("Write", ratio=1, no_wrap=True)
        workers.add_column("Seconds", width=8, no_wrap=True)
        workers.add_column("Message", overflow="ellipsis", ratio=2, no_wrap=True)
        worker_rows = [self.stats.workers[idx] for idx in sorted(self.stats.workers)[:12]]
        while len(worker_rows) < 12:
            worker_rows.append(PackageState(worker_id=len(worker_rows)))
        for worker in worker_rows:
            elapsed_worker = time.perf_counter() - worker.started_at if worker.started_at else worker.seconds
            worker.seconds = elapsed_worker
            workers.add_row(
                str(worker.worker_id),
                worker.month or "-",
                worker.ticker or "-",
                worker.stage or worker.status,
                _progress_text(worker.events_done, worker.events_total, elapsed_worker, Text=Text),
                _progress_text(worker.context_done, worker.context_total, elapsed_worker, Text=Text),
                _progress_text(worker.labels_done, worker.labels_total, elapsed_worker, Text=Text),
                _progress_text(worker.cpu_done, worker.cpu_total, elapsed_worker, Text=Text),
                _progress_text(worker.write_done, worker.write_total, elapsed_worker, Text=Text),
                f"{elapsed_worker:.1f}",
                worker.message,
            )

        messages = Table.grid(expand=True)
        message_rows = list(self.stats.messages[-8:])
        while len(message_rows) < 8:
            message_rows.append("")
        for message in message_rows:
            messages.add_row(message)
        return Group(
            Panel(summary, title="Ticker/Month Rolling Cache", box=box.ASCII),
            Panel(lanes, title="Concurrent Lanes", box=box.ASCII),
            Panel(workers, title="Package Workers", box=box.ASCII),
            Panel(messages, title="Messages", box=box.ASCII),
        )


def _progress_text(done: int, total: int, elapsed_seconds: float, *, Text: Any) -> Any:
    if int(total) <= 0:
        return Text("-", style="dim")
    safe_total = max(1, int(total))
    safe_done = min(max(0, int(done)), safe_total)
    pct = 100.0 * safe_done / safe_total
    bar_width = 8
    filled = min(bar_width, int(round((pct / 100.0) * bar_width)))
    bar = "#" * filled + "-" * (bar_width - filled)
    rate = safe_done / max(float(elapsed_seconds), 1e-9)
    eta = (safe_total - safe_done) / rate if safe_done > 0 and rate > 0 and safe_done < safe_total else None
    return Text(f"[{bar}] {safe_done:,}/{safe_total:,} {pct:3.0f}% eta {_format_duration(eta)}", style="green" if safe_done >= safe_total else "cyan")


if __name__ == "__main__":
    raise SystemExit(main())
