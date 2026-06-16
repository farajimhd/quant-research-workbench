from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib import error, parse, request


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.clickhouse_ingest_sip_flatfiles import (  # noqa: E402
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    quote_ident,
    sql_string,
)
from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402
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
from pipelines.sec.edgar.sec_initial_fill_download import sha256_file  # noqa: E402


DEFAULT_TARGET_DATABASE = "q_live"
DEFAULT_TARGET_TABLE = "sec_filing_v2"
DEFAULT_STAGE_DATABASE = "sec_core"
DEFAULT_STAGE_TABLE = "sec_bulk_mirror_filing_acceptance_v1"
DEFAULT_ARTIFACT_ROOT_WIN = Path("D:/market-data/sec_core")
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_acceptance_backfill")
DEFAULT_BATCH_SIZE = 50_000
EXPECTED_STAGE_PARTITION_KEY = "cityHash64(cik) % 64"


@dataclass(frozen=True, slots=True)
class RunPaths:
    run_root: Path
    accepted_jsonl: Path
    not_found_keys_jsonl: Path
    not_found_ciks_jsonl: Path
    manifest_json: Path
    summary_md: Path

    @classmethod
    def create(cls, output_root: Path, run_id: str) -> "RunPaths":
        run_root = output_root / run_id
        run_root.mkdir(parents=True, exist_ok=True)
        return cls(
            run_root=run_root,
            accepted_jsonl=run_root / "accepted_rows.jsonl",
            not_found_keys_jsonl=run_root / "not_found_keys.jsonl",
            not_found_ciks_jsonl=run_root / "not_found_ciks.jsonl",
            manifest_json=run_root / "sec_acceptance_backfill_manifest.json",
            summary_md=run_root / "sec_acceptance_backfill_summary.md",
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a narrow SEC accepted-timestamp source for current q_live filings only. "
            "The script reads missing q_live keys, scans SEC submissions.zip, saves matched "
            "accepted rows and not-found diagnostics, and optionally inserts the matched rows."
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
    parser.add_argument("--output-root-win", default=os.environ.get("SEC_ACCEPTANCE_BACKFILL_OUTPUT_ROOT_WIN", str(DEFAULT_OUTPUT_ROOT_WIN)))
    parser.add_argument("--storage-policy", default=os.environ.get("SEC_CLICKHOUSE_STORAGE_POLICY") or os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY") or "")
    parser.add_argument("--batch-size", type=int, default=int(os.environ.get("SEC_ACCEPTANCE_BACKFILL_BATCH_SIZE", str(DEFAULT_BATCH_SIZE))))
    parser.add_argument("--limit-missing-keys", type=int, default=0, help="Debug cap for q_live missing keys. 0 means all missing keys.")
    parser.add_argument("--limit-ciks", type=int, default=0, help="Debug cap for CIK JSON files scanned from submissions.zip. 0 means all.")
    parser.add_argument("--execute", action="store_true", help="Create and insert into the narrow stage table. Without this flag, only local artifacts are written.")
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

    run_id = f"sec_acceptance_backfill_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    paths = RunPaths.create(Path(args.output_root_win), run_id)
    submissions_zip = resolve_submissions_zip(args)
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)

    print_header(args, paths, loaded_env, submissions_zip, run_id)
    started = time.perf_counter()
    missing_by_cik, total_missing_rows = load_missing_keys(client, args)
    source_sha256 = sha256_file(submissions_zip)
    source_file_id = hashlib.sha256(f"submissions_bulk|{submissions_zip}|{source_sha256}".encode("utf-8")).hexdigest()

    if args.execute:
        create_stage_table(client, args)

    stats = scan_submissions_zip(
        client=client,
        args=args,
        paths=paths,
        submissions_zip=submissions_zip,
        source_file_id=source_file_id,
        source_sha256=source_sha256,
        missing_by_cik=missing_by_cik,
    )
    stats["total_missing_rows"] = total_missing_rows
    stats["remaining_missing_rows"] = sum(len(values) for values in missing_by_cik.values())
    stats["remaining_missing_ciks"] = sum(1 for values in missing_by_cik.values() if values)
    stats["wall_seconds"] = round(time.perf_counter() - started, 3)

    write_not_found(paths, missing_by_cik, stats["found_by_cik"])
    write_manifest(paths.manifest_json, args, paths, loaded_env, run_id, submissions_zip, source_sha256, source_file_id, stats)
    write_summary(paths.summary_md, args, paths, run_id, submissions_zip, stats)
    print("summary=" + json.dumps(compact_stats(stats), sort_keys=True, default=str), flush=True)
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


def resolve_submissions_zip(args: argparse.Namespace) -> Path:
    if args.submissions_zip_win:
        path = Path(args.submissions_zip_win)
    else:
        path = Path(args.artifact_root_win) / "bulk" / "submissions" / "submissions.zip"
    if not path.exists():
        raise SystemExit(f"SEC submissions zip not found: {path}")
    return path


def load_missing_keys(client: ClickHouseHttpClient, args: argparse.Namespace) -> tuple[dict[str, set[str]], int]:
    target = f"{quote_ident(args.target_database)}.{quote_ident(args.target_table)}"
    limit_clause = f"\nLIMIT {int(args.limit_missing_keys)}" if args.limit_missing_keys else ""
    sql = f"""
SELECT cik, accession_number
FROM {target} FINAL
WHERE accepted_at_utc IS NULL
  AND cik != ''
  AND accession_number != ''
{limit_clause}
FORMAT TSV
"""
    missing_by_cik: dict[str, set[str]] = defaultdict(set)
    total = 0
    started = time.perf_counter()
    for line in stream_clickhouse_lines(client, sql):
        text = line.decode("utf-8", errors="replace").rstrip("\n")
        if not text:
            continue
        parts = text.split("\t")
        if len(parts) < 2:
            continue
        cik = cik10(parts[0])
        accession = clean_string(parts[1])
        if cik and accession:
            missing_by_cik[cik].add(accession)
            total += 1
            if total % 1_000_000 == 0:
                print(f"missing_keys_loaded={total:,} ciks={len(missing_by_cik):,} elapsed={time.perf_counter() - started:.1f}s", flush=True)
    print(f"missing_keys_loaded={total:,} ciks={len(missing_by_cik):,} elapsed={time.perf_counter() - started:.1f}s", flush=True)
    return dict(missing_by_cik), total


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


def scan_submissions_zip(
    *,
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    paths: RunPaths,
    submissions_zip: Path,
    source_file_id: str,
    source_sha256: str,
    missing_by_cik: dict[str, set[str]],
) -> dict[str, Any]:
    rows_batch: list[dict[str, Any]] = []
    found_by_cik: dict[str, int] = defaultdict(int)
    stats: dict[str, Any] = {
        "zip_entries_scanned": 0,
        "cik_entries_with_missing_keys": 0,
        "accepted_rows_written": 0,
        "accepted_rows_inserted": 0,
        "accepted_rows_missing_acceptance_datetime": 0,
        "found_by_cik": found_by_cik,
    }
    now = clickhouse_now64()
    started = time.perf_counter()
    with paths.accepted_jsonl.open("w", encoding="utf-8") as accepted_handle:
        with zipfile.ZipFile(submissions_zip) as archive:
            names = sorted(item for item in archive.namelist() if item.lower().endswith(".json"))
            for name in names:
                if args.limit_ciks and stats["zip_entries_scanned"] >= args.limit_ciks:
                    break
                stats["zip_entries_scanned"] += 1
                data = json.loads(archive.read(name).decode("utf-8", errors="replace"))
                cik = cik10(data.get("cik") or data.get("cik_str") or Path(name).stem.replace("CIK", ""))
                wanted = missing_by_cik.get(cik)
                if not wanted:
                    continue
                stats["cik_entries_with_missing_keys"] += 1
                for row in acceptance_rows_for_company(data, cik, wanted, source_file_id, source_sha256, now):
                    accepted_handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")
                    stats["accepted_rows_written"] += 1
                    found_by_cik[cik] += 1
                    wanted.discard(row["accession_number"])
                    if not row["accepted_at_utc"]:
                        stats["accepted_rows_missing_acceptance_datetime"] += 1
                        continue
                    if args.execute:
                        rows_batch.append(row)
                    if args.execute and len(rows_batch) >= args.batch_size:
                        stats["accepted_rows_inserted"] += insert_rows(client, args.stage_database, args.stage_table, rows_batch)
                        rows_batch.clear()
                if stats["zip_entries_scanned"] % 5_000 == 0:
                    print(
                        "submissions_scan "
                        f"entries={stats['zip_entries_scanned']:,}/{len(names):,} "
                        f"accepted={stats['accepted_rows_written']:,} "
                        f"remaining={sum(len(values) for values in missing_by_cik.values()):,} "
                        f"elapsed={time.perf_counter() - started:.1f}s",
                        flush=True,
                    )
    if args.execute:
        stats["accepted_rows_inserted"] += insert_rows(client, args.stage_database, args.stage_table, rows_batch)
        rows_batch.clear()
    stats["found_by_cik"] = dict(found_by_cik)
    return stats


def acceptance_rows_for_company(
    data: dict[str, Any],
    cik: str,
    wanted_accessions: set[str],
    source_file_id: str,
    source_sha256: str,
    now: str,
) -> list[dict[str, Any]]:
    recent = data.get("filings", {}).get("recent", {}) or {}
    if not recent:
        return []
    lengths = [len(value) for value in recent.values() if isinstance(value, list)]
    count = max(lengths) if lengths else 0
    company_name = clean_string(data.get("name", ""))
    rows: list[dict[str, Any]] = []
    for index in range(count):
        accession = recent_value(recent, "accessionNumber", index)
        if not accession or accession not in wanted_accessions:
            continue
        accession_compact = accession.replace("-", "")
        accepted_raw = recent_value(recent, "acceptanceDateTime", index)
        primary_document = recent_value(recent, "primaryDocument", index)
        row = {
            "acceptance_id": hashlib.sha256(f"{cik}|{accession}|{accepted_raw}".encode("utf-8")).hexdigest(),
            "cik": cik,
            "accession_number": accession,
            "accession_number_compact": accession_compact,
            "company_name": company_name,
            "form_type": recent_value(recent, "form", index),
            "filing_date": nullable_date(recent_value(recent, "filingDate", index)),
            "report_date": nullable_date(recent_value(recent, "reportDate", index)),
            "accepted_at_utc": accepted_at_utc(accepted_raw),
            "acceptance_datetime_raw": accepted_raw or None,
            "accepted_at_source": "submissions_bulk_recent" if accepted_raw else "missing_in_submissions_bulk_recent",
            "primary_document": primary_document or None,
            "primary_document_url": filing_document_url(cik, accession_compact, primary_document) if primary_document else None,
            "filing_detail_url": filing_detail_url(cik, accession_compact),
            "filing_size": int_or_none(recent_value(recent, "size", index)),
            "items": nullable_string(recent_value(recent, "items", index)),
            "source_file_id": source_file_id,
            "source_zip_sha256": source_sha256,
            "source_content_sha256": hashlib.sha256(json.dumps({key: recent_value(recent, key, index) for key in recent}, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
            "last_seen_at_utc": now,
        }
        rows.append(row)
    return rows


def write_not_found(paths: RunPaths, missing_by_cik: dict[str, set[str]], found_by_cik: dict[str, int]) -> None:
    with paths.not_found_keys_jsonl.open("w", encoding="utf-8") as key_handle, paths.not_found_ciks_jsonl.open("w", encoding="utf-8") as cik_handle:
        for cik in sorted(missing_by_cik):
            missing = sorted(missing_by_cik[cik])
            if not missing:
                continue
            cik_handle.write(
                json.dumps(
                    {
                        "cik": cik,
                        "not_found_count": len(missing),
                        "found_count": int(found_by_cik.get(cik, 0)),
                    },
                    separators=(",", ":"),
                )
                + "\n"
            )
            for accession in missing:
                key_handle.write(json.dumps({"cik": cik, "accession_number": accession}, separators=(",", ":")) + "\n")


def create_stage_table(client: ClickHouseHttpClient, args: argparse.Namespace) -> None:
    client.execute(f"CREATE DATABASE IF NOT EXISTS {quote_ident(args.stage_database)}")
    ensure_stage_table_compatible(client, args.stage_database, args.stage_table)
    client.execute(stage_table_sql(args.stage_database, args.stage_table, args.storage_policy))


def ensure_stage_table_compatible(client: ClickHouseHttpClient, database: str, table: str) -> None:
    if not table_exists(client, database, table):
        return
    partition_key = first_cell(
        client,
        f"""
        SELECT partition_key
        FROM system.tables
        WHERE database = {sql_string(database)}
          AND name = {sql_string(table)}
        FORMAT TSV
        """,
    )
    if normalize_clickhouse_expr(partition_key) == normalize_clickhouse_expr(EXPECTED_STAGE_PARTITION_KEY):
        return
    row_count = int(
        first_cell(
            client,
            f"SELECT count() FROM {quote_ident(database)}.{quote_ident(table)} FORMAT TSV",
        )
        or "0"
    )
    if row_count == 0:
        print(
            "stage_table_recreate_empty=true "
            f"table={database}.{table} old_partition_key={partition_key!r} "
            f"new_partition_key={EXPECTED_STAGE_PARTITION_KEY!r}",
            flush=True,
        )
        client.execute(f"DROP TABLE {quote_ident(database)}.{quote_ident(table)}")
        return
    raise SystemExit(
        f"Existing stage table {database}.{table} has incompatible partition key {partition_key!r} "
        f"and {row_count:,} rows. Create a new --stage-table or migrate/drop it manually."
    )


def stage_table_sql(database: str, table: str, storage_policy: str) -> str:
    settings = ["index_granularity = 8192"]
    if storage_policy.strip():
        settings.append(f"storage_policy = {sql_string(storage_policy.strip())}")
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(database)}.{quote_ident(table)}
(
    acceptance_id String,
    cik String,
    accession_number String,
    accession_number_compact String,
    company_name String,
    form_type LowCardinality(String),
    filing_date Nullable(Date),
    report_date Nullable(Date),
    accepted_at_utc DateTime64(9, 'UTC'),
    acceptance_datetime_raw Nullable(String),
    accepted_at_source LowCardinality(String),
    primary_document Nullable(String),
    primary_document_url Nullable(String),
    filing_detail_url Nullable(String),
    filing_size Nullable(UInt64),
    items Nullable(String),
    source_file_id String,
    source_zip_sha256 String,
    source_content_sha256 String,
    last_seen_at_utc DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(last_seen_at_utc)
PARTITION BY {EXPECTED_STAGE_PARTITION_KEY}
ORDER BY (cik, accession_number)
SETTINGS {", ".join(settings)}
"""


def insert_rows(client: ClickHouseHttpClient, database: str, table: str, rows: list[dict[str, Any]]) -> int:
    valid_rows = [row for row in rows if row.get("accepted_at_utc")]
    if not valid_rows:
        return 0
    body = "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) for row in valid_rows)
    client.execute(f"INSERT INTO {quote_ident(database)}.{quote_ident(table)} FORMAT JSONEachRow\n{body}")
    return len(valid_rows)


def table_exists(client: ClickHouseHttpClient, database: str, table: str) -> bool:
    return (
        first_cell(
            client,
            f"""
            SELECT count()
            FROM system.tables
            WHERE database = {sql_string(database)}
              AND name = {sql_string(table)}
            FORMAT TSV
            """,
        )
        == "1"
    )


def first_cell(client: ClickHouseHttpClient, sql: str) -> str:
    text = client.execute(sql.strip().rstrip(";"))
    if not text.strip():
        return ""
    return text.splitlines()[0].split("\t")[0]


def normalize_clickhouse_expr(value: str) -> str:
    return "".join(value.lower().split())


def write_manifest(
    path: Path,
    args: argparse.Namespace,
    paths: RunPaths,
    loaded_env: list[Path],
    run_id: str,
    submissions_zip: Path,
    source_sha256: str,
    source_file_id: str,
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
        "source_sha256": source_sha256,
        "source_file_id": source_file_id,
        "run_root": str(paths.run_root),
        "accepted_jsonl": str(paths.accepted_jsonl),
        "not_found_keys_jsonl": str(paths.not_found_keys_jsonl),
        "not_found_ciks_jsonl": str(paths.not_found_ciks_jsonl),
        "stats": compact_stats(stats),
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
            ]
        ),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def compact_stats(stats: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in stats.items() if key != "found_by_cik"}


def write_summary(paths_summary: Path, args: argparse.Namespace, paths: RunPaths, run_id: str, submissions_zip: Path, stats: dict[str, Any]) -> None:
    lines = [
        "# SEC Acceptance Backfill Build",
        "",
        f"- Run id: `{run_id}`",
        f"- Execute mode: `{args.execute}`",
        f"- q_live target: `{args.target_database}.{args.target_table}`",
        f"- Stage table: `{args.stage_database}.{args.stage_table}`",
        f"- submissions.zip: `{submissions_zip}`",
        "",
        "## Outputs",
        "",
        f"- Accepted rows: `{paths.accepted_jsonl}`",
        f"- Not found keys: `{paths.not_found_keys_jsonl}`",
        f"- Not found CIK summary: `{paths.not_found_ciks_jsonl}`",
        "",
        "## Counts",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
    ]
    for key in [
        "total_missing_rows",
        "zip_entries_scanned",
        "cik_entries_with_missing_keys",
        "accepted_rows_written",
        "accepted_rows_inserted",
        "accepted_rows_missing_acceptance_datetime",
        "remaining_missing_rows",
        "remaining_missing_ciks",
        "wall_seconds",
    ]:
        value = stats.get(key, 0)
        if isinstance(value, float):
            lines.append(f"| `{key}` | {value:,.3f} |")
        else:
            lines.append(f"| `{key}` | {int(value):,} |")
    paths_summary.write_text("\n".join(lines) + "\n", encoding="utf-8")


def clickhouse_now64() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def validate_identifier(value: str, label: str) -> None:
    if not value or not value.replace("_", "").isalnum() or value[0].isdigit():
        raise SystemExit(f"{label} must be a simple ClickHouse identifier: {value!r}")


def print_header(args: argparse.Namespace, paths: RunPaths, loaded_env: list[Path], submissions_zip: Path, run_id: str) -> None:
    print("=" * 96, flush=True)
    print("SEC acceptance backfill build", flush=True)
    print(f"execute={args.execute}", flush=True)
    print(f"target_table={args.target_database}.{args.target_table}", flush=True)
    print(f"stage_table={args.stage_database}.{args.stage_table}", flush=True)
    print(f"submissions_zip={submissions_zip}", flush=True)
    print(f"run_id={run_id}", flush=True)
    print(f"run_root={paths.run_root}", flush=True)
    print("loaded_env_files=" + json.dumps([str(item) for item in loaded_env]), flush=True)
    print("=" * 96, flush=True)


if __name__ == "__main__":
    main()
