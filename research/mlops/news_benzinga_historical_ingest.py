from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
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


@dataclass(frozen=True, slots=True)
class BucketResult:
    bucket_id: str
    start_utc: str
    end_utc: str
    rows: list[dict[str, Any]]
    downloaded_rows: int
    normalized_rows: int
    page_count: int
    saturated: int
    wall_seconds: float
    exception: str = ""


@dataclass(frozen=True, slots=True)
class InsertJob:
    bucket_id: str
    start_utc: str
    end_utc: str
    rows: list[dict[str, Any]]
    clickhouse_url: str
    user: str
    password: str
    database: str
    news_table: str


@dataclass(frozen=True, slots=True)
class InsertResult:
    bucket_id: str
    start_utc: str
    end_utc: str
    inserted_rows: int
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
    parser.add_argument("--insert-concurrency", type=int, default=int(os.environ.get("NEWS_BENZINGA_INSERT_CONCURRENCY", "4")))
    parser.add_argument("--artifact-root-win", default=os.environ.get("NEWS_BENZINGA_ARTIFACT_ROOT_WIN", str(DEFAULT_ARTIFACT_ROOT_WIN)))
    parser.add_argument("--output-root-win", default=os.environ.get("NEWS_BENZINGA_OUTPUT_ROOT_WIN", str(DEFAULT_OUTPUT_ROOT_WIN)))
    parser.add_argument("--external-min-body-chars", type=int, default=int(os.environ.get("NEWS_EXTRACTION_MIN_BODY_CHARS", "300")))
    parser.add_argument("--extraction-timeout-seconds", type=float, default=float(os.environ.get("NEWS_EXTRACTION_TIMEOUT_SECONDS", "8")))
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
    loaded_env_files = load_env_files(discover_clickhouse_env_files(), verbose=True)
    args = parse_args()
    if not args.api_key:
        raise RuntimeError("MASSIVE_API_KEY is required for Benzinga historical download.")
    if not args.storage_policy:
        print("WARNING: CLICKHOUSE_LIVE_STORAGE_POLICY/--storage-policy is empty; news tables will use ClickHouse defaults.", flush=True)

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_root = Path(args.output_root_win)
    output_root.mkdir(parents=True, exist_ok=True)
    report_path = output_root / f"benzinga_historical_ingest_{run_id}.jsonl"
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
    print(f"download_processes={args.download_processes} insert_concurrency={args.insert_concurrency}", flush=True)
    print(f"fetch_external={not args.no_fetch_external} extract_pdfs={not args.no_extract_pdfs}", flush=True)
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

    completed = 0
    failed = 0
    inserted_total = 0
    normalized_total = 0
    started_at = time.perf_counter()
    insert_futures: dict[concurrent.futures.Future[InsertResult], BucketResult] = {}
    max_insert_workers = max(1, min(args.insert_concurrency, len(pending)))

    with concurrent.futures.ProcessPoolExecutor(max_workers=max(1, args.download_processes)) as download_pool, concurrent.futures.ThreadPoolExecutor(max_workers=max_insert_workers) as insert_pool:
        future_to_bucket = {download_pool.submit(process_bucket_worker, bucket): bucket for bucket in pending}
        for index, future in enumerate(concurrent.futures.as_completed(future_to_bucket), start=1):
            bucket = future_to_bucket[future]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001
                result = BucketResult(
                    bucket_id=bucket.bucket_id,
                    start_utc=bucket.start_utc,
                    end_utc=bucket.end_utc,
                    rows=[],
                    downloaded_rows=0,
                    normalized_rows=0,
                    page_count=0,
                    saturated=0,
                    wall_seconds=0.0,
                    exception=repr(exc),
                )
            normalized_total += result.normalized_rows
            append_jsonl(report_path, {"type": "bucket", "run_id": run_id, "result": bucket_result_public(result)})
            if not args.no_insert:
                insert_manifest_rows(
                    client,
                    database=args.database,
                    table=args.manifest_table,
                    rows=[
                        NewsIngestManifestRow(
                            run_id=run_id,
                            bucket_id=result.bucket_id,
                            bucket_start_utc=result.start_utc,
                            bucket_end_utc=result.end_utc,
                            status="normalized" if not result.exception else "failed",
                            downloaded_rows=result.downloaded_rows,
                            normalized_rows=result.normalized_rows,
                            page_count=result.page_count,
                            saturated=result.saturated,
                            wall_seconds=result.wall_seconds,
                            exception=result.exception,
                        )
                    ],
                )
            if result.exception:
                failed += 1
            elif args.no_insert:
                completed += 1
            elif result.rows:
                insert_job = InsertJob(
                    bucket_id=result.bucket_id,
                    start_utc=result.start_utc,
                    end_utc=result.end_utc,
                    rows=result.rows,
                    clickhouse_url=args.clickhouse_url,
                    user=args.user,
                    password=args.password,
                    database=args.database,
                    news_table=args.news_table,
                )
                insert_futures[insert_pool.submit(insert_rows_worker, insert_job)] = result
            else:
                completed += 1
                insert_manifest_rows(
                    client,
                    database=args.database,
                    table=args.manifest_table,
                    rows=[
                        NewsIngestManifestRow(
                            run_id=run_id,
                            bucket_id=result.bucket_id,
                            bucket_start_utc=result.start_utc,
                            bucket_end_utc=result.end_utc,
                            status="inserted",
                            downloaded_rows=result.downloaded_rows,
                            normalized_rows=result.normalized_rows,
                            inserted_rows=0,
                            page_count=result.page_count,
                            saturated=result.saturated,
                            wall_seconds=result.wall_seconds,
                        )
                    ],
                )
            insert_completed, insert_failed, insert_rows = drain_finished_inserts(
                insert_futures,
                client,
                args,
                run_id,
                report_path,
                block=False,
            )
            completed += insert_completed
            failed += insert_failed
            inserted_total += insert_rows
            print_progress(index, len(pending), completed, failed, normalized_total, inserted_total, started_at)

        while insert_futures:
            insert_completed, insert_failed, insert_rows = drain_finished_inserts(
                insert_futures,
                client,
                args,
                run_id,
                report_path,
                block=True,
            )
            completed += insert_completed
            failed += insert_failed
            inserted_total += insert_rows

    elapsed = time.perf_counter() - started_at
    print("=" * 96, flush=True)
    print(f"DONE completed={completed:,} failed={failed:,} normalized_rows={normalized_total:,} inserted_rows={inserted_total:,}", flush=True)
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
            )
        )
        current = bucket_end
    if args.limit_buckets > 0:
        jobs = jobs[: args.limit_buckets]
    return jobs


