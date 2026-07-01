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
from urllib.parse import urlencode, urlparse

import numpy as np

if __package__ in {None, ""}:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "research").is_dir():
            sys.path.insert(0, str(parent))
            break

from research.mlops.clickhouse import (
    default_storage_policy,
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
    BAR_START_TIME_FEATURE_COLUMNS,
    CONTEXT_AVAILABLE_TIME_FEATURE_COLUMNS,
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
    read_json,
    ticker_package_dir,
    timestamp_us_to_utc,
    write_json_atomic,
)
from pipelines.market_sip.events.clickhouse_build_training_category_reference import (
    create_reference_table_sql,
    insert_reference_sql,
    query_settings as category_reference_query_settings,
)


DEFAULTS: dict[str, Any] = {
    "database": "market_sip_compact",
    "sec_context_database": "market_sip_compact",
    "events_table": "events",
    "condition_token_reference_table": "event_condition_token_reference",
    "macro_bars_table": "macro_bars_by_time_symbol",
    "news_token_table": "news_text_tokens",
    "sec_filing_text_token_table": "sec_filing_text_tokens",
    "news_embedding_table": "news_text_embeddings",
    "sec_filing_text_embedding_table": "sec_filing_text_embeddings",
    "sec_xbrl_context_table": "sec_xbrl_context",
    "category_reference_table": "training_category_reference",
    "cache_root": str(DEFAULT_TICKER_MONTH_CACHE_ROOT),
    "split": "train",
    "workers": 64,
    "event_fetch_workers": 6,
    "context_fetch_workers": 16,
    "label_fetch_workers": 6,
    "cpu_workers": 16,
    "write_workers": 8,
    "audit_workers": 2,
    "inline_audit_samples_per_part": 2,
    "max_inflight_packages": 96,
    "max_origin_events_per_part": 500_000,
    "max_cached_event_lookback_rows": 8_192,
    "clickhouse_query_retries": 2,
    "clickhouse_query_retry_backoff_seconds": 2.0,
    "events_per_chunk": 128,
    "short_context_chunks": 32,
    "context_chunk_stride_events": 64,
    "short_context_stride_chunks": 1,
    "long_context_lags": "",
    "sample_stride_events": 1,
    "max_threads": 8,
    "max_memory_usage": "120G",
    "macro_lookback_days": 400,
    "label_lookahead_days": 400,
    "news_lookback_days": 30,
    "sec_lookback_days": 365,
    "xbrl_lookback_days": 730,
    "ticker_news_items": 8,
    "market_news_items": 16,
    "sec_filing_items": 4,
    "ticker_news_prior_items": 64,
    "market_news_prior_items": 512,
    "sec_filing_prior_items": 32,
    "xbrl_items": 4096,
    "xbrl_prior_rows": 4096,
    "intraday_label_horizons": "100ms,250ms,500ms,750ms,1s,5s,10s,30s,60s,120s,180s,300s,600s,1200s,1800s,3600s,7200s,3h,4h,5h",
    "refresh_seconds": 1.0,
    "profile_slow_seconds": 10.0,
}

SESSION_START_SECOND = 4 * 60 * 60
SESSION_END_SECOND = 20 * 60 * 60

FUTURE_CONDITION_GROUPS: tuple[tuple[str, tuple[tuple[str, tuple[int, ...]], ...]], ...] = (
    (
        "condition_halt_pause_flag",
        (
            ("cta_security_status", (102, 114, 117)),
            ("halt_reason", (153, 154, 155, 156, 157, 158, 159, 160, 161, 163, 165, 166, 168, 184, 186)),
            ("quote_conditions", (43,)),
            ("luld_indicators", (17,)),
        ),
    ),
    (
        "condition_resume_flag",
        (
            ("cta_security_status", (103,)),
            ("halt_reason", (169, 170, 171, 172, 173, 174, 178)),
            ("quote_conditions", (16,)),
        ),
    ),
    (
        "condition_news_risk_flag",
        (
            ("halt_reason", (151,)),
            ("quote_conditions", (25, 27)),
            ("halt_reason", (152, 167)),
            ("quote_conditions", (21, 23)),
        ),
    ),
    (
        "condition_luld_limit_state_flag",
        (
            ("cta_security_status", (114,)),
            ("halt_reason", (153, 165, 166, 186)),
            ("quote_conditions", (35, 39, 43)),
            ("luld_indicators", (11, 12, 22, 23, 24, 25, 26, 27, 28, 29, 30)),
        ),
    ),
)
FUTURE_CONDITION_LABEL_KEYS: tuple[str, ...] = tuple(name for name, _ in FUTURE_CONDITION_GROUPS)
FUTURE_EXTERNAL_ARRIVAL_LABEL_KEYS: tuple[str, ...] = ("ticker_news_arrival_flag", "sec_filing_arrival_flag")
FUTURE_EVENT_FLAG_LABEL_KEYS: tuple[str, ...] = (*FUTURE_CONDITION_LABEL_KEYS, *FUTURE_EXTERNAL_ARRIVAL_LABEL_KEYS)
CONDITION_INDICATOR_SOURCE_FAMILIES: frozenset[str] = frozenset({"cta_security_status", "halt_reason", "luld_indicators"})
CONDITION_DIRECT_SOURCE_FAMILIES: frozenset[str] = frozenset({"quote_conditions", "trade_conditions", "trade_corrections_nyse", "unknown"})

NEWS_EMBEDDING_COLUMNS: tuple[str, ...] = tuple(
    column for column in NEWS_TOKEN_COLUMNS if column not in {"input_ids", "attention_mask"}
) + ("embedding_model", "embedding_pooling", "embedding_dtype", "embedding_dim", "embedding")
SEC_EMBEDDING_COLUMNS: tuple[str, ...] = tuple(
    column for column in SEC_TOKEN_COLUMNS if column not in {"input_ids", "attention_mask"}
) + ("embedding_model", "embedding_pooling", "embedding_dtype", "embedding_dim", "embedding")


def _available_time_feature_sql(timestamp_expr: str, *, prefix: str = "available") -> str:
    ts = str(timestamp_expr)
    return f"""
    toFloat32(sin(2 * pi() * dateDiff('second', toStartOfDay(fromUnixTimestamp64Micro({ts}, 'UTC')), fromUnixTimestamp64Micro({ts}, 'UTC')) / 86400.0)) AS {quote_ident(prefix + "_utc_second_of_day_sin")},
    toFloat32(cos(2 * pi() * dateDiff('second', toStartOfDay(fromUnixTimestamp64Micro({ts}, 'UTC')), fromUnixTimestamp64Micro({ts}, 'UTC')) / 86400.0)) AS {quote_ident(prefix + "_utc_second_of_day_cos")},
    toFloat32(sin(2 * pi() * (toDayOfWeek(fromUnixTimestamp64Micro({ts}, 'UTC')) - 1) / 7.0)) AS {quote_ident(prefix + "_utc_day_of_week_sin")},
    toFloat32(cos(2 * pi() * (toDayOfWeek(fromUnixTimestamp64Micro({ts}, 'UTC')) - 1) / 7.0)) AS {quote_ident(prefix + "_utc_day_of_week_cos")},
    toFloat32(sin(2 * pi() * (toDayOfYear(fromUnixTimestamp64Micro({ts}, 'UTC')) - 1) / 366.0)) AS {quote_ident(prefix + "_utc_day_of_year_sin")},
    toFloat32(cos(2 * pi() * (toDayOfYear(fromUnixTimestamp64Micro({ts}, 'UTC')) - 1) / 366.0)) AS {quote_ident(prefix + "_utc_day_of_year_cos")},
    toFloat32(toYear(fromUnixTimestamp64Micro({ts}, 'UTC')) - 2000 + (toDayOfYear(fromUnixTimestamp64Micro({ts}, 'UTC')) - 1) / 366.0) AS {quote_ident(prefix + "_years_since_2000")}""".strip()


