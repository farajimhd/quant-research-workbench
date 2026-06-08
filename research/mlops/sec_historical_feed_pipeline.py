from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import time
import traceback
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


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
    build_day_job,
    discover_available_archive_days,
    download_archive,
    fetch_headers_for_submissions,
    merge_submission_timestamp,
    parse_date,
    parse_nc_archive,
    quarter_name,
    sec_user_agent,
    sha256_file,
    write_rows,
)


DEFAULT_NORMALIZED_ROOT_WIN = Path("D:/market-data/sec_edgar_feed_normalized")


@dataclass(frozen=True, slots=True)
class PipelineDayResult:
    archive_date: str
    archive_url: str
    archive_path: str
    normalized_day_dir: str
    archive_bytes: int
    archive_sha256: str
    submissions: int
    documents: int
    header_success: int
    header_failed: int
    download_seconds: float
    parse_seconds: float
    wall_seconds: float
    status: str
    error: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Bounded SEC EDGAR Feed pipeline: discover real archive days, download compressed .nc.tar.gz archives, "
            "stream-parse normalized filing/document rows, fetch accepted_at headers, and store normalized JSONL."
        )
    )
    parser.add_argument("--start-date", required=True, help="Inclusive archive date, YYYY-MM-DD.")
    parser.add_argument("--end-date", required=True, help="Exclusive archive date, YYYY-MM-DD.")
    parser.add_argument("--artifact-root-win", default=os.environ.get("SEC_HISTORICAL_ARTIFACT_ROOT_WIN", str(DEFAULT_ARTIFACT_ROOT_WIN)))
    parser.add_argument("--normalized-root-win", default=os.environ.get("SEC_HISTORICAL_NORMALIZED_ROOT_WIN", str(DEFAULT_NORMALIZED_ROOT_WIN)))
    parser.add_argument("--output-root-win", default=os.environ.get("SEC_HISTORICAL_OUTPUT_ROOT_WIN", str(DEFAULT_OUTPUT_ROOT_WIN)))
    parser.add_argument("--download-concurrency", type=int, default=int(os.environ.get("SEC_DOWNLOAD_CONCURRENCY", "2")))
    parser.add_argument("--header-concurrency", type=int, default=int(os.environ.get("SEC_HEADER_CONCURRENCY", "8")))
    parser.add_argument("--sec-request-min-interval-seconds", type=float, default=float(os.environ.get("SEC_REQUEST_MIN_INTERVAL_SECONDS", "0.11")))
    parser.add_argument("--request-timeout-seconds", type=float, default=float(os.environ.get("SEC_REQUEST_TIMEOUT_SECONDS", "60")))
    parser.add_argument("--max-retries", type=int, default=int(os.environ.get("SEC_MAX_RETRIES", "4")))
    parser.add_argument("--retry-base-seconds", type=float, default=float(os.environ.get("SEC_RETRY_BASE_SECONDS", "1.5")))
    parser.add_argument("--limit-days", type=int, default=0, help="Smoke-test cap on discovered archive days.")
    parser.add_argument("--limit-files-per-day", type=int, default=0, help="Smoke-test cap on .nc files parsed per day.")
    parser.add_argument("--force-redownload", action="store_true", help="Redownload archives even when already present.")
    parser.add_argument("--no-header-fetch", action="store_true", help="Skip .hdr.sgml accepted_at enrichment.")
    parser.add_argument("--persist-nc-files", action="store_true", help="Also persist individual .nc files. Default streams from tar.gz only.")
    parser.add_argument("--delete-archive-after-parse", action="store_true", help="Delete each compressed archive after successful normalized output write.")
    parser.add_argument("--dry-run", action="store_true", help="Only discover and report jobs.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    loaded_env_files = load_env_files(discover_env_files(REPO_ROOT))
    start_date = parse_date(args.start_date)
    end_date = parse_date(args.end_date)
    if end_date <= start_date:
        raise SystemExit("--end-date must be later than --start-date")

    artifact_root = Path(args.artifact_root_win)
    normalized_root = Path(args.normalized_root_win)
    output_root = Path(args.output_root_win)
    output_root.mkdir(parents=True, exist_ok=True)
    normalized_root.mkdir(parents=True, exist_ok=True)
    user_agent = sec_user_agent()
    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    report_path = output_root / f"sec_feed_pipeline_{run_id}.jsonl"
    request_min_interval = max(0.0, args.sec_request_min_interval_seconds)
    request_timeout = max(1.0, args.request_timeout_seconds)
    max_retries = max(0, args.max_retries)
    retry_base = max(0.1, args.retry_base_seconds)

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
        "normalized_root": str(normalized_root),
        "output_root": str(output_root),
        "report_path": str(report_path),
        "download_concurrency": max(1, args.download_concurrency),
        "header_concurrency": max(1, args.header_concurrency),
        "sec_request_min_interval_seconds": request_min_interval,
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
    print_header(config)
    append_jsonl(report_path, config)

    if args.dry_run:
        for job in jobs:
            append_jsonl(report_path, {"type": "planned_job", "run_id": run_id, "job": asdict(job)})
        print("dry_run=1, no archives downloaded and no normalized rows written", flush=True)
        return

    run_pipeline(args, jobs, normalized_root, report_path, run_id)


def run_pipeline(args: argparse.Namespace, jobs: list[DayJob], normalized_root: Path, report_path: Path, run_id: str) -> None:
    started = time.perf_counter()
    sec_request_limiter = RateLimiter(max(0.0, args.sec_request_min_interval_seconds))
    download_concurrency = max(1, args.download_concurrency)
    next_index = 0
    active: dict[concurrent.futures.Future[tuple[DayJob, float]], DayJob] = {}
    totals = {
        "downloaded_days": 0,
        "parsed_days": 0,
        "failed_days": 0,
        "archive_bytes": 0,
        "submissions": 0,
        "documents": 0,
        "header_success": 0,
        "header_failed": 0,
    }

    with concurrent.futures.ThreadPoolExecutor(max_workers=download_concurrency) as pool:
        while next_index < len(jobs) and len(active) < download_concurrency:
            future = pool.submit(download_one_archive, jobs[next_index], sec_request_limiter)
            active[future] = jobs[next_index]
            next_index += 1

        while active:
            done, _ = concurrent.futures.wait(active.keys(), return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                job = active.pop(future)
                try:
                    downloaded_job, download_seconds = future.result()
                    totals["downloaded_days"] += 1
                    result = parse_one_archive(downloaded_job, args, normalized_root, download_seconds, sec_request_limiter)
                except Exception as exc:  # noqa: BLE001
                    result = failed_result(job, exc)
                append_jsonl(report_path, {"type": "day", "run_id": run_id, "result": asdict(result)})
                if result.status == "ok":
                    totals["parsed_days"] += 1
                    totals["archive_bytes"] += result.archive_bytes
                    totals["submissions"] += result.submissions
                    totals["documents"] += result.documents
                    totals["header_success"] += result.header_success
                    totals["header_failed"] += result.header_failed
                else:
                    totals["failed_days"] += 1
                print_progress(len(jobs), totals, started, result)
                while next_index < len(jobs) and len(active) < download_concurrency:
                    future = pool.submit(download_one_archive, jobs[next_index], sec_request_limiter)
                    active[future] = jobs[next_index]
                    next_index += 1

    summary = {
        "type": "summary",
        "run_id": run_id,
        "status": "failed" if totals["failed_days"] else "ok",
        "wall_seconds": round(time.perf_counter() - started, 3),
        **totals,
    }
    append_jsonl(report_path, summary)
    print("\nsummary=" + json.dumps(summary, sort_keys=True), flush=True)


def download_one_archive(job: DayJob, limiter: RateLimiter) -> tuple[DayJob, float]:
    started = time.perf_counter()
    download_archive(job, limiter)
    return job, round(time.perf_counter() - started, 3)


def parse_one_archive(
    job: DayJob,
    args: argparse.Namespace,
    normalized_root: Path,
    download_seconds: float,
    sec_request_limiter: RateLimiter,
) -> PipelineDayResult:
    started = time.perf_counter()
    archive_path = Path(job.archive_path)
    archive_sha = sha256_file(archive_path)
    archive_bytes = archive_path.stat().st_size
    normalized_day_dir = normalized_root / job.archive_date[:4] / quarter_name(parse_date(job.archive_date)) / job.archive_date
    normalized_day_dir.mkdir(parents=True, exist_ok=True)
    submissions_path = normalized_day_dir / "submissions.jsonl"
    documents_path = normalized_day_dir / "documents.jsonl"
    headers_path = normalized_day_dir / "headers.jsonl"
    manifest_path = normalized_day_dir / "manifest.jsonl"

    parsed, documents = parse_nc_archive(job.archive_date, archive_path, Path(job.extract_dir), job.limit_files, job.persist_nc_files)
    timestamps = fetch_headers_for_submissions(parsed, job, sec_request_limiter)
    submission_rows = [merge_submission_timestamp(item, timestamps.get(item.accession_number)) for item in parsed]
    write_rows(submissions_path, submission_rows)
    write_rows(documents_path, [asdict(item) for item in documents])
    write_rows(headers_path, [asdict(item) for item in timestamps.values()])
    parse_seconds = round(time.perf_counter() - started, 3)

    result = PipelineDayResult(
        archive_date=job.archive_date,
        archive_url=job.archive_url,
        archive_path=str(archive_path),
        normalized_day_dir=str(normalized_day_dir),
        archive_bytes=archive_bytes,
        archive_sha256=archive_sha,
        submissions=len(parsed),
        documents=len(documents),
        header_success=sum(1 for item in timestamps.values() if item.fetch_status == "ok"),
        header_failed=sum(1 for item in timestamps.values() if item.fetch_status == "failed"),
        download_seconds=download_seconds,
        parse_seconds=parse_seconds,
        wall_seconds=round(download_seconds + parse_seconds, 3),
        status="ok",
    )
    append_jsonl(manifest_path, {"type": "day_manifest", "result": asdict(result)})
    if args.delete_archive_after_parse:
        archive_path.unlink(missing_ok=True)
    return result


def failed_result(job: DayJob, exc: Exception) -> PipelineDayResult:
    return PipelineDayResult(
        archive_date=job.archive_date,
        archive_url=job.archive_url,
        archive_path=job.archive_path,
        normalized_day_dir="",
        archive_bytes=0,
        archive_sha256="",
        submissions=0,
        documents=0,
        header_success=0,
        header_failed=0,
        download_seconds=0.0,
        parse_seconds=0.0,
        wall_seconds=0.0,
        status="failed",
        error=f"{exc!r}\n{traceback.format_exc()}",
    )


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n")


def print_header(config: dict[str, Any]) -> None:
    print("=" * 112, flush=True)
    print("SEC EDGAR bounded historical pipeline", flush=True)
    for key in [
        "run_id",
        "start_date",
        "end_date",
        "job_count",
        "download_concurrency",
        "header_concurrency",
        "sec_request_min_interval_seconds",
        "artifact_root",
        "normalized_root",
        "output_root",
        "limit_days",
        "limit_files_per_day",
        "persist_nc_files",
        "delete_archive_after_parse",
        "no_header_fetch",
    ]:
        print(f"{key}={config.get(key)}", flush=True)
    print(f"secret_status={config.get('secret_status')}", flush=True)
    print(f"loaded_env_files={config.get('loaded_env_files')}", flush=True)
    print("=" * 112, flush=True)


def print_progress(total_days: int, totals: dict[str, int], started: float, last_result: PipelineDayResult) -> None:
    elapsed = max(0.001, time.perf_counter() - started)
    processed = totals["parsed_days"] + totals["failed_days"]
    gib = totals["archive_bytes"] / (1024**3)
    print(
        "progress "
        f"{processed:,}/{total_days:,} days "
        f"downloaded={totals['downloaded_days']:,} parsed={totals['parsed_days']:,} failed={totals['failed_days']:,} "
        f"archives={gib:.2f} GiB submissions={totals['submissions']:,} documents={totals['documents']:,} "
        f"headers_ok={totals['header_success']:,} headers_failed={totals['header_failed']:,} "
        f"last={last_result.archive_date}:{last_result.status} "
        f"last_download={last_result.download_seconds:.1f}s last_parse={last_result.parse_seconds:.1f}s "
        f"elapsed={elapsed:.1f}s",
        flush=True,
    )


if __name__ == "__main__":
    main()
