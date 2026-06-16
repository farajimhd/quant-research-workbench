from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import multiprocessing as mp
import os
import queue as queue_module
import re
import signal
import ssl
import sys
import time
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib import error, parse, request


REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists() and (parent / "pipelines").exists())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402


DEFAULT_START_DATE = "2022-01-01"
DEFAULT_END_DATE = "2022-12-31"
DEFAULT_PROCESSES = 8
DEFAULT_CHUNK_BYTES = 8 * 1024 * 1024
DEFAULT_AWS_REGION = "us-east-1"
DEFAULT_AWS_SERVICE = "s3"
DEFAULT_DISCOVERY = "remote"
DEFAULT_PROGRESS_LAYOUT = "auto"
DEFAULT_PROGRESS_LOG_LINES = 24
DEFAULT_PROGRESS_REFRESH_PER_SECOND = 4
DEFAULT_PROGRESS_INTERVAL_SECONDS = 0.5
DEFAULT_PROGRESS_PANEL_ROWS = 0
DEFAULT_PROGRESS_WORKER_COLUMNS = 0
KIND_PREFIXES = {
    "quotes": "us_stocks_sip/quotes_v1",
    "trades": "us_stocks_sip/trades_v1",
}
REMOTE_FILE_RE = re.compile(r"(?P<date>\d{4}-\d{2}-\d{2})\.csv\.gz$")
ENV_KEYS = [
    "MASSIVE_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "S3_ENDPOINT_URL",
    "BUCKET",
    "FLATFILES_ROOT",
]


@dataclass(frozen=True, slots=True)
class DownloadJob:
    kind: str
    session_date: str
    key: str
    destination: str
    remote_size: int = 0


@dataclass(frozen=True, slots=True)
class RemoteObject:
    key: str
    size: int


@dataclass(frozen=True, slots=True)
class DownloadResult:
    worker_id: int
    kind: str
    session_date: str
    key: str
    destination: str
    status: str
    bytes_expected: int = 0
    bytes_written: int = 0
    wall_seconds: float = 0.0
    exception: str = ""


@dataclass(frozen=True, slots=True)
class DownloadConfig:
    endpoint_url: str
    bucket: str
    access_key: str
    secret_key: str
    region: str
    service: str
    timeout_seconds: float
    chunk_bytes: int
    verify_tls: bool
    overwrite_incomplete: bool
    dry_run: bool
    progress_interval_seconds: float