def _bar_start_time_feature_sql(timestamp_expr: str) -> str:
    ts = str(timestamp_expr)
    return f"""
    toFloat32(sin(2 * pi() * dateDiff('second', toStartOfDay({ts}), {ts}) / 86400.0)) AS bar_start_utc_second_of_day_sin,
    toFloat32(cos(2 * pi() * dateDiff('second', toStartOfDay({ts}), {ts}) / 86400.0)) AS bar_start_utc_second_of_day_cos,
    toFloat32(sin(2 * pi() * (toDayOfWeek({ts}) - 1) / 7.0)) AS bar_start_utc_day_of_week_sin,
    toFloat32(cos(2 * pi() * (toDayOfWeek({ts}) - 1) / 7.0)) AS bar_start_utc_day_of_week_cos,
    toFloat32(sin(2 * pi() * (toDayOfYear({ts}) - 1) / 366.0)) AS bar_start_utc_day_of_year_sin,
    toFloat32(cos(2 * pi() * (toDayOfYear({ts}) - 1) / 366.0)) AS bar_start_utc_day_of_year_cos,
    toFloat32(toYear({ts}) - 2000 + (toDayOfYear({ts}) - 1) / 366.0) AS bar_start_years_since_2000""".strip()


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
QUERY_CONTEXT = threading.local()


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

    def start_package(self, *, month: str, ticker: str) -> None:
        self.month = month
        self.ticker = ticker
        self.status = "running"
        self.stage = "query"
        self.events_done = 0
        self.events_total = 0
        self.labels_done = 0
        self.labels_total = 0
        self.context_done = 0
        self.context_total = 0
        self.cpu_done = 0
        self.cpu_total = 0
        self.write_done = 0
        self.write_total = 0
        self.started_at = time.perf_counter()
        self.seconds = 0.0
        self.message = "submitting package tasks"


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
            QUERY_CONTEXT.label = label
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
            finally:
                QUERY_CONTEXT.label = ""
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
    parser.add_argument("--condition-token-reference-table", default=DEFAULTS["condition_token_reference_table"])
    parser.add_argument("--macro-bars-table", default=DEFAULTS["macro_bars_table"])
    parser.add_argument("--news-token-table", default=DEFAULTS["news_token_table"])
    parser.add_argument("--sec-filing-text-token-table", default=DEFAULTS["sec_filing_text_token_table"])
    parser.add_argument("--news-embedding-table", default=DEFAULTS["news_embedding_table"])
    parser.add_argument("--sec-filing-text-embedding-table", default=DEFAULTS["sec_filing_text_embedding_table"])
    parser.add_argument("--sec-xbrl-context-table", default=DEFAULTS["sec_xbrl_context_table"])
    parser.add_argument("--category-reference-table", default=DEFAULTS["category_reference_table"])
    parser.add_argument("--force-category-reference-build", action="store_true", help="Run the append-only category reference builder at startup even when the table already exists.")
    parser.add_argument("--skip-category-reference-check", action="store_true", help="Skip the startup category reference existence/empty-table check.")
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
    parser.add_argument("--inline-audit-samples-per-part", type=int, default=DEFAULTS["inline_audit_samples_per_part"], help="Cheap in-memory part checks before writing. Set 0 to disable.")
    parser.add_argument("--max-inflight-packages", type=int, default=DEFAULTS["max_inflight_packages"])
    parser.add_argument("--max-origin-events-per-part", type=int, default=DEFAULTS["max_origin_events_per_part"], help="Maximum origin ordinal span per physical part inside a ticker-month package.")
    parser.add_argument("--max-cached-event-lookback-rows", type=int, default=DEFAULTS["max_cached_event_lookback_rows"], help="Maximum prior raw event rows stored before each part's first origin so future loaders can choose coverage at load time.")
    parser.add_argument("--clickhouse-query-retries", type=int, default=DEFAULTS["clickhouse_query_retries"], help="Retries for transient ClickHouse HTTP read failures while fetching SELECT Arrow results.")
    parser.add_argument("--clickhouse-query-retry-backoff-seconds", type=float, default=DEFAULTS["clickhouse_query_retry_backoff_seconds"], help="Base backoff between transient ClickHouse query retries.")
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
    parser.add_argument("--ticker-news-prior-items", type=int, default=DEFAULTS["ticker_news_prior_items"], help="Logical ticker-news items saved before month start for as-of context.")
    parser.add_argument("--market-news-prior-items", type=int, default=DEFAULTS["market_news_prior_items"], help="Logical global news items saved before month start for as-of market context.")
    parser.add_argument("--sec-filing-prior-items", type=int, default=DEFAULTS["sec_filing_prior_items"], help="Logical SEC filing text items saved before month start for as-of context.")
    parser.add_argument("--xbrl-prior-rows", type=int, default=DEFAULTS["xbrl_prior_rows"], help="XBRL fact rows saved before month start for as-of context.")
    parser.add_argument("--intraday-label-horizons", default=DEFAULTS["intraday_label_horizons"])
    parser.add_argument("--skip-token-contexts", action="store_true", help="Skip text embedding context fetches. Name kept for compatibility with older token-cache builds.")
    parser.add_argument("--skip-xbrl", action="store_true")
    parser.add_argument("--refresh-context-only", action="store_true", help="Refresh only text embedding, XBRL, and XBRL category context files for existing ticker/month packages.")
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
        worker_slots = max(1, int(args.workers), int(args.max_inflight_packages))
        stats.workers = {idx: PackageState(worker_id=idx) for idx in range(worker_slots)}
        lanes = LaneExecutors(args, stats)
        dashboard = TickerMonthDashboard(enabled=not args.no_rich, live=not args.plain_status, refresh_seconds=args.refresh_seconds, stats=stats)

        def request_stop(signum: int, _frame: Any) -> None:
            stop_event.set()
            stats.stop_requested = True
            stats.interrupted = True
            stats.phase = "stopping"
            interrupt_message = "Ctrl+C received; stopping after cancelling active ClickHouse queries and queued work"
            stats.message(interrupt_message)
            try:
                original_stderr.write("\n" + interrupt_message + "\n")
                original_stderr.flush()
            except Exception:
                pass
            if dashboard is not None:
                try:
                    dashboard.refresh(force=True)
                except Exception as exc:
                    stats.log_event("interrupt_dashboard_refresh_failed", error=repr(exc))
            cancel_active_clickhouse_queries(client_opts=client_opts, stats=stats)
            raise KeyboardInterrupt

        signal.signal(signal.SIGINT, request_stop)
        dashboard.start()
        heartbeat = ProgressHeartbeat(cache_root=cache_root, stats=stats, dashboard=dashboard, refresh_seconds=args.refresh_seconds)
        heartbeat.start()
        ensure_category_reference_table(args=args, client_opts=client_opts, config=config, stats=stats)
        for month in months:
            if stop_event.is_set():
                break
            stats.phase = f"month {month}"
            month_dir = month_dir_for(cache_root, args.split, month)
            month_dir.mkdir(parents=True, exist_ok=True)
            window = month_window(month)
            month_tickers = _resolve_refresh_tickers(args, cache_root, month) if args.refresh_context_only else _resolve_tickers_for_month(args, client_opts, config, window)
            stats.packages_total += len(month_tickers)
            stats.message(f"{month}: tickers={len(month_tickers):,}" + (" context-refresh-only" if args.refresh_context_only else ""))
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
                    worker_id = next_worker % max(1, len(stats.workers))
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
                    sec_context_database=args.sec_context_database,
                    events_table=args.events_table,
                    condition_token_reference_table=args.condition_token_reference_table,
                    news_embedding_table=args.news_embedding_table,
                    sec_filing_text_embedding_table=args.sec_filing_text_embedding_table,
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
        _cancel_active_work_with_grace(client_opts=client_opts, stats=stats, dashboard=dashboard, cache_root=cache_root, reason="user interrupt")
        stats.message("interrupted by user; active ClickHouse queries were cancelled; completed package directories remain usable")
        if manifest:
            write_json_atomic(cache_root / "manifest.json", manifest | {"status": "interrupted", "summary": _summary(stats)})
        return 130
    except BaseException as exc:
        stop_event.set()
        stats.stop_requested = True
        stats.phase = "error"
        stats.log_error("main", exc)
        stats.message(f"fatal error received; cancelling active ClickHouse queries and queued work: {exc!r}")
        _cancel_active_work_with_grace(client_opts=client_opts, stats=stats, dashboard=dashboard, cache_root=cache_root, reason="fatal error")
        if manifest:
            write_json_atomic(cache_root / "manifest.json", manifest | {"status": "error", "error": repr(exc), "summary": _summary(stats)})
        return 1
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
        "query_retries": str(max(0, int(args.clickhouse_query_retries))),
        "query_retry_backoff_seconds": str(max(0.0, float(args.clickhouse_query_retry_backoff_seconds))),
    }


def ensure_category_reference_table(
    *,
    args: argparse.Namespace,
    client_opts: Mapping[str, str],
    config: RollingMarketDataConfig,
    stats: BuildStats,
) -> None:
    if bool(getattr(args, "skip_category_reference_check", False)):
        stats.message("category_reference: startup check skipped")
        stats.log_event("category_reference_check_skipped")
        return
    started = time.perf_counter()
    exists, rows = _category_reference_table_status(client_opts=client_opts, config=config)
    force = bool(getattr(args, "force_category_reference_build", False))
    if exists and rows > 0 and not force:
        stats.message(f"category_reference: ready table={config.sec_context_database}.{config.category_reference_table} rows={rows:,}")
        stats.log_event("category_reference_ready", table=config.category_reference_table, rows=rows, elapsed_seconds=time.perf_counter() - started)
        return

    reason = "forced" if force else ("empty" if exists else "missing")
    stats.message(f"category_reference: {reason}; running append-only builder")
    stats.log_event("category_reference_build_started", table=config.category_reference_table, exists=exists, rows=rows, reason=reason)
    category_args = argparse.Namespace(
        database=config.sec_context_database,
        xbrl_table=config.sec_xbrl_context_table,
        news_token_table=config.news_token_table,
        sec_token_table=config.sec_filing_text_token_table,
        reference_table=config.category_reference_table,
        max_threads=max(1, int(config.max_threads)),
        max_memory_usage=str(config.max_memory_usage),
    )
    storage_policy = default_storage_policy()
    create_sql = create_reference_table_sql(config.sec_context_database, config.category_reference_table, storage_policy)
    insert_sql = insert_reference_sql(category_args).rstrip(";") + category_reference_query_settings(category_args)
    _execute_clickhouse_sql(client_opts=client_opts, sql=create_sql, label="category_reference_create")
    _execute_clickhouse_sql(client_opts=client_opts, sql=insert_sql, label="category_reference_insert")
    final_exists, final_rows = _category_reference_table_status(client_opts=client_opts, config=config)
    if not final_exists or final_rows <= 0:
        raise RuntimeError(f"Category reference table build did not produce rows: {config.sec_context_database}.{config.category_reference_table}")
    stats.message(f"category_reference: built table={config.sec_context_database}.{config.category_reference_table} rows={final_rows:,}")
    stats.log_event("category_reference_build_done", table=config.category_reference_table, rows=final_rows, elapsed_seconds=time.perf_counter() - started)


def _category_reference_table_status(*, client_opts: Mapping[str, str], config: RollingMarketDataConfig) -> tuple[bool, int]:
    exists_sql = f"""
SELECT count()
FROM system.tables
WHERE database = {sql_string(config.sec_context_database)}
  AND name = {sql_string(config.category_reference_table)}
FORMAT TSV
"""
    exists_text = _execute_clickhouse_sql(client_opts=client_opts, sql=exists_sql, label="category_reference_exists")
    exists = _parse_clickhouse_count(exists_text) > 0
    if not exists:
        return False, 0
    count_sql = f"SELECT count() FROM {quote_ident(config.sec_context_database)}.{quote_ident(config.category_reference_table)} FORMAT TSV"
    rows_text = _execute_clickhouse_sql(client_opts=client_opts, sql=count_sql, label="category_reference_count")
    return True, _parse_clickhouse_count(rows_text)


def _parse_clickhouse_count(text: str) -> int:
    first = next((line.strip() for line in str(text).splitlines() if line.strip()), "0")
    try:
        return int(first)
    except ValueError as exc:
        raise RuntimeError(f"Could not parse ClickHouse count result: {text!r}") from exc


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


