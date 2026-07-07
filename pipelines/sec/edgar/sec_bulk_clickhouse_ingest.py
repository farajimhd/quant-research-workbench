from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import URLError
from zoneinfo import ZoneInfo


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
from pipelines.sec.edgar.sec_initial_fill_download import (  # noqa: E402
    SEC_BULK_BASE_URL,
    SEC_FILES_BASE_URL,
    is_g_drive_path,
    sha256_file,
)


DEFAULT_DATABASE = "sec_core"
DEFAULT_ARTIFACT_ROOT_WIN = Path("D:/market-data/sec_core")
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_core")
DEFAULT_BATCH_SIZE = 50_000
DEFAULT_INSERT_MAX_RETRIES = 12
DEFAULT_INSERT_RETRY_BASE_SECONDS = 5.0
DEFAULT_INSERT_RETRY_MAX_SECONDS = 120.0
MEMBER_MANIFEST_TABLE = "sec_bulk_mirror_member_manifest_v1"
SEC_ET = ZoneInfo("America/New_York")
SOURCE_URLS = {
    "submissions": f"{SEC_BULK_BASE_URL}/bulkdata/submissions.zip",
    "companyfacts": f"{SEC_BULK_BASE_URL}/xbrl/companyfacts.zip",
    "company_tickers": f"{SEC_FILES_BASE_URL}/company_tickers.json",
    "company_tickers_exchange": f"{SEC_FILES_BASE_URL}/company_tickers_exchange.json",
    "company_tickers_mf": f"{SEC_FILES_BASE_URL}/company_tickers_mf.json",
}


@dataclass(frozen=True, slots=True)
class SourceArtifact:
    source_name: str
    source_kind: str
    source_url: str
    path: Path
    source_file_id: str
    byte_size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class InsertRetryConfig:
    max_retries: int
    base_seconds: float
    max_seconds: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create sec_core ClickHouse SEC bulk mirror tables and insert SEC bulk metadata. Daily EDGAR feed archives are not used."
    )
    parser.add_argument("--clickhouse-url", default=default_sec_clickhouse_url())
    parser.add_argument("--user", default=default_sec_clickhouse_user())
    parser.add_argument("--password", default=default_sec_clickhouse_password())
    parser.add_argument("--database", default=os.environ.get("SEC_CLICKHOUSE_DATABASE", DEFAULT_DATABASE))
    parser.add_argument("--storage-policy", default=default_sec_storage_policy())
    parser.add_argument("--artifact-root-win", default=os.environ.get("SEC_CORE_ARTIFACT_ROOT_WIN", str(DEFAULT_ARTIFACT_ROOT_WIN)))
    parser.add_argument("--output-root-win", default=os.environ.get("SEC_CORE_OUTPUT_ROOT_WIN", str(DEFAULT_OUTPUT_ROOT_WIN)))
    parser.add_argument(
        "--sources",
        default="company_tickers,company_tickers_exchange,company_tickers_mf,submissions,companyfacts",
        help="Comma-separated subset of company_tickers,company_tickers_exchange,company_tickers_mf,submissions,companyfacts.",
    )
    parser.add_argument("--batch-size", type=int, default=int(os.environ.get("SEC_BULK_INGEST_BATCH_SIZE", str(DEFAULT_BATCH_SIZE))))
    parser.add_argument("--insert-max-retries", type=int, default=int(os.environ.get("SEC_BULK_INSERT_MAX_RETRIES", str(DEFAULT_INSERT_MAX_RETRIES))))
    parser.add_argument(
        "--insert-retry-base-seconds",
        type=float,
        default=float(os.environ.get("SEC_BULK_INSERT_RETRY_BASE_SECONDS", str(DEFAULT_INSERT_RETRY_BASE_SECONDS))),
    )
    parser.add_argument(
        "--insert-retry-max-seconds",
        type=float,
        default=float(os.environ.get("SEC_BULK_INSERT_RETRY_MAX_SECONDS", str(DEFAULT_INSERT_RETRY_MAX_SECONDS))),
    )
    parser.add_argument("--limit-ciks", type=int, default=0, help="Debug cap for submissions/companyfacts CIK JSON files.")
    parser.add_argument(
        "--disable-member-manifest",
        action="store_true",
        default=os.environ.get("SEC_BULK_MEMBER_MANIFEST_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"},
        help="Fallback to the legacy full ZIP parse/insert path instead of skipping completed ZIP members.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-g-drive", action="store_true")
    return parser.parse_args()


def main() -> None:
    loaded_env_files = load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    validate_args(args)
    artifact_root = Path(args.artifact_root_win)
    output_root = Path(args.output_root_win)
    output_root.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    report_path = output_root / f"sec_bulk_clickhouse_ingest_{run_id}.jsonl"
    sources = parse_sources(args.sources)
    artifacts = discover_artifacts(artifact_root, sources)

    print_header(args, loaded_env_files, artifacts, report_path)
    if args.dry_run:
        write_report(report_path, {"run_id": run_id, "status": "dry_run", "artifacts": [artifact_report(item) for item in artifacts]})
        return

    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    retry = InsertRetryConfig(
        max_retries=max(0, args.insert_max_retries),
        base_seconds=max(0.0, args.insert_retry_base_seconds),
        max_seconds=max(0.0, args.insert_retry_max_seconds),
    )
    create_database_and_tables(client, args.database, args.storage_policy)
    insert_raw_source_rows(client, args.database, artifacts, retry)
    if not args.disable_member_manifest:
        bootstrap_member_manifests(client, args.database, artifacts, retry)

    totals: dict[str, int] = {}
    for artifact in artifacts:
        started = time.perf_counter()
        if artifact.source_name in {"company_tickers", "company_tickers_exchange", "company_tickers_mf"}:
            rows = parse_ticker_mapping(artifact)
            inserted = insert_rows(client, args.database, "sec_bulk_mirror_company_ticker_v1", rows, retry)
        elif artifact.source_name == "submissions":
            inserted = ingest_submissions_zip(client, args.database, artifact, args.batch_size, args.limit_ciks, retry, not args.disable_member_manifest)
        elif artifact.source_name == "companyfacts":
            inserted = ingest_companyfacts_zip(client, args.database, artifact, args.batch_size, args.limit_ciks, retry, not args.disable_member_manifest)
        else:
            inserted = 0
        totals[artifact.source_name] = inserted
        row = {
            "run_id": run_id,
            "source": artifact.source_name,
            "source_file_id": artifact.source_file_id,
            "inserted_rows": inserted,
            "wall_seconds": round(time.perf_counter() - started, 3),
            "status": "ok",
        }
        write_report(report_path, row)
        print(json.dumps(row, sort_keys=True), flush=True)

    summary = {"run_id": run_id, "status": "ok", "totals": totals, "report_path": str(report_path)}
    write_report(report_path, summary)
    print("summary=" + json.dumps(summary, sort_keys=True), flush=True)


