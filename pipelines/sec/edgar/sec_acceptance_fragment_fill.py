from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import sys
import threading
import time
import zipfile
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib import error, request


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.clickhouse import (  # noqa: E402
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    quote_ident,
    sql_string,
)
from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402
from pipelines.sec.edgar.sec_acceptance_backfill_build import (  # noqa: E402
    create_stage_table,
    insert_rows,
)
from pipelines.sec.edgar.sec_bulk_clickhouse_ingest import (  # noqa: E402
    accepted_at_utc,
    cik10,
    clean_string,
    filing_detail_url,
    filing_document_url,
    int_or_none,
    nullable_date,
    nullable_string,
    recent_value,
)
from pipelines.sec.edgar.sec_historical_feed_download import (  # noqa: E402
    RETRY_HTTP_CODES,
    RateLimiter,
    parse_date,
    parse_retry_after,
    sec_user_agent,
)
from pipelines.sec.edgar.sec_initial_fill_download import sha256_file  # noqa: E402


DEFAULT_TARGET_DATABASE = "q_live"
DEFAULT_TARGET_TABLE = "sec_filing_v3"
DEFAULT_STAGE_DATABASE = "sec_core"
DEFAULT_STAGE_TABLE = "sec_bulk_mirror_filing_acceptance_v3"
DEFAULT_ARTIFACT_ROOT_WIN = Path("D:/market-data/sec_core")
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_acceptance_fragment_fill")
DEFAULT_BATCH_SIZE = 25_000
DEFAULT_REQUEST_MIN_INTERVAL_SECONDS = 0.11
DATA_SEC_SUBMISSIONS_BASE_URL = "https://data.sec.gov/submissions"


@dataclass(frozen=True, slots=True)
class MissingFiling:
    cik: str
    accession_number: str
    filing_date: str
    form_type: str


@dataclass(frozen=True, slots=True)
class FragmentJob:
    cik: str
    file_name: str
    filing_from: str
    filing_to: str
    filing_count: int
    url: str
    artifact_path: str
    wanted_accessions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FragmentResult:
    job: FragmentJob
    status: str
    artifact_path: str
    byte_size: int
    sha256: str
    downloaded: bool
    matched_rows: tuple[dict[str, Any], ...]
    error: str
    elapsed_seconds: float


