from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import sys
import time
import traceback
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib import error, parse, request


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.clickhouse_ingest_sip_flatfiles import ClickHouseHttpClient, discover_clickhouse_env_files  # noqa: E402
from research.mlops.env import load_env_files, secret_status  # noqa: E402
from research.mlops.news_benzinga_clickhouse import (  # noqa: E402
    DEFAULT_MANIFEST_TABLE,
    DEFAULT_NEWS_TABLE,
    NewsIngestManifestRow,
    create_news_database_and_tables,
    insert_manifest_rows,
    insert_news_rows,
    latest_manifest_statuses,
)
from research.mlops.news_benzinga_normalize import (  # noqa: E402
    NewsExtractionOptions,
    artifact_path_for_payload,
    normalize_benzinga_payload,
    parse_provider_datetime,
    to_clickhouse_dt64,
    to_provider_rfc3339,
    write_raw_payload,
)


DEFAULT_ENDPOINT = "https://api.massive.com/benzinga/v2/news"
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/benzinga_news_ingest")
DEFAULT_ARTIFACT_ROOT_WIN = Path("D:/market-data/benzinga_news_canonical")
DEFAULT_START_UTC = "2024-01-01T00:00:00Z"
DEFAULT_END_UTC = "2026-01-01T00:00:00Z"
PROVIDER_RETRY_HTTP_CODES = {408, 425, 429, 500, 502, 503, 504}
CURRENT_RUN_ID = ""
CURRENT_REPORT_PATH: Path | None = None


@dataclass(frozen=True, slots=True)
class BucketJob:
    bucket_id: str
    start_utc: str
    end_utc: str
    endpoint_url: str
    api_key: str
    limit: int
    max_pages: int
    artifact_root_win: str
    fetch_external: bool
    extract_pdfs: bool
    extraction_timeout_seconds: float
    external_min_body_chars: int
    max_pdf_bytes: int
    text_limit_chars: int
    external_request_min_interval_seconds: float
    benzinga_request_min_interval_seconds: float
    sec_request_min_interval_seconds: float
    external_max_retries: int
    external_retry_base_seconds: float
    default_user_agent: str
    sec_user_agent: str


@dataclass(frozen=True, slots=True)
class DownloadedArtifact:
    raw_artifact_path: str
    raw_payload_hash: str
    downloaded_at_utc: str


@dataclass(frozen=True, slots=True)
class FileError:
    stage: str
    bucket_id: str
    raw_artifact_path: str = ""
    raw_payload_hash: str = ""
    provider_article_id: str = ""
    published_raw: str = ""
    exception: str = ""
    traceback: str = ""


@dataclass(frozen=True, slots=True)
class DownloadResult:
    bucket_id: str
    start_utc: str
    end_utc: str
    artifacts: list[DownloadedArtifact]
    file_errors: list[FileError]
    downloaded_rows: int
    page_count: int
    saturated: int
    wall_seconds: float
    exception: str = ""


@dataclass(frozen=True, slots=True)
class NormalizeJob:
    bucket_id: str
    start_utc: str
    end_utc: str
    artifacts: list[DownloadedArtifact]
    downloaded_rows: int
    page_count: int
    saturated: int
    artifact_root_win: str
    fetch_external: bool
    extract_pdfs: bool
    extraction_timeout_seconds: float
    external_min_body_chars: int
    max_pdf_bytes: int
    text_limit_chars: int
    external_request_min_interval_seconds: float
    benzinga_request_min_interval_seconds: float
    sec_request_min_interval_seconds: float
    external_max_retries: int
    external_retry_base_seconds: float
    default_user_agent: str
    sec_user_agent: str


@dataclass(frozen=True, slots=True)
class NormalizeResult:
    bucket_id: str
    start_utc: str
    end_utc: str
    rows: list[dict[str, Any]]
    file_errors: list[FileError]
    extraction_events: list[dict[str, Any]]
    downloaded_rows: int
    normalized_rows: int
    page_count: int
    saturated: int
    wall_seconds: float
    exception: str = ""


@dataclass(frozen=True, slots=True)
class BucketInsertMeta:
    bucket_id: str
    start_utc: str
    end_utc: str
    downloaded_rows: int
    normalized_rows: int
    page_count: int
    saturated: int


@dataclass(frozen=True, slots=True)
class InsertJob:
    batch_id: str
    rows: list[dict[str, Any]]
    bucket_metas: list[BucketInsertMeta]
    bucket_row_counts: dict[str, int]
    clickhouse_url: str
    user: str
    password: str
    database: str
    news_table: str


@dataclass(frozen=True, slots=True)
class InsertResult:
    batch_id: str
    bucket_metas: list[BucketInsertMeta]
    bucket_row_counts: dict[str, int]
    inserted_rows: int
    wall_seconds: float
    exception: str = ""


@dataclass(frozen=True, slots=True)
class ManifestJob:
    batch_id: str
    rows: list[NewsIngestManifestRow]
    clickhouse_url: str
    user: str
    password: str
    database: str
    manifest_table: str