class DownloadProgressDisplay:
    def __init__(
        self,
        *,
        mode: str,
        worker_slots: int,
        log_lines: int,
        screen: bool,
        refresh_per_second: float,
        panel_rows: int,
        worker_columns: int,
        total_jobs: int,
        total_bytes: int,
    ) -> None:
        self.mode = mode
        self.worker_slots = max(1, int(worker_slots))
        self.log_lines = max(5, int(log_lines))
        self.screen = bool(screen)
        self.refresh_per_second = max(1.0, float(refresh_per_second))
        self.panel_rows = max(0, int(panel_rows))
        self.worker_columns = max(0, int(worker_columns))
        self.total_jobs = max(0, int(total_jobs))
        self.total_bytes = max(0, int(total_bytes))
        self._started_at = time.time()
        self._completed_jobs = 0
        self._completed_bytes = 0
        self._status_counts: dict[str, int] = {}
        self._logs: deque[str] = deque(maxlen=self.log_lines)
        self._rows: dict[int, dict[str, Any]] = {slot: self._empty_row(slot) for slot in range(self.worker_slots)}
        self._rich = False
        self._fallback_reason = ""
        self._layout: Any = None
        self._live: Any = None

    def __enter__(self) -> "DownloadProgressDisplay":
        if self.mode in {"auto", "rich"}:
            try:
                from rich.layout import Layout
                from rich.live import Live
                from rich.panel import Panel
                from rich.table import Table
                from rich.text import Text
                from rich.console import Group
            except ImportError:
                self._fallback_reason = "Rich is not installed; using text progress"
                if self.mode == "rich":
                    raise
            else:
                self._rich = True
                self._layout_cls = Layout
                self._live_cls = Live
                self._panel_cls = Panel
                self._table_cls = Table
                self._text_cls = Text
                self._group_cls = Group
                progress_rows = self.panel_rows or self._default_progress_rows()
                self._layout = Layout(name="root")
                self._layout.split_column(
                    Layout(self._progress_panel(), name="progress", size=progress_rows),
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
            self._live.stop()

    @property
    def rich_active(self) -> bool:
        return self._rich

    @property
    def fallback_reason(self) -> str:
        return self._fallback_reason

    def log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"{timestamp} {message}"
        if self._rich:
            self._logs.append(line)
            self._refresh()
        else:
            print(line, flush=True)

    def handle_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "progress":
            self._update_progress(event)
        elif event_type == "result":
            self._update_result(event)
        elif event_type == "log":
            self.log(str(event.get("message", "")))

    def _update_progress(self, event: dict[str, Any]) -> None:
        worker_id = int(event.get("worker_id", 0))
        row = self._rows.setdefault(worker_id, self._empty_row(worker_id))
        row["job"] = str(event.get("job_label") or "")
        row["status"] = str(event.get("status") or "running")
        row["expected"] = int(event.get("bytes_expected") or 0)
        row["written"] = int(event.get("bytes_written") or 0)
        row["started_at"] = float(event.get("started_at") or row.get("started_at") or time.time())
        row["updated_at"] = time.time()
        if self._rich:
            self._refresh()
        elif row["status"] in {"downloaded", "failed_size_mismatch"}:
            self.log(f"worker {worker_id:02d} {row['status']} {row['job']} {self._format_bytes(row['written'])}")

    def _update_result(self, event: dict[str, Any]) -> None:
        worker_id = int(event.get("worker_id", 0))
        row = self._rows.setdefault(worker_id, self._empty_row(worker_id))
        row["job"] = str(event.get("job_label") or row.get("job") or "")
        row["status"] = str(event.get("status") or "done")
        row["expected"] = int(event.get("bytes_expected") or row.get("expected") or 0)
        row["written"] = int(event.get("bytes_written") or row.get("written") or 0)
        row["updated_at"] = time.time()
        self._completed_jobs += 1
        self._status_counts[row["status"]] = self._status_counts.get(row["status"], 0) + 1
        self._completed_bytes += self._result_completed_bytes(row)
        if row["status"] not in {"downloaded", "skipped_complete", "would_download"}:
            self.log(f"worker {worker_id:02d} {row['status']} {row['job']} {event.get('exception', '')}")
        if self._rich:
            self._refresh()
        elif row["status"] in {"downloaded", "skipped_complete", "would_download"}:
            self.log(f"worker {worker_id:02d} {row['status']} {row['job']}")

    def _refresh(self) -> None:
        if not self._rich:
            return
        self._layout["progress"].update(self._progress_panel())
        self._layout["logs"].update(self._log_panel())

    def _progress_panel(self) -> Any:
        table = self._table_cls(expand=True, show_header=True, header_style="bold", box=None, pad_edge=False)
        worker_columns = self._resolved_worker_columns()
        for column_index in range(worker_columns):
            suffix = "" if worker_columns == 1 else f" {column_index + 1}"
            compact = worker_columns > 1
            ultra_compact = worker_columns > 2
            table.add_column(f"W{suffix}", width=3 if compact else 4, no_wrap=True)
            table.add_column(f"File{suffix}", ratio=2, min_width=9 if ultra_compact else 12 if compact else 14, no_wrap=True, overflow="ellipsis")
            table.add_column(f"Progress{suffix}", ratio=2, min_width=8 if ultra_compact else 12 if compact else 18, no_wrap=True, overflow="ellipsis")
            if not ultra_compact:
                table.add_column(f"Speed{suffix}", width=9 if compact else 11, no_wrap=True)
            table.add_column(f"State{suffix}", width=5 if ultra_compact else 7 if compact else 13, no_wrap=True, overflow="ellipsis")
        rows_per_column = (self.worker_slots + worker_columns - 1) // worker_columns
        for row_index in range(rows_per_column):
            cells = []
            for column_index in range(worker_columns):
                worker_id = row_index + column_index * rows_per_column
                if worker_id >= self.worker_slots:
                    cells.extend(["", "", "", ""] if worker_columns > 2 else ["", "", "", "", ""])
                    continue
                row = self._rows.get(worker_id, self._empty_row(worker_id))
                status = str(row.get("status") or "idle")
                row_cells = [
                    self._text_cls(f"{worker_id:02d}", style="cyan"),
                    self._text_cls(str(row.get("job") or "-"), style="white" if row.get("job") else "dim"),
                    self._text_cls(self._progress_cell(row, compact=worker_columns > 1), style=self._status_style(status)),
                ]
                if worker_columns <= 2:
                    row_cells.append(self._text_cls(self._speed_cell(row), style="green"))
                row_cells.append(self._text_cls(self._status_cell(status, compact=compact), style=self._status_style(status)))
                cells.extend(row_cells)
            table.add_row(*cells)
        return self._panel_cls(self._group_cls(self._global_status_line(), table), title="Massive SIP Downloads", border_style="cyan")

    def _log_panel(self) -> Any:
        text = self._text_cls("\n".join(self._logs) if self._logs else "No log messages yet.")
        return self._panel_cls(text, title="Logs", border_style="white")

    def _progress_cell(self, row: dict[str, Any], *, compact: bool = False) -> str:
        expected = int(row.get("expected") or 0)
        written = int(row.get("written") or 0)
        if expected <= 0:
            return "-"
        pct = max(0.0, min(1.0, written / expected))
        if compact:
            return f"{self._mini_bar(pct, width=8)} {pct:4.0%} {self._format_bytes(written)}/{self._format_bytes(expected)}"
        return f"{self._mini_bar(pct)} {pct:5.1%} {self._format_bytes(written)}/{self._format_bytes(expected)}"

    def _speed_cell(self, row: dict[str, Any]) -> str:
        written = int(row.get("written") or 0)
        started_at = float(row.get("started_at") or 0)
        if not written or not started_at:
            return "-"
        elapsed = max(1e-6, time.time() - started_at)
        return f"{self._format_bytes(written / elapsed)}/s"

    def _eta_cell(self, row: dict[str, Any]) -> str:
        expected = int(row.get("expected") or 0)
        written = int(row.get("written") or 0)
        started_at = float(row.get("started_at") or 0)
        if expected <= 0 or written <= 0 or written >= expected or not started_at:
            return "-"
        elapsed = max(1e-6, time.time() - started_at)
        rate = written / elapsed
        if rate <= 0:
            return "-"
        return self._format_duration((expected - written) / rate)

    def _status_style(self, status: str) -> str:
        if status in {"downloaded", "skipped_complete", "would_download"}:
            return "green"
        if status in {"failed", "failed_size_mismatch", "incomplete_existing", "missing_remote"}:
            return "red"
        if status in {"downloading", "running"}:
            return "bold white"
        return "dim"

    def _status_cell(self, status: str, *, compact: bool = False) -> str:
        if not compact:
            return status
        return {
            "idle": "-",
            "checking": "CHK",
            "downloading": "DL",
            "downloaded": "OK",
            "skipped_complete": "SKIP",
            "would_download": "DRY",
            "missing_remote": "MISS",
            "incomplete_existing": "PART",
            "failed_size_mismatch": "SIZE",
            "failed": "ERR",
            "running": "RUN",
        }.get(status, status[:5].upper())

    def _global_status_line(self) -> Any:
        covered_bytes = min(self.total_bytes, self._completed_bytes + self._active_bytes())
        file_pct = (self._completed_jobs / self.total_jobs) if self.total_jobs else 0.0
        byte_pct = (covered_bytes / self.total_bytes) if self.total_bytes else 0.0
        elapsed = max(1e-6, time.time() - self._started_at)
        speed = covered_bytes / elapsed
        eta = self._format_duration((self.total_bytes - covered_bytes) / speed) if speed > 0 and self.total_bytes > covered_bytes else "-"
        counts = " ".join(
            f"{self._status_cell(status, compact=True)}={count:,}"
            for status, count in sorted(self._status_counts.items())
            if count
        )
        if not counts:
            counts = "no completed files yet"
        message = (
            f"Overall {self._mini_bar(byte_pct, width=28)} {byte_pct:5.1%} "
            f"bytes {self._format_bytes(covered_bytes)}/{self._format_bytes(self.total_bytes)} "
            f"files {self._completed_jobs:,}/{self.total_jobs:,} ({file_pct:5.1%}) "
            f"speed {self._format_bytes(speed)}/s eta {eta} | {counts}"
        )
        return self._text_cls(message, style="bold cyan")

    def _active_bytes(self) -> int:
        total = 0
        for row in self._rows.values():
            if str(row.get("status") or "") in {"checking", "downloading", "running"}:
                total += int(row.get("written") or 0)
        return total

    def _result_completed_bytes(self, row: dict[str, Any]) -> int:
        status = str(row.get("status") or "")
        expected = int(row.get("expected") or 0)
        written = int(row.get("written") or 0)
        if status in {"downloaded", "skipped_complete", "would_download"}:
            return expected
        return min(expected, written) if expected else written

    def _mini_bar(self, fraction: float, width: int = 12) -> str:
        filled = int(round(max(0.0, min(1.0, fraction)) * width))
        return "#" * filled + "-" * (width - filled)

    def _resolved_worker_columns(self) -> int:
        if self.worker_columns:
            return min(max(1, self.worker_columns), 4)
        if self.worker_slots > 72:
            return 4
        if self.worker_slots > 16:
            return 2
        return 1

    def _default_progress_rows(self) -> int:
        rows_per_column = (self.worker_slots + self._resolved_worker_columns() - 1) // self._resolved_worker_columns()
        return max(8, rows_per_column + 4)

    def _empty_row(self, worker_id: int) -> dict[str, Any]:
        return {
            "worker_id": worker_id,
            "job": "",
            "status": "idle",
            "expected": 0,
            "written": 0,
            "started_at": 0.0,
            "updated_at": 0.0,
        }

    def _format_bytes(self, value: float) -> str:
        units = ["B", "KiB", "MiB", "GiB", "TiB"]
        amount = float(value)
        for unit in units:
            if abs(amount) < 1024.0 or unit == units[-1]:
                return f"{amount:.1f} {unit}"
            amount /= 1024.0
        return f"{amount:.1f} TiB"

    def _format_duration(self, seconds: float) -> str:
        seconds = max(0, int(seconds))
        if seconds < 60:
            return f"{seconds}s"
        minutes, sec = divmod(seconds, 60)
        if minutes < 60:
            return f"{minutes}m{sec:02d}s"
        hours, minutes = divmod(minutes, 60)
        return f"{hours}h{minutes:02d}m"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download Massive stock SIP quote/trade flatfiles concurrently from the S3-compatible flatfiles bucket. "
            "The local directory structure matches the S3 object key structure."
        )
    )
    parser.add_argument("--start-date", required=True, help="Inclusive YYYY-MM-DD start date.")
    parser.add_argument("--end-date", required=True, help="Inclusive YYYY-MM-DD end date.")
    parser.add_argument("--kinds", default="quotes,trades", help="Comma-separated subset: quotes,trades.")
    parser.add_argument("--processes", type=int, default=DEFAULT_PROCESSES)
    parser.add_argument("--flatfiles-root", default="", help="Destination root. Defaults to FLATFILES_ROOT.")
    parser.add_argument("--endpoint-url", default="", help="S3 endpoint. Defaults to S3_ENDPOINT_URL.")
    parser.add_argument("--bucket", default="", help="S3 bucket. Defaults to BUCKET.")
    parser.add_argument("--aws-access-key-id", default="", help="Defaults to AWS_ACCESS_KEY_ID.")
    parser.add_argument("--aws-secret-access-key", default="", help="Defaults to AWS_SECRET_ACCESS_KEY.")
    parser.add_argument("--aws-region", default=DEFAULT_AWS_REGION)
    parser.add_argument("--aws-service", default=DEFAULT_AWS_SERVICE)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--chunk-bytes", type=int, default=DEFAULT_CHUNK_BYTES)
    parser.add_argument("--progress-interval-seconds", type=float, default=DEFAULT_PROGRESS_INTERVAL_SECONDS)
    parser.add_argument("--limit-files", type=int, default=0, help="Debug limit after job discovery. 0 means no limit.")
    parser.add_argument("--report-path", default="", help="Optional JSONL report path.")
    parser.add_argument(
        "--discovery",
        choices=("remote", "calendar"),
        default=DEFAULT_DISCOVERY,
        help=(
            "remote lists Massive prefixes and downloads only existing remote files; "
            "calendar builds every calendar date and HEADs each object."
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-verify-tls", action="store_true")
    parser.add_argument(
        "--progress-layout",
        choices=("auto", "rich", "text"),
        default=DEFAULT_PROGRESS_LAYOUT,
        help="Console progress layout. auto uses Rich when installed; text uses plain log lines.",
    )
    parser.add_argument("--progress-log-lines", type=int, default=DEFAULT_PROGRESS_LOG_LINES)
    parser.add_argument("--progress-refresh-per-second", type=float, default=DEFAULT_PROGRESS_REFRESH_PER_SECOND)
    parser.add_argument(
        "--progress-panel-rows",
        type=int,
        default=DEFAULT_PROGRESS_PANEL_ROWS,
        help="Fixed height for the Rich top progress panel. 0 chooses a compact automatic height.",
    )
    parser.add_argument(
        "--progress-worker-columns",
        type=int,
        default=DEFAULT_PROGRESS_WORKER_COLUMNS,
        help="Number of side-by-side worker blocks in the Rich top panel. 0 auto-selects based on process count.",
    )
    screen_group = parser.add_mutually_exclusive_group()
    screen_group.add_argument(
        "--progress-screen",
        dest="progress_screen",
        action="store_true",
        help="Use a fixed Rich alternate screen so progress stays pinned.",
    )
    screen_group.add_argument(
        "--no-progress-screen",
        dest="progress_screen",
        action="store_false",
        help="Render Rich progress in normal terminal scrollback.",
    )
    parser.set_defaults(progress_screen=True)
    parser.add_argument(
        "--keep-incomplete",
        action="store_true",
        help="Keep incomplete existing destination files instead of replacing them.",
    )
    return parser.parse_args()


def iter_dates(start: str, end: str) -> Iterable[str]:
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    if end_date < start_date:
        raise ValueError(f"end-date {end} is before start-date {start}")
    current = start_date
    while current <= end_date:
        yield current.isoformat()
        current += timedelta(days=1)


def iter_months(start: str, end: str) -> Iterable[tuple[int, int]]:
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    if end_date < start_date:
        raise ValueError(f"end-date {end} is before start-date {start}")
    year = start_date.year
    month = start_date.month
    while (year, month) <= (end_date.year, end_date.month):
        yield year, month
        month += 1
        if month == 13:
            month = 1
            year += 1


def parse_kinds(raw: str) -> list[str]:
    kinds = [item.strip() for item in raw.split(",") if item.strip()]
    invalid = [kind for kind in kinds if kind not in KIND_PREFIXES]
    if invalid:
        raise ValueError(f"Invalid kinds {invalid}; expected subset of {sorted(KIND_PREFIXES)}")
    if not kinds:
        raise ValueError("--kinds must include at least one kind")
    return kinds


def object_key(kind: str, session_date: str) -> str:
    year = session_date[:4]
    month = session_date[5:7]
    return f"{KIND_PREFIXES[kind]}/{year}/{month}/{session_date}.csv.gz"


def build_calendar_jobs(flatfiles_root: Path, start_date: str, end_date: str, kinds: list[str]) -> list[DownloadJob]:
    jobs: list[DownloadJob] = []
    for session_date in iter_dates(start_date, end_date):
        for kind in kinds:
            key = object_key(kind, session_date)
            jobs.append(DownloadJob(kind=kind, session_date=session_date, key=key, destination=str(flatfiles_root / key)))
    return jobs


def env_value(cli_value: str, key: str, *, required: bool = True) -> str:
    value = cli_value or os.environ.get(key, "")
    if required and not value:
        raise RuntimeError(f"Missing required configuration {key}. Set it in .env or pass the matching CLI argument.")
    return value


def canonical_query(params: dict[str, str]) -> str:
    return "&".join(
        f"{parse.quote(key, safe='-_.~')}={parse.quote(value, safe='-_.~')}"
        for key, value in sorted(params.items())
    )


def sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def signing_key(secret_key: str, date_stamp: str, region: str, service: str) -> bytes:
    key_date = sign(("AWS4" + secret_key).encode("utf-8"), date_stamp)
    key_region = sign(key_date, region)
    key_service = sign(key_region, service)
    return sign(key_service, "aws4_request")


def signed_headers(
    *,
    method: str,
    endpoint_url: str,
    bucket: str,
    key: str,
    access_key: str,
    secret_key: str,
    region: str,
    service: str,
    query_params: dict[str, str] | None = None,
) -> tuple[str, dict[str, str]]:
    endpoint = endpoint_url.rstrip("/")
    parsed = parse.urlparse(endpoint)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid S3 endpoint URL: {endpoint_url}")
    canonical_uri = "/" + parse.quote(f"{bucket}/{key}", safe="/-_.~")
    query_params = query_params or {}
    canonical_qs = canonical_query(query_params)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    date_stamp = timestamp[:8]
    host = parsed.netloc
    payload_hash = "UNSIGNED-PAYLOAD"
    canonical_headers = f"host:{host}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{timestamp}\n"
    signed_header_names = "host;x-amz-content-sha256;x-amz-date"
    canonical_request = "\n".join(
        [method, canonical_uri, canonical_qs, canonical_headers, signed_header_names, payload_hash]
    )
    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            timestamp,
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    signature = hmac.new(
        signing_key(secret_key, date_stamp, region, service),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    authorization = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_header_names}, Signature={signature}"
    )
    url = f"{endpoint}{canonical_uri}"
    if canonical_qs:
        url += f"?{canonical_qs}"
    return url, {
        "Authorization": authorization,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": timestamp,
    }


def signed_request(
    config: DownloadConfig,
    method: str,
    key: str,
    query_params: dict[str, str] | None = None,
) -> request.Request:
    url, headers = signed_headers(
        method=method,
        endpoint_url=config.endpoint_url,
        bucket=config.bucket,
        key=key,
        access_key=config.access_key,
        secret_key=config.secret_key,
        region=config.region,
        service=config.service,
        query_params=query_params,
    )
    return request.Request(url, headers=headers, method=method)


def urlopen_signed(req: request.Request, config: DownloadConfig):
    context = None if config.verify_tls else ssl._create_unverified_context()
    return request.urlopen(req, timeout=config.timeout_seconds, context=context)


def remote_size(config: DownloadConfig, key: str) -> int | None:
    req = signed_request(config, "HEAD", key)
    try:
        with urlopen_signed(req, config) as response:
            return int(response.headers.get("Content-Length", "0"))
    except error.HTTPError as exc:
        if exc.code in (403, 404):
            return None
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HEAD failed for {key}: HTTP {exc.code} {exc.reason}: {body}") from exc


def xml_child_text(parent: ET.Element, child_name: str) -> str:
    for child in list(parent):
        if child.tag.rsplit("}", 1)[-1] == child_name:
            return child.text or ""
    return ""


def xml_children(parent: ET.Element, child_name: str) -> list[ET.Element]:
    return [child for child in list(parent) if child.tag.rsplit("}", 1)[-1] == child_name]


def list_remote_objects(config: DownloadConfig, prefix: str) -> list[RemoteObject]:
    objects: list[RemoteObject] = []
    continuation_token = ""
    page = 0
    while True:
        page += 1
        query_params = {
            "list-type": "2",
            "prefix": prefix,
        }
        if continuation_token:
            query_params["continuation-token"] = continuation_token
        req = signed_request(config, "GET", "", query_params=query_params)
        try:
            with urlopen_signed(req, config) as response:
                body = response.read()
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"LIST failed for prefix {prefix!r} page={page}: HTTP {exc.code} {exc.reason}: {body}") from exc

        root = ET.fromstring(body)
        for item in xml_children(root, "Contents"):
            key = xml_child_text(item, "Key")
            size_text = xml_child_text(item, "Size")
            if key:
                objects.append(RemoteObject(key=key, size=int(size_text or "0")))
        is_truncated = xml_child_text(root, "IsTruncated").lower() == "true"
        continuation_token = xml_child_text(root, "NextContinuationToken")
        if not is_truncated:
            break
        if not continuation_token:
            raise RuntimeError(f"LIST response for prefix {prefix!r} is truncated but has no NextContinuationToken")
    return objects