def _resolve_refresh_tickers(args: argparse.Namespace, cache_root: Path, month: str) -> list[str]:
    if args.tickers:
        tickers = sorted({item.strip().upper() for item in args.tickers.split(",") if item.strip()})
        return tickers[: int(args.ticker_limit)] if int(args.ticker_limit) > 0 else tickers
    month_dir = month_dir_for(cache_root, args.split, month)
    tickers = sorted({path.name.split("=", 1)[1].upper() for path in month_dir.glob("ticker_hash=*/ticker=*") if path.is_dir() and path.name.startswith("ticker=")})
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
    if args.refresh_context_only:
        _refresh_global_context_package(args=args, client_opts=client_opts, config=config, month=month, window=window, final_dir=final_dir, lanes=lanes, stats=stats, stop_event=stop_event)
        return
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
        lanes.submit("write", f"{month}:write_market_news", lambda frame=outputs["market_news"]: _write_parquet(frame, tmp_dir / "market_news_embeddings.parquet")),
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
                "market_news_embeddings": "market_news_embeddings.parquet",
                "global_daily_bars": "global_daily_bars.parquet",
                "category_references": "category_references.parquet",
            },
            "time_feature_columns": {
                "market_news_embeddings": list(CONTEXT_AVAILABLE_TIME_FEATURE_COLUMNS),
                "global_daily_bars": list(BAR_START_TIME_FEATURE_COLUMNS),
            },
            "counts": {key: int(value.height) for key, value in outputs.items()},
            "completed_at": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
        },
    )
    replace_complete_dir(tmp_dir, final_dir, resume=True)


def _refresh_global_context_package(
    *,
    args: argparse.Namespace,
    client_opts: Mapping[str, str],
    config: RollingMarketDataConfig,
    month: str,
    window: Any,
    final_dir: Path,
    lanes: LaneExecutors,
    stats: BuildStats,
    stop_event: threading.Event,
) -> None:
    if not final_dir.exists():
        stats.message(f"{month}: global package missing; cannot refresh global text/XBRL context")
        return
    manifest_path = final_dir / "manifest.json"
    manifest = read_json(manifest_path) if manifest_path.exists() else {}
    futures = {
        "market_news": lanes.submit("context", f"{month}:refresh_global_market_news", lambda: _query_market_news(args, client_opts, config, window)),
        "categories": lanes.submit("context", f"{month}:refresh_categories", lambda: _query_category_references(args, client_opts, config)),
    }
    outputs = {name: future.result() for name, future in futures.items()}
    if stop_event.is_set():
        raise KeyboardInterrupt
    writes = {
        "market_news_embeddings": lanes.submit("write", f"{month}:refresh_write_market_news", lambda: _write_parquet(outputs["market_news"], final_dir / "market_news_embeddings.parquet")),
        "category_references": lanes.submit("write", f"{month}:refresh_write_categories", lambda: _write_parquet(outputs["categories"], final_dir / "category_references.parquet")),
    }
    write_results = {name: future.result() for name, future in writes.items()}
    counts = dict(manifest.get("counts") or {})
    counts["market_news"] = int(outputs["market_news"].height)
    counts["categories"] = int(outputs["categories"].height)
    files = dict(manifest.get("files") or {})
    files.pop("market_news_tokens", None)
    files["market_news_embeddings"] = "market_news_embeddings.parquet"
    files["category_references"] = "category_references.parquet"
    time_feature_columns = dict(manifest.get("time_feature_columns") or {})
    time_feature_columns["market_news_embeddings"] = list(CONTEXT_AVAILABLE_TIME_FEATURE_COLUMNS)
    manifest.update(
        {
            "format": TICKER_MONTH_CACHE_FORMAT,
            "version": TICKER_MONTH_CACHE_VERSION,
            "status": "complete",
            "month": month,
            "window": month_window_dict(window),
            "files": files,
            "counts": counts,
            "time_feature_columns": time_feature_columns,
            "context_refresh": _context_refresh_metadata(args),
            "context_refreshed_at": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
        }
    )
    write_json_atomic(manifest_path, manifest)
    stats.bytes_written += sum(_byte_count(result) for result in write_results.values())
    stats.message(f"{month}: refreshed global market_news={int(outputs['market_news'].height):,} categories={int(outputs['categories'].height):,}")


def _refresh_ticker_month_context_package(
    args: argparse.Namespace,
    client_opts: Mapping[str, str],
    config: RollingMarketDataConfig,
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
    state.start_package(month=month, ticker=ticker)
    state.stage = "refresh-context"
    package_dir = ticker_package_dir(month_dir_for(cache_root, args.split, month), ticker)
    manifest_path = package_dir / "manifest.json"
    if not package_dir.exists() or not manifest_path.exists():
        state.status = "missing"
        state.stage = "missing"
        state.message = "existing package missing"
        return TickerMonthResult(month=month, ticker=ticker, package_dir=package_dir, status="missing", byte_count=0)
    manifest = read_json(manifest_path)
    if manifest.get("status") != "complete":
        state.status = "skipped"
        state.stage = "skipped"
        state.message = f"package status={manifest.get('status')!r}"
        return TickerMonthResult(month=month, ticker=ticker, package_dir=package_dir, status="skipped", byte_count=directory_size(package_dir))
    futures: dict[str, Future[Any]] = {
        "ticker_news": lanes.submit("context", f"{month}:{ticker}:refresh_ticker_news", lambda: _query_ticker_news(args, client_opts, config, window, ticker)),
        "sec_filings": lanes.submit("context", f"{month}:{ticker}:refresh_sec", lambda: _query_sec_tokens(args, client_opts, config, window, ticker)),
    }
    if not args.skip_xbrl:
        futures["xbrl"] = lanes.submit("context", f"{month}:{ticker}:refresh_xbrl", lambda: _query_xbrl(args, client_opts, config, window, ticker))
    else:
        futures["xbrl"] = lanes.submit("context", f"{month}:{ticker}:refresh_xbrl_empty", lambda: _empty_frame())
    state.context_total = 3
    outputs = {}
    for name, future in futures.items():
        outputs[name] = future.result()
        state.context_done += 1
    if stop_event.is_set():
        raise KeyboardInterrupt
    state.stage = "write-context"
    writes = {
        "ticker_news_embeddings": lanes.submit("write", f"{month}:{ticker}:refresh_write_news", lambda: _write_parquet(outputs["ticker_news"], package_dir / "ticker_news_embeddings.parquet")),
        "sec_filing_embeddings": lanes.submit("write", f"{month}:{ticker}:refresh_write_sec", lambda: _write_parquet(outputs["sec_filings"], package_dir / "sec_filing_embeddings.parquet")),
        "xbrl": lanes.submit("write", f"{month}:{ticker}:refresh_write_xbrl", lambda: _write_parquet(outputs["xbrl"], package_dir / "xbrl.parquet")),
    }
    state.write_total = len(writes)
    write_results = {}
    for name, future in writes.items():
        write_results[name] = future.result()
        state.write_done += 1
    counts = dict(manifest.get("counts") or {})
    counts.pop("ticker_news_tokens", None)
    counts.pop("sec_filing_tokens", None)
    counts["ticker_news_embeddings"] = int(outputs["ticker_news"].height)
    counts["sec_filing_embeddings"] = int(outputs["sec_filings"].height)
    counts["xbrl"] = int(outputs["xbrl"].height)
    files = dict(manifest.get("files") or {})
    files.pop("ticker_news_tokens", None)
    files.pop("sec_filing_tokens", None)
    files["ticker_news_embeddings"] = "ticker_news_embeddings.parquet"
    files["sec_filing_embeddings"] = "sec_filing_embeddings.parquet"
    files["xbrl"] = "xbrl.parquet"
    package_config = dict(manifest.get("config") or {})
    package_config.update(_context_refresh_metadata(args))
    package_config["context_available_time_feature_columns"] = list(CONTEXT_AVAILABLE_TIME_FEATURE_COLUMNS)
    package_config["bar_start_time_feature_columns"] = list(BAR_START_TIME_FEATURE_COLUMNS)
    time_feature_columns = dict(manifest.get("time_feature_columns") or {})
    time_feature_columns["ticker_news_embeddings"] = list(CONTEXT_AVAILABLE_TIME_FEATURE_COLUMNS)
    time_feature_columns["sec_filing_embeddings"] = list(CONTEXT_AVAILABLE_TIME_FEATURE_COLUMNS)
    time_feature_columns["xbrl"] = list(CONTEXT_AVAILABLE_TIME_FEATURE_COLUMNS)
    manifest.update(
        {
            "status": "complete",
            "files": files,
            "counts": counts,
            "config": package_config,
            "time_feature_columns": time_feature_columns,
            "context_refresh": _context_refresh_metadata(args),
            "context_refreshed_at": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
        }
    )
    write_json_atomic(manifest_path, manifest)
    state.status = "done"
    state.stage = "done"
    state.seconds = time.perf_counter() - state.started_at
    state.message = f"context refreshed news={counts['ticker_news_embeddings']:,} sec={counts['sec_filing_embeddings']:,} xbrl={counts['xbrl']:,}"
    return TickerMonthResult(
        month=month,
        ticker=ticker,
        package_dir=package_dir,
        status="context_refreshed",
        event_count=0,
        origin_count=0,
        label_rows=0,
        byte_count=sum(_byte_count(result) for result in write_results.values()),
    )


def _context_refresh_metadata(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "ticker_news_prior_items": max(0, int(args.ticker_news_prior_items)),
        "market_news_prior_items": max(0, int(args.market_news_prior_items)),
        "sec_filing_prior_items": max(0, int(args.sec_filing_prior_items)),
        "xbrl_prior_rows": max(0, int(args.xbrl_prior_rows)),
        "xbrl_items": max(0, int(args.xbrl_items)),
        "context_fetch_mode": "month_plus_latest_prior_embedding_items",
        "news_embedding_table": str(args.news_embedding_table),
        "sec_filing_text_embedding_table": str(args.sec_filing_text_embedding_table),
    }


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
    state.start_package(month=month, ticker=ticker)
    package_dir = ticker_package_dir(month_dir_for(cache_root, args.split, month), ticker)
    if args.refresh_context_only:
        return _refresh_ticker_month_context_package(args, client_opts, config, cache_root, month, window, ticker, worker_id, stats, lanes, stop_event)
    if package_dir.exists() and args.resume:
        state.status = "done"
        state.stage = "exists"
        state.message = "already exists"
        return TickerMonthResult(month=month, ticker=ticker, package_dir=package_dir, status="exists", byte_count=directory_size(package_dir))
    tmp_dir = package_dir.with_name(package_dir.name + ".tmp")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    try:
        default_required_lookback = required_event_lookback_rows(context_lags, args.events_per_chunk)
        max_cached_event_lookback = max(int(default_required_lookback), max(0, int(args.max_cached_event_lookback_rows)))
        origin_bounds = _query_origin_bounds(args, client_opts, config, window, ticker)
        parts = _origin_ordinal_parts(origin_bounds, fetch_lookback_rows=max_cached_event_lookback, max_origin_events_per_part=args.max_origin_events_per_part)
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
        state.events_total = len(parts)
        state.labels_total = len(parts)
        state.cpu_total = len(parts)
        state.write_total = len(parts) * 5 + state.context_total
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
            state.stage = f"labels {part.part_id + 1}/{len(parts)}"
            labels = labels_future.result()
            state.labels_done += 1
            labels, labels_filtered_out = _align_labels_to_origins(
                labels,
                origins,
                prefix=f"{month}:{ticker}:{part_name}",
            )
            audit_samples = _light_audit_part_in_memory(
                events=events,
                origins=origins,
                windows=windows,
                labels=labels,
                context_lags=context_lags,
                events_per_chunk=int(args.events_per_chunk),
                horizon_count=len(parse_horizons(args.intraday_label_horizons)),
                samples_per_part=max(0, int(args.inline_audit_samples_per_part)),
                month=month,
                ticker=ticker,
                part=part,
            )
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
                        "labels_filtered_out": int(labels_filtered_out),
                        "inline_audit_samples": int(audit_samples),
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
            "ticker_news": lanes.submit("write", f"{month}:{ticker}:write_news", lambda: _write_parquet(ticker_news, tmp_dir / "ticker_news_embeddings.parquet")),
            "sec_filings": lanes.submit("write", f"{month}:{ticker}:write_sec", lambda: _write_parquet(sec_filings, tmp_dir / "sec_filing_embeddings.parquet")),
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
                "context_fetch_mode": "month_plus_latest_prior_embedding_items",
                "news_embedding_table": str(args.news_embedding_table),
                "sec_filing_text_embedding_table": str(args.sec_filing_text_embedding_table),
                "ticker_news_prior_items": int(args.ticker_news_prior_items),
                "market_news_prior_items": int(args.market_news_prior_items),
                "sec_filing_prior_items": int(args.sec_filing_prior_items),
                "xbrl_prior_rows": int(args.xbrl_prior_rows),
                "xbrl_items": int(args.xbrl_items),
                "required_event_lookback_rows": int(default_required_lookback),
                "default_required_event_lookback_rows": int(default_required_lookback),
                "max_cached_event_lookback_rows": int(max_cached_event_lookback),
                "default_event_window_index": True,
                "max_origin_events_per_part": int(args.max_origin_events_per_part),
                "intraday_label_horizons": [h.name for h in parse_horizons(args.intraday_label_horizons)],
                "future_condition_label_keys": list(FUTURE_CONDITION_LABEL_KEYS),
                "future_external_arrival_label_keys": list(FUTURE_EXTERNAL_ARRIVAL_LABEL_KEYS),
                "future_event_flag_label_keys": list(FUTURE_EVENT_FLAG_LABEL_KEYS),
                "event_payload_columns": list(EVENT_PAYLOAD_COLUMNS),
                "event_time_feature_columns": list(EVENT_TIME_FEATURE_COLUMNS),
                "context_available_time_feature_columns": list(CONTEXT_AVAILABLE_TIME_FEATURE_COLUMNS),
                "bar_start_time_feature_columns": list(BAR_START_TIME_FEATURE_COLUMNS),
            },
            "counts": {
                "parts": int(len(part_manifests)),
                "events": int(total_events),
                "origins": int(total_origins),
                "event_windows": int(total_windows),
                "intraday_forward_labels": int(total_labels),
                "ticker_news_embeddings": int(ticker_news.height),
                "sec_filing_embeddings": int(sec_filings.height),
                "xbrl": int(xbrl.height),
                "daily_bars": int(daily_bars.height),
                "skipped_not_enough_history": int(skipped_history),
                "skipped_window_gap": int(skipped_gap),
            },
            "files": {
                "ticker_news_embeddings": "ticker_news_embeddings.parquet",
                "sec_filing_embeddings": "sec_filing_embeddings.parquet",
                "xbrl": "xbrl.parquet",
                "daily_bars": "daily_bars.parquet",
            },
            "time_feature_columns": {
                "ticker_news_embeddings": list(CONTEXT_AVAILABLE_TIME_FEATURE_COLUMNS),
                "sec_filing_embeddings": list(CONTEXT_AVAILABLE_TIME_FEATURE_COLUMNS),
                "xbrl": list(CONTEXT_AVAILABLE_TIME_FEATURE_COLUMNS),
                "daily_bars": list(BAR_START_TIME_FEATURE_COLUMNS),
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


def _origin_ordinal_parts(bounds: tuple[int, int] | None, *, fetch_lookback_rows: int, max_origin_events_per_part: int) -> list[OriginOrdinalPart]:
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
                fetch_ordinal_start=max(0, int(start) - int(fetch_lookback_rows)),
                fetch_ordinal_end=int(end),
            )
        )
        start = end + 1
    return parts