def default_sec_clickhouse_url() -> str:
    return os.environ.get("SEC_CLICKHOUSE_URL") or os.environ.get("QMD_CLICKHOUSE_URL") or default_clickhouse_url()


def default_sec_clickhouse_user() -> str:
    return os.environ.get("SEC_CLICKHOUSE_USER") or os.environ.get("QMD_CLICKHOUSE_USER") or default_clickhouse_user()


def default_sec_clickhouse_password() -> str:
    return os.environ.get("SEC_CLICKHOUSE_PASSWORD") or os.environ.get("QMD_CLICKHOUSE_PASSWORD") or default_clickhouse_password()


def default_sec_storage_policy() -> str:
    return os.environ.get("SEC_CLICKHOUSE_STORAGE_POLICY") or os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY") or os.environ.get("CLICKHOUSE_STORAGE_POLICY") or ""


def validate_args(args: argparse.Namespace) -> None:
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be >= 1")
    if args.insert_max_retries < 0:
        raise SystemExit("--insert-max-retries must be >= 0")
    if args.insert_retry_base_seconds < 0:
        raise SystemExit("--insert-retry-base-seconds must be >= 0")
    if args.insert_retry_max_seconds < 0:
        raise SystemExit("--insert-retry-max-seconds must be >= 0")
    if not args.allow_g_drive:
        for label, raw_path in [("artifact root", args.artifact_root_win), ("output root", args.output_root_win)]:
            if is_g_drive_path(Path(raw_path)):
                raise SystemExit(f"{label} points to G:, which is blocked for SEC bulk ingest: {raw_path}")


def parse_sources(text: str) -> list[str]:
    allowed = {"company_tickers", "company_tickers_exchange", "company_tickers_mf", "submissions", "companyfacts"}
    sources = [item.strip() for item in text.split(",") if item.strip()]
    invalid = sorted(set(sources) - allowed)
    if invalid:
        raise SystemExit(f"Invalid --sources: {invalid}; expected subset of {sorted(allowed)}")
    return sources


def discover_artifacts(root: Path, sources: list[str]) -> list[SourceArtifact]:
    paths = {
        "submissions": root / "bulk" / "submissions" / "submissions.zip",
        "companyfacts": root / "bulk" / "companyfacts" / "companyfacts.zip",
        "company_tickers": root / "bulk" / "mappings" / "company_tickers.json",
        "company_tickers_exchange": root / "bulk" / "mappings" / "company_tickers_exchange.json",
        "company_tickers_mf": root / "bulk" / "mappings" / "company_tickers_mf.json",
    }
    kinds = {
        "submissions": "submissions_bulk",
        "companyfacts": "companyfacts_bulk",
        "company_tickers": "company_tickers",
        "company_tickers_exchange": "company_tickers_exchange",
        "company_tickers_mf": "company_tickers_mf",
    }
    artifacts: list[SourceArtifact] = []
    missing: list[str] = []
    for source in sources:
        path = paths[source]
        if not path.exists():
            missing.append(f"{source}: {path}")
            continue
        sha = sha256_file(path)
        source_id = hashlib.sha256(f"{kinds[source]}|{SOURCE_URLS[source]}|{path}|{sha}".encode("utf-8")).hexdigest()
        artifacts.append(
            SourceArtifact(
                source_name=source,
                source_kind=kinds[source],
                source_url=SOURCE_URLS[source],
                path=path,
                source_file_id=source_id,
                byte_size=path.stat().st_size,
                sha256=sha,
            )
        )
    if missing:
        raise SystemExit("Missing SEC bulk artifacts:\n" + "\n".join(missing))
    return artifacts


def create_database_and_tables(client: ClickHouseHttpClient, database: str, storage_policy: str) -> None:
    client.execute(f"CREATE DATABASE IF NOT EXISTS {quote_ident(database)}")
    for sql in [
        raw_source_file_table_sql(database, storage_policy),
        company_table_sql(database, storage_policy),
        ticker_table_sql(database, storage_policy),
        submission_file_ref_table_sql(database, storage_policy),
        filing_table_sql(database, storage_policy),
        xbrl_fact_table_sql(database, storage_policy),
        member_manifest_table_sql(database, storage_policy),
    ]:
        client.execute(sql)


def insert_raw_source_rows(client: ClickHouseHttpClient, database: str, artifacts: list[SourceArtifact], retry: InsertRetryConfig) -> None:
    now = clickhouse_datetime64_now()
    rows = [
        {
            "source_file_id": item.source_file_id,
            "source_kind": item.source_kind,
            "source_url": item.source_url,
            "artifact_path": str(item.path),
            "source_date": None,
            "downloaded_at_utc": now,
            "byte_size": item.byte_size,
            "sha256": item.sha256,
            "status": "ok",
            "error": "",
        }
        for item in artifacts
    ]
    insert_rows(client, database, "sec_bulk_mirror_raw_source_file_v1", rows, retry)


