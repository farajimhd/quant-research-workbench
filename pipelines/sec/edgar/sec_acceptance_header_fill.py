from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import sys
import threading
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib import error, request
from zoneinfo import ZoneInfo


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.clickhouse_ingest_sip_flatfiles import (  # noqa: E402
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    quote_ident,
)
from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402
from pipelines.sec.edgar.sec_acceptance_backfill_build import (  # noqa: E402
    create_stage_table,
    insert_rows,
)
from pipelines.sec.edgar.sec_bulk_clickhouse_ingest import cik_archive_segment, cik10, clean_string  # noqa: E402
from pipelines.sec.edgar.sec_historical_feed_download import (  # noqa: E402
    RETRY_HTTP_CODES,
    RateLimiter,
    parse_retry_after,
    sec_user_agent,
)


DEFAULT_TARGET_DATABASE = "q_live"
DEFAULT_TARGET_TABLE = "sec_filing_v2"
DEFAULT_STAGE_DATABASE = "sec_core"
DEFAULT_STAGE_TABLE = "sec_bulk_mirror_filing_acceptance_v1"
DEFAULT_ARTIFACT_ROOT_WIN = Path("D:/market-data/sec_core")
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_acceptance_header_fill")
DEFAULT_BATCH_SIZE = 5_000
DEFAULT_REQUEST_MIN_INTERVAL_SECONDS = 0.11
SEC_ARCHIVES_BASE_URL = "https://www.sec.gov/Archives/edgar"
SEC_ET = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class HeaderJob:
    cik: str
    accession_number: str
    accession_number_compact: str
    company_name: str
    form_type: str
    filing_date: str | None
    report_date: str | None
    primary_document: str | None
    primary_document_url: str | None
    filing_detail_url: str | None
    filing_size: int | None
    items: str | None
    url: str
    artifact_path: str


@dataclass(frozen=True, slots=True)
class HeaderResult:
    job: HeaderJob
    status: str
    artifact_path: str
    byte_size: int
    sha256: str
    downloaded: bool
    accepted_at_utc: str
    acceptance_datetime_raw: str
    row: dict[str, Any] | None
    error: str
    elapsed_seconds: float