def process_bucket_worker(job: BucketJob) -> BucketResult:
    started_at = time.perf_counter()
    artifact_root = Path(job.artifact_root_win)
    rows: list[dict[str, Any]] = []
    downloaded_rows = 0
    page_count = 0
    saturated = 0
    next_url: str | None = build_benzinga_url(job.endpoint_url, job.api_key, job.start_utc, job.end_utc, job.limit)
    options = NewsExtractionOptions(
        fetch_external=job.fetch_external,
        extract_pdfs=job.extract_pdfs,
        external_min_body_chars=job.external_min_body_chars,
        request_timeout_seconds=job.extraction_timeout_seconds,
        max_pdf_bytes=job.max_pdf_bytes,
        text_limit_chars=job.text_limit_chars,
    )
    try:
        while next_url and page_count < job.max_pages:
            page_count += 1
            response = fetch_json(next_url)
            items = response.get("results") or []
            downloaded_rows += len(items)
            downloaded_at = datetime.now(UTC)
            for item in items:
                if not isinstance(item, dict):
                    continue
                try:
                    published = parse_provider_datetime(str(item.get("published") or ""))
                except Exception:
                    published = datetime.now(UTC)
                raw_path = artifact_path_for_payload(artifact_root, item, published)
                raw_hash = write_raw_payload(raw_path, item)
                row = normalize_benzinga_payload(
                    item,
                    raw_artifact_path=str(raw_path),
                    raw_payload_hash=raw_hash,
                    downloaded_at_utc=downloaded_at,
                    artifact_root=artifact_root,
                    options=options,
                )
                rows.append(row)
            next_url = response.get("next_url")
            if next_url:
                next_url = append_api_key(str(next_url), job.api_key)
        if next_url:
            saturated = 1
        return BucketResult(
            bucket_id=job.bucket_id,
            start_utc=job.start_utc,
            end_utc=job.end_utc,
            rows=rows,
            downloaded_rows=downloaded_rows,
            normalized_rows=len(rows),
            page_count=page_count,
            saturated=saturated,
            wall_seconds=time.perf_counter() - started_at,
        )
    except Exception as exc:  # noqa: BLE001
        return BucketResult(
            bucket_id=job.bucket_id,
            start_utc=job.start_utc,
            end_utc=job.end_utc,
            rows=rows,
            downloaded_rows=downloaded_rows,
            normalized_rows=len(rows),
            page_count=page_count,
            saturated=saturated,
            wall_seconds=time.perf_counter() - started_at,
            exception=repr(exc),
        )


