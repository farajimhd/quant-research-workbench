from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import queue
import signal
import shutil
import sys
import threading
import time
import traceback
import uuid
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

if __package__ in {None, ""}:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "research").is_dir():
            sys.path.insert(0, str(parent))
            break

from pipelines.market_sip.events.clickhouse_build_unified_events import events_table_for_year, events_table_uses_year_suffix
from research.mlops.clickhouse import (
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    discover_clickhouse_env_files,
    parse_size_bytes,
    quote_ident,
    sql_string,
)
from research.mlops.env import load_env_files
from research.mlops.rolling_loader.run_build_ticker_month_cache import (
    DEFAULTS as TICKER_MONTH_DEFAULTS,
    _query_corporate_actions,
    _query_daily_bars,
    _query_market_news,
    _query_sec_tokens,
    _query_ticker_news,
    _query_xbrl,
    cancel_active_clickhouse_queries as cancel_legacy_active_clickhouse_queries,
)
from research.mlops.rolling_loader.run_build_ticker_month_cache_streaming import (
    _build_intraday_base_bars,
    _build_intraday_condition_events,
)
from research.mlops.rolling_loader.ticker_month_cache import (
    EVENT_PAYLOAD_COLUMNS,
    EVENT_TIME_FEATURE_COLUMNS,
    add_months,
    build_config_from_args,
    month_window as cache_month_window,
    full_months_in_period,
    jsonable,
    write_json_atomic,
)


DEFAULT_CACHE_ROOT = Path("D:/market-data/prepared/daily_index_streaming_cache")
DEFAULT_DATA_GROUPS = "events,intraday_labels,macro_bars,news,sec,xbrl,corporate_actions"
DEFAULT_EVENT_COLUMNS = (*EVENT_PAYLOAD_COLUMNS, *EVENT_TIME_FEATURE_COLUMNS, "context_only")
MODALITY_PANEL_NAMES = ("Events", "Intraday Labels", "Macro Bars", "News Embeddings", "SEC Embeddings", "XBRL", "Corporate Actions")
DATA_GROUP_ALIASES = {
    "events": "events",
    "intraday_labels": "intraday_labels",
    "labels": "intraday_labels",
    "macro": "macro_bars",
    "macro_bars": "macro_bars",
    "daily_bars": "macro_bars",
    "news": "news",
    "news_embeddings": "news",
    "sec": "sec",
    "sec_embeddings": "sec",
    "xbrl": "xbrl",
    "corporate": "corporate_actions",
    "corporate_actions": "corporate_actions",
}
MODALITY_BY_DATA_GROUP = {
    "events": "Events",
    "intraday_labels": "Intraday Labels",
    "macro_bars": "Macro Bars",
    "news": "News Embeddings",
    "sec": "SEC Embeddings",
    "xbrl": "XBRL",
    "corporate_actions": "Corporate Actions",
}
SESSION_TIMEZONE = "America/New_York"
SESSION_START_SECOND = 4 * 60 * 60
SESSION_REGULAR_START_SECOND = 9 * 60 * 60 + 30 * 60
SESSION_REGULAR_END_SECOND = 16 * 60 * 60
SESSION_END_SECOND = 20 * 60 * 60
SESSION_LENGTH_SECOND = SESSION_END_SECOND - SESSION_START_SECOND
QUERY_ID_PREFIX = "daily_index_streaming_cache_"


@dataclass(frozen=True, slots=True)
class EventDailyUnit:
    month: str
    ticker: str
    source_date: str
    event_count: int
    first_ordinal: int
    last_ordinal: int
    next_ordinal: int
    first_sip_timestamp_us: int
    last_sip_timestamp_us: int
    build_step: int
    built_at: str


@dataclass(frozen=True, slots=True)
class EventFetchJob:
    modality: str
    job_id: str
    month: str
    ticker: str
    kind: str
    part_id: int
    ordinal_start: int
    ordinal_end: int
    expected_rows: int
    source_date: str = ""
    event_date_start: str = ""
    event_date_end: str = ""
    origin_first_ordinal: int = 0
    origin_last_ordinal: int = 0
    first_sip_timestamp_us: int = 0
    last_sip_timestamp_us: int = 0


@dataclass(slots=True)
class FetchedPayload:
    job: EventFetchJob
    frame: Any
    row_count: int
    estimated_bytes: int
    query_id: str
    seconds: float


@dataclass(slots=True)
class ProcessedPayload:
    job: EventFetchJob
    events: Any
    origins: Any
    audit: dict[str, Any]
    estimated_bytes: int
    fetch_query_id: str
    fetch_seconds: float
    process_seconds: float


@dataclass(frozen=True, slots=True)
class ModalityJob:
    modality: str
    data_group: str
    job_id: str
    month: str
    ticker: str
    scope: str
    part_id: int
    expected_rows: int = 0
    ordinal_start: int = 0
    ordinal_end: int = 0
    output_name: str = ""


@dataclass(slots=True)
class GenericFetchedPayload:
    job: ModalityJob
    frames: dict[str, Any]
    row_count: int
    estimated_bytes: int
    query_id: str
    seconds: float


@dataclass(slots=True)
class GenericProcessedPayload:
    job: ModalityJob
    frames: dict[str, Any]
    audit: dict[str, Any]
    estimated_bytes: int
    fetch_query_id: str
    fetch_seconds: float
    process_seconds: float


@dataclass(slots=True)
class WrittenPart:
    modality: str
    month: str
    ticker: str
    kind: str
    part_id: int
    job_id: str
    event_path: str
    origin_path: str
    metadata_path: str
    event_rows: int
    origin_rows: int
    context_rows: int
    ordinal_min: int
    ordinal_max: int
    timestamp_min_us: int
    timestamp_max_us: int
    bytes_written: int
    fetch_query_id: str
    fetch_seconds: float
    process_seconds: float
    write_seconds: float
    row_count: int = 0
    output_paths: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class WorkerSlot:
    process_name: str
    worker_id: int
    status: str = "idle"
    current_job: str = "-"
    completed: int = 0
    total: int = 0
    rate: float = 0.0
    started_at: float = 0.0
    updated_at: float = field(default_factory=time.perf_counter)


@dataclass(slots=True)
class ModalityStats:
    name: str
    expected_units: int = 0
    fetched_units: int = 0
    processed_units: int = 0
    written_units: int = 0
    fetch_jobs: int = 0
    fetch_done: int = 0
    process_done: int = 0
    write_done: int = 0
    fetch_queue_depth: int = 0
    process_queue_depth: int = 0
    write_queue_depth: int = 0
    workers: list[WorkerSlot] = field(default_factory=list)


class PayloadQueue:
    def __init__(self, name: str, max_bytes: int) -> None:
        self.name = name
        self.max_bytes = max(1, int(max_bytes))
        self._queue: queue.Queue[Any] = queue.Queue()
        self._condition = threading.Condition()
        self._bytes = 0

    @property
    def depth(self) -> int:
        return self._queue.qsize()

    @property
    def bytes(self) -> int:
        with self._condition:
            return self._bytes

    def put(self, item: Any, estimated_bytes: int, stop_event: threading.Event) -> None:
        size = max(0, int(estimated_bytes))
        with self._condition:
            while size > 0 and self._bytes + size > self.max_bytes and not stop_event.is_set():
                self._condition.wait(timeout=0.25)
            if stop_event.is_set():
                raise RuntimeError(f"Stop requested before enqueue to {self.name}.")
            self._bytes += size
        self._queue.put((item, size))

    def put_sentinel(self) -> None:
        self._queue.put((None, 0))

    def get(self, timeout: float = 0.5) -> Any:
        item, size = self._queue.get(timeout=timeout)
        if size:
            with self._condition:
                self._bytes = max(0, self._bytes - int(size))
                self._condition.notify_all()
        return item

    def task_done(self) -> None:
        self._queue.task_done()

    def join(self) -> None:
        self._queue.join()

    def drain(self) -> int:
        drained = 0
        while True:
            try:
                _item, size = self._queue.get_nowait()
            except queue.Empty:
                break
            if size:
                with self._condition:
                    self._bytes = max(0, self._bytes - int(size))
                    self._condition.notify_all()
            self._queue.task_done()
            drained += 1
        return drained


@dataclass(slots=True)
class PipelineQueues:
    fetch_queue: Any
    process_queue: PayloadQueue
    write_queue: PayloadQueue


class ActiveQueries:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queries: dict[str, str] = {}

    def register(self, query_id: str, label: str) -> None:
        with self._lock:
            self._queries[str(query_id)] = str(label)

    def unregister(self, query_id: str) -> None:
        with self._lock:
            self._queries.pop(str(query_id), None)

    def snapshot(self) -> dict[str, str]:
        with self._lock:
            return dict(self._queries)


class BuildState:
    def __init__(self, *, cache_root: Path, cache_id: str, months: tuple[str, ...], data_groups: tuple[str, ...], report_path: Path) -> None:
        self.cache_root = cache_root
        self.cache_id = cache_id
        self.months = months
        self.data_groups = data_groups
        self.report_path = report_path
        self.started_at = time.perf_counter()
        self.status = "starting"
        self.last_error = ""
        self.messages: deque[str] = deque(maxlen=12)
        self.modalities: dict[str, ModalityStats] = {}
        self.completed_parts: list[WrittenPart] = []
        self.errors: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def modality(self, name: str) -> ModalityStats:
        with self._lock:
            return self.modalities[name]

    def message(self, text: str) -> None:
        line = f"{dt.datetime.now().strftime('%H:%M:%S')} {text}"
        with self._lock:
            self.messages.append(line)
        print(line, flush=True)

    def add_part(self, part: WrittenPart) -> None:
        with self._lock:
            self.completed_parts.append(part)

    def add_error(self, *, worker: str, error: BaseException, job_id: str = "") -> None:
        item = {"worker": worker, "job_id": job_id, "error": repr(error), "traceback": traceback.format_exc(), "utc": utc_now()}
        with self._lock:
            self.errors.append(item)
            self.last_error = repr(error)