def build_remote_jobs(
    flatfiles_root: Path,
    start_date: str,
    end_date: str,
    kinds: list[str],
    config: DownloadConfig,
) -> list[DownloadJob]:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    jobs: list[DownloadJob] = []
    for kind in kinds:
        for year, month in iter_months(start_date, end_date):
            prefix = f"{KIND_PREFIXES[kind]}/{year:04d}/{month:02d}/"
            print(f"DISCOVER {kind} prefix={prefix}", flush=True)
            objects = list_remote_objects(config, prefix)
            matched = 0
            for obj in objects:
                match = REMOTE_FILE_RE.search(Path(obj.key).name)
                if not match:
                    continue
                session = date.fromisoformat(match.group("date"))
                if not (start <= session <= end):
                    continue
                expected_key = object_key(kind, session.isoformat())
                if obj.key != expected_key:
                    continue
                jobs.append(
                    DownloadJob(
                        kind=kind,
                        session_date=session.isoformat(),
                        key=obj.key,
                        destination=str(flatfiles_root / obj.key),
                        remote_size=obj.size,
                    )
                )
                matched += 1
            print(f"DISCOVER {kind} prefix={prefix} remote_files={matched:,}", flush=True)
    jobs.sort(key=lambda item: (item.session_date, item.kind, item.key))
    return jobs


