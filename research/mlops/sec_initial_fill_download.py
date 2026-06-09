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
    results_lock = threading.Lock()
    started = time.perf_counter()
    completed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.download_concurrency)) as executor:
        futures = [
            executor.submit(
                download_source,
                spec,
                user_agent,
                limiter,
                max(1.0, args.request_timeout_seconds),
                max(0, args.max_retries),
                max(0.1, args.retry_base_seconds),
                max(1.0, args.progress_interval_seconds),
                bool(args.force),
            )
            for spec in specs
        ]
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            with results_lock:
                completed += 1
                results.append(result)
                print(
                    f"[{completed}/{len(specs)}] {result.status} {result.source_kind} "
                    f"bytes={result.byte_size} elapsed={result.elapsed_seconds:.1f}s path={result.artifact_path}",
                    flush=True,
                )
    print(f"download_wall_seconds={time.perf_counter() - started:.1f}", flush=True)
    return sorted(results, key=lambda item: (item.source_kind, item.source_date, item.artifact_path))


def download_source(
    spec: SourceSpec,
    user_agent: str,
    limiter: RateLimiter,
    timeout_seconds: float,
    max_retries: int,
    retry_base_seconds: float,
    progress_interval_seconds: float,
    force: bool,
) -> DownloadResult:
    target = Path(spec.artifact_path)
    started = time.perf_counter()
    if target.exists() and not force:
        sha256 = sha256_file(target)
        return DownloadResult(
            source_file_id=source_file_id(spec, target, sha256),
            source_kind=spec.source_kind,
            source_url=spec.source_url,
            artifact_path=str(target),
            source_date=spec.source_date,
            downloaded_at_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            byte_size=target.stat().st_size,
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
            user_agent,
            limiter,
            timeout_seconds,
            max_retries,
            retry_base_seconds,
            progress_interval_seconds,
        )
        sha256 = sha256_file(target)
        return DownloadResult(
            source_file_id=source_file_id(spec, target, sha256),
            source_kind=spec.source_kind,
            source_url=spec.source_url,
            artifact_path=str(target),
            source_date=spec.source_date,
            downloaded_at_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            byte_size=target.stat().st_size,
            sha256=sha256,
            etag=etag,
            last_modified=last_modified,
            elapsed_seconds=round(time.perf_counter() - started, 3),
            status="downloaded",
        )
    except Exception as exc:  # noqa: BLE001
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
    user_agent: str,
    limiter: RateLimiter,
    timeout_seconds: float,
    max_retries: int,
    retry_base_seconds: float,
    progress_interval_seconds: float,
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
        limiter.wait()
        req = request.Request(url, headers=headers)
        bytes_written = 0
        last_progress_at = time.perf_counter()
        try:
            with request.urlopen(req, timeout=timeout_seconds) as response:
                etag = response.headers.get("ETag", "")
                last_modified = response.headers.get("Last-Modified", "")
                length = parse_int(response.headers.get("Content-Length"))
                with part_path.open("wb") as handle:
                    while True:
                        chunk = response.read(CHUNK_SIZE_BYTES)
                        if not chunk:
                            break
                        handle.write(chunk)
                        bytes_written += len(chunk)
                        now = time.perf_counter()
                        if now - last_progress_at >= progress_interval_seconds:
                            print(progress_line(target, bytes_written, length), flush=True)
                            last_progress_at = now
            part_path.replace(target)
            return etag, last_modified
        except error.HTTPError as exc:
            last_error = f"HTTP {exc.code}: {exc.reason}"
            part_path.unlink(missing_ok=True)
            if exc.code not in RETRY_HTTP_CODES or attempt >= max_retries:
                raise RuntimeError(last_error) from exc
            retry_after = parse_retry_after(exc.headers.get("Retry-After"))
            time.sleep(retry_after if retry_after is not None else retry_base_seconds * (2**attempt))
        except Exception as exc:  # noqa: BLE001
            last_error = repr(exc)
            part_path.unlink(missing_ok=True)
            if attempt >= max_retries:
                raise RuntimeError(last_error) from exc
            time.sleep(retry_base_seconds * (2**attempt))
    raise RuntimeError(last_error or "request failed")


def progress_line(target: Path, bytes_written: int, total_bytes: int) -> str:
    mib = bytes_written / (1024 * 1024)
    if total_bytes:
        total_mib = total_bytes / (1024 * 1024)
        pct = 100.0 * bytes_written / total_bytes
        return f"downloading {target.name}: {mib:.1f}/{total_mib:.1f} MiB ({pct:.1f}%)"
    return f"downloading {target.name}: {mib:.1f} MiB"


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
