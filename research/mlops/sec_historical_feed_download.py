from __future__ import annotations

import argparse
import concurrent.futures
import gzip
import hashlib
import json
import os
import re
import sys
import tarfile
import threading
import time
import traceback
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from urllib import error, request
from zoneinfo import ZoneInfo


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402


DEFAULT_ARTIFACT_ROOT_WIN = Path("D:/market-data/sec_edgar_feed")
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_edgar_feed")
DEFAULT_SEC_USER_AGENT = "QuantResearchWorkbench SEC historical feed ingest contact@example.com"
SEC_BASE_URL = "https://www.sec.gov/Archives/edgar"
SEC_ET = ZoneInfo("America/New_York")
RETRY_HTTP_CODES = {408, 425, 429, 500, 502, 503, 504}


class MissingArchiveError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DayJob:
    archive_date: str
    archive_url: str
    archive_path: str
    extract_dir: str
    user_agent: str
    request_min_interval_seconds: float
    request_timeout_seconds: float
    max_retries: int
    retry_base_seconds: float
    header_concurrency: int
    limit_files: int
    force_redownload: bool
    download_only: bool
    persist_nc_files: bool
    no_header_fetch: bool


@dataclass(frozen=True, slots=True)
class ExistingDirJob:
    archive_date: str
    extract_dir: str
    user_agent: str
    request_min_interval_seconds: float
    request_timeout_seconds: float
    max_retries: int
    retry_base_seconds: float
    header_concurrency: int
    limit_files: int
    no_header_fetch: bool


@dataclass(frozen=True, slots=True)
class ParsedSubmission:
    archive_date: str
    accession_number: str
    accession_cik: str
    form_type: str
    filing_date: str
    period: str
    public_document_count: int
    parsed_document_count: int
    nc_artifact_path: str
    nc_byte_size: int
    nc_sha256: str
    subject_company_json: str
    filed_by_json: str
    issuer_json: str
    reporting_owner_json: str
    parse_status: str
    parse_error: str


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    archive_date: str
    accession_number: str
    sequence: int
    document_type: str
    filename: str
    description: str
    content_format: str
    text_byte_length: int
    text_sha256: str
    payload_prefix: str


@dataclass(frozen=True, slots=True)
class HeaderTimestamp:
    accession_number: str
    header_url: str
    hdr_artifact_path: str
    hdr_byte_size: int
    hdr_sha256: str
    accepted_at_edgar_raw: str
    accepted_at_et: str
    accepted_at_utc: str
    timestamp_source: str
    fetch_status: str
    fetch_error: str


@dataclass(frozen=True, slots=True)
class DayResult:
    archive_date: str
    archive_url: str
    archive_path: str
    extract_dir: str
    archive_bytes: int
    archive_sha256: str
    nc_files: int
    submissions: int
    documents: int
    header_success: int
    header_failed: int
    wall_seconds: float
    status: str
    error: str = ""