def job_label(job: DownloadJob) -> str:
    return f"{job.kind}:{job.session_date}"


def emit_progress(progress_queue: mp.Queue | None, event: dict[str, Any]) -> None:
    if progress_queue is None:
        return
    try:
        progress_queue.put(event)
    except Exception:
        return


def progress_event(
    *,
    worker_id: int,
    job: DownloadJob,
    status: str,
    bytes_expected: int = 0,
    bytes_written: int = 0,
    started_at: float = 0.0,
) -> dict[str, Any]:
    return {
        "type": "progress",
        "worker_id": worker_id,
        "job_label": job_label(job),
        "kind": job.kind,
        "session_date": job.session_date,
        "key": job.key,
        "status": status,
        "bytes_expected": int(bytes_expected or 0),
        "bytes_written": int(bytes_written or 0),
        "started_at": float(started_at or time.time()),
    }


def result_event(worker_id: int, result: DownloadResult) -> dict[str, Any]:
    return {
        "type": "result",
        "worker_id": worker_id,
        "job_label": f"{result.kind}:{result.session_date}",
        "kind": result.kind,
        "session_date": result.session_date,
        "key": result.key,
        "status": result.status,
        "bytes_expected": result.bytes_expected,
        "bytes_written": result.bytes_written,
        "exception": result.exception,
    }