@dataclass(frozen=True, slots=True)
class ManifestResult:
    batch_id: str
    row_count: int
    bucket_ids: list[str]
    wall_seconds: float
    exception: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download, normalize, and ingest canonical Benzinga news.")
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--database", default=default_clickhouse_database())
    parser.add_argument("--news-table", default=DEFAULT_NEWS_TABLE)
    parser.add_argument("--manifest-table", default=DEFAULT_MANIFEST_TABLE)
    parser.add_argument("--storage-policy", default=default_storage_policy())
    parser.add_argument("--api-key", default=os.environ.get("MASSIVE_API_KEY", ""))
    parser.add_argument(
        "--endpoint-url",
        default=os.environ.get("NEWS_BENZINGA_URL") or os.environ.get("NEWS_MASSIVE_BENZINGA_URL", DEFAULT_ENDPOINT),
    )
    parser.add_argument("--start-utc", default=os.environ.get("NEWS_BENZINGA_HISTORICAL_START_UTC", DEFAULT_START_UTC))
    parser.add_argument("--end-utc", default=os.environ.get("NEWS_BENZINGA_HISTORICAL_END_UTC", DEFAULT_END_UTC))
    parser.add_argument("--bucket-minutes", type=int, default=int(os.environ.get("NEWS_BENZINGA_BUCKET_MINUTES", "15")))
    parser.add_argument("--limit", type=int, default=int(os.environ.get("NEWS_BENZINGA_POLL_LIMIT", "1000")))
    parser.add_argument("--max-pages", type=int, default=int(os.environ.get("NEWS_BENZINGA_MAX_PAGES", "20")))
    parser.add_argument("--download-processes", type=int, default=int(os.environ.get("NEWS_BENZINGA_DOWNLOAD_PROCESSES", "8")))
    parser.add_argument("--normalize-processes", type=int, default=int(os.environ.get("NEWS_BENZINGA_NORMALIZE_PROCESSES", "0")))
    parser.add_argument("--insert-concurrency", type=int, default=int(os.environ.get("NEWS_BENZINGA_INSERT_CONCURRENCY", "4")))
    parser.add_argument("--insert-batch-rows", type=int, default=int(os.environ.get("NEWS_BENZINGA_INSERT_BATCH_ROWS", "5000")))
    parser.add_argument("--manifest-batch-rows", type=int, default=int(os.environ.get("NEWS_BENZINGA_MANIFEST_BATCH_ROWS", "1000")))
    parser.add_argument("--artifact-root-win", default=os.environ.get("NEWS_BENZINGA_ARTIFACT_ROOT_WIN", str(DEFAULT_ARTIFACT_ROOT_WIN)))
    parser.add_argument("--output-root-win", default=os.environ.get("NEWS_BENZINGA_OUTPUT_ROOT_WIN", str(DEFAULT_OUTPUT_ROOT_WIN)))
    parser.add_argument("--external-min-body-chars", type=int, default=int(os.environ.get("NEWS_EXTRACTION_MIN_BODY_CHARS", "300")))
    parser.add_argument("--extraction-timeout-seconds", type=float, default=float(os.environ.get("NEWS_EXTRACTION_TIMEOUT_SECONDS", "8")))
    parser.add_argument("--external-request-min-interval-seconds", type=float, default=float(os.environ.get("NEWS_EXTERNAL_REQUEST_MIN_INTERVAL_SECONDS", "0.5")))
    parser.add_argument("--benzinga-request-min-interval-seconds", type=float, default=float(os.environ.get("NEWS_BENZINGA_REQUEST_MIN_INTERVAL_SECONDS", "1.0")))
    parser.add_argument("--sec-request-min-interval-seconds", type=float, default=float(os.environ.get("NEWS_SEC_REQUEST_MIN_INTERVAL_SECONDS", "0.13")))
    parser.add_argument("--external-max-retries", type=int, default=int(os.environ.get("NEWS_EXTERNAL_MAX_RETRIES", "3")))
    parser.add_argument("--external-retry-base-seconds", type=float, default=float(os.environ.get("NEWS_EXTERNAL_RETRY_BASE_SECONDS", "1.0")))
    parser.add_argument("--external-user-agent", default=os.environ.get("NEWS_EXTERNAL_USER_AGENT", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36"))
    parser.add_argument("--sec-user-agent", default=os.environ.get("NEWS_SEC_USER_AGENT") or os.environ.get("SEC_EDGAR_USER_AGENT", ""))
    parser.add_argument("--max-pdf-bytes", type=int, default=int(os.environ.get("NEWS_PDF_MAX_BYTES", "12000000")))
    parser.add_argument("--text-limit-chars", type=int, default=int(os.environ.get("NEWS_NORMALIZED_TEXT_LIMIT_CHARS", "24000")))
    parser.add_argument("--no-fetch-external", action="store_true")
    parser.add_argument("--no-extract-pdfs", action="store_true")
    parser.add_argument("--limit-buckets", type=int, default=0)
    parser.add_argument("--retry-inserted", action="store_true")
    parser.add_argument("--retry-partial", action="store_true")
    parser.add_argument("--no-insert", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def default_clickhouse_url() -> str:
    return (
        os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_URL")
        or os.environ.get("NEWS_CLICKHOUSE_URL")
        or os.environ.get("QMD_CLICKHOUSE_URL")
        or os.environ.get("CLICKHOUSE_URL")
        or os.environ.get("TD__DATABASE__CLICKHOUSE__ENDPOINT_URL")
        or "http://localhost:8123"
    )


def default_clickhouse_user() -> str:
    return (
        os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_USER")
        or os.environ.get("NEWS_CLICKHOUSE_USER")
        or os.environ.get("QMD_CLICKHOUSE_USER")
        or os.environ.get("CLICKHOUSE_WORKSTATION_USER")
        or os.environ.get("CLICKHOUSE_USER")
        or os.environ.get("TD__DATABASE__CLICKHOUSE__USER")
        or "default"
    )


def default_clickhouse_password() -> str:
    return (
        os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD")
        or os.environ.get("NEWS_CLICKHOUSE_PASSWORD")
        or os.environ.get("QMD_CLICKHOUSE_PASSWORD")
        or os.environ.get("CLICKHOUSE_WORKSTATION_PASSWORD")
        or os.environ.get("CLICKHOUSE_PASSWORD")
        or os.environ.get("TD__DATABASE__CLICKHOUSE__PASSWORD")
        or ""
    )


def default_clickhouse_database() -> str:
    return (
        os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_DATABASE")
        or os.environ.get("NEWS_CLICKHOUSE_DATABASE")
        or os.environ.get("QMD_CLICKHOUSE_DATABASE")
        or "q_live"
    )


def default_storage_policy() -> str:
    return os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY") or os.environ.get("NEWS_CLICKHOUSE_STORAGE_POLICY") or ""


def env_status_keys() -> list[str]:
    return [
        "MASSIVE_API_KEY",
        "REAL_LIVE_CLICKHOUSE_WRITE_URL",
        "REAL_LIVE_CLICKHOUSE_WRITE_DATABASE",
        "REAL_LIVE_CLICKHOUSE_WRITE_USER",
        "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD",
        "CLICKHOUSE_LIVE_STORAGE_POLICY",
        "NEWS_BENZINGA_ARTIFACT_ROOT_WIN",
        "NEWS_BENZINGA_OUTPUT_ROOT_WIN",
    ]


def main() -> None:
    global CURRENT_REPORT_PATH, CURRENT_RUN_ID
    loaded_env_files = load_env_files(discover_clickhouse_env_files(), verbose=True)
    args = parse_args()
    args.download_processes = max(1, args.download_processes)
    args.normalize_processes = args.normalize_processes if args.normalize_processes > 0 else max(1, args.download_processes // 2)
    args.insert_concurrency = max(1, args.insert_concurrency)
    args.insert_batch_rows = max(1, args.insert_batch_rows)
    args.manifest_batch_rows = max(1, args.manifest_batch_rows)
    args.external_max_retries = max(0, args.external_max_retries)
    args.external_retry_base_seconds = max(0.0, args.external_retry_base_seconds)
    args.external_request_min_interval_seconds = max(0.0, args.external_request_min_interval_seconds)
    args.benzinga_request_min_interval_seconds = max(0.0, args.benzinga_request_min_interval_seconds)
    args.sec_request_min_interval_seconds = max(0.0, args.sec_request_min_interval_seconds)
    if not args.api_key:
        raise RuntimeError("MASSIVE_API_KEY is required for Benzinga historical download.")
    if not args.storage_policy:
        print("WARNING: CLICKHOUSE_LIVE_STORAGE_POLICY/--storage-policy is empty; news tables will use ClickHouse defaults.", flush=True)
    if not args.no_extract_pdfs and not args.sec_user_agent:
        print(
            "WARNING: SEC PDF fetching is enabled but NEWS_SEC_USER_AGENT/SEC_EDGAR_USER_AGENT is empty; "
            "set it to an app name and contact email before full SEC-heavy runs.",
            flush=True,
        )

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_root = Path(args.output_root_win)
    output_root.mkdir(parents=True, exist_ok=True)
    report_path = output_root / f"benzinga_historical_ingest_{run_id}.jsonl"
    CURRENT_RUN_ID = run_id
    CURRENT_REPORT_PATH = report_path
    artifact_root = Path(args.artifact_root_win)
    buckets = build_bucket_jobs(args)

    print("=" * 96, flush=True)
    print("Canonical Benzinga historical news ingest", flush=True)
    print(f"run_id={run_id}", flush=True)
    print(f"database={args.database} news_table={args.news_table} manifest_table={args.manifest_table}", flush=True)
    print(f"clickhouse_url={args.clickhouse_url}", flush=True)
    print(f"storage_policy={args.storage_policy or '<default>'}", flush=True)
    print(f"start_utc={args.start_utc} end_utc={args.end_utc} bucket_minutes={args.bucket_minutes}", flush=True)
    print(f"buckets={len(buckets):,} limit_buckets={args.limit_buckets}", flush=True)
    print(
        f"download_processes={args.download_processes} normalize_processes={args.normalize_processes} "
        f"insert_concurrency={args.insert_concurrency} insert_batch_rows={args.insert_batch_rows} "
        f"manifest_batch_rows={args.manifest_batch_rows}",
        flush=True,
    )
    print(f"fetch_external={not args.no_fetch_external} extract_pdfs={not args.no_extract_pdfs}", flush=True)
    print(
        f"external_intervals_seconds=default:{args.external_request_min_interval_seconds} "
        f"benzinga:{args.benzinga_request_min_interval_seconds} sec:{args.sec_request_min_interval_seconds} "
        f"external_max_retries={args.external_max_retries}",
        flush=True,
    )
    print(f"artifact_root={artifact_root}", flush=True)
    print(f"report={report_path}", flush=True)
    print(f"dry_run={args.dry_run} no_insert={args.no_insert}", flush=True)
    print(f"secret_status={secret_status(env_status_keys())}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print("=" * 96, flush=True)

    append_jsonl(report_path, {"type": "config", "run_id": run_id, "args": public_args(args), "bucket_count": len(buckets)})
    if args.dry_run:
        for preview in buckets[:10]:
            print(f"preview {preview.bucket_id} {preview.start_utc} -> {preview.end_utc}", flush=True)
        return

    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    if not args.no_insert:
        create_news_database_and_tables(
            client,
            database=args.database,
            news_table=args.news_table,
            manifest_table=args.manifest_table,
            storage_policy=args.storage_policy,
        )
    statuses = {} if args.no_insert else latest_manifest_statuses(client, database=args.database, table=args.manifest_table)
    pending = filter_pending_buckets(buckets, statuses, retry_inserted=args.retry_inserted, retry_partial=args.retry_partial)
    print(f"pending_buckets={len(pending):,} skipped={len(buckets) - len(pending):,}", flush=True)
    if not pending:
        return

    final_completed = 0
    final_failed = 0
    download_completed = 0
    normalize_completed = 0
    downloaded_total = 0
    inserted_total = 0
    normalized_total = 0
    started_at = time.perf_counter()
    insert_futures: dict[concurrent.futures.Future[InsertResult], InsertJob] = {}
    manifest_futures: dict[concurrent.futures.Future[ManifestResult], ManifestJob] = {}
    insert_rows_buffer: list[dict[str, Any]] = []
    insert_bucket_counts: dict[str, int] = {}
    insert_bucket_metas: dict[str, BucketInsertMeta] = {}
    manifest_rows_buffer: list[NewsIngestManifestRow] = []
    insert_batch_index = 0
    manifest_batch_index = 0
    next_download_index = 0
    max_download_backlog = max(args.download_processes, args.download_processes * 4)
    max_insert_workers = max(1, min(args.insert_concurrency, len(pending)))

    def submit_insert_batch(insert_pool: concurrent.futures.ThreadPoolExecutor) -> None:
        nonlocal insert_batch_index
        if not insert_rows_buffer:
            return
        insert_batch_index += 1
        batch_id = f"{run_id}_batch_{insert_batch_index:08d}"
        job = InsertJob(
            batch_id=batch_id,
            rows=list(insert_rows_buffer),
            bucket_metas=list(insert_bucket_metas.values()),
            bucket_row_counts=dict(insert_bucket_counts),
            clickhouse_url=args.clickhouse_url,
            user=args.user,
            password=args.password,
            database=args.database,
            news_table=args.news_table,
        )
        insert_futures[insert_pool.submit(insert_rows_worker, job)] = job
        insert_rows_buffer.clear()
        insert_bucket_counts.clear()
        insert_bucket_metas.clear()

    def queue_manifest_rows(rows: list[NewsIngestManifestRow], insert_pool: concurrent.futures.ThreadPoolExecutor) -> None:
        if not rows:
            return
        manifest_rows_buffer.extend(rows)
        if len(manifest_rows_buffer) >= args.manifest_batch_rows:
            submit_manifest_batch(insert_pool)

    def submit_manifest_batch(insert_pool: concurrent.futures.ThreadPoolExecutor) -> None:
        nonlocal manifest_batch_index
        if not manifest_rows_buffer:
            return
        manifest_batch_index += 1
        batch_id = f"{run_id}_manifest_{manifest_batch_index:08d}"
        job = ManifestJob(
            batch_id=batch_id,
            rows=list(manifest_rows_buffer),
            clickhouse_url=args.clickhouse_url,
            user=args.user,
            password=args.password,
            database=args.database,
            manifest_table=args.manifest_table,
        )
        manifest_futures[insert_pool.submit(manifest_rows_worker, job)] = job
        manifest_rows_buffer.clear()

    with (
        concurrent.futures.ProcessPoolExecutor(max_workers=args.download_processes) as download_pool,
        concurrent.futures.ProcessPoolExecutor(max_workers=args.normalize_processes) as normalize_pool,
        concurrent.futures.ThreadPoolExecutor(max_workers=max_insert_workers) as insert_pool,
    ):
        download_futures: dict[concurrent.futures.Future[DownloadResult], BucketJob] = {}
        normalize_futures: dict[concurrent.futures.Future[NormalizeResult], DownloadResult] = {}

        def submit_more_downloads() -> None:
            nonlocal next_download_index
            while next_download_index < len(pending) and len(download_futures) < max_download_backlog:
                bucket = pending[next_download_index]
                next_download_index += 1
                download_futures[download_pool.submit(download_bucket_worker, bucket)] = bucket

        submit_more_downloads()
        while (
            download_futures
            or normalize_futures
            or insert_futures
            or manifest_futures
            or manifest_rows_buffer
            or next_download_index < len(pending)
        ):
            submit_more_downloads()
            if not download_futures and not normalize_futures and not insert_futures and manifest_rows_buffer:
                submit_manifest_batch(insert_pool)
            active_futures: list[concurrent.futures.Future[Any]] = [
                *download_futures.keys(),
                *normalize_futures.keys(),
                *insert_futures.keys(),
                *manifest_futures.keys(),
            ]
            if not active_futures:
                continue
            done, _ = concurrent.futures.wait(active_futures, return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                if future in download_futures:
                    bucket = download_futures.pop(future)
                    try:
                        download_result = future.result()
                    except Exception as exc:  # noqa: BLE001
                        download_result = DownloadResult(
                            bucket_id=bucket.bucket_id,
                            start_utc=bucket.start_utc,
                            end_utc=bucket.end_utc,
                            artifacts=[],
                            file_errors=[],
                            downloaded_rows=0,
                            page_count=0,
                            saturated=0,
                            wall_seconds=0.0,
                            exception=repr(exc),
                        )
                    download_completed += 1
                    downloaded_total += download_result.downloaded_rows
                    append_jsonl(report_path, {"type": "download", "run_id": run_id, "result": result_public(download_result)})
                    append_file_errors(report_path, run_id, download_result.file_errors)
                    if not args.no_insert:
                        queue_manifest_rows([manifest_from_download(run_id, download_result)], insert_pool)
                    if download_result.exception:
                        final_failed += 1
                    elif download_result.artifacts:
                        normalize_job = NormalizeJob(
                            bucket_id=download_result.bucket_id,
                            start_utc=download_result.start_utc,
                            end_utc=download_result.end_utc,
                            artifacts=download_result.artifacts,
                            downloaded_rows=download_result.downloaded_rows,
                            page_count=download_result.page_count,
                            saturated=download_result.saturated,
                            artifact_root_win=bucket.artifact_root_win,
                            fetch_external=bucket.fetch_external,
                            extract_pdfs=bucket.extract_pdfs,
                            extraction_timeout_seconds=bucket.extraction_timeout_seconds,
                            external_min_body_chars=bucket.external_min_body_chars,
                            max_pdf_bytes=bucket.max_pdf_bytes,
                            text_limit_chars=bucket.text_limit_chars,
                            external_request_min_interval_seconds=bucket.external_request_min_interval_seconds,
                            benzinga_request_min_interval_seconds=bucket.benzinga_request_min_interval_seconds,
                            sec_request_min_interval_seconds=bucket.sec_request_min_interval_seconds,
                            external_max_retries=bucket.external_max_retries,
                            external_retry_base_seconds=bucket.external_retry_base_seconds,
                            default_user_agent=bucket.default_user_agent,
                            sec_user_agent=bucket.sec_user_agent,
                        )
                        normalize_futures[normalize_pool.submit(normalize_bucket_worker, normalize_job)] = download_result
                    else:
                        normalize_completed += 1
                        if args.no_insert:
                            final_completed += 1
                        else:
                            queue_manifest_rows([manifest_empty_insert(run_id, download_result)], insert_pool)
                            final_completed += 1

                elif future in normalize_futures:
                    download_result = normalize_futures.pop(future)
                    try:
                        normalize_result = future.result()
                    except Exception as exc:  # noqa: BLE001
                        normalize_result = NormalizeResult(
                            bucket_id=download_result.bucket_id,
                            start_utc=download_result.start_utc,
                            end_utc=download_result.end_utc,
                            rows=[],
                            file_errors=[],
                            extraction_events=[],
                            downloaded_rows=download_result.downloaded_rows,
                            normalized_rows=0,
                            page_count=download_result.page_count,
                            saturated=download_result.saturated,
                            wall_seconds=0.0,
                            exception=repr(exc),
                        )
                    normalize_completed += 1
                    normalized_total += normalize_result.normalized_rows
                    append_jsonl(report_path, {"type": "normalize", "run_id": run_id, "result": result_public(normalize_result)})
                    append_file_errors(report_path, run_id, normalize_result.file_errors)
                    append_extraction_events(report_path, run_id, normalize_result.extraction_events)
                    if not args.no_insert:
                        queue_manifest_rows([manifest_from_normalize(run_id, normalize_result)], insert_pool)
                    if normalize_result.exception:
                        final_failed += 1
                    elif args.no_insert:
                        final_completed += 1
                    elif normalize_result.rows:
                        meta = BucketInsertMeta(
                            bucket_id=normalize_result.bucket_id,
                            start_utc=normalize_result.start_utc,
                            end_utc=normalize_result.end_utc,
                            downloaded_rows=normalize_result.downloaded_rows,
                            normalized_rows=normalize_result.normalized_rows,
                            page_count=normalize_result.page_count,
                            saturated=normalize_result.saturated,
                        )
                        insert_rows_buffer.extend(normalize_result.rows)
                        insert_bucket_metas[meta.bucket_id] = meta
                        insert_bucket_counts[meta.bucket_id] = insert_bucket_counts.get(meta.bucket_id, 0) + len(normalize_result.rows)
                        if len(insert_rows_buffer) >= args.insert_batch_rows:
                            submit_insert_batch(insert_pool)
                    else:
                        queue_manifest_rows([manifest_empty_insert(run_id, normalize_result)], insert_pool)
                        final_completed += 1

                elif future in insert_futures:
                    insert_job = insert_futures.pop(future)
                    try:
                        insert_result = future.result()
                    except Exception as exc:  # noqa: BLE001
                        insert_result = InsertResult(
                            batch_id=insert_job.batch_id,
                            bucket_metas=insert_job.bucket_metas,
                            bucket_row_counts=insert_job.bucket_row_counts,
                            inserted_rows=0,
                            wall_seconds=0.0,
                            exception=repr(exc),
                        )
                    insert_completed, insert_failed, insert_rows, manifest_rows = handle_insert_result(
                        insert_result,
                        run_id,
                        report_path,
                    )
                    final_completed += insert_completed
                    final_failed += insert_failed
                    inserted_total += insert_rows
                    queue_manifest_rows(manifest_rows, insert_pool)

                elif future in manifest_futures:
                    manifest_futures.pop(future)
                    try:
                        manifest_result = future.result()
                    except Exception as exc:  # noqa: BLE001
                        manifest_result = ManifestResult(
                            batch_id="unknown",
                            row_count=0,
                            bucket_ids=[],
                            wall_seconds=0.0,
                            exception=repr(exc),
                        )
                    append_jsonl(report_path, {"type": "manifest_insert", "run_id": run_id, "result": asdict(manifest_result)})

                if not download_futures and not normalize_futures and not args.no_insert and insert_rows_buffer:
                    submit_insert_batch(insert_pool)
                print_progress(
                    total=len(pending),
                    download_completed=download_completed,
                    normalize_completed=normalize_completed,
                    final_completed=final_completed,
                    final_failed=final_failed,
                    downloaded_rows=downloaded_total,
                    normalized_rows=normalized_total,
                    inserted_rows=inserted_total,
                    pending_insert_rows=len(insert_rows_buffer),
                    started_at=started_at,
                )

    elapsed = time.perf_counter() - started_at
    print("=" * 96, flush=True)
    print(
        f"DONE completed={final_completed:,} failed={final_failed:,} "
        f"downloaded_rows={downloaded_total:,} normalized_rows={normalized_total:,} inserted_rows={inserted_total:,}",
        flush=True,
    )
    print(f"elapsed_min={elapsed / 60:.1f} report={report_path}", flush=True)
    print("=" * 96, flush=True)


def build_bucket_jobs(args: argparse.Namespace) -> list[BucketJob]:
    start = parse_input_datetime(args.start_utc)
    end = parse_input_datetime(args.end_utc)
    if end <= start:
        raise ValueError("--end-utc must be after --start-utc")
    step = timedelta(minutes=max(1, args.bucket_minutes))
    jobs: list[BucketJob] = []
    current = start
    while current < end:
        bucket_end = min(current + step, end)
        bucket_id = bucket_identity(current, bucket_end)
        jobs.append(
            BucketJob(
                bucket_id=bucket_id,
                start_utc=to_provider_rfc3339(current),
                end_utc=to_provider_rfc3339(bucket_end),
                endpoint_url=args.endpoint_url,
                api_key=args.api_key,
                limit=max(1, min(args.limit, 50_000)),
                max_pages=max(1, args.max_pages),
                artifact_root_win=args.artifact_root_win,
                fetch_external=not args.no_fetch_external,
                extract_pdfs=not args.no_extract_pdfs,
                extraction_timeout_seconds=max(1.0, args.extraction_timeout_seconds),
                external_min_body_chars=max(0, args.external_min_body_chars),
                max_pdf_bytes=max(1, args.max_pdf_bytes),
                text_limit_chars=max(0, args.text_limit_chars),
                external_request_min_interval_seconds=max(0.0, args.external_request_min_interval_seconds),
                benzinga_request_min_interval_seconds=max(0.0, args.benzinga_request_min_interval_seconds),
                sec_request_min_interval_seconds=max(0.0, args.sec_request_min_interval_seconds),
                external_max_retries=max(0, args.external_max_retries),
                external_retry_base_seconds=max(0.0, args.external_retry_base_seconds),
                default_user_agent=args.external_user_agent,
                sec_user_agent=args.sec_user_agent,
            )
        )
        current = bucket_end
    if args.limit_buckets > 0:
        jobs = jobs[: args.limit_buckets]
    return jobs


def download_bucket_worker(job: BucketJob) -> DownloadResult:
    started_at = time.perf_counter()
    artifact_root = Path(job.artifact_root_win)
    artifacts: list[DownloadedArtifact] = []
    file_errors: list[FileError] = []
    downloaded_rows = 0
    page_count = 0
    saturated = 0
    next_url: str | None = build_benzinga_url(job.endpoint_url, job.api_key, job.start_utc, job.end_utc, job.limit)
    try:
        while next_url and page_count < job.max_pages:
            page_count += 1
            response = fetch_json(next_url)
            items = response.get("results") or []
            downloaded_rows += len(items)
            downloaded_at = to_provider_rfc3339(datetime.now(UTC))
            for item in items:
                if not isinstance(item, dict):
                    continue
                raw_path: Path | None = None
                try:
                    try:
                        published = parse_provider_datetime(str(item.get("published") or ""))
                    except Exception:
                        published = datetime.now(UTC)
                    raw_path = artifact_path_for_payload(artifact_root, item, published)
                    raw_hash = write_raw_payload(raw_path, item)
                    artifacts.append(
                        DownloadedArtifact(
                            raw_artifact_path=str(raw_path),
                            raw_payload_hash=raw_hash,
                            downloaded_at_utc=downloaded_at,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    file_errors.append(
                        FileError(
                            stage="download_raw_write",
                            bucket_id=job.bucket_id,
                            raw_artifact_path=str(raw_path or ""),
                            provider_article_id=provider_id_from_payload(item),
                            published_raw=str(item.get("published") or ""),
                            exception=repr(exc),
                            traceback=traceback.format_exc(),
                        )
                    )
            next_url = response.get("next_url")
            if next_url:
                next_url = append_api_key(str(next_url), job.api_key)
        if next_url:
            saturated = 1
        return DownloadResult(
            bucket_id=job.bucket_id,
            start_utc=job.start_utc,
            end_utc=job.end_utc,
            artifacts=artifacts,
            file_errors=file_errors,
            downloaded_rows=downloaded_rows,
            page_count=page_count,
            saturated=saturated,
            wall_seconds=time.perf_counter() - started_at,
            exception="all downloaded payload raw writes failed" if downloaded_rows and file_errors and not artifacts else "",
        )
    except Exception as exc:  # noqa: BLE001
        return DownloadResult(
            bucket_id=job.bucket_id,
            start_utc=job.start_utc,
            end_utc=job.end_utc,
            artifacts=artifacts,
            file_errors=file_errors,
            downloaded_rows=downloaded_rows,
            page_count=page_count,
            saturated=saturated,
            wall_seconds=time.perf_counter() - started_at,
            exception=repr(exc),
        )


def normalize_bucket_worker(job: NormalizeJob) -> NormalizeResult:
    started_at = time.perf_counter()
    artifact_root = Path(job.artifact_root_win)
    rows: list[dict[str, Any]] = []
    file_errors: list[FileError] = []
    extraction_events: list[dict[str, Any]] = []
    options = NewsExtractionOptions(
        fetch_external=job.fetch_external,
        extract_pdfs=job.extract_pdfs,
        external_min_body_chars=job.external_min_body_chars,
        request_timeout_seconds=job.extraction_timeout_seconds,
        max_pdf_bytes=job.max_pdf_bytes,
        text_limit_chars=job.text_limit_chars,
        external_request_min_interval_seconds=job.external_request_min_interval_seconds,
        benzinga_request_min_interval_seconds=job.benzinga_request_min_interval_seconds,
        sec_request_min_interval_seconds=job.sec_request_min_interval_seconds,
        external_max_retries=job.external_max_retries,
        external_retry_base_seconds=job.external_retry_base_seconds,
        external_rate_limit_root=str(Path(job.artifact_root_win) / "rate_limits"),
        default_user_agent=job.default_user_agent,
        sec_user_agent=job.sec_user_agent,
    )
    for artifact in job.artifacts:
        provider_article_id = ""
        published_raw = ""
        try:
            raw_path = Path(artifact.raw_artifact_path)
            payload = json.loads(raw_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise TypeError(f"raw payload was {type(payload).__name__}, expected dict")
            provider_article_id = provider_id_from_payload(payload)
            published_raw = str(payload.get("published") or "")
            rows.append(
                normalize_benzinga_payload(
                    payload,
                    raw_artifact_path=artifact.raw_artifact_path,
                    raw_payload_hash=artifact.raw_payload_hash,
                    downloaded_at_utc=parse_provider_datetime(artifact.downloaded_at_utc),
                    artifact_root=artifact_root,
                    options=options,
                    diagnostics=extraction_events,
                )
            )
        except Exception as exc:  # noqa: BLE001
            file_errors.append(
                FileError(
                    stage="normalize_raw_file",
                    bucket_id=job.bucket_id,
                    raw_artifact_path=artifact.raw_artifact_path,
                    raw_payload_hash=artifact.raw_payload_hash,
                    provider_article_id=provider_article_id,
                    published_raw=published_raw,
                    exception=repr(exc),
                    traceback=traceback.format_exc(),
                )
            )
    return NormalizeResult(
        bucket_id=job.bucket_id,
        start_utc=job.start_utc,
        end_utc=job.end_utc,
        rows=rows,
        file_errors=file_errors,
        extraction_events=extraction_events,
        downloaded_rows=job.downloaded_rows,
        normalized_rows=len(rows),
        page_count=job.page_count,
        saturated=job.saturated,
        wall_seconds=time.perf_counter() - started_at,
        exception="all raw files failed normalization" if job.artifacts and file_errors and not rows else "",
    )


def insert_rows_worker(job: InsertJob) -> InsertResult:
    started_at = time.perf_counter()
    try:
        client = ClickHouseHttpClient(job.clickhouse_url, job.user, job.password)
        inserted = insert_news_rows(client, database=job.database, table=job.news_table, rows=job.rows)
        return InsertResult(
            batch_id=job.batch_id,
            bucket_metas=job.bucket_metas,
            bucket_row_counts=job.bucket_row_counts,
            inserted_rows=inserted,
            wall_seconds=time.perf_counter() - started_at,
        )
    except Exception as exc:  # noqa: BLE001
        return InsertResult(
            batch_id=job.batch_id,
            bucket_metas=job.bucket_metas,
            bucket_row_counts=job.bucket_row_counts,
            inserted_rows=0,
            wall_seconds=time.perf_counter() - started_at,
            exception=repr(exc),
        )


def manifest_rows_worker(job: ManifestJob) -> ManifestResult:
    started_at = time.perf_counter()
    try:
        client = ClickHouseHttpClient(job.clickhouse_url, job.user, job.password)
        insert_manifest_rows(client, database=job.database, table=job.manifest_table, rows=job.rows)
        return ManifestResult(
            batch_id=job.batch_id,
            row_count=len(job.rows),
            bucket_ids=[row.bucket_id for row in job.rows[:200]],
            wall_seconds=time.perf_counter() - started_at,
        )
    except Exception as exc:  # noqa: BLE001
        return ManifestResult(
            batch_id=job.batch_id,
            row_count=len(job.rows),
            bucket_ids=[row.bucket_id for row in job.rows[:200]],
            wall_seconds=time.perf_counter() - started_at,
            exception=repr(exc),
        )


def handle_insert_result(
    result: InsertResult,
    run_id: str,
    report_path: Path,
) -> tuple[int, int, int, list[NewsIngestManifestRow]]:
    completed = 0
    failed = 0
    inserted_rows = 0
    manifest_rows: list[NewsIngestManifestRow] = []
    for meta in result.bucket_metas:
        status = "inserted"
        exception = result.exception
        if result.exception:
            status = "failed"
            failed += 1
        else:
            completed += 1
            bucket_inserted_rows = result.bucket_row_counts.get(meta.bucket_id, 0)
            inserted_rows += bucket_inserted_rows
            if meta.saturated:
                status = "partial"
        manifest_rows.append(
            NewsIngestManifestRow(
                run_id=run_id,
                bucket_id=meta.bucket_id,
                bucket_start_utc=meta.start_utc,
                bucket_end_utc=meta.end_utc,
                status=status,
                downloaded_rows=meta.downloaded_rows,
                normalized_rows=meta.normalized_rows,
                inserted_rows=0 if result.exception else result.bucket_row_counts.get(meta.bucket_id, 0),
                page_count=meta.page_count,
                saturated=meta.saturated,
                wall_seconds=result.wall_seconds,
                exception=exception,
            )
        )
    append_jsonl(
        report_path,
        {
            "type": "insert",
            "run_id": run_id,
            "result": result_public(result),
            "status": "failed" if result.exception else "inserted",
        },
    )
    return completed, failed, inserted_rows, manifest_rows


def filter_pending_buckets(
    buckets: list[BucketJob],
    statuses: dict[str, str],
    *,
    retry_inserted: bool,
    retry_partial: bool,
) -> list[BucketJob]:
    pending: list[BucketJob] = []
    for bucket in buckets:
        status = statuses.get(bucket.bucket_id, "")
        if status == "inserted" and not retry_inserted:
            continue
        if status == "partial" and not retry_partial:
            continue
        pending.append(bucket)
    return pending


def build_benzinga_url(endpoint_url: str, api_key: str, start_utc: str, end_utc: str, limit: int) -> str:
    params = {
        "published.gte": start_utc,
        "published.lte": end_utc,
        "limit": str(limit),
        "sort": "published.asc",
        "apiKey": api_key,
    }
    separator = "&" if "?" in endpoint_url else "?"
    return endpoint_url.rstrip("?&") + separator + parse.urlencode(params)


def append_api_key(url: str, api_key: str) -> str:
    if "apiKey=" in url:
        return url
    return url + ("&" if "?" in url else "?") + parse.urlencode({"apiKey": api_key})


def fetch_json(url: str) -> dict[str, Any]:
    req = request.Request(url, headers={"User-Agent": "quant-research-workbench-benzinga-ingest/1.0"})
    attempts = 4
    body = ""
    for attempt in range(1, attempts + 1):
        try:
            with request.urlopen(req, timeout=60) as response:  # noqa: S310
                body = response.read().decode("utf-8", errors="replace")
                break
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code not in PROVIDER_RETRY_HTTP_CODES or attempt >= attempts:
                raise RuntimeError(f"Massive Benzinga HTTP {exc.code}: {body}") from exc
            time.sleep(provider_retry_sleep_seconds(exc, attempt))
        except (TimeoutError, error.URLError):
            if attempt >= attempts:
                raise
            time.sleep(provider_retry_sleep_seconds(None, attempt))
    value = json.loads(body)
    if not isinstance(value, dict):
        raise RuntimeError("Massive Benzinga response was not a JSON object")
    return value


def provider_retry_sleep_seconds(exc: error.HTTPError | None, attempt: int) -> float:
    retry_after = exc.headers.get("Retry-After", "") if exc is not None else ""
    parsed_retry_after = parse_retry_after_seconds(retry_after)
    if parsed_retry_after is not None:
        return min(300.0, parsed_retry_after)
    return min(300.0, 1.0 * (2 ** (attempt - 1)))


def parse_retry_after_seconds(value: str) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return max(0.0, (parsed.astimezone(UTC) - datetime.now(UTC)).total_seconds())


def parse_input_datetime(value: str) -> datetime:
    text = value.strip()
    if len(text) == 10:
        text += "T00:00:00Z"
    return parse_provider_datetime(text)


def bucket_identity(start: datetime, end: datetime) -> str:
    return hashlib.blake2b(f"{to_clickhouse_dt64(start)}|{to_clickhouse_dt64(end)}".encode("utf-8"), digest_size=12).hexdigest()


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, default=str) + "\n")


def public_args(args: argparse.Namespace) -> dict[str, Any]:
    payload = vars(args).copy()
    if payload.get("api_key"):
        payload["api_key"] = "present"
    if payload.get("password"):
        payload["password"] = "present"
    return payload


def result_public(result: Any) -> dict[str, Any]:
    payload = asdict(result)
    file_errors = payload.pop("file_errors", [])
    payload["file_error_count"] = len(file_errors)
    extraction_events = payload.pop("extraction_events", [])
    payload["extraction_event_count"] = len(extraction_events)
    payload.pop("artifacts", None)
    payload.pop("rows", None)
    return payload


def append_file_errors(report_path: Path, run_id: str, errors: list[FileError]) -> None:
    for item in errors:
        append_jsonl(report_path, {"type": "file_error", "run_id": run_id, "error": asdict(item)})


def append_extraction_events(report_path: Path, run_id: str, events: list[dict[str, Any]]) -> None:
    for item in events:
        append_jsonl(report_path, {"type": "extraction_event", "run_id": run_id, "event": item})


def provider_id_from_payload(payload: dict[str, Any]) -> str:
    value = payload.get("benzinga_id", payload.get("id", ""))
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value or "").strip()


def manifest_from_download(run_id: str, result: DownloadResult) -> NewsIngestManifestRow:
    return NewsIngestManifestRow(
        run_id=run_id,
        bucket_id=result.bucket_id,
        bucket_start_utc=result.start_utc,
        bucket_end_utc=result.end_utc,
        status="failed" if result.exception else "downloaded",
        downloaded_rows=result.downloaded_rows,
        normalized_rows=0,
        inserted_rows=0,
        page_count=result.page_count,
        saturated=result.saturated,
        wall_seconds=result.wall_seconds,
        exception=result.exception,
    )


def manifest_from_normalize(run_id: str, result: NormalizeResult) -> NewsIngestManifestRow:
    return NewsIngestManifestRow(
        run_id=run_id,
        bucket_id=result.bucket_id,
        bucket_start_utc=result.start_utc,
        bucket_end_utc=result.end_utc,
        status="failed" if result.exception else "normalized",
        downloaded_rows=result.downloaded_rows,
        normalized_rows=result.normalized_rows,
        inserted_rows=0,
        page_count=result.page_count,
        saturated=result.saturated,
        wall_seconds=result.wall_seconds,
        exception=result.exception,
    )


def manifest_empty_insert(run_id: str, result: DownloadResult | NormalizeResult) -> NewsIngestManifestRow:
    normalized_rows = result.normalized_rows if isinstance(result, NormalizeResult) else 0
    return NewsIngestManifestRow(
        run_id=run_id,
        bucket_id=result.bucket_id,
        bucket_start_utc=result.start_utc,
        bucket_end_utc=result.end_utc,
        status="partial" if result.saturated else "inserted",
        downloaded_rows=result.downloaded_rows,
        normalized_rows=normalized_rows,
        inserted_rows=0,
        page_count=result.page_count,
        saturated=result.saturated,
        wall_seconds=result.wall_seconds,
    )


def print_progress(
    *,
    total: int,
    download_completed: int,
    normalize_completed: int,
    final_completed: int,
    final_failed: int,
    downloaded_rows: int,
    normalized_rows: int,
    inserted_rows: int,
    pending_insert_rows: int,
    started_at: float,
) -> None:
    elapsed = time.perf_counter() - started_at
    finalized = final_completed + final_failed
    rate = finalized / elapsed if elapsed > 0 else 0.0
    remaining = total - finalized
    eta = remaining / rate if rate > 0 else 0.0
    print(
        f"[finalized {finalized:,}/{total:,}] downloaded_buckets={download_completed:,} "
        f"normalized_buckets={normalize_completed:,} completed={final_completed:,} failed={final_failed:,} "
        f"downloaded_rows={downloaded_rows:,} normalized_rows={normalized_rows:,} "
        f"inserted_rows={inserted_rows:,} insert_buffer={pending_insert_rows:,} "
        f"elapsed_min={elapsed / 60:.1f} eta_min={eta / 60:.1f}",
        flush=True,
    )


def append_fatal_error(exc: BaseException) -> None:
    if CURRENT_REPORT_PATH is None:
        return
    append_jsonl(
        CURRENT_REPORT_PATH,
        {
            "type": "fatal",
            "run_id": CURRENT_RUN_ID,
            "exception": repr(exc),
            "traceback": traceback.format_exc(),
        },
    )


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:
        append_fatal_error(exc)
        raise
