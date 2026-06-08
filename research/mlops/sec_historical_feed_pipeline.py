from __future__ import annotations

import argparse
import concurrent.futures
import gzip
import json
import os
import shutil
import sys
import tarfile
import time
import traceback
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
    loaded_env_files = load_env_files(discover_env_files(REPO_ROOT))
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
    print_header(config)
    append_jsonl(report_path, config)

    if args.dry_run:
        for job in jobs:
            append_jsonl(report_path, {"type": "planned_job", "run_id": run_id, "job": asdict(job)})
        print("dry_run=1, no archives downloaded and no normalized rows written", flush=True)
        return

    run_pipeline(args, jobs, temp_root, normalized_root, report_path, run_id)


def run_pipeline(
    args: argparse.Namespace,
    jobs: list[DayJob],
    temp_root: Path,
    normalized_root: Path,
    report_path: Path,
    run_id: str,
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
            print(f"[{jobs[next_index].archive_date}] queued for SSD staging", flush=True)
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
                        result = parse_one_archive(downloaded, args, normalized_root, sec_request_limiter, copy_pool)
                    except Exception as exc:  # noqa: BLE001
                        result = failed_result(downloaded.job, exc, downloaded)
                append_jsonl(report_path, {"type": "day", "run_id": run_id, "result": asdict(result)})
                if result.status == "ok":
                    totals["parsed_days"] += 1
                    totals["archive_bytes"] += result.archive_bytes
                    totals["submissions"] += result.submissions
                    totals["documents"] += result.documents
                    totals["header_success"] += result.header_success
                    totals["header_failed"] += result.header_failed
                    totals["hdd_copied_days"] += 1 if result.hdd_copy_status in {"ok", "existing"} else 0
                    totals["ssd_cleaned_days"] += 1 if result.cleanup_status.startswith("ssd_deleted") else 0
                else:
                    totals["failed_days"] += 1
                print_progress(len(jobs), totals, started, result)
                while next_index < len(jobs) and len(active) < download_concurrency:
                    slot = available_download_slots.pop(0)
                    print(f"[{jobs[next_index].archive_date}] queued for SSD staging", flush=True)
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
    print("\nsummary=" + json.dumps(summary, sort_keys=True), flush=True)


def stage_archive_on_ssd(
    job: DayJob,
    temp_root: Path,
    limiter: RateLimiter,
    progress_interval_seconds: float,
    progress_file_interval_bytes: int,
    progress_record_interval: int,
    download_progress_bars: bool,
    progress_position: int,
) -> DownloadedArchive:
    started = time.perf_counter()
    temp_archive_path = temp_archive_path_for_job(job, temp_root)
    hdd_archive_path = Path(job.archive_path)
    source = "sec_download"
    label = f"[{job.archive_date}]"
    print(f"{label} staging: temp={temp_archive_path} hdd={hdd_archive_path}", flush=True)

    if temp_archive_path.exists() and temp_archive_path.stat().st_size > 0 and not job.force_redownload:
        try:
            print(f"{label} staging: validating existing SSD temp archive ({format_bytes(temp_archive_path.stat().st_size)})", flush=True)
            validate_archive_integrity(temp_archive_path, f"{label} validate-ssd", progress_record_interval, progress_interval_seconds)
            source = "ssd_existing"
        except ArchiveIntegrityError:
            print(f"{label} staging: deleting corrupt SSD temp archive", flush=True)
            temp_archive_path.unlink(missing_ok=True)

    if not temp_archive_path.exists() and hdd_archive_path.exists() and hdd_archive_path.stat().st_size > 0 and not job.force_redownload:
        try:
            print(f"{label} staging: validating existing HDD archive ({format_bytes(hdd_archive_path.stat().st_size)})", flush=True)
            validate_archive_integrity(hdd_archive_path, f"{label} validate-hdd", progress_record_interval, progress_interval_seconds)
            copy_file_verified(
                hdd_archive_path,
                temp_archive_path,
                f"{label} copy-hdd-to-ssd",
                progress_file_interval_bytes,
                progress_interval_seconds,
            )
            validate_archive_integrity(temp_archive_path, f"{label} validate-ssd", progress_record_interval, progress_interval_seconds)
            source = "hdd_existing"
        except ArchiveIntegrityError:
            print(f"{label} staging: deleting corrupt cached archive and redownloading", flush=True)
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
        )
        validate_archive_integrity(temp_archive_path, f"{label} validate-download", progress_record_interval, progress_interval_seconds)

    elapsed = round(time.perf_counter() - started, 3)
    print(
        f"{label} staging: ready source={source} size={format_bytes(temp_archive_path.stat().st_size)} elapsed={elapsed:.1f}s",
        flush=True,
    )
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
                bar = make_download_bar(
                    enabled=download_progress_bars,
                    archive_date=job.archive_date,
                    attempt=attempt + 1,
                    max_attempts=job.max_retries + 1,
                    total_bytes=expected_bytes,
                    position=progress_position,
                )
                print(
                    f"[{job.archive_date}] download: attempt={attempt + 1}/{job.max_retries + 1} "
                    f"expected={format_bytes(expected_bytes) if expected_bytes else 'unknown'} target={target_path}",
                    flush=True,
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
                        now = time.perf_counter()
                        if bar is None and (
                            written - last_reported_bytes >= progress_file_interval_bytes
                            or now - last_progress >= progress_interval_seconds
                        ):
                            pct = f" {written / expected_bytes:.1%}" if expected_bytes else ""
                            print(
                                f"[{job.archive_date}] download: {format_bytes(written)}{pct} elapsed={now - started:.1f}s",
                                flush=True,
                            )
                            last_progress = now
                            last_reported_bytes = written
                finally:
                    if bar is not None:
                        bar.close()
            if expected_length and written != int(expected_length):
                part_path.unlink(missing_ok=True)
                raise RuntimeError(f"incomplete archive download: expected {expected_length} bytes, wrote {written} bytes")
            part_path.replace(target_path)
            print(
                f"[{job.archive_date}] download: complete size={format_bytes(written)} elapsed={time.perf_counter() - started:.1f}s",
                flush=True,
            )
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
) -> int:
    try:
        nc_members = 0
        started = time.perf_counter()
        last_progress = started
        with tarfile.open(archive_path, "r:gz") as tar:
            for member in tar:
                if member.isfile() and member.name.lower().endswith(".nc"):
                    nc_members += 1
                    now = time.perf_counter()
                    if progress_label and (
                        (progress_every > 0 and nc_members % progress_every == 0)
                        or now - last_progress >= progress_interval_seconds
                    ):
                        print(f"{progress_label}: {nc_members:,} .nc members scanned elapsed={now - started:.1f}s", flush=True)
                        last_progress = now
        if nc_members <= 0:
            raise ArchiveIntegrityError(f"archive contains no .nc members: {archive_path}")
        if progress_label:
            print(f"{progress_label}: complete members={nc_members:,} elapsed={time.perf_counter() - started:.1f}s", flush=True)
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
) -> tuple[str, float]:
    started = time.perf_counter()
    if hdd_archive_path.exists() and hdd_archive_path.stat().st_size == temp_archive_path.stat().st_size:
        if sha256_file(hdd_archive_path) == expected_sha256:
            if progress_label:
                print(f"{progress_label}: HDD archive already verified", flush=True)
            return "existing", round(time.perf_counter() - started, 3)
    copy_file_verified(temp_archive_path, hdd_archive_path, progress_label, progress_file_interval_bytes, progress_interval_seconds)
    actual_sha256 = sha256_file(hdd_archive_path)
    if actual_sha256 != expected_sha256:
        hdd_archive_path.unlink(missing_ok=True)
        raise RuntimeError(f"HDD archive checksum mismatch for {hdd_archive_path}")
    if progress_label:
        print(f"{progress_label}: complete elapsed={time.perf_counter() - started:.1f}s", flush=True)
    return "ok", round(time.perf_counter() - started, 3)