def download_one(
    config: DownloadConfig,
    job: DownloadJob,
    worker_id: int,
    progress_queue: mp.Queue | None = None,
) -> DownloadResult:
    t0 = time.time()
    started_at = time.time()
    destination = Path(job.destination)
    part_path = destination.with_name(destination.name + ".part")
    try:
        expected_size = job.remote_size or remote_size(config, job.key)
        if expected_size is None:
            result = DownloadResult(worker_id, job.kind, job.session_date, job.key, str(destination), "missing_remote", wall_seconds=time.time() - t0)
            emit_progress(progress_queue, result_event(worker_id, result))
            return result
        emit_progress(
            progress_queue,
            progress_event(worker_id=worker_id, job=job, status="checking", bytes_expected=expected_size, started_at=started_at),
        )
        if destination.exists():
            local_size = destination.stat().st_size
            if local_size == expected_size:
                result = DownloadResult(worker_id, job.kind, job.session_date, job.key, str(destination), "skipped_complete", expected_size, local_size, time.time() - t0)
                emit_progress(progress_queue, result_event(worker_id, result))
                return result
            if not config.overwrite_incomplete:
                result = DownloadResult(
                    worker_id,
                    job.kind,
                    job.session_date,
                    job.key,
                    str(destination),
                    "incomplete_existing",
                    expected_size,
                    local_size,
                    time.time() - t0,
                    f"existing size {local_size} != remote size {expected_size}",
                )
                emit_progress(progress_queue, result_event(worker_id, result))
                return result
        if config.dry_run:
            result = DownloadResult(worker_id, job.kind, job.session_date, job.key, str(destination), "would_download", expected_size, 0, time.time() - t0)
            emit_progress(progress_queue, result_event(worker_id, result))
            return result

        destination.parent.mkdir(parents=True, exist_ok=True)
        if part_path.exists():
            part_path.unlink()
        req = signed_request(config, "GET", job.key)
        bytes_written = 0
        last_progress_update = time.monotonic()
        emit_progress(
            progress_queue,
            progress_event(
                worker_id=worker_id,
                job=job,
                status="downloading",
                bytes_expected=expected_size,
                bytes_written=0,
                started_at=started_at,
            ),
        )
        with urlopen_signed(req, config) as response, part_path.open("wb") as handle:
            while True:
                chunk = response.read(config.chunk_bytes)
                if not chunk:
                    break
                handle.write(chunk)
                bytes_written += len(chunk)
                now = time.monotonic()
                if now - last_progress_update >= max(0.1, config.progress_interval_seconds):
                    emit_progress(
                        progress_queue,
                        progress_event(
                            worker_id=worker_id,
                            job=job,
                            status="downloading",
                            bytes_expected=expected_size,
                            bytes_written=bytes_written,
                            started_at=started_at,
                        ),
                    )
                    last_progress_update = now
        actual_size = part_path.stat().st_size
        if actual_size != expected_size:
            result = DownloadResult(
                worker_id,
                job.kind,
                job.session_date,
                job.key,
                str(destination),
                "failed_size_mismatch",
                expected_size,
                actual_size,
                time.time() - t0,
                f"downloaded {actual_size} bytes, expected {expected_size}",
            )
            emit_progress(progress_queue, result_event(worker_id, result))
            return result
        if destination.exists():
            destination.unlink()
        part_path.replace(destination)
        result = DownloadResult(worker_id, job.kind, job.session_date, job.key, str(destination), "downloaded", expected_size, actual_size, time.time() - t0)
        emit_progress(progress_queue, result_event(worker_id, result))
        return result
    except Exception as exc:
        result = DownloadResult(worker_id, job.kind, job.session_date, job.key, str(destination), "failed", wall_seconds=time.time() - t0, exception=repr(exc))
        emit_progress(progress_queue, result_event(worker_id, result))
        return result