class DailyIndexStreamingDashboard:
    def __init__(
        self,
        state: BuildState,
        *,
        refresh_per_second: float,
        progress_screen: bool,
        progress_layout: str,
        worker_detail: bool,
    ) -> None:
        self.state = state
        self.refresh_per_second = max(0.5, float(refresh_per_second))
        self.progress_screen = bool(progress_screen)
        self.progress_layout = str(progress_layout)
        self.worker_detail = bool(worker_detail)
        self._live = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._rich_enabled = False

    def __enter__(self) -> "DailyIndexStreamingDashboard":
        if self.progress_layout != "text":
            try:
                from rich.live import Live

                self._live = Live(
                    self._render(),
                    refresh_per_second=self.refresh_per_second,
                    transient=False,
                    auto_refresh=False,
                    screen=self.progress_screen,
                    vertical_overflow="crop",
                )
                self._live.start(refresh=True)
                self._rich_enabled = True
                self._thread = threading.Thread(target=self._refresh_loop, name="daily-index-dashboard", daemon=True)
                self._thread.start()
            except Exception as exc:  # noqa: BLE001
                self._rich_enabled = False
                print(f"Rich progress unavailable; falling back to text progress: {exc!r}", flush=True)
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        if self._live is not None:
            self.refresh()
            self._live.stop()

    def refresh(self) -> None:
        if self._live is not None:
            self._live.update(self._render(), refresh=True)

    def _refresh_loop(self) -> None:
        interval = 1.0 / self.refresh_per_second
        while not self._stop.wait(interval):
            self.refresh()

    def _render(self) -> object:
        from rich import box
        from rich.console import Group
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text

        terminal = shutil.get_terminal_size((160, 48))
        compact = (not self.worker_detail) or terminal.lines < 58
        message_limit = 4 if compact else 8
        with self.state._lock:
            modalities = {name: snapshot_modality(modality) for name, modality in self.state.modalities.items()}
            messages = list(self.state.messages)
            status = self.state.status
            last_error = self.state.last_error
            parts = len(self.state.completed_parts)
            errors = len(self.state.errors)

        elapsed = max(0.001, time.perf_counter() - self.state.started_at)
        done_units = sum(int(item["written_units"]) for item in modalities.values())
        total_units = sum(int(item["expected_units"]) for item in modalities.values())
        progress = done_units / total_units if total_units > 0 else 0.0
        rate = done_units / elapsed if elapsed > 0 else 0.0
        remaining = max(0, total_units - done_units)
        eta = remaining / rate if rate > 0 else 0.0

        summary = Table(box=box.SIMPLE, expand=True, show_edge=False)
        summary.add_column("Metric", style="cyan", no_wrap=True)
        summary.add_column("Value", no_wrap=True)
        summary.add_column("Detail")
        summary.add_row("Status", status.upper(), f"errors={errors:,} parts={parts:,}")
        summary.add_row("Cache", self.state.cache_id, str(self.state.cache_root))
        summary.add_row("Months", ", ".join(self.state.months), f"groups={','.join(self.state.data_groups)}")
        summary.add_row("Overall", f"{progress * 100.0:.2f}%", f"units={done_units:,}/{total_units:,} rate={rate:,.0f}/s eta={format_seconds(eta)}")
        summary.add_row("Elapsed", format_seconds(elapsed), f"rss={current_rss_mib():,.1f} MiB")
        if last_error:
            summary.add_row("Last error", last_error, "")

        panels: list[Any] = [
            Panel(summary, title="Daily-Indexed Streaming Cache", box=box.ROUNDED, border_style="red" if last_error else "green", padding=(0, 1))
        ]

        modality_panels = [self._modality_panel(name, modalities.get(name) or empty_modality_snapshot(name), compact=compact) for name in MODALITY_PANEL_NAMES]
        panels.append(two_column_panel_grid(modality_panels))

        msg_table = Table(box=box.SIMPLE, show_header=False, expand=True)
        msg_table.add_column("Message")
        for line in messages[-message_limit:]:
            msg_table.add_row(Text(line, overflow="fold"))
        panels.append(Panel(msg_table, title="Messages / Errors", box=box.ROUNDED, border_style="yellow", padding=(0, 1)))
        return Group(*panels)

    def _modality_panel(self, name: str, modality: dict[str, Any], *, compact: bool) -> object:
        from rich import box
        from rich.panel import Panel
        from rich.table import Table

        expected = int(modality["expected_units"])
        written = int(modality["written_units"])
        progress = written / expected if expected > 0 else 0.0
        workers = list(modality["workers"])
        if compact:
            table = Table(box=box.SIMPLE, expand=True)
            table.add_column("Stage", no_wrap=True, style="cyan")
            table.add_column("Workers", justify="right", no_wrap=True)
            table.add_column("Progress", overflow="fold")
            table.add_column("Active job", overflow="fold")
            table.add_row("Overall", str(len(workers)), f"{progress * 100.0:.1f}%  {written:,}/{expected:,}", queue_detail(modality))
            if not workers:
                table.add_row("Pipeline", "0", "disabled", "not selected in --data-groups")
            for process_name in ("Fetch", "Process", "Write"):
                process_workers = [worker for worker in workers if str(worker.get("process_name")) == process_name]
                if not process_workers:
                    continue
                active = [
                    worker
                    for worker in process_workers
                    if str(worker.get("status") or "idle") not in {"idle", "done", "stopped"}
                ]
                completed_jobs = int(modality.get(f"{process_name.lower()}_done") or 0)
                total_jobs = int(modality.get("fetch_jobs") or 0)
                completed_rows = sum(int(worker.get("completed") or 0) for worker in process_workers)
                total_rows = sum(int(worker.get("total") or 0) for worker in process_workers)
                rate = sum(float(worker.get("rate") or 0.0) for worker in active or process_workers)
                active_job = "-"
                if active:
                    active_job = str(active[0].get("current_job") or "-")
                    if len(active) > 1:
                        active_job = f"{active_job} (+{len(active) - 1})"
                progress_text = f"{completed_jobs:,}/{total_jobs:,} jobs"
                if total_rows > 0:
                    progress_text = f"{progress_text}  rows {completed_rows:,}/{total_rows:,}"
                if rate > 0:
                    progress_text = f"{progress_text}  {rate:,.0f}/s"
                table.add_row(process_name, f"{len(active):,}/{len(process_workers):,}", progress_text, active_job)
            return Panel(table, title=name, box=box.ROUNDED, border_style="blue", padding=(0, 1))

        table = Table(box=box.SIMPLE, expand=True)
        table.add_column("Process", no_wrap=True, style="cyan")
        table.add_column("W", justify="right", no_wrap=True)
        table.add_column("Status", overflow="fold")
        table.add_column("Current job", overflow="fold")
        table.add_row("Overall", "-", f"{progress * 100.0:.2f}%  units {written:,}/{expected:,}", queue_detail(modality))
        if not workers:
            table.add_row("Pipeline", "-", "disabled", "not selected in --data-groups")
        for worker in workers:
            table.add_row(worker["process_name"], f"{int(worker['worker_id']):02d}", worker_status_text(worker), str(worker["current_job"]))
        return Panel(table, title=name, box=box.ROUNDED, border_style="blue", padding=(0, 1))


