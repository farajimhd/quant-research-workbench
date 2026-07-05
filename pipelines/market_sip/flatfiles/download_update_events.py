from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import queue as queue_module
import re
import struct
import sys
import time
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists() and (parent / "pipelines").exists())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.market_sip.benchmarks.clickhouse_compact_schema_codec_benchmark import (  # noqa: E402
    QUOTE_SCHEMA_STRING,
    TRADE_SCHEMA_STRING,
    clamp_int32_sql,
    price_int_sql,
    price_precision_clipped_sql,
    scale_code_sql,
    tape_code_sql,
)
from pipelines.market_sip.events.clickhouse_build_unified_events import (  # noqa: E402
    DEFAULT_CONTINUITY_TABLE,
    DEFAULT_EVENTS_TABLE,
    DEFAULT_CONDITION_TOKEN_REFERENCE_TABLE,
    DEFAULT_MANIFEST_TABLE,
    DEFAULT_DROP_TRADE_CORRECTION_CODES,
    CONDITION_TOKEN_SLOTS,
    condition_code_expr,
    condition_token_expr,
    create_continuity_table_sql,
    create_events_table_sql,
    create_manifest_table_sql,
    delete_day_sql,
    condition_token_reference_subquery,
    indicator_code_expr,
    indicator_token_reference_subquery,
    insert_day_manifest,
    latest_day_status,
    mergetree_settings,
    mutation_settings,
    ensure_continuity_table_columns,
    parse_trade_correction_codes,
    query_settings,
    quote_event_meta_expr,
    trade_event_meta_expr,
    validate_events_table_schema,
)
from pipelines.market_sip.events.clickhouse_build_trade_bars import (  # noqa: E402
    DEFAULT_BARS_BY_SYMBOL_TIME_TABLE,
    DEFAULT_BARS_BY_TIME_SYMBOL_TABLE,
    DEFAULT_BARS_TABLE,
    DEFAULT_MACRO_BARS_TABLE,
    bar_table_specs,
    build_macro_bars,
    format_timeframe_ranges,
    format_bar_tables,
    parse_timeframes,
    timeframe_ranges,
)
from pipelines.market_sip.flatfiles.download_massive_sip_flatfiles import (  # noqa: E402
    DEFAULT_AWS_REGION,
    DEFAULT_AWS_SERVICE,
    DEFAULT_CHUNK_BYTES,
    DEFAULT_DISCOVERY,
    DownloadConfig,
    DownloadJob,
    build_remote_jobs,
    download_one,
    env_value,
    ignore_sigint_in_worker,
    parse_kinds,
)
from pipelines.market_sip.ingest.clickhouse_ingest_sip_compact_codec import (  # noqa: E402
    DEFAULT_DATABASE,
    env_status_keys,
)
from pipelines.market_sip.validation.clickhouse_compact_schema_validate_sample import (  # noqa: E402
    price_int,
    price_precision_clipped,
    scale_code,
    tape_code,
    to_decimal_or_zero,
    to_int_or_zero,
)
from pipelines.market_sip.validation.clickhouse_delete_compact_audit_rows import default_clickhouse_url_with_network_fallback  # noqa: E402
from research.mlops.clickhouse import (  # noqa: E402
    DEFAULT_FLATFILES_ROOT_WIN,
    DEFAULT_OUTPUT_ROOT_WIN,
    ClickHouseHttpClient,
    QueryProfile,
    default_clickhouse_file_root,
    default_clickhouse_password,
    default_clickhouse_user,
    default_storage_policy,
    discover_clickhouse_env_files,
    format_optional_int,
    parse_size_bytes,
    quote_ident,
    run_profiled,
    sql_string,
    windows_path_to_clickhouse_path,
)
from research.mlops.env import load_env_files, secret_status  # noqa: E402


DEFAULT_START_DATE = "2025-01-01"
DEFAULT_END_DATE = "2026-12-31"
DEFAULT_DOWNLOAD_WORKERS = 8
DEFAULT_MAX_THREADS = 32
DEFAULT_TEST_TABLE_PREFIX = "test_flatfile_event_update"
DEFAULT_TEST_SAMPLE_SIZE = 100
DEFAULT_TICKER_DAY_INDEX_TABLE = "events_ticker_day_index"
DEFAULT_DIRECT_MACRO_BAR_TIMEFRAMES = ("1d",)

SAFE_TEST_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class DayFiles:
    source_date: str
    quote_job: DownloadJob
    trade_job: DownloadJob


@dataclass(frozen=True, slots=True)
class DayJob:
    source_date: str
    build_step: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download Massive SIP flatfiles and update market_sip_compact.events directly. "
            "Quotes/trades are kept as flatfiles on disk and are not persisted as ClickHouse quote/trade tables."
        )
    )
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url_with_network_fallback())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--events-table", default=DEFAULT_EVENTS_TABLE)
    parser.add_argument("--condition-token-reference-table", default=DEFAULT_CONDITION_TOKEN_REFERENCE_TABLE)
    parser.add_argument("--manifest-table", default=DEFAULT_MANIFEST_TABLE)
    parser.add_argument("--continuity-table", default=DEFAULT_CONTINUITY_TABLE)
    parser.add_argument("--ticker-day-index-table", default=DEFAULT_TICKER_DAY_INDEX_TABLE)
    parser.add_argument("--macro-bars-table", default=DEFAULT_MACRO_BARS_TABLE)
    parser.add_argument("--bars-table", default=DEFAULT_BARS_TABLE)
    parser.add_argument("--bars-by-symbol-time-table", default=DEFAULT_BARS_BY_SYMBOL_TIME_TABLE)
    parser.add_argument("--bars-by-time-symbol-table", default=DEFAULT_BARS_BY_TIME_SYMBOL_TABLE)
    parser.add_argument("--bar-timeframes", default=",".join(DEFAULT_DIRECT_MACRO_BAR_TIMEFRAMES))
    parser.add_argument("--bar-chunk-days", type=int, default=7)
    parser.add_argument("--bar-staging-table", default="_staging_trade_bars")
    parser.add_argument("--bar-keep-staging-table", action="store_true")
    parser.add_argument("--bar-copy-at-end", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--bar-summarize-chunks", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--end-date", default=DEFAULT_END_DATE)
    parser.add_argument("--flatfiles-root-win", default=str(DEFAULT_FLATFILES_ROOT_WIN))
    parser.add_argument("--flatfiles-root-ch", default=default_clickhouse_file_root())
    parser.add_argument("--storage-policy", default=default_storage_policy())
    parser.add_argument(
        "--drop-trade-correction-codes",
        default=DEFAULT_DROP_TRADE_CORRECTION_CODES,
        help="Comma-separated flatfile trade correction codes to exclude from event-table inserts.",
    )
    parser.add_argument("--partition-mode", choices=("month", "ticker_hash", "none"), default="month")
    parser.add_argument("--partition-buckets", type=int, default=256)
    parser.add_argument("--download-workers", type=int, default=DEFAULT_DOWNLOAD_WORKERS)
    parser.add_argument("--max-threads", type=int, default=DEFAULT_MAX_THREADS)
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text"), default="auto")
    parser.add_argument("--progress-refresh-per-second", type=float, default=4.0)
    parser.add_argument("--progress-log-lines", type=int, default=14)
    parser.add_argument("--progress-screen", dest="progress_screen", action="store_true", default=True)
    parser.add_argument("--no-progress-screen", dest="progress_screen", action="store_false")
    parser.add_argument("--max-partitions-per-insert-block", type=int, default=1024)
    parser.add_argument("--max-memory-usage", default="400G")
    parser.add_argument("--output-root-win", default=str(DEFAULT_OUTPUT_ROOT_WIN / "flatfile_event_update"))
    parser.add_argument("--discovery", choices=("remote",), default=DEFAULT_DISCOVERY)
    parser.add_argument("--aws-region", default=DEFAULT_AWS_REGION)
    parser.add_argument("--aws-service", default=DEFAULT_AWS_SERVICE)
    parser.add_argument("--s3-endpoint-url", default="")
    parser.add_argument("--bucket", default="")
    parser.add_argument("--aws-access-key-id", default="")
    parser.add_argument("--aws-secret-access-key", default="")
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    parser.add_argument("--chunk-bytes", type=int, default=DEFAULT_CHUNK_BYTES)
    parser.add_argument("--no-verify-tls", action="store_true")
    parser.add_argument("--overwrite-incomplete", action="store_true", default=True)
    parser.add_argument("--limit-days", type=int, default=0)
    parser.add_argument("--day-offset", type=int, default=0)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--retry-started", action="store_true")
    parser.add_argument("--force-day-delete", action="store_true")
    parser.add_argument("--skip-bars", action="store_true", help="Only update compact events; do not rebuild macro bar rows.")
    parser.add_argument(
        "--bar-replace-range",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Delete and rebuild overlapping bar rows for the updated date range before inserting bars.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--test-mode",
        action="store_true",
        help=(
            "Run a safety build into isolated temp events/manifest/continuity tables, then audit the "
            "temp events against the raw quote/trade CSVs used for the run. Production events are never touched."
        ),
    )
    parser.add_argument(
        "--test-table-prefix",
        default=DEFAULT_TEST_TABLE_PREFIX,
        help="Prefix for isolated test tables created by --test-mode. Must be an identifier-safe non-production prefix.",
    )
    parser.add_argument(
        "--test-sample-size",
        type=int,
        default=DEFAULT_TEST_SAMPLE_SIZE,
        help="Per-kind reference rows sampled from main quotes/trades and matched back to temp events.",
    )
    parser.add_argument(
        "--test-keep-tables",
        action="store_true",
        help="Keep isolated test tables after a successful audit for manual inspection.",
    )
    return parser.parse_args()


def download_config(args: argparse.Namespace) -> DownloadConfig:
    return DownloadConfig(
        endpoint_url=env_value(args.s3_endpoint_url, "S3_ENDPOINT_URL"),
        bucket=env_value(args.bucket, "BUCKET"),
        access_key=env_value(args.aws_access_key_id, "AWS_ACCESS_KEY_ID"),
        secret_key=env_value(args.aws_secret_access_key, "AWS_SECRET_ACCESS_KEY"),
        region=args.aws_region,
        service=args.aws_service,
        timeout_seconds=float(args.timeout_seconds),
        chunk_bytes=int(args.chunk_bytes),
        verify_tls=not bool(args.no_verify_tls),
        overwrite_incomplete=bool(args.overwrite_incomplete),
        dry_run=bool(args.dry_run),
        progress_interval_seconds=5.0,
    )


def source_days(args: argparse.Namespace, config: DownloadConfig) -> list[DayFiles]:
    flatfiles_root = Path(args.flatfiles_root_win)
    jobs = build_remote_jobs(flatfiles_root, args.start_date, args.end_date, parse_kinds("quotes,trades"), config)
    jobs = [normalize_download_job_destination(job, flatfiles_root) for job in jobs]
    by_day: dict[str, dict[str, DownloadJob]] = {}
    for job in jobs:
        by_day.setdefault(job.session_date, {})[job.kind] = job
    days = [
        DayFiles(source_date=source_date, quote_job=kinds["quotes"], trade_job=kinds["trades"])
        for source_date, kinds in sorted(by_day.items())
        if "quotes" in kinds and "trades" in kinds
    ]
    if args.day_offset:
        days = days[int(args.day_offset) :]
    if args.limit_days:
        days = days[: int(args.limit_days)]
    return days


def normalize_download_job_destination(job: DownloadJob, flatfiles_root: Path) -> DownloadJob:
    key_parts = Path(job.key).parts
    root_name = flatfiles_root.name.replace("\\", "/").rstrip("/")
    if key_parts and key_parts[0] == root_name:
        destination = flatfiles_root.joinpath(*key_parts[1:])
    else:
        destination = flatfiles_root.joinpath(*key_parts)
    return DownloadJob(
        kind=job.kind,
        session_date=job.session_date,
        key=job.key,
        destination=str(destination),
        remote_size=job.remote_size,
    )


