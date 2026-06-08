from __future__ import annotations

import argparse
import concurrent.futures
import gzip
import json
import os
import re
import shutil
import sys
import tarfile
import threading
import time
import traceback
from collections import deque
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib import error, request


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402
from research.mlops.sec_historical_feed_download import (  # noqa: E402
    DEFAULT_ARTIFACT_ROOT_WIN,
    DEFAULT_OUTPUT_ROOT_WIN,
    DayJob,
    MissingArchiveError,
    RateLimiter,
    RETRY_HTTP_CODES,
    build_day_job,
    discover_available_archive_days,
    fetch_headers_for_submissions,
    merge_submission_timestamp,
    parse_retry_after,
    parse_date,
    parse_nc_archive,
    quarter_name,
    sec_user_agent,
    sha256_file,
    write_rows,
)


DEFAULT_NORMALIZED_ROOT_WIN = Path("D:/market-data/sec_edgar_feed_normalized")
DEFAULT_TEMP_ROOT_WIN = Path("D:/market-data/sec_edgar_feed_temp")


@dataclass(frozen=True, slots=True)
class PipelineDayResult:
    archive_date: str
    archive_url: str
    hdd_archive_path: str
    temp_archive_path: str
    normalized_day_dir: str
    archive_bytes: int
    archive_sha256: str
    submissions: int
    documents: int
    header_success: int
    header_unavailable: int
    header_failed: int
    download_seconds: float
    hdd_copy_seconds: float
    parse_seconds: float
    wall_seconds: float
    archive_source: str
    hdd_copy_status: str
    cleanup_status: str
    status: str
    error: str = ""


@dataclass(frozen=True, slots=True)
class DownloadedArchive:
    job: DayJob
    temp_archive_path: str
    hdd_archive_path: str
    archive_bytes: int
    archive_sha256: str
    download_seconds: float
    archive_source: str


class ArchiveIntegrityError(RuntimeError):
    pass