def bootstrap_member_manifests(client: ClickHouseHttpClient, database: str, artifacts: list[SourceArtifact], retry: InsertRetryConfig) -> None:
    for artifact in artifacts:
        if artifact.source_name not in {"submissions", "companyfacts"}:
            continue
        existing = scalar_int(
            client,
            f"""
            SELECT count()
            FROM {quote_ident(database)}.{quote_ident(MEMBER_MANIFEST_TABLE)} FINAL
            WHERE source_name = {sql_string(artifact.source_name)}
              AND source_file_id = {sql_string(artifact.source_file_id)}
              AND status = 'completed'
            """,
        )
        if existing:
            continue
        completion_state = load_existing_completion_state(client, database, artifact)
        if not completion_state:
            print(f"member_manifest_bootstrap source={artifact.source_name} current_source_file_id_has_no_existing_mirror_rows", flush=True)
            continue
        rows: list[dict[str, Any]] = []
        now = clickhouse_datetime64_now()
        completed_ciks = 0
        with zipfile.ZipFile(artifact.path) as archive:
            infos = sorted((info for info in archive.infolist() if info.filename.lower().endswith(".json")), key=lambda item: item.filename)
            for info in infos:
                cik = cik_from_member_name(info.filename)
                if not existing_member_is_complete(artifact, archive, info, cik, completion_state):
                    continue
                completed_ciks += 1
                rows.append(member_manifest_row(artifact, info, cik, now, status="completed", rows_inserted=0, error="bootstrap_from_existing_mirror_rows"))
                if len(rows) >= 10_000:
                    insert_rows(client, database, MEMBER_MANIFEST_TABLE, rows, retry)
                    rows.clear()
        insert_rows(client, database, MEMBER_MANIFEST_TABLE, rows, retry)
        print(f"member_manifest_bootstrap source={artifact.source_name} completed_ciks={completed_ciks:,}", flush=True)


def load_existing_completion_state(client: ClickHouseHttpClient, database: str, artifact: SourceArtifact) -> dict[str, Any]:
    if artifact.source_name == "submissions":
        company_ciks = load_distinct_ciks(client, database, "sec_bulk_mirror_company_v1", artifact.source_file_id)
        filing_counts = load_counts_by_cik(client, database, "sec_bulk_mirror_filing_v1", artifact.source_file_id)
        return {"company_ciks": company_ciks, "filing_counts": filing_counts}
    elif artifact.source_name == "companyfacts":
        fact_counts = load_counts_by_cik(client, database, "sec_bulk_mirror_xbrl_fact_v1", artifact.source_file_id)
        return {"fact_counts": fact_counts}
    return {}


def load_distinct_ciks(client: ClickHouseHttpClient, database: str, table: str, source_file_id: str) -> set[str]:
    out = client.execute(
        f"""
        SELECT DISTINCT cik
        FROM {quote_ident(database)}.{quote_ident(table)} FINAL
        WHERE source_file_id = {sql_string(source_file_id)}
          AND cik != ''
        FORMAT TSV
        """
    )
    return {line.strip() for line in out.splitlines() if line.strip()}


def load_counts_by_cik(client: ClickHouseHttpClient, database: str, table: str, source_file_id: str) -> dict[str, int]:
    out = client.execute(
        f"""
        SELECT cik, count()
        FROM {quote_ident(database)}.{quote_ident(table)} FINAL
        WHERE source_file_id = {sql_string(source_file_id)}
          AND cik != ''
        GROUP BY cik
        FORMAT TSV
        """
    )
    counts: dict[str, int] = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 2:
            counts[parts[0]] = int(parts[1])
    return counts


def existing_member_is_complete(
    artifact: SourceArtifact,
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    cik: str,
    completion_state: dict[str, Any],
) -> bool:
    if artifact.source_name == "submissions":
        if cik not in completion_state.get("company_ciks", set()):
            return False
        data = json.loads(archive.read(info).decode("utf-8", errors="replace"))
        expected_filings = expected_submission_filing_count(data)
        return completion_state.get("filing_counts", {}).get(cik, 0) >= expected_filings
    if artifact.source_name == "companyfacts":
        data = json.loads(archive.read(info).decode("utf-8", errors="replace"))
        expected_facts = expected_companyfacts_fact_count(data)
        return expected_facts == 0 or completion_state.get("fact_counts", {}).get(cik, 0) >= expected_facts
    return False


def load_completed_member_signatures(client: ClickHouseHttpClient, database: str, artifact: SourceArtifact) -> set[str]:
    if artifact.source_name not in {"submissions", "companyfacts"}:
        return set()
    out = client.execute(
        f"""
        SELECT member_signature
        FROM {quote_ident(database)}.{quote_ident(MEMBER_MANIFEST_TABLE)} FINAL
        WHERE source_name = {sql_string(artifact.source_name)}
          AND source_file_id = {sql_string(artifact.source_file_id)}
          AND status = 'completed'
        FORMAT TSV
        """
    )
    return {line.strip() for line in out.splitlines() if line.strip()}


def scalar_int(client: ClickHouseHttpClient, sql: str) -> int:
    out = client.execute(sql + "\nFORMAT TSV").strip()
    return int(out or "0")