class RateLimiter:
    def __init__(self, min_interval_seconds: float) -> None:
        self.min_interval_seconds = max(0.0, min_interval_seconds)
        self._lock = threading.Lock()
        self._next_at = 0.0

    def wait(self) -> None:
        if self.min_interval_seconds <= 0:
            return
        with self._lock:
            now = time.perf_counter()
            wait_seconds = self._next_at - now
            if wait_seconds > 0:
                time.sleep(wait_seconds)
                now = time.perf_counter()
            self._next_at = now + self.min_interval_seconds


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download SEC EDGAR daily Feed archives, extract .nc SGML containers, "
            "parse filing/document metadata, and enrich each accession with SEC accepted_at from .hdr.sgml."
        )
    )
    parser.add_argument("--start-date", help="Inclusive archive date, YYYY-MM-DD.")
    parser.add_argument("--end-date", help="Exclusive archive date, YYYY-MM-DD.")
    parser.add_argument("--existing-extracted-dir", help="Use an already extracted directory of .nc files instead of downloading archives.")
    parser.add_argument("--existing-archive-date", help="Archive date for --existing-extracted-dir, YYYY-MM-DD.")
    parser.add_argument("--artifact-root-win", default=os.environ.get("SEC_HISTORICAL_ARTIFACT_ROOT_WIN", str(DEFAULT_ARTIFACT_ROOT_WIN)))
    parser.add_argument("--output-root-win", default=os.environ.get("SEC_HISTORICAL_OUTPUT_ROOT_WIN", str(DEFAULT_OUTPUT_ROOT_WIN)))
    parser.add_argument("--archive-concurrency", type=int, default=int(os.environ.get("SEC_ARCHIVE_CONCURRENCY", "1")))
    parser.add_argument("--header-concurrency", type=int, default=int(os.environ.get("SEC_HEADER_CONCURRENCY", "8")))
    parser.add_argument("--sec-request-min-interval-seconds", type=float, default=float(os.environ.get("SEC_REQUEST_MIN_INTERVAL_SECONDS", "0.11")))
    parser.add_argument("--request-timeout-seconds", type=float, default=float(os.environ.get("SEC_REQUEST_TIMEOUT_SECONDS", "60")))
    parser.add_argument("--max-retries", type=int, default=int(os.environ.get("SEC_MAX_RETRIES", "4")))
    parser.add_argument("--retry-base-seconds", type=float, default=float(os.environ.get("SEC_RETRY_BASE_SECONDS", "1.5")))
    parser.add_argument("--limit-days", type=int, default=0, help="Optional smoke-test cap on archive days.")
    parser.add_argument("--limit-files-per-day", type=int, default=0, help="Optional smoke-test cap on .nc files parsed per day.")
    parser.add_argument("--force-redownload", action="store_true", help="Redownload archive files even when already present.")
    parser.add_argument("--download-only", action="store_true", help="Only download compressed daily .nc.tar.gz archives; do not decompress, parse, or fetch headers.")
    parser.add_argument("--persist-nc-files", action="store_true", help="When parsing downloaded archives, also write individual .nc files to disk. By default parsing streams from tar.gz without expanding all files.")
    parser.add_argument("--no-header-fetch", action="store_true", help="Parse .nc content only; do not fetch .hdr.sgml accepted timestamps.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned jobs without downloading or parsing.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    loaded_env_files = load_env_files(discover_env_files(REPO_ROOT))
    validate_args(args)

    artifact_root = Path(args.artifact_root_win)
    output_root = Path(args.output_root_win)
    output_root.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    report_path = output_root / f"sec_feed_historical_{run_id}.jsonl"
    submissions_path = output_root / f"sec_feed_submissions_{run_id}.jsonl"
    documents_path = output_root / f"sec_feed_documents_{run_id}.jsonl"
    headers_path = output_root / f"sec_feed_headers_{run_id}.jsonl"
    user_agent = sec_user_agent()

    if args.existing_extracted_dir:
        jobs: list[DayJob | ExistingDirJob] = [
            ExistingDirJob(
                archive_date=args.existing_archive_date,
                extract_dir=str(Path(args.existing_extracted_dir)),
                user_agent=user_agent,
                request_min_interval_seconds=max(0.0, args.sec_request_min_interval_seconds),
                request_timeout_seconds=max(1.0, args.request_timeout_seconds),
                max_retries=max(0, args.max_retries),
                retry_base_seconds=max(0.1, args.retry_base_seconds),
                header_concurrency=max(1, args.header_concurrency),
                limit_files=max(0, args.limit_files_per_day),
                no_header_fetch=args.no_header_fetch,
            )
        ]
    else:
        discovery_limiter = RateLimiter(max(0.0, args.sec_request_min_interval_seconds))
        days = discover_available_archive_days(
            parse_date(args.start_date),
            parse_date(args.end_date),
            user_agent,
            max(1.0, args.request_timeout_seconds),
            max(0, args.max_retries),
            max(0.1, args.retry_base_seconds),
            discovery_limiter,
        )
        if args.limit_days:
            days = days[: max(0, args.limit_days)]
        jobs = [
            build_day_job(
                day,
                artifact_root,
                user_agent,
                max(0.0, args.sec_request_min_interval_seconds),
                max(1.0, args.request_timeout_seconds),
                max(0, args.max_retries),
                max(0.1, args.retry_base_seconds),
                max(1, args.header_concurrency),
                max(0, args.limit_files_per_day),
                args.force_redownload,
                args.download_only,
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
        "existing_extracted_dir": args.existing_extracted_dir or "",
        "existing_archive_date": args.existing_archive_date or "",
        "artifact_root": str(artifact_root),
        "output_root": str(output_root),
        "report_path": str(report_path),
        "submissions_path": str(submissions_path),
        "documents_path": str(documents_path),
        "headers_path": str(headers_path),
        "archive_concurrency": effective_archive_concurrency(args),
        "header_concurrency": max(1, args.header_concurrency),
        "sec_request_min_interval_seconds": max(0.0, args.sec_request_min_interval_seconds),
        "request_timeout_seconds": max(1.0, args.request_timeout_seconds),
        "max_retries": max(0, args.max_retries),
        "retry_base_seconds": max(0.1, args.retry_base_seconds),
        "limit_days": max(0, args.limit_days),
        "limit_files_per_day": max(0, args.limit_files_per_day),
        "download_only": args.download_only,
        "persist_nc_files": args.persist_nc_files,
        "no_header_fetch": args.no_header_fetch,
        "user_agent_present": bool(os.environ.get("SEC_USER_AGENT") or os.environ.get("SEC_EDGAR_USER_AGENT") or os.environ.get("NEWS_SEC_USER_AGENT")),
        "secret_status": secret_status(["SEC_USER_AGENT", "SEC_EDGAR_USER_AGENT", "NEWS_SEC_USER_AGENT"]),
        "loaded_env_files": [str(path) for path in loaded_env_files],
        "job_count": len(jobs),
        "job_discovery": "sec_feed_quarter_directory_listing" if not args.existing_extracted_dir else "existing_extracted_dir",
    }
    print_header(config)
    append_jsonl(report_path, config)

    if args.dry_run:
        for job in jobs:
            append_jsonl(report_path, {"type": "planned_job", "run_id": run_id, "job": asdict(job)})
        print("dry_run=1, no archives downloaded and no files parsed", flush=True)
        return

    started = time.perf_counter()
    completed = 0
    failed = 0
    totals = {
        "nc_files": 0,
        "submissions": 0,
        "documents": 0,
        "header_success": 0,
        "header_failed": 0,
        "archive_bytes": 0,
    }

    archive_concurrency = effective_archive_concurrency(args)
    with concurrent.futures.ThreadPoolExecutor(max_workers=archive_concurrency) as pool:
        futures = {
            pool.submit(
                process_existing_dir_job if isinstance(job, ExistingDirJob) else process_day_job,
                job,
                submissions_path,
                documents_path,
                headers_path,
            ): job
            for job in jobs
        }
        for future in concurrent.futures.as_completed(futures):
            job = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001
                archive_date = job.archive_date
                result = DayResult(
                    archive_date=archive_date,
                    archive_url=getattr(job, "archive_url", ""),
                    archive_path=getattr(job, "archive_path", ""),
                    extract_dir=job.extract_dir,
                    archive_bytes=0,
                    archive_sha256="",
                    nc_files=0,
                    submissions=0,
                    documents=0,
                    header_success=0,
                    header_failed=0,
                    wall_seconds=0.0,
                    status="failed",
                    error=f"{exc!r}\n{traceback.format_exc()}",
                )
            append_jsonl(report_path, {"type": "day", "run_id": run_id, "result": asdict(result)})
            if result.status in {"ok", "downloaded", "missing_archive"}:
                completed += 1
            else:
                failed += 1
            for key in totals:
                totals[key] += getattr(result, key)
            print_progress(len(jobs), completed, failed, totals, started)

    summary = {
        "type": "summary",
        "run_id": run_id,
        "status": "failed" if failed else "ok",
        "completed_days": completed,
        "failed_days": failed,
        "wall_seconds": round(time.perf_counter() - started, 3),
        **totals,
    }
    append_jsonl(report_path, summary)
    print("\nsummary=" + json.dumps(summary, sort_keys=True), flush=True)


def validate_args(args: argparse.Namespace) -> None:
    if args.existing_extracted_dir:
        if not args.existing_archive_date:
            raise SystemExit("--existing-archive-date is required with --existing-extracted-dir")
        if not Path(args.existing_extracted_dir).exists():
            raise SystemExit(f"--existing-extracted-dir does not exist: {args.existing_extracted_dir}")
        parse_date(args.existing_archive_date)
        return
    if not args.start_date or not args.end_date:
        raise SystemExit("--start-date and --end-date are required unless --existing-extracted-dir is used")
    start = parse_date(args.start_date)
    end = parse_date(args.end_date)
    if end <= start:
        raise SystemExit("--end-date must be later than --start-date")


def effective_archive_concurrency(args: argparse.Namespace) -> int:
    if args.existing_extracted_dir:
        return 1
    if args.download_only:
        return max(1, args.archive_concurrency)
    return 1


def discover_available_archive_days(
    start: date,
    end: date,
    user_agent: str,
    request_timeout_seconds: float,
    max_retries: int,
    retry_base_seconds: float,
    limiter: RateLimiter,
) -> list[date]:
    available: set[date] = set()
    for year, quarter in quarters_between(start, end):
        url = f"{SEC_BASE_URL}/Feed/{year}/{quarter}/"
        body = fetch_url(url, user_agent, request_timeout_seconds, max_retries, retry_base_seconds, limiter)
        text = body.decode("utf-8", errors="replace")
        for match in re.finditer(r"(?P<day>[0-9]{8})\.nc\.tar\.gz", text):
            archive_day = datetime.strptime(match.group("day"), "%Y%m%d").date()
            if start <= archive_day < end:
                available.add(archive_day)
    return sorted(available)


def quarters_between(start: date, end: date) -> Iterable[tuple[int, str]]:
    current = date(start.year, ((start.month - 1) // 3) * 3 + 1, 1)
    seen: set[tuple[int, str]] = set()
    while current < end:
        key = (current.year, quarter_name(current))
        if key not in seen:
            seen.add(key)
            yield key
        month = current.month + 3
        year = current.year + ((month - 1) // 12)
        month = ((month - 1) % 12) + 1
        current = date(year, month, 1)


def process_day_job(job: DayJob, submissions_path: Path, documents_path: Path, headers_path: Path) -> DayResult:
    started = time.perf_counter()
    archive_path = Path(job.archive_path)
    extract_dir = Path(job.extract_dir)
    limiter = RateLimiter(job.request_min_interval_seconds)

    try:
        try:
            download_archive(job, limiter)
        except MissingArchiveError as exc:
            return DayResult(
                archive_date=job.archive_date,
                archive_url=job.archive_url,
                archive_path=str(archive_path),
                extract_dir="",
                archive_bytes=0,
                archive_sha256="",
                nc_files=0,
                submissions=0,
                documents=0,
                header_success=0,
                header_failed=0,
                wall_seconds=round(time.perf_counter() - started, 3),
                status="missing_archive",
                error=str(exc),
            )
        archive_bytes = archive_path.stat().st_size
        archive_sha = sha256_file(archive_path)
        if job.download_only:
            return DayResult(
                archive_date=job.archive_date,
                archive_url=job.archive_url,
                archive_path=str(archive_path),
                extract_dir="",
                archive_bytes=archive_bytes,
                archive_sha256=archive_sha,
                nc_files=0,
                submissions=0,
                documents=0,
                header_success=0,
                header_failed=0,
                wall_seconds=round(time.perf_counter() - started, 3),
                status="downloaded",
            )
        parsed, documents = parse_nc_archive(job.archive_date, archive_path, extract_dir, job.limit_files, job.persist_nc_files)
        timestamps = fetch_headers_for_submissions(parsed, job, limiter)
        write_rows(submissions_path, [merge_submission_timestamp(item, timestamps.get(item.accession_number)) for item in parsed])
        write_rows(documents_path, [asdict(item) for item in documents])
        write_rows(headers_path, [asdict(item) for item in timestamps.values()])
        return DayResult(
            archive_date=job.archive_date,
            archive_url=job.archive_url,
            archive_path=str(archive_path),
            extract_dir=str(extract_dir) if job.persist_nc_files else "",
            archive_bytes=archive_bytes,
            archive_sha256=archive_sha,
            nc_files=len(parsed),
            submissions=len(parsed),
            documents=len(documents),
            header_success=sum(1 for item in timestamps.values() if item.fetch_status == "ok"),
            header_failed=sum(1 for item in timestamps.values() if item.fetch_status == "failed"),
            wall_seconds=round(time.perf_counter() - started, 3),
            status="ok",
        )
    except Exception as exc:  # noqa: BLE001
        return DayResult(
            archive_date=job.archive_date,
            archive_url=job.archive_url,
            archive_path=str(archive_path),
            extract_dir=str(extract_dir),
            archive_bytes=archive_path.stat().st_size if archive_path.exists() else 0,
            archive_sha256=sha256_file(archive_path) if archive_path.exists() else "",
            nc_files=len(list(extract_dir.glob("*.nc"))) if extract_dir.exists() else 0,
            submissions=0,
            documents=0,
            header_success=0,
            header_failed=0,
            wall_seconds=round(time.perf_counter() - started, 3),
            status="failed",
            error=f"{exc!r}\n{traceback.format_exc()}",
        )


def process_existing_dir_job(job: ExistingDirJob, submissions_path: Path, documents_path: Path, headers_path: Path) -> DayResult:
    started = time.perf_counter()
    extract_dir = Path(job.extract_dir)
    limiter = RateLimiter(job.request_min_interval_seconds)
    parsed, documents = parse_nc_directory(job.archive_date, extract_dir, job.limit_files)
    timestamps = fetch_headers_for_submissions(parsed, job, limiter)
    write_rows(submissions_path, [merge_submission_timestamp(item, timestamps.get(item.accession_number)) for item in parsed])
    write_rows(documents_path, [asdict(item) for item in documents])
    write_rows(headers_path, [asdict(item) for item in timestamps.values()])
    return DayResult(
        archive_date=job.archive_date,
        archive_url="",
        archive_path="",
        extract_dir=str(extract_dir),
        archive_bytes=0,
        archive_sha256="",
        nc_files=len(parsed),
        submissions=len(parsed),
        documents=len(documents),
        header_success=sum(1 for item in timestamps.values() if item.fetch_status == "ok"),
        header_failed=sum(1 for item in timestamps.values() if item.fetch_status == "failed"),
        wall_seconds=round(time.perf_counter() - started, 3),
        status="ok",
    )


def download_archive(job: DayJob, limiter: RateLimiter) -> None:
    archive_path = Path(job.archive_path)
    if archive_path.exists() and archive_path.stat().st_size > 0 and not job.force_redownload:
        return
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = archive_path.with_suffix(archive_path.suffix + ".part")
    body = fetch_url(job.archive_url, job.user_agent, job.request_timeout_seconds, job.max_retries, job.retry_base_seconds, limiter)
    tmp_path.write_bytes(body)
    tmp_path.replace(archive_path)


def parse_nc_archive(
    archive_date: str,
    archive_path: Path,
    extract_dir: Path,
    limit_files: int,
    persist_nc_files: bool,
    progress_label: str = "",
    progress_every: int = 500,
    progress_interval_seconds: float = 10.0,
) -> tuple[list[ParsedSubmission], list[ParsedDocument]]:
    if persist_nc_files:
        extract_dir.mkdir(parents=True, exist_ok=True)
    root = extract_dir.resolve()
    submissions: list[ParsedSubmission] = []
    documents: list[ParsedDocument] = []
    started = time.perf_counter()
    last_progress = started
    with tarfile.open(archive_path, "r:gz") as tar:
        members = [member for member in tar.getmembers() if member.isfile() and member.name.lower().endswith(".nc")]
        members.sort(key=lambda member: member.name)
        if limit_files:
            members = members[:limit_files]
        total_members = len(members)
        if progress_label:
            print(f"{progress_label} parse: discovered {total_members:,} .nc members", flush=True)
        for index, member in enumerate(members, start=1):
            handle = tar.extractfile(member)
            if handle is None:
                continue
            raw = handle.read()
            if persist_nc_files:
                target = (extract_dir / safe_tar_member_name(member.name)).resolve()
                if not str(target).startswith(str(root)):
                    raise RuntimeError(f"refusing unsafe tar member path: {member.name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
                artifact_path = target
            else:
                artifact_path = virtual_nc_artifact_path(archive_path, member.name)
            submission, docs = parse_nc_bytes(archive_date, artifact_path, raw)
            submissions.append(submission)
            documents.extend(docs)
            now = time.perf_counter()
            if progress_label and (
                index == total_members
                or (progress_every > 0 and index % progress_every == 0)
                or now - last_progress >= progress_interval_seconds
            ):
                print(
                    f"{progress_label} parse: {index:,}/{total_members:,} members "
                    f"submissions={len(submissions):,} documents={len(documents):,} elapsed={now - started:.1f}s",
                    flush=True,
                )
                last_progress = now
    return submissions, documents


def parse_nc_directory(archive_date: str, extract_dir: Path, limit_files: int) -> tuple[list[ParsedSubmission], list[ParsedDocument]]:
    files = sorted(extract_dir.glob("*.nc"))
    if limit_files:
        files = files[:limit_files]
    submissions: list[ParsedSubmission] = []
    documents: list[ParsedDocument] = []
    for path in files:
        submission, docs = parse_nc_bytes(archive_date, path, path.read_bytes())
        submissions.append(submission)
        documents.extend(docs)
    return submissions, documents


def parse_nc_bytes(archive_date: str, path: Path, raw: bytes) -> tuple[ParsedSubmission, list[ParsedDocument]]:
    nc_sha = hashlib.sha256(raw).hexdigest()
    text = raw.decode("utf-8", errors="replace")
    header_text = text.split("<DOCUMENT>", 1)[0]
    accession = first_tag(header_text, "ACCESSION-NUMBER") or path.stem
    form_type = first_tag(header_text, "TYPE")
    public_document_count = parse_int(first_tag(header_text, "PUBLIC-DOCUMENT-COUNT"))
    docs = parse_document_blocks(archive_date, accession, text)
    parse_status = "ok"
    parse_error = ""
    if not accession:
        parse_status = "missing_accession"
        parse_error = "missing ACCESSION-NUMBER"
    return (
        ParsedSubmission(
            archive_date=archive_date,
            accession_number=accession,
            accession_cik=accession[:10] if len(accession) >= 10 else "",
            form_type=form_type,
            filing_date=yyyymmdd_to_date(first_tag(header_text, "FILING-DATE")),
            period=yyyymmdd_to_date(first_tag(header_text, "PERIOD")),
            public_document_count=public_document_count,
            parsed_document_count=len(docs),
            nc_artifact_path=str(path),
            nc_byte_size=len(raw),
            nc_sha256=nc_sha,
            subject_company_json=section_tags_json(header_text, "SUBJECT-COMPANY"),
            filed_by_json=section_tags_json(header_text, "FILED-BY"),
            issuer_json=section_tags_json(header_text, "ISSUER"),
            reporting_owner_json=section_tags_json(header_text, "REPORTING-OWNER"),
            parse_status=parse_status,
            parse_error=parse_error,
        ),
        docs,
    )


def parse_document_blocks(archive_date: str, accession: str, text: str) -> list[ParsedDocument]:
    docs: list[ParsedDocument] = []
    for block in re.findall(r"<DOCUMENT>\s*(.*?)\s*</DOCUMENT>", text, flags=re.S | re.I):
        text_match = re.search(r"<TEXT>\s*(.*)", block, flags=re.S | re.I)
        payload = text_match.group(1) if text_match else ""
        payload = re.sub(r"</TEXT>\s*$", "", payload, flags=re.S | re.I)
        payload_bytes = payload.encode("utf-8", errors="replace")
        filename = first_tag(block, "FILENAME")
        docs.append(
            ParsedDocument(
                archive_date=archive_date,
                accession_number=accession,
                sequence=parse_int(first_tag(block, "SEQUENCE")),
                document_type=first_tag(block, "TYPE"),
                filename=filename,
                description=first_tag(block, "DESCRIPTION"),
                content_format=detect_content_format(filename, payload),
                text_byte_length=len(payload_bytes),
                text_sha256=hashlib.sha256(payload_bytes).hexdigest() if payload else "",
                payload_prefix=normalize_space(payload[:500]),
            )
        )
    return docs


def fetch_headers_for_submissions(
    submissions: list[ParsedSubmission],
    job: DayJob | ExistingDirJob,
    limiter: RateLimiter,
    progress_label: str = "",
    progress_every: int = 200,
    progress_interval_seconds: float = 10.0,
) -> dict[str, HeaderTimestamp]:
    if job.no_header_fetch:
        if progress_label:
            print(f"{progress_label} headers: skipped for {len(submissions):,} submissions", flush=True)
        return {
            item.accession_number: HeaderTimestamp(
                accession_number=item.accession_number,
                header_url=hdr_url_for_accession(item.accession_number),
                hdr_artifact_path="",
                hdr_byte_size=0,
                hdr_sha256="",
                accepted_at_edgar_raw="",
                accepted_at_et="",
                accepted_at_utc="",
                timestamp_source="not_fetched",
                fetch_status="skipped",
                fetch_error="",
            )
            for item in submissions
        }
    output: dict[str, HeaderTimestamp] = {}
    total = len(submissions)
    started = time.perf_counter()
    last_progress = started
    if progress_label:
        print(f"{progress_label} headers: fetching accepted_at for {total:,} submissions", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=job.header_concurrency) as pool:
        futures = {pool.submit(fetch_header_timestamp, item, job, limiter): item for item in submissions}
        for completed, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            item = futures[future]
            try:
                output[item.accession_number] = future.result()
            except Exception as exc:  # noqa: BLE001
                output[item.accession_number] = failed_header(item.accession_number, repr(exc))
            now = time.perf_counter()
            if progress_label and (
                completed == total
                or (progress_every > 0 and completed % progress_every == 0)
                or now - last_progress >= progress_interval_seconds
            ):
                ok = sum(1 for timestamp in output.values() if timestamp.fetch_status == "ok")
                failed = sum(1 for timestamp in output.values() if timestamp.fetch_status == "failed")
                missing = sum(1 for timestamp in output.values() if timestamp.fetch_status == "missing_acceptance")
                print(
                    f"{progress_label} headers: {completed:,}/{total:,} done "
                    f"ok={ok:,} missing={missing:,} failed={failed:,} elapsed={now - started:.1f}s",
                    flush=True,
                )
                last_progress = now
    return output


def fetch_header_timestamp(submission: ParsedSubmission, job: DayJob | ExistingDirJob, limiter: RateLimiter) -> HeaderTimestamp:
    url = hdr_url_for_accession(submission.accession_number)
    try:
        body = fetch_url(url, job.user_agent, job.request_timeout_seconds, job.max_retries, job.retry_base_seconds, limiter)
        text = body.decode("utf-8", errors="replace")
        accepted_raw = first_tag(text, "ACCEPTANCE-DATETIME")
        accepted_et, accepted_utc = accepted_times(accepted_raw)
        hdr_path = hdr_artifact_path(Path(submission.nc_artifact_path), submission.accession_number)
        hdr_path.write_bytes(body)
        return HeaderTimestamp(
            accession_number=submission.accession_number,
            header_url=url,
            hdr_artifact_path=str(hdr_path),
            hdr_byte_size=len(body),
            hdr_sha256=hashlib.sha256(body).hexdigest(),
            accepted_at_edgar_raw=accepted_raw,
            accepted_at_et=accepted_et,
            accepted_at_utc=accepted_utc,
            timestamp_source="hdr_sgml" if accepted_raw else "hdr_sgml_missing_acceptance",
            fetch_status="ok" if accepted_raw else "missing_acceptance",
            fetch_error="",
        )
    except Exception as exc:  # noqa: BLE001
        return failed_header(submission.accession_number, repr(exc))


def failed_header(accession: str, error_text: str) -> HeaderTimestamp:
    return HeaderTimestamp(
        accession_number=accession,
        header_url=hdr_url_for_accession(accession),
        hdr_artifact_path="",
        hdr_byte_size=0,
        hdr_sha256="",
        accepted_at_edgar_raw="",
        accepted_at_et="",
        accepted_at_utc="",
        timestamp_source="missing",
        fetch_status="failed",
        fetch_error=error_text,
    )


def fetch_url(
    url: str,
    user_agent: str,
    timeout_seconds: float,
    max_retries: int,
    retry_base_seconds: float,
    limiter: RateLimiter,
) -> bytes:
    headers = {
        "User-Agent": user_agent,
        "Accept-Encoding": "identity",
        "Host": "www.sec.gov",
    }
    last_error = ""
    for attempt in range(max_retries + 1):
        limiter.wait()
        req = request.Request(url, headers=headers)
        try:
            with request.urlopen(req, timeout=timeout_seconds) as response:
                body = response.read()
                if (response.headers.get("Content-Encoding") or "").lower() == "gzip":
                    return gzip.decompress(body)
                return body
        except error.HTTPError as exc:
            last_error = f"HTTP {exc.code}: {exc.reason}"
            if exc.code in {403, 404} and url.endswith(".nc.tar.gz"):
                raise MissingArchiveError(last_error) from exc
            if exc.code not in RETRY_HTTP_CODES or attempt >= max_retries:
                raise RuntimeError(last_error) from exc
            retry_after = parse_retry_after(exc.headers.get("Retry-After"))
            time.sleep(retry_after if retry_after is not None else retry_base_seconds * (2**attempt))
        except Exception as exc:  # noqa: BLE001
            last_error = repr(exc)
            if attempt >= max_retries:
                raise RuntimeError(last_error) from exc
            time.sleep(retry_base_seconds * (2**attempt))
    raise RuntimeError(last_error or "request failed")


def merge_submission_timestamp(submission: ParsedSubmission, timestamp: HeaderTimestamp | None) -> dict[str, Any]:
    row = asdict(submission)
    if timestamp is None:
        timestamp = failed_header(submission.accession_number, "header timestamp missing from fetch output")
    row.update(
        {
            "header_url": timestamp.header_url,
            "hdr_artifact_path": timestamp.hdr_artifact_path,
            "accepted_at_edgar_raw": timestamp.accepted_at_edgar_raw,
            "accepted_at_et": timestamp.accepted_at_et,
            "accepted_at_utc": timestamp.accepted_at_utc,
            "timestamp_source": timestamp.timestamp_source,
            "timestamp_fetch_status": timestamp.fetch_status,
            "timestamp_fetch_error": timestamp.fetch_error,
        }
    )
    return row


def accepted_times(raw_value: str) -> tuple[str, str]:
    if not raw_value:
        return "", ""
    dt = datetime.strptime(raw_value, "%Y%m%d%H%M%S").replace(tzinfo=SEC_ET)
    return dt.isoformat(), dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def hdr_url_for_accession(accession: str) -> str:
    compact = accession.replace("-", "")
    cik = accession[:10].lstrip("0") or "0"
    return f"{SEC_BASE_URL}/data/{cik}/{compact}/{accession}.hdr.sgml"


def hdr_artifact_path(nc_path: Path, accession: str) -> Path:
    nc_path_text = str(nc_path)
    if "::" in nc_path_text:
        archive_path = Path(nc_path_text.split("::", 1)[0])
        headers_dir = archive_path.parent / "_headers"
    else:
        headers_dir = nc_path.parent / "_headers"
    headers_dir.mkdir(parents=True, exist_ok=True)
    return headers_dir / f"{accession}.hdr.sgml"


def build_day_job(
    archive_day: date,
    artifact_root: Path,
    user_agent: str,
    request_min_interval_seconds: float,
    request_timeout_seconds: float,
    max_retries: int,
    retry_base_seconds: float,
    header_concurrency: int,
    limit_files: int,
    force_redownload: bool,
    download_only: bool,
    persist_nc_files: bool,
    no_header_fetch: bool,
) -> DayJob:
    quarter = quarter_name(archive_day)
    archive_name = f"{archive_day:%Y%m%d}.nc.tar.gz"
    archive_url = f"{SEC_BASE_URL}/Feed/{archive_day:%Y}/{quarter}/{archive_name}"
    archive_path = artifact_root / "archives" / f"{archive_day:%Y}" / quarter / archive_name
    extract_dir = artifact_root / "extracted" / f"{archive_day:%Y}" / quarter / f"{archive_day:%Y%m%d}.nc"
    return DayJob(
        archive_date=archive_day.isoformat(),
        archive_url=archive_url,
        archive_path=str(archive_path),
        extract_dir=str(extract_dir),
        user_agent=user_agent,
        request_min_interval_seconds=request_min_interval_seconds,
        request_timeout_seconds=request_timeout_seconds,
        max_retries=max_retries,
        retry_base_seconds=retry_base_seconds,
        header_concurrency=header_concurrency,
        limit_files=limit_files,
        force_redownload=force_redownload,
        download_only=download_only,
        persist_nc_files=persist_nc_files,
        no_header_fetch=no_header_fetch,
    )


def safe_tar_member_name(value: str) -> str:
    return Path(value).name


def virtual_nc_artifact_path(archive_path: Path, member_name: str) -> Path:
    return Path(f"{archive_path}::{safe_tar_member_name(member_name)}")


def first_tag(text: str, tag: str) -> str:
    match = re.search(rf"<{re.escape(tag)}>\s*([^\r\n<]*)", text, flags=re.I)
    return match.group(1).strip() if match else ""


def section_tags_json(text: str, section_name: str) -> str:
    sections = re.findall(rf"<{section_name}>\s*(.*?)\s*</{section_name}>", text, flags=re.S | re.I)
    return json.dumps([parse_simple_tags(section) for section in sections], separators=(",", ":"))


def parse_simple_tags(text: str) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for match in re.finditer(r"<([A-Z0-9\-]+)>\s*([^\r\n<]*)", text):
        key = match.group(1).lower().replace("-", "_")
        value = match.group(2).strip()
        if not value:
            continue
        if key in output:
            current = output[key]
            if isinstance(current, list):
                current.append(value)
            else:
                output[key] = [current, value]
        else:
            output[key] = value
    return output


def detect_content_format(filename: str, payload: str) -> str:
    lower = filename.lower()
    prefix = payload.lstrip()[:500].lower()
    if lower.endswith(".pdf") or "begin 644" in prefix:
        return "encoded_binary_or_pdf"
    if lower.endswith(".xml") or prefix.startswith("<xml>") or "<?xml" in prefix:
        return "xml"
    if lower.endswith((".htm", ".html")) or "<html" in prefix:
        return "html"
    if lower.endswith(".xsd"):
        return "xsd"
    if lower.endswith(".txt"):
        return "text"
    return "unknown"


def parse_int(value: str) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def yyyymmdd_to_date(value: str) -> str:
    if not value:
        return ""
    try:
        return datetime.strptime(value, "%Y%m%d").date().isoformat()
    except ValueError:
        return value


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    part_path = path.with_suffix(path.suffix + ".part")
    part_path.unlink(missing_ok=True)
    with part_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n")
    part_path.replace(path)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n")


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def date_range(start: date, end: date) -> list[date]:
    output: list[date] = []
    current = start
    while current < end:
        output.append(current)
        current += timedelta(days=1)
    return output


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def quarter_name(value: date) -> str:
    return f"QTR{((value.month - 1) // 3) + 1}"


def sec_user_agent() -> str:
    return (
        os.environ.get("SEC_USER_AGENT")
        or os.environ.get("SEC_EDGAR_USER_AGENT")
        or os.environ.get("NEWS_SEC_USER_AGENT")
        or DEFAULT_SEC_USER_AGENT
    )


def normalize_space(value: str) -> str:
    return " ".join(value.split())


def print_header(config: dict[str, Any]) -> None:
    print("=" * 96, flush=True)
    print("SEC EDGAR historical Feed download + .nc parse + accepted timestamp enrichment", flush=True)
    for key in [
        "run_id",
        "start_date",
        "end_date",
        "existing_extracted_dir",
        "artifact_root",
        "output_root",
        "job_count",
        "archive_concurrency",
        "header_concurrency",
        "sec_request_min_interval_seconds",
        "limit_days",
        "limit_files_per_day",
        "download_only",
        "persist_nc_files",
        "no_header_fetch",
        "user_agent_present",
    ]:
        print(f"{key}={config.get(key)}", flush=True)
    print(f"secret_status={config.get('secret_status')}", flush=True)
    print(f"loaded_env_files={config.get('loaded_env_files')}", flush=True)
    print("=" * 96, flush=True)


def print_progress(total: int, completed: int, failed: int, totals: dict[str, int], started: float) -> None:
    done = completed + failed
    elapsed = time.perf_counter() - started
    print(
        "progress "
        f"{done:,}/{total:,} days "
        f"ok={completed:,} failed={failed:,} "
        f"submissions={totals['submissions']:,} documents={totals['documents']:,} "
        f"headers_ok={totals['header_success']:,} headers_failed={totals['header_failed']:,} "
        f"elapsed={elapsed:.1f}s",
        flush=True,
    )


if __name__ == "__main__":
    main()