def split_download_file_jobs(days: list[DayFiles], workers: int) -> list[list[tuple[str, DownloadJob]]]:
    jobs: list[tuple[str, DownloadJob]] = []
    for day in days:
        jobs.append((day.source_date, day.quote_job))
        jobs.append((day.source_date, day.trade_job))
    worker_count = max(1, min(int(workers), len(jobs) or 1))
    chunks: list[list[tuple[str, DownloadJob]]] = [[] for _ in range(worker_count)]
    for index, job in enumerate(jobs):
        chunks[index % worker_count].append(job)
    return chunks


def download_file_worker(
    worker_id: int,
    jobs: list[tuple[str, DownloadJob]],
    config: DownloadConfig,
    result_queue: mp.Queue,
    progress_queue: mp.Queue,
) -> None:
    ignore_sigint_in_worker()
    try:
        for source_date, job in jobs:
            result = download_one(config, job, worker_id, progress_queue)
            result_queue.put({"type": "file", "source_date": source_date, "result": asdict(result)})
    finally:
        result_queue.put({"type": "worker_done", "worker_id": worker_id})


def drain_download_result_queue(
    result_queue: mp.Queue,
    day_file_results: dict[str, dict[str, dict[str, Any]]],
    completed_workers: set[int],
    report_path: Path,
) -> None:
    while True:
        try:
            item = result_queue.get_nowait()
        except queue_module.Empty:
            break
        if item.get("type") == "worker_done":
            completed_workers.add(int(item.get("worker_id", -1)))
            continue
        if item.get("type") == "file":
            result = dict(item["result"])
            source_date = str(item["source_date"])
            kind = str(result.get("kind", ""))
            day_file_results[source_date][kind] = result
            append_jsonl(report_path, {"type": "download_file", **result})


def run_download_phase(
    *,
    days: list[DayFiles],
    config: DownloadConfig,
    args: argparse.Namespace,
    reporter: "UpdateProgressReporter",
    report_path: Path,
    progress_queue: mp.Queue,
) -> dict[str, dict[str, Any]]:
    chunks = split_download_file_jobs(days, max(1, int(args.download_workers)))
    result_queue: mp.Queue = mp.Queue()
    workers = [
        mp.Process(target=download_file_worker, args=(worker_id, chunk, config, result_queue, progress_queue), daemon=False)
        for worker_id, chunk in enumerate(chunks)
        if chunk
    ]
    reporter.configure_download_workers(len(workers))
    day_file_results: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    completed_workers: set[int] = set()
    interrupted = False
    started_at = time.time()
    try:
        for process in workers:
            process.start()
        while len(completed_workers) < len(workers):
            drain_download_progress(progress_queue, {}, reporter=reporter)
            drain_download_result_queue(result_queue, day_file_results, completed_workers, report_path)
            for worker_id, process in enumerate(workers):
                if worker_id in completed_workers:
                    continue
                if not process.is_alive() and process.exitcode is not None:
                    process.join(timeout=0.1)
                    completed_workers.add(worker_id)
                    if process.exitcode != 0:
                        reporter.log(f"download worker {worker_id:02d} exited with code {process.exitcode}")
            time.sleep(0.2)
        drain_download_result_queue(result_queue, day_file_results, completed_workers, report_path)
        drain_download_progress(progress_queue, {}, reporter=reporter, force=True)
    except KeyboardInterrupt:
        interrupted = True
        reporter.log("CTRL+C received. Terminating download workers; completed files remain valid and .part files will be retried.")
        append_jsonl(report_path, {"type": "interrupted", "stage": "download", "elapsed_seconds": time.time() - started_at})
        for process in workers:
            if process.is_alive():
                reporter.log(f"TERM download worker pid={process.pid}")
                process.terminate()
        deadline = time.time() + 5.0
        for process in workers:
            process.join(timeout=max(0.0, deadline - time.time()))
        for process in workers:
            if process.is_alive():
                reporter.log(f"KILL download worker pid={process.pid}")
                process.kill()
                process.join(timeout=2.0)
        drain_download_progress(progress_queue, {}, reporter=reporter, force=True)
        raise SystemExit(130)
    finally:
        if not interrupted:
            for process in workers:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=2.0)
            drain_download_result_queue(result_queue, day_file_results, completed_workers, report_path)

    download_results: dict[str, dict[str, Any]] = {}
    for index, day in enumerate(days, start=1):
        by_kind = day_file_results.get(day.source_date, {})
        statuses = {
            f"{kind}:{day.source_date}": str(row.get("status", "missing_result"))
            for kind, row in sorted(by_kind.items())
        }
        seconds = max((float(row.get("wall_seconds") or 0.0) for row in by_kind.values()), default=0.0)
        ok = all(
            str(by_kind.get(kind, {}).get("status", "")) in {"downloaded", "skipped_complete"}
            for kind in ("quotes", "trades")
        )
        result = {"source_date": day.source_date, "ok": ok, "statuses": statuses, "seconds": seconds}
        download_results[day.source_date] = result
        append_jsonl(report_path, {"type": "download", **result})
        reporter.log(f"download day [{index:,}/{len(days):,}] {day.source_date} ok={ok} seconds={seconds:.1f} statuses={statuses}")
    return download_results


def format_bytes(value: int) -> str:
    size = float(value or 0)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024.0 or unit == "TiB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024.0
    return f"{size:.1f} TiB"