@dataclass(frozen=True, slots=True)
class RunPaths:
    run_root: Path
    header_jobs_jsonl: Path
    header_results_jsonl: Path
    accepted_jsonl: Path
    still_not_found_keys_jsonl: Path
    manifest_json: Path
    summary_md: Path

    @classmethod
    def create(cls, output_root: Path, run_id: str) -> "RunPaths":
        run_root = output_root / run_id
        run_root.mkdir(parents=True, exist_ok=True)
        return cls(
            run_root=run_root,
            header_jobs_jsonl=run_root / "header_jobs.jsonl",
            header_results_jsonl=run_root / "header_results.jsonl",
            accepted_jsonl=run_root / "accepted_rows.jsonl",
            still_not_found_keys_jsonl=run_root / "still_not_found_keys.jsonl",
            manifest_json=run_root / "sec_acceptance_header_fill_manifest.json",
            summary_md=run_root / "sec_acceptance_header_fill_summary.md",
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Final SEC accepted timestamp fallback. It fetches .hdr.sgml files only for q_live "
            "filings still missing after recent and fragment submissions passes, parses "
            "ACCEPTANCE-DATETIME, appends valid rows to the narrow acceptance staging table, "
            "and writes diagnostics for anything still unresolved."
        )
    )
    parser.add_argument("--clickhouse-url", default=default_migration_clickhouse_url())
    parser.add_argument("--user", default=default_migration_clickhouse_user())
    parser.add_argument("--password", default=default_migration_clickhouse_password())
    parser.add_argument("--target-database", default=os.environ.get("QLIVE_MIGRATION_TARGET_DATABASE", DEFAULT_TARGET_DATABASE))
    parser.add_argument("--target-table", default=os.environ.get("QLIVE_MIGRATION_SEC_FILING_TABLE", DEFAULT_TARGET_TABLE))
    parser.add_argument("--stage-database", default=os.environ.get("SEC_ACCEPTANCE_STAGE_DATABASE", DEFAULT_STAGE_DATABASE))
    parser.add_argument("--stage-table", default=os.environ.get("SEC_ACCEPTANCE_STAGE_TABLE", DEFAULT_STAGE_TABLE))
    parser.add_argument("--artifact-root-win", default=os.environ.get("SEC_CORE_ARTIFACT_ROOT_WIN", str(DEFAULT_ARTIFACT_ROOT_WIN)))
    parser.add_argument("--output-root-win", default=os.environ.get("SEC_ACCEPTANCE_HEADER_FILL_OUTPUT_ROOT_WIN", str(DEFAULT_OUTPUT_ROOT_WIN)))
    parser.add_argument("--storage-policy", default=os.environ.get("SEC_CLICKHOUSE_STORAGE_POLICY") or os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY") or "")
    parser.add_argument("--batch-size", type=int, default=int(os.environ.get("SEC_ACCEPTANCE_HEADER_FILL_BATCH_SIZE", str(DEFAULT_BATCH_SIZE))))
    parser.add_argument("--download-workers", type=int, default=int(os.environ.get("SEC_ACCEPTANCE_HEADER_WORKERS", "8")))
    parser.add_argument("--sec-request-min-interval-seconds", type=float, default=float(os.environ.get("SEC_REQUEST_MIN_INTERVAL_SECONDS", str(DEFAULT_REQUEST_MIN_INTERVAL_SECONDS))))
    parser.add_argument("--request-timeout-seconds", type=float, default=float(os.environ.get("SEC_REQUEST_TIMEOUT_SECONDS", "60")))
    parser.add_argument("--max-retries", type=int, default=int(os.environ.get("SEC_MAX_RETRIES", "4")))
    parser.add_argument("--retry-base-seconds", type=float, default=float(os.environ.get("SEC_RETRY_BASE_SECONDS", "1.5")))
    parser.add_argument("--limit-accessions", type=int, default=0, help="Debug cap for remaining accession headers. 0 means all.")
    parser.add_argument("--force-redownload", action="store_true")
    parser.add_argument("--execute", action="store_true", help="Insert matched rows into the staging table. Without this flag, only local artifacts are written.")
    return parser.parse_args()


def main() -> None:
    loaded_env = load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    validate_identifier(args.target_database, "--target-database")
    validate_identifier(args.target_table, "--target-table")
    validate_identifier(args.stage_database, "--stage-database")
    validate_identifier(args.stage_table, "--stage-table")
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be >= 1")
    if args.download_workers < 1:
        raise SystemExit("--download-workers must be >= 1")

    run_id = f"sec_acceptance_header_fill_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    paths = RunPaths.create(Path(args.output_root_win), run_id)
    artifact_root = Path(args.artifact_root_win)
    header_root = artifact_root / "bulk" / "submissions" / "headers"
    header_root.mkdir(parents=True, exist_ok=True)
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)

    print_header(args, paths, loaded_env, header_root, run_id)
    started = time.perf_counter()
    jobs = load_remaining_header_jobs(client, args, header_root)
    write_jsonl(paths.header_jobs_jsonl, [asdict(job) for job in jobs])

    if args.execute:
        create_stage_table(client, args)

    stats = run_header_jobs(client, args, paths, jobs)
    stats["initial_remaining_rows"] = len(jobs)
    stats["still_not_found_rows"] = len(jobs) - stats["accepted_rows_written"]
    stats["wall_seconds"] = round(time.perf_counter() - started, 3)
    write_manifest(paths.manifest_json, args, paths, loaded_env, run_id, header_root, stats)
    write_summary(paths.summary_md, args, paths, run_id, header_root, stats)
    print("summary=" + json.dumps(stats, sort_keys=True, default=str), flush=True)
    print(f"summary_md={paths.summary_md}", flush=True)


def default_migration_clickhouse_url() -> str:
    return os.environ.get("QLIVE_MIGRATION_CLICKHOUSE_URL") or os.environ.get("QMD_CLICKHOUSE_URL") or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_URL") or default_clickhouse_url()