def _light_audit_part_in_memory(
    *,
    events: Any,
    origins: Any,
    windows: Any,
    labels: Any,
    context_lags: tuple[int, ...],
    events_per_chunk: int,
    horizon_count: int,
    samples_per_part: int,
    month: str,
    ticker: str,
    part: OriginOrdinalPart,
) -> int:
    samples = max(0, int(samples_per_part))
    if samples <= 0 or int(getattr(origins, "height", 0) or 0) == 0:
        return 0
    prefix = f"{month}:{ticker}:part_{part.part_id:05d}"
    if int(windows.height) != int(origins.height):
        raise RuntimeError(f"{prefix} inline audit failed: origins/windows row count mismatch {origins.height:,} != {windows.height:,}.")
    if int(events.height) <= 0:
        raise RuntimeError(f"{prefix} inline audit failed: origins exist but events are empty.")
    events_per_chunk = max(1, int(events_per_chunk))
    event_ordinals = events.get_column("ordinal").to_numpy().astype(np.int64, copy=False)
    event_timestamps = events.get_column("timestamp_us").to_numpy().astype(np.int64, copy=False)
    if "session_second" not in events.columns:
        raise RuntimeError(f"{prefix} inline audit failed: events are missing session_second.")
    session_seconds = events.get_column("session_second").to_numpy().astype(np.int64, copy=False)
    if event_ordinals.size > 1 and np.any(event_ordinals[1:] <= event_ordinals[:-1]):
        raise RuntimeError(f"{prefix} inline audit failed: events are not strictly increasing by ordinal.")
    origin_count = int(origins.height)
    sample_indexes = _deterministic_sample_indexes(origin_count, min(samples, origin_count), month=month, ticker=ticker, part_id=part.part_id)
    label_ordinals = None
    if int(horizon_count) > 0:
        if int(labels.height) <= 0:
            raise RuntimeError(f"{prefix} inline audit failed: labels are empty for {origin_count:,} origins.")
        label_ordinals = labels.get_column("origin_ordinal").to_numpy().astype(np.int64, copy=False)
    checked = 0
    lag0_index = list(context_lags).index(0) if 0 in context_lags else None
    for origin_index in sample_indexes:
        origin = origins.row(int(origin_index), named=True)
        origin_id = int(origin["origin_id"])
        origin_key = str(origin["origin_key"])
        origin_ordinal = int(origin["origin_ordinal"])
        origin_timestamp_us = int(origin["origin_timestamp_us"])
        event_row_offset = int(origin["event_row_offset"])
        if origin_ordinal < int(part.origin_ordinal_start) or origin_ordinal > int(part.origin_ordinal_end):
            raise RuntimeError(f"{prefix} inline audit failed: sampled origin {origin_ordinal:,} is outside part origin bounds.")
        if event_row_offset < 0 or event_row_offset >= int(event_ordinals.size):
            raise RuntimeError(f"{prefix} inline audit failed: sampled origin {origin_ordinal:,} event_row_offset is out of bounds.")
        if int(event_ordinals[event_row_offset]) != origin_ordinal or int(event_timestamps[event_row_offset]) != origin_timestamp_us:
            raise RuntimeError(f"{prefix} inline audit failed: sampled origin {origin_key} does not match event row offset.")
        origin_session_second = int(session_seconds[event_row_offset])
        if origin_session_second < SESSION_START_SECOND or origin_session_second >= SESSION_END_SECOND:
            raise RuntimeError(f"{prefix} inline audit failed: sampled origin {origin_key} is outside the active session.")
        window = windows.row(int(origin_index), named=True)
        if int(window.get("origin_id", -1)) != origin_id or str(window.get("origin_key", "")) != origin_key:
            raise RuntimeError(f"{prefix} inline audit failed: window row is not aligned with sampled origin {origin_key}.")
        for context_index in range(len(context_lags)):
            column = f"window_start_{context_index:03d}"
            start = int(window[column])
            end = start + events_per_chunk - 1
            if start < 0 or end >= int(event_ordinals.size):
                raise RuntimeError(f"{prefix} inline audit failed: {origin_key} {column} is out of event bounds.")
            if int(event_ordinals[end]) - int(event_ordinals[start]) != events_per_chunk - 1:
                raise RuntimeError(f"{prefix} inline audit failed: {origin_key} {column} crosses an ordinal gap.")
            if lag0_index is not None and context_index == lag0_index and int(event_ordinals[end]) != origin_ordinal:
                raise RuntimeError(f"{prefix} inline audit failed: {origin_key} current event window does not end at origin.")
        if label_ordinals is not None:
            left = int(np.searchsorted(label_ordinals, origin_ordinal, side="left"))
            right = int(np.searchsorted(label_ordinals, origin_ordinal, side="right"))
            if _labels_are_pivoted(labels):
                if right - left != 1:
                    raise RuntimeError(f"{prefix} inline audit failed: {origin_key} has {right - left:,} label rows, expected 1 compact row.")
                values = _label_arrays_from_row(labels, left, int(horizon_count), origin_key)
                available = values["available"]
                label_event_counts = values["event_count"]
                last_ts = values["last_event_timestamp_us"].astype(np.int64, copy=False)
                horizon_us = values["horizon_us"].astype(np.int64, copy=False)
            else:
                if right - left != int(horizon_count):
                    raise RuntimeError(f"{prefix} inline audit failed: {origin_key} has {right - left:,} labels, expected {int(horizon_count):,}.")
                label_slice = labels.slice(left, right - left)
                if label_slice.get_column("origin_key").n_unique() != 1 or str(label_slice.get_column("origin_key")[0]) != origin_key:
                    raise RuntimeError(f"{prefix} inline audit failed: label rows are not aligned to sampled origin {origin_key}.")
                available = label_slice.get_column("available").to_numpy()
                label_event_counts = label_slice.get_column("event_count").to_numpy()
                last_ts = label_slice.get_column("last_event_timestamp_us").to_numpy().astype(np.int64, copy=False)
                horizon_us = label_slice.get_column("horizon_us").to_numpy().astype(np.int64, copy=False)
            valid = available.astype(bool)
            if valid.any():
                if np.any(label_event_counts[valid] <= 0):
                    raise RuntimeError(f"{prefix} inline audit failed: available label has zero event_count for {origin_key}.")
                if np.any(last_ts[valid] <= origin_timestamp_us):
                    raise RuntimeError(f"{prefix} inline audit failed: available label is not forward-looking for {origin_key}.")
                if np.any(last_ts[valid] > origin_timestamp_us + horizon_us[valid]):
                    raise RuntimeError(f"{prefix} inline audit failed: label exceeds its horizon for {origin_key}.")
        checked += 1
    return checked