class UpdateProgressReporter:
    def __init__(self, args: argparse.Namespace, *, total_days: int, total_files: int, total_bytes: int) -> None:
        self.mode = str(args.progress_layout)
        self.refresh_per_second = max(1.0, float(args.progress_refresh_per_second))
        self.screen = bool(args.progress_screen)
        self.total_days = max(0, int(total_days))
        self.total_files = max(0, int(total_files))
        self.total_bytes = max(0, int(total_bytes))
        self.started_at = time.time()
        self.stage = "starting"
        self.completed_days = 0
        self.success_days = 0
        self.failed_days = 0
        self.completed_files = 0
        self.completed_transfer_bytes = 0
        self.completed_checked_bytes = 0
        self.file_states: dict[str, dict[str, Any]] = {}
        self.worker_states: dict[int, dict[str, Any]] = {}
        self.day_status: dict[str, str] = {}
        self.task_states: dict[str, dict[str, Any]] = {}
        self.notices: deque[tuple[str, str]] = deque(maxlen=12)
        self.logs: deque[str] = deque(maxlen=max(5, int(args.progress_log_lines)))
        self.download_started_at: float | None = None
        self.download_phase_started_at: float | None = None
        self.download_worker_count = max(1, int(getattr(args, "download_workers", 1) or 1))
        self.download_total_files = max(0, int(total_files))
        self.download_status_counts: dict[str, int] = {}
        self._rich = False
        self._live: Any = None
        self._layout: Any = None
        self._last_text_download = 0.0
        self._last_refresh_at = 0.0

    def __enter__(self) -> "UpdateProgressReporter":
        if self.mode in {"auto", "rich"}:
            try:
                from rich.console import Group
                from rich.layout import Layout
                from rich.live import Live
                from rich.panel import Panel
                from rich.table import Table
                from rich.text import Text
            except ImportError:
                if self.mode == "rich":
                    raise
                self.log("Rich is not installed; using text progress.")
            else:
                self._rich = True
                self._group_cls = Group
                self._layout_cls = Layout
                self._live_cls = Live
                self._panel_cls = Panel
                self._table_cls = Table
                self._text_cls = Text
                self._layout = Layout(name="root")
                self._layout.split_column(
                    Layout(self._summary_panel(), name="summary", size=10),
                    Layout(self._download_panel(), name="download", size=max(10, min(26, self.download_worker_count + 5))),
                    Layout(self._task_panel(), name="tasks", size=10),
                    Layout(self._notice_panel(), name="notices", size=6),
                    Layout(self._log_panel(), name="logs", ratio=1),
                )
                self._live = Live(
                    self._layout,
                    refresh_per_second=self.refresh_per_second,
                    transient=False,
                    vertical_overflow="crop",
                    screen=self.screen,
                    redirect_stdout=True,
                    redirect_stderr=True,
                )
                self._live.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._live is not None:
            self._refresh(force=True)
            self._live.stop()

    def log(self, message: str) -> None:
        line = f"{datetime.now().strftime('%H:%M:%S')} {message}"
        if self._rich:
            self.logs.append(line)
            self._refresh(force=True)
        else:
            print(line, flush=True)

    def set_stage(self, stage: str) -> None:
        self.stage = stage
        self.log(f"stage={stage}")

    def notice(self, message: str, *, style: str = "yellow") -> None:
        line = f"{datetime.now().strftime('%H:%M:%S')} {message}"
        self.notices.append((line, style))
        self.log(message)

    def configure_download_workers(self, worker_count: int) -> None:
        self.download_worker_count = max(1, int(worker_count))
        self.download_phase_started_at = time.time()
        for worker_id in range(self.download_worker_count):
            self.worker_states.setdefault(worker_id, self._empty_worker_state(worker_id))
        if self._rich and self._layout is not None:
            self._layout["download"].size = max(10, min(26, self.download_worker_count + 5))
            self._refresh(force=True)

    def handle_download_event(self, event: dict[str, Any]) -> None:
        label = str(event.get("job_label") or f"{event.get('kind')}:{event.get('session_date')}")
        worker_id = int(event.get("worker_id") or 0)
        worker_state = self.worker_states.setdefault(worker_id, self._empty_worker_state(worker_id))
        if event.get("type") in {"progress", "result"}:
            worker_state.update(
                {
                    "worker_id": worker_id,
                    "job": label,
                    "status": str(event.get("status") or worker_state.get("status") or "running"),
                    "expected": int(event.get("bytes_expected") or worker_state.get("expected") or 0),
                    "written": int(event.get("bytes_written") or worker_state.get("written") or 0),
                    "started_at": float(event.get("started_at") or worker_state.get("started_at") or time.time()),
                    "updated_at": time.time(),
                }
            )
        state = self.file_states.setdefault(label, {})
        previous_type = state.get("type")
        previous_status = state.get("status")
        state.update(event)
        if event.get("status") == "downloading" and self.download_started_at is None:
            self.download_started_at = time.time()
        if event.get("type") == "result" and previous_type != "result":
            self.completed_files += 1
            expected = int(event.get("bytes_expected") or 0)
            written = int(event.get("bytes_written") or 0)
            status = str(event.get("status") or "")
            self.download_status_counts[status] = self.download_status_counts.get(status, 0) + 1
            self.completed_checked_bytes += expected if status in {"downloaded", "skipped_complete", "would_download"} else min(expected, written)
            if status in {"downloaded", "would_download"}:
                self.completed_transfer_bytes += written
            elif status not in {"skipped_complete"}:
                self.completed_transfer_bytes += min(expected, written) if expected else written
            worker_state["status"] = status
            worker_state["written"] = written
            worker_state["expected"] = expected
            if event.get("status") not in {"downloaded", "skipped_complete", "would_download"}:
                self.log(f"download file failed {label} status={event.get('status')} exception={event.get('exception')}")
        elif event.get("status") != previous_status and event.get("status") in {"checking", "downloading"}:
            self.log(f"download {label} {event.get('status')}")
        self._refresh()

    def handle_day_start(self, day: str, index: int) -> None:
        self.day_status[day] = "running"
        self.completed_days = max(self.completed_days, index - 1)
        self.log(f"day start [{index:,}/{self.total_days:,}] {day}")
        self._refresh(force=True)

    def handle_day_done(self, day: str, status: str) -> None:
        prior = self.day_status.get(day)
        self.day_status[day] = status
        if prior != status:
            self.completed_days += 1
            if status == "ok":
                self.success_days += 1
            elif status not in {"skipped", "dry_run"}:
                self.failed_days += 1
        self.log(f"day {status} {day}")
        self._refresh(force=True)

    def task_start(self, key: str, label: str, *, day: str = "", stage: str = "") -> None:
        self.task_states[key] = {
            "label": label,
            "day": day,
            "stage": stage or label,
            "status": "running",
            "started_at": time.time(),
            "seconds": 0.0,
            "rows": "",
            "detail": "",
        }
        self._refresh(force=True)

    def task_done(self, key: str, status: str, *, seconds: float | None = None, rows: int | None = None, detail: str = "") -> None:
        state = self.task_states.setdefault(key, {"label": key, "day": "", "stage": key, "started_at": time.time()})
        state["status"] = status
        state["seconds"] = float(seconds if seconds is not None else time.time() - float(state.get("started_at") or time.time()))
        state["rows"] = "" if rows is None else f"{int(rows):,}"
        state["detail"] = detail
        self._refresh(force=True)

    def _refresh(self, *, force: bool = False) -> None:
        if not self._rich or self._layout is None:
            return
        now = time.time()
        min_interval = 1.0 / self.refresh_per_second
        if not force and now - self._last_refresh_at < min_interval:
            return
        self._last_refresh_at = now
        self._layout["summary"].update(self._summary_panel())
        self._layout["download"].update(self._download_panel())
        self._layout["tasks"].update(self._task_panel())
        self._layout["notices"].update(self._notice_panel())
        self._layout["logs"].update(self._log_panel())

    def _summary_panel(self) -> Any:
        elapsed = max(1e-6, time.time() - self.started_at)
        day_rate = self.completed_days / elapsed if self.completed_days else 0.0
        day_eta = self._format_duration((self.total_days - self.completed_days) / day_rate) if day_rate > 0 else "-"
        transfer_speed = self._run_transfer_speed()
        active_remaining = self._active_download_remaining_bytes()
        download_eta = self._format_duration(active_remaining / transfer_speed) if transfer_speed > 0 and active_remaining > 0 else "-"
        table = self._table_cls.grid(expand=True)
        for _ in range(4):
            table.add_column(ratio=1)
        table.add_row("stage", str(self.stage), "elapsed", self._format_duration(elapsed))
        table.add_row("days", f"{self.completed_days:,}/{self.total_days:,}", "days/min", f"{self._day_completion_rate_per_minute():.2f}")
        table.add_row("day eta", day_eta, "elapsed", self._format_duration(elapsed))
        table.add_row("downloads checked", f"{self.completed_files:,}/{self.download_total_files:,}", "dl eta", download_eta)
        table.add_row("run transfer", self._format_bytes(self._run_transfer_bytes()), "speed", f"{self._format_bytes(transfer_speed)}/s")
        return self._panel_cls(table, title="Flatfile Event Update", border_style="cyan")

    def _download_panel(self) -> Any:
        table = self._table_cls(expand=True, show_header=True, header_style="bold", box=None, pad_edge=False)
        table.add_column("W", width=3, no_wrap=True)
        table.add_column("File", ratio=2, min_width=18, overflow="ellipsis", no_wrap=True)
        table.add_column("Progress", ratio=3, min_width=24, overflow="ellipsis", no_wrap=True)
        table.add_column("Speed", width=11, no_wrap=True)
        table.add_column("ETA", width=8, no_wrap=True)
        table.add_column("State", width=13, overflow="ellipsis", no_wrap=True)
        checked_pct = self.completed_files / self.download_total_files if self.download_total_files else 0.0
        summary = (
            f"{self._mini_bar(checked_pct, 12)} {checked_pct:5.1%} "
            f"files {self.completed_files:,}/{self.download_total_files:,} "
            f"checked {self._format_bytes(self.completed_checked_bytes)}/{self._format_bytes(self.total_bytes)} "
            f"run {self._format_bytes(self._run_transfer_bytes())}"
        )
        table.add_row("all", "summary", summary, f"{self._format_bytes(self._run_transfer_speed())}/s", "-", self._status_counts_cell())
        for worker_id in range(self.download_worker_count):
            state = self.worker_states.get(worker_id, self._empty_worker_state(worker_id))
            expected = int(state.get("expected") or 0)
            written = int(state.get("written") or 0)
            pct = written / expected if expected else 0.0
            table.add_row(
                f"{worker_id:02d}",
                str(state.get("job") or "-"),
                f"{self._mini_bar(pct, 12)} {pct:5.1%} {self._format_bytes(written)}/{self._format_bytes(expected)}",
                self._speed_cell(state),
                self._eta_cell(state),
                self._download_state_cell(str(state.get("status") or "pending")),
            )
        return self._panel_cls(table, title="Downloads", border_style="green")

    def _task_panel(self) -> Any:
        table = self._table_cls(expand=True, show_header=True, header_style="bold", box=None, pad_edge=False)
        table.add_column("Kind", width=12, no_wrap=True)
        table.add_column("Day", width=10, no_wrap=True)
        table.add_column("Status", width=11, no_wrap=True)
        table.add_column("Rows", width=12, justify="right", no_wrap=True)
        table.add_column("Seconds", width=9, justify="right", no_wrap=True)
        table.add_column("Detail", ratio=2, overflow="ellipsis")
        day_pct = self.completed_days / self.total_days if self.total_days else 0.0
        table.add_row("summary", "-", f"{day_pct:5.1%}", f"ok {self.success_days:,}", f"fail {self.failed_days:,}", f"{self._mini_bar(day_pct, 12)} days {self.completed_days:,}/{self.total_days:,}")
        rows = sorted(self.task_states.values(), key=lambda row: float(row.get("started_at") or 0.0), reverse=True)[:8]
        if not rows:
            table.add_row("-", "-", "waiting", "", "", "No insert/audit/bar task started yet.")
        for row in rows:
            seconds = float(row.get("seconds") or (time.time() - float(row.get("started_at") or time.time()) if row.get("status") == "running" else 0.0))
            table.add_row(
                str(row.get("stage") or row.get("label") or "-")[:12],
                str(row.get("day") or "-"),
                str(row.get("status") or "-"),
                str(row.get("rows") or ""),
                f"{seconds:.1f}",
                str(row.get("detail") or row.get("label") or ""),
            )
        return self._panel_cls(table, title="Event Inserts, Audit, Index and Macro Bars", border_style="magenta")

    def _notice_panel(self) -> Any:
        if not self.notices:
            text = self._text_cls("No persistent notices.")
        else:
            text = self._text_cls()
            for line, style in self.notices:
                text.append(line + "\n", style=style)
        return self._panel_cls(text, title="Important Notices", border_style="yellow")

    def _log_panel(self) -> Any:
        text = self._text_cls("\n".join(self.logs) if self.logs else "No messages yet.")
        return self._panel_cls(text, title="Messages", border_style="white")

    def _covered_download_bytes(self) -> int:
        return min(self.total_bytes, self.completed_checked_bytes + self._active_download_bytes())

    def _run_transfer_bytes(self) -> int:
        return max(0, int(self.completed_transfer_bytes) + self._active_download_bytes())

    def _active_download_bytes(self) -> int:
        total = 0
        for state in self.worker_states.values():
            if str(state.get("status") or "") == "downloading":
                total += int(state.get("written") or 0)
        return total

    def _active_download_remaining_bytes(self) -> int:
        total = 0
        for state in self.worker_states.values():
            if str(state.get("status") or "") == "downloading":
                expected = int(state.get("expected") or 0)
                written = int(state.get("written") or 0)
                total += max(0, expected - written)
        return total

    def _run_transfer_speed(self) -> float:
        started_at = self.download_started_at or self.started_at
        elapsed = max(1e-6, time.time() - started_at)
        return self._run_transfer_bytes() / elapsed

    def _day_completion_rate_per_minute(self) -> float:
        elapsed_minutes = max(1e-6, (time.time() - self.started_at) / 60.0)
        return float(self.completed_days) / elapsed_minutes

    def _speed_cell(self, state: dict[str, Any]) -> str:
        written = int(state.get("written") or state.get("bytes_written") or 0)
        started_at = float(state.get("started_at") or 0.0)
        if not written or not started_at:
            return "-"
        return f"{self._format_bytes(written / max(1e-6, time.time() - started_at))}/s"

    def _eta_cell(self, state: dict[str, Any]) -> str:
        expected = int(state.get("expected") or state.get("bytes_expected") or 0)
        written = int(state.get("written") or state.get("bytes_written") or 0)
        started_at = float(state.get("started_at") or 0.0)
        if expected <= 0 or written <= 0 or written >= expected or not started_at:
            return "-"
        elapsed = max(1e-6, time.time() - started_at)
        rate = written / elapsed
        return self._format_duration((expected - written) / rate) if rate > 0 else "-"

    def _status_counts_cell(self) -> str:
        if not self.download_status_counts:
            return "waiting"
        names = {
            "downloaded": "dl",
            "skipped_complete": "skip",
            "would_download": "dry",
            "failed": "err",
            "failed_size_mismatch": "size",
            "missing_remote": "miss",
            "incomplete_existing": "part",
        }
        return " ".join(f"{names.get(status, status[:5])}={count:,}" for status, count in sorted(self.download_status_counts.items()))

    def _download_state_cell(self, status: str) -> str:
        return {
            "idle": "-",
            "checking": "CHK",
            "downloading": "DL",
            "downloaded": "OK",
            "skipped_complete": "SKIP",
            "would_download": "DRY",
            "failed": "ERR",
            "failed_size_mismatch": "SIZE",
            "missing_remote": "MISS",
            "incomplete_existing": "PART",
        }.get(status, status[:8])

    def _empty_worker_state(self, worker_id: int) -> dict[str, Any]:
        return {
            "worker_id": int(worker_id),
            "job": "",
            "status": "idle",
            "expected": 0,
            "written": 0,
            "started_at": 0.0,
            "updated_at": 0.0,
        }

    def _mini_bar(self, fraction: float, width: int) -> str:
        filled = int(round(max(0.0, min(1.0, float(fraction))) * width))
        return "#" * filled + "-" * (width - filled)

    def _format_bytes(self, value: float) -> str:
        size = float(value or 0)
        for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
            if abs(size) < 1024.0 or unit == "TiB":
                return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
            size /= 1024.0
        return f"{size:.1f} TiB"

    def _format_duration(self, seconds: float) -> str:
        seconds = max(0, int(seconds))
        if seconds < 60:
            return f"{seconds}s"
        minutes, sec = divmod(seconds, 60)
        if minutes < 60:
            return f"{minutes}m{sec:02d}s"
        hours, minute = divmod(minutes, 60)
        return f"{hours}h{minute:02d}m"


def drain_download_progress(progress_queue: queue_module.Queue, file_states: dict[str, dict[str, Any]], *, reporter: UpdateProgressReporter | None = None, force: bool = False) -> None:
    saw_event = False
    while True:
        try:
            event = progress_queue.get_nowait()
        except queue_module.Empty:
            break
        saw_event = True
        if reporter is not None:
            reporter.handle_download_event(event)
            continue
        label = str(event.get("job_label") or f"{event.get('kind')}:{event.get('session_date')}")
        state = file_states.setdefault(label, {})
        state.update(event)
        if event.get("type") == "result":
            expected = int(event.get("bytes_expected") or 0)
            written = int(event.get("bytes_written") or 0)
            print(
                f"DOWNLOAD FILE DONE {label} status={event.get('status')} "
                f"bytes={format_bytes(written)}/{format_bytes(expected)}"
                + (f" exception={event.get('exception')}" if event.get("exception") else ""),
                flush=True,
            )
    now = time.time()
    last_print = getattr(drain_download_progress, "_last_print", 0.0)
    if force or saw_event or now - float(last_print) >= 10.0:
        active = [
            (label, state)
            for label, state in sorted(file_states.items())
            if state.get("type") != "result" and state.get("status") not in {"skipped_complete", "downloaded"}
        ]
        if active:
            parts = []
            for label, state in active[:8]:
                expected = int(state.get("bytes_expected") or 0)
                written = int(state.get("bytes_written") or 0)
                pct = (100.0 * written / expected) if expected else 0.0
                parts.append(f"{label} {state.get('status')} {pct:.1f}% {format_bytes(written)}/{format_bytes(expected)}")
            suffix = f" (+{len(active) - 8} more)" if len(active) > 8 else ""
            print("DOWNLOAD LIVE " + " | ".join(parts) + suffix, flush=True)
        drain_download_progress._last_print = now


def event_date_expr_from_us(expr: str) -> str:
    return f"toDate(fromUnixTimestamp64Micro(toInt64({expr}), 'UTC'))"


def quote_clean_predicate() -> str:
    return f"""
ticker != ''
AND toUInt64OrZero(sip_timestamp) > 0
AND toUInt32OrZero(sequence_number) > 0
""".strip()