def worker_main(
    worker_id: int,
    jobs: list[DownloadJob],
    config: DownloadConfig,
    queue: mp.Queue | None,
    progress_queue: mp.Queue | None,
) -> list[DownloadResult]:
    results: list[DownloadResult] = []
    for job in jobs:
        result = download_one(config, job, worker_id, progress_queue)
        results.append(result)
        if queue is not None:
            queue.put(asdict(result))
    return results


def ignore_sigint_in_worker() -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, signal.SIG_IGN)


def install_console_interrupt_handlers() -> None:
    signal.signal(signal.SIGINT, signal.default_int_handler)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, signal.default_int_handler)


def worker_process_main(
    worker_id: int,
    jobs: list[DownloadJob],
    config: DownloadConfig,
    report_queue: mp.Queue | None,
    progress_queue: mp.Queue | None,
    summary_queue: mp.Queue,
) -> None:
    install_console_interrupt_handlers()
    try:
        results = worker_main(worker_id, jobs, config, report_queue, progress_queue)
    except KeyboardInterrupt:
        emit_progress(progress_queue, {"type": "log", "message": f"worker {worker_id:02d} interrupted"})
        summary_queue.put(
            {
                "worker_id": worker_id,
                "counts": {},
                "downloaded_bytes": 0,
                "result_count": 0,
                "interrupted": True,
            }
        )
        return
    counts: dict[str, int] = {}
    downloaded_bytes = 0
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
        if result.status == "downloaded":
            downloaded_bytes += result.bytes_written
    summary_queue.put(
        {
            "worker_id": worker_id,
            "counts": counts,
            "downloaded_bytes": downloaded_bytes,
            "result_count": len(results),
            "interrupted": False,
        }
    )


