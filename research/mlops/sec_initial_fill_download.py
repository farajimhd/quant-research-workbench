from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import sys
import threading
import time
import traceback
from collections import deque
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib import error, request


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402
from research.mlops.sec_historical_feed_download import (  # noqa: E402
    RateLimiter,
    RETRY_HTTP_CODES,
    discover_available_archive_days,
    parse_date,
    parse_retry_after,
    quarter_name,
    sec_user_agent,
    sha256_file,
)


DEFAULT_ARTIFACT_ROOT_WIN = Path("G:/market-data/sec_core")
DEFAULT_OUTPUT_ROOT_WIN = Path("G:/market-data/prepared/sec_core")
SEC_ARCHIVES_BASE_URL = "https://www.sec.gov/Archives/edgar"
SEC_FILES_BASE_URL = "https://www.sec.gov/files"
SEC_BULK_BASE_URL = "https://www.sec.gov/Archives/edgar/daily-index"
CHUNK_SIZE_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class SourceSpec:
    source_kind: str
    source_url: str
    artifact_path: str
    source_date: str = ""


@dataclass(frozen=True, slots=True)
class DownloadResult:
    source_file_id: str
    source_kind: str
    source_url: str
    artifact_path: str
    source_date: str
    downloaded_at_utc: str
    byte_size: int
    sha256: str
    etag: str
    last_modified: str
    elapsed_seconds: float
    status: str
    error: str = ""