def trade_clean_predicate(args: argparse.Namespace) -> str:
    correction_filter = flatfile_trade_correction_filter_sql(args)
    return f"""
ticker != ''
AND toUInt64OrZero(sip_timestamp) > 0
AND toUInt32OrZero(sequence_number) > 0
{correction_filter}
""".strip()


def flatfile_trade_correction_filter_sql(args: argparse.Namespace) -> str:
    codes = parse_trade_correction_codes(getattr(args, "drop_trade_correction_codes", ""))
    if not codes:
        return ""
    values = ", ".join(str(code) for code in codes)
    return f"\nAND toUInt8(greatest(0, least(15, toInt16OrZero(correction)))) NOT IN ({values})"


def raw_event_union_sql(args: argparse.Namespace, day: DayFiles) -> str:
    quote_path = windows_path_to_clickhouse_path(Path(day.quote_job.destination), Path(args.flatfiles_root_win), args.flatfiles_root_ch)
    trade_path = windows_path_to_clickhouse_path(Path(day.trade_job.destination), Path(args.flatfiles_root_win), args.flatfiles_root_ch)
    bid_price = "toFloat64OrZero(bid_price)"
    ask_price = "toFloat64OrZero(ask_price)"
    trade_price = "toFloat64OrZero(price)"
    bid_price_int = price_int_sql(bid_price)
    ask_price_int = price_int_sql(ask_price)
    trade_price_int = price_int_sql(trade_price)
    bid_scale = scale_code_sql(bid_price)
    ask_scale = scale_code_sql(ask_price)
    trade_scale = scale_code_sql(trade_price)
    bid_decoded = f"if({bid_scale} = 1, {bid_price_int} / 10000.0, {bid_price_int} / 100.0)"
    ask_decoded = f"if({ask_scale} = 1, {ask_price_int} / 10000.0, {ask_price_int} / 100.0)"
    quote_price_valid = (
        f"({bid_price} > 0 AND {ask_price} > 0 "
        f"AND {bid_price_int} > 0 AND {ask_price_int} > 0 "
        f"AND NOT {price_precision_clipped_sql(bid_price)} "
        f"AND NOT {price_precision_clipped_sql(ask_price)} "
        f"AND {bid_decoded} <= {ask_decoded})"
    )
    trade_price_valid = f"({trade_price} > 0 AND {trade_price_int} > 0 AND NOT {price_precision_clipped_sql(trade_price)})"
    clean_bid_scale = f"if({quote_price_valid}, {bid_scale}, 0)"
    clean_ask_scale = f"if({quote_price_valid}, {ask_scale}, 0)"
    clean_trade_scale = f"if({trade_price_valid}, {trade_scale}, 0)"
    quote_flags = f"toUInt8({clean_bid_scale} + ({clean_ask_scale} * 2) + ({tape_code_sql('tape')} * 4))"
    trade_flags = f"toUInt8({clean_trade_scale} + ({tape_code_sql('tape')} * 2))"
    return f"""
    SELECT
        q.ticker AS ticker,
        {quote_event_meta_expr()} AS event_meta,
        q.sip_timestamp_us AS sip_timestamp_us,
        q.sequence_number_u32 AS sequence_number,
        q.ask_price_int AS price_primary_int,
        q.bid_price_int AS price_secondary_int,
        toFloat32(q.ask_size_u32) AS size_primary,
        toFloat32(q.bid_size_u32) AS size_secondary,
        q.ask_exchange_u8 AS exchange_primary,
        q.bid_exchange_u8 AS exchange_secondary,
        {condition_token_expr("qc1")} AS condition_token_1,
        {condition_token_expr("qc2")} AS condition_token_2,
        {condition_token_expr("qc3")} AS condition_token_3,
        {condition_token_expr("qc4")} AS condition_token_4,
        {condition_token_expr("qi1")} AS condition_token_5,
        q.event_date AS event_date
    FROM
    (
        SELECT
            ticker,
            toUInt64(intDiv(toUInt64OrZero(sip_timestamp), 1000)) AS sip_timestamp_us,
            toUInt32OrZero(sequence_number) AS sequence_number_u32,
            toUInt32(if({quote_price_valid}, {bid_price_int}, 0)) AS bid_price_int,
            toUInt32(if({quote_price_valid}, {ask_price_int}, 0)) AS ask_price_int,
            toUInt32(if(toFloat64OrZero(bid_size) > 0, toFloat64OrZero(bid_size), 0)) AS bid_size_u32,
            toUInt32(if(toFloat64OrZero(ask_size) > 0, toFloat64OrZero(ask_size), 0)) AS ask_size_u32,
            toUInt8OrZero(bid_exchange) AS bid_exchange_u8,
            toUInt8OrZero(ask_exchange) AS ask_exchange_u8,
            conditions,
            indicators,
            {quote_flags} AS quote_flags,
            {event_date_expr_from_us("intDiv(toUInt64OrZero(sip_timestamp), 1000)")} AS event_date,
            {condition_code_expr(1)} AS condition_code_1,
            {condition_code_expr(2)} AS condition_code_2,
            {condition_code_expr(3)} AS condition_code_3,
            {condition_code_expr(4)} AS condition_code_4,
            arrayElement(splitByChar(',', conditions), 1) AS condition_raw_1,
            arrayElement(splitByChar(',', conditions), 2) AS condition_raw_2,
            arrayElement(splitByChar(',', conditions), 3) AS condition_raw_3,
            arrayElement(splitByChar(',', conditions), 4) AS condition_raw_4,
            {indicator_code_expr(1)} AS indicator_code_1,
            arrayElement(splitByChar(',', indicators), 1) AS indicator_raw_1
        FROM file({sql_string(quote_path)}, 'CSVWithNames', {sql_string(QUOTE_SCHEMA_STRING)})
        WHERE {quote_clean_predicate()}
    ) AS q
    LEFT JOIN {condition_token_reference_subquery(args, "quote_conditions")} AS qc1 ON qc1.modifier_int = q.condition_code_1
    LEFT JOIN {condition_token_reference_subquery(args, "quote_conditions")} AS qc2 ON qc2.modifier_int = q.condition_code_2
    LEFT JOIN {condition_token_reference_subquery(args, "quote_conditions")} AS qc3 ON qc3.modifier_int = q.condition_code_3
    LEFT JOIN {condition_token_reference_subquery(args, "quote_conditions")} AS qc4 ON qc4.modifier_int = q.condition_code_4
    LEFT JOIN {indicator_token_reference_subquery(args)} AS qi1 ON qi1.modifier_int = q.indicator_code_1

    UNION ALL

    SELECT
        t.ticker AS ticker,
        {trade_event_meta_expr()} AS event_meta,
        t.sip_timestamp_us AS sip_timestamp_us,
        t.sequence_number_u32 AS sequence_number,
        t.price_int AS price_primary_int,
        toUInt32(0) AS price_secondary_int,
        t.size_f32 AS size_primary,
        toFloat32(0) AS size_secondary,
        t.exchange_u8 AS exchange_primary,
        toUInt8(0) AS exchange_secondary,
        {condition_token_expr("tc1")} AS condition_token_1,
        {condition_token_expr("tc2")} AS condition_token_2,
        {condition_token_expr("tc3")} AS condition_token_3,
        {condition_token_expr("tc4")} AS condition_token_4,
        {condition_token_expr("tc5")} AS condition_token_5,
        t.event_date AS event_date
    FROM
    (
        SELECT
            ticker,
            toUInt64(intDiv(toUInt64OrZero(sip_timestamp), 1000)) AS sip_timestamp_us,
            toUInt32OrZero(sequence_number) AS sequence_number_u32,
            toUInt32(if({trade_price_valid}, {trade_price_int}, 0)) AS price_int,
            toFloat32(if(toFloat64OrZero(size) > 0, toFloat64OrZero(size), 0)) AS size_f32,
            toUInt8OrZero(exchange) AS exchange_u8,
            conditions,
            {trade_flags} AS trade_flags,
            {event_date_expr_from_us("intDiv(toUInt64OrZero(sip_timestamp), 1000)")} AS event_date,
            {condition_code_expr(1)} AS condition_code_1,
            {condition_code_expr(2)} AS condition_code_2,
            {condition_code_expr(3)} AS condition_code_3,
            {condition_code_expr(4)} AS condition_code_4,
            {condition_code_expr(5)} AS condition_code_5,
            arrayElement(splitByChar(',', conditions), 1) AS condition_raw_1,
            arrayElement(splitByChar(',', conditions), 2) AS condition_raw_2,
            arrayElement(splitByChar(',', conditions), 3) AS condition_raw_3,
            arrayElement(splitByChar(',', conditions), 4) AS condition_raw_4,
            arrayElement(splitByChar(',', conditions), 5) AS condition_raw_5
        FROM file({sql_string(trade_path)}, 'CSVWithNames', {sql_string(TRADE_SCHEMA_STRING)})
        WHERE {trade_clean_predicate(args)}
    ) AS t
    LEFT JOIN {condition_token_reference_subquery(args, "trade_conditions")} AS tc1 ON tc1.modifier_int = t.condition_code_1
    LEFT JOIN {condition_token_reference_subquery(args, "trade_conditions")} AS tc2 ON tc2.modifier_int = t.condition_code_2
    LEFT JOIN {condition_token_reference_subquery(args, "trade_conditions")} AS tc3 ON tc3.modifier_int = t.condition_code_3
    LEFT JOIN {condition_token_reference_subquery(args, "trade_conditions")} AS tc4 ON tc4.modifier_int = t.condition_code_4
    LEFT JOIN {condition_token_reference_subquery(args, "trade_conditions")} AS tc5 ON tc5.modifier_int = t.condition_code_5
"""


def insert_direct_day_sql(args: argparse.Namespace, day: DayFiles, build_step: int) -> str:
    db = quote_ident(args.database)
    table = quote_ident(args.events_table)
    continuity_table = quote_ident(args.continuity_table)
    return f"""
INSERT INTO {db}.{table}
(
    ticker,
    ordinal,
    event_meta,
    sip_timestamp_us,
    price_primary_int,
    price_secondary_int,
    size_primary,
    size_secondary,
    exchange_primary,
    exchange_secondary,
    condition_token_1,
    condition_token_2,
    condition_token_3,
    condition_token_4,
    condition_token_5,
    event_date
)
SELECT
    e.ticker,
    coalesce(c.ordinal_offset, toUInt64(0))
        + toUInt64(row_number() OVER (PARTITION BY e.ticker ORDER BY e.sip_timestamp_us, e.sequence_number, bitAnd(e.event_meta, 1)) - 1) AS ordinal,
    e.event_meta,
    e.sip_timestamp_us,
    e.price_primary_int,
    e.price_secondary_int,
    e.size_primary,
    e.size_secondary,
    e.exchange_primary,
    e.exchange_secondary,
    e.condition_token_1,
    e.condition_token_2,
    e.condition_token_3,
    e.condition_token_4,
    e.condition_token_5,
    e.event_date
FROM
(
{raw_event_union_sql(args, day)}
) AS e
LEFT JOIN
(
    SELECT
        ticker,
        argMax(next_ordinal, tuple(build_step, updated_at)) AS ordinal_offset
    FROM {db}.{continuity_table}
    WHERE build_step < toUInt32({int(build_step)})
    GROUP BY ticker
) AS c ON c.ticker = e.ticker
ORDER BY e.ticker, ordinal
{query_settings(args)}
"""


def insert_direct_day_continuity_sql(args: argparse.Namespace, day: DayFiles, build_step: int) -> str:
    db = quote_ident(args.database)
    continuity_table = quote_ident(args.continuity_table)
    return f"""
INSERT INTO {db}.{continuity_table}
(
    ticker,
    build_step,
    source_date,
    event_count,
    next_ordinal,
    last_ordinal,
    first_sip_timestamp_us,
    last_sip_timestamp_us
)
SELECT
    e.ticker,
    toUInt32({int(build_step)}) AS build_step,
    toDate({sql_string(day.source_date)}) AS source_date,
    count() AS event_count,
    coalesce(c.ordinal_offset, toUInt64(0)) + count() AS next_ordinal,
    coalesce(c.ordinal_offset, toUInt64(0)) + count() - 1 AS last_ordinal,
    min(e.sip_timestamp_us) AS first_sip_timestamp_us,
    max(e.sip_timestamp_us) AS last_sip_timestamp_us
FROM
(
{raw_event_union_sql(args, day)}
) AS e
LEFT JOIN
(
    SELECT
        ticker,
        argMax(next_ordinal, tuple(build_step, updated_at)) AS ordinal_offset
    FROM {db}.{continuity_table}
    WHERE build_step < toUInt32({int(build_step)})
    GROUP BY ticker
) AS c ON c.ticker = e.ticker
GROUP BY e.ticker, c.ordinal_offset
{query_settings(args)}
"""