def _labels_are_pivoted(labels: Any) -> bool:
    if labels is None or int(getattr(labels, "height", 0) or 0) <= 0 or "horizon_us" not in labels.columns:
        return False
    dtype_text = str(labels.schema.get("horizon_us", "")).lower()
    return "list" in dtype_text or "array" in dtype_text


def _align_labels_to_origins(labels: Any, origins: Any, *, prefix: str) -> tuple[Any, int]:
    pl = _polars()
    origin_count = int(getattr(origins, "height", 0) or 0)
    label_count = int(getattr(labels, "height", 0) or 0)
    if origin_count <= 0:
        return labels.head(0) if label_count > 0 else labels, label_count
    if label_count <= 0:
        raise RuntimeError(f"{prefix} label alignment failed: labels are empty for {origin_count:,} eligible origins.")
    required = {"origin_key", "origin_ordinal", "origin_timestamp_us"}
    missing = sorted(required.difference(set(labels.columns)))
    if missing:
        raise RuntimeError(f"{prefix} label alignment failed: labels are missing columns {missing}.")
    missing_origins = sorted({"origin_key", "origin_ordinal", "origin_timestamp_us"}.difference(set(origins.columns)))
    if missing_origins:
        raise RuntimeError(f"{prefix} label alignment failed: origins are missing columns {missing_origins}.")

    origin_keys = origins.select(["origin_key", "origin_ordinal", "origin_timestamp_us"]).with_row_index("__origin_order")
    if int(origin_keys.get_column("origin_key").n_unique()) != origin_count:
        raise RuntimeError(f"{prefix} label alignment failed: duplicate origin keys in eligible origins.")
    if int(labels.get_column("origin_key").n_unique()) != label_count:
        duplicates = (
            labels.group_by("origin_key")
            .agg(pl.len().alias("__count"))
            .filter(pl.col("__count") > 1)
            .select("origin_key")
            .head(5)
            .get_column("origin_key")
            .to_list()
        )
        raise RuntimeError(f"{prefix} label alignment failed: duplicate compact label rows for origin keys {duplicates}.")

    label_keys = labels.select("origin_key")
    missing_label_count = int(origin_keys.join(label_keys, on="origin_key", how="anti").height)
    if missing_label_count:
        examples = origin_keys.join(label_keys, on="origin_key", how="anti").select("origin_key").head(5).get_column("origin_key").to_list()
        raise RuntimeError(f"{prefix} label alignment failed: {missing_label_count:,} eligible origins have no labels; examples={examples}.")

    extra_label_count = int(label_keys.join(origin_keys.select("origin_key"), on="origin_key", how="anti").height)
    aligned = (
        origin_keys
        .select(["__origin_order", "origin_key"])
        .join(labels, on="origin_key", how="left")
        .sort("__origin_order")
        .drop("__origin_order")
    )
    if int(aligned.height) != origin_count:
        raise RuntimeError(f"{prefix} label alignment failed: aligned labels {aligned.height:,} != origins {origin_count:,}.")
    expected = origins.select(
        [
            "origin_key",
            pl.col("origin_ordinal").alias("__expected_origin_ordinal"),
            pl.col("origin_timestamp_us").alias("__expected_origin_timestamp_us"),
        ]
    )
    mismatches = (
        aligned
        .select(["origin_key", "origin_ordinal", "origin_timestamp_us"])
        .join(expected, on="origin_key", how="left")
        .filter(
            (pl.col("origin_ordinal") != pl.col("__expected_origin_ordinal"))
            | (pl.col("origin_timestamp_us") != pl.col("__expected_origin_timestamp_us"))
        )
    )
    if int(mismatches.height):
        examples = mismatches.select("origin_key").head(5).get_column("origin_key").to_list()
        raise RuntimeError(f"{prefix} label alignment failed: labels disagree with origin identity; examples={examples}.")
    return aligned, extra_label_count


def _label_arrays_from_row(labels: Any, row_index: int, expected: int, origin_key: str) -> dict[str, np.ndarray]:
    row = labels.row(int(row_index), named=True)
    if str(row.get("origin_key", "")) != origin_key:
        raise RuntimeError(f"label row is not aligned to sampled origin {origin_key}.")
    arrays = {
        "horizon_us": _cell_array(row.get("horizon_us"), np.int64),
        "event_count": _cell_array(row.get("event_count"), np.uint64),
        "last_event_timestamp_us": _cell_array(row.get("last_event_timestamp_us"), np.int64),
        "available": _cell_array(row.get("available"), np.uint8),
    }
    for key in FUTURE_EVENT_FLAG_LABEL_KEYS:
        if key in labels.columns:
            arrays[key] = _cell_array(row.get(key), np.uint8)
    for key, value in arrays.items():
        if int(expected) and int(value.shape[0]) != int(expected):
            raise RuntimeError(f"{origin_key} compact label field {key} has {value.shape[0]:,} values, expected {int(expected):,}.")
    return arrays


def _cell_array(value: Any, dtype: Any) -> np.ndarray:
    if value is None:
        return np.asarray([], dtype=dtype)
    if hasattr(value, "to_numpy"):
        arr = value.to_numpy()
    elif isinstance(value, np.ndarray):
        arr = value
    elif isinstance(value, (list, tuple)):
        arr = np.asarray(value)
    else:
        arr = np.asarray([value])
    return arr.astype(dtype, copy=False)