def member_signature(artifact: SourceArtifact, info: zipfile.ZipInfo) -> str:
    payload = "|".join(
        [
            artifact.source_name,
            artifact.source_file_id,
            info.filename,
            str(info.CRC),
            str(info.file_size),
            str(info.compress_size),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def member_manifest_row(
    artifact: SourceArtifact,
    info: zipfile.ZipInfo,
    cik: str,
    now: str,
    *,
    status: str,
    rows_inserted: int,
    error: str,
) -> dict[str, Any]:
    return {
        "source_name": artifact.source_name,
        "source_kind": artifact.source_kind,
        "source_file_id": artifact.source_file_id,
        "member_name": info.filename,
        "cik": cik,
        "member_crc": int(info.CRC),
        "member_file_size": int(info.file_size),
        "member_compress_size": int(info.compress_size),
        "member_modified_at_utc": zip_member_modified_at(info),
        "member_signature": member_signature(artifact, info),
        "status": status,
        "rows_inserted": max(0, int(rows_inserted)),
        "processed_at_utc": now,
        "error": error,
    }


def zip_member_modified_at(info: zipfile.ZipInfo) -> str | None:
    try:
        year, month, day, hour, minute, second = info.date_time
        return datetime(year, month, day, hour, minute, second, tzinfo=UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
    except (TypeError, ValueError):
        return None


def cik_from_member_name(name: str) -> str:
    match = re.search(r"(\d{1,10})", Path(name).stem)
    return cik10(match.group(1)) if match else ""


def parse_ticker_mapping(artifact: SourceArtifact) -> list[dict[str, Any]]:
    payload = json.loads(artifact.path.read_text(encoding="utf-8"))
    now = clickhouse_datetime64_now()
    rows: list[dict[str, Any]] = []
    if artifact.source_name == "company_tickers_mf":
        items = payload.values() if isinstance(payload, dict) else payload
        for item in items:
            cik = cik10(item.get("cik") or item.get("cik_str") or item.get("CIK", ""))
            ticker = clean_string(item.get("ticker") or item.get("Ticker", ""))
            series_id = clean_string(item.get("seriesId") or item.get("series_id") or item.get("series", ""))
            class_id = clean_string(item.get("classId") or item.get("class_id") or item.get("class", ""))
            rows.append(ticker_row(artifact, cik, ticker, "", clean_string(item.get("title") or item.get("companyName", "")), series_id, class_id, now))
        return rows
    items = payload.values() if isinstance(payload, dict) else payload
    for item in items:
        cik = cik10(item.get("cik_str") or item.get("cik") or item.get("CIK", ""))
        ticker = clean_string(item.get("ticker") or item.get("Ticker", ""))
        exchange = clean_string(item.get("exchange", ""))
        company_name = clean_string(item.get("title") or item.get("name") or item.get("companyName", ""))
        rows.append(ticker_row(artifact, cik, ticker, exchange, company_name, "", "", now))
    return rows


def ticker_row(
    artifact: SourceArtifact,
    cik: str,
    ticker: str,
    exchange: str,
    company_name: str,
    series_id: str,
    class_id: str,
    now: str,
) -> dict[str, Any]:
    mapping_key = f"{artifact.source_name}|{cik}|{ticker}|{exchange}|{series_id}|{class_id}"
    return {
        "mapping_id": hashlib.sha256(mapping_key.encode("utf-8")).hexdigest(),
        "cik": cik,
        "ticker": ticker.upper(),
        "exchange": exchange or None,
        "company_name": company_name,
        "mapping_source": artifact.source_kind,
        "series_id": series_id or None,
        "class_id": class_id or None,
        "first_seen_at_utc": now,
        "last_seen_at_utc": now,
        "is_active": 1,
        "source_file_id": artifact.source_file_id,
    }


def ingest_submissions_zip(
    client: ClickHouseHttpClient,
    database: str,
    artifact: SourceArtifact,
    batch_size: int,
    limit_ciks: int,
    retry: InsertRetryConfig,
    use_member_manifest: bool,
) -> int:
    company_batch: list[dict[str, Any]] = []
    filing_batch: list[dict[str, Any]] = []
    file_ref_batch: list[dict[str, Any]] = []
    manifest_batch: list[dict[str, Any]] = []
    completed_signatures = load_completed_member_signatures(client, database, artifact) if use_member_manifest else set()
    inserted = 0
    processed = 0
    skipped = 0
    now = clickhouse_datetime64_now()
    with zipfile.ZipFile(artifact.path) as archive:
        infos = sorted((info for info in archive.infolist() if info.filename.lower().endswith(".json")), key=lambda item: item.filename)
        for info in infos:
            if limit_ciks and processed >= limit_ciks:
                break
            name = info.filename
            signature = member_signature(artifact, info)
            if signature in completed_signatures:
                skipped += 1
                if skipped % 25_000 == 0:
                    print(f"submissions skipped_completed_members={skipped:,} processed_ciks={processed:,}", flush=True)
                continue
            data = json.loads(archive.read(info).decode("utf-8", errors="replace"))
            cik = cik10(data.get("cik") or data.get("cik_str") or cik_from_member_name(name))
            company_batch.append(company_row(data, cik, artifact.source_file_id, now))
            file_ref_batch.extend(submission_file_ref_rows(data, cik, artifact.source_file_id, now))
            filing_rows = submission_filing_rows(data, cik, artifact, now)
            filing_batch.extend(filing_rows)
            if use_member_manifest:
                manifest_batch.append(member_manifest_row(artifact, info, cik, now, status="completed", rows_inserted=1 + len(filing_rows), error=""))
            processed += 1
            if len(company_batch) >= batch_size:
                inserted += flush(client, database, "sec_bulk_mirror_company_v1", company_batch, retry)
            if len(file_ref_batch) >= batch_size:
                inserted += flush(client, database, "sec_bulk_mirror_submission_file_ref_v1", file_ref_batch, retry)
            if len(filing_batch) >= batch_size:
                inserted += flush(client, database, "sec_bulk_mirror_filing_v1", filing_batch, retry)
            if use_member_manifest and len(manifest_batch) >= 5_000:
                inserted += flush_member_manifest(client, database, retry, company_batch, file_ref_batch, filing_batch, manifest_batch)
            if processed % 5_000 == 0:
                print(f"submissions processed_ciks={processed:,} skipped_completed={skipped:,} pending_filings={len(filing_batch):,}", flush=True)
    inserted += flush(client, database, "sec_bulk_mirror_company_v1", company_batch, retry)
    inserted += flush(client, database, "sec_bulk_mirror_submission_file_ref_v1", file_ref_batch, retry)
    inserted += flush(client, database, "sec_bulk_mirror_filing_v1", filing_batch, retry)
    if use_member_manifest:
        insert_rows(client, database, MEMBER_MANIFEST_TABLE, manifest_batch, retry)
    if skipped:
        print(f"submissions skipped_completed_members={skipped:,}", flush=True)
    return inserted


def company_row(data: dict[str, Any], cik: str, source_file_id: str, now: str) -> dict[str, Any]:
    return {
        "cik": cik,
        "entity_name": clean_string(data.get("name", "")),
        "sic": nullable_string(data.get("sic")),
        "sic_description": nullable_string(data.get("sicDescription")),
        "ein": nullable_string(data.get("ein")),
        "category": nullable_string(data.get("category")),
        "fiscal_year_end": nullable_string(data.get("fiscalYearEnd")),
        "state_of_incorporation": nullable_string(data.get("stateOfIncorporation")),
        "addresses_json": compact_json(data.get("addresses", {})),
        "former_names_json": compact_json(data.get("formerNames", [])),
        "source_file_id": source_file_id,
        "last_seen_at_utc": now,
    }


def submission_file_ref_rows(data: dict[str, Any], cik: str, source_file_id: str, now: str) -> list[dict[str, Any]]:
    rows = []
    for item in data.get("filings", {}).get("files", []) or []:
        name = clean_string(item.get("name", ""))
        file_key = f"{cik}|{name}"
        rows.append(
            {
                "file_ref_id": hashlib.sha256(file_key.encode("utf-8")).hexdigest(),
                "cik": cik,
                "file_name": name,
                "filing_count": int_or_zero(item.get("filingCount")),
                "filing_from": nullable_date(item.get("filingFrom")),
                "filing_to": nullable_date(item.get("filingTo")),
                "source_file_id": source_file_id,
                "last_seen_at_utc": now,
            }
        )
    return rows


def expected_submission_filing_count(data: dict[str, Any]) -> int:
    recent = data.get("filings", {}).get("recent", {}) or {}
    accessions = recent.get("accessionNumber", [])
    if isinstance(accessions, list):
        return sum(1 for accession in accessions if clean_string(accession))
    return 0


def submission_filing_rows(data: dict[str, Any], cik: str, artifact: SourceArtifact, now: str) -> list[dict[str, Any]]:
    recent = data.get("filings", {}).get("recent", {}) or {}
    if not recent:
        return []
    lengths = [len(value) for value in recent.values() if isinstance(value, list)]
    count = max(lengths) if lengths else 0
    company_name = clean_string(data.get("name", ""))
    rows: list[dict[str, Any]] = []
    for index in range(count):
        accession = recent_value(recent, "accessionNumber", index)
        if not accession:
            continue
        accession_compact = accession.replace("-", "")
        primary_document = recent_value(recent, "primaryDocument", index)
        accepted_raw = recent_value(recent, "acceptanceDateTime", index)
        row = {
            "accession_number": accession,
            "accession_number_compact": accession_compact,
            "cik": cik,
            "company_name": company_name,
            "form_type": recent_value(recent, "form", index),
            "filing_date": nullable_date(recent_value(recent, "filingDate", index)),
            "report_date": nullable_date(recent_value(recent, "reportDate", index)),
            "accepted_at_utc": accepted_at_utc(accepted_raw),
            "acceptance_datetime_raw": accepted_raw or None,
            "accepted_at_source": "submissions_bulk" if accepted_raw else "missing",
            "primary_document": primary_document or None,
            "primary_document_url": filing_document_url(cik, accession_compact, primary_document) if primary_document else None,
            "filing_detail_url": filing_detail_url(cik, accession_compact),
            "document_count": None,
            "filing_size": int_or_none(recent_value(recent, "size", index)),
            "items": nullable_string(recent_value(recent, "items", index)),
            "act": nullable_string(recent_value(recent, "act", index)),
            "file_number": nullable_string(recent_value(recent, "fileNumber", index)),
            "film_number": nullable_string(recent_value(recent, "filmNumber", index)),
            "source_kind": artifact.source_kind,
            "source_file_id": artifact.source_file_id,
            "raw_submission_json": compact_json({key: recent_value(recent, key, index) for key in recent}),
            "last_seen_at_utc": now,
        }
        rows.append(row)
    return rows


def expected_companyfacts_fact_count(data: dict[str, Any]) -> int:
    count = 0
    for tags in (data.get("facts", {}) or {}).values():
        if not isinstance(tags, dict):
            continue
        for tag_payload in tags.values():
            for facts in (tag_payload.get("units", {}) or {}).values():
                if isinstance(facts, list):
                    count += len(facts)
    return count


def ingest_companyfacts_zip(
    client: ClickHouseHttpClient,
    database: str,
    artifact: SourceArtifact,
    batch_size: int,
    limit_ciks: int,
    retry: InsertRetryConfig,
    use_member_manifest: bool,
) -> int:
    batch: list[dict[str, Any]] = []
    manifest_batch: list[dict[str, Any]] = []
    completed_signatures = load_completed_member_signatures(client, database, artifact) if use_member_manifest else set()
    inserted = 0
    processed = 0
    skipped = 0
    now = clickhouse_datetime64_now()
    with zipfile.ZipFile(artifact.path) as archive:
        infos = sorted((info for info in archive.infolist() if info.filename.lower().endswith(".json")), key=lambda item: item.filename)
        for info in infos:
            if limit_ciks and processed >= limit_ciks:
                break
            signature = member_signature(artifact, info)
            if signature in completed_signatures:
                skipped += 1
                if skipped % 10_000 == 0:
                    print(f"companyfacts skipped_completed_members={skipped:,} processed_ciks={processed:,}", flush=True)
                continue
            data = json.loads(archive.read(info).decode("utf-8", errors="replace"))
            cik = cik10(data.get("cik") or cik_from_member_name(info.filename))
            entity_name = clean_string(data.get("entityName", ""))
            member_rows = 0
            for taxonomy, tags in (data.get("facts", {}) or {}).items():
                if not isinstance(tags, dict):
                    continue
                for tag, tag_payload in tags.items():
                    label = clean_string(tag_payload.get("label", ""))
                    description = clean_string(tag_payload.get("description", ""))
                    for unit, facts in (tag_payload.get("units", {}) or {}).items():
                        if not isinstance(facts, list):
                            continue
                        for fact in facts:
                            batch.append(xbrl_fact_row(cik, entity_name, taxonomy, tag, label, description, unit, fact, artifact.source_file_id, now))
                            member_rows += 1
                            if len(batch) >= batch_size:
                                inserted += flush(client, database, "sec_bulk_mirror_xbrl_fact_v1", batch, retry)
            if use_member_manifest:
                manifest_batch.append(member_manifest_row(artifact, info, cik, now, status="completed", rows_inserted=member_rows, error=""))
            processed += 1
            if use_member_manifest and len(manifest_batch) >= 5_000:
                inserted += flush(client, database, "sec_bulk_mirror_xbrl_fact_v1", batch, retry)
                insert_rows(client, database, MEMBER_MANIFEST_TABLE, manifest_batch, retry)
                manifest_batch.clear()
            if processed % 1_000 == 0:
                print(f"companyfacts processed_ciks={processed:,} skipped_completed={skipped:,} pending_facts={len(batch):,}", flush=True)
    inserted += flush(client, database, "sec_bulk_mirror_xbrl_fact_v1", batch, retry)
    if use_member_manifest:
        insert_rows(client, database, MEMBER_MANIFEST_TABLE, manifest_batch, retry)
    if skipped:
        print(f"companyfacts skipped_completed_members={skipped:,}", flush=True)
    return inserted


def xbrl_fact_row(
    cik: str,
    entity_name: str,
    taxonomy: str,
    tag: str,
    label: str,
    description: str,
    unit: str,
    fact: dict[str, Any],
    source_file_id: str,
    now: str,
) -> dict[str, Any]:
    accession = nullable_string(fact.get("accn"))
    dimensions = {key: value for key, value in fact.items() if key not in {"val", "accn", "fy", "fp", "form", "filed", "frame", "start", "end"}}
    fact_key = compact_json([cik, taxonomy, tag, unit, fact.get("start"), fact.get("end"), accession, fact.get("frame"), dimensions])
    return {
        "fact_id": hashlib.sha256(fact_key.encode("utf-8")).hexdigest(),
        "cik": cik,
        "entity_name": entity_name,
        "taxonomy": clean_string(taxonomy),
        "tag": clean_string(tag),
        "label": label,
        "description": description,
        "unit": clean_string(unit),
        "value": float_or_none(fact.get("val")),
        "start_date": nullable_date(fact.get("start")),
        "end_date": nullable_date(fact.get("end")),
        "filed_date": nullable_date(fact.get("filed")),
        "fy": int_or_none(fact.get("fy")),
        "fp": nullable_string(fact.get("fp")),
        "form_type": nullable_string(fact.get("form")),
        "frame": nullable_string(fact.get("frame")),
        "accession_number": accession,
        "dimensions_json": compact_json(dimensions),
        "source_file_id": source_file_id,
        "last_seen_at_utc": now,
    }


def flush(client: ClickHouseHttpClient, database: str, table: str, rows: list[dict[str, Any]], retry: InsertRetryConfig) -> int:
    count = insert_rows(client, database, table, rows, retry)
    rows.clear()
    return count


def flush_member_manifest(
    client: ClickHouseHttpClient,
    database: str,
    retry: InsertRetryConfig,
    company_batch: list[dict[str, Any]],
    file_ref_batch: list[dict[str, Any]],
    filing_batch: list[dict[str, Any]],
    manifest_batch: list[dict[str, Any]],
) -> int:
    inserted = 0
    inserted += flush(client, database, "sec_bulk_mirror_company_v1", company_batch, retry)
    inserted += flush(client, database, "sec_bulk_mirror_submission_file_ref_v1", file_ref_batch, retry)
    inserted += flush(client, database, "sec_bulk_mirror_filing_v1", filing_batch, retry)
    insert_rows(client, database, MEMBER_MANIFEST_TABLE, manifest_batch, retry)
    manifest_batch.clear()
    return inserted


def insert_rows(client: ClickHouseHttpClient, database: str, table: str, rows: list[dict[str, Any]], retry: InsertRetryConfig) -> int:
    if not rows:
        return 0
    partitioned = partition_rows_for_insert(table, rows)
    if len(partitioned) > 1:
        for bucket, bucket_rows in partitioned.items():
            insert_rows_json_with_retry(client, database, table, bucket_rows, retry, partition_bucket=bucket)
        return len(rows)
    insert_rows_json_with_retry(client, database, table, rows, retry, partition_bucket=next(iter(partitioned)))
    return len(rows)


def insert_rows_json(client: ClickHouseHttpClient, database: str, table: str, rows: list[dict[str, Any]]) -> None:
    body = "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) for row in rows)
    client.execute(f"INSERT INTO {quote_ident(database)}.{quote_ident(table)} FORMAT JSONEachRow\n{body}")


def insert_rows_json_with_retry(
    client: ClickHouseHttpClient,
    database: str,
    table: str,
    rows: list[dict[str, Any]],
    retry: InsertRetryConfig,
    *,
    partition_bucket: str,
) -> None:
    for attempt in range(retry.max_retries + 1):
        try:
            insert_rows_json(client, database, table, rows)
            return
        except Exception as exc:  # noqa: BLE001
            if attempt >= retry.max_retries or not is_retryable_insert_error(exc):
                raise
            delay = retry_delay_seconds(retry, attempt)
            print(
                "clickhouse_insert_retry "
                f"table={table} partition={partition_bucket} rows={len(rows):,} "
                f"attempt={attempt + 1}/{retry.max_retries} delay_seconds={delay:.1f} "
                f"error={summarize_error(exc)}",
                flush=True,
            )
            time.sleep(delay)


def retry_delay_seconds(retry: InsertRetryConfig, attempt: int) -> float:
    delay = retry.base_seconds * (2**attempt)
    if retry.max_seconds:
        delay = min(delay, retry.max_seconds)
    return max(0.0, delay)


def is_retryable_insert_error(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError, URLError)):
        return True
    text = repr(exc)
    if "TOO_MANY_PARTS" in text or "Too many partitions" in text:
        return False
    if "ClickHouse HTTP 5" in text:
        return True
    transient_markers = [
        "timed out",
        "Connection reset",
        "Connection aborted",
        "Connection refused",
        "failed to respond",
        "Remote end closed connection",
    ]
    return any(marker in text for marker in transient_markers)


def summarize_error(exc: BaseException) -> str:
    text = repr(exc).replace("\n", " ")
    return text[:500]


def partition_rows_for_insert(table: str, rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    if table == "sec_bulk_mirror_raw_source_file_v1":
        return group_rows_by_yyyymm(rows, "downloaded_at_utc")
    if table == "sec_bulk_mirror_filing_v1":
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            key = yyyymm_from_value(row.get("accepted_at_utc")) or yyyymm_from_value(row.get("filing_date")) or "197001"
            grouped[key].append(row)
        return dict(grouped)
    if table == "sec_bulk_mirror_xbrl_fact_v1":
        return group_rows_by_yyyymm(rows, "end_date", default="197001")
    return {"all": rows}


def group_rows_by_yyyymm(rows: list[dict[str, Any]], key: str, *, default: str = "197001") -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[yyyymm_from_value(row.get(key)) or default].append(row)
    return dict(grouped)


def yyyymm_from_value(value: Any) -> str | None:
    text = clean_string(value)
    if len(text) >= 7 and text[4] == "-" and text[5:7].isdigit():
        return text[:4] + text[5:7]
    return None


def raw_source_file_table_sql(database: str, storage_policy: str) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(database)}.sec_bulk_mirror_raw_source_file_v1
(
    source_file_id String,
    source_kind LowCardinality(String),
    source_url String,
    artifact_path String,
    source_date Nullable(Date),
    downloaded_at_utc DateTime64(9, 'UTC'),
    byte_size UInt64,
    sha256 String,
    status LowCardinality(String),
    error String
)
ENGINE = ReplacingMergeTree(downloaded_at_utc)
PARTITION BY toYYYYMM(downloaded_at_utc)
ORDER BY (source_kind, ifNull(source_date, toDate('1970-01-01')), source_file_id)
SETTINGS {merge_tree_settings(storage_policy)}
"""


def company_table_sql(database: str, storage_policy: str) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(database)}.sec_bulk_mirror_company_v1
(
    cik String,
    entity_name String,
    sic Nullable(String),
    sic_description Nullable(String),
    ein Nullable(String),
    category Nullable(String),
    fiscal_year_end Nullable(String),
    state_of_incorporation Nullable(String),
    addresses_json String,
    former_names_json String,
    source_file_id String,
    last_seen_at_utc DateTime64(9, 'UTC')
)
ENGINE = ReplacingMergeTree(last_seen_at_utc)
ORDER BY (cik)
SETTINGS {merge_tree_settings(storage_policy)}
"""


def ticker_table_sql(database: str, storage_policy: str) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(database)}.sec_bulk_mirror_company_ticker_v1
(
    mapping_id String,
    cik String,
    ticker String,
    exchange Nullable(String),
    company_name String,
    mapping_source LowCardinality(String),
    series_id Nullable(String),
    class_id Nullable(String),
    first_seen_at_utc DateTime64(9, 'UTC'),
    last_seen_at_utc DateTime64(9, 'UTC'),
    is_active UInt8,
    source_file_id String
)
ENGINE = ReplacingMergeTree(last_seen_at_utc)
ORDER BY (cik, mapping_source, ticker, ifNull(series_id, ''), ifNull(class_id, ''))
SETTINGS {merge_tree_settings(storage_policy)}
"""


def submission_file_ref_table_sql(database: str, storage_policy: str) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(database)}.sec_bulk_mirror_submission_file_ref_v1
(
    file_ref_id String,
    cik String,
    file_name String,
    filing_count UInt64,
    filing_from Nullable(Date),
    filing_to Nullable(Date),
    source_file_id String,
    last_seen_at_utc DateTime64(9, 'UTC')
)
ENGINE = ReplacingMergeTree(last_seen_at_utc)
ORDER BY (cik, file_name)
SETTINGS {merge_tree_settings(storage_policy)}
"""


def filing_table_sql(database: str, storage_policy: str) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(database)}.sec_bulk_mirror_filing_v1
(
    accession_number String,
    accession_number_compact String,
    cik String,
    company_name String,
    form_type LowCardinality(String),
    filing_date Nullable(Date),
    report_date Nullable(Date),
    accepted_at_utc Nullable(DateTime64(9, 'UTC')),
    acceptance_datetime_raw Nullable(String),
    accepted_at_source LowCardinality(String),
    primary_document Nullable(String),
    primary_document_url Nullable(String),
    filing_detail_url Nullable(String),
    document_count Nullable(UInt16),
    filing_size Nullable(UInt64),
    items Nullable(String),
    act Nullable(String),
    file_number Nullable(String),
    film_number Nullable(String),
    source_kind LowCardinality(String),
    source_file_id String,
    raw_submission_json String,
    last_seen_at_utc DateTime64(9, 'UTC')
)
ENGINE = ReplacingMergeTree(last_seen_at_utc)
PARTITION BY toYYYYMM(coalesce(accepted_at_utc, toDateTime64(ifNull(filing_date, toDate('1970-01-01')), 9, 'UTC')))
ORDER BY (cik, accession_number)
SETTINGS {merge_tree_settings(storage_policy)}
"""


def xbrl_fact_table_sql(database: str, storage_policy: str) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(database)}.sec_bulk_mirror_xbrl_fact_v1
(
    fact_id String,
    cik String,
    entity_name String,
    taxonomy LowCardinality(String),
    tag String,
    label String,
    description String,
    unit LowCardinality(String),
    value Nullable(Float64),
    start_date Nullable(Date),
    end_date Nullable(Date),
    filed_date Nullable(Date),
    fy Nullable(UInt16),
    fp Nullable(String),
    form_type Nullable(String),
    frame Nullable(String),
    accession_number Nullable(String),
    dimensions_json String,
    source_file_id String,
    last_seen_at_utc DateTime64(9, 'UTC')
)
ENGINE = ReplacingMergeTree(last_seen_at_utc)
PARTITION BY toYYYYMM(ifNull(end_date, toDate('1970-01-01')))
ORDER BY (cik, taxonomy, tag, unit, ifNull(end_date, toDate('1970-01-01')), ifNull(accession_number, ''))
SETTINGS {merge_tree_settings(storage_policy)}
"""