class ProgressDisplay:
    def __init__(
        self,
        total_sources: int,
        worker_count: int,
        mode: str,
        log_lines: int,
        refresh_per_second: float,
        screen: bool,
    ) -> None:
        self.total_sources = max(0, total_sources)
        self.worker_count = max(1, worker_count)
        self.mode = mode
        self.log_lines = max(5, log_lines)
        self.refresh_per_second = max(0.5, refresh_per_second)
        self.screen = screen
        self._lock = threading.RLock()
        self._logs: deque[str] = deque(maxlen=self.log_lines)
        self._rows: list[dict[str, Any]] = [self._empty_row(index) for index in range(self.worker_count)]
        self._job_slot: dict[str, int] = {}
        self._completed = 0
        self._downloaded = 0
        self._reused = 0
        self._failed = 0
        self._completed_bytes = 0
        self._started_at = time.perf_counter()
        self._rich = False
        self._fallback_reason = ""
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
                self._fallback_reason = "Rich is not installed; using text progress."
                if self.mode == "rich":
                    raise
            else:
                self._rich = True
                self._layout_cls = Layout
                self._live_cls = Live
                self._panel_cls = Panel
                self._table_cls = Table
                self._text_cls = Text
                self._layout = Layout(name="root")
                worker_panel_size = max(8, self.worker_count + 4)
                self._layout.split_column(
                    Layout(self._overall_panel(), name="overall", size=7),
                    Layout(self._worker_panel(), name="workers", size=worker_panel_size),
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
        if not self._rich and self._fallback_reason:
            print(self._fallback_reason, flush=True)
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._live is not None:
            self._live.stop()

    @property
    def rich_active(self) -> bool:
        return self._rich

    def start(self, job_id: str, spec: SourceSpec) -> None:
        with self._lock:
            slot = self._first_free_slot()
            self._job_slot[job_id] = slot
            self._rows[slot] = {
                "slot": slot,
                "job_id": job_id,
                "source": source_label(spec),
                "kind": spec.source_kind,
                "status": "starting",
                "downloaded": 0,
                "total": 0,
                "attempt": 1,
                "started_at": time.perf_counter(),
                "updated_at": time.perf_counter(),
                "message": "queued",
            }
            self._refresh()

    def update(
        self,
        job_id: str,
        *,
        status: str,
        downloaded: int | None = None,
        total: int | None = None,
        attempt: int | None = None,
        message: str = "",
    ) -> None:
        with self._lock:
            row = self._row_for_job(job_id)
            row["status"] = status
            if downloaded is not None:
                row["downloaded"] = downloaded
            if total is not None:
                row["total"] = total
            if attempt is not None:
                row["attempt"] = attempt
            if message:
                row["message"] = message
            row["updated_at"] = time.perf_counter()
            self._refresh()

    def finish(self, job_id: str, result: DownloadResult) -> None:
        with self._lock:
            self._completed += 1
            if result.status == "failed":
                self._failed += 1
            elif result.status == "reused":
                self._reused += 1
            elif result.status == "downloaded":
                self._downloaded += 1
            self._completed_bytes += max(0, result.byte_size)
            slot = self._job_slot.pop(job_id, None)
            if slot is not None:
                self._rows[slot] = self._empty_row(slot)
            self.log(
                f"[{self._completed}/{self.total_sources}] {result.status} {result.source_kind} "
                f"bytes={result.byte_size} elapsed={result.elapsed_seconds:.1f}s path={result.artifact_path}",
                refresh=False,
            )
            self._refresh()

    def log(self, message: str, *, refresh: bool = True) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"{timestamp} {message}"
        with self._lock:
            if self._rich:
                self._logs.append(line)
                if refresh:
                    self._refresh()
            else:
                print(line, flush=True)

    def _first_free_slot(self) -> int:
        for index, row in enumerate(self._rows):
            if not row.get("job_id"):
                return index
        return 0

    def _row_for_job(self, job_id: str) -> dict[str, Any]:
        slot = self._job_slot.get(job_id)
        if slot is None:
            slot = self._first_free_slot()
            self._job_slot[job_id] = slot
            self._rows[slot]["job_id"] = job_id
        return self._rows[slot]

    def _empty_row(self, slot: int) -> dict[str, Any]:
        return {
            "slot": slot,
            "job_id": "",
            "source": "-",
            "kind": "",
            "status": "idle",
            "downloaded": 0,
            "total": 0,
            "attempt": 0,
            "started_at": 0.0,
            "updated_at": 0.0,
            "message": "",
        }

    def _refresh(self) -> None:
        if not self._rich or self._layout is None:
            return
        self._layout["overall"].update(self._overall_panel())
        self._layout["workers"].update(self._worker_panel())
        self._layout["logs"].update(self._log_panel())

    def _overall_panel(self) -> Any:
        active = len(self._job_slot)
        pct = safe_pct(self._completed, self.total_sources)
        elapsed = format_seconds(time.perf_counter() - self._started_at)
        active_bytes = sum(int(row.get("downloaded") or 0) for row in self._rows if row.get("job_id"))
        lines = [
            f"Sources {self.total_sources:<6} Done {self._completed:<6} ({pct:5.1f}%)  Active {active:<4} Elapsed {elapsed}",
            f"Downloaded {self._downloaded:<5} Reused {self._reused:<5} Failed {self._failed:<5} Bytes {format_bytes(self._completed_bytes + active_bytes)}",
            f"Overall  {self._bar(self._completed, self.total_sources)}",
        ]
        return self._panel_cls("\n".join(lines), title="Overall SEC Initial Fill Download", border_style="cyan")

    def _worker_panel(self) -> Any:
        lines = [
            f"{'W':<2} {'Source':<16} {'State':<10} {'Progress':<21} {'Rate':>8} {'Try':>3} {'Time':>5}",
            f"{'-' * 2} {'-' * 16} {'-' * 10} {'-' * 21} {'-' * 8} {'-' * 3} {'-' * 5}",
        ]
        for row in self._rows:
            downloaded = int(row.get("downloaded") or 0)
            total = int(row.get("total") or 0)
            started_at = float(row.get("started_at") or 0.0)
            elapsed = time.perf_counter() - started_at if started_at else 0.0
            rate = downloaded / elapsed if elapsed > 0 and row.get("job_id") else 0.0
            source = truncate_middle(str(row.get("source") or "-"), 16)
            status = truncate_right(str(row.get("status") or "idle"), 10)
            progress = self._bar(downloaded, total, width=14) if row.get("job_id") else ""
            lines.append(
                f"{int(row['slot']) + 1:<2} {source:<16} {status:<10} {progress:<21} "
                f"{(format_bytes(rate) + '/s') if rate else '':>8} {str(row.get('attempt') or ''):>3} "
                f"{(format_seconds(elapsed) if row.get('job_id') else ''):>5}"
            )
        return self._panel_cls("\n".join(lines), title="Download Workers", border_style="green")

    def _log_panel(self) -> Any:
        text = "\n".join(self._logs) if self._logs else "No messages yet."
        return self._panel_cls(text, title="Messages", border_style="dim")

    def _bar(self, value: int | float, total: int | float, *, width: int = 24) -> str:
        if total <= 0:
            return f"{format_bytes(value)}"
        ratio = max(0.0, min(1.0, float(value) / float(total)))
        filled = int(round(width * ratio))
        return f"{'#' * filled}{'.' * (width - filled)} {ratio * 100:5.1f}%"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download SEC source artifacts needed for a sec_core initial fill. "
            "This script only downloads raw inputs and writes a manifest; it does not parse or insert into ClickHouse."
        )
    )
    parser.add_argument(
        "--artifact-root-win",
        default=os.environ.get(
            "SEC_CORE_ARTIFACT_ROOT_WIN",
            os.environ.get("SEC_HISTORICAL_ARTIFACT_ROOT_WIN", str(DEFAULT_ARTIFACT_ROOT_WIN)),
        ),
        help="Root where raw SEC source artifacts are retained.",
    )
    parser.add_argument(
        "--output-root-win",
        default=os.environ.get(
            "SEC_CORE_OUTPUT_ROOT_WIN",
            os.environ.get("SEC_HISTORICAL_OUTPUT_ROOT_WIN", str(DEFAULT_OUTPUT_ROOT_WIN)),
        ),
        help="Root where manifests and run reports are written.",
    )
    parser.add_argument(
        "--sources",
        default="submissions,companyfacts,company_tickers,company_tickers_exchange,company_tickers_mf",
        help=(
            "Comma-separated bulk sources to download. Use 'all' for all bulk sources or 'none' "
            "when only daily archives are desired."
        ),
    )
    parser.add_argument("--include-daily-archives", action="store_true", help="Also download daily .nc.tar.gz feed archives.")
    parser.add_argument("--start-date", help="Inclusive daily archive date, YYYY-MM-DD. Required with --include-daily-archives.")
    parser.add_argument("--end-date", help="Exclusive daily archive date, YYYY-MM-DD. Required with --include-daily-archives.")
    parser.add_argument("--limit-days", type=int, default=0, help="Optional daily archive cap for smoke tests.")
    parser.add_argument("--download-concurrency", type=int, default=int(os.environ.get("SEC_INITIAL_DOWNLOAD_CONCURRENCY", "2")))
    parser.add_argument(
        "--sec-request-min-interval-seconds",
        type=float,
        default=float(os.environ.get("SEC_REQUEST_MIN_INTERVAL_SECONDS", "0.11")),
        help="Global minimum delay between SEC requests. 0.11 stays below SEC's 10 requests/second guidance.",
    )
    parser.add_argument("--request-timeout-seconds", type=float, default=float(os.environ.get("SEC_REQUEST_TIMEOUT_SECONDS", "120")))
    parser.add_argument("--max-retries", type=int, default=int(os.environ.get("SEC_MAX_RETRIES", "4")))
    parser.add_argument("--retry-base-seconds", type=float, default=float(os.environ.get("SEC_RETRY_BASE_SECONDS", "1.5")))
    parser.add_argument("--progress-interval-seconds", type=float, default=20.0)
    parser.add_argument("--progress-layout", choices=["auto", "rich", "text"], default=os.environ.get("SEC_INITIAL_PROGRESS_LAYOUT", "auto"))
    parser.add_argument("--progress-log-lines", type=int, default=18)
    parser.add_argument("--progress-refresh-per-second", type=float, default=4.0)
    parser.add_argument("--progress-screen", dest="progress_screen", action="store_true", default=True)
    parser.add_argument("--no-progress-screen", dest="progress_screen", action="store_false")
    parser.add_argument("--force", action="store_true", help="Redownload even when the artifact already exists.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned downloads and write a planned manifest only.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    loaded_env_files = load_env_files(discover_env_files(REPO_ROOT))
    validate_args(args)

    artifact_root = Path(args.artifact_root_win)
    output_root = Path(args.output_root_win)
    output_root.mkdir(parents=True, exist_ok=True)
    artifact_root.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    manifest_path = output_root / f"sec_initial_fill_sources_{run_id}.jsonl"
    summary_path = output_root / f"sec_initial_fill_summary_{run_id}.json"
    user_agent = sec_user_agent()
    limiter = RateLimiter(max(0.0, args.sec_request_min_interval_seconds))

    specs = build_specs(args, artifact_root, user_agent, limiter)
    print_header(
        {
            "run_id": run_id,
            "artifact_root": str(artifact_root),
            "output_root": str(output_root),
            "sources": args.sources,
            "include_daily_archives": args.include_daily_archives,
            "start_date": args.start_date or "",
            "end_date": args.end_date or "",
            "planned_downloads": len(specs),
            "download_concurrency": max(1, args.download_concurrency),
            "dry_run": args.dry_run,
            "loaded_env_files": [str(path) for path in loaded_env_files],
            "secret_status": secret_status(["SEC_USER_AGENT", "SEC_EDGAR_USER_AGENT", "NEWS_SEC_USER_AGENT"]),
        }
    )

    started = time.perf_counter()
    if args.dry_run:
        results = [planned_result(spec) for spec in specs]
        write_manifest(manifest_path, results)
    else:
        results = download_all(specs, args, user_agent, limiter)
        write_manifest(manifest_path, results)

    summary = build_summary(run_id, results, time.perf_counter() - started, manifest_path)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print("manifest_path=" + str(manifest_path), flush=True)
    print("summary_path=" + str(summary_path), flush=True)
    print("summary=" + json.dumps(summary, sort_keys=True), flush=True)
    if any(row.status == "failed" for row in results):
        raise SystemExit(1)


def validate_args(args: argparse.Namespace) -> None:
    if args.include_daily_archives:
        if not args.start_date or not args.end_date:
            raise SystemExit("--start-date and --end-date are required with --include-daily-archives")
        start = parse_date(args.start_date)
        end = parse_date(args.end_date)
        if end <= start:
            raise SystemExit("--end-date must be later than --start-date")
    if args.download_concurrency < 1:
        raise SystemExit("--download-concurrency must be >= 1")


def build_specs(args: argparse.Namespace, artifact_root: Path, user_agent: str, limiter: RateLimiter) -> list[SourceSpec]:
    specs: list[SourceSpec] = []
    for source_name in parse_sources(args.sources):
        specs.append(bulk_source_spec(source_name, artifact_root))
    if args.include_daily_archives:
        days = discover_available_archive_days(
            parse_date(args.start_date),
            parse_date(args.end_date),
            user_agent,
            max(1.0, args.request_timeout_seconds),
            max(0, args.max_retries),
            max(0.1, args.retry_base_seconds),
            limiter,
        )
        if args.limit_days:
            days = days[: max(0, args.limit_days)]
        specs.extend(daily_archive_spec(day, artifact_root) for day in days)
    return specs


def parse_sources(raw: str) -> list[str]:
    normalized = [item.strip().lower() for item in raw.split(",") if item.strip()]
    if not normalized or normalized == ["none"]:
        return []
    if "all" in normalized:
        return ["submissions", "companyfacts", "company_tickers", "company_tickers_exchange", "company_tickers_mf"]
    allowed = {"submissions", "companyfacts", "company_tickers", "company_tickers_exchange", "company_tickers_mf"}
    invalid = sorted(set(normalized) - allowed)
    if invalid:
        raise SystemExit(f"Unknown --sources values: {', '.join(invalid)}")
    return normalized


def bulk_source_spec(source_name: str, artifact_root: Path) -> SourceSpec:
    specs = {
        "submissions": SourceSpec(
            "submissions_bulk",
            f"{SEC_BULK_BASE_URL}/bulkdata/submissions.zip",
            str(artifact_root / "bulk" / "submissions" / "submissions.zip"),
        ),
        "companyfacts": SourceSpec(
            "companyfacts_bulk",
            f"{SEC_BULK_BASE_URL}/xbrl/companyfacts.zip",
            str(artifact_root / "bulk" / "companyfacts" / "companyfacts.zip"),
        ),
        "company_tickers": SourceSpec(
            "company_tickers",
            f"{SEC_FILES_BASE_URL}/company_tickers.json",
            str(artifact_root / "bulk" / "mappings" / "company_tickers.json"),
        ),
        "company_tickers_exchange": SourceSpec(
            "company_tickers_exchange",
            f"{SEC_FILES_BASE_URL}/company_tickers_exchange.json",
            str(artifact_root / "bulk" / "mappings" / "company_tickers_exchange.json"),
        ),
        "company_tickers_mf": SourceSpec(
            "company_tickers_mf",
            f"{SEC_FILES_BASE_URL}/company_tickers_mf.json",
            str(artifact_root / "bulk" / "mappings" / "company_tickers_mf.json"),
        ),
    }
    return specs[source_name]


def daily_archive_spec(archive_day: date, artifact_root: Path) -> SourceSpec:
    day_text = archive_day.strftime("%Y%m%d")
    quarter = quarter_name(archive_day)
    return SourceSpec(
        "daily_feed_archive",
        f"{SEC_ARCHIVES_BASE_URL}/Feed/{archive_day.year}/{quarter}/{day_text}.nc.tar.gz",
        str(artifact_root / "daily_archives" / str(archive_day.year) / quarter / f"{day_text}.nc.tar.gz"),
        archive_day.isoformat(),
    )


def download_all(specs: list[SourceSpec], args: argparse.Namespace, user_agent: str, limiter: RateLimiter) -> list[DownloadResult]:
    if not specs:
        return []
    results: list[DownloadResult] = []
    started = time.perf_counter()
    worker_count = max(1, args.download_concurrency)
    with ProgressDisplay(
        total_sources=len(specs),
        worker_count=worker_count,
        mode=args.progress_layout,
        log_lines=args.progress_log_lines,
        refresh_per_second=args.progress_refresh_per_second,
        screen=args.progress_screen,
    ) as progress:
        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_to_job = {}
            for spec in specs:
                job_id = source_job_id(spec)
                future = executor.submit(
                    download_source,
                    spec,
                    job_id,
                    user_agent,
                    limiter,
                    max(1.0, args.request_timeout_seconds),
                    max(0, args.max_retries),
                    max(0.1, args.retry_base_seconds),
                    max(1.0, args.progress_interval_seconds),
                    bool(args.force),
                    progress,
                )
                future_to_job[future] = job_id
            for future in concurrent.futures.as_completed(future_to_job):
                result = future.result()
                results.append(result)
                progress.finish(future_to_job[future], result)
        progress.log(f"download_wall_seconds={time.perf_counter() - started:.1f}")
    return sorted(results, key=lambda item: (item.source_kind, item.source_date, item.artifact_path))


def download_source(
    spec: SourceSpec,
    job_id: str,
    user_agent: str,
    limiter: RateLimiter,
    timeout_seconds: float,
    max_retries: int,
    retry_base_seconds: float,
    progress_interval_seconds: float,
    force: bool,
    progress: ProgressDisplay,
) -> DownloadResult:
    target = Path(spec.artifact_path)
    started = time.perf_counter()
    progress.start(job_id, spec)
    if target.exists() and not force:
        size = target.stat().st_size
        progress.update(job_id, status="hashing", downloaded=size, total=size, message="reusing existing file")
        sha256 = sha256_file(target)
        return DownloadResult(
            source_file_id=source_file_id(spec, target, sha256),
            source_kind=spec.source_kind,
            source_url=spec.source_url,
            artifact_path=str(target),
            source_date=spec.source_date,
            downloaded_at_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            byte_size=size,
            sha256=sha256,
            etag="",
            last_modified="",
            elapsed_seconds=round(time.perf_counter() - started, 3),
            status="reused",
        )
    try:
        etag, last_modified = stream_download(
            spec.source_url,
            target,
            job_id,
            user_agent,
            limiter,
            timeout_seconds,
            max_retries,
            retry_base_seconds,
            progress_interval_seconds,
            progress,
        )
        size = target.stat().st_size
        progress.update(job_id, status="hashing", downloaded=size, total=size, message="calculating sha256")
        sha256 = sha256_file(target)
        return DownloadResult(
            source_file_id=source_file_id(spec, target, sha256),
            source_kind=spec.source_kind,
            source_url=spec.source_url,
            artifact_path=str(target),
            source_date=spec.source_date,
            downloaded_at_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            byte_size=size,
            sha256=sha256,
            etag=etag,
            last_modified=last_modified,
            elapsed_seconds=round(time.perf_counter() - started, 3),
            status="downloaded",
        )
    except Exception as exc:  # noqa: BLE001
        progress.update(job_id, status="failed", message=repr(exc))
        return DownloadResult(
            source_file_id=source_file_id_from_text(spec),
            source_kind=spec.source_kind,
            source_url=spec.source_url,
            artifact_path=str(target),
            source_date=spec.source_date,
            downloaded_at_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            byte_size=target.stat().st_size if target.exists() else 0,
            sha256=sha256_file(target) if target.exists() else "",
            etag="",
            last_modified="",
            elapsed_seconds=round(time.perf_counter() - started, 3),
            status="failed",
            error=f"{exc!r}\n{traceback.format_exc()}",
        )


def stream_download(
    url: str,
    target: Path,
    job_id: str,
    user_agent: str,
    limiter: RateLimiter,
    timeout_seconds: float,
    max_retries: int,
    retry_base_seconds: float,
    progress_interval_seconds: float,
    progress: ProgressDisplay,
) -> tuple[str, str]:
    target.parent.mkdir(parents=True, exist_ok=True)
    part_path = target.with_suffix(target.suffix + ".part")
    headers = {
        "User-Agent": user_agent,
        "Accept-Encoding": "identity",
        "Host": "www.sec.gov",
    }
    last_error = ""
    for attempt in range(max_retries + 1):
        part_path.unlink(missing_ok=True)
        progress.update(job_id, status="waiting", attempt=attempt + 1, message="waiting for SEC rate limiter")
        limiter.wait()
        req = request.Request(url, headers=headers)
        bytes_written = 0
        last_progress_at = time.perf_counter()
        try:
            progress.update(job_id, status="requesting", attempt=attempt + 1, message="opening connection")
            with request.urlopen(req, timeout=timeout_seconds) as response:
                etag = response.headers.get("ETag", "")
                last_modified = response.headers.get("Last-Modified", "")
                length = parse_int(response.headers.get("Content-Length"))
                progress.update(
                    job_id,
                    status="downloading",
                    downloaded=0,
                    total=length,
                    attempt=attempt + 1,
                    message=response.headers.get("Content-Type", ""),
                )
                with part_path.open("wb") as handle:
                    while True:
                        chunk = response.read(CHUNK_SIZE_BYTES)
                        if not chunk:
                            break
                        handle.write(chunk)
                        bytes_written += len(chunk)
                        now = time.perf_counter()
                        if now - last_progress_at >= progress_interval_seconds:
                            progress.update(
                                job_id,
                                status="downloading",
                                downloaded=bytes_written,
                                total=length,
                                attempt=attempt + 1,
                                message=target.name,
                            )
                            last_progress_at = now
                progress.update(job_id, status="finalizing", downloaded=bytes_written, total=length, message="moving part file")
            part_path.replace(target)
            return etag, last_modified
        except error.HTTPError as exc:
            last_error = f"HTTP {exc.code}: {exc.reason}"
            part_path.unlink(missing_ok=True)
            if exc.code not in RETRY_HTTP_CODES or attempt >= max_retries:
                raise RuntimeError(last_error) from exc
            retry_after = parse_retry_after(exc.headers.get("Retry-After"))
            progress.log(f"retry {attempt + 1}/{max_retries} {target.name}: {last_error}")
            time.sleep(retry_after if retry_after is not None else retry_base_seconds * (2**attempt))
        except Exception as exc:  # noqa: BLE001
            last_error = repr(exc)
            part_path.unlink(missing_ok=True)
            if attempt >= max_retries:
                raise RuntimeError(last_error) from exc
            progress.log(f"retry {attempt + 1}/{max_retries} {target.name}: {last_error}")
            time.sleep(retry_base_seconds * (2**attempt))
    raise RuntimeError(last_error or "request failed")


def source_job_id(spec: SourceSpec) -> str:
    return hashlib.sha256(f"{spec.source_kind}|{spec.source_date}|{spec.source_url}|{spec.artifact_path}".encode("utf-8")).hexdigest()


def source_label(spec: SourceSpec) -> str:
    if spec.source_date:
        return f"{spec.source_date} {Path(spec.artifact_path).name}"
    return Path(spec.artifact_path).name


def safe_pct(value: int, total: int) -> float:
    if total <= 0:
        return 100.0
    return 100.0 * value / total


def format_bytes(value: int | float) -> str:
    number = float(value)
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    for unit in units:
        if abs(number) < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{number:.0f} {unit}"
            return f"{number:.1f} {unit}"
        number /= 1024.0
    return f"{number:.1f} TiB"


def truncate_middle(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    if width <= 3:
        return value[:width]
    keep_left = (width - 3) // 2
    keep_right = width - 3 - keep_left
    return value[:keep_left] + "..." + value[-keep_right:]


def truncate_right(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    if width <= 3:
        return value[:width]
    return value[: width - 3] + "..."


def format_seconds(value: float) -> str:
    seconds = max(0, int(value))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def parse_int(value: str | None) -> int:
    if not value:
        return 0
    try:
        return int(value)
    except ValueError:
        return 0


def planned_result(spec: SourceSpec) -> DownloadResult:
    target = Path(spec.artifact_path)
    return DownloadResult(
        source_file_id=source_file_id_from_text(spec),
        source_kind=spec.source_kind,
        source_url=spec.source_url,
        artifact_path=str(target),
        source_date=spec.source_date,
        downloaded_at_utc="",
        byte_size=target.stat().st_size if target.exists() else 0,
        sha256=sha256_file(target) if target.exists() else "",
        etag="",
        last_modified="",
        elapsed_seconds=0.0,
        status="planned_exists" if target.exists() else "planned",
    )


def source_file_id(spec: SourceSpec, target: Path, sha256: str) -> str:
    text = f"{spec.source_kind}|{spec.source_url}|{target}|{sha256}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def source_file_id_from_text(spec: SourceSpec) -> str:
    text = f"{spec.source_kind}|{spec.source_url}|{spec.artifact_path}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_manifest(path: Path, rows: list[DownloadResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(asdict(row), separators=(",", ":"), ensure_ascii=False) + "\n")


def build_summary(run_id: str, rows: list[DownloadResult], wall_seconds: float, manifest_path: Path) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    bytes_by_kind: dict[str, int] = {}
    for row in rows:
        status_counts[row.status] = status_counts.get(row.status, 0) + 1
        bytes_by_kind[row.source_kind] = bytes_by_kind.get(row.source_kind, 0) + row.byte_size
    return {
        "run_id": run_id,
        "status": "failed" if status_counts.get("failed") else "ok",
        "sources": len(rows),
        "status_counts": status_counts,
        "bytes_total": sum(row.byte_size for row in rows),
        "bytes_by_kind": bytes_by_kind,
        "manifest_path": str(manifest_path),
        "wall_seconds": round(wall_seconds, 3),
    }


def print_header(config: dict[str, Any]) -> None:
    print("=" * 96, flush=True)
    print("SEC initial-fill source downloader", flush=True)
    for key, value in config.items():
        print(f"{key}={json.dumps(value, sort_keys=True)}", flush=True)
    print("=" * 96, flush=True)


if __name__ == "__main__":
    main()