def _deterministic_sample_indexes(count: int, samples: int, *, month: str, ticker: str, part_id: int) -> list[int]:
    count = max(0, int(count))
    samples = max(0, min(int(samples), count))
    if samples == 0:
        return []
    seed = 1469598103934665603
    for byte in f"{month}|{ticker}|{part_id}".encode("utf-8"):
        seed ^= int(byte)
        seed = (seed * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    rng = np.random.default_rng(seed)
    return sorted(int(value) for value in rng.choice(count, size=samples, replace=False))


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
    return _query_intraday_forward_labels_asof(args, client_opts, config, window, ticker, horizons, part, month_min_ordinal)


def _condition_token_array_aliases_sql(config: RollingMarketDataConfig) -> str:
    table = f"{quote_ident(config.database)}.{quote_ident(config.condition_token_reference_table)}"
    aliases: list[str] = []
    for label_key, groups in FUTURE_CONDITION_GROUPS:
        predicates = []
        for source_family, modifiers in groups:
            modifier_sql = ", ".join(str(int(value)) for value in modifiers)
            if source_family in CONDITION_INDICATOR_SOURCE_FAMILIES:
                excluded = ", ".join(sql_string(value) for value in sorted(CONDITION_DIRECT_SOURCE_FAMILIES))
                predicates.append(f"(source_family NOT IN ({excluded}) AND modifier_int IN ({modifier_sql}))")
            else:
                predicates.append(f"(source_family = {sql_string(source_family)} AND modifier_int IN ({modifier_sql}))")
        aliases.append(
            f"""(
        SELECT groupArray(toUInt8(token_id))
        FROM {table}
        WHERE is_join_canonical = 1
          AND ({" OR ".join(predicates)})
    ) AS {quote_ident(label_key + "_tokens")}"""
        )
    return ",\n    ".join(aliases)


def _future_condition_event_select_sql() -> str:
    token_array = "arrayFilter(t -> t != 0, [condition_token_1, condition_token_2, condition_token_3, condition_token_4, condition_token_5])"
    return ",\n                ".join(
        f"toUInt8(arrayExists(t -> has({quote_ident(label_key + '_tokens')}, t), {token_array})) AS {quote_ident(label_key + '_event')}"
        for label_key in FUTURE_CONDITION_LABEL_KEYS
    )


def _future_condition_cumulative_select_sql() -> str:
    return ",\n            ".join(
        f"sum(toUInt64({quote_ident(label_key + '_event')})) OVER event_window AS {quote_ident('cum_' + label_key)}"
        for label_key in FUTURE_CONDITION_LABEL_KEYS
    )


def _future_condition_count_select_sql() -> str:
    return ",\n                ".join(
        f"greatest(toInt64(ifNull(target.{quote_ident('cum_' + label_key)}, 0)) - toInt64(ifNull(base.{quote_ident('cum_' + label_key)}, 0)), 0) AS {quote_ident(label_key)}"
        for label_key in FUTURE_CONDITION_LABEL_KEYS
    )


def _future_condition_label_select_sql() -> str:
    return ",\n            ".join(
        f"toUInt8((local_session_us + horizon_us) <= 72000000000 AND {quote_ident(label_key)} > 0) AS {quote_ident(label_key)}"
        for label_key in FUTURE_CONDITION_LABEL_KEYS
    )


def _future_external_label_select_sql() -> str:
    return """
            toUInt8((local_session_us + horizon_us) <= 72000000000 AND ticker_news_count > 0) AS ticker_news_arrival_flag,
            toUInt8((local_session_us + horizon_us) <= 72000000000 AND sec_filing_count > 0) AS sec_filing_arrival_flag
""".strip()


def _future_external_count_select_sql() -> str:
    return """
                greatest(toInt64(ifNull(news_target.cum_ticker_news_arrivals, 0)) - toInt64(ifNull(news_base.cum_ticker_news_arrivals, 0)), 0) AS ticker_news_count,
                greatest(toInt64(ifNull(sec_target.cum_sec_filing_arrivals, 0)) - toInt64(ifNull(sec_base.cum_sec_filing_arrivals, 0)), 0) AS sec_filing_count
""".strip()


def _future_event_flag_array_select_sql(start_index: int) -> str:
    return ",\n    ".join(
        f"arrayMap(x -> tupleElement(x, {int(start_index) + offset}), label_items) AS {quote_ident(label_key)}"
        for offset, label_key in enumerate(FUTURE_EVENT_FLAG_LABEL_KEYS)
    )


def _future_event_flag_tuple_items_sql() -> str:
    return ",\n            ".join(quote_ident(label_key) for label_key in FUTURE_EVENT_FLAG_LABEL_KEYS)


def _query_intraday_forward_labels_asof(
    args: argparse.Namespace,
    client_opts: Mapping[str, str],
    config: RollingMarketDataConfig,
    window: Any,
    ticker: str,
    horizons: list[TimeBarHorizon],
    part: OriginOrdinalPart,
    month_min_ordinal: int,
) -> Any:
    table = f"{quote_ident(config.database)}.{quote_ident(config.events_table)}"
    horizon_tuples = ",\n        ".join(f"tuple({sql_string(horizon.name)}, toUInt64({int(horizon.microseconds)}))" for horizon in horizons)
    condition_token_aliases = _condition_token_array_aliases_sql(config)
    condition_event_select = _future_condition_event_select_sql()
    condition_cumulative_select = _future_condition_cumulative_select_sql()
    condition_count_select = _future_condition_count_select_sql()
    condition_label_select = _future_condition_label_select_sql()
    external_count_select = _future_external_count_select_sql()
    external_label_select = _future_external_label_select_sql()
    event_flag_array_select = _future_event_flag_array_select_sql(10)
    event_flag_tuple_items = _future_event_flag_tuple_items_sql()
    news_table = f"{quote_ident(config.database)}.{quote_ident(config.news_embedding_table)}"
    sec_table = f"{quote_ident(config.sec_context_database)}.{quote_ident(config.sec_filing_text_embedding_table)}"
    # One set query per ticker/month/part. It resolves every horizon through cumulative ASOF lookups
    # instead of repeatedly range-joining future events for every horizon.
    query = f"""
WITH
    [{horizon_tuples}] AS horizons,
    {condition_token_aliases},
    origins AS
    (
        SELECT
            upper(ticker) AS ticker,
            cityHash64(ticker) AS ticker_id,
            ordinal AS origin_ordinal,
            sip_timestamp_us AS origin_timestamp_us,
            concat(upper(ticker), '|', toString(ordinal)) AS origin_key,
            toDate(toTimeZone(fromUnixTimestamp64Micro(sip_timestamp_us, 'UTC'), {sql_string(SESSION_TIMEZONE)})) AS origin_local_date,
            dateDiff('second', toStartOfDay(toTimeZone(fromUnixTimestamp64Micro(sip_timestamp_us, 'UTC'), {sql_string(SESSION_TIMEZONE)})), toTimeZone(fromUnixTimestamp64Micro(sip_timestamp_us, 'UTC'), {sql_string(SESSION_TIMEZONE)})) AS local_second,
            dateDiff('microsecond', toStartOfDay(toTimeZone(fromUnixTimestamp64Micro(sip_timestamp_us, 'UTC'), {sql_string(SESSION_TIMEZONE)})), toTimeZone(fromUnixTimestamp64Micro(sip_timestamp_us, 'UTC'), {sql_string(SESSION_TIMEZONE)})) AS local_session_us
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
    origin_horizons AS
    (
        SELECT
            origin_key,
            ticker_id,
            ticker,
            origin_ordinal,
            origin_timestamp_us,
            origin_local_date,
            local_second,
            local_session_us,
            tupleElement(horizon_tuple, 1) AS horizon,
            tupleElement(horizon_tuple, 2) AS horizon_us,
            origin_timestamp_us + tupleElement(horizon_tuple, 2) AS target_timestamp_us
        FROM origins
        ARRAY JOIN horizons AS horizon_tuple
        ORDER BY ticker, origin_local_date, target_timestamp_us, origin_ordinal, horizon_us
    ),
    cumulative_events AS
    (
        SELECT
            ticker,
            local_date,
            ordinal,
            sip_timestamp_us,
            price_primary_int,
            price_secondary_int,
            count() OVER event_window AS cum_count,
            sum(toFloat64(size_primary)) OVER event_window AS cum_size_primary,
            sum(toFloat64(size_secondary)) OVER event_window AS cum_size_secondary,
            {condition_cumulative_select}
        FROM
        (
            SELECT
                ticker,
                ordinal,
                sip_timestamp_us,
                price_primary_int,
                price_secondary_int,
                size_primary,
                size_secondary,
                {condition_event_select},
                toTimeZone(fromUnixTimestamp64Micro(sip_timestamp_us, 'UTC'), {sql_string(SESSION_TIMEZONE)}) AS ts_local,
                toDate(ts_local) AS local_date,
                dateDiff('second', toStartOfDay(ts_local), ts_local) AS local_second
            FROM {table}
            PREWHERE ticker = {sql_string(ticker)}
              AND event_date >= toDate({sql_string(window.first_date.isoformat())})
              AND event_date <= toDate({sql_string(window.next_month_date.isoformat())})
            WHERE sip_timestamp_us >= {int(window.first_session_start_us)}
              AND sip_timestamp_us <= {int(window.last_session_end_us)}
              AND toDate(ts_local) >= toDate({sql_string(window.first_date.isoformat())})
              AND toDate(ts_local) < toDate({sql_string(window.next_month_date.isoformat())})
              AND local_second < 72000
        )
        WINDOW event_window AS (PARTITION BY ticker, local_date ORDER BY sip_timestamp_us, ordinal ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
        ORDER BY ticker, local_date, sip_timestamp_us, ordinal
    ),
    ticker_news_arrivals AS
    (
        SELECT
            ticker,
            local_date,
            timestamp_us,
            count() OVER arrival_window AS cum_ticker_news_arrivals
        FROM
        (
            SELECT
                upper(ticker) AS ticker,
                timestamp_us,
                toTimeZone(fromUnixTimestamp64Micro(timestamp_us, 'UTC'), {sql_string(SESSION_TIMEZONE)}) AS ts_local,
                toDate(ts_local) AS local_date,
                dateDiff('second', toStartOfDay(ts_local), ts_local) AS local_second
            FROM {news_table}
            PREWHERE ticker = {sql_string(ticker)}
              AND timestamp_us >= {int(window.first_session_start_us)}
              AND timestamp_us <= {int(window.last_session_end_us)}
            WHERE published_at_utc <= fromUnixTimestamp64Micro(timestamp_us, 'UTC')
              AND toDate(ts_local) >= toDate({sql_string(window.first_date.isoformat())})
              AND toDate(ts_local) < toDate({sql_string(window.next_month_date.isoformat())})
              AND local_second < 72000
            GROUP BY
                ticker,
                timestamp_us,
                ts_local,
                local_date,
                local_second,
                source_id,
                provider_article_id,
                text_hash
        )
        WINDOW arrival_window AS (PARTITION BY ticker, local_date ORDER BY timestamp_us ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
        ORDER BY ticker, local_date, timestamp_us
    ),
    sec_filing_arrivals AS
    (
        SELECT
            ticker,
            local_date,
            timestamp_us,
            count() OVER arrival_window AS cum_sec_filing_arrivals
        FROM
        (
            SELECT
                upper(ticker) AS ticker,
                timestamp_us,
                toTimeZone(fromUnixTimestamp64Micro(timestamp_us, 'UTC'), {sql_string(SESSION_TIMEZONE)}) AS ts_local,
                toDate(ts_local) AS local_date,
                dateDiff('second', toStartOfDay(ts_local), ts_local) AS local_second
            FROM {sec_table}
            PREWHERE ticker = {sql_string(ticker)}
              AND timestamp_us >= {int(window.first_session_start_us)}
              AND timestamp_us <= {int(window.last_session_end_us)}
            WHERE accepted_at_utc <= fromUnixTimestamp64Micro(timestamp_us, 'UTC')
              AND toDate(ts_local) >= toDate({sql_string(window.first_date.isoformat())})
              AND toDate(ts_local) < toDate({sql_string(window.next_month_date.isoformat())})
              AND local_second < 72000
            GROUP BY
                ticker,
                timestamp_us,
                ts_local,
                local_date,
                local_second,
                accession_number
        )
        WINDOW arrival_window AS (PARTITION BY ticker, local_date ORDER BY timestamp_us ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
        ORDER BY ticker, local_date, timestamp_us
    ),
    label_rows AS
    (
        SELECT
            origin_key,
            ticker_id,
            ticker,
            origin_ordinal,
            origin_timestamp_us,
            horizon,
            horizon_us,
            toInt32(if(event_count > 0, target_price_primary_int, 0)) AS price_primary_int,
            toInt32(if(event_count > 0, target_price_secondary_int, 0)) AS price_secondary_int,
            toFloat32(greatest(size_primary_sum, 0.0)) AS size_primary_sum,
            toFloat32(greatest(size_secondary_sum, 0.0)) AS size_secondary_sum,
            toUInt64(event_count) AS event_count,
            toInt64(if(event_count > 0, target_timestamp_us, 0)) AS last_event_timestamp_us,
            toUInt8((local_session_us + horizon_us) <= 72000000000 AND event_count > 0) AS available,
            {condition_label_select},
            {external_label_select}
        FROM
        (
            SELECT
                o.origin_key AS origin_key,
                o.ticker_id AS ticker_id,
                o.ticker AS ticker,
                o.origin_ordinal AS origin_ordinal,
                o.origin_timestamp_us AS origin_timestamp_us,
                o.local_session_us AS local_session_us,
                o.horizon AS horizon,
                o.horizon_us AS horizon_us,
                target.price_primary_int AS target_price_primary_int,
                target.price_secondary_int AS target_price_secondary_int,
                target.sip_timestamp_us AS target_timestamp_us,
                greatest(toInt64(ifNull(target.cum_count, 0)) - toInt64(ifNull(base.cum_count, 0)), 0) AS event_count,
                ifNull(target.cum_size_primary, 0.0) - ifNull(base.cum_size_primary, 0.0) AS size_primary_sum,
                ifNull(target.cum_size_secondary, 0.0) - ifNull(base.cum_size_secondary, 0.0) AS size_secondary_sum,
                {condition_count_select},
                {external_count_select}
            FROM origin_horizons AS o
            ASOF LEFT JOIN cumulative_events AS target
                ON target.ticker = o.ticker
               AND target.local_date = o.origin_local_date
               AND o.target_timestamp_us >= target.sip_timestamp_us
            ASOF LEFT JOIN cumulative_events AS base
                ON base.ticker = o.ticker
               AND base.local_date = o.origin_local_date
               AND o.origin_timestamp_us >= base.sip_timestamp_us
            ASOF LEFT JOIN ticker_news_arrivals AS news_target
                ON news_target.ticker = o.ticker
               AND news_target.local_date = o.origin_local_date
               AND o.target_timestamp_us >= news_target.timestamp_us
            ASOF LEFT JOIN ticker_news_arrivals AS news_base
                ON news_base.ticker = o.ticker
               AND news_base.local_date = o.origin_local_date
               AND o.origin_timestamp_us >= news_base.timestamp_us
            ASOF LEFT JOIN sec_filing_arrivals AS sec_target
                ON sec_target.ticker = o.ticker
               AND sec_target.local_date = o.origin_local_date
               AND o.target_timestamp_us >= sec_target.timestamp_us
            ASOF LEFT JOIN sec_filing_arrivals AS sec_base
                ON sec_base.ticker = o.ticker
               AND sec_base.local_date = o.origin_local_date
               AND o.origin_timestamp_us >= sec_base.timestamp_us
        )
    )
SELECT
    origin_key,
    ticker_id,
    ticker,
    origin_ordinal,
    origin_timestamp_us,
    arrayMap(x -> tupleElement(x, 1), label_items) AS horizon,
    arrayMap(x -> tupleElement(x, 2), label_items) AS horizon_us,
    arrayMap(x -> tupleElement(x, 3), label_items) AS price_primary_int,
    arrayMap(x -> tupleElement(x, 4), label_items) AS price_secondary_int,
    arrayMap(x -> tupleElement(x, 5), label_items) AS size_primary_sum,
    arrayMap(x -> tupleElement(x, 6), label_items) AS size_secondary_sum,
    arrayMap(x -> tupleElement(x, 7), label_items) AS event_count,
    arrayMap(x -> tupleElement(x, 8), label_items) AS last_event_timestamp_us,
    arrayMap(x -> tupleElement(x, 9), label_items) AS available,
    {event_flag_array_select}
FROM
(
    SELECT
        origin_key,
        ticker_id,
        ticker,
        origin_ordinal,
        origin_timestamp_us,
        arraySort(x -> tupleElement(x, 2), groupArray(tuple(
            horizon,
            horizon_us,
            price_primary_int,
            price_secondary_int,
            size_primary_sum,
            size_secondary_sum,
            event_count,
            last_event_timestamp_us,
            available,
            {event_flag_tuple_items}
        ))) AS label_items
    FROM label_rows
    GROUP BY
        origin_key,
        ticker_id,
        ticker,
        origin_ordinal,
        origin_timestamp_us
)
ORDER BY origin_ordinal
{_settings_sql(config)}
"""
    return query_polars(client_opts, query)


def _query_ticker_news(args: argparse.Namespace, client_opts: Mapping[str, str], config: RollingMarketDataConfig, window: Any, ticker: str) -> Any:
    if args.skip_token_contexts:
        return _empty_frame()
    table = f"{quote_ident(config.database)}.{quote_ident(config.news_embedding_table)}"
    columns = ",\n    ".join(quote_ident(column) for column in NEWS_EMBEDDING_COLUMNS)
    time_columns = _available_time_feature_sql("timestamp_us", prefix="available")
    prior_items = max(0, int(getattr(args, "ticker_news_prior_items", 0) or 0))
    query = f"""
WITH prior_items AS
(
    SELECT
        toString(source_id) AS source_id_key,
        toString(provider_article_id) AS provider_article_id_key,
        toString(text_hash) AS text_hash_key
    FROM {table}
    WHERE ticker = {sql_string(ticker)}
      AND timestamp_us < {int(window.first_session_start_us)}
      AND published_at_utc < {date_time64_from_us(window.first_session_start_us)}
    GROUP BY
        source_id_key,
        provider_article_id_key,
        text_hash_key
    ORDER BY
        max(timestamp_us) DESC,
        source_id_key,
        provider_article_id_key,
        text_hash_key
    LIMIT {int(prior_items)}
)
SELECT
    {columns},
    {time_columns}
FROM {table}
WHERE ticker = {sql_string(ticker)}
  AND timestamp_us < {int(window.last_session_end_us)}
  AND published_at_utc < {date_time64_from_us(window.last_session_end_us)}
  AND (
      timestamp_us >= {int(window.first_session_start_us)}
      OR tuple(toString(source_id), toString(provider_article_id), toString(text_hash)) IN (SELECT source_id_key, provider_article_id_key, text_hash_key FROM prior_items)
  )
ORDER BY ticker, timestamp_us, source_id, token_chunk_index
{_settings_sql(config)}
"""
    return query_polars(client_opts, query)


def _query_market_news(args: argparse.Namespace, client_opts: Mapping[str, str], config: RollingMarketDataConfig, window: Any) -> Any:
    if args.skip_token_contexts:
        return _empty_frame()
    table = f"{quote_ident(config.database)}.{quote_ident(config.news_embedding_table)}"
    source_columns = ",\n        ".join(f"t.{quote_ident(column)}" for column in NEWS_EMBEDDING_COLUMNS if column != "ticker")
    time_columns = _available_time_feature_sql("t.timestamp_us", prefix="available")
    prior_items = max(0, int(getattr(args, "market_news_prior_items", 0) or 0))
    query = f"""
WITH prior_items AS
(
    SELECT
        toString(source_id) AS source_id_key,
        toString(provider_article_id) AS provider_article_id_key,
        toString(text_hash) AS text_hash_key
    FROM {table}
    WHERE timestamp_us < {int(window.first_session_start_us)}
      AND published_at_utc < {date_time64_from_us(window.first_session_start_us)}
    GROUP BY
        source_id_key,
        provider_article_id_key,
        text_hash_key
    ORDER BY
        max(timestamp_us) DESC,
        source_id_key,
        provider_article_id_key,
        text_hash_key
    LIMIT {int(prior_items)}
)
SELECT
    '__MARKET__' AS ticker,
    {source_columns},
    {time_columns}
FROM
(
    SELECT *
    FROM {table}
    WHERE timestamp_us < {int(window.last_session_end_us)}
      AND published_at_utc < {date_time64_from_us(window.last_session_end_us)}
      AND (
          timestamp_us >= {int(window.first_session_start_us)}
          OR tuple(toString(source_id), toString(provider_article_id), toString(text_hash)) IN (SELECT source_id_key, provider_article_id_key, text_hash_key FROM prior_items)
      )
    ORDER BY source_id, provider_article_id, text_hash, token_chunk_index, ticker
    LIMIT 1 BY source_id, provider_article_id, text_hash, token_chunk_index
) AS t
ORDER BY timestamp_us, source_id, token_chunk_index
{_settings_sql(config)}
"""
    return query_polars(client_opts, query)


def _query_sec_tokens(args: argparse.Namespace, client_opts: Mapping[str, str], config: RollingMarketDataConfig, window: Any, ticker: str) -> Any:
    if args.skip_token_contexts:
        return _empty_frame()
    table = f"{quote_ident(config.sec_context_database)}.{quote_ident(config.sec_filing_text_embedding_table)}"
    columns = ",\n    ".join(quote_ident(column) for column in SEC_EMBEDDING_COLUMNS)
    time_columns = _available_time_feature_sql("timestamp_us", prefix="available")
    prior_items = max(0, int(getattr(args, "sec_filing_prior_items", 0) or 0))
    query = f"""
WITH prior_items AS
(
    SELECT
        toString(accession_number) AS accession_number_key,
        toString(document_id) AS document_id_key,
        toString(text_rank) AS text_rank_key,
        toString(source_id) AS source_id_key
    FROM {table}
    WHERE ticker = {sql_string(ticker)}
      AND timestamp_us < {int(window.first_session_start_us)}
      AND accepted_at_utc < {date_time64_from_us(window.first_session_start_us)}
    GROUP BY
        accession_number_key,
        document_id_key,
        text_rank_key,
        source_id_key
    ORDER BY
        max(timestamp_us) DESC,
        accession_number_key,
        document_id_key,
        text_rank_key,
        source_id_key
    LIMIT {int(prior_items)}
)
SELECT
    {columns},
    {time_columns}
FROM {table}
WHERE ticker = {sql_string(ticker)}
  AND timestamp_us < {int(window.last_session_end_us)}
  AND accepted_at_utc < {date_time64_from_us(window.last_session_end_us)}
  AND (
      timestamp_us >= {int(window.first_session_start_us)}
      OR tuple(toString(accession_number), toString(document_id), toString(text_rank), toString(source_id)) IN (SELECT accession_number_key, document_id_key, text_rank_key, source_id_key FROM prior_items)
  )
ORDER BY ticker, timestamp_us, accession_number, text_rank, document_id, source_id, token_chunk_index
{_settings_sql(config)}
"""
    return query_polars(client_opts, query)


def _query_xbrl(args: argparse.Namespace, client_opts: Mapping[str, str], config: RollingMarketDataConfig, window: Any, ticker: str) -> Any:
    table = f"{quote_ident(config.sec_context_database)}.{quote_ident(config.sec_xbrl_context_table)}"
    reference_table = f"{quote_ident(config.sec_context_database)}.{quote_ident(config.category_reference_table)}"
    prior_rows = max(0, int(getattr(args, "xbrl_prior_rows", 0) or 0))
    time_columns = _available_time_feature_sql("timestamp_us", prefix="available")
    category_reference_cte = f"""
refs AS
(
    SELECT
        field_name,
        category_value,
        argMax(category_id, updated_at) AS category_id
    FROM {reference_table}
    WHERE domain = 'xbrl'
    GROUP BY
        field_name,
        category_value
)
"""
    category_joins = """
    LEFT JOIN refs AS taxonomy_ref ON taxonomy_ref.field_name = 'taxonomy' AND taxonomy_ref.category_value = trim(BOTH ' ' FROM toString(x.taxonomy))
    LEFT JOIN refs AS tag_ref ON tag_ref.field_name = 'tag' AND tag_ref.category_value = trim(BOTH ' ' FROM toString(x.tag))
    LEFT JOIN refs AS unit_ref ON unit_ref.field_name = 'unit_code' AND unit_ref.category_value = trim(BOTH ' ' FROM toString(x.unit_code))
    LEFT JOIN refs AS form_ref ON form_ref.field_name = 'form_type' AND form_ref.category_value = trim(BOTH ' ' FROM toString(x.form_type))
    LEFT JOIN refs AS row_kind_ref ON row_kind_ref.field_name = 'xbrl_row_kind' AND row_kind_ref.category_value = trim(BOTH ' ' FROM toString(x.xbrl_row_kind))
    LEFT JOIN refs AS location_ref ON location_ref.field_name = 'location_code' AND location_ref.category_value = trim(BOTH ' ' FROM toString(x.location_code))
    LEFT JOIN refs AS fiscal_period_ref ON fiscal_period_ref.field_name = 'fiscal_period' AND fiscal_period_ref.category_value = trim(BOTH ' ' FROM toString(x.fiscal_period))
    LEFT JOIN refs AS calendar_period_ref ON calendar_period_ref.field_name = 'calendar_period_code' AND calendar_period_ref.category_value = trim(BOTH ' ' FROM toString(x.calendar_period_code))
"""
    category_columns = """
        toUInt32(ifNull(taxonomy_ref.category_id, 0)) AS taxonomy_id,
        toUInt32(ifNull(tag_ref.category_id, 0)) AS tag_id,
        toUInt32(ifNull(unit_ref.category_id, 0)) AS unit_id,
        toUInt32(ifNull(form_ref.category_id, 0)) AS form_id,
        toUInt32(ifNull(row_kind_ref.category_id, 0)) AS row_kind_id,
        toUInt32(ifNull(location_ref.category_id, 0)) AS location_id,
        toUInt32(ifNull(fiscal_period_ref.category_id, 0)) AS fiscal_period_id,
        toUInt32(ifNull(calendar_period_ref.category_id, 0)) AS calendar_period_id,
"""
    query = f"""
WITH {category_reference_cte},
prior_rows AS
(
    SELECT
        x.ticker,
        x.timestamp_us,
        x.source_id,
        x.cik,
        x.issuer_id,
        x.taxonomy,
        x.tag,
        x.unit_code,
        x.fiscal_year,
        x.fiscal_period,
        x.form_type,
        x.accepted_at_source,
        x.accession_number,
        x.period_end_date,
        x.value,
        x.calendar_period_code,
        x.location_code,
        x.xbrl_row_kind,
        x.bridge_id,
        x.mapping_confidence AS mapping_confidence_score,
        {category_columns}
        {time_columns}
    FROM {table} AS x
    {category_joins}
    WHERE x.ticker = {sql_string(ticker)}
      AND x.timestamp_us < {int(window.first_session_start_us)}
    ORDER BY x.ticker, x.timestamp_us DESC, x.xbrl_row_kind DESC, x.taxonomy DESC, x.tag DESC, x.unit_code DESC, x.period_end_date DESC
    LIMIT {int(prior_rows)}
)
SELECT
    x.ticker,
    x.timestamp_us,
    x.source_id,
    x.cik,
    x.issuer_id,
    x.taxonomy,
    x.tag,
    x.unit_code,
    x.fiscal_year,
    x.fiscal_period,
    x.form_type,
    x.accepted_at_source,
    x.accession_number,
    x.period_end_date,
    x.value,
    x.calendar_period_code,
    x.location_code,
    x.xbrl_row_kind,
    x.bridge_id,
    x.mapping_confidence AS mapping_confidence_score,
    {category_columns}
    {time_columns}
FROM {table} AS x
{category_joins}
WHERE x.ticker = {sql_string(ticker)}
  AND x.timestamp_us >= {int(window.first_session_start_us)}
  AND x.timestamp_us < {int(window.last_session_end_us)}
UNION ALL
SELECT *
FROM prior_rows
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
    time_columns = _bar_start_time_feature_sql("bar_start")
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
    vwap,
    {time_columns}
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
        if "session_second" not in part.columns:
            raise RuntimeError("Event frame is missing session_second; cannot build no-lookahead session-aligned origins.")
        session_seconds = part.get_column("session_second").to_numpy().astype(np.int64, copy=False)
        row_offsets = part.get_column("row_offset").to_numpy().astype(np.int64, copy=False)
        ticker_ids = part.get_column("ticker_id").to_numpy()
        positions = np.flatnonzero(
            (ordinals >= int(origin_ordinal_start))
            & (ordinals <= int(origin_ordinal_end))
            & (timestamps >= int(window.first_session_start_us))
            & (timestamps < int(window.last_session_end_us))
            & (session_seconds >= SESSION_START_SECOND)
            & (session_seconds < SESSION_END_SECOND)
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
    retries = max(0, int(client_opts.get("query_retries") or 0))
    backoff_seconds = max(0.0, float(client_opts.get("query_retry_backoff_seconds") or 0.0))
    attempt = 0
    while True:
        retry_sleep = 0.0
        query_id = f"rolling_ticker_month_{os.getpid()}_{threading.get_ident()}_{uuid.uuid4().hex}"
        ACTIVE_QUERIES.register(query_id, label=str(getattr(QUERY_CONTEXT, "label", "")))
        client = clickhouse_connect.get_client(
            host=parsed.hostname or "localhost",
            port=parsed.port or (8443 if secure else 8123),
            username=str(client_opts.get("user") or "default"),
            password=str(client_opts.get("password") or ""),
            secure=secure,
        )
        try:
            table = _query_arrow_with_id(client, query=query, query_id=query_id)
            return _polars().from_arrow(table)
        except Exception as exc:
            if attempt >= retries or not _is_transient_clickhouse_read_error(exc):
                raise
            retry_sleep = backoff_seconds * float(2**attempt)
            attempt += 1
        finally:
            ACTIVE_QUERIES.unregister(query_id)
            try:
                client.close()
            except Exception:
                pass
        if retry_sleep > 0:
            time.sleep(retry_sleep)


def _query_arrow_with_id(client: Any, *, query: str, query_id: str) -> Any:
    try:
        return client.query_arrow(query, settings={"query_id": query_id})
    except TypeError:
        try:
            return client.query_arrow(query, query_id=query_id)
        except TypeError:
            return client.query_arrow(f"/* query_id={query_id} */\n{query}")


def _is_transient_clickhouse_read_error(exc: BaseException) -> bool:
    text = repr(exc)
    if "QUERY_WAS_CANCELLED" in text or "DB::Exception" in text:
        return False
    transient_markers = (
        "IncompleteRead",
        "ProtocolError",
        "Connection broken",
        "RemoteDisconnected",
        "Connection reset",
        "Read timed out",
        "timed out",
    )
    return any(marker in text for marker in transient_markers)


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
    if stats is not None:
        stats.log_event("clickhouse_cancel_done", cancelled=len(ids), active_query_ids=ids)
    return len(ids)


def _cancel_active_work_with_grace(
    *,
    client_opts: Mapping[str, str],
    stats: BuildStats,
    dashboard: "TickerMonthDashboard | None",
    cache_root: Path,
    reason: str,
    grace_seconds: float = 12.0,
) -> None:
    deadline = time.perf_counter() + max(1.0, float(grace_seconds))
    attempt = 0
    while True:
        active = ACTIVE_QUERIES.snapshot()
        if not active:
            stats.log_event("shutdown_active_queries_clear", reason=reason, attempts=attempt)
            break
        attempt += 1
        stats.message(f"{reason}: cancelling {len(active)} active ClickHouse quer{'y' if len(active) == 1 else 'ies'}")
        cancel_active_clickhouse_queries(client_opts=client_opts, stats=stats)
        try:
            _refresh(stats, dashboard, cache_root, force=True)
        except Exception as exc:
            stats.log_event("shutdown_refresh_failed", reason=reason, error=repr(exc))
        if time.perf_counter() >= deadline:
            remaining = ACTIVE_QUERIES.snapshot()
            stats.message(f"{reason}: {len(remaining)} ClickHouse quer{'y is' if len(remaining) == 1 else 'ies are'} still unwinding after cancellation")
            stats.log_event("shutdown_active_queries_remaining", reason=reason, active_queries=remaining, attempts=attempt)
            break
        time.sleep(1.0)


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


def _execute_clickhouse_sql(*, client_opts: Mapping[str, str], sql: str, label: str, timeout_seconds: float | None = None) -> str:
    query_id = f"rolling_ticker_month_{os.getpid()}_{threading.get_ident()}_{uuid.uuid4().hex}"
    ACTIVE_QUERIES.register(query_id, label=label)
    url = str(client_opts["clickhouse_url"]).rstrip("/") + "/?" + urlencode({"query_id": query_id})
    req = url_request.Request(url, data=sql.rstrip(";").encode("utf-8"), method="POST")
    user = str(client_opts.get("user") or "default")
    password = str(client_opts.get("password") or "")
    if user:
        req.add_header("X-ClickHouse-User", user)
    if password:
        req.add_header("X-ClickHouse-Key", password)
    try:
        with url_request.urlopen(req, timeout=timeout_seconds) as response:
            return response.read().decode("utf-8", errors="replace")
    finally:
        ACTIVE_QUERIES.unregister(query_id)


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
        console = Console()
        self._live = Live(
            self._render(),
            console=console,
            refresh_per_second=max(0.5, 1.0 / self.refresh_seconds),
            auto_refresh=False,
            screen=True,
            transient=False,
            vertical_overflow="visible",
        )
        try:
            self._live.start(refresh=True)
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

        lanes = Table(expand=True, box=box.SIMPLE)
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

        workers = Table(expand=True, box=box.SIMPLE)
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
        summary_style = _dashboard_status_style(self.stats)
        return Group(
            Panel(summary, title="Ticker/Month Rolling Cache", box=box.ROUNDED, border_style=summary_style, padding=(0, 1)),
            Panel(lanes, title="Concurrent Lanes", box=box.ROUNDED, border_style="blue", padding=(0, 1)),
            Panel(workers, title="Package Workers", box=box.ROUNDED, border_style="green", padding=(0, 1)),
            Panel(messages, title="Messages", box=box.ROUNDED, border_style="yellow", padding=(0, 1)),
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


def _dashboard_status_style(stats: BuildStats) -> str:
    phase = str(stats.phase or "").lower()
    if stats.packages_failed > 0 or phase in {"error", "audit_failed"}:
        return "red"
    if stats.stop_requested or stats.interrupted or phase in {"stopping", "interrupted"}:
        return "yellow"
    if phase == "complete":
        return "green"
    return "cyan"


if __name__ == "__main__":
    raise SystemExit(main())