def default_migration_clickhouse_user() -> str:
    return os.environ.get("QLIVE_MIGRATION_CLICKHOUSE_USER") or os.environ.get("QMD_CLICKHOUSE_USER") or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_USER") or default_clickhouse_user()


def default_migration_clickhouse_password() -> str:
    return (
        os.environ.get("QLIVE_MIGRATION_CLICKHOUSE_PASSWORD")
        or os.environ.get("QMD_CLICKHOUSE_PASSWORD")
        or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD")
        or default_clickhouse_password()
    )


def load_remaining_header_jobs(client: ClickHouseHttpClient, args: argparse.Namespace, header_root: Path) -> list[HeaderJob]:
    target = f"{quote_ident(args.target_database)}.{quote_ident(args.target_table)}"
    stage = f"{quote_ident(args.stage_database)}.{quote_ident(args.stage_table)}"
    limit_clause = f"\nLIMIT {int(args.limit_accessions)}" if args.limit_accessions else ""
    sql = f"""
SELECT
    q.cik,
    q.accession_number,
    q.accession_number_compact,
    ifNull(q.company_name, '') AS company_name,
    toString(q.form_type) AS form_type,
    ifNull(toString(q.filing_date), '') AS filing_date,
    ifNull(toString(q.report_date), '') AS report_date,
    ifNull(q.primary_document, '') AS primary_document,
    ifNull(q.primary_document_url, '') AS primary_document_url,
    ifNull(q.filing_detail_url, '') AS filing_detail_url,
    ifNull(toString(q.filing_size), '') AS filing_size,
    ifNull(q.items, '') AS items
FROM (SELECT * FROM {target} FINAL WHERE accepted_at_utc IS NULL) AS q
LEFT JOIN (SELECT cik, accession_number, 1 AS matched FROM {stage} FINAL) AS s
    ON q.cik = s.cik AND q.accession_number = s.accession_number
WHERE s.matched = 0
ORDER BY q.cik, q.accession_number
{limit_clause}
FORMAT TSV
"""
    jobs = []
    started = time.perf_counter()
    for line in stream_clickhouse_lines(client, sql):
        text = line.decode("utf-8", errors="replace").rstrip("\n")
        if not text:
            continue
        parts = text.split("\t")
        if len(parts) < 12:
            continue
        cik = cik10(parts[0])
        accession = clean_string(parts[1])
        compact = clean_string(parts[2]) or accession.replace("-", "")
        if not cik or not accession:
            continue
        url = hdr_url(cik, accession, compact)
        artifact_path = header_root / cik / f"{accession}.hdr.sgml"
        jobs.append(
            HeaderJob(
                cik=cik,
                accession_number=accession,
                accession_number_compact=compact,
                company_name=null_tsv_to_string(parts[3]) or "",
                form_type=clean_string(parts[4]),
                filing_date=null_tsv_to_optional(parts[5]),
                report_date=null_tsv_to_optional(parts[6]),
                primary_document=null_tsv_to_optional(parts[7]),
                primary_document_url=null_tsv_to_optional(parts[8]),
                filing_detail_url=null_tsv_to_optional(parts[9]),
                filing_size=int_or_none_from_tsv(parts[10]),
                items=null_tsv_to_optional(parts[11]),
                url=url,
                artifact_path=str(artifact_path),
            )
        )
    print(f"header_jobs_loaded={len(jobs):,} elapsed={time.perf_counter() - started:.1f}s", flush=True)
    return jobs


def stream_clickhouse_lines(client: ClickHouseHttpClient, sql: str) -> Any:
    req = request.Request(client.base_url + "/", data=sql.encode("utf-8"), method="POST")
    if client.user:
        req.add_header("X-ClickHouse-User", client.user)
    if client.password:
        req.add_header("X-ClickHouse-Key", client.password)
    try:
        with request.urlopen(req, timeout=None) as response:
            yield from response
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ClickHouse HTTP {exc.code} {exc.reason}: {body}") from exc