def copy_file_verified(
    source: Path,
    target: Path,
    progress_label: str = "",
    progress_file_interval_bytes: int = 64 * 1024 * 1024,
    progress_interval_seconds: float = 10.0,
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
        print(f"{progress_label}: copying {format_bytes(total_bytes)} {source} -> {target}", flush=True)
    with source.open("rb") as src, part_path.open("wb") as dst:
        while True:
            chunk = src.read(1024 * 1024 * 16)
            if not chunk:
                break
            dst.write(chunk)
            copied += len(chunk)
            now = time.perf_counter()
            if progress_label and (
                copied - last_reported_bytes >= progress_file_interval_bytes
                or now - last_progress >= progress_interval_seconds
            ):
                print(
                    f"{progress_label}: {format_bytes(copied)} {copied / total_bytes:.1%} elapsed={now - started:.1f}s",
                    flush=True,
                )
                last_progress = now
                last_reported_bytes = copied
    if part_path.stat().st_size != source.stat().st_size:
        part_path.unlink(missing_ok=True)
        raise RuntimeError(f"copy size mismatch: {source} -> {target}")
    part_path.replace(target)
    if progress_label:
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
) -> PipelineDayResult:
    started = time.perf_counter()
    job = downloaded.job
    temp_archive_path = Path(downloaded.temp_archive_path)
    hdd_archive_path = Path(downloaded.hdd_archive_path)
    label = f"[{job.archive_date}]"
    progress_interval = max(1.0, args.progress_interval_seconds)
    progress_file_interval_bytes = int(max(1.0, args.progress_file_interval_mib) * 1024 * 1024)
    progress_record_interval = max(1, args.progress_record_interval)
    print(f"{label} day: start parse/write pipeline source={downloaded.archive_source}", flush=True)
    copy_future = copy_pool.submit(
        copy_archive_to_hdd,
        temp_archive_path,
        hdd_archive_path,
        downloaded.archive_sha256,
        f"{label} copy-ssd-to-hdd",
        progress_file_interval_bytes,
        progress_interval,
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
    )
    timestamps = fetch_headers_for_submissions(parsed, job, sec_request_limiter, f"{label}", max(1, progress_record_interval // 2), progress_interval)
    submission_rows = [merge_submission_timestamp(item, timestamps.get(item.accession_number)) for item in parsed]
    print(f"{label} write: submissions={len(submission_rows):,} documents={len(documents):,} headers={len(timestamps):,}", flush=True)
    write_rows(submissions_path, submission_rows)
    write_rows(documents_path, [asdict(item) for item in documents])
    write_rows(headers_path, [asdict(item) for item in timestamps.values()])
    print(f"{label} write: normalized files written to {normalized_day_dir}", flush=True)
    parse_seconds = round(time.perf_counter() - started, 3)
    hdd_copy_status, hdd_copy_seconds = copy_future.result()
    print(f"{label} copy: hdd_status={hdd_copy_status} copy_seconds={hdd_copy_seconds:.1f}", flush=True)
    cleanup_status = cleanup_archives(temp_archive_path, hdd_archive_path, hdd_copy_status, args.delete_archive_after_parse)
    print(f"{label} cleanup: {cleanup_status}", flush=True)

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


def print_header(config: dict[str, Any]) -> None:
    print("=" * 112, flush=True)
    print("SEC EDGAR bounded historical pipeline", flush=True)
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
        f"staged={totals['staged_days']:,} parsed={totals['parsed_days']:,} failed={totals['failed_days']:,} "
        f"archives={gib:.2f} GiB submissions={totals['submissions']:,} documents={totals['documents']:,} "
        f"headers_ok={totals['header_success']:,} headers_failed={totals['header_failed']:,} "
        f"hdd_archived={totals['hdd_copied_days']:,} ssd_cleaned={totals['ssd_cleaned_days']:,} "
        f"last={last_result.archive_date}:{last_result.status} "
        f"last_download={last_result.download_seconds:.1f}s last_copy={last_result.hdd_copy_seconds:.1f}s "
        f"last_parse={last_result.parse_seconds:.1f}s "
        f"elapsed={elapsed:.1f}s",
        flush=True,
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