def member_manifest_table_sql(database: str, storage_policy: str) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(database)}.{quote_ident(MEMBER_MANIFEST_TABLE)}
(
    source_name LowCardinality(String),
    source_kind LowCardinality(String),
    source_file_id String,
    member_name String,
    cik String,
    member_crc UInt64,
    member_file_size UInt64,
    member_compress_size UInt64,
    member_modified_at_utc Nullable(DateTime64(6, 'UTC')),
    member_signature String,
    status LowCardinality(String),
    rows_inserted UInt64,
    processed_at_utc DateTime64(9, 'UTC'),
    error String
)
ENGINE = ReplacingMergeTree(processed_at_utc)
ORDER BY (source_name, source_file_id, member_name, member_signature)
SETTINGS {merge_tree_settings(storage_policy)}
"""


def merge_tree_settings(storage_policy: str) -> str:
    settings = ["index_granularity = 8192"]
    if storage_policy.strip():
        settings.append(f"storage_policy = {sql_string(storage_policy.strip())}")
    return ", ".join(settings)


def accepted_at_utc(raw: str) -> str | None:
    text = clean_string(raw)
    if not text:
        return None
    try:
        if "T" in text:
            iso_text = text[:-1] if text.endswith("Z") else text
            parsed = datetime.fromisoformat(iso_text)
        else:
            parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        parsed = parsed.replace(tzinfo=SEC_ET)
        return parsed.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        digits = re.sub(r"\D+", "", text)
        if len(digits) < 14:
            return None
        try:
            parsed = datetime.strptime(digits[:14], "%Y%m%d%H%M%S").replace(tzinfo=SEC_ET)
            return parsed.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
        except ValueError:
            return None


def filing_document_url(cik: str, accession_compact: str, document: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{cik_archive_segment(cik)}/{accession_compact}/{document}"


def filing_detail_url(cik: str, accession_compact: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{cik_archive_segment(cik)}/{accession_compact}/"


def cik_archive_segment(cik: str) -> str:
    return cik.lstrip("0") or "0"


def recent_value(recent: dict[str, Any], key: str, index: int) -> str:
    values = recent.get(key, [])
    if not isinstance(values, list) or index >= len(values):
        return ""
    return clean_string(values[index])


def cik10(value: Any) -> str:
    text = str(value or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits.zfill(10)[-10:] if digits else ""


def clean_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def nullable_string(value: Any) -> str | None:
    text = clean_string(value)
    return text or None


def nullable_date(value: Any) -> str | None:
    text = clean_string(value)
    if not text:
        return None
    return text[:10]


def int_or_zero(value: Any) -> int:
    parsed = int_or_none(value)
    return parsed if parsed is not None else 0


def int_or_none(value: Any) -> int | None:
    text = clean_string(value).replace(",", "")
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def float_or_none(value: Any) -> float | None:
    text = clean_string(value)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def clickhouse_datetime64_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")


def write_report(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")


def artifact_report(item: SourceArtifact) -> dict[str, Any]:
    return {
        "source_name": item.source_name,
        "source_kind": item.source_kind,
        "path": str(item.path),
        "byte_size": item.byte_size,
        "sha256": item.sha256,
    }


def print_header(args: argparse.Namespace, loaded_env_files: list[Path], artifacts: list[SourceArtifact], report_path: Path) -> None:
    print("=" * 96, flush=True)
    print("SEC bulk ClickHouse ingest", flush=True)
    print(f"database={args.database}", flush=True)
    print(f"artifact_root={args.artifact_root_win}", flush=True)
    print(f"output_root={args.output_root_win}", flush=True)
    print(f"sources={args.sources}", flush=True)
    print(f"artifacts={len(artifacts)}", flush=True)
    print(f"storage_policy={args.storage_policy or '<default>'}", flush=True)
    print(f"batch_size={args.batch_size} limit_ciks={args.limit_ciks} dry_run={args.dry_run}", flush=True)
    print(f"member_manifest_enabled={not args.disable_member_manifest}", flush=True)
    print(f"report_path={report_path}", flush=True)
    print(
        "secret_status="
        + json.dumps(
            secret_status(
                [
                    "SEC_CLICKHOUSE_URL",
                    "SEC_CLICKHOUSE_USER",
                    "SEC_CLICKHOUSE_PASSWORD",
                    "SEC_CLICKHOUSE_DATABASE",
                    "SEC_CLICKHOUSE_STORAGE_POLICY",
                    "QMD_CLICKHOUSE_URL",
                    "QMD_CLICKHOUSE_USER",
                    "QMD_CLICKHOUSE_PASSWORD",
                    "CLICKHOUSE_LIVE_STORAGE_POLICY",
                ]
            ),
            sort_keys=True,
        ),
        flush=True,
    )
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    for artifact in artifacts:
        print(f"artifact {artifact.source_name}: path={artifact.path} bytes={artifact.byte_size}", flush=True)
    print("=" * 96, flush=True)


if __name__ == "__main__":
    main()