def insert_rows_worker(job: InsertJob) -> InsertResult:
    started_at = time.perf_counter()
    try:
        client = ClickHouseHttpClient(job.clickhouse_url, job.user, job.password)
        inserted = insert_news_rows(client, database=job.database, table=job.news_table, rows=job.rows)
        return InsertResult(
            bucket_id=job.bucket_id,
            start_utc=job.start_utc,
            end_utc=job.end_utc,
            inserted_rows=inserted,
            wall_seconds=time.perf_counter() - started_at,
        )
    except Exception as exc:  # noqa: BLE001
        return InsertResult(
            bucket_id=job.bucket_id,
            start_utc=job.start_utc,
            end_utc=job.end_utc,
            inserted_rows=0,
            wall_seconds=time.perf_counter() - started_at,
            exception=repr(exc),
        )


def drain_finished_inserts(
    futures: dict[concurrent.futures.Future[InsertResult], BucketResult],
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    run_id: str,
    report_path: Path,
    *,
    block: bool,
) -> tuple[int, int, int]:
    if not futures:
        return 0, 0, 0
    done: list[concurrent.futures.Future[InsertResult]] = []
    if block:
        done = [next(concurrent.futures.as_completed(futures))]
    else:
        done = [future for future in futures if future.done()]
    completed = 0
    failed = 0
    inserted_rows = 0
    for future in done:
        bucket_result = futures.pop(future)
        result = future.result()
        status = "inserted"
        exception = result.exception
        if result.exception:
            status = "failed"
            failed += 1
        else:
            completed += 1
            inserted_rows += result.inserted_rows
            if bucket_result.saturated:
                status = "partial"
        insert_manifest_rows(
            client,
            database=args.database,
            table=args.manifest_table,
            rows=[
                NewsIngestManifestRow(
                    run_id=run_id,
                    bucket_id=result.bucket_id,
                    bucket_start_utc=result.start_utc,
                    bucket_end_utc=result.end_utc,
                    status=status,
                    downloaded_rows=bucket_result.downloaded_rows,
                    normalized_rows=bucket_result.normalized_rows,
                    inserted_rows=result.inserted_rows,
                    page_count=bucket_result.page_count,
                    saturated=bucket_result.saturated,
                    wall_seconds=result.wall_seconds,
                    exception=exception,
                )
            ],
        )
        append_jsonl(report_path, {"type": "insert", "run_id": run_id, "result": asdict(result), "status": status})
    return completed, failed, inserted_rows


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
    try:
        with request.urlopen(req, timeout=60) as response:  # noqa: S310
            body = response.read().decode("utf-8", errors="replace")
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Massive Benzinga HTTP {exc.code}: {body}") from exc
    value = json.loads(body)
    if not isinstance(value, dict):
        raise RuntimeError("Massive Benzinga response was not a JSON object")
    return value


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


def bucket_result_public(result: BucketResult) -> dict[str, Any]:
    payload = asdict(result)
    payload.pop("rows", None)
    return payload


def print_progress(
    index: int,
    total: int,
    completed: int,
    failed: int,
    normalized_rows: int,
    inserted_rows: int,
    started_at: float,
) -> None:
    elapsed = time.perf_counter() - started_at
    rate = index / elapsed if elapsed > 0 else 0.0
    remaining = total - index
    eta = remaining / rate if rate > 0 else 0.0
    print(
        f"[{index:,}/{total:,}] completed={completed:,} failed={failed:,} "
        f"normalized={normalized_rows:,} inserted={inserted_rows:,} "
        f"elapsed_min={elapsed / 60:.1f} eta_min={eta / 60:.1f}",
        flush=True,
    )


if __name__ == "__main__":
    main()