def result_writer(report_path: Path, queue: mp.Queue, expected_done_messages: int) -> None:
    ignore_sigint_in_worker()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    done = 0
    with report_path.open("a", encoding="utf-8") as handle:
        while done < expected_done_messages:
            item = queue.get()
            if item == {"type": "worker_done"}:
                done += 1
                continue
            handle.write(json.dumps(item, sort_keys=True) + "\n")
            handle.flush()


def split_jobs(jobs: list[DownloadJob], processes: int) -> list[list[DownloadJob]]:
    chunks = [[] for _ in range(processes)]
    for idx, job in enumerate(jobs):
        chunks[idx % processes].append(job)
    return chunks


def drain_progress_events(progress_queue: mp.Queue, progress: DownloadProgressDisplay) -> None:
    while True:
        try:
            event = progress_queue.get_nowait()
        except queue_module.Empty:
            break
        progress.handle_event(event)


def main() -> None:
    install_console_interrupt_handlers()
    loaded_env_files = load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    kinds = parse_kinds(args.kinds)
    flatfiles_root_raw = env_value(args.flatfiles_root, "FLATFILES_ROOT")
    flatfiles_root = Path(flatfiles_root_raw)
    config = DownloadConfig(
        endpoint_url=env_value(args.endpoint_url, "S3_ENDPOINT_URL"),
        bucket=env_value(args.bucket, "BUCKET"),
        access_key=env_value(args.aws_access_key_id, "AWS_ACCESS_KEY_ID"),
        secret_key=env_value(args.aws_secret_access_key, "AWS_SECRET_ACCESS_KEY"),
        region=args.aws_region,
        service=args.aws_service,
        timeout_seconds=float(args.timeout_seconds),
        chunk_bytes=int(args.chunk_bytes),
        verify_tls=not args.no_verify_tls,
        overwrite_incomplete=not args.keep_incomplete,
        dry_run=bool(args.dry_run),
        progress_interval_seconds=float(args.progress_interval_seconds),
    )
    if args.discovery == "remote":
        jobs = build_remote_jobs(flatfiles_root, args.start_date, args.end_date, kinds, config)
    else:
        jobs = build_calendar_jobs(flatfiles_root, args.start_date, args.end_date, kinds)
    if args.limit_files > 0:
        jobs = jobs[: args.limit_files]
    processes = max(1, min(int(args.processes), len(jobs) or 1))
    chunks = split_jobs(jobs, processes)
    total_remote_bytes = sum(max(0, int(job.remote_size or 0)) for job in jobs)
    default_report = flatfiles_root / "_download_reports" / f"massive_sip_flatfiles_download_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    report_path = Path(args.report_path) if args.report_path else default_report

    report_queue = mp.Queue()
    progress_queue = mp.Queue()
    summary_queue = mp.Queue()
    writer = mp.Process(target=result_writer, args=(report_path, report_queue, processes), daemon=True)
    writer.start()
    worker_processes = [
        mp.Process(
            target=worker_process_main,
            args=(worker_id, chunk, config, report_queue, progress_queue, summary_queue),
            daemon=False,
        )
        for worker_id, chunk in enumerate(chunks)
    ]
    interrupted = False
    worker_failures: list[str] = []
    with DownloadProgressDisplay(
        mode=args.progress_layout,
        worker_slots=processes,
        log_lines=args.progress_log_lines,
        screen=args.progress_screen,
        refresh_per_second=args.progress_refresh_per_second,
        panel_rows=args.progress_panel_rows,
        worker_columns=args.progress_worker_columns,
        total_jobs=len(jobs),
        total_bytes=total_remote_bytes,
    ) as progress:
        if progress.fallback_reason:
            progress.log(progress.fallback_reason)
        progress.log("Massive SIP flatfile downloader")
        progress.log(f"date_range={args.start_date} -> {args.end_date} kinds={kinds} discovery={args.discovery}")
        progress.log(
            f"jobs={len(jobs):,} processes={processes} chunk_bytes={config.chunk_bytes:,} "
            f"remote_bytes={total_remote_bytes:,}"
        )
        progress.log(f"endpoint={config.endpoint_url} bucket={config.bucket}")
        progress.log(f"flatfiles_root={flatfiles_root}")
        progress.log(f"report_path={report_path}")
        progress.log(f"dry_run={config.dry_run} overwrite_incomplete={config.overwrite_incomplete}")
        progress.log(f"secret_status={secret_status(ENV_KEYS)}")
        progress.log(f"loaded_env_files={[str(path) for path in loaded_env_files]}")
        try:
            for process in worker_processes:
                process.start()
            while True:
                drain_progress_events(progress_queue, progress)
                alive = False
                for process in worker_processes:
                    process.join(timeout=0.2)
                    if process.is_alive():
                        alive = True
                if not alive:
                    break
            drain_progress_events(progress_queue, progress)
        except KeyboardInterrupt:
            interrupted = True
            progress.log(
                "CTRL+C received. Stopping download workers now; completed files remain valid and partial .part files will be retried on the next run."
            )
            for process in worker_processes:
                if process.is_alive():
                    progress.log(f"TERM worker pid={process.pid}")
                    process.terminate()
            deadline = time.time() + 5.0
            for process in worker_processes:
                remaining = max(0.0, deadline - time.time())
                process.join(timeout=remaining)
            for process in worker_processes:
                if process.is_alive():
                    progress.log(f"KILL worker pid={process.pid}")
                    process.kill()
                    process.join(timeout=2.0)
        except BaseException:
            for process in worker_processes:
                if process.is_alive():
                    process.terminate()
            for process in worker_processes:
                process.join(timeout=5.0)
            raise
        finally:
            drain_progress_events(progress_queue, progress)
            for _ in range(processes):
                report_queue.put({"type": "worker_done"})
            writer.join(timeout=10)
            if writer.is_alive():
                writer.terminate()
                writer.join(timeout=5)
            report_queue.close()
            report_queue.join_thread()
            progress_queue.close()
            progress_queue.join_thread()

    for process in worker_processes:
        if process.exitcode not in (0, None):
            worker_failures.append(f"worker pid={process.pid} exitcode={process.exitcode}")

    counts: dict[str, int] = {}
    total_downloaded_bytes = 0
    summaries = []
    while True:
        try:
            summaries.append(summary_queue.get_nowait())
        except queue_module.Empty:
            break
    summary_queue.close()
    summary_queue.join_thread()
    summary_interrupted = any(bool(summary.get("interrupted", False)) for summary in summaries)
    for summary in summaries:
        for status, count in summary["counts"].items():
            counts[status] = counts.get(status, 0) + int(count)
        total_downloaded_bytes += int(summary["downloaded_bytes"])

    if interrupted or summary_interrupted:
        raise SystemExit(130)
    print("\n" + "=" * 96, flush=True)
    print("Download summary", flush=True)
    for status in sorted(counts):
        print(f"{status}: {counts[status]:,}", flush=True)
    print(f"downloaded_bytes={total_downloaded_bytes:,}", flush=True)
    print(f"downloaded_gib={total_downloaded_bytes / (1024**3):.3f}", flush=True)
    print(f"report_path={report_path}", flush=True)
    for failure in worker_failures:
        print(f"worker_failure: {failure}", flush=True)
    failed = sum(count for status, count in counts.items() if status.startswith("failed"))
    incomplete = counts.get("incomplete_existing", 0)
    if failed or incomplete or worker_failures:
        raise SystemExit(
            f"Download completed with failed={failed:,} incomplete_existing={incomplete:,} "
            f"worker_failures={len(worker_failures):,}. See report: {report_path}"
        )
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