class ProgressDisplay:
    STAGES = ("download", "validate", "copy", "parse", "headers")
    STAGE_TITLES = {
        "download": "Download",
        "validate": "Validate",
        "copy": "Copy",
        "parse": "Parse",
        "headers": "Headers",
    }

    def __init__(self, mode: str, log_lines: int, progress_rows: int = 12, screen: bool = True, worker_slots: int = 1) -> None:
        self.mode = mode
        self.log_lines = max(5, log_lines)
        self.progress_rows = max(6, progress_rows)
        self.screen = screen
        self.worker_slots = max(1, worker_slots)
        self._lock = threading.RLock()
        self._logs: deque[str] = deque(maxlen=self.log_lines)
        self._task_stage: dict[str, tuple[str, str]] = {}
        self._job_slot: dict[str, int] = {}
        self._rows = [self._empty_row(slot) for slot in range(self.worker_slots)]
        self._rich = False
        self._live: Any = None
        self._layout: Any = None

    def __enter__(self) -> "ProgressDisplay":
        if self.mode in {"auto", "rich"}:
            try:
                from rich.layout import Layout
                from rich.live import Live
                from rich.panel import Panel
                from rich.table import Table
                from rich.text import Text
            except ImportError:
                if self.mode == "rich":
                    raise
            else:
                self._rich = True
                self._text_cls = Text
                self._panel_cls = Panel
                self._table_cls = Table
                self._layout = Layout(name="root")
                self._layout.split_column(
                    Layout(self._progress_panel(), name="progress", size=self.progress_rows),
                    Layout(self._log_panel(), name="logs", ratio=1),
                )
                self._live = Live(
                    self._layout,
                    refresh_per_second=4,
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
            self._live.stop()

    @property
    def rich_active(self) -> bool:
        return self._rich

    def job_start(self, job_key: str, slot: int | None = None) -> None:
        with self._lock:
            slot_index = self._resolve_slot(job_key, slot)
            row = self._rows[slot_index]
            if row["job"] != job_key:
                self._rows[slot_index] = self._empty_row(slot_index)
                self._rows[slot_index]["job"] = job_key
            self._job_slot[job_key] = slot_index
            if self._rich:
                self._refresh()

    def job_finish(self, job_key: str) -> None:
        with self._lock:
            slot_index = self._job_slot.pop(job_key, None)
            if slot_index is not None:
                self._rows[slot_index] = self._empty_row(slot_index)
            stale = [key for key, (job, _stage) in self._task_stage.items() if job == job_key]
            for key in stale:
                self._task_stage.pop(key, None)
            if self._rich:
                self._refresh()

    def log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"{timestamp} {message}"
        with self._lock:
            if self._rich:
                self._logs.append(line)
                self._refresh()
            else:
                print(line, flush=True)

    def task_start(self, key: str, description: str, total: int | float | None = None, detail: str = "") -> None:
        with self._lock:
            if not self._rich:
                return
            job, stage = self._resolve_task(key, description)
            self.job_start(job)
            self._task_stage[key] = (job, stage)
            self._row_for_job(job)["stages"][stage] = {
                "status": "running",
                "completed": 0.0,
                "total": float(total) if total else None,
                "detail": detail,
                "started_at": time.perf_counter(),
                "ended_at": None,
            }
            self._refresh()

    def task_update(
        self,
        key: str,
        advance: int | float = 0,
        completed: int | float | None = None,
        total: int | float | None = None,
        detail: str | None = None,
    ) -> None:
        with self._lock:
            if not self._rich or key not in self._task_stage:
                return
            job, stage = self._task_stage[key]
            state = self._row_for_job(job)["stages"][stage]
            if total is not None:
                state["total"] = float(total) if total else None
            if completed is not None:
                state["completed"] = float(completed)
            else:
                state["completed"] = float(state.get("completed") or 0) + float(advance or 0)
            if detail is not None:
                state["detail"] = detail
            self._refresh()

    def task_stop(self, key: str, detail: str = "") -> None:
        with self._lock:
            if not self._rich or key not in self._task_stage:
                return
            job, stage = self._task_stage[key]
            state = self._row_for_job(job)["stages"][stage]
            state["status"] = "complete"
            state["ended_at"] = time.perf_counter()
            if state.get("total") is None:
                state["total"] = 1.0
                state["completed"] = 1.0
            else:
                state["completed"] = state["total"]
            if detail:
                state["detail"] = detail
            self._refresh()

    def _refresh(self) -> None:
        if not self._rich:
            return
        self._layout["progress"].update(self._progress_panel())
        self._layout["logs"].update(self._log_panel())

    def _progress_panel(self) -> Any:
        table = self._table_cls(expand=True, show_header=True, header_style="bold", box=None, pad_edge=False)
        table.add_column("File", no_wrap=True, width=12)
        for stage in self.STAGES:
            table.add_column(self.STAGE_TITLES[stage], ratio=1, min_width=14, no_wrap=True)
        for row in self._rows:
            job = row["job"]
            file_label = job if job else f"slot {row['slot'] + 1}"
            file_style = "bold cyan" if job else "dim"
            states = row["stages"]
            table.add_row(self._text_cls(file_label, style=file_style), *(self._stage_cell(states[stage]) for stage in self.STAGES))
        return self._panel_cls(table, title="Per-file stage progress", border_style="cyan")

    def _log_panel(self) -> Any:
        log_text = self._text_cls("\n".join(self._logs) if self._logs else "No log messages yet.")
        return self._panel_cls(log_text, title="Logs (oldest to newest)", border_style="white")

    def _stage_cell(self, state: dict[str, Any]) -> Any:
        status = state.get("status", "pending")
        if status == "pending":
            return self._text_cls("-", style="dim", no_wrap=True)
        completed = float(state.get("completed") or 0)
        total = state.get("total")
        detail = str(state.get("detail") or "")
        elapsed = self._stage_elapsed(state)
        elapsed_text = f"{elapsed:.1f}s" if elapsed is not None else ""
        if total:
            pct = completed / float(total)
            message = self._compact_stage_message(
                f"{self._mini_bar(pct)}{pct:.0%}",
                10,
            )
            return self._text_cls(message, style="green" if status == "complete" else "white", no_wrap=True)
        style = "green" if status == "complete" else "yellow"
        short_status = "done" if status == "complete" else "run"
        message = self._compact_stage_message(" ".join(item for item in (short_status, elapsed_text) if item), 16)
        return self._text_cls(message, style=style, no_wrap=True)

    def _compact_stage_message(self, message: str, limit: int = 16) -> str:
        normalized = " ".join(message.split())
        if len(normalized) <= limit:
            return normalized
        return normalized[: max(0, limit - 1)].rstrip() + "..."

    def _mini_bar(self, fraction: float, width: int = 6) -> str:
        bounded = max(0.0, min(1.0, fraction))
        filled = int(round(bounded * width))
        return "#" * filled + "-" * (width - filled)

    def _empty_stage(self) -> dict[str, Any]:
        return {"status": "pending", "completed": 0.0, "total": None, "detail": "", "started_at": None, "ended_at": None}

    def _empty_row(self, slot: int) -> dict[str, Any]:
        return {
            "slot": slot,
            "job": "",
            "stages": {stage: self._empty_stage() for stage in self.STAGES},
        }

    def _resolve_slot(self, job_key: str, slot: int | None = None) -> int:
        if job_key in self._job_slot:
            return self._job_slot[job_key]
        if slot is not None:
            return min(max(0, slot), self.worker_slots - 1)
        for row in self._rows:
            if not row["job"]:
                return int(row["slot"])
        return 0

    def _row_for_job(self, job_key: str) -> dict[str, Any]:
        slot_index = self._resolve_slot(job_key)
        self._job_slot[job_key] = slot_index
        row = self._rows[slot_index]
        if row["job"] != job_key:
            self._rows[slot_index] = self._empty_row(slot_index)
            self._rows[slot_index]["job"] = job_key
        return self._rows[slot_index]

    def _stage_elapsed(self, state: dict[str, Any]) -> float | None:
        started_at = state.get("started_at")
        if started_at is None:
            return None
        ended_at = state.get("ended_at") or time.perf_counter()
        return max(0.0, float(ended_at) - float(started_at))

    def _resolve_task(self, key: str, description: str) -> tuple[str, str]:
        text = f"{key} {description}"
        job = "global"
        if "[" in text and "]" in text:
            job = text.split("[", 1)[1].split("]", 1)[0]
        else:
            match = re.search(r"\d{4}-\d{2}-\d{2}", text)
            if match:
                job = match.group(0)
        lowered = text.lower()
        if "download" in lowered:
            return job, "download"
        if "validate" in lowered:
            return job, "validate"
        if "copy" in lowered:
            return job, "copy"
        if "parse" in lowered:
            return job, "parse"
        if "headers" in lowered or "header" in lowered:
            return job, "headers"
        return job, "parse"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Bounded SEC EDGAR Feed pipeline: discover real archive days, stage compressed .nc.tar.gz archives "
            "on SSD, archive them to HDD, stream-parse normalized filing/document rows from SSD, fetch accepted_at "
            "headers, and store normalized JSONL."
        )
    )
    parser.add_argument("--start-date", required=True, help="Inclusive archive date, YYYY-MM-DD.")
    parser.add_argument("--end-date", required=True, help="Exclusive archive date, YYYY-MM-DD.")
    parser.add_argument("--artifact-root-win", default=os.environ.get("SEC_HISTORICAL_ARTIFACT_ROOT_WIN", str(DEFAULT_ARTIFACT_ROOT_WIN)))
    parser.add_argument("--temp-root-win", default=os.environ.get("SEC_HISTORICAL_TEMP_ROOT_WIN", str(DEFAULT_TEMP_ROOT_WIN)))
    parser.add_argument("--normalized-root-win", default=os.environ.get("SEC_HISTORICAL_NORMALIZED_ROOT_WIN", str(DEFAULT_NORMALIZED_ROOT_WIN)))
    parser.add_argument("--output-root-win", default=os.environ.get("SEC_HISTORICAL_OUTPUT_ROOT_WIN", str(DEFAULT_OUTPUT_ROOT_WIN)))
    parser.add_argument("--download-concurrency", type=int, default=int(os.environ.get("SEC_DOWNLOAD_CONCURRENCY", "2")))
    parser.add_argument("--archive-copy-concurrency", type=int, default=int(os.environ.get("SEC_ARCHIVE_COPY_CONCURRENCY", "1")))
    parser.add_argument("--header-concurrency", type=int, default=int(os.environ.get("SEC_HEADER_CONCURRENCY", "8")))
    parser.add_argument("--sec-request-min-interval-seconds", type=float, default=float(os.environ.get("SEC_REQUEST_MIN_INTERVAL_SECONDS", "0.11")))
    parser.add_argument("--request-timeout-seconds", type=float, default=float(os.environ.get("SEC_REQUEST_TIMEOUT_SECONDS", "60")))
    parser.add_argument("--max-retries", type=int, default=int(os.environ.get("SEC_MAX_RETRIES", "4")))
    parser.add_argument("--retry-base-seconds", type=float, default=float(os.environ.get("SEC_RETRY_BASE_SECONDS", "1.5")))
    parser.add_argument("--progress-interval-seconds", type=float, default=float(os.environ.get("SEC_PROGRESS_INTERVAL_SECONDS", "10")))
    parser.add_argument("--progress-file-interval-mib", type=float, default=float(os.environ.get("SEC_PROGRESS_FILE_INTERVAL_MIB", "64")))
    parser.add_argument("--progress-record-interval", type=int, default=int(os.environ.get("SEC_PROGRESS_RECORD_INTERVAL", "500")))
    parser.add_argument(
        "--progress-layout",
        choices=["auto", "rich", "text"],
        default=os.environ.get("SEC_PROGRESS_LAYOUT", "auto"),
        help="Console progress layout. auto uses Rich when installed; text keeps plain logs/tqdm.",
    )
    parser.add_argument("--progress-log-lines", type=int, default=int(os.environ.get("SEC_PROGRESS_LOG_LINES", "24")))
    parser.add_argument("--progress-panel-rows", type=int, default=int(os.environ.get("SEC_PROGRESS_PANEL_ROWS", "12")))
    parser.set_defaults(progress_screen=parse_bool_env("SEC_PROGRESS_SCREEN", True))
    progress_screen_group = parser.add_mutually_exclusive_group()
    progress_screen_group.add_argument(
        "--progress-screen",
        dest="progress_screen",
        action="store_true",
        help="Use a fixed Rich live screen so log updates cannot scroll the progress matrix.",
    )
    progress_screen_group.add_argument(
        "--no-progress-screen",
        dest="progress_screen",
        action="store_false",
        help="Render Rich progress in normal terminal scrollback instead of the fixed live screen.",
    )
    parser.set_defaults(download_progress_bars=parse_bool_env("SEC_DOWNLOAD_PROGRESS_BARS", True))
    progress_bar_group = parser.add_mutually_exclusive_group()
    progress_bar_group.add_argument("--download-progress-bars", dest="download_progress_bars", action="store_true", help="Show tqdm progress bars for SEC archive downloads.")
    progress_bar_group.add_argument("--no-download-progress-bars", dest="download_progress_bars", action="store_false", help="Disable tqdm archive download bars and use text progress only.")
    parser.add_argument("--limit-days", type=int, default=0, help="Smoke-test cap on discovered archive days.")
    parser.add_argument("--limit-files-per-day", type=int, default=0, help="Smoke-test cap on .nc files parsed per day.")
    parser.add_argument("--force-redownload", action="store_true", help="Redownload archives even when already present.")
    parser.add_argument("--no-header-fetch", action="store_true", help="Skip .hdr.sgml accepted_at enrichment.")
    parser.add_argument("--persist-nc-files", action="store_true", help="Also persist individual .nc files. Default streams from tar.gz only.")
    parser.add_argument(
        "--delete-archive-after-parse",
        action="store_true",
        help=(
            "Delete the permanent HDD compressed archive after successful normalized output write. "
            "SSD temp archives are always deleted after parse and verified HDD copy."
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="Only discover and report jobs.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    loaded_env_files = load_env_files(discover_env_files(REPO_ROOT), verbose=False)
    start_date = parse_date(args.start_date)
    end_date = parse_date(args.end_date)
    if end_date <= start_date:
        raise SystemExit("--end-date must be later than --start-date")

    artifact_root = Path(args.artifact_root_win)
    temp_root = Path(args.temp_root_win)
    normalized_root = Path(args.normalized_root_win)
    output_root = Path(args.output_root_win)
    output_root.mkdir(parents=True, exist_ok=True)
    temp_root.mkdir(parents=True, exist_ok=True)
    normalized_root.mkdir(parents=True, exist_ok=True)
    if temp_root.resolve() == artifact_root.resolve():
        raise SystemExit("--temp-root-win must be different from --artifact-root-win because temp archives are cleaned up after parsing")
    user_agent = sec_user_agent()
    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    report_path = output_root / f"sec_feed_pipeline_{run_id}.jsonl"
    request_min_interval = max(0.0, args.sec_request_min_interval_seconds)
    request_timeout = max(1.0, args.request_timeout_seconds)
    max_retries = max(0, args.max_retries)
    retry_base = max(0.1, args.retry_base_seconds)
    progress_interval = max(1.0, args.progress_interval_seconds)
    progress_file_interval_mib = max(1.0, args.progress_file_interval_mib)
    progress_record_interval = max(1, args.progress_record_interval)

    discovery_limiter = RateLimiter(request_min_interval)
    days = discover_available_archive_days(start_date, end_date, user_agent, request_timeout, max_retries, retry_base, discovery_limiter)
    if args.limit_days:
        days = days[: max(0, args.limit_days)]
    jobs = [
        build_day_job(
            day,
            artifact_root,
            user_agent,
            request_min_interval,
            request_timeout,
            max_retries,
            retry_base,
            max(1, args.header_concurrency),
            max(0, args.limit_files_per_day),
            args.force_redownload,
            False,
            args.persist_nc_files,
            args.no_header_fetch,
        )
        for day in days
    ]

    config = {
        "type": "config",
        "run_id": run_id,
        "script": str(Path(__file__).resolve()),
        "start_date": args.start_date,
        "end_date": args.end_date,
        "artifact_root": str(artifact_root),
        "temp_root": str(temp_root),
        "normalized_root": str(normalized_root),
        "output_root": str(output_root),
        "report_path": str(report_path),
        "download_concurrency": max(1, args.download_concurrency),
        "archive_copy_concurrency": max(1, args.archive_copy_concurrency),
        "header_concurrency": max(1, args.header_concurrency),
        "sec_request_min_interval_seconds": request_min_interval,
        "progress_interval_seconds": progress_interval,
        "progress_file_interval_mib": progress_file_interval_mib,
        "progress_record_interval": progress_record_interval,
        "progress_layout": args.progress_layout,
        "progress_log_lines": max(5, args.progress_log_lines),
        "progress_panel_rows": max(6, args.progress_panel_rows),
        "progress_screen": bool(args.progress_screen),
        "download_progress_bars": bool(args.download_progress_bars),
        "limit_days": max(0, args.limit_days),
        "limit_files_per_day": max(0, args.limit_files_per_day),
        "persist_nc_files": args.persist_nc_files,
        "delete_archive_after_parse": args.delete_archive_after_parse,
        "no_header_fetch": args.no_header_fetch,
        "job_count": len(jobs),
        "job_discovery": "sec_feed_quarter_directory_listing",
        "secret_status": secret_status(["SEC_USER_AGENT", "SEC_EDGAR_USER_AGENT", "NEWS_SEC_USER_AGENT"]),
        "loaded_env_files": [str(path) for path in loaded_env_files],
    }
    append_jsonl(report_path, config)

    with ProgressDisplay(
        args.progress_layout,
        args.progress_log_lines,
        args.progress_panel_rows,
        bool(args.progress_screen),
        max(1, args.download_concurrency),
    ) as progress:
        print_header(config, progress)

        if args.dry_run:
            for job in jobs:
                append_jsonl(report_path, {"type": "planned_job", "run_id": run_id, "job": asdict(job)})
            progress.log("dry_run=1, no archives downloaded and no normalized rows written")
            return

        run_pipeline(args, jobs, temp_root, normalized_root, report_path, run_id, progress)


def run_pipeline(
    args: argparse.Namespace,
    jobs: list[DayJob],
    temp_root: Path,
    normalized_root: Path,
    report_path: Path,
    run_id: str,
    progress: ProgressDisplay,
) -> None:
    started = time.perf_counter()
    sec_request_limiter = RateLimiter(max(0.0, args.sec_request_min_interval_seconds))
    download_concurrency = max(1, args.download_concurrency)
    archive_copy_concurrency = max(1, args.archive_copy_concurrency)
    progress_interval = max(1.0, args.progress_interval_seconds)
    progress_file_interval_bytes = int(max(1.0, args.progress_file_interval_mib) * 1024 * 1024)
    progress_record_interval = max(1, args.progress_record_interval)
    next_index = 0
    active: dict[concurrent.futures.Future[DownloadedArchive], tuple[DayJob, int]] = {}
    available_download_slots = list(range(download_concurrency))
    totals = {
        "staged_days": 0,
        "parsed_days": 0,
        "failed_days": 0,
        "archive_bytes": 0,
        "submissions": 0,
        "documents": 0,
        "header_success": 0,
        "header_unavailable": 0,
        "header_failed": 0,
        "hdd_copied_days": 0,
        "ssd_cleaned_days": 0,
    }

    with (
        concurrent.futures.ThreadPoolExecutor(max_workers=download_concurrency) as pool,
        concurrent.futures.ThreadPoolExecutor(max_workers=archive_copy_concurrency) as copy_pool,
    ):
        while next_index < len(jobs) and len(active) < download_concurrency:
            slot = available_download_slots.pop(0)
            progress.job_start(jobs[next_index].archive_date, slot)
            progress.log(f"[{jobs[next_index].archive_date}] queued for SSD staging")
            future = pool.submit(
                stage_archive_on_ssd,
                jobs[next_index],
                temp_root,
                sec_request_limiter,
                progress_interval,
                progress_file_interval_bytes,
                progress_record_interval,
                bool(args.download_progress_bars),
                slot,
                progress,
            )
            active[future] = (jobs[next_index], slot)
            next_index += 1

        while active:
            done, _ = concurrent.futures.wait(active.keys(), return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                job, slot = active.pop(future)
                available_download_slots.append(slot)
                available_download_slots.sort()
                try:
                    downloaded = future.result()
                    totals["staged_days"] += 1
                except Exception as exc:  # noqa: BLE001
                    result = failed_result(job, exc)
                else:
                    try:
                        result = parse_one_archive(downloaded, args, normalized_root, sec_request_limiter, copy_pool, progress)
                    except Exception as exc:  # noqa: BLE001
                        result = failed_result(downloaded.job, exc, downloaded)
                append_jsonl(report_path, {"type": "day", "run_id": run_id, "result": asdict(result)})
                if result.status == "ok":
                    totals["parsed_days"] += 1
                    totals["archive_bytes"] += result.archive_bytes
                    totals["submissions"] += result.submissions
                    totals["documents"] += result.documents
                    totals["header_success"] += result.header_success
                    totals["header_unavailable"] += result.header_unavailable
                    totals["header_failed"] += result.header_failed
                    totals["hdd_copied_days"] += 1 if result.hdd_copy_status in {"ok", "existing"} else 0
                    totals["ssd_cleaned_days"] += 1 if result.cleanup_status.startswith("ssd_deleted") else 0
                else:
                    totals["failed_days"] += 1
                print_progress(len(jobs), totals, started, result, progress)
                progress.job_finish(result.archive_date)
                while next_index < len(jobs) and len(active) < download_concurrency:
                    slot = available_download_slots.pop(0)
                    progress.job_start(jobs[next_index].archive_date, slot)
                    progress.log(f"[{jobs[next_index].archive_date}] queued for SSD staging")
                    future = pool.submit(
                        stage_archive_on_ssd,
                        jobs[next_index],
                        temp_root,
                        sec_request_limiter,
                        progress_interval,
                        progress_file_interval_bytes,
                        progress_record_interval,
                        bool(args.download_progress_bars),
                        slot,
                        progress,
                    )
                    active[future] = (jobs[next_index], slot)
                    next_index += 1

    summary = {
        "type": "summary",
        "run_id": run_id,
        "status": "failed" if totals["failed_days"] else "ok",
        "wall_seconds": round(time.perf_counter() - started, 3),
        **totals,
    }
    append_jsonl(report_path, summary)
    progress.log("summary=" + json.dumps(summary, sort_keys=True))


def stage_archive_on_ssd(
    job: DayJob,
    temp_root: Path,
    limiter: RateLimiter,
    progress_interval_seconds: float,
    progress_file_interval_bytes: int,
    progress_record_interval: int,
    download_progress_bars: bool,
    progress_position: int,
    progress: ProgressDisplay,
) -> DownloadedArchive:
    started = time.perf_counter()
    temp_archive_path = temp_archive_path_for_job(job, temp_root)
    hdd_archive_path = Path(job.archive_path)
    source = "sec_download"
    label = f"[{job.archive_date}]"
    progress.log(f"{label} staging: temp={temp_archive_path} hdd={hdd_archive_path}")

    if temp_archive_path.exists() and temp_archive_path.stat().st_size > 0 and not job.force_redownload:
        try:
            progress.log(f"{label} staging: validating existing SSD temp archive ({format_bytes(temp_archive_path.stat().st_size)})")
            validate_archive_integrity(temp_archive_path, f"{label} validate-ssd", progress_record_interval, progress_interval_seconds, progress)
            source = "ssd_existing"
        except ArchiveIntegrityError:
            progress.log(f"{label} staging: deleting corrupt SSD temp archive")
            temp_archive_path.unlink(missing_ok=True)

    if not temp_archive_path.exists() and hdd_archive_path.exists() and hdd_archive_path.stat().st_size > 0 and not job.force_redownload:
        try:
            progress.log(f"{label} staging: validating existing HDD archive ({format_bytes(hdd_archive_path.stat().st_size)})")
            validate_archive_integrity(hdd_archive_path, f"{label} validate-hdd", progress_record_interval, progress_interval_seconds, progress)
            copy_file_verified(
                hdd_archive_path,
                temp_archive_path,
                f"{label} copy-hdd-to-ssd",
                progress_file_interval_bytes,
                progress_interval_seconds,
                progress,
            )
            validate_archive_integrity(temp_archive_path, f"{label} validate-ssd", progress_record_interval, progress_interval_seconds, progress)
            source = "hdd_existing"
        except ArchiveIntegrityError:
            progress.log(f"{label} staging: deleting corrupt cached archive and redownloading")
            hdd_archive_path.unlink(missing_ok=True)
            temp_archive_path.unlink(missing_ok=True)

    if not temp_archive_path.exists():
        stream_download_to_file(
            job,
            temp_archive_path,
            limiter,
            progress_file_interval_bytes,
            progress_interval_seconds,
            download_progress_bars,
            progress_position,
            progress,
        )
        validate_archive_integrity(temp_archive_path, f"{label} validate-download", progress_record_interval, progress_interval_seconds, progress)

    elapsed = round(time.perf_counter() - started, 3)
    progress.log(f"{label} staging: ready source={source} size={format_bytes(temp_archive_path.stat().st_size)} elapsed={elapsed:.1f}s")
    return DownloadedArchive(
        job=job,
        temp_archive_path=str(temp_archive_path),
        hdd_archive_path=str(hdd_archive_path),
        archive_bytes=temp_archive_path.stat().st_size,
        archive_sha256=sha256_file(temp_archive_path),
        download_seconds=elapsed,
        archive_source=source,
    )


def stream_download_to_file(
    job: DayJob,
    target_path: Path,
    limiter: RateLimiter,
    progress_file_interval_bytes: int,
    progress_interval_seconds: float,
    download_progress_bars: bool,
    progress_position: int,
    progress: ProgressDisplay,
) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = target_path.with_suffix(target_path.suffix + ".part")
    part_path.unlink(missing_ok=True)
    headers = {
        "User-Agent": job.user_agent,
        "Accept-Encoding": "identity",
        "Host": "www.sec.gov",
    }
    last_error = ""
    for attempt in range(job.max_retries + 1):
        started = time.perf_counter()
        last_progress = started
        last_reported_bytes = 0
        limiter.wait()
        req = request.Request(job.archive_url, headers=headers)
        try:
            with request.urlopen(req, timeout=job.request_timeout_seconds) as response, part_path.open("wb") as handle:
                expected_length = response.headers.get("Content-Length")
                expected_bytes = int(expected_length) if expected_length else 0
                written = 0
                task_key = f"{job.archive_date}:download:{attempt}"
                use_structured_download_bar = download_progress_bars and progress.rich_active
                bar = make_download_bar(
                    enabled=download_progress_bars and not progress.rich_active,
                    archive_date=job.archive_date,
                    attempt=attempt + 1,
                    max_attempts=job.max_retries + 1,
                    total_bytes=expected_bytes,
                    position=progress_position,
                )
                if use_structured_download_bar:
                    progress.task_start(
                        task_key,
                        f"{job.archive_date} download {attempt + 1}/{job.max_retries + 1}",
                        total=expected_bytes or None,
                        detail=format_bytes(expected_bytes) if expected_bytes else "unknown size",
                    )
                progress.log(
                    f"[{job.archive_date}] download: attempt={attempt + 1}/{job.max_retries + 1} "
                    f"expected={format_bytes(expected_bytes) if expected_bytes else 'unknown'} target={target_path}"
                )
                try:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        handle.write(chunk)
                        written += len(chunk)
                        if bar is not None:
                            bar.update(len(chunk))
                        if use_structured_download_bar:
                            progress.task_update(
                                task_key,
                                advance=len(chunk),
                                detail=f"{format_bytes(written)}" + (f" / {format_bytes(expected_bytes)}" if expected_bytes else ""),
                            )
                        now = time.perf_counter()
                        if bar is None and not progress.rich_active and (
                            written - last_reported_bytes >= progress_file_interval_bytes
                            or now - last_progress >= progress_interval_seconds
                        ):
                            pct = f" {written / expected_bytes:.1%}" if expected_bytes else ""
                            progress.log(
                                f"[{job.archive_date}] download: {format_bytes(written)}{pct} elapsed={now - started:.1f}s",
                            )
                            last_progress = now
                            last_reported_bytes = written
                finally:
                    if bar is not None:
                        bar.close()
                    if use_structured_download_bar:
                        progress.task_stop(task_key, detail=f"complete {format_bytes(written)}")
            if expected_length and written != int(expected_length):
                part_path.unlink(missing_ok=True)
                raise RuntimeError(f"incomplete archive download: expected {expected_length} bytes, wrote {written} bytes")
            part_path.replace(target_path)
            progress.log(f"[{job.archive_date}] download: complete size={format_bytes(written)} elapsed={time.perf_counter() - started:.1f}s")
            return
        except error.HTTPError as exc:
            part_path.unlink(missing_ok=True)
            last_error = f"HTTP {exc.code}: {exc.reason}"
            if exc.code in {403, 404}:
                raise MissingArchiveError(last_error) from exc
            if exc.code not in RETRY_HTTP_CODES or attempt >= job.max_retries:
                raise RuntimeError(last_error) from exc
            retry_after = parse_retry_after(exc.headers.get("Retry-After"))
            time.sleep(retry_after if retry_after is not None else job.retry_base_seconds * (2**attempt))
        except Exception as exc:  # noqa: BLE001
            part_path.unlink(missing_ok=True)
            last_error = repr(exc)
            if attempt >= job.max_retries:
                raise RuntimeError(last_error) from exc
            time.sleep(job.retry_base_seconds * (2**attempt))
    raise RuntimeError(last_error or "archive download failed")


def validate_archive_integrity(
    archive_path: Path,
    progress_label: str = "",
    progress_every: int = 500,
    progress_interval_seconds: float = 10.0,
    progress: ProgressDisplay | None = None,
) -> int:
    try:
        nc_members = 0
        started = time.perf_counter()
        last_progress = started
        task_key = f"{progress_label}:validate"
        if progress is not None and progress_label:
            progress.task_start(task_key, progress_label, total=None, detail="scanning .nc members")
        with tarfile.open(archive_path, "r:gz") as tar:
            for member in tar:
                if member.isfile() and member.name.lower().endswith(".nc"):
                    nc_members += 1
                    if progress is not None and progress_label:
                        progress.task_update(task_key, advance=1, detail=f"{nc_members:,} .nc members")
                    now = time.perf_counter()
                    if progress_label and (
                        (progress_every > 0 and nc_members % progress_every == 0)
                        or now - last_progress >= progress_interval_seconds
                    ):
                        if progress is not None:
                            progress.log(f"{progress_label}: {nc_members:,} .nc members scanned elapsed={now - started:.1f}s")
                        else:
                            print(f"{progress_label}: {nc_members:,} .nc members scanned elapsed={now - started:.1f}s", flush=True)
                        last_progress = now
        if nc_members <= 0:
            raise ArchiveIntegrityError(f"archive contains no .nc members: {archive_path}")
        if progress_label:
            message = f"{progress_label}: complete members={nc_members:,} elapsed={time.perf_counter() - started:.1f}s"
            if progress is not None:
                progress.log(message)
                progress.task_stop(task_key, detail=f"{nc_members:,} members")
            else:
                print(message, flush=True)
        return nc_members
    except (EOFError, gzip.BadGzipFile, tarfile.TarError, OSError) as exc:
        raise ArchiveIntegrityError(f"invalid SEC feed archive {archive_path}: {exc!r}") from exc


def copy_archive_to_hdd(
    temp_archive_path: Path,
    hdd_archive_path: Path,
    expected_sha256: str,
    progress_label: str = "",
    progress_file_interval_bytes: int = 64 * 1024 * 1024,
    progress_interval_seconds: float = 10.0,
    progress: ProgressDisplay | None = None,
) -> tuple[str, float]:
    started = time.perf_counter()
    if hdd_archive_path.exists() and hdd_archive_path.stat().st_size == temp_archive_path.stat().st_size:
        if sha256_file(hdd_archive_path) == expected_sha256:
            if progress_label:
                if progress is not None:
                    progress.log(f"{progress_label}: HDD archive already verified")
                else:
                    print(f"{progress_label}: HDD archive already verified", flush=True)
            return "existing", round(time.perf_counter() - started, 3)
    copy_file_verified(temp_archive_path, hdd_archive_path, progress_label, progress_file_interval_bytes, progress_interval_seconds, progress)
    actual_sha256 = sha256_file(hdd_archive_path)
    if actual_sha256 != expected_sha256:
        hdd_archive_path.unlink(missing_ok=True)
        raise RuntimeError(f"HDD archive checksum mismatch for {hdd_archive_path}")
    if progress_label:
        if progress is not None:
            progress.log(f"{progress_label}: complete elapsed={time.perf_counter() - started:.1f}s")
        else:
            print(f"{progress_label}: complete elapsed={time.perf_counter() - started:.1f}s", flush=True)
    return "ok", round(time.perf_counter() - started, 3)


def copy_file_verified(
    source: Path,
    target: Path,
    progress_label: str = "",
    progress_file_interval_bytes: int = 64 * 1024 * 1024,
    progress_interval_seconds: float = 10.0,
    progress: ProgressDisplay | None = None,
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    part_path = target.with_suffix(target.suffix + ".part")
    part_path.unlink(missing_ok=True)
    total_bytes = source.stat().st_size
    copied = 0
    started = time.perf_counter()
    last_progress = started
    last_reported_bytes = 0
    if progress_label:
        if progress is not None:
            progress.log(f"{progress_label}: copying {format_bytes(total_bytes)} {source} -> {target}")
            progress.task_start(progress_label, progress_label, total=total_bytes, detail=format_bytes(total_bytes))
        else:
            print(f"{progress_label}: copying {format_bytes(total_bytes)} {source} -> {target}", flush=True)
    with source.open("rb") as src, part_path.open("wb") as dst:
        while True:
            chunk = src.read(1024 * 1024 * 16)
            if not chunk:
                break
            dst.write(chunk)
            copied += len(chunk)
            if progress is not None and progress_label:
                progress.task_update(progress_label, advance=len(chunk), detail=f"{format_bytes(copied)} / {format_bytes(total_bytes)}")
            now = time.perf_counter()
            if progress_label and (
                copied - last_reported_bytes >= progress_file_interval_bytes
                or now - last_progress >= progress_interval_seconds
            ):
                message = f"{progress_label}: {format_bytes(copied)} {copied / total_bytes:.1%} elapsed={now - started:.1f}s"
                if progress is not None:
                    progress.log(message)
                else:
                    print(message, flush=True)
                last_progress = now
                last_reported_bytes = copied
    if part_path.stat().st_size != source.stat().st_size:
        part_path.unlink(missing_ok=True)
        raise RuntimeError(f"copy size mismatch: {source} -> {target}")
    part_path.replace(target)
    if progress_label:
        if progress is not None:
            progress.log(f"{progress_label}: copied {format_bytes(copied)} elapsed={time.perf_counter() - started:.1f}s")
            progress.task_stop(progress_label, detail=f"copied {format_bytes(copied)}")
        else:
            print(f"{progress_label}: copied {format_bytes(copied)} elapsed={time.perf_counter() - started:.1f}s", flush=True)


def cleanup_archives(
    temp_archive_path: Path,
    hdd_archive_path: Path,
    hdd_copy_status: str,
    delete_hdd_archive_after_parse: bool,
) -> str:
    if hdd_copy_status not in {"ok", "existing"}:
        return "kept_ssd_copy_not_verified"
    temp_archive_path.unlink(missing_ok=True)
    if delete_hdd_archive_after_parse:
        hdd_archive_path.unlink(missing_ok=True)
        return "ssd_deleted_hdd_deleted"
    return "ssd_deleted"


def temp_archive_path_for_job(job: DayJob, temp_root: Path) -> Path:
    archive_day = parse_date(job.archive_date)
    quarter = quarter_name(archive_day)
    return temp_root / "archives" / f"{archive_day:%Y}" / quarter / f"{archive_day:%Y%m%d}.nc.tar.gz"


def parse_one_archive(
    downloaded: DownloadedArchive,
    args: argparse.Namespace,
    normalized_root: Path,
    sec_request_limiter: RateLimiter,
    copy_pool: concurrent.futures.ThreadPoolExecutor,
    progress: ProgressDisplay,
) -> PipelineDayResult:
    started = time.perf_counter()
    job = downloaded.job
    temp_archive_path = Path(downloaded.temp_archive_path)
    hdd_archive_path = Path(downloaded.hdd_archive_path)
    label = f"[{job.archive_date}]"
    progress_interval = max(1.0, args.progress_interval_seconds)
    progress_file_interval_bytes = int(max(1.0, args.progress_file_interval_mib) * 1024 * 1024)
    progress_record_interval = max(1, args.progress_record_interval)
    progress.log(f"{label} day: start parse/write pipeline source={downloaded.archive_source}")
    copy_future = copy_pool.submit(
        copy_archive_to_hdd,
        temp_archive_path,
        hdd_archive_path,
        downloaded.archive_sha256,
        f"{label} copy-ssd-to-hdd",
        progress_file_interval_bytes,
        progress_interval,
        progress,
    )
    normalized_day_dir = normalized_root / job.archive_date[:4] / quarter_name(parse_date(job.archive_date)) / job.archive_date
    normalized_day_dir.mkdir(parents=True, exist_ok=True)
    submissions_path = normalized_day_dir / "submissions.jsonl"
    documents_path = normalized_day_dir / "documents.jsonl"
    headers_path = normalized_day_dir / "headers.jsonl"
    manifest_path = normalized_day_dir / "manifest.jsonl"

    parsed, documents = parse_nc_archive(
        job.archive_date,
        temp_archive_path,
        Path(job.extract_dir),
        job.limit_files,
        job.persist_nc_files,
        f"{label}",
        progress_record_interval,
        progress_interval,
        progress,
    )
    timestamps = fetch_headers_for_submissions(
        parsed,
        job,
        sec_request_limiter,
        f"{label}",
        max(1, progress_record_interval // 2),
        progress_interval,
        progress,
    )
    submission_rows = [merge_submission_timestamp(item, timestamps.get(item.accession_number)) for item in parsed]
    progress.log(f"{label} write: submissions={len(submission_rows):,} documents={len(documents):,} headers={len(timestamps):,}")
    write_rows(submissions_path, submission_rows)
    write_rows(documents_path, [asdict(item) for item in documents])
    write_rows(headers_path, [asdict(item) for item in timestamps.values()])
    progress.log(f"{label} write: normalized files written to {normalized_day_dir}")
    parse_seconds = round(time.perf_counter() - started, 3)
    hdd_copy_status, hdd_copy_seconds = copy_future.result()
    progress.log(f"{label} copy: hdd_status={hdd_copy_status} copy_seconds={hdd_copy_seconds:.1f}")
    cleanup_status = cleanup_archives(temp_archive_path, hdd_archive_path, hdd_copy_status, args.delete_archive_after_parse)
    progress.log(f"{label} cleanup: {cleanup_status}")

    result = PipelineDayResult(
        archive_date=job.archive_date,
        archive_url=job.archive_url,
        hdd_archive_path=str(hdd_archive_path),
        temp_archive_path=str(temp_archive_path),
        normalized_day_dir=str(normalized_day_dir),
        archive_bytes=downloaded.archive_bytes,
        archive_sha256=downloaded.archive_sha256,
        submissions=len(parsed),
        documents=len(documents),
        header_success=sum(1 for item in timestamps.values() if item.fetch_status == "ok"),
        header_unavailable=sum(1 for item in timestamps.values() if item.fetch_status == "unavailable_404"),
        header_failed=sum(1 for item in timestamps.values() if item.fetch_status == "failed"),
        download_seconds=downloaded.download_seconds,
        hdd_copy_seconds=hdd_copy_seconds,
        parse_seconds=parse_seconds,
        wall_seconds=round(downloaded.download_seconds + max(parse_seconds, hdd_copy_seconds), 3),
        archive_source=downloaded.archive_source,
        hdd_copy_status=hdd_copy_status,
        cleanup_status=cleanup_status,
        status="ok",
    )
    append_jsonl(manifest_path, {"type": "day_manifest", "result": asdict(result)})
    return result


def failed_result(job: DayJob, exc: Exception, downloaded: DownloadedArchive | None = None) -> PipelineDayResult:
    return PipelineDayResult(
        archive_date=job.archive_date,
        archive_url=job.archive_url,
        hdd_archive_path=downloaded.hdd_archive_path if downloaded else job.archive_path,
        temp_archive_path=downloaded.temp_archive_path if downloaded else "",
        normalized_day_dir="",
        archive_bytes=downloaded.archive_bytes if downloaded else 0,
        archive_sha256=downloaded.archive_sha256 if downloaded else "",
        submissions=0,
        documents=0,
        header_success=0,
        header_unavailable=0,
        header_failed=0,
        download_seconds=downloaded.download_seconds if downloaded else 0.0,
        hdd_copy_seconds=0.0,
        parse_seconds=0.0,
        wall_seconds=0.0,
        archive_source=downloaded.archive_source if downloaded else "",
        hdd_copy_status="not_started",
        cleanup_status="not_started",
        status="failed",
        error=f"{exc!r}\n{traceback.format_exc()}",
    )


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n")


def print_header(config: dict[str, Any], progress: ProgressDisplay) -> None:
    progress.log("SEC EDGAR bounded historical pipeline")
    for key in [
        "run_id",
        "start_date",
        "end_date",
        "job_count",
        "download_concurrency",
        "archive_copy_concurrency",
        "header_concurrency",
        "sec_request_min_interval_seconds",
        "progress_interval_seconds",
        "progress_file_interval_mib",
        "progress_record_interval",
        "download_progress_bars",
        "artifact_root",
        "temp_root",
        "normalized_root",
        "output_root",
        "limit_days",
        "limit_files_per_day",
        "persist_nc_files",
        "delete_archive_after_parse",
        "no_header_fetch",
    ]:
        progress.log(f"{key}={config.get(key)}")
    progress.log(f"secret_status={config.get('secret_status')}")
    progress.log(f"loaded_env_files={config.get('loaded_env_files')}")


def print_progress(
    total_days: int,
    totals: dict[str, int],
    started: float,
    last_result: PipelineDayResult,
    progress: ProgressDisplay,
) -> None:
    elapsed = max(0.001, time.perf_counter() - started)
    processed = totals["parsed_days"] + totals["failed_days"]
    gib = totals["archive_bytes"] / (1024**3)
    progress.log(
        "progress "
        f"{processed:,}/{total_days:,} days "
        f"staged={totals['staged_days']:,} parsed={totals['parsed_days']:,} failed={totals['failed_days']:,} "
        f"archives={gib:.2f} GiB submissions={totals['submissions']:,} documents={totals['documents']:,} "
        f"headers_ok={totals['header_success']:,} headers_unavailable={totals['header_unavailable']:,} "
        f"headers_failed={totals['header_failed']:,} "
        f"hdd_archived={totals['hdd_copied_days']:,} ssd_cleaned={totals['ssd_cleaned_days']:,} "
        f"last={last_result.archive_date}:{last_result.status} "
        f"last_download={last_result.download_seconds:.1f}s last_copy={last_result.hdd_copy_seconds:.1f}s "
        f"last_parse={last_result.parse_seconds:.1f}s "
        f"elapsed={elapsed:.1f}s",
    )


def make_download_bar(
    enabled: bool,
    archive_date: str,
    attempt: int,
    max_attempts: int,
    total_bytes: int,
    position: int,
) -> Any:
    if not enabled:
        return None
    try:
        from tqdm.auto import tqdm
    except ImportError:
        print(f"[{archive_date}] download: tqdm is not installed; using text progress", flush=True)
        return None
    return tqdm(
        total=total_bytes or None,
        desc=f"{archive_date} download {attempt}/{max_attempts}",
        unit="B",
        unit_scale=True,
        unit_divisor=1024,
        dynamic_ncols=True,
        leave=True,
        position=max(0, position),
    )


def parse_bool_env(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def format_bytes(value: int | float) -> str:
    size = float(value)
    for unit in ["B", "KiB", "MiB", "GiB", "TiB"]:
        if abs(size) < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TiB"


if __name__ == "__main__":
    main()