def run_header_jobs(client: ClickHouseHttpClient, args: argparse.Namespace, paths: RunPaths, jobs: list[HeaderJob]) -> dict[str, int]:
    stats = {
        "header_jobs_completed": 0,
        "header_jobs_failed": 0,
        "header_jobs_downloaded": 0,
        "header_jobs_reused": 0,
        "headers_missing_acceptance_datetime": 0,
        "accepted_rows_written": 0,
        "accepted_rows_inserted": 0,
    }
    rows_batch: list[dict[str, Any]] = []
    unresolved: list[HeaderJob] = []
    limiter = RateLimiter(args.sec_request_min_interval_seconds)
    started = time.perf_counter()
    with paths.header_results_jsonl.open("w", encoding="utf-8") as result_handle, paths.accepted_jsonl.open("w", encoding="utf-8") as accepted_handle:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.download_workers) as executor:
            future_to_job = {
                executor.submit(
                    process_header_job,
                    job,
                    sec_user_agent(),
                    args.request_timeout_seconds,
                    args.max_retries,
                    args.retry_base_seconds,
                    args.force_redownload,
                    limiter,
                ): job
                for job in jobs
            }
            for future in concurrent.futures.as_completed(future_to_job):
                result = future.result()
                result_handle.write(json.dumps(header_result_record(result), ensure_ascii=False, separators=(",", ":"), default=str) + "\n")
                stats["header_jobs_completed"] += 1
                if result.status != "ok":
                    stats["header_jobs_failed"] += 1
                    unresolved.append(result.job)
                if result.downloaded:
                    stats["header_jobs_downloaded"] += 1
                else:
                    stats["header_jobs_reused"] += 1
                if result.status == "missing_acceptance":
                    stats["headers_missing_acceptance_datetime"] += 1
                    unresolved.append(result.job)
                if result.row:
                    accepted_handle.write(json.dumps(result.row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")
                    stats["accepted_rows_written"] += 1
                    if args.execute:
                        rows_batch.append(result.row)
                    if args.execute and len(rows_batch) >= args.batch_size:
                        stats["accepted_rows_inserted"] += insert_rows(client, args.stage_database, args.stage_table, rows_batch)
                        rows_batch.clear()
                if stats["header_jobs_completed"] % 100 == 0 or stats["header_jobs_completed"] == len(jobs):
                    print(
                        "header_fill "
                        f"jobs={stats['header_jobs_completed']:,}/{len(jobs):,} "
                        f"accepted={stats['accepted_rows_written']:,} "
                        f"failed={stats['header_jobs_failed']:,} "
                        f"elapsed={time.perf_counter() - started:.1f}s",
                        flush=True,
                    )
    if args.execute:
        stats["accepted_rows_inserted"] += insert_rows(client, args.stage_database, args.stage_table, rows_batch)
    write_jsonl(paths.still_not_found_keys_jsonl, [asdict(job) for job in unresolved])
    return stats


def process_header_job(
    job: HeaderJob,
    user_agent: str,
    timeout_seconds: float,
    max_retries: int,
    retry_base_seconds: float,
    force_redownload: bool,
    limiter: RateLimiter,
) -> HeaderResult:
    started = time.perf_counter()
    artifact = Path(job.artifact_path)
    downloaded = False
    try:
        if force_redownload or not artifact.exists():
            body = fetch_url(job.url, user_agent, timeout_seconds, max_retries, retry_base_seconds, limiter)
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(body)
            downloaded = True
        body = artifact.read_bytes()
        sha = sha256_bytes(body)
        text = body.decode("utf-8", errors="replace")
        accepted_raw = first_sgml_value(text, "ACCEPTANCE-DATETIME")
        accepted_utc = acceptance_datetime_to_utc(accepted_raw)
        if not accepted_utc:
            return HeaderResult(
                job=job,
                status="missing_acceptance",
                artifact_path=str(artifact),
                byte_size=len(body),
                sha256=sha,
                downloaded=downloaded,
                accepted_at_utc="",
                acceptance_datetime_raw=accepted_raw,
                row=None,
                error="ACCEPTANCE-DATETIME missing or unparsable",
                elapsed_seconds=round(time.perf_counter() - started, 3),
            )
        row = header_stage_row(job, accepted_raw, accepted_utc, sha, clickhouse_now64())
        return HeaderResult(
            job=job,
            status="ok",
            artifact_path=str(artifact),
            byte_size=len(body),
            sha256=sha,
            downloaded=downloaded,
            accepted_at_utc=accepted_utc,
            acceptance_datetime_raw=accepted_raw,
            row=row,
            error="",
            elapsed_seconds=round(time.perf_counter() - started, 3),
        )
    except Exception as exc:  # noqa: BLE001
        return HeaderResult(
            job=job,
            status="failed",
            artifact_path=str(artifact),
            byte_size=artifact.stat().st_size if artifact.exists() else 0,
            sha256=sha256_path(artifact) if artifact.exists() else "",
            downloaded=downloaded,
            accepted_at_utc="",
            acceptance_datetime_raw="",
            row=None,
            error=repr(exc),
            elapsed_seconds=round(time.perf_counter() - started, 3),
        )


def fetch_url(url: str, user_agent: str, timeout_seconds: float, max_retries: int, retry_base_seconds: float, limiter: RateLimiter) -> bytes:
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/plain,text/html,*/*",
        "Accept-Encoding": "identity",
        "Host": "www.sec.gov",
    }
    last_error = ""
    for attempt in range(max_retries + 1):
        limiter.wait()
        req = request.Request(url, headers=headers)
        try:
            with request.urlopen(req, timeout=timeout_seconds) as response:
                return response.read()
        except error.HTTPError as exc:
            last_error = f"HTTP {exc.code}: {exc.reason}"
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


def header_stage_row(job: HeaderJob, accepted_raw: str, accepted_utc: str, header_sha256: str, now: str) -> dict[str, Any]:
    content_key = f"{job.cik}|{job.accession_number}|{accepted_raw}|{header_sha256}"
    return {
        "acceptance_id": hashlib.sha256(content_key.encode("utf-8")).hexdigest(),
        "cik": job.cik,
        "accession_number": job.accession_number,
        "accession_number_compact": job.accession_number_compact,
        "company_name": job.company_name,
        "form_type": job.form_type,
        "filing_date": job.filing_date,
        "report_date": job.report_date,
        "accepted_at_utc": accepted_utc,
        "acceptance_datetime_raw": accepted_raw or None,
        "accepted_at_source": "accession_header_hdr_sgml",
        "primary_document": job.primary_document,
        "primary_document_url": job.primary_document_url,
        "filing_detail_url": job.filing_detail_url,
        "filing_size": job.filing_size,
        "items": job.items,
        "source_file_id": hashlib.sha256(f"accession_header|{job.url}|{header_sha256}".encode("utf-8")).hexdigest(),
        "source_zip_sha256": header_sha256,
        "source_content_sha256": hashlib.sha256(content_key.encode("utf-8")).hexdigest(),
        "last_seen_at_utc": now,
    }


def header_result_record(result: HeaderResult) -> dict[str, Any]:
    row = asdict(result)
    row["has_row"] = result.row is not None
    row.pop("row", None)
    return row


def hdr_url(cik: str, accession: str, accession_compact: str) -> str:
    return f"{SEC_ARCHIVES_BASE_URL}/data/{cik_archive_segment(cik)}/{accession_compact}/{accession}.hdr.sgml"


def first_sgml_value(text: str, tag: str) -> str:
    match = re.search(rf"<{re.escape(tag)}>\s*([^<\r\n]+)", text, flags=re.IGNORECASE)
    return match.group(1).strip() if match else ""


def acceptance_datetime_to_utc(raw: str) -> str:
    text = clean_string(raw)
    if not text:
        return ""
    try:
        parsed = datetime.strptime(text[:14], "%Y%m%d%H%M%S").replace(tzinfo=SEC_ET)
    except ValueError:
        return ""
    return parsed.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")


def write_manifest(
    path: Path,
    args: argparse.Namespace,
    paths: RunPaths,
    loaded_env: list[Path],
    run_id: str,
    header_root: Path,
    stats: dict[str, Any],
) -> None:
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "run_id": run_id,
        "repo_root": str(REPO_ROOT),
        "dry_run": not args.execute,
        "target_table": f"{args.target_database}.{args.target_table}",
        "stage_table": f"{args.stage_database}.{args.stage_table}",
        "header_root": str(header_root),
        "run_root": str(paths.run_root),
        "stats": stats,
        "loaded_env_files": [str(item) for item in loaded_env],
        "secret_status": secret_status(
            [
                "QLIVE_MIGRATION_CLICKHOUSE_URL",
                "QLIVE_MIGRATION_CLICKHOUSE_USER",
                "QLIVE_MIGRATION_CLICKHOUSE_PASSWORD",
                "REAL_LIVE_CLICKHOUSE_WRITE_URL",
                "REAL_LIVE_CLICKHOUSE_WRITE_USER",
                "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD",
                "SEC_CORE_ARTIFACT_ROOT_WIN",
                "SEC_ACCEPTANCE_STAGE_DATABASE",
                "SEC_ACCEPTANCE_STAGE_TABLE",
                "SEC_USER_AGENT",
                "SEC_EDGAR_USER_AGENT",
            ]
        ),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def write_summary(paths_summary: Path, args: argparse.Namespace, paths: RunPaths, run_id: str, header_root: Path, stats: dict[str, Any]) -> None:
    lines = [
        "# SEC Acceptance Header Fill",
        "",
        f"- Run id: `{run_id}`",
        f"- Execute mode: `{args.execute}`",
        f"- q_live target: `{args.target_database}.{args.target_table}`",
        f"- Stage table: `{args.stage_database}.{args.stage_table}`",
        f"- Header root: `{header_root}`",
        "",
        "## Outputs",
        "",
        f"- Header jobs: `{paths.header_jobs_jsonl}`",
        f"- Header results: `{paths.header_results_jsonl}`",
        f"- Accepted rows: `{paths.accepted_jsonl}`",
        f"- Still not found keys: `{paths.still_not_found_keys_jsonl}`",
        "",
        "## Counts",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
    ]
    for key in sorted(stats):
        value = stats[key]
        if isinstance(value, float):
            lines.append(f"| `{key}` | {value:,.3f} |")
        elif isinstance(value, int):
            lines.append(f"| `{key}` | {value:,} |")
    paths_summary.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")


def sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def int_or_none_from_tsv(value: str) -> int | None:
    text = null_tsv_to_string(value)
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def null_tsv_to_optional(value: str) -> str | None:
    text = null_tsv_to_string(value)
    return text or None


def null_tsv_to_string(value: str) -> str:
    text = clean_string(value)
    return "" if text == r"\N" else text


def clickhouse_now64() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def validate_identifier(value: str, label: str) -> None:
    if not value or not value.replace("_", "").isalnum() or value[0].isdigit():
        raise SystemExit(f"{label} must be a simple ClickHouse identifier: {value!r}")


def print_header(args: argparse.Namespace, paths: RunPaths, loaded_env: list[Path], header_root: Path, run_id: str) -> None:
    print("=" * 96, flush=True)
    print("SEC acceptance header fill", flush=True)
    print(f"execute={args.execute}", flush=True)
    print(f"target_table={args.target_database}.{args.target_table}", flush=True)
    print(f"stage_table={args.stage_database}.{args.stage_table}", flush=True)
    print(f"header_root={header_root}", flush=True)
    print(f"download_workers={args.download_workers}", flush=True)
    print(f"sec_request_min_interval_seconds={args.sec_request_min_interval_seconds}", flush=True)
    print(f"run_id={run_id}", flush=True)
    print(f"run_root={paths.run_root}", flush=True)
    print("loaded_env_files=" + json.dumps([str(item) for item in loaded_env]), flush=True)
    print("=" * 96, flush=True)


if __name__ == "__main__":
    main()