@dataclass(frozen=True, slots=True)
class RunPaths:
    run_root: Path
    fragment_jobs_jsonl: Path
    fragment_results_jsonl: Path
    accepted_jsonl: Path
    still_not_found_keys_jsonl: Path
    still_not_found_ciks_jsonl: Path
    manifest_json: Path
    summary_md: Path

    @classmethod
    def create(cls, output_root: Path, run_id: str) -> "RunPaths":
        run_root = output_root / run_id
        run_root.mkdir(parents=True, exist_ok=True)
        return cls(
            run_root=run_root,
            fragment_jobs_jsonl=run_root / "fragment_jobs.jsonl",
            fragment_results_jsonl=run_root / "fragment_results.jsonl",
            accepted_jsonl=run_root / "accepted_rows.jsonl",
            still_not_found_keys_jsonl=run_root / "still_not_found_keys.jsonl",
            still_not_found_ciks_jsonl=run_root / "still_not_found_ciks.jsonl",
            manifest_json=run_root / "sec_acceptance_fragment_fill_manifest.json",
            summary_md=run_root / "sec_acceptance_fragment_fill_summary.md",
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Second-pass SEC accepted timestamp fill. It finds q_live filings still missing after "
            "the submissions.zip recent pass, downloads only needed older SEC submission fragment "
            "JSON files, appends matched rows to the narrow acceptance staging table, and writes "
            "still-not-found diagnostics."
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
    parser.add_argument("--submissions-zip-win", default=os.environ.get("SEC_SUBMISSIONS_ZIP_WIN", ""))
    parser.add_argument("--output-root-win", default=os.environ.get("SEC_ACCEPTANCE_FRAGMENT_FILL_OUTPUT_ROOT_WIN", str(DEFAULT_OUTPUT_ROOT_WIN)))
    parser.add_argument("--storage-policy", default=os.environ.get("SEC_CLICKHOUSE_STORAGE_POLICY") or os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY") or "")
    parser.add_argument("--batch-size", type=int, default=int(os.environ.get("SEC_ACCEPTANCE_FRAGMENT_FILL_BATCH_SIZE", str(DEFAULT_BATCH_SIZE))))
    parser.add_argument("--download-workers", type=int, default=int(os.environ.get("SEC_ACCEPTANCE_FRAGMENT_WORKERS", "8")))
    parser.add_argument("--sec-request-min-interval-seconds", type=float, default=float(os.environ.get("SEC_REQUEST_MIN_INTERVAL_SECONDS", str(DEFAULT_REQUEST_MIN_INTERVAL_SECONDS))))
    parser.add_argument("--request-timeout-seconds", type=float, default=float(os.environ.get("SEC_REQUEST_TIMEOUT_SECONDS", "60")))
    parser.add_argument("--max-retries", type=int, default=int(os.environ.get("SEC_MAX_RETRIES", "4")))
    parser.add_argument("--retry-base-seconds", type=float, default=float(os.environ.get("SEC_RETRY_BASE_SECONDS", "1.5")))
    parser.add_argument("--limit-ciks", type=int, default=0, help="Debug cap for remaining CIKs. 0 means all.")
    parser.add_argument("--limit-fragments", type=int, default=0, help="Debug cap for planned fragments. 0 means all.")
    parser.add_argument("--download-all-fragments-per-cik", action="store_true", help="Ignore fragment date ranges and download every older fragment for each remaining CIK.")
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

    run_id = f"sec_acceptance_fragment_fill_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    paths = RunPaths.create(Path(args.output_root_win), run_id)
    artifact_root = Path(args.artifact_root_win)
    submissions_zip = resolve_submissions_zip(args, artifact_root)
    fragment_root = artifact_root / "bulk" / "submissions" / "fragments"
    fragment_root.mkdir(parents=True, exist_ok=True)
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)

    print_header(args, paths, loaded_env, submissions_zip, fragment_root, run_id)
    started = time.perf_counter()
    missing_by_cik = load_remaining_missing(client, args)
    jobs, planning_stats = plan_fragment_jobs(args, submissions_zip, fragment_root, missing_by_cik)
    write_jsonl(paths.fragment_jobs_jsonl, [asdict(job) for job in jobs])

    if args.execute:
        create_stage_table(client, args)

    stats = run_fragment_jobs(client, args, paths, jobs, missing_by_cik)
    stats.update(planning_stats)
    stats["initial_remaining_rows"] = sum(len(values) for values in missing_by_cik.values()) + stats["accepted_rows_written"]
    stats["still_not_found_rows"] = sum(len(values) for values in missing_by_cik.values())
    stats["still_not_found_ciks"] = sum(1 for values in missing_by_cik.values() if values)
    stats["wall_seconds"] = round(time.perf_counter() - started, 3)
    write_not_found(paths, missing_by_cik)
    write_manifest(paths.manifest_json, args, paths, loaded_env, run_id, submissions_zip, fragment_root, stats)
    write_summary(paths.summary_md, args, paths, run_id, submissions_zip, fragment_root, stats)
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


def resolve_submissions_zip(args: argparse.Namespace, artifact_root: Path) -> Path:
    path = Path(args.submissions_zip_win) if args.submissions_zip_win else artifact_root / "bulk" / "submissions" / "submissions.zip"
    if not path.exists():
        raise SystemExit(f"SEC submissions zip not found: {path}")
    return path


def load_remaining_missing(client: ClickHouseHttpClient, args: argparse.Namespace) -> dict[str, dict[str, MissingFiling]]:
    target = f"{quote_ident(args.target_database)}.{quote_ident(args.target_table)}"
    stage = f"{quote_ident(args.stage_database)}.{quote_ident(args.stage_table)}"
    sql = f"""
SELECT
    q.cik,
    q.accession_number,
    ifNull(toString(q.filing_date), '') AS filing_date,
    toString(q.form_type) AS form_type
FROM (SELECT * FROM {target} FINAL WHERE accepted_at_utc IS NULL) AS q
LEFT JOIN (SELECT cik, accession_number, 1 AS matched FROM {stage} FINAL) AS s
    ON q.cik = s.cik AND q.accession_number = s.accession_number
WHERE s.matched = 0
ORDER BY q.cik, q.accession_number
FORMAT TSV
"""
    missing: dict[str, dict[str, MissingFiling]] = defaultdict(dict)
    skipped_for_limit = 0
    total = 0
    started = time.perf_counter()
    for line in stream_clickhouse_lines(client, sql):
        text = line.decode("utf-8", errors="replace").rstrip("\n")
        if not text:
            continue
        parts = text.split("\t")
        if len(parts) < 4:
            continue
        cik = cik10(parts[0])
        accession = clean_string(parts[1])
        if not cik or not accession:
            continue
        if args.limit_ciks and cik not in missing and len(missing) >= args.limit_ciks:
            skipped_for_limit += 1
            continue
        missing[cik][accession] = MissingFiling(cik=cik, accession_number=accession, filing_date=clean_string(parts[2]), form_type=clean_string(parts[3]))
        total += 1
    print(f"remaining_missing_loaded={total:,} ciks={len(missing):,} skipped_for_limit={skipped_for_limit:,} elapsed={time.perf_counter() - started:.1f}s", flush=True)
    return dict(missing)


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


def plan_fragment_jobs(
    args: argparse.Namespace,
    submissions_zip: Path,
    fragment_root: Path,
    missing_by_cik: dict[str, dict[str, MissingFiling]],
) -> tuple[list[FragmentJob], dict[str, int]]:
    jobs: list[FragmentJob] = []
    stats = {
        "remaining_ciks_loaded": len(missing_by_cik),
        "remaining_rows_loaded": sum(len(values) for values in missing_by_cik.values()),
        "ciks_with_fragment_index": 0,
        "ciks_without_fragment_index": 0,
        "fragment_refs_examined": 0,
        "fragment_jobs_planned": 0,
        "fragment_jobs_reused_on_disk": 0,
    }
    names_by_cik_name: dict[str, str] = {}
    with zipfile.ZipFile(submissions_zip) as archive:
        for name in archive.namelist():
            if name.lower().endswith(".json"):
                names_by_cik_name[Path(name).name] = name
        for cik in sorted(missing_by_cik):
            zip_name = f"CIK{cik}.json"
            archive_name = names_by_cik_name.get(zip_name)
            if archive_name is None:
                stats["ciks_without_fragment_index"] += 1
                continue
            data = json.loads(archive.read(archive_name).decode("utf-8", errors="replace"))
            fragment_refs = data.get("filings", {}).get("files", []) or []
            if not fragment_refs:
                stats["ciks_without_fragment_index"] += 1
                continue
            stats["ciks_with_fragment_index"] += 1
            for ref in fragment_refs:
                stats["fragment_refs_examined"] += 1
                file_name = clean_string(ref.get("name", ""))
                if not file_name:
                    continue
                filing_from = clean_string(ref.get("filingFrom", ""))
                filing_to = clean_string(ref.get("filingTo", ""))
                wanted = matching_accessions_for_fragment(
                    missing_by_cik[cik],
                    filing_from,
                    filing_to,
                    download_all=args.download_all_fragments_per_cik,
                )
                if not wanted:
                    continue
                artifact_path = fragment_root / file_name
                if artifact_path.exists():
                    stats["fragment_jobs_reused_on_disk"] += 1
                jobs.append(
                    FragmentJob(
                        cik=cik,
                        file_name=file_name,
                        filing_from=filing_from,
                        filing_to=filing_to,
                        filing_count=int_or_none(ref.get("filingCount")) or 0,
                        url=f"{DATA_SEC_SUBMISSIONS_BASE_URL}/{file_name}",
                        artifact_path=str(artifact_path),
                        wanted_accessions=tuple(sorted(wanted)),
                    )
                )
                if args.limit_fragments and len(jobs) >= args.limit_fragments:
                    stats["fragment_jobs_planned"] = len(jobs)
                    return jobs, stats
    stats["fragment_jobs_planned"] = len(jobs)
    return jobs, stats


def matching_accessions_for_fragment(missing: dict[str, MissingFiling], filing_from: str, filing_to: str, *, download_all: bool) -> list[str]:
    if download_all:
        return list(missing.keys())
    start = parse_optional_date(filing_from)
    end = parse_optional_date(filing_to)
    if start is None or end is None:
        return list(missing.keys())
    output = []
    for accession, item in missing.items():
        filing_date = parse_optional_date(item.filing_date)
        if filing_date is None or start <= filing_date <= end:
            output.append(accession)
    return output


def parse_optional_date(value: str) -> date | None:
    text = clean_string(value)
    if not text:
        return None
    try:
        return parse_date(text[:10])
    except ValueError:
        return None


def run_fragment_jobs(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    paths: RunPaths,
    jobs: list[FragmentJob],
    missing_by_cik: dict[str, dict[str, MissingFiling]],
) -> dict[str, int]:
    stats = {
        "fragment_jobs_completed": 0,
        "fragment_jobs_failed": 0,
        "fragment_jobs_downloaded": 0,
        "fragment_jobs_reused": 0,
        "accepted_rows_written": 0,
        "accepted_rows_inserted": 0,
        "accepted_rows_missing_acceptance_datetime": 0,
    }
    rows_batch: list[dict[str, Any]] = []
    limiter = RateLimiter(args.sec_request_min_interval_seconds)
    write_lock = threading.Lock()
    started = time.perf_counter()
    with paths.fragment_results_jsonl.open("w", encoding="utf-8") as result_handle, paths.accepted_jsonl.open("w", encoding="utf-8") as accepted_handle:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.download_workers) as executor:
            future_to_job = {
                executor.submit(
                    process_fragment_job,
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
                with write_lock:
                    result_handle.write(json.dumps(fragment_result_record(result), ensure_ascii=False, separators=(",", ":"), default=str) + "\n")
                    stats["fragment_jobs_completed"] += 1
                    if result.status != "ok":
                        stats["fragment_jobs_failed"] += 1
                    if result.downloaded:
                        stats["fragment_jobs_downloaded"] += 1
                    else:
                        stats["fragment_jobs_reused"] += 1
                    for row in result.matched_rows:
                        accepted_handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")
                        stats["accepted_rows_written"] += 1
                        cik_missing = missing_by_cik.get(row["cik"], {})
                        cik_missing.pop(row["accession_number"], None)
                        if not row["accepted_at_utc"]:
                            stats["accepted_rows_missing_acceptance_datetime"] += 1
                            continue
                        if args.execute:
                            rows_batch.append(row)
                        if args.execute and len(rows_batch) >= args.batch_size:
                            stats["accepted_rows_inserted"] += insert_rows(client, args.stage_database, args.stage_table, rows_batch)
                            rows_batch.clear()
                    if stats["fragment_jobs_completed"] % 100 == 0 or stats["fragment_jobs_completed"] == len(jobs):
                        print(
                            "fragment_fill "
                            f"jobs={stats['fragment_jobs_completed']:,}/{len(jobs):,} "
                            f"accepted={stats['accepted_rows_written']:,} "
                            f"remaining={sum(len(values) for values in missing_by_cik.values()):,} "
                            f"elapsed={time.perf_counter() - started:.1f}s",
                            flush=True,
                        )
    if args.execute:
        stats["accepted_rows_inserted"] += insert_rows(client, args.stage_database, args.stage_table, rows_batch)
    return stats


def process_fragment_job(
    job: FragmentJob,
    user_agent: str,
    timeout_seconds: float,
    max_retries: int,
    retry_base_seconds: float,
    force_redownload: bool,
    limiter: RateLimiter,
) -> FragmentResult:
    started = time.perf_counter()
    artifact = Path(job.artifact_path)
    downloaded = False
    try:
        if force_redownload or not artifact.exists():
            body = fetch_url(job.url, user_agent, timeout_seconds, max_retries, retry_base_seconds, limiter)
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(body)
            downloaded = True
        payload = json.loads(artifact.read_text(encoding="utf-8"))
        sha = sha256_file_local(artifact)
        rows = fragment_acceptance_rows(payload, job, sha, clickhouse_now64())
        return FragmentResult(
            job=job,
            status="ok",
            artifact_path=str(artifact),
            byte_size=artifact.stat().st_size,
            sha256=sha,
            downloaded=downloaded,
            matched_rows=tuple(rows),
            error="",
            elapsed_seconds=round(time.perf_counter() - started, 3),
        )
    except Exception as exc:  # noqa: BLE001
        return FragmentResult(
            job=job,
            status="failed",
            artifact_path=str(artifact),
            byte_size=artifact.stat().st_size if artifact.exists() else 0,
            sha256=sha256_file_local(artifact) if artifact.exists() else "",
            downloaded=downloaded,
            matched_rows=(),
            error=repr(exc),
            elapsed_seconds=round(time.perf_counter() - started, 3),
        )


def fetch_url(url: str, user_agent: str, timeout_seconds: float, max_retries: int, retry_base_seconds: float, limiter: RateLimiter) -> bytes:
    headers = {
        "User-Agent": user_agent,
        "Accept": "application/json,text/plain,*/*",
        "Accept-Encoding": "identity",
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


def fragment_acceptance_rows(payload: dict[str, Any], job: FragmentJob, source_sha256: str, now: str) -> list[dict[str, Any]]:
    recent = payload.get("filings", {}).get("recent", {}) if isinstance(payload.get("filings"), dict) else payload
    if not isinstance(recent, dict):
        return []
    wanted = set(job.wanted_accessions)
    lengths = [len(value) for value in recent.values() if isinstance(value, list)]
    count = max(lengths) if lengths else 0
    rows = []
    for index in range(count):
        accession = recent_value(recent, "accessionNumber", index)
        if not accession or accession not in wanted:
            continue
        accession_compact = accession.replace("-", "")
        accepted_raw = recent_value(recent, "acceptanceDateTime", index)
        primary_document = recent_value(recent, "primaryDocument", index)
        content_fingerprint = json.dumps({key: recent_value(recent, key, index) for key in recent}, sort_keys=True, separators=(",", ":"))
        rows.append(
            {
                "acceptance_id": hashlib.sha256(f"{job.cik}|{accession}|{accepted_raw}|{job.file_name}".encode("utf-8")).hexdigest(),
                "cik": job.cik,
                "accession_number": accession,
                "accession_number_compact": accession_compact,
                "company_name": "",
                "form_type": recent_value(recent, "form", index),
                "filing_date": nullable_date(recent_value(recent, "filingDate", index)),
                "report_date": nullable_date(recent_value(recent, "reportDate", index)),
                "accepted_at_utc": accepted_at_utc(accepted_raw),
                "acceptance_datetime_raw": accepted_raw or None,
                "accepted_at_source": "submissions_bulk_fragment" if accepted_raw else "missing_in_submissions_bulk_fragment",
                "primary_document": primary_document or None,
                "primary_document_url": filing_document_url(job.cik, accession_compact, primary_document) if primary_document else None,
                "filing_detail_url": filing_detail_url(job.cik, accession_compact),
                "filing_size": int_or_none(recent_value(recent, "size", index)),
                "items": nullable_string(recent_value(recent, "items", index)),
                "source_file_id": hashlib.sha256(f"submissions_fragment|{job.url}|{source_sha256}".encode("utf-8")).hexdigest(),
                "source_zip_sha256": source_sha256,
                "source_content_sha256": hashlib.sha256(content_fingerprint.encode("utf-8")).hexdigest(),
                "last_seen_at_utc": now,
            }
        )
    return rows


def fragment_result_record(result: FragmentResult) -> dict[str, Any]:
    row = asdict(result)
    row["matched_row_count"] = len(result.matched_rows)
    row.pop("matched_rows", None)
    return row


def write_not_found(paths: RunPaths, missing_by_cik: dict[str, dict[str, MissingFiling]]) -> None:
    with paths.still_not_found_keys_jsonl.open("w", encoding="utf-8") as key_handle, paths.still_not_found_ciks_jsonl.open("w", encoding="utf-8") as cik_handle:
        for cik in sorted(missing_by_cik):
            missing = missing_by_cik[cik]
            if not missing:
                continue
            cik_handle.write(json.dumps({"cik": cik, "not_found_count": len(missing)}, separators=(",", ":")) + "\n")
            for accession in sorted(missing):
                key_handle.write(json.dumps(asdict(missing[accession]), separators=(",", ":")) + "\n")


def write_manifest(
    path: Path,
    args: argparse.Namespace,
    paths: RunPaths,
    loaded_env: list[Path],
    run_id: str,
    submissions_zip: Path,
    fragment_root: Path,
    stats: dict[str, Any],
) -> None:
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "run_id": run_id,
        "repo_root": str(REPO_ROOT),
        "dry_run": not args.execute,
        "target_table": f"{args.target_database}.{args.target_table}",
        "stage_table": f"{args.stage_database}.{args.stage_table}",
        "submissions_zip": str(submissions_zip),
        "fragment_root": str(fragment_root),
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
                "SEC_SUBMISSIONS_ZIP_WIN",
                "SEC_ACCEPTANCE_STAGE_DATABASE",
                "SEC_ACCEPTANCE_STAGE_TABLE",
                "SEC_USER_AGENT",
                "SEC_EDGAR_USER_AGENT",
            ]
        ),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def write_summary(paths_summary: Path, args: argparse.Namespace, paths: RunPaths, run_id: str, submissions_zip: Path, fragment_root: Path, stats: dict[str, Any]) -> None:
    lines = [
        "# SEC Acceptance Fragment Fill",
        "",
        f"- Run id: `{run_id}`",
        f"- Execute mode: `{args.execute}`",
        f"- q_live target: `{args.target_database}.{args.target_table}`",
        f"- Stage table: `{args.stage_database}.{args.stage_table}`",
        f"- submissions.zip: `{submissions_zip}`",
        f"- Fragment root: `{fragment_root}`",
        "",
        "## Outputs",
        "",
        f"- Fragment jobs: `{paths.fragment_jobs_jsonl}`",
        f"- Fragment results: `{paths.fragment_results_jsonl}`",
        f"- Accepted rows: `{paths.accepted_jsonl}`",
        f"- Still not found keys: `{paths.still_not_found_keys_jsonl}`",
        f"- Still not found CIK summary: `{paths.still_not_found_ciks_jsonl}`",
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


def sha256_file_local(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def clickhouse_now64() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def validate_identifier(value: str, label: str) -> None:
    if not value or not value.replace("_", "").isalnum() or value[0].isdigit():
        raise SystemExit(f"{label} must be a simple ClickHouse identifier: {value!r}")


def print_header(args: argparse.Namespace, paths: RunPaths, loaded_env: list[Path], submissions_zip: Path, fragment_root: Path, run_id: str) -> None:
    print("=" * 96, flush=True)
    print("SEC acceptance fragment fill", flush=True)
    print(f"execute={args.execute}", flush=True)
    print(f"target_table={args.target_database}.{args.target_table}", flush=True)
    print(f"stage_table={args.stage_database}.{args.stage_table}", flush=True)
    print(f"submissions_zip={submissions_zip}", flush=True)
    print(f"fragment_root={fragment_root}", flush=True)
    print(f"download_workers={args.download_workers}", flush=True)
    print(f"sec_request_min_interval_seconds={args.sec_request_min_interval_seconds}", flush=True)
    print(f"run_id={run_id}", flush=True)
    print(f"run_root={paths.run_root}", flush=True)
    print("loaded_env_files=" + json.dumps([str(item) for item in loaded_env]), flush=True)
    print("=" * 96, flush=True)


if __name__ == "__main__":
    main()