def delete_day_continuity_sql(args: argparse.Namespace, day: DayFiles) -> str:
    return f"""
ALTER TABLE {quote_ident(args.database)}.{quote_ident(args.continuity_table)}
DELETE WHERE source_date = toDate({sql_string(day.source_date)})
{mutation_settings(args)}
"""


def create_ticker_day_index_table_sql(args: argparse.Namespace) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(args.database)}.{quote_ident(args.ticker_day_index_table)}
(
    ticker LowCardinality(String),
    source_date Date,
    event_count UInt64,
    first_ordinal UInt64,
    last_ordinal UInt64,
    next_ordinal UInt64,
    first_sip_timestamp_us UInt64,
    last_sip_timestamp_us UInt64,
    build_step UInt32,
    built_at DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(built_at)
PARTITION BY toYYYYMM(source_date)
ORDER BY (source_date, ticker)
{mergetree_settings(args.storage_policy)}
"""


def validate_ticker_day_index_table_schema(client: ClickHouseHttpClient, args: argparse.Namespace) -> None:
    expected = {
        "ticker": "LowCardinality(String)",
        "source_date": "Date",
        "event_count": "UInt64",
        "first_ordinal": "UInt64",
        "last_ordinal": "UInt64",
        "next_ordinal": "UInt64",
        "first_sip_timestamp_us": "UInt64",
        "last_sip_timestamp_us": "UInt64",
        "build_step": "UInt32",
        "built_at": "DateTime",
    }
    rows = client.query_tsv(
        f"""
SELECT name, type
FROM system.columns
WHERE database = {sql_string(args.database)}
  AND table = {sql_string(args.ticker_day_index_table)}
FORMAT TSV
"""
    )
    actual: dict[str, str] = {}
    for line in rows.splitlines():
        if not line.strip():
            continue
        name, col_type = line.split("\t", 1)
        actual[name] = col_type
    missing = [name for name in expected if name not in actual]
    wrong_type = [
        f"{name}: expected {expected_type}, got {actual[name]}"
        for name, expected_type in expected.items()
        if name in actual and actual[name] != expected_type
    ]
    if missing or wrong_type:
        details = []
        if missing:
            details.append(f"missing={missing}")
        if wrong_type:
            details.append(f"wrong_type={wrong_type}")
        raise RuntimeError(
            f"{args.database}.{args.ticker_day_index_table} is not the expected event ticker/day index schema; "
            + "; ".join(details)
            + ". Drop the stale index table or pass --ticker-day-index-table to use a fresh table."
        )


def delete_day_ticker_index_sql(args: argparse.Namespace, day: DayFiles) -> str:
    return f"""
ALTER TABLE {quote_ident(args.database)}.{quote_ident(args.ticker_day_index_table)}
DELETE WHERE source_date = toDate({sql_string(day.source_date)})
{mutation_settings(args)}
"""


def insert_day_ticker_index_sql(args: argparse.Namespace, day: DayFiles, build_step: int) -> str:
    return f"""
INSERT INTO {quote_ident(args.database)}.{quote_ident(args.ticker_day_index_table)}
(
    ticker,
    source_date,
    event_count,
    first_ordinal,
    last_ordinal,
    next_ordinal,
    first_sip_timestamp_us,
    last_sip_timestamp_us,
    build_step
)
SELECT
    ticker,
    source_date,
    event_count,
    last_ordinal - event_count + 1 AS first_ordinal,
    last_ordinal,
    next_ordinal,
    first_sip_timestamp_us,
    last_sip_timestamp_us,
    build_step
FROM
(
    SELECT
        ticker,
        toUInt32({int(build_step)}) AS build_step,
        toDate({sql_string(day.source_date)}) AS source_date,
        argMax(event_count, updated_at) AS event_count,
        argMax(next_ordinal, updated_at) AS next_ordinal,
        argMax(last_ordinal, updated_at) AS last_ordinal,
        argMax(first_sip_timestamp_us, updated_at) AS first_sip_timestamp_us,
        argMax(last_sip_timestamp_us, updated_at) AS last_sip_timestamp_us
    FROM {quote_ident(args.database)}.{quote_ident(args.continuity_table)}
    WHERE build_step = toUInt32({int(build_step)})
      AND source_date = toDate({sql_string(day.source_date)})
    GROUP BY ticker
)
WHERE event_count > 0
{query_settings(args)}
"""


def build_step_for_date(value: str) -> int:
    return date.fromisoformat(value).toordinal()


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, default=str) + "\n")


def ensure_tables(client: ClickHouseHttpClient, args: argparse.Namespace) -> None:
    client.execute(f"CREATE DATABASE IF NOT EXISTS {quote_ident(args.database)}")
    client.execute(create_events_table_sql(args))
    validate_events_table_schema(client, args)
    client.execute(create_manifest_table_sql(args))
    client.execute(create_continuity_table_sql(args))
    ensure_continuity_table_columns(client, args)
    client.execute(create_ticker_day_index_table_sql(args))
    validate_ticker_day_index_table_schema(client, args)


def configure_test_tables(args: argparse.Namespace, run_id: str) -> None:
    prefix = str(args.test_table_prefix).strip()
    if not SAFE_TEST_TABLE_RE.match(prefix):
        raise ValueError(f"--test-table-prefix must be a safe ClickHouse identifier prefix, got {prefix!r}")
    if prefix in {
        DEFAULT_EVENTS_TABLE,
        DEFAULT_MANIFEST_TABLE,
        DEFAULT_CONTINUITY_TABLE,
        DEFAULT_TICKER_DAY_INDEX_TABLE,
        DEFAULT_MACRO_BARS_TABLE,
        DEFAULT_BARS_TABLE,
        DEFAULT_BARS_BY_SYMBOL_TIME_TABLE,
        DEFAULT_BARS_BY_TIME_SYMBOL_TABLE,
    }:
        raise ValueError("--test-table-prefix cannot be a production table name")
    if args.dry_run:
        raise ValueError("--test-mode needs to insert and audit temp tables; do not combine it with --dry-run")
    if int(args.limit_days) <= 0:
        args.limit_days = 1
    args.events_table = f"{prefix}_{run_id}_events"
    args.manifest_table = f"{prefix}_{run_id}_manifest"
    args.continuity_table = f"{prefix}_{run_id}_continuity"
    args.ticker_day_index_table = f"{prefix}_{run_id}_ticker_day_index"
    args.macro_bars_table = f"{prefix}_{run_id}_macro_bars_by_time_symbol"
    args.bars_table = f"{prefix}_{run_id}_bars"
    args.bars_by_symbol_time_table = f"{prefix}_{run_id}_bars_by_symbol_time"
    args.bars_by_time_symbol_table = f"{prefix}_{run_id}_bars_by_time_symbol"
    for table in (
        args.events_table,
        args.manifest_table,
        args.continuity_table,
        args.ticker_day_index_table,
        args.macro_bars_table,
        args.bars_table,
        args.bars_by_symbol_time_table,
        args.bars_by_time_symbol_table,
    ):
        if table in {
            DEFAULT_EVENTS_TABLE,
            DEFAULT_MANIFEST_TABLE,
            DEFAULT_CONTINUITY_TABLE,
            DEFAULT_TICKER_DAY_INDEX_TABLE,
            DEFAULT_MACRO_BARS_TABLE,
            DEFAULT_BARS_TABLE,
            DEFAULT_BARS_BY_SYMBOL_TIME_TABLE,
            DEFAULT_BARS_BY_TIME_SYMBOL_TABLE,
        } or not SAFE_TEST_TABLE_RE.match(table):
            raise ValueError(f"Unsafe test table name generated: {table!r}")
    args.retry_failed = True
    args.retry_started = True
    args.force_day_delete = True


def drop_test_tables(client: ClickHouseHttpClient, args: argparse.Namespace) -> None:
    if not args.test_mode:
        return
    prefix = f"{str(args.test_table_prefix).strip()}_"
    tables = [
        args.events_table,
        args.manifest_table,
        args.continuity_table,
        args.ticker_day_index_table,
        args.macro_bars_table,
        args.bars_table,
        args.bars_by_symbol_time_table,
        args.bars_by_time_symbol_table,
    ]
    unsafe = [table for table in tables if not table.startswith(prefix) or not SAFE_TEST_TABLE_RE.match(table)]
    if unsafe:
        raise RuntimeError(f"Refusing to drop unsafe test table names: {unsafe}")
    for table in tables:
        client.execute(f"DROP TABLE IF EXISTS {quote_ident(args.database)}.{quote_ident(table)} SYNC")


def first_tsv_row(client: ClickHouseHttpClient, sql: str) -> list[str]:
    text = client.query_tsv(sql).strip()
    return text.splitlines()[0].split("\t") if text else []


def allowed_utc_event_dates(days: list[DayFiles]) -> list[str]:
    values: set[str] = set()
    for day in days:
        source = date.fromisoformat(day.source_date)
        values.add(source.isoformat())
        values.add(date.fromordinal(source.toordinal() + 1).isoformat())
    return sorted(values)


def query_audit_counts(client: ClickHouseHttpClient, args: argparse.Namespace, days: list[DayFiles]) -> dict[str, int]:
    allowed_dates = ", ".join(sql_string(value) for value in allowed_utc_event_dates(days))
    row = first_tsv_row(
        client,
        f"""
SELECT
    count() AS rows,
    countIf(ticker = '') AS blank_ticker_rows,
    countIf(bitAnd(event_meta, 1) NOT IN (0, 1)) AS bad_event_type_rows,
    countIf(sip_timestamp_us = 0) AS zero_timestamp_rows,
    countIf(event_date NOT IN ({allowed_dates})) AS wrong_event_date_rows,
    countIf(bitAnd(event_meta, 1) = 0 AND ((price_primary_int = 0) != (price_secondary_int = 0) OR size_primary < 0 OR size_secondary < 0)) AS bad_quote_rows,
    countIf(bitAnd(event_meta, 1) = 1 AND (price_secondary_int != 0 OR size_secondary != 0 OR exchange_secondary != 0 OR size_primary < 0)) AS bad_trade_rows,
    count() - uniqExact(ticker, ordinal) AS duplicate_ticker_ordinal_rows
FROM {quote_ident(args.database)}.{quote_ident(args.events_table)}
""",
    )
    keys = [
        "rows",
        "blank_ticker_rows",
        "bad_event_type_rows",
        "zero_timestamp_rows",
        "wrong_event_date_rows",
        "bad_quote_rows",
        "bad_trade_rows",
        "duplicate_ticker_ordinal_rows",
    ]
    return {key: int(float(value or 0)) for key, value in zip(keys, row, strict=False)}


def query_continuity_mismatches(client: ClickHouseHttpClient, args: argparse.Namespace, days: list[DayFiles]) -> int:
    dates = ", ".join(sql_string(day.source_date) for day in days)
    raw_count_queries = "\nUNION ALL\n".join(
        f"""
        SELECT ticker, toDate({sql_string(day.source_date)}) AS source_date, count() AS raw_rows
        FROM
        (
        {raw_event_union_sql(args, day)}
        )
        GROUP BY ticker
        """
        for day in days
    )
    row = first_tsv_row(
        client,
        f"""
SELECT count()
FROM
(
    SELECT
        coalesce(r.ticker, c.ticker) AS ticker,
        coalesce(r.source_date, c.source_date) AS source_date,
        coalesce(r.raw_rows, toUInt64(0)) AS raw_rows,
        coalesce(c.continuity_rows, toUInt64(0)) AS continuity_rows
    FROM
    (
        {raw_count_queries}
    ) AS r
    FULL OUTER JOIN
    (
        SELECT ticker, source_date, argMax(event_count, updated_at) AS continuity_rows
        FROM {quote_ident(args.database)}.{quote_ident(args.continuity_table)}
        WHERE source_date IN ({dates})
        GROUP BY ticker, source_date
    ) AS c ON c.ticker = r.ticker AND c.source_date = r.source_date
)
WHERE raw_rows != continuity_rows
""",
    )
    return int(float(row[0] or 0)) if row else 0


def query_ticker_day_index_mismatches(client: ClickHouseHttpClient, args: argparse.Namespace, days: list[DayFiles]) -> int:
    dates = ", ".join(sql_string(day.source_date) for day in days)
    row = first_tsv_row(
        client,
        f"""
SELECT count()
FROM
(
    SELECT
        coalesce(c.ticker, i.ticker) AS ticker,
        coalesce(c.source_date, i.source_date) AS source_date,
        c.event_count AS continuity_event_count,
        i.event_count AS index_event_count,
        c.first_ordinal AS continuity_first_ordinal,
        i.first_ordinal AS index_first_ordinal,
        c.last_ordinal AS continuity_last_ordinal,
        i.last_ordinal AS index_last_ordinal,
        c.next_ordinal AS continuity_next_ordinal,
        i.next_ordinal AS index_next_ordinal
    FROM
    (
        SELECT
            ticker,
            source_date,
            event_count,
            last_ordinal - event_count + 1 AS first_ordinal,
            last_ordinal,
            next_ordinal
        FROM
        (
            SELECT
                ticker,
                build_step,
                source_date,
                argMax(event_count, updated_at) AS event_count,
                argMax(last_ordinal, updated_at) AS last_ordinal,
                argMax(next_ordinal, updated_at) AS next_ordinal
            FROM {quote_ident(args.database)}.{quote_ident(args.continuity_table)}
            WHERE source_date IN ({dates})
            GROUP BY ticker, build_step, source_date
        )
        WHERE event_count > 0
    ) AS c
    FULL OUTER JOIN
    (
        SELECT
            ticker,
            source_date,
            argMax(event_count, built_at) AS event_count,
            argMax(first_ordinal, built_at) AS first_ordinal,
            argMax(last_ordinal, built_at) AS last_ordinal,
            argMax(next_ordinal, built_at) AS next_ordinal
        FROM {quote_ident(args.database)}.{quote_ident(args.ticker_day_index_table)}
        WHERE source_date IN ({dates})
        GROUP BY ticker, source_date
    ) AS i ON i.ticker = c.ticker AND i.source_date = c.source_date
)
WHERE continuity_event_count != index_event_count
   OR continuity_first_ordinal != index_first_ordinal
   OR continuity_last_ordinal != index_last_ordinal
   OR continuity_next_ordinal != index_next_ordinal
   OR isNull(continuity_event_count)
   OR isNull(index_event_count)
""",
    )
    return int(float(row[0] or 0)) if row else 0


def query_sample_events(client: ClickHouseHttpClient, args: argparse.Namespace, days: list[DayFiles], event_type: int) -> list[dict[str, Any]]:
    sql = f"""
SELECT
    ticker,
    ordinal,
    bitAnd(event_meta, 1) AS event_type,
    event_meta,
    sip_timestamp_us,
    price_primary_int,
    price_secondary_int,
    size_primary,
    size_secondary,
    exchange_primary,
    exchange_secondary,
    condition_token_1,
    condition_token_2,
    condition_token_3,
    condition_token_4,
    condition_token_5,
    event_date
FROM {quote_ident(args.database)}.{quote_ident(args.events_table)}
WHERE bitAnd(event_meta, 1) = toUInt8({int(event_type)})
ORDER BY cityHash64(ticker, ordinal, sip_timestamp_us, bitAnd(event_meta, 1))
LIMIT {max(1, int(args.test_sample_size))}
FORMAT JSONEachRow
"""
    rows = []
    for line in client.execute(sql).splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def load_condition_token_maps(client: ClickHouseHttpClient, args: argparse.Namespace) -> dict[str, dict[int, int]]:
    rows = client.query_tsv(
        f"""
SELECT source_family, modifier_int, min(token_id)
FROM {quote_ident(args.database)}.{quote_ident(args.condition_token_reference_table)}
WHERE is_join_canonical = 1
GROUP BY source_family, modifier_int
"""
    ).strip().splitlines()
    maps: dict[str, dict[int, int]] = defaultdict(dict)
    for line in rows:
        if not line.strip():
            continue
        family, modifier, token_id = line.split("\t")
        maps[family][int(modifier or 0)] = int(token_id or 0)
    indicator: dict[int, int] = {}
    for family, values in maps.items():
        if family not in {"unknown", "quote_conditions", "trade_conditions", "trade_corrections_nyse"}:
            for modifier, token_id in values.items():
                indicator.setdefault(modifier, token_id)
    maps["quote_indicators"] = indicator
    return maps


def collect_lazy(frame: Any):
    try:
        return frame.collect(engine="streaming")
    except (TypeError, ValueError):
        return frame.collect()


def float32_value(value: Any) -> float:
    return struct.unpack("f", struct.pack("f", float(to_decimal_or_zero(value))))[0]


def condition_codes(raw_conditions: Any, slots: int) -> list[int]:
    parts = str(raw_conditions or "").split(",")
    values: list[int] = []
    for index in range(slots):
        values.append(to_int_or_zero(parts[index] if index < len(parts) else ""))
    return values


def nonempty_codes(raw_value: Any) -> list[int]:
    return [to_int_or_zero(part) for part in str(raw_value or "").split(",") if str(part or "") != ""]


def event_meta(event_type: int, primary_price_scale: int, secondary_price_scale: int, tape: int) -> int:
    return (
        (int(event_type) & 1)
        | ((int(primary_price_scale) & 1) << 1)
        | ((int(secondary_price_scale) & 1) << 2)
        | ((int(tape) & 0x07) << 3)
    )


def token_columns(token_ids: list[int]) -> dict[str, int]:
    return {
        f"condition_token_{slot + 1}": int(token_ids[slot]) & 0xFF if slot < len(token_ids) else 0
        for slot in range(CONDITION_TOKEN_SLOTS)
    }


def quote_condition_tokens(row: dict[str, Any], token_maps: dict[str, dict[int, int]]) -> dict[str, int]:
    condition_codes_ = nonempty_codes(row.get("conditions"))
    indicator_codes = nonempty_codes(row.get("indicators"))
    token_ids: list[int] = []
    for code in condition_codes_[:4]:
        token_id = token_maps["quote_conditions"].get(code, 0)
        token_ids.append(token_id)
    for code in indicator_codes[: max(0, CONDITION_TOKEN_SLOTS - len(token_ids))]:
        token_id = token_maps["quote_indicators"].get(code, 0)
        token_ids.append(token_id)
    return token_columns(token_ids)


def trade_condition_tokens(row: dict[str, Any], token_maps: dict[str, dict[int, int]]) -> dict[str, int]:
    condition_codes_ = nonempty_codes(row.get("conditions"))
    token_ids: list[int] = []
    for code in condition_codes_[:CONDITION_TOKEN_SLOTS]:
        token_id = token_maps["trade_conditions"].get(code, 0)
        token_ids.append(token_id)
    return token_columns(token_ids)


def event_key(row: dict[str, Any]) -> tuple[str, int, int, int]:
    return (str(row["ticker"]), int(row["event_type"]), int(row["sip_timestamp_us"]), int(row["ordinal"]))


def raw_lookup_key(row: dict[str, Any], event_type: int) -> tuple[str, int, int, int]:
    return (str(row["ticker"]), int(event_type), int(row["sip_timestamp_us"]), int(row["sequence_number"]))


def sampled_raw_lookup_keys(events: list[dict[str, Any]]) -> set[tuple[str, int, int]]:
    return {(str(row["ticker"]), int(row["sip_timestamp_us"]), int(row["event_type"])) for row in events}


def event_values_match(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    int_fields = [
        "event_type",
        "event_meta",
        "sip_timestamp_us",
        "price_primary_int",
        "price_secondary_int",
        "exchange_primary",
        "exchange_secondary",
        "condition_token_1",
        "condition_token_2",
        "condition_token_3",
        "condition_token_4",
        "condition_token_5",
    ]
    for field in int_fields:
        if int(expected[field]) != int(actual[field]):
            return False
    for field in ("size_primary", "size_secondary"):
        if abs(float(expected[field]) - float(actual[field])) > 1e-4:
            return False
    return str(expected["ticker"]) == str(actual["ticker"]) and str(expected["event_date"]) == str(actual["event_date"])


def quote_raw_row_to_event(row: dict[str, Any], token_maps: dict[str, dict[int, int]]) -> dict[str, Any] | None:
    bid = to_decimal_or_zero(row.get("bid_price"))
    ask = to_decimal_or_zero(row.get("ask_price"))
    bid_int = price_int(bid)
    ask_int = price_int(ask)
    bid_size = to_decimal_or_zero(row.get("bid_size"))
    ask_size = to_decimal_or_zero(row.get("ask_size"))
    bid_size_int = int(float(bid_size)) if bid_size > 0 else 0
    ask_size_int = int(float(ask_size)) if ask_size > 0 else 0
    if not row.get("ticker") or to_int_or_zero(row.get("sip_timestamp")) <= 0 or to_int_or_zero(row.get("sequence_number")) <= 0:
        return None
    bid_scale = scale_code(bid)
    ask_scale = scale_code(ask)
    bid_price = bid_int / (10000.0 if bid_scale else 100.0)
    ask_price = ask_int / (10000.0 if ask_scale else 100.0)
    quote_price_valid = (
        bid > 0
        and ask > 0
        and bid_int > 0
        and ask_int > 0
        and not price_precision_clipped(bid)
        and not price_precision_clipped(ask)
        and bid_price <= ask_price
    )
    if not quote_price_valid:
        bid_int = 0
        ask_int = 0
        bid_scale = 0
        ask_scale = 0
    return {
        "ticker": str(row["ticker"]),
        "event_type": 0,
        "event_meta": event_meta(0, ask_scale, bid_scale, tape_code(row.get("tape"))),
        "sip_timestamp_us": to_int_or_zero(row.get("sip_timestamp")) // 1000,
        "sequence_number": to_int_or_zero(row.get("sequence_number")),
        "price_primary_int": ask_int,
        "price_secondary_int": bid_int,
        "size_primary": float32_value(ask_size_int),
        "size_secondary": float32_value(bid_size_int),
        "exchange_primary": to_int_or_zero(row.get("ask_exchange")),
        "exchange_secondary": to_int_or_zero(row.get("bid_exchange")),
        **quote_condition_tokens(row, token_maps),
        "event_date": datetime.fromtimestamp((to_int_or_zero(row.get("sip_timestamp")) // 1000) / 1_000_000, tz=timezone.utc).date().isoformat(),
    }


def trade_raw_row_to_event(row: dict[str, Any], token_maps: dict[str, dict[int, int]]) -> dict[str, Any] | None:
    trade_price = to_decimal_or_zero(row.get("price"))
    trade_int = price_int(trade_price)
    size = to_decimal_or_zero(row.get("size"))
    if not row.get("ticker") or to_int_or_zero(row.get("sip_timestamp")) <= 0 or to_int_or_zero(row.get("sequence_number")) <= 0:
        return None
    trade_scale = scale_code(trade_price)
    if trade_price <= 0 or trade_int <= 0 or price_precision_clipped(trade_price):
        trade_int = 0
        trade_scale = 0
    size = size if size > 0 else 0
    return {
        "ticker": str(row["ticker"]),
        "event_type": 1,
        "event_meta": event_meta(1, trade_scale, 0, tape_code(row.get("tape"))),
        "sip_timestamp_us": to_int_or_zero(row.get("sip_timestamp")) // 1000,
        "sequence_number": to_int_or_zero(row.get("sequence_number")),
        "price_primary_int": trade_int,
        "price_secondary_int": 0,
        "size_primary": float32_value(size),
        "size_secondary": 0.0,
        "exchange_primary": to_int_or_zero(row.get("exchange")),
        "exchange_secondary": 0,
        **trade_condition_tokens(row, token_maps),
        "event_date": datetime.fromtimestamp((to_int_or_zero(row.get("sip_timestamp")) // 1000) / 1_000_000, tz=timezone.utc).date().isoformat(),
    }


def read_raw_event_candidates(
    path: Path,
    kind: str,
    sampled_events: list[dict[str, Any]],
    token_maps: dict[str, dict[int, int]],
) -> list[dict[str, Any]]:
    if not sampled_events:
        return []
    try:
        import polars as pl
    except ImportError as exc:
        raise RuntimeError("polars is required for raw CSV test-mode validation") from exc
    tickers = sorted({str(row["ticker"]) for row in sampled_events})
    sip_us_values = sorted({int(row["sip_timestamp_us"]) for row in sampled_events})
    event_type = 0 if kind == "quotes" else 1
    if kind == "quotes":
        columns = [
            "ticker",
            "ask_exchange",
            "ask_price",
            "ask_size",
            "bid_exchange",
            "bid_price",
            "bid_size",
            "conditions",
            "indicators",
            "participant_timestamp",
            "sequence_number",
            "sip_timestamp",
            "tape",
        ]
    else:
        columns = [
            "ticker",
            "conditions",
            "correction",
            "exchange",
            "participant_timestamp",
            "price",
            "sequence_number",
            "sip_timestamp",
            "size",
            "tape",
        ]
    scan = pl.scan_csv(str(path), schema_overrides={column: pl.Utf8 for column in columns}, infer_schema_length=0).select(columns)
    sip_us_expr = (pl.col("sip_timestamp").cast(pl.UInt64, strict=False) // 1000).alias("sip_timestamp_us")
    filtered = (
        scan.with_columns(sip_us_expr)
        .filter(pl.col("ticker").is_in(tickers))
        .filter(pl.col("sip_timestamp_us").is_in(sip_us_values))
    )
    rows = collect_lazy(filtered).to_dicts()
    converted: list[dict[str, Any]] = []
    converter = quote_raw_row_to_event if kind == "quotes" else trade_raw_row_to_event
    for row in rows:
        event = converter(row, token_maps)
        if event is not None:
            converted.append(event)
    sampled_keys = sampled_raw_lookup_keys(sampled_events)
    return [row for row in converted if (row["ticker"], row["sip_timestamp_us"], event_type) in sampled_keys]


def validate_events_against_raw_csv(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    days: list[DayFiles],
) -> dict[str, dict[str, Any]]:
    token_maps = load_condition_token_maps(client, args)
    sample_by_kind = {
        "quotes": query_sample_events(client, args, days, 0),
        "trades": query_sample_events(client, args, days, 1),
    }
    days_by_date = {day.source_date: day for day in days}
    result: dict[str, dict[str, Any]] = {}
    for kind, sampled_events in sample_by_kind.items():
        expected_by_key: dict[tuple[str, int, int], list[dict[str, Any]]] = defaultdict(list)
        for source_date in sorted(days_by_date):
            day = days_by_date[source_date]
            path = Path(day.quote_job.destination if kind == "quotes" else day.trade_job.destination)
            for event in read_raw_event_candidates(path, kind, sampled_events, token_maps):
                expected_by_key[(event["ticker"], event["sip_timestamp_us"], event["sequence_number"])].append(event)
        mismatches: list[dict[str, Any]] = []
        matched = 0
        for event in sampled_events:
            key = (str(event["ticker"]), int(event["sip_timestamp_us"]), None)
            candidates = [
                candidate
                for candidate_key, candidate_rows in expected_by_key.items()
                if candidate_key[0] == key[0] and candidate_key[1] == key[1]
                for candidate in candidate_rows
            ]
            if not any(event_values_match(candidate, event) for candidate in candidates):
                mismatches.append(
                    {
                        "ticker": event["ticker"],
                        "event_type": event["event_type"],
                        "sip_timestamp_us": event["sip_timestamp_us"],
                        "ordinal": event["ordinal"],
                        "candidate_count": len(candidates),
                    }
                )
            else:
                matched += 1
        result[kind] = {
            "sample_rows": len(sampled_events),
            "matched_rows": matched,
            "mismatch_rows": len(mismatches),
            "mismatches_preview": mismatches[:10],
        }
    return result


def audit_test_events(client: ClickHouseHttpClient, args: argparse.Namespace, days: list[DayFiles], report_path: Path) -> None:
    print("=" * 100, flush=True)
    print("TEST AUDIT START", flush=True)
    if not days:
        raise RuntimeError("Test-mode audit has no completed source days to validate.")
    counts = query_audit_counts(client, args, days)
    continuity_mismatches = query_continuity_mismatches(client, args, days)
    ticker_day_index_mismatches = query_ticker_day_index_mismatches(client, args, days)
    raw_csv_validation = validate_events_against_raw_csv(client, args, days)
    audit = {
        "type": "test_audit",
        "status": "ok",
        "events_table": args.events_table,
        "manifest_table": args.manifest_table,
        "continuity_table": args.continuity_table,
        "ticker_day_index_table": args.ticker_day_index_table,
        "source_dates": [day.source_date for day in days],
        "counts": counts,
        "continuity_mismatches": continuity_mismatches,
        "ticker_day_index_mismatches": ticker_day_index_mismatches,
        "raw_csv_validation": raw_csv_validation,
    }
    failures = {
        key: value
        for key, value in counts.items()
        if key != "rows" and value
    }
    if counts.get("rows", 0) <= 0:
        failures["rows"] = counts.get("rows", 0)
    if continuity_mismatches:
        failures["continuity_mismatches"] = continuity_mismatches
    if ticker_day_index_mismatches:
        failures["ticker_day_index_mismatches"] = ticker_day_index_mismatches
    for kind, validation in raw_csv_validation.items():
        if validation["sample_rows"] <= 0 or validation["mismatch_rows"]:
            failures[f"{kind}_raw_csv_match"] = validation
    if failures:
        audit["status"] = "failed"
        audit["failures"] = failures
        append_jsonl(report_path, audit)
        print(f"TEST AUDIT FAILED failures={json.dumps(failures, sort_keys=True)}", flush=True)
        raise RuntimeError(f"Test-mode audit failed; temp events were not promoted. Failures: {failures}")
    append_jsonl(report_path, audit)
    print(
        "TEST AUDIT OK "
        f"rows={counts['rows']:,} quote_samples={raw_csv_validation['quotes']['sample_rows']:,} "
        f"trade_samples={raw_csv_validation['trades']['sample_rows']:,}",
        flush=True,
    )
    print("=" * 100, flush=True)


def rebuild_day_ticker_index(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    day: DayFiles,
    build_step: int,
    report_path: Path,
    *,
    reason: str,
) -> QueryProfile:
    run_profiled(client, f"delete_ticker_day_index_{day.source_date}", delete_day_ticker_index_sql(args, day))
    profile = run_profiled(
        client,
        f"insert_ticker_day_index_{day.source_date}",
        insert_day_ticker_index_sql(args, day, build_step),
    )
    mismatches = query_ticker_day_index_mismatches(client, args, [day])
    check = {
        "type": "ticker_day_index_check",
        "source_date": day.source_date,
        "build_step": build_step,
        "reason": reason,
        "mismatches": mismatches,
        "status": "ok" if mismatches == 0 else "failed",
    }
    append_jsonl(report_path, check)
    if mismatches:
        try:
            cleanup_profile = run_profiled(
                client,
                f"cleanup_bad_ticker_day_index_{day.source_date}",
                delete_day_ticker_index_sql(args, day),
            )
            append_jsonl(
                report_path,
                {
                    "type": "ticker_day_index_cleanup",
                    "source_date": day.source_date,
                    "build_step": build_step,
                    "reason": "validation_failed",
                    "profile": asdict(cleanup_profile),
                    "status": "ok",
                },
            )
        except Exception as cleanup_exc:
            append_jsonl(
                report_path,
                {
                    "type": "ticker_day_index_cleanup",
                    "source_date": day.source_date,
                    "build_step": build_step,
                    "reason": "validation_failed",
                    "status": "failed",
                    "error": repr(cleanup_exc),
                },
            )
        raise RuntimeError(
            f"day={day.source_date} rebuilt ticker/day index has {mismatches:,} mismatched ticker rows "
            f"against {args.database}.{args.continuity_table}; refusing to mark the day usable."
        )
    append_jsonl(
        report_path,
        {
            "type": "ticker_day_index",
            "source_date": day.source_date,
            "reason": reason,
            "profile": asdict(profile),
        },
    )
    return profile


def day_continuity_summary(client: ClickHouseHttpClient, args: argparse.Namespace, day: DayFiles, build_step: int) -> tuple[int, int]:
    row = first_tsv_row(
        client,
        f"""
SELECT count(), coalesce(sum(event_count), toUInt64(0))
FROM {quote_ident(args.database)}.{quote_ident(args.continuity_table)}
WHERE build_step = toUInt32({int(build_step)})
  AND source_date = toDate({sql_string(day.source_date)})
""",
    )
    if not row:
        return 0, 0
    return int(float(row[0] or 0)), int(float(row[1] or 0))


def validate_day_continuity_after_insert(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    day: DayFiles,
    build_step: int,
    expected_event_rows: int | None,
    report_path: Path,
) -> None:
    ticker_rows, continuity_events = day_continuity_summary(client, args, day, build_step)
    audit = {
        "type": "day_continuity_check",
        "source_date": day.source_date,
        "build_step": build_step,
        "ticker_rows": ticker_rows,
        "continuity_events": continuity_events,
        "expected_event_rows": expected_event_rows,
        "status": "ok",
    }
    if expected_event_rows is not None and continuity_events != expected_event_rows:
        audit["status"] = "failed"
        append_jsonl(report_path, audit)
        raise RuntimeError(
            f"day={day.source_date} continuity event_count sum {continuity_events:,} "
            f"does not match inserted event rows {expected_event_rows:,}; refusing to build ticker/day index."
        )
    append_jsonl(report_path, audit)


def _run_profiled_with_reporter(
    client: ClickHouseHttpClient,
    label: str,
    sql: str,
    reporter: UpdateProgressReporter | None,
    *,
    day: str,
    stage: str,
    detail: str = "",
) -> QueryProfile:
    key = f"{day}:{stage}:{label}"
    if reporter is not None:
        reporter.task_start(key, label, day=day, stage=stage)
    try:
        profile = run_profiled(client, label, sql)
    except Exception as exc:
        if reporter is not None:
            reporter.task_done(key, "failed", detail=repr(exc))
        raise
    if reporter is not None:
        reporter.task_done(key, "ok", seconds=float(profile.wall_seconds), rows=profile.written_rows, detail=detail or f"query_id={profile.query_id}")
    return profile


def run_day(client: ClickHouseHttpClient, args: argparse.Namespace, day: DayFiles, run_id: str, report_path: Path, reporter: UpdateProgressReporter | None = None) -> str:
    job = DayJob(source_date=day.source_date, build_step=build_step_for_date(day.source_date))
    status = latest_day_status(client, args, job)
    if status == "ok":
        if reporter is not None:
            reporter.task_start(f"{day.source_date}:index:manifest_ok", "rebuild ticker/day index", day=day.source_date, stage="index")
        rebuild_day_ticker_index(client, args, day, job.build_step, report_path, reason="manifest_ok")
        if reporter is not None:
            reporter.task_done(f"{day.source_date}:index:manifest_ok", "ok", detail="manifest was already ok")
        print(f"DAY SKIP {day.source_date} status=ok", flush=True)
        return "skipped"
    if status in {"failed", "started", "interrupted"} and not (args.retry_failed or args.retry_started):
        print(f"DAY SKIP {day.source_date} status={status}; pass retry flags to revisit", flush=True)
        return "incomplete_skipped"
    if status in {"failed", "started", "interrupted"}:
        if not args.force_day_delete:
            raise RuntimeError(f"day={day.source_date} status={status}; rerun with --force-day-delete to avoid duplicate rows")
        if reporter is not None:
            reporter.notice(f"Deleting existing event rows for {day.source_date} because status={status} and --force-day-delete is set.", style="bold yellow")
        _run_profiled_with_reporter(client, f"delete_events_day_{day.source_date}", delete_day_sql(args, job), reporter, day=day.source_date, stage="delete", detail="event rows")

    # Continuity and ticker/day index are derived per-day metadata. Always purge
    # them before a fresh day insert so absent tickers from stale attempts cannot
    # survive and be copied into the loader-facing index.
    if reporter is not None:
        reporter.notice(f"Refreshing derived continuity and ticker/day index rows for {day.source_date}.", style="yellow")
    _run_profiled_with_reporter(client, f"delete_continuity_day_{day.source_date}", delete_day_continuity_sql(args, day), reporter, day=day.source_date, stage="delete", detail="continuity rows")
    _run_profiled_with_reporter(client, f"delete_ticker_day_index_{day.source_date}", delete_day_ticker_index_sql(args, day), reporter, day=day.source_date, stage="delete", detail="ticker/day index rows")

    insert_day_manifest(client, args, job, status="started", run_id=run_id)
    try:
        profile = _run_profiled_with_reporter(client, f"insert_events_from_flatfiles_{day.source_date}", insert_direct_day_sql(args, day, job.build_step), reporter, day=day.source_date, stage="events", detail="flatfile events")
        continuity_profile = _run_profiled_with_reporter(
            client,
            f"insert_events_continuity_from_flatfiles_{day.source_date}",
            insert_direct_day_continuity_sql(args, day, job.build_step),
            reporter,
            day=day.source_date,
            stage="continuity",
            detail="ticker continuity",
        )
        if reporter is not None:
            reporter.task_start(f"{day.source_date}:audit:continuity", "validate continuity counts", day=day.source_date, stage="audit")
        validate_day_continuity_after_insert(client, args, day, job.build_step, profile.written_rows, report_path)
        if reporter is not None:
            reporter.task_done(f"{day.source_date}:audit:continuity", "ok", rows=profile.written_rows, detail="continuity sum matches event rows")
            reporter.task_start(f"{day.source_date}:index:events_inserted", "rebuild ticker/day index", day=day.source_date, stage="index")
        index_profile = rebuild_day_ticker_index(client, args, day, job.build_step, report_path, reason="events_inserted")
        if reporter is not None:
            reporter.task_done(f"{day.source_date}:index:events_inserted", "ok", seconds=float(index_profile.wall_seconds), rows=index_profile.written_rows, detail="events inserted")
        insert_day_manifest(client, args, job, status="ok", run_id=run_id, profile=profile)
        append_jsonl(
            report_path,
            {
                "type": "day",
                "source_date": day.source_date,
                "status": "ok",
                "event_profile": asdict(profile),
                "continuity_profile": asdict(continuity_profile),
                "ticker_day_index_profile": asdict(index_profile),
            },
        )
        print(
            f"DAY OK {day.source_date} events_seconds={profile.wall_seconds:.1f} "
            f"written_rows={format_optional_int(profile.written_rows)} index_seconds={index_profile.wall_seconds:.1f}",
            flush=True,
        )
        return "ok"
    except KeyboardInterrupt:
        profile = QueryProfile(label=f"insert_events_from_flatfiles_{day.source_date}", query_id="", wall_seconds=0.0, exception="KeyboardInterrupt")
        insert_day_manifest(client, args, job, status="interrupted", run_id=run_id, profile=profile, exception="KeyboardInterrupt")
        raise
    except Exception as exc:
        profile = QueryProfile(label=f"insert_events_from_flatfiles_{day.source_date}", query_id="", wall_seconds=0.0, exception=repr(exc))
        insert_day_manifest(client, args, job, status="failed", run_id=run_id, profile=profile, exception=repr(exc))
        append_jsonl(report_path, {"type": "day", "source_date": day.source_date, "status": "failed", "exception": repr(exc)})
        raise


def build_updated_bars(client: ClickHouseHttpClient, args: argparse.Namespace, days: list[DayFiles], report_path: Path, reporter: UpdateProgressReporter | None = None) -> None:
    if args.skip_bars:
        if reporter is not None:
            reporter.notice("Macro bar creation skipped because --skip-bars was set.", style="yellow")
        print("BAR SKIP --skip-bars was set", flush=True)
        return
    if not days:
        if reporter is not None:
            reporter.notice("Macro bar creation skipped because no successfully updated days were available.", style="yellow")
        print("BAR SKIP no successfully updated days", flush=True)
        return
    parse_timeframes(args.bar_timeframes)
    min_day = min(day.source_date for day in days)
    max_day = max(day.source_date for day in days)
    bar_args = argparse.Namespace(
        bar_mode="macro",
        clickhouse_url=args.clickhouse_url,
        user=args.user,
        password=args.password,
        database=args.database,
        events_table=args.events_table,
        macro_bars_table=args.macro_bars_table,
        bars_table=args.bars_table,
        bars_by_symbol_time_table=args.bars_by_symbol_time_table,
        bars_by_time_symbol_table=args.bars_by_time_symbol_table,
        start_date=min_day,
        end_date=max_day,
        timeframes=args.bar_timeframes,
        storage_policy=args.storage_policy,
        max_threads=args.max_threads,
        max_memory_usage=args.max_memory_usage,
        output_root_win=args.output_root_win,
        replace_range=args.bar_replace_range,
        chunk_days=args.bar_chunk_days,
        staging_table=args.bar_staging_table,
        keep_staging_table=args.bar_keep_staging_table,
        copy_at_end=args.bar_copy_at_end,
        summarize_chunks=args.bar_summarize_chunks,
        expand_boundaries=True,
        drop_table=False,
        purge_unsupported_macro_timeframes=False,
        dry_run=args.dry_run,
    )
    if bar_args.drop_table or getattr(bar_args, "purge_unsupported_macro_timeframes", False):
        raise RuntimeError("Direct flatfile updates must not drop or purge macro bar tables.")
    bar_specs = parse_timeframes(args.bar_timeframes)
    ranges = timeframe_ranges(bar_args, bar_specs)
    bar_tables = bar_table_specs(bar_args)
    if reporter is not None:
        if bar_args.replace_range:
            reporter.notice(
                f"Macro bar replace_range is enabled; overlapping rows will be rebuilt for requested days {min_day}->{max_day}.",
                style="bold yellow",
            )
        reporter.task_start("macro_bars:build", "build macro bars", day=f"{min_day}->{max_day}", stage="macro")
    print("=" * 100, flush=True)
    print(
        f"MACRO BAR UPDATE tables={format_bar_tables(bar_tables)} timeframes={bar_args.timeframes} "
        f"requested_days={min_day}->{max_day} build_ranges={format_timeframe_ranges(ranges)}",
        flush=True,
    )
    print("=" * 100, flush=True)
    started_at = time.time()
    try:
        results = build_macro_bars(client, bar_args, report_path=report_path)
    except Exception as exc:
        if reporter is not None:
            reporter.task_done("macro_bars:build", "failed", seconds=time.time() - started_at, detail=repr(exc))
        raise
    if reporter is not None:
        reporter.task_done("macro_bars:build", "ok", seconds=time.time() - started_at, rows=len(results), detail=f"timeframes={bar_args.timeframes}")
    append_jsonl(
        report_path,
        {
            "type": "bar_update",
            "bar_tables": [asdict(spec) for spec in bar_tables],
            "timeframes": bar_args.timeframes,
            "requested_start_date": min_day,
            "requested_end_date": max_day,
            "build_ranges": {timeframe: {"start_date": start, "end_date": end} for timeframe, (start, end) in ranges.items()},
            "result_count": len(results),
        },
    )


def main() -> None:
    loaded_env_files = load_env_files(discover_clickhouse_env_files(), verbose=True)
    args = parse_args()
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    if args.test_mode:
        configure_test_tables(args, run_id)
    report_path = Path(args.output_root_win) / f"flatfile_event_update_{run_id}.jsonl"
    bar_tables = bar_table_specs(args)
    print("=" * 100, flush=True)
    print("Massive SIP flatfile download + direct event update", flush=True)
    print(f"database={args.database} events_table={args.events_table}", flush=True)
    print(f"ticker_day_index_table={args.ticker_day_index_table}", flush=True)
    print(f"bar_tables={format_bar_tables(bar_tables)}", flush=True)
    print(f"test_mode={args.test_mode}", flush=True)
    if args.test_mode:
        print(
            f"test_tables manifest={args.manifest_table} continuity={args.continuity_table} "
            f"ticker_day_index={args.ticker_day_index_table} macro_bars={format_bar_tables(bar_tables)} "
            f"raw_csv_sample_size={args.test_sample_size}",
            flush=True,
        )
    print(f"date_range={args.start_date} -> {args.end_date}", flush=True)
    print(f"flatfiles_root_win={args.flatfiles_root_win}", flush=True)
    print(f"flatfiles_root_ch={args.flatfiles_root_ch}", flush=True)
    print(f"download_workers={args.download_workers} max_threads={args.max_threads}", flush=True)
    print(f"storage_policy={args.storage_policy}", flush=True)
    print(f"macro_bar_timeframes={args.bar_timeframes} skip_bars={args.skip_bars} bar_replace_range={args.bar_replace_range}", flush=True)
    print(f"report={report_path}", flush=True)
    print(f"secret_status={secret_status(env_status_keys() + ['AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'S3_ENDPOINT_URL', 'BUCKET'])}", flush=True)
    print(f"loaded_env_files={loaded_env_files}", flush=True)
    print("=" * 100, flush=True)

    config = download_config(args)
    days = source_days(args, config)
    if not days:
        print("No complete quote/trade day pairs discovered.", flush=True)
        return
    print(f"Discovered {len(days):,} complete quote/trade day pairs", flush=True)

    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    total_download_bytes = sum(int(day.quote_job.remote_size or 0) + int(day.trade_job.remote_size or 0) for day in days)
    with UpdateProgressReporter(args, total_days=len(days), total_files=len(days) * 2, total_bytes=total_download_bytes) as reporter:
        reporter.log(f"report={report_path}")
        reporter.log(
            f"database={args.database} events_table={args.events_table} "
            f"ticker_day_index={args.ticker_day_index_table} bars={format_bar_tables(bar_tables)} test_mode={args.test_mode}"
        )
        if args.test_mode:
            reporter.set_stage("prepare test tables")
            reporter.notice("Dropping same-run temp tables before isolated test build.", style="bold yellow")
            drop_test_tables(client, args)
        reporter.set_stage("ensure tables")
        reporter.notice("Ensuring event, manifest, continuity, ticker/day index, and macro bar tables exist.", style="cyan")
        ensure_tables(client, args)

        progress_queue: mp.Queue = mp.Queue()
        reporter.set_stage("download flatfiles")
        download_results = run_download_phase(
            days=days,
            config=config,
            args=args,
            reporter=reporter,
            report_path=report_path,
            progress_queue=progress_queue,
        )

        reporter.set_stage("insert events")
        completed = 0
        bar_days: list[DayFiles] = []
        for day in sorted(days, key=lambda item: item.source_date):
            if not download_results.get(day.source_date, {}).get("ok"):
                reporter.handle_day_done(day.source_date, "download_failed")
                continue
            completed += 1
            reporter.handle_day_start(day.source_date, completed)
            if args.dry_run:
                reporter.handle_day_done(day.source_date, "dry_run")
                continue
            result_status = run_day(client, args, day, run_id, report_path, reporter=reporter)
            reporter.handle_day_done(day.source_date, result_status)
            if result_status in {"ok", "skipped"}:
                bar_days.append(day)

        if args.test_mode and not args.dry_run:
            reporter.set_stage("test audit")
            audited_days = [day for day in sorted(days, key=lambda item: item.source_date) if download_results.get(day.source_date, {}).get("ok")]
            audit_test_events(client, args, audited_days, report_path)
        reporter.set_stage("build bars")
        build_updated_bars(client, args, bar_days, report_path, reporter=reporter)
        if args.test_mode and not args.dry_run:
            if not args.test_keep_tables:
                reporter.set_stage("drop test tables")
                reporter.notice("Dropping isolated test tables after successful test-mode audit.", style="bold yellow")
                drop_test_tables(client, args)

    print(f"DONE report={report_path}", flush=True)


if __name__ == "__main__":
    main()