def two_column_panel_grid(panels: list[Any]) -> object:
    from rich.table import Table

    grid = Table.grid(expand=True)
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    for index in range(0, len(panels), 2):
        left = panels[index]
        right = panels[index + 1] if index + 1 < len(panels) else ""
        grid.add_row(left, right)
    return grid


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a daily-index streaming ticker/month SSD cache.")
    parser.add_argument("--database", default="market_sip_compact")
    parser.add_argument("--sec-context-database", default=TICKER_MONTH_DEFAULTS["sec_context_database"])
    parser.add_argument("--q-live-database", default=TICKER_MONTH_DEFAULTS["q_live_database"])
    parser.add_argument("--events-table", default="events")
    parser.add_argument("--events-ticker-day-index-table", default="events_ticker_day_index")
    parser.add_argument("--condition-token-reference-table", default=TICKER_MONTH_DEFAULTS["condition_token_reference_table"])
    parser.add_argument("--macro-bars-table", default=TICKER_MONTH_DEFAULTS["macro_bars_table"])
    parser.add_argument("--news-token-table", default=TICKER_MONTH_DEFAULTS["news_token_table"])
    parser.add_argument("--sec-filing-text-token-table", default=TICKER_MONTH_DEFAULTS["sec_filing_text_token_table"])
    parser.add_argument("--news-embedding-table", default=TICKER_MONTH_DEFAULTS["news_embedding_table"])
    parser.add_argument("--sec-filing-text-embedding-table", default=TICKER_MONTH_DEFAULTS["sec_filing_text_embedding_table"])
    parser.add_argument("--sec-xbrl-context-table", default=TICKER_MONTH_DEFAULTS["sec_xbrl_context_table"])
    parser.add_argument("--category-reference-table", default=TICKER_MONTH_DEFAULTS["category_reference_table"])
    parser.add_argument("--stock-split-table", default=TICKER_MONTH_DEFAULTS["stock_split_table"])
    parser.add_argument("--cash-dividend-table", default=TICKER_MONTH_DEFAULTS["cash_dividend_table"])
    parser.add_argument("--cache-root", default=str(DEFAULT_CACHE_ROOT))
    parser.add_argument("--cache-id", default="")
    parser.add_argument("--month", action="append", default=[], help="Month to build, YYYY-MM. Repeatable.")
    parser.add_argument("--start-utc", default="")
    parser.add_argument("--end-utc", default="")
    parser.add_argument("--tickers", default="")
    parser.add_argument("--data-groups", default=DEFAULT_DATA_GROUPS)
    parser.add_argument("--event-context-rows", type=int, default=1024)
    parser.add_argument("--event-context-guard-rows", type=int, default=0)
    parser.add_argument(
        "--event-context-year-lookback",
        type=int,
        default=5,
        help="How many prior yearly event tables to include for beginning-of-month event context.",
    )
    parser.add_argument("--chunk-size", type=int, default=2_000_000, help="Max event rows per fetch/write job.")
    parser.add_argument("--event-fetch-workers", type=int, default=16)
    parser.add_argument("--event-process-workers", type=int, default=2)
    parser.add_argument("--event-write-workers", type=int, default=8)
    parser.add_argument("--label-fetch-workers", type=int, default=6)
    parser.add_argument("--label-process-workers", type=int, default=20)
    parser.add_argument("--label-write-workers", type=int, default=6)
    parser.add_argument("--macro-fetch-workers", type=int, default=3)
    parser.add_argument("--macro-process-workers", type=int, default=6)
    parser.add_argument("--macro-write-workers", type=int, default=3)
    parser.add_argument("--news-fetch-workers", type=int, default=3)
    parser.add_argument("--news-process-workers", type=int, default=2)
    parser.add_argument("--news-write-workers", type=int, default=2)
    parser.add_argument("--sec-fetch-workers", type=int, default=3)
    parser.add_argument("--sec-process-workers", type=int, default=2)
    parser.add_argument("--sec-write-workers", type=int, default=2)
    parser.add_argument("--xbrl-fetch-workers", type=int, default=3)
    parser.add_argument("--xbrl-process-workers", type=int, default=4)
    parser.add_argument("--xbrl-write-workers", type=int, default=2)
    parser.add_argument("--corporate-fetch-workers", type=int, default=1)
    parser.add_argument("--corporate-process-workers", type=int, default=1)
    parser.add_argument("--corporate-write-workers", type=int, default=1)
    parser.add_argument("--events-per-chunk", type=int, default=TICKER_MONTH_DEFAULTS["events_per_chunk"])
    parser.add_argument("--short-context-chunks", type=int, default=TICKER_MONTH_DEFAULTS["short_context_chunks"])
    parser.add_argument("--context-chunk-stride-events", type=int, default=TICKER_MONTH_DEFAULTS["context_chunk_stride_events"])
    parser.add_argument("--short-context-stride-chunks", type=int, default=TICKER_MONTH_DEFAULTS["short_context_stride_chunks"])
    parser.add_argument("--long-context-lags", default=TICKER_MONTH_DEFAULTS["long_context_lags"])
    parser.add_argument("--sample-stride-events", type=int, default=TICKER_MONTH_DEFAULTS["sample_stride_events"])
    parser.add_argument("--macro-lookback-days", type=int, default=TICKER_MONTH_DEFAULTS["macro_lookback_days"])
    parser.add_argument("--label-lookahead-days", type=int, default=TICKER_MONTH_DEFAULTS["label_lookahead_days"])
    parser.add_argument("--news-lookback-days", type=int, default=TICKER_MONTH_DEFAULTS["news_lookback_days"])
    parser.add_argument("--sec-lookback-days", type=int, default=TICKER_MONTH_DEFAULTS["sec_lookback_days"])
    parser.add_argument("--xbrl-lookback-days", type=int, default=TICKER_MONTH_DEFAULTS["xbrl_lookback_days"])
    parser.add_argument("--ticker-news-items", type=int, default=TICKER_MONTH_DEFAULTS["ticker_news_items"])
    parser.add_argument("--market-news-items", type=int, default=TICKER_MONTH_DEFAULTS["market_news_items"])
    parser.add_argument("--sec-filing-items", type=int, default=TICKER_MONTH_DEFAULTS["sec_filing_items"])
    parser.add_argument("--ticker-news-prior-items", type=int, default=TICKER_MONTH_DEFAULTS["ticker_news_prior_items"])
    parser.add_argument("--market-news-prior-items", type=int, default=TICKER_MONTH_DEFAULTS["market_news_prior_items"])
    parser.add_argument("--sec-filing-prior-items", type=int, default=TICKER_MONTH_DEFAULTS["sec_filing_prior_items"])
    parser.add_argument("--xbrl-items", type=int, default=TICKER_MONTH_DEFAULTS["xbrl_items"])
    parser.add_argument("--xbrl-prior-rows", type=int, default=TICKER_MONTH_DEFAULTS["xbrl_prior_rows"])
    parser.add_argument("--corporate-action-items", type=int, default=TICKER_MONTH_DEFAULTS["corporate_action_items"])
    parser.add_argument("--corporate-action-lookback-days", type=int, default=TICKER_MONTH_DEFAULTS["corporate_action_lookback_days"])
    parser.add_argument("--corporate-action-label-days", default=TICKER_MONTH_DEFAULTS["corporate_action_label_days"])
    parser.add_argument("--intraday-label-horizons", default=TICKER_MONTH_DEFAULTS["intraday_label_horizons"])
    parser.add_argument("--intraday-context-horizons", default=TICKER_MONTH_DEFAULTS["intraday_context_horizons"])
    parser.add_argument("--skip-xbrl", action="store_true")
    parser.add_argument("--skip-corporate-actions", action="store_true")
    parser.add_argument("--skip-token-contexts", action="store_true")
    parser.add_argument("--max-fetched-queue-gib", type=float, default=96.0)
    parser.add_argument("--max-processed-queue-gib", type=float, default=96.0)
    parser.add_argument("--max-active-clickhouse-queries", type=int, default=16)
    parser.add_argument("--max-active-writers", type=int, default=8)
    parser.add_argument("--max-threads", type=int, default=8)
    parser.add_argument("--max-memory-usage", default="120G")
    parser.add_argument("--clickhouse-query-retries", type=int, default=2)
    parser.add_argument("--clickhouse-query-retry-backoff-seconds", type=float, default=2.0)
    parser.add_argument("--clickhouse-url", default="")
    parser.add_argument("--user", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text"), default="auto")
    parser.add_argument("--progress-refresh-per-second", type=float, default=1.0)
    parser.add_argument("--progress-screen", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--progress-worker-detail",
        action="store_true",
        help="Show one Rich dashboard row per worker. By default panels use compact per-stage rows so all modalities fit on screen.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--replace-existing", action="store_true")
    parser.add_argument("--shutdown-timeout-seconds", type=float, default=15.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_env_files(discover_clickhouse_env_files(), verbose=True)
    data_groups = normalize_data_groups(args.data_groups)
    months = parse_months(args)
    cache_id = args.cache_id or default_cache_id(months=months, data_groups=data_groups)
    cache_root = Path(args.cache_root) / cache_id
    report_path = cache_root / "build_log.jsonl"
    errors_path = cache_root / "errors.jsonl"
    client_opts = client_options(args)
    stop_event = threading.Event()
    active_queries = ActiveQueries()
    query_slots = threading.Semaphore(max(1, int(args.max_active_clickhouse_queries)))
    writer_slots = threading.Semaphore(max(1, int(args.max_active_writers)))
    build_state = BuildState(cache_root=cache_root, cache_id=cache_id, months=months, data_groups=data_groups, report_path=report_path)
    initialize_modality_stats(build_state, args=args, data_groups=data_groups)
    prepare_cache_root(cache_root=cache_root, replace_existing=bool(args.replace_existing), dry_run=bool(args.dry_run))

    def _handle_signal(signum: int, _frame: object) -> None:
        build_state.status = "interrupted"
        build_state.message(f"Interrupt received signal={signum}; stopping queues and cancelling active ClickHouse queries.")
        stop_event.set()
        cancel_active_queries(client_opts=client_opts, active_queries=active_queries)
        cancel_legacy_active_clickhouse_queries(client_opts=client_opts, stats=None)

    previous_sigint = signal.getsignal(signal.SIGINT)
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    try:
        return run(args=args, client_opts=client_opts, months=months, cache_root=cache_root, report_path=report_path, errors_path=errors_path, build_state=build_state, stop_event=stop_event, active_queries=active_queries, query_slots=query_slots, writer_slots=writer_slots)
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)


def run(
    *,
    args: argparse.Namespace,
    client_opts: Mapping[str, str],
    months: tuple[str, ...],
    cache_root: Path,
    report_path: Path,
    errors_path: Path,
    build_state: BuildState,
    stop_event: threading.Event,
    active_queries: ActiveQueries,
    query_slots: threading.Semaphore,
    writer_slots: threading.Semaphore,
) -> int:
    build_state.status = "planning"
    with DailyIndexStreamingDashboard(
        build_state,
        refresh_per_second=args.progress_refresh_per_second,
        progress_screen=args.progress_screen,
        progress_layout=args.progress_layout,
        worker_detail=bool(args.progress_worker_detail),
    ):
        config = build_config_from_args(args)
        units = query_event_daily_units(args=args, client_opts=client_opts, months=months, active_queries=active_queries, query_slots=query_slots)
        if not units:
            raise RuntimeError("No events_ticker_day_index rows found for requested months/tickers.")
        event_jobs, units_by_package = build_event_jobs(args=args, units=units)
        data_groups = set(build_state.data_groups)
        if "events" not in data_groups:
            event_jobs = []
        modality_jobs = build_modality_jobs(args=args, units_by_package=units_by_package, data_groups=build_state.data_groups)
        expected_units = 0
        total_jobs = len(event_jobs)
        if "events" in data_groups:
            event_stats = build_state.modalities["Events"]
            event_stats.expected_units = sum(job.expected_rows for job in event_jobs)
            event_stats.fetch_jobs = len(event_jobs)
            expected_units += int(event_stats.expected_units)
        for jobs_for_group in modality_jobs.values():
            if not jobs_for_group:
                continue
            modality_name = MODALITY_BY_DATA_GROUP[jobs_for_group[0].data_group]
            stats = build_state.modalities[modality_name]
            stats.fetch_jobs = len(jobs_for_group)
            stats.expected_units = sum(max(1, int(job.expected_rows)) for job in jobs_for_group)
            expected_units += int(stats.expected_units)
            total_jobs += len(jobs_for_group)
        write_json_atomic(cache_root / "manifest.json", root_manifest(args=args, months=months, status="running", jobs=total_jobs, expected_units=expected_units))
        append_jsonl(report_path, {"event": "plan", "months": months, "daily_units": len(units), "fetch_jobs": total_jobs, "expected_units": expected_units, "data_groups": list(build_state.data_groups), "utc": utc_now()})
        build_state.message(f"planned groups={','.join(build_state.data_groups)} daily_units={len(units):,} fetch_jobs={total_jobs:,} expected_units={expected_units:,}")
        if args.dry_run:
            write_json_atomic(cache_root / "manifest.json", root_manifest(args=args, months=months, status="dry_run", jobs=total_jobs, expected_units=expected_units))
            return 0

        pipelines: dict[str, PipelineQueues] = {}
        if "events" in data_groups:
            fetch_queue: queue.Queue[EventFetchJob | None] = queue.Queue()
            process_queue = PayloadQueue("events_process_queue", int(float(args.max_fetched_queue_gib) * 1024**3))
            write_queue = PayloadQueue("events_write_queue", int(float(args.max_processed_queue_gib) * 1024**3))
            for job in event_jobs:
                fetch_queue.put(job)
            for _ in range(max(1, int(args.event_fetch_workers))):
                fetch_queue.put(None)
            pipelines["events"] = PipelineQueues(fetch_queue=fetch_queue, process_queue=process_queue, write_queue=write_queue)
        for data_group, jobs_for_group in modality_jobs.items():
            fetch_queue_generic: queue.Queue[ModalityJob | None] = queue.Queue()
            process_queue_generic = PayloadQueue(f"{data_group}_process_queue", int(float(args.max_fetched_queue_gib) * 1024**3))
            write_queue_generic = PayloadQueue(f"{data_group}_write_queue", int(float(args.max_processed_queue_gib) * 1024**3))
            for job in jobs_for_group:
                fetch_queue_generic.put(job)
            for _ in range(max(1, worker_count(args, data_group, "fetch"))):
                fetch_queue_generic.put(None)
            pipelines[data_group] = PipelineQueues(fetch_queue=fetch_queue_generic, process_queue=process_queue_generic, write_queue=write_queue_generic)

        build_state.status = "running"
        threads: list[threading.Thread] = []
        if "events" in data_groups:
            event_stats = build_state.modalities["Events"]
            event_pipeline = pipelines["events"]
            for slot in [worker for worker in event_stats.workers if worker.process_name == "Fetch"]:
                thread = threading.Thread(target=event_fetch_worker, name=f"event-fetch-{slot.worker_id:02d}", args=(slot, args, client_opts, event_pipeline.fetch_queue, event_pipeline.process_queue, build_state, stop_event, active_queries, query_slots))
                thread.start()
                threads.append(thread)
            for slot in [worker for worker in event_stats.workers if worker.process_name == "Process"]:
                thread = threading.Thread(target=event_process_worker, name=f"event-process-{slot.worker_id:02d}", args=(slot, event_pipeline.process_queue, event_pipeline.write_queue, build_state, stop_event))
                thread.start()
                threads.append(thread)
            for slot in [worker for worker in event_stats.workers if worker.process_name == "Write"]:
                thread = threading.Thread(target=event_write_worker, name=f"event-write-{slot.worker_id:02d}", args=(slot, event_pipeline.write_queue, build_state, stop_event, writer_slots))
                thread.start()
                threads.append(thread)
        for data_group in sorted(modality_jobs):
            modality_name = MODALITY_BY_DATA_GROUP[data_group]
            stats = build_state.modalities[modality_name]
            pipeline = pipelines[data_group]
            for slot in [worker for worker in stats.workers if worker.process_name == "Fetch"]:
                thread = threading.Thread(target=generic_fetch_worker, name=f"{data_group}-fetch-{slot.worker_id:02d}", args=(slot, data_group, args, client_opts, config, pipeline.fetch_queue, pipeline.process_queue, build_state, stop_event, active_queries, query_slots))
                thread.start()
                threads.append(thread)
            for slot in [worker for worker in stats.workers if worker.process_name == "Process"]:
                thread = threading.Thread(target=generic_process_worker, name=f"{data_group}-process-{slot.worker_id:02d}", args=(slot, data_group, pipeline.process_queue, pipeline.write_queue, build_state, stop_event))
                thread.start()
                threads.append(thread)
            for slot in [worker for worker in stats.workers if worker.process_name == "Write"]:
                thread = threading.Thread(target=generic_write_worker, name=f"{data_group}-write-{slot.worker_id:02d}", args=(slot, data_group, pipeline.write_queue, build_state, stop_event, writer_slots))
                thread.start()
                threads.append(thread)

        while not stop_event.is_set():
            update_queue_depths(build_state=build_state, pipelines=pipelines)
            if all_modalities_complete(build_state):
                break
            if build_state.errors:
                stop_event.set()
                break
            time.sleep(0.25)

        if stop_event.is_set() or build_state.errors:
            drained = drain_pipelines(pipelines)
            build_state.message(f"shutdown drain fetch={drained['fetch']:,} process={drained['process']:,} write={drained['write']:,}")
        else:
            join_and_stop_pipelines(args=args, build_state=build_state, pipelines=pipelines)
        for thread in threads:
            thread.join(timeout=max(1.0, float(args.shutdown_timeout_seconds)))

        if build_state.errors:
            for item in build_state.errors:
                append_jsonl(errors_path, item)
            build_state.status = "error"
            write_json_atomic(cache_root / "manifest.json", root_manifest(args=args, months=months, status="error", jobs=total_jobs, expected_units=expected_units, state=build_state))
            raise RuntimeError(f"Daily-index streaming cache failed with {len(build_state.errors):,} error(s). See {errors_path}.")
        if stop_event.is_set():
            build_state.status = "interrupted"
            write_json_atomic(cache_root / "manifest.json", root_manifest(args=args, months=months, status="interrupted", jobs=total_jobs, expected_units=expected_units, state=build_state))
            return 130

        write_package_manifests(cache_root=cache_root, units_by_package=units_by_package, parts=build_state.completed_parts, data_groups=build_state.data_groups)
        write_global_manifests(cache_root=cache_root, months=months, parts=build_state.completed_parts, data_groups=build_state.data_groups)
        build_state.status = "complete"
        write_json_atomic(cache_root / "manifest.json", root_manifest(args=args, months=months, status="complete", jobs=total_jobs, expected_units=expected_units, state=build_state))
        append_jsonl(report_path, {"event": "complete", "parts": len(build_state.completed_parts), "written_units": sum(modality.written_units for modality in build_state.modalities.values()), "utc": utc_now()})
        build_state.message(f"complete parts={len(build_state.completed_parts):,} written_units={sum(modality.written_units for modality in build_state.modalities.values()):,}")
        return 0


def event_fetch_worker(
    slot: WorkerSlot,
    args: argparse.Namespace,
    client_opts: Mapping[str, str],
    fetch_queue: "queue.Queue[EventFetchJob | None]",
    process_queue: PayloadQueue,
    state: BuildState,
    stop_event: threading.Event,
    active_queries: ActiveQueries,
    query_slots: threading.Semaphore,
) -> None:
    stats = state.modality("Events")
    while not stop_event.is_set():
        try:
            job = fetch_queue.get(timeout=0.5)
        except queue.Empty:
            continue
        try:
            if job is None:
                slot.status = "idle"
                slot.current_job = "-"
                return
            slot.started_at = time.perf_counter()
            slot.status = "fetching"
            slot.current_job = job_label(job)
            slot.total = job.expected_rows
            slot.completed = 0
            payload = fetch_events(args=args, client_opts=client_opts, job=job, active_queries=active_queries, query_slots=query_slots)
            slot.completed = payload.row_count
            slot.rate = payload.row_count / max(0.001, payload.seconds)
            process_queue.put(payload, payload.estimated_bytes, stop_event)
            stats.fetched_units += payload.row_count
            stats.fetch_done += 1
            slot.status = "done"
        except Exception as exc:  # noqa: BLE001
            state.add_error(worker=f"Fetch {slot.worker_id:02d}", error=exc, job_id=getattr(job, "job_id", ""))
            stop_event.set()
        finally:
            fetch_queue.task_done()


def event_process_worker(slot: WorkerSlot, process_queue: PayloadQueue, write_queue: PayloadQueue, state: BuildState, stop_event: threading.Event) -> None:
    stats = state.modality("Events")
    while not stop_event.is_set():
        try:
            payload = process_queue.get(timeout=0.5)
        except queue.Empty:
            continue
        try:
            if payload is None:
                slot.status = "idle"
                slot.current_job = "-"
                return
            slot.started_at = time.perf_counter()
            slot.status = "processing"
            slot.current_job = job_label(payload.job)
            slot.total = payload.row_count
            processed = process_events(payload)
            slot.completed = int(processed.events.height)
            slot.rate = slot.completed / max(0.001, processed.process_seconds)
            write_queue.put(processed, processed.estimated_bytes, stop_event)
            stats.processed_units += int(processed.events.height)
            stats.process_done += 1
            slot.status = "done"
        except Exception as exc:  # noqa: BLE001
            state.add_error(worker=f"Process {slot.worker_id:02d}", error=exc, job_id=getattr(getattr(payload, "job", None), "job_id", ""))
            stop_event.set()
        finally:
            process_queue.task_done()


def event_write_worker(slot: WorkerSlot, write_queue: PayloadQueue, state: BuildState, stop_event: threading.Event, writer_slots: threading.Semaphore) -> None:
    stats = state.modality("Events")
    while not stop_event.is_set():
        try:
            payload = write_queue.get(timeout=0.5)
        except queue.Empty:
            continue
        try:
            if payload is None:
                slot.status = "idle"
                slot.current_job = "-"
                return
            slot.started_at = time.perf_counter()
            slot.status = "writing"
            slot.current_job = job_label(payload.job)
            slot.total = int(payload.events.height)
            with writer_slots:
                part = write_event_payload(state.cache_root, payload)
            slot.completed = int(payload.events.height)
            slot.rate = part.bytes_written / max(0.001, part.write_seconds)
            state.add_part(part)
            stats.written_units += int(payload.events.height)
            stats.write_done += 1
            slot.status = "done"
        except Exception as exc:  # noqa: BLE001
            state.add_error(worker=f"Write {slot.worker_id:02d}", error=exc, job_id=getattr(getattr(payload, "job", None), "job_id", ""))
            stop_event.set()
        finally:
            write_queue.task_done()


def generic_fetch_worker(
    slot: WorkerSlot,
    data_group: str,
    args: argparse.Namespace,
    client_opts: Mapping[str, str],
    config: Any,
    fetch_queue: "queue.Queue[ModalityJob | None]",
    process_queue: PayloadQueue,
    state: BuildState,
    stop_event: threading.Event,
    active_queries: ActiveQueries,
    query_slots: threading.Semaphore,
) -> None:
    stats = state.modality(MODALITY_BY_DATA_GROUP[data_group])
    while not stop_event.is_set():
        try:
            job = fetch_queue.get(timeout=0.5)
        except queue.Empty:
            continue
        try:
            if job is None:
                slot.status = "idle"
                slot.current_job = "-"
                return
            slot.started_at = time.perf_counter()
            slot.status = "fetching"
            slot.current_job = generic_job_label(job)
            slot.total = max(1, int(job.expected_rows))
            payload = fetch_generic_modality(args=args, client_opts=client_opts, config=config, job=job, active_queries=active_queries, query_slots=query_slots)
            slot.completed = max(1, payload.row_count)
            slot.rate = slot.completed / max(0.001, payload.seconds)
            process_queue.put(payload, payload.estimated_bytes, stop_event)
            stats.fetched_units += progress_units_for_job(job)
            stats.fetch_done += 1
            slot.status = "done"
        except Exception as exc:  # noqa: BLE001
            state.add_error(worker=f"{MODALITY_BY_DATA_GROUP[data_group]} Fetch {slot.worker_id:02d}", error=exc, job_id=getattr(job, "job_id", ""))
            stop_event.set()
        finally:
            fetch_queue.task_done()


def generic_process_worker(slot: WorkerSlot, data_group: str, process_queue: PayloadQueue, write_queue: PayloadQueue, state: BuildState, stop_event: threading.Event) -> None:
    stats = state.modality(MODALITY_BY_DATA_GROUP[data_group])
    while not stop_event.is_set():
        try:
            payload = process_queue.get(timeout=0.5)
        except queue.Empty:
            continue
        try:
            if payload is None:
                slot.status = "idle"
                slot.current_job = "-"
                return
            slot.started_at = time.perf_counter()
            slot.status = "processing"
            slot.current_job = generic_job_label(payload.job)
            slot.total = max(1, payload.row_count)
            processed = process_generic_modality(payload)
            slot.completed = max(1, sum(int(frame.height) for frame in processed.frames.values()))
            slot.rate = slot.completed / max(0.001, processed.process_seconds)
            write_queue.put(processed, processed.estimated_bytes, stop_event)
            stats.processed_units += progress_units_for_job(processed.job)
            stats.process_done += 1
            slot.status = "done"
        except Exception as exc:  # noqa: BLE001
            state.add_error(worker=f"{MODALITY_BY_DATA_GROUP[data_group]} Process {slot.worker_id:02d}", error=exc, job_id=getattr(getattr(payload, "job", None), "job_id", ""))
            stop_event.set()
        finally:
            process_queue.task_done()


def generic_write_worker(slot: WorkerSlot, data_group: str, write_queue: PayloadQueue, state: BuildState, stop_event: threading.Event, writer_slots: threading.Semaphore) -> None:
    stats = state.modality(MODALITY_BY_DATA_GROUP[data_group])
    while not stop_event.is_set():
        try:
            payload = write_queue.get(timeout=0.5)
        except queue.Empty:
            continue
        try:
            if payload is None:
                slot.status = "idle"
                slot.current_job = "-"
                return
            slot.started_at = time.perf_counter()
            slot.status = "writing"
            slot.current_job = generic_job_label(payload.job)
            slot.total = max(1, int(payload.audit.get("row_count", 0)))
            with writer_slots:
                parts = write_generic_payload(state.cache_root, payload)
            rows = sum(max(1, int(part.row_count)) for part in parts)
            bytes_written = sum(int(part.bytes_written) for part in parts)
            slot.completed = rows
            slot.rate = bytes_written / max(0.001, sum(float(part.write_seconds) for part in parts))
            for part in parts:
                state.add_part(part)
            stats.written_units += progress_units_for_job(payload.job)
            stats.write_done += 1
            slot.status = "done"
        except Exception as exc:  # noqa: BLE001
            state.add_error(worker=f"{MODALITY_BY_DATA_GROUP[data_group]} Write {slot.worker_id:02d}", error=exc, job_id=getattr(getattr(payload, "job", None), "job_id", ""))
            stop_event.set()
        finally:
            write_queue.task_done()


def fetch_events(
    *,
    args: argparse.Namespace,
    client_opts: Mapping[str, str],
    job: EventFetchJob,
    active_queries: ActiveQueries,
    query_slots: threading.Semaphore,
) -> FetchedPayload:
    started = time.perf_counter()
    query_id = f"{QUERY_ID_PREFIX}{threading.get_ident()}_{uuid.uuid4().hex}"
    active_queries.register(query_id, job_label(job))
    query = event_query(args=args, job=job)
    try:
        with query_slots:
            frame = query_polars(client_opts=client_opts, query=query, query_id=query_id, retries=int(args.clickhouse_query_retries), backoff_seconds=float(args.clickhouse_query_retry_backoff_seconds))
    finally:
        active_queries.unregister(query_id)
    seconds = time.perf_counter() - started
    estimated = frame_estimated_bytes(frame)
    return FetchedPayload(job=job, frame=frame, row_count=int(frame.height), estimated_bytes=estimated, query_id=query_id, seconds=seconds)


def process_events(payload: FetchedPayload) -> ProcessedPayload:
    started = time.perf_counter()
    pl = polars()
    frame = payload.frame
    job = payload.job
    if int(frame.height) != int(job.expected_rows):
        raise RuntimeError(f"{job.job_id} expected {job.expected_rows:,} rows, fetched {int(frame.height):,}.")
    if int(frame.height) > 0:
        ticker_count = int(frame.get_column("ticker").n_unique())
        if ticker_count != 1:
            raise RuntimeError(f"{job.job_id} expected one ticker, found {ticker_count:,}.")
        ordinals = frame.get_column("ordinal").to_numpy()
        if ordinals.size and (int(ordinals[0]) != int(job.ordinal_start) or int(ordinals[-1]) != int(job.ordinal_end)):
            raise RuntimeError(f"{job.job_id} fetched ordinal bounds {int(ordinals[0]):,}->{int(ordinals[-1]):,}, expected {job.ordinal_start:,}->{job.ordinal_end:,}.")
        if ordinals.size > 1 and not bool((ordinals[1:] == ordinals[:-1] + 1).all()):
            raise RuntimeError(f"{job.job_id} fetched event ordinals are not contiguous.")
    context_only = job.kind == "context"
    events = frame.with_columns(pl.lit(bool(context_only)).cast(pl.Boolean).alias("context_only"))
    if job.kind == "context":
        origins = empty_origins()
    else:
        origins = (
            events
            .select(
                pl.col("ticker"),
                pl.col("ticker_id"),
                pl.col("ordinal").alias("origin_ordinal"),
                pl.col("timestamp_us").alias("origin_timestamp_us"),
                pl.col("local_date").cast(pl.Utf8).alias("origin_local_date"),
                pl.col("local_session_us").alias("origin_local_session_us"),
            )
            .with_columns(
                (pl.col("ticker") + pl.lit("|") + pl.col("origin_ordinal").cast(pl.Utf8)).alias("origin_key"),
                pl.int_range(0, pl.len(), eager=False).alias("event_row_offset"),
            )
            .with_row_index("origin_id")
            .select(["origin_id", "origin_key", "ticker", "ticker_id", "origin_ordinal", "origin_timestamp_us", "origin_local_date", "origin_local_session_us", "event_row_offset"])
        )
    audit = {
        "row_count": int(events.height),
        "origin_count": int(origins.height),
        "context_count": int(events.filter(pl.col("context_only")).height) if int(events.height) else 0,
        "ordinal_min": int(events.get_column("ordinal")[0]) if int(events.height) else 0,
        "ordinal_max": int(events.get_column("ordinal")[-1]) if int(events.height) else 0,
        "timestamp_min_us": int(events.get_column("timestamp_us").min()) if int(events.height) else 0,
        "timestamp_max_us": int(events.get_column("timestamp_us").max()) if int(events.height) else 0,
    }
    estimated = frame_estimated_bytes(events) + frame_estimated_bytes(origins)
    return ProcessedPayload(
        job=job,
        events=events,
        origins=origins,
        audit=audit,
        estimated_bytes=estimated,
        fetch_query_id=payload.query_id,
        fetch_seconds=payload.seconds,
        process_seconds=time.perf_counter() - started,
    )


def write_event_payload(cache_root: Path, payload: ProcessedPayload) -> WrittenPart:
    started = time.perf_counter()
    job = payload.job
    package = package_dir(cache_root=cache_root, month=job.month, ticker=job.ticker)
    prefix = f"part_{job.part_id:08d}_{job.kind}_{safe_token(job.source_date or job.month)}_{job.ordinal_start}_{job.ordinal_end}"
    event_path = package / "events" / f"{prefix}.parquet"
    origin_path = package / "origins" / f"{prefix}.parquet"
    meta_path = package / "event_metadata" / f"{prefix}.json"
    event_result = write_parquet(payload.events.select([column for column in DEFAULT_EVENT_COLUMNS if column in payload.events.columns]), event_path)
    origin_result = write_parquet(payload.origins, origin_path)
    metadata = {
        "cache_version": "daily_index_streaming_cache_v1",
        "modality": "events",
        "month": job.month,
        "ticker": job.ticker,
        "kind": job.kind,
        "part_id": job.part_id,
        "job_id": job.job_id,
        "source_date": job.source_date,
        "event_rows": event_result["rows"],
        "origin_rows": origin_result["rows"],
        "context_rows": int(payload.audit["context_count"]),
        "ordinal_min": payload.audit["ordinal_min"],
        "ordinal_max": payload.audit["ordinal_max"],
        "timestamp_min_us": payload.audit["timestamp_min_us"],
        "timestamp_max_us": payload.audit["timestamp_max_us"],
        "event_path": rel_path(event_path, cache_root),
        "origin_path": rel_path(origin_path, cache_root),
        "fetch_query_id": payload.fetch_query_id,
        "fetch_seconds": payload.fetch_seconds,
        "process_seconds": payload.process_seconds,
        "created_at_utc": utc_now(),
    }
    write_json_atomic(meta_path, metadata)
    written = WrittenPart(
        modality="events",
        month=job.month,
        ticker=job.ticker,
        kind=job.kind,
        part_id=job.part_id,
        job_id=job.job_id,
        event_path=rel_path(event_path, cache_root),
        origin_path=rel_path(origin_path, cache_root),
        metadata_path=rel_path(meta_path, cache_root),
        event_rows=event_result["rows"],
        origin_rows=origin_result["rows"],
        context_rows=int(payload.audit["context_count"]),
        ordinal_min=int(payload.audit["ordinal_min"]),
        ordinal_max=int(payload.audit["ordinal_max"]),
        timestamp_min_us=int(payload.audit["timestamp_min_us"]),
        timestamp_max_us=int(payload.audit["timestamp_max_us"]),
        bytes_written=event_result["bytes"] + origin_result["bytes"] + int(meta_path.stat().st_size),
        fetch_query_id=payload.fetch_query_id,
        fetch_seconds=payload.fetch_seconds,
        process_seconds=payload.process_seconds,
        write_seconds=time.perf_counter() - started,
    )
    return written


def fetch_generic_modality(
    *,
    args: argparse.Namespace,
    client_opts: Mapping[str, str],
    config: Any,
    job: ModalityJob,
    active_queries: ActiveQueries,
    query_slots: threading.Semaphore,
) -> GenericFetchedPayload:
    started = time.perf_counter()
    query_id = f"{QUERY_ID_PREFIX}{job.data_group}_{threading.get_ident()}_{uuid.uuid4().hex}"
    active_queries.register(query_id, generic_job_label(job))
    try:
        with query_slots:
            frames = query_generic_frames(args=args, client_opts=client_opts, config=config, job=job, query_id=query_id)
    finally:
        active_queries.unregister(query_id)
    seconds = time.perf_counter() - started
    rows = sum(int(frame.height) for frame in frames.values())
    estimated = sum(frame_estimated_bytes(frame) for frame in frames.values())
    return GenericFetchedPayload(job=job, frames=frames, row_count=rows, estimated_bytes=estimated, query_id=query_id, seconds=seconds)


def query_generic_frames(*, args: argparse.Namespace, client_opts: Mapping[str, str], config: Any, job: ModalityJob, query_id: str) -> dict[str, Any]:
    window = cache_month_window(job.month)
    if job.data_group == "intraday_labels":
        event_job = EventFetchJob(
            modality="events",
            job_id=job.job_id,
            month=job.month,
            ticker=job.ticker,
            kind="origin",
            part_id=job.part_id,
            ordinal_start=job.ordinal_start,
            ordinal_end=job.ordinal_end,
            expected_rows=max(0, int(job.ordinal_end) - int(job.ordinal_start) + 1),
        )
        return {"events": query_polars(client_opts=client_opts, query=event_query(args=args, job=event_job), query_id=query_id, retries=int(args.clickhouse_query_retries), backoff_seconds=float(args.clickhouse_query_retry_backoff_seconds))}
    if job.data_group == "macro_bars":
        symbols = tuple(config.global_symbols) if job.scope == "global" else (job.ticker,)
        return {"macro_bars": _query_daily_bars(args, client_opts, config, window, symbols=symbols)}
    if job.data_group == "news":
        if job.scope == "global":
            return {"market_news_embeddings": _query_market_news(args, client_opts, config, window)}
        return {"news_embeddings": _query_ticker_news(args, client_opts, config, window, job.ticker)}
    if job.data_group == "sec":
        return {"sec_embeddings": _query_sec_tokens(args, client_opts, config, window, job.ticker)}
    if job.data_group == "xbrl":
        if bool(args.skip_xbrl):
            return {"xbrl": polars().DataFrame()}
        return {"xbrl": _query_xbrl(args, client_opts, config, window, job.ticker)}
    if job.data_group == "corporate_actions":
        if bool(args.skip_corporate_actions):
            return {"corporate_actions": polars().DataFrame()}
        return {"corporate_actions": _query_corporate_actions(args, client_opts, config, window, job.ticker)}
    raise ValueError(f"Unsupported generic data group: {job.data_group}")


def process_generic_modality(payload: GenericFetchedPayload) -> GenericProcessedPayload:
    started = time.perf_counter()
    job = payload.job
    frames = dict(payload.frames)
    if job.data_group == "intraday_labels":
        events = frames.get("events")
        if events is None:
            raise RuntimeError(f"{job.job_id} missing intraday event source frame.")
        frames = {
            "intraday_base_bars": _build_intraday_base_bars(events),
            "intraday_condition_events": _build_intraday_condition_events(events),
        }
    audit = {
        "row_count": sum(int(frame.height) for frame in frames.values()),
        "frames": {name: int(frame.height) for name, frame in frames.items()},
    }
    return GenericProcessedPayload(
        job=job,
        frames=frames,
        audit=audit,
        estimated_bytes=sum(frame_estimated_bytes(frame) for frame in frames.values()),
        fetch_query_id=payload.query_id,
        fetch_seconds=payload.seconds,
        process_seconds=time.perf_counter() - started,
    )


def write_generic_payload(cache_root: Path, payload: GenericProcessedPayload) -> list[WrittenPart]:
    started = time.perf_counter()
    job = payload.job
    output_root = generic_output_dir(cache_root=cache_root, job=job)
    output_root.mkdir(parents=True, exist_ok=True)
    parts: list[WrittenPart] = []
    for name, frame in payload.frames.items():
        file_name = generic_output_file_name(job, name)
        path = output_root / file_name
        result = write_parquet(frame, path)
        meta_path = output_root / f"{file_name}.json"
        metadata = {
            "cache_version": "daily_index_streaming_cache_v1",
            "modality": job.modality,
            "data_group": job.data_group,
            "month": job.month,
            "ticker": job.ticker,
            "scope": job.scope,
            "part_id": job.part_id,
            "job_id": job.job_id,
            "frame": name,
            "rows": result["rows"],
            "path": rel_path(path, cache_root),
            "fetch_query_id": payload.fetch_query_id,
            "fetch_seconds": payload.fetch_seconds,
            "process_seconds": payload.process_seconds,
            "created_at_utc": utc_now(),
        }
        write_json_atomic(meta_path, metadata)
        row_count = int(result["rows"])
        parts.append(
            WrittenPart(
                modality=job.modality,
                month=job.month,
                ticker=job.ticker,
                kind=job.scope,
                part_id=job.part_id,
                job_id=job.job_id,
                event_path=rel_path(path, cache_root),
                origin_path="",
                metadata_path=rel_path(meta_path, cache_root),
                event_rows=0,
                origin_rows=0,
                context_rows=0,
                ordinal_min=0,
                ordinal_max=0,
                timestamp_min_us=0,
                timestamp_max_us=0,
                bytes_written=int(result["bytes"]) + int(meta_path.stat().st_size),
                fetch_query_id=payload.fetch_query_id,
                fetch_seconds=payload.fetch_seconds,
                process_seconds=payload.process_seconds,
                write_seconds=time.perf_counter() - started,
                row_count=row_count,
                output_paths={name: rel_path(path, cache_root)},
            )
        )
    return parts


def query_event_daily_units(
    *,
    args: argparse.Namespace,
    client_opts: Mapping[str, str],
    months: tuple[str, ...],
    active_queries: ActiveQueries,
    query_slots: threading.Semaphore,
) -> list[EventDailyUnit]:
    start = month_start(months[0])
    end = add_months(month_start(months[-1]), 1)
    ticker_filter = ticker_filter_sql(args.tickers)
    table = f"{quote_ident(args.database)}.{quote_ident(args.events_ticker_day_index_table)}"
    query = f"""
SELECT
    upper(ticker) AS ticker,
    source_date,
    toUInt64(event_count) AS event_count,
    toUInt64(first_ordinal) AS first_ordinal,
    toUInt64(last_ordinal) AS last_ordinal,
    toUInt64(next_ordinal) AS next_ordinal,
    toUInt64(first_sip_timestamp_us) AS first_sip_timestamp_us,
    toUInt64(last_sip_timestamp_us) AS last_sip_timestamp_us,
    toUInt32(build_step) AS build_step,
    toString(built_at) AS built_at
FROM {table}
WHERE source_date >= toDate({sql_string(start.isoformat())})
  AND source_date < toDate({sql_string(end.isoformat())})
  {ticker_filter}
ORDER BY source_date ASC, ticker ASC
"""
    query_id = f"{QUERY_ID_PREFIX}plan_{uuid.uuid4().hex}"
    active_queries.register(query_id, "events daily index plan")
    try:
        with query_slots:
            frame = query_polars(client_opts=client_opts, query=query, query_id=query_id, retries=int(args.clickhouse_query_retries), backoff_seconds=float(args.clickhouse_query_retry_backoff_seconds))
    finally:
        active_queries.unregister(query_id)
    units: list[EventDailyUnit] = []
    for row in frame.iter_rows(named=True):
        source_date = str(row["source_date"])[:10]
        month = source_date[:7]
        if month not in months:
            continue
        units.append(
            EventDailyUnit(
                month=month,
                ticker=str(row["ticker"]).upper(),
                source_date=source_date,
                event_count=int(row["event_count"]),
                first_ordinal=int(row["first_ordinal"]),
                last_ordinal=int(row["last_ordinal"]),
                next_ordinal=int(row["next_ordinal"]),
                first_sip_timestamp_us=int(row["first_sip_timestamp_us"]),
                last_sip_timestamp_us=int(row["last_sip_timestamp_us"]),
                build_step=int(row["build_step"]),
                built_at=str(row["built_at"]),
            )
        )
    return units


def build_event_jobs(args: argparse.Namespace, units: list[EventDailyUnit]) -> tuple[list[EventFetchJob], dict[tuple[str, str], list[EventDailyUnit]]]:
    by_package: dict[tuple[str, str], list[EventDailyUnit]] = defaultdict(list)
    for unit in units:
        by_package[(unit.month, unit.ticker)].append(unit)
    jobs: list[EventFetchJob] = []
    part_id = 0
    max_rows = max(1, int(args.chunk_size))
    for (month, ticker), package_units in sorted(by_package.items()):
        ordered = sorted(package_units, key=lambda item: (item.source_date, item.first_ordinal))
        origin_first = int(ordered[0].first_ordinal)
        context_start = max(1, origin_first - max(1, int(args.event_context_rows)) + 1 - max(0, int(args.event_context_guard_rows)))
        context_end = origin_first - 1
        if context_start <= context_end:
            part_id += 1
            jobs.append(
                EventFetchJob(
                    modality="events",
                    job_id=f"{month}|{ticker}|context|{context_start}|{context_end}",
                    month=month,
                    ticker=ticker,
                    kind="context",
                    part_id=part_id,
                    ordinal_start=context_start,
                    ordinal_end=context_end,
                    expected_rows=context_end - context_start + 1,
                    origin_first_ordinal=origin_first,
                    origin_last_ordinal=int(ordered[-1].last_ordinal),
                )
            )
        for unit in ordered:
            start = int(unit.first_ordinal)
            split_index = 0
            while start <= int(unit.last_ordinal):
                end = min(int(unit.last_ordinal), start + max_rows - 1)
                part_id += 1
                split_index += 1
                jobs.append(
                    EventFetchJob(
                        modality="events",
                        job_id=f"{unit.month}|{unit.ticker}|{unit.source_date}|{split_index:04d}|{start}|{end}",
                        month=unit.month,
                        ticker=unit.ticker,
                        kind="origin",
                        part_id=part_id,
                        ordinal_start=start,
                        ordinal_end=end,
                        expected_rows=end - start + 1,
                        source_date=unit.source_date,
                        event_date_start=unit.source_date,
                        event_date_end=(dt.date.fromisoformat(unit.source_date) + dt.timedelta(days=1)).isoformat(),
                        origin_first_ordinal=unit.first_ordinal,
                        origin_last_ordinal=unit.last_ordinal,
                        first_sip_timestamp_us=unit.first_sip_timestamp_us,
                        last_sip_timestamp_us=unit.last_sip_timestamp_us,
                    )
                )
                start = end + 1
    return jobs, by_package


def build_modality_jobs(
    *,
    args: argparse.Namespace,
    units_by_package: Mapping[tuple[str, str], list[EventDailyUnit]],
    data_groups: tuple[str, ...],
) -> dict[str, list[ModalityJob]]:
    groups = set(data_groups)
    jobs: dict[str, list[ModalityJob]] = {group: [] for group in groups if group != "events"}
    for group in sorted(jobs):
        part_id = 0
        months_seen: set[str] = set()
        for (month, ticker), units in sorted(units_by_package.items()):
            ordered = sorted(units, key=lambda item: (item.source_date, item.first_ordinal))
            origin_first = int(ordered[0].first_ordinal)
            origin_last = int(ordered[-1].last_ordinal)
            expected_rows = sum(int(unit.event_count) for unit in ordered)
            if group == "news" and month not in months_seen:
                part_id += 1
                jobs[group].append(ModalityJob(modality=MODALITY_BY_DATA_GROUP[group], data_group=group, job_id=f"{month}|__GLOBAL__|news", month=month, ticker="__GLOBAL__", scope="global", part_id=part_id, expected_rows=1))
                months_seen.add(month)
            if group == "macro_bars" and month not in months_seen:
                part_id += 1
                jobs[group].append(ModalityJob(modality=MODALITY_BY_DATA_GROUP[group], data_group=group, job_id=f"{month}|__GLOBAL__|macro", month=month, ticker="__GLOBAL__", scope="global", part_id=part_id, expected_rows=1))
                months_seen.add(month)
            part_id += 1
            jobs[group].append(
                ModalityJob(
                    modality=MODALITY_BY_DATA_GROUP[group],
                    data_group=group,
                    job_id=f"{month}|{ticker}|{group}",
                    month=month,
                    ticker=ticker,
                    scope="ticker",
                    part_id=part_id,
                    expected_rows=expected_rows if group == "intraday_labels" else 1,
                    ordinal_start=origin_first,
                    ordinal_end=origin_last,
                )
            )
    return jobs


def event_query(*, args: argparse.Namespace, job: EventFetchJob) -> str:
    table = events_source_table(args=args, job=job)
    date_filter = ""
    if job.event_date_start and job.event_date_end:
        date_filter = f"AND event_date >= toDate({sql_string(str(job.event_date_start))})\n  AND event_date < toDate({sql_string(str(job.event_date_end))})"
    return f"""
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
    toUInt64(ordinal) AS ordinal,
    event_meta,
    toUInt64(sip_timestamp_us) AS timestamp_us,
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
    toFloat32(greatest(0, least({SESSION_LENGTH_SECOND}, local_second - {SESSION_START_SECOND})) / {float(SESSION_LENGTH_SECOND)}) AS session_progress,
    toUInt8(local_second >= {SESSION_REGULAR_START_SECOND} AND local_second < {SESSION_REGULAR_END_SECOND}) AS is_regular_hours,
    toUInt8(local_second >= {SESSION_START_SECOND} AND local_second < {SESSION_REGULAR_START_SECOND}) AS is_premarket,
    toUInt8(local_second >= {SESSION_REGULAR_END_SECOND} AND local_second < {SESSION_END_SECOND}) AS is_afterhours
FROM {table}
PREWHERE ticker = {sql_string(job.ticker)}
  AND ordinal >= {int(job.ordinal_start)}
  AND ordinal <= {int(job.ordinal_end)}
  {date_filter}
ORDER BY ticker, ordinal
{settings_sql(args)}
"""


def events_source_table(*, args: argparse.Namespace, job: EventFetchJob) -> str:
    base = str(args.events_table)
    if not events_table_uses_year_suffix(base):
        return f"{quote_ident(args.database)}.{quote_ident(base)}"
    month_year = int(job.month[:4])
    years = {month_year}
    if job.kind == "context":
        lookback = max(1, int(args.event_context_year_lookback))
        years.update(range(month_year - lookback, month_year + 1))
    tables = [events_table_for_year(base, year) for year in sorted(years)]
    if len(tables) == 1:
        return f"{quote_ident(args.database)}.{quote_ident(tables[0])}"
    pattern = "^(" + "|".join(table.replace(".", "\\.") for table in tables) + ")$"
    return f"merge({sql_string(args.database)}, {sql_string(pattern)})"


def query_polars(*, client_opts: Mapping[str, str], query: str, query_id: str, retries: int, backoff_seconds: float) -> Any:
    try:
        import clickhouse_connect  # type: ignore
    except ModuleNotFoundError as exc:
        raise RuntimeError("Install clickhouse-connect to run the daily-index streaming cache builder.") from exc
    parsed = urlparse(str(client_opts["clickhouse_url"]))
    secure = parsed.scheme == "https"
    attempt = 0
    while True:
        client = clickhouse_connect.get_client(
            host=parsed.hostname or "localhost",
            port=parsed.port or (8443 if secure else 8123),
            username=str(client_opts.get("user") or "default"),
            password=str(client_opts.get("password") or ""),
            secure=secure,
        )
        try:
            try:
                table = client.query_arrow(query, settings={"query_id": query_id})
            except TypeError:
                table = client.query_arrow(f"/* query_id={query_id} */\n{query}")
            return polars().from_arrow(table)
        except Exception as exc:
            if attempt >= max(0, int(retries)) or not transient_clickhouse_error(exc):
                raise
            time.sleep(max(0.0, float(backoff_seconds)) * float(2**attempt))
            attempt += 1
        finally:
            try:
                client.close()
            except Exception:
                pass


def transient_clickhouse_error(exc: BaseException) -> bool:
    text = repr(exc)
    if "QUERY_WAS_CANCELLED" in text or "DB::Exception" in text:
        return False
    return any(marker in text for marker in ("IncompleteRead", "ProtocolError", "RemoteDisconnected", "Connection reset", "timed out", "Connection broken"))


def write_package_manifests(*, cache_root: Path, units_by_package: Mapping[tuple[str, str], list[EventDailyUnit]], parts: list[WrittenPart], data_groups: tuple[str, ...]) -> None:
    parts_by_package: dict[tuple[str, str], list[WrittenPart]] = defaultdict(list)
    for part in parts:
        if part.ticker != "__GLOBAL__":
            parts_by_package[(part.month, part.ticker)].append(part)
    for (month, ticker), units in units_by_package.items():
        package = package_dir(cache_root=cache_root, month=month, ticker=ticker)
        package.mkdir(parents=True, exist_ok=True)
        write_parquet(polars().DataFrame([asdict(unit) for unit in units]), package / "daily_index.parquet")
        package_parts = sorted(parts_by_package.get((month, ticker), []), key=lambda item: item.part_id)
        event_parts = [part for part in package_parts if part.modality == "events"]
        modality_parts = [part for part in package_parts if part.modality != "events"]
        manifest = {
            "cache_version": "daily_index_streaming_cache_v1",
            "month": month,
            "ticker": ticker,
            "data_groups": list(data_groups),
            "daily_units": len(units),
            "expected_origin_rows": sum(int(unit.event_count) for unit in units),
            "written_event_rows": sum(int(part.event_rows) for part in event_parts),
            "written_origin_rows": sum(int(part.origin_rows) for part in event_parts),
            "written_context_rows": sum(int(part.context_rows) for part in event_parts),
            "written_modality_rows": {name: sum(int(part.row_count) for part in modality_parts if part.modality == name) for name in sorted({part.modality for part in modality_parts})},
            "origin_first_ordinal": min(int(unit.first_ordinal) for unit in units),
            "origin_last_ordinal": max(int(unit.last_ordinal) for unit in units),
            "parts": [asdict(part) for part in event_parts],
            "modality_parts": [asdict(part) for part in modality_parts],
            "audit_status": "pass",
            "completed_at_utc": utc_now(),
        }
        write_json_atomic(package / "manifest.json", manifest)


def write_global_manifests(*, cache_root: Path, months: tuple[str, ...], parts: list[WrittenPart], data_groups: tuple[str, ...]) -> None:
    parts_by_month: dict[str, list[WrittenPart]] = defaultdict(list)
    for part in parts:
        if part.ticker == "__GLOBAL__":
            parts_by_month[part.month].append(part)
    for month in months:
        global_dir = cache_root / f"month={month}" / "global"
        global_dir.mkdir(parents=True, exist_ok=True)
        month_parts = sorted(parts_by_month.get(month, []), key=lambda item: (item.modality, item.part_id, item.event_path))
        manifest = {
            "cache_version": "daily_index_streaming_cache_v1",
            "month": month,
            "scope": "global",
            "data_groups": list(data_groups),
            "written_modality_rows": {name: sum(int(part.row_count) for part in month_parts if part.modality == name) for name in sorted({part.modality for part in month_parts})},
            "modality_parts": [asdict(part) for part in month_parts],
            "audit_status": "pass",
            "completed_at_utc": utc_now(),
        }
        write_json_atomic(global_dir / "manifest.json", manifest)


def root_manifest(*, args: argparse.Namespace, months: tuple[str, ...], status: str, jobs: int, expected_units: int, state: BuildState | None = None) -> dict[str, Any]:
    payload = {
        "cache_format": "daily_index_streaming_cache",
        "cache_version": 1,
        "status": status,
        "months": list(months),
        "data_groups": [item.strip() for item in str(args.data_groups).split(",") if item.strip()],
        "events_table": args.events_table,
        "events_ticker_day_index_table": args.events_ticker_day_index_table,
        "event_context_rows": int(args.event_context_rows),
        "event_context_guard_rows": int(args.event_context_guard_rows),
        "event_context_year_lookback": int(args.event_context_year_lookback),
        "chunk_size": int(args.chunk_size),
        "event_fetch_workers": int(args.event_fetch_workers),
        "event_process_workers": int(args.event_process_workers),
        "event_write_workers": int(args.event_write_workers),
        "fetch_jobs": int(jobs),
        "expected_units": int(expected_units),
        "updated_at_utc": utc_now(),
    }
    if state is not None:
        payload["summary"] = {
            "parts": len(state.completed_parts),
            "errors": len(state.errors),
            "modalities": {
                name: {
                    "expected_units": int(stats.expected_units),
                    "fetched_units": int(stats.fetched_units),
                    "processed_units": int(stats.processed_units),
                    "written_units": int(stats.written_units),
                    "fetch_jobs": int(stats.fetch_jobs),
                    "fetch_done": int(stats.fetch_done),
                    "process_done": int(stats.process_done),
                    "write_done": int(stats.write_done),
                }
                for name, stats in sorted(state.modalities.items())
            },
        }
    return payload


def cancel_active_queries(*, client_opts: Mapping[str, str], active_queries: ActiveQueries) -> int:
    ids = sorted(active_queries.snapshot())
    if not ids:
        return 0
    try:
        import clickhouse_connect  # type: ignore

        parsed = urlparse(str(client_opts["clickhouse_url"]))
        secure = parsed.scheme == "https"
        client = clickhouse_connect.get_client(
            host=parsed.hostname or "localhost",
            port=parsed.port or (8443 if secure else 8123),
            username=str(client_opts.get("user") or "default"),
            password=str(client_opts.get("password") or ""),
            secure=secure,
        )
        quoted = ", ".join(sql_string(query_id) for query_id in ids)
        client.command(f"KILL QUERY WHERE query_id IN ({quoted}) ASYNC")
        client.close()
        return len(ids)
    except Exception:
        return 0


def drain_standard_queue(work_queue: "queue.Queue[Any]") -> int:
    drained = 0
    while True:
        try:
            work_queue.get_nowait()
        except queue.Empty:
            break
        work_queue.task_done()
        drained += 1
    return drained


def prepare_cache_root(*, cache_root: Path, replace_existing: bool, dry_run: bool) -> None:
    if cache_root.exists() and replace_existing and not dry_run:
        resolved = cache_root.resolve()
        parent = resolved.parent
        if str(resolved) in {"", str(parent)}:
            raise RuntimeError(f"Refusing to replace unsafe cache root: {resolved}")
        shutil.rmtree(resolved)
    elif cache_root.exists() and not dry_run:
        manifest = cache_root / "manifest.json"
        if manifest.exists():
            raise RuntimeError(f"Cache root already exists: {cache_root}. Use --replace-existing to rebuild it.")
    cache_root.mkdir(parents=True, exist_ok=True)


def add_workers(modality: ModalityStats, process_name: str, count: int) -> None:
    for index in range(max(0, int(count))):
        modality.workers.append(WorkerSlot(process_name=process_name, worker_id=index + 1))


def normalize_data_groups(value: str) -> tuple[str, ...]:
    raw = [item.strip().lower() for item in str(value).split(",") if item.strip()]
    groups: list[str] = []
    for item in raw:
        canonical = DATA_GROUP_ALIASES.get(item)
        if canonical is None:
            raise ValueError(f"Unsupported data group {item!r}. Supported groups: {', '.join(sorted(set(DATA_GROUP_ALIASES.values())))}")
        if canonical not in groups:
            groups.append(canonical)
    return tuple(groups or ("events",))


def initialize_modality_stats(build_state: BuildState, *, args: argparse.Namespace, data_groups: tuple[str, ...]) -> None:
    for group in data_groups:
        name = MODALITY_BY_DATA_GROUP[group]
        stats = ModalityStats(name=name)
        add_workers(stats, "Fetch", worker_count(args, group, "fetch"))
        add_workers(stats, "Process", worker_count(args, group, "process"))
        add_workers(stats, "Write", worker_count(args, group, "write"))
        build_state.modalities[name] = stats


def worker_count(args: argparse.Namespace, data_group: str, stage: str) -> int:
    prefix = {
        "events": "event",
        "intraday_labels": "label",
        "macro_bars": "macro",
        "news": "news",
        "sec": "sec",
        "xbrl": "xbrl",
        "corporate_actions": "corporate",
    }[data_group]
    return max(1, int(getattr(args, f"{prefix}_{stage}_workers")))


def progress_units_for_job(job: ModalityJob) -> int:
    return max(1, int(job.expected_rows))


def update_queue_depths(*, build_state: BuildState, pipelines: Mapping[str, PipelineQueues]) -> None:
    for group, pipe in pipelines.items():
        stats = build_state.modalities[MODALITY_BY_DATA_GROUP[group]]
        stats.fetch_queue_depth = pipe.fetch_queue.qsize()
        stats.process_queue_depth = pipe.process_queue.depth
        stats.write_queue_depth = pipe.write_queue.depth


def all_modalities_complete(build_state: BuildState) -> bool:
    return all(stats.write_done >= stats.fetch_jobs for stats in build_state.modalities.values())


def drain_pipelines(pipelines: Mapping[str, PipelineQueues]) -> dict[str, int]:
    totals = {"fetch": 0, "process": 0, "write": 0}
    for pipe in pipelines.values():
        totals["fetch"] += drain_standard_queue(pipe.fetch_queue)
        totals["process"] += pipe.process_queue.drain()
        totals["write"] += pipe.write_queue.drain()
    return totals


def join_and_stop_pipelines(*, args: argparse.Namespace, build_state: BuildState, pipelines: Mapping[str, PipelineQueues]) -> None:
    for group, pipe in pipelines.items():
        pipe.fetch_queue.join()
        for _ in range(worker_count(args, group, "process")):
            pipe.process_queue.put_sentinel()
        pipe.process_queue.join()
        for _ in range(worker_count(args, group, "write")):
            pipe.write_queue.put_sentinel()
        pipe.write_queue.join()


def parse_months(args: argparse.Namespace) -> tuple[str, ...]:
    values = [str(item).strip() for item in args.month if str(item).strip()]
    if values:
        return tuple(sorted({validate_month(item) for item in values}))
    if args.start_utc and args.end_utc:
        return full_months_in_period(args.start_utc, args.end_utc)
    raise ValueError("Provide --month YYYY-MM or --start-utc/--end-utc.")


def validate_month(value: str) -> str:
    dt.datetime.strptime(value, "%Y-%m")
    return value


def month_start(month: str) -> dt.date:
    return dt.datetime.strptime(month, "%Y-%m").date().replace(day=1)


def default_cache_id(*, months: tuple[str, ...], data_groups: tuple[str, ...]) -> str:
    start = months[0].replace("-", "")
    end = months[-1].replace("-", "")
    return f"daily_index_{start}_{end}_{'_'.join(data_groups)}"


def client_options(args: argparse.Namespace) -> dict[str, str]:
    return {
        "clickhouse_url": args.clickhouse_url or default_clickhouse_url(),
        "user": args.user or default_clickhouse_user(),
        "password": args.password or default_clickhouse_password(),
    }


def settings_sql(args: argparse.Namespace) -> str:
    settings: list[str] = []
    if int(args.max_threads) > 0:
        settings.append(f"max_threads = {int(args.max_threads)}")
    if str(args.max_memory_usage) != "0":
        settings.append(f"max_memory_usage = {parse_size_bytes(str(args.max_memory_usage))}")
    return "SETTINGS " + ", ".join(settings) if settings else ""


def ticker_filter_sql(text: str) -> str:
    values = sorted({item.strip().upper() for item in str(text).split(",") if item.strip()})
    if not values:
        return ""
    return "AND upper(ticker) IN (" + ", ".join(sql_string(value) for value in values) + ")"


def package_dir(*, cache_root: Path, month: str, ticker: str) -> Path:
    upper = str(ticker).upper()
    return cache_root / f"month={month}" / f"ticker={upper}"


def generic_output_dir(*, cache_root: Path, job: ModalityJob) -> Path:
    if job.scope == "global":
        base = cache_root / f"month={job.month}" / "global"
    else:
        base = package_dir(cache_root=cache_root, month=job.month, ticker=job.ticker)
    folder = {
        "intraday_labels": "intraday_labels",
        "macro_bars": "global_macro_bars" if job.scope == "global" else "macro_bars",
        "news": "market_news_embeddings" if job.scope == "global" else "news_embeddings",
        "sec": "sec_embeddings",
        "xbrl": "xbrl",
        "corporate_actions": "corporate_actions",
    }[job.data_group]
    return base / folder


def generic_output_file_name(job: ModalityJob, frame_name: str) -> str:
    stem = safe_token(frame_name)
    if job.scope == "global":
        return f"{stem}_part_{job.part_id:06d}.parquet"
    return f"{stem}_part_{job.part_id:06d}_{safe_token(job.ticker)}.parquet"


def generic_job_label(job: ModalityJob) -> str:
    target = "__GLOBAL__" if job.scope == "global" else job.ticker
    return f"{job.month} {target} {job.data_group}"


def write_parquet(frame: Any, path: Path) -> dict[str, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    frame.write_parquet(tmp, compression="zstd")
    tmp.replace(path)
    return {"rows": int(getattr(frame, "height", 0) or 0), "bytes": int(path.stat().st_size)}


def empty_origins() -> Any:
    return polars().DataFrame(
        {
            "origin_id": [],
            "origin_key": [],
            "ticker": [],
            "ticker_id": [],
            "origin_ordinal": [],
            "origin_timestamp_us": [],
            "origin_local_date": [],
            "origin_local_session_us": [],
            "event_row_offset": [],
        }
    )


def frame_estimated_bytes(frame: Any) -> int:
    try:
        return int(frame.estimated_size())
    except Exception:
        return int(getattr(frame, "height", 0) or 0) * max(1, int(getattr(frame, "width", 1) or 1)) * 8


def polars() -> Any:
    try:
        import polars as pl  # type: ignore
    except ModuleNotFoundError as exc:
        raise RuntimeError("Install polars to run the daily-index streaming cache builder.") from exc
    return pl


def current_rss_mib() -> float:
    try:
        import psutil  # type: ignore

        return float(psutil.Process(os.getpid()).memory_info().rss) / 1024.0 / 1024.0
    except Exception:
        return 0.0


def snapshot_modality(modality: ModalityStats) -> dict[str, Any]:
    return {
        "name": modality.name,
        "expected_units": modality.expected_units,
        "fetched_units": modality.fetched_units,
        "processed_units": modality.processed_units,
        "written_units": modality.written_units,
        "fetch_jobs": modality.fetch_jobs,
        "fetch_done": modality.fetch_done,
        "process_done": modality.process_done,
        "write_done": modality.write_done,
        "fetch_queue_depth": modality.fetch_queue_depth,
        "process_queue_depth": modality.process_queue_depth,
        "write_queue_depth": modality.write_queue_depth,
        "workers": [asdict(worker) for worker in modality.workers],
    }


def empty_modality_snapshot(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "expected_units": 0,
        "fetched_units": 0,
        "processed_units": 0,
        "written_units": 0,
        "fetch_jobs": 0,
        "fetch_done": 0,
        "process_done": 0,
        "write_done": 0,
        "fetch_queue_depth": 0,
        "process_queue_depth": 0,
        "write_queue_depth": 0,
        "workers": [],
    }


def worker_status_text(worker: Mapping[str, Any]) -> str:
    status = str(worker.get("status") or "idle")
    completed = int(worker.get("completed") or 0)
    total = int(worker.get("total") or 0)
    rate = float(worker.get("rate") or 0.0)
    if total > 0 and status not in {"idle"}:
        return f"{status} {completed:,}/{total:,} {rate:,.0f}/s"
    return status


def queue_detail(modality: Mapping[str, Any]) -> str:
    return (
        f"queues fetch={int(modality['fetch_queue_depth']):,} "
        f"process={int(modality['process_queue_depth']):,} write={int(modality['write_queue_depth']):,}"
    )


def format_seconds(value: float) -> str:
    seconds = max(0, int(value))
    hours, rem = divmod(seconds, 3600)
    minutes, sec = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{sec:02d}"


def safe_token(value: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in str(value))[:64] or "part"


def rel_path(path: Path, root: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")


def job_label(job: EventFetchJob) -> str:
    if job.kind == "context":
        return f"{job.month} {job.ticker} context {job.ordinal_start:,}-{job.ordinal_end:,}"
    return f"{job.month} {job.ticker} {job.source_date} {job.ordinal_start:,}-{job.ordinal_end:,}"


def append_jsonl(path: Path, item: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(jsonable(item), sort_keys=True) + "\n")


def utc_now() -> str:
    return dt.datetime.now(tz=dt.timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
