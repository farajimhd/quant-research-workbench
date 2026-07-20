from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import hashlib
import json
import multiprocessing
import os
import re
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.sec.edgar.sec_pipeline.text_renderer import (  # noqa: E402
    SEC_PACKED_TEXT_RENDERER_VERSION,
    STRUCTURED_XML_EXCLUDED_QUALITY_FLAG,
    render_sec_packed_text,
)
from pipelines.sec.edgar.sec_source_text_revision_engine import (  # noqa: E402
    SOURCE_AUTHORITY_VERSION,
    ensure_source_revision_engine,
    load_source_layout,
    mark_renderer_reset_completed,
    revision_engine_matches,
)
from pipelines.sec.edgar.sec_parquet_parts import ParquetShardWriter  # noqa: E402
from pipelines.sec.edgar.sec_filing_text_clickhouse_file_ingest import (  # noqa: E402
    windows_path_to_clickhouse_path,
)
from research.mlops.clickhouse import (  # noqa: E402
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    parse_size_bytes,
    quote_ident,
    sql_string,
)
from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402


DEFAULT_DATABASE = "q_live"
DEFAULT_SOURCE_TABLE = "sec_filing_text_v3"
DEFAULT_TARGET_TABLE = "sec_filing_text_rendered_v3"
DEFAULT_MANIFEST_TABLE = "sec_filing_text_rendered_rebuild_manifest_v3"
DEFAULT_BUNDLE_MANIFEST_TABLE = "sec_filing_text_rendered_rebuild_bundle_manifest_v3"
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_filing_text_rendered_v3_rebuild")
DEFAULT_FILE_ROOT_WIN = Path("D:/market-data")
DEFAULT_FILE_ROOT_CH = "/mnt/d/market-data"
BUILD_PARTITION_KEY = "toYYYYMM(source_archive_date)"
FINAL_PARTITION_KEY = "cityHash64(cik) % 64"
RENDERED_SORTING_KEY = "cik, accession_number, document_id, text_kind"

_INSERT_SEMAPHORE: Any | None = None

SOURCE_COLUMNS = [
    "document_id",
    "filing_id",
    "accession_number",
    "accession_number_compact",
    "cik",
    "document_name",
    "document_type",
    "text_kind",
    "content_format",
    "source_text",
    "source_archive_date",
    "source_archive_member",
    "source_version_key",
    "source_revision_at",
    "source_revision_rank",
    "source_revision_kind",
    "pac_event_id",
]

TARGET_COLUMNS = [
    "document_id",
    "filing_id",
    "accession_number",
    "accession_number_compact",
    "cik",
    "text_kind",
    "text",
    "text_char_count",
    "text_byte_count",
    "text_sha256",
    "extraction_method",
    "normalizer_version",
    "quality_flags",
    "source_archive_date",
    "source_archive_member",
    "source_version_key",
    "source_revision_at",
    "source_revision_rank",
    "source_revision_kind",
    "pac_event_id",
    "extracted_at_utc",
    "source_run_id",
    "inserted_at",
]


@dataclass(frozen=True, slots=True)
class SourceWatermark:
    rows: int
    source_bytes: int
    max_revision_rank: int
    max_inserted_at: str
    source_metadata_hash: int


@dataclass(frozen=True, slots=True)
class FilingWatermark:
    rows: int
    unique_filing_ids: int
    max_inserted_at: str
    metadata_hash: int


@dataclass(frozen=True, slots=True)
class PartitionJob:
    partition_id: int
    expected_rows: int
    expected_source_chars: int
    run_id: str
    run_root: str
    database: str
    source_table: str
    staging_table: str
    manifest_table: str
    bundle_manifest_table: str
    clickhouse_url: str
    clickhouse_user: str
    clickhouse_password: str
    file_root_win: str
    file_root_ch: str
    lookup_database_path: str
    export_threads: int
    insert_threads: int
    max_memory_usage: int
    parquet_row_group_bytes: int
    parquet_file_bytes: int
    row_groups_per_bundle: int
    max_rows_per_partition: int
    keep_temp_files: bool


@dataclass(frozen=True, slots=True)
class PartitionResult:
    partition_id: int
    source_rows: int
    rendered_rows: int
    excluded_rows: int
    source_chars: int
    rendered_chars: int
    output_parts: int
    wall_seconds: float
    status: str
    error: str = ""


@dataclass(frozen=True, slots=True)
class BundleResult:
    partition_id: int
    bundle_id: int
    row_group_start: int
    row_group_end: int
    physical_rows: int
    source_rows: int
    rendered_rows: int
    excluded_rows: int
    source_chars: int
    rendered_chars: int
    output_parts: int
    wall_seconds: float
    status: str
    error: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Rebuild q_live.sec_filing_text_rendered_v3 from the complete source-text v3 table "
            "with the canonical packed renderer, validated staging, resumable monthly workers, and atomic cutover."
        )
    )
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--database", default=os.environ.get("SEC_CLICKHOUSE_DATABASE", DEFAULT_DATABASE))
    parser.add_argument("--source-table", default=DEFAULT_SOURCE_TABLE)
    parser.add_argument("--target-table", default=DEFAULT_TARGET_TABLE)
    parser.add_argument(
        "--staging-table",
        default="",
        help="Optional explicit staging table. By default each run gets an isolated run-specific table.",
    )
    parser.add_argument("--manifest-table", default=DEFAULT_MANIFEST_TABLE)
    parser.add_argument("--bundle-manifest-table", default=DEFAULT_BUNDLE_MANIFEST_TABLE)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--output-root-win", default=str(DEFAULT_OUTPUT_ROOT_WIN))
    parser.add_argument("--file-root-win", default=str(DEFAULT_FILE_ROOT_WIN))
    parser.add_argument("--file-root-ch", default=DEFAULT_FILE_ROOT_CH)
    parser.add_argument("--workers", type=int, default=int(os.environ.get("SEC_RENDER_REBUILD_WORKERS", "4")))
    parser.add_argument("--export-threads", type=int, default=2)
    parser.add_argument("--insert-threads", type=int, default=2)
    parser.add_argument(
        "--max-concurrent-inserts",
        type=int,
        default=int(os.environ.get("SEC_RENDER_MAX_CONCURRENT_INSERTS", "2")),
        help="Global ClickHouse insert limit across renderer workers; rendering remains fully parallel.",
    )
    parser.add_argument("--max-memory-usage", default=os.environ.get("SEC_RENDER_REBUILD_MAX_MEMORY", "32G"))
    parser.add_argument("--parquet-row-group-mib", type=int, default=128)
    parser.add_argument("--parquet-file-mib", type=int, default=1024)
    parser.add_argument(
        "--row-groups-per-bundle",
        type=int,
        default=int(os.environ.get("SEC_RENDER_ROW_GROUPS_PER_BUNDLE", "8")),
        help="Durable render/insert checkpoint size; workers stop only between these bounded bundles.",
    )
    parser.add_argument("--limit-partitions", type=int, default=0, help="Testing only; cutover is forbidden when set.")
    parser.add_argument("--max-rows-per-partition", type=int, default=0, help="Testing only; cutover is forbidden when set.")
    parser.add_argument("--keep-temp-files", action="store_true")
    parser.add_argument("--execute", action="store_true", help="Create/resume staging and process source partitions.")
    parser.add_argument("--cutover", action="store_true", help="After complete validation, atomically replace the target and retain a backup.")
    parser.add_argument(
        "--confirm-sec-gateway-stopped",
        action="store_true",
        help="Required for execution. The script also verifies that the source watermark remains unchanged.",
    )
    return parser.parse_args()


def main() -> int:
    loaded_env = load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    run_id = args.run_id.strip() or f"sec_render_v8_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    if not args.staging_table:
        args.staging_table = staging_table_for_run(run_id)
    validate_args(args)
    run_root = Path(args.output_root_win) / run_id
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)

    require_tables(client, args)
    source_engine_report = run_root / "source_engine_migration.json"
    authority_reset_partitions: set[int] = set()
    if args.execute:
        run_root.mkdir(parents=True, exist_ok=True)
        authority_reset_partitions = ensure_source_revision_engine(
            client,
            database=args.database,
            table_name=args.source_table,
            report_path=source_engine_report,
            run_id=run_id,
        )
    elif not revision_engine_matches(load_source_layout(client, args.database, args.source_table)):
        print(
            "source_engine_migration=required stale_engine=ReplacingMergeTree(inserted_at) "
            "target_engine=ReplacingMergeTree(source_revision_rank)",
            flush=True,
        )
    current_watermark = load_source_watermark(client, args)
    current_filing_watermark = load_filing_watermark(client, args)
    partitions = load_partitions(client, args)
    if args.limit_partitions:
        partitions = partitions[: args.limit_partitions]
    print_header(args, run_id, run_root, current_watermark, partitions)

    if not args.execute:
        print("dry_run=true; no ClickHouse tables or local run files were changed", flush=True)
        print(f"run with --execute --confirm-sec-gateway-stopped; add --cutover for validated atomic replacement", flush=True)
        return 0

    verify_clickhouse_file_root(client, args, run_root)
    ensure_manifest_table(client, args)
    ensure_staging_table(client, args)
    ensure_bundle_manifest_table(client, args)
    if authority_reset_partitions:
        reset_authority_drift_partitions(
            client,
            args,
            run_id,
            run_root,
            authority_reset_partitions,
            current_watermark,
            current_filing_watermark,
            partitions,
        )
        mark_renderer_reset_completed(source_engine_report)
    source_watermark = load_or_create_run_manifest(
        run_root, args, run_id, loaded_env, current_watermark, current_filing_watermark, partitions
    )
    lookup_database_path = prepare_lookup_database(
        client, args, run_root, current_watermark, current_filing_watermark
    )
    completed = load_completed_partitions(client, args, run_id)
    pending = [row for row in partitions if row["partition_id"] not in completed]
    print(f"resume completed={len(completed):,} pending={len(pending):,}", flush=True)

    jobs = [build_job(args, run_id, run_root, lookup_database_path, row) for row in pending]
    results = run_jobs(
        client,
        jobs,
        max_workers=args.workers,
        max_concurrent_inserts=args.max_concurrent_inserts,
        total_partitions=len(partitions),
        already_completed=len(completed),
    )
    failures = [result for result in results if result.status != "ok"]
    if failures:
        write_results(run_root, results)
        raise RuntimeError(f"render rebuild failed partitions={[result.partition_id for result in failures]}")

    validation = validate_staging(
        client, args, run_id, partitions, source_watermark, current_filing_watermark
    )
    (run_root / "validation.json").write_text(json.dumps(validation, indent=2, sort_keys=True), encoding="utf-8")
    write_results(run_root, results)
    print_validation(validation)

    if args.cutover:
        backup_table = cutover(client, args, run_id, source_watermark, current_filing_watermark)
        print(f"cutover=complete target={args.database}.{args.target_table} backup={args.database}.{backup_table}", flush=True)
    else:
        print(f"cutover=pending staging={args.database}.{args.staging_table}", flush=True)
        print(f"resume validation and cutover with --run-id {run_id} --execute --cutover --confirm-sec-gateway-stopped", flush=True)
    return 0


def validate_args(args: argparse.Namespace) -> None:
    for label in (
        "database",
        "source_table",
        "target_table",
        "staging_table",
        "manifest_table",
        "bundle_manifest_table",
    ):
        value = str(getattr(args, label))
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            raise SystemExit(f"--{label.replace('_', '-')} must be a simple ClickHouse identifier: {value!r}")
    if (
        args.workers < 1
        or args.export_threads < 1
        or args.insert_threads < 1
        or args.row_groups_per_bundle < 1
        or args.max_concurrent_inserts < 1
        or args.max_concurrent_inserts > args.workers
    ):
        raise SystemExit(
            "worker, thread, bundle, and concurrent-insert counts must be positive; "
            "--max-concurrent-inserts cannot exceed --workers"
        )
    if args.parquet_row_group_mib < 1 or args.parquet_file_mib < args.parquet_row_group_mib:
        raise SystemExit("Parquet file size must be at least the row-group size")
    if args.cutover and not args.execute:
        raise SystemExit("--cutover requires --execute")
    if args.execute and not args.confirm_sec_gateway_stopped:
        raise SystemExit("--execute requires --confirm-sec-gateway-stopped")
    if args.cutover and (args.limit_partitions or args.max_rows_per_partition):
        raise SystemExit("cutover is forbidden with testing limits")
    run_root = Path(args.output_root_win).resolve()
    file_root = Path(args.file_root_win).resolve()
    try:
        run_root.relative_to(file_root)
    except ValueError as exc:
        raise SystemExit(f"--output-root-win must be under --file-root-win: {run_root} not under {file_root}") from exc


def require_tables(client: ClickHouseHttpClient, args: argparse.Namespace) -> None:
    rows = int(
        client.execute(
            f"SELECT count() FROM system.tables WHERE database={sql_string(args.database)} "
            f"AND name IN ({sql_string(args.source_table)},{sql_string(args.target_table)})"
        ).strip()
        or "0"
    )
    if rows != 2:
        raise RuntimeError(f"required tables are missing from {args.database}: {args.source_table}, {args.target_table}")
    renderer_versions = client.execute(
        f"SELECT groupUniqArray(normalizer_version) FROM {table(args.database, args.target_table)} FINAL FORMAT JSON"
    )
    if "sec_text_normalizer_v1" not in renderer_versions and SEC_PACKED_TEXT_RENDERER_VERSION not in renderer_versions:
        print("warning: target renderer version is neither known stale v1 nor canonical v8", flush=True)


def load_source_watermark(client: ClickHouseHttpClient, args: argparse.Namespace) -> SourceWatermark:
    row = query_one(
        client,
        f"""
SELECT count() AS rows, sum(source_text_byte_count) AS source_bytes,
       max(source_revision_rank) AS max_revision_rank, toString(max(inserted_at)) AS max_inserted_at,
       groupBitXor(cityHash64(document_id, source_version_key, source_revision_rank,
                              source_text_byte_count, content_sha256)) AS source_metadata_hash
FROM {table(args.database, args.source_table)} FINAL
SETTINGS do_not_merge_across_partitions_select_final=0
""",
    )
    return SourceWatermark(
        rows=int(row["rows"]),
        source_bytes=int(row["source_bytes"]),
        max_revision_rank=int(row["max_revision_rank"]),
        max_inserted_at=str(row["max_inserted_at"]),
        source_metadata_hash=int(row["source_metadata_hash"]),
    )


def load_filing_watermark(client: ClickHouseHttpClient, args: argparse.Namespace) -> FilingWatermark:
    row = query_one(
        client,
        f"""
SELECT count() AS rows, uniqExact(filing_id) AS unique_filing_ids,
       toString(max(inserted_at)) AS max_inserted_at,
       groupBitXor(cityHash64(filing_id, form_type)) AS metadata_hash
FROM {table(args.database, 'sec_filing_v3')} FINAL
""",
    )
    watermark = FilingWatermark(
        rows=int(row["rows"]),
        unique_filing_ids=int(row["unique_filing_ids"]),
        max_inserted_at=str(row["max_inserted_at"]),
        metadata_hash=int(row["metadata_hash"]),
    )
    if watermark.rows != watermark.unique_filing_ids:
        raise RuntimeError(
            "sec_filing_v3 filing_id is not unique; cannot build deterministic form lookup "
            f"rows={watermark.rows} unique={watermark.unique_filing_ids}"
        )
    return watermark


def load_partitions(client: ClickHouseHttpClient, args: argparse.Namespace) -> list[dict[str, int]]:
    return [
        {key: int(value) for key, value in row.items()}
        for row in query_rows(
            client,
            f"""
SELECT toYYYYMM(source_archive_date) AS partition_id, count() AS source_rows,
       sum(source_text_char_count) AS source_chars
FROM {table(args.database, args.source_table)} FINAL
GROUP BY partition_id
ORDER BY partition_id
SETTINGS do_not_merge_across_partitions_select_final=0
""",
        )
    ]


def prepare_lookup_database(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    run_root: Path,
    source_watermark: SourceWatermark,
    filing_watermark: FilingWatermark,
) -> Path:
    database_path = run_root / "render_lookup.sqlite"
    if database_path.exists():
        validate_lookup_database(database_path, source_watermark, filing_watermark)
        return database_path

    form_parquet_path = run_root / "filing_form_map.parquet"
    authority_parquet_path = run_root / "source_authority.parquet"
    sqlite_temporary_path = database_path.with_suffix(".sqlite.tmp")

    if sqlite_temporary_path.exists():
        try:
            validate_lookup_database(sqlite_temporary_path, source_watermark, filing_watermark)
        except (RuntimeError, sqlite3.DatabaseError) as exc:
            print(f"render_lookup temporary_database=invalid action=rebuild error={exc!r}", flush=True)
            sqlite_temporary_path.unlink()
        else:
            form_parquet_path.unlink(missing_ok=True)
            authority_parquet_path.unlink(missing_ok=True)
            sqlite_temporary_path.replace(database_path)
            print(f"render_lookup temporary_database=valid action=resume path={database_path}", flush=True)
            return database_path

    form_parquet_path.unlink(missing_ok=True)
    authority_parquet_path.unlink(missing_ok=True)
    clickhouse_path = windows_path_to_clickhouse_path(form_parquet_path, Path(args.file_root_win), args.file_root_ch)
    print("render_lookup stage=export_filing_forms status=active", flush=True)
    client.execute(
        f"""
INSERT INTO TABLE FUNCTION file({sql_string(clickhouse_path)}, 'Parquet')
SELECT filing_id, form_type
FROM {table(args.database, 'sec_filing_v3')} FINAL
SETTINGS max_threads=2, max_memory_usage={parse_size_bytes(args.max_memory_usage)},
         max_block_size=65536, output_format_parquet_compression_method='zstd',
         do_not_merge_across_partitions_select_final=0
"""
    )
    print("render_lookup stage=export_filing_forms status=completed", flush=True)

    connection = sqlite3.connect(sqlite_temporary_path)
    try:
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        connection.execute("PRAGMA temp_store=FILE")
        connection.execute("PRAGMA cache_size=-262144")
        connection.execute("PRAGMA page_size=32768")
        connection.execute("CREATE TABLE filing_forms (filing_id TEXT NOT NULL, form_type TEXT NOT NULL)")
        parquet = pq.ParquetFile(form_parquet_path)
        try:
            inserted = 0
            for batch in parquet.iter_batches(batch_size=100_000, columns=["filing_id", "form_type"]):
                rows = [
                    (str(row.get("filing_id") or ""), str(row.get("form_type") or ""))
                    for row in batch.to_pylist()
                ]
                connection.executemany("INSERT INTO filing_forms VALUES (?, ?)", rows)
                inserted += len(rows)
                if inserted % 1_000_000 < len(rows):
                    print(f"render_lookup stage=load_filing_forms rows={inserted:,}", flush=True)
        finally:
            parquet.close()
        if inserted != filing_watermark.unique_filing_ids:
            raise RuntimeError(
                f"filing form-map export mismatch expected={filing_watermark.unique_filing_ids} actual={inserted}"
            )
        print("render_lookup stage=index_filing_forms status=active", flush=True)
        connection.execute("CREATE UNIQUE INDEX filing_forms_id ON filing_forms(filing_id)")
        print("render_lookup stage=index_filing_forms status=completed", flush=True)

        authority_clickhouse_path = windows_path_to_clickhouse_path(
            authority_parquet_path, Path(args.file_root_win), args.file_root_ch
        )
        print("render_lookup stage=export_source_authority status=active", flush=True)
        client.execute(
            f"""
INSERT INTO TABLE FUNCTION file({sql_string(authority_clickhouse_path)}, 'Parquet')
SELECT cik, accession_number, document_id, content_format, source_version_key,
       source_revision_rank, toYYYYMM(source_archive_date) AS partition_id, filing_id
FROM {table(args.database, args.source_table)} FINAL
SETTINGS max_threads=2, max_memory_usage={parse_size_bytes(args.max_memory_usage)},
         max_block_size=65536, output_format_parquet_compression_method='zstd',
         do_not_merge_across_partitions_select_final=0
"""
        )
        print("render_lookup stage=export_source_authority status=completed", flush=True)
        connection.execute(
            "CREATE TABLE source_authority ("
            "cik TEXT NOT NULL, accession_number TEXT NOT NULL, document_id TEXT NOT NULL, "
            "content_format TEXT NOT NULL, source_version_key TEXT NOT NULL, source_revision_rank INTEGER NOT NULL, "
            "partition_id INTEGER NOT NULL, filing_id TEXT NOT NULL)"
        )
        authority_parquet = pq.ParquetFile(authority_parquet_path)
        try:
            authority_inserted = 0
            for batch in authority_parquet.iter_batches(batch_size=100_000):
                rows = [
                    (
                        str(row.get("cik") or ""),
                        str(row.get("accession_number") or ""),
                        str(row.get("document_id") or ""),
                        str(row.get("content_format") or ""),
                        str(row.get("source_version_key") or ""),
                        int(row.get("source_revision_rank") or 0),
                        int(row.get("partition_id") or 0),
                        str(row.get("filing_id") or ""),
                    )
                    for row in batch.to_pylist()
                ]
                connection.executemany("INSERT INTO source_authority VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
                authority_inserted += len(rows)
                if authority_inserted % 1_000_000 < len(rows):
                    print(f"render_lookup stage=load_source_authority rows={authority_inserted:,}", flush=True)
        finally:
            authority_parquet.close()
        if authority_inserted != source_watermark.rows:
            raise RuntimeError(
                f"source authority export mismatch expected={source_watermark.rows} actual={authority_inserted}"
            )
        print("render_lookup stage=index_source_authority status=active", flush=True)
        connection.execute(
            "CREATE UNIQUE INDEX source_authority_key ON "
            "source_authority(cik, accession_number, document_id, content_format)"
        )
        connection.execute("CREATE INDEX source_authority_partition ON source_authority(partition_id)")
        print("render_lookup stage=index_source_authority status=completed", flush=True)
        connection.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.executemany(
            "INSERT INTO metadata VALUES (?, ?)",
            [
                ("source_rows", str(source_watermark.rows)),
                ("source_bytes", str(source_watermark.source_bytes)),
                ("source_max_revision_rank", str(source_watermark.max_revision_rank)),
                ("source_max_inserted_at", source_watermark.max_inserted_at),
                ("source_metadata_hash", str(source_watermark.source_metadata_hash)),
                ("filing_rows", str(filing_watermark.rows)),
                ("unique_filing_ids", str(filing_watermark.unique_filing_ids)),
                ("filing_max_inserted_at", filing_watermark.max_inserted_at),
                ("filing_metadata_hash", str(filing_watermark.metadata_hash)),
                ("source_authority_version", str(SOURCE_AUTHORITY_VERSION)),
            ],
        )
        connection.commit()
    finally:
        connection.close()
        form_parquet_path.unlink(missing_ok=True)
        authority_parquet_path.unlink(missing_ok=True)
    sqlite_temporary_path.replace(database_path)
    validate_lookup_database(database_path, source_watermark, filing_watermark)
    print(
        f"render_lookup=ready authority_rows={source_watermark.rows:,} "
        f"filing_rows={filing_watermark.unique_filing_ids:,} path={database_path}",
        flush=True,
    )
    return database_path


def validate_lookup_database(
    path: Path,
    source_watermark: SourceWatermark,
    filing_watermark: FilingWatermark,
) -> None:
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
    try:
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        actual_filing_rows = int(connection.execute("SELECT count() FROM filing_forms").fetchone()[0])
        actual_source_rows = int(connection.execute("SELECT count() FROM source_authority").fetchone()[0])
    finally:
        connection.close()
    expected = {
        "source_rows": str(source_watermark.rows),
        "source_bytes": str(source_watermark.source_bytes),
        "source_max_revision_rank": str(source_watermark.max_revision_rank),
        "source_max_inserted_at": source_watermark.max_inserted_at,
        "source_metadata_hash": str(source_watermark.source_metadata_hash),
        "filing_rows": str(filing_watermark.rows),
        "unique_filing_ids": str(filing_watermark.unique_filing_ids),
        "filing_max_inserted_at": filing_watermark.max_inserted_at,
        "filing_metadata_hash": str(filing_watermark.metadata_hash),
        "source_authority_version": str(SOURCE_AUTHORITY_VERSION),
    }
    if (
        metadata != expected
        or actual_filing_rows != filing_watermark.unique_filing_ids
        or actual_source_rows != source_watermark.rows
    ):
        raise RuntimeError(
            f"render lookup watermark mismatch path={path} expected={expected} actual={metadata} "
            f"source_rows={actual_source_rows} filing_rows={actual_filing_rows}"
        )


def ensure_manifest_table(client: ClickHouseHttpClient, args: argparse.Namespace) -> None:
    client.execute(
        f"""
CREATE TABLE IF NOT EXISTS {table(args.database, args.manifest_table)}
(
    run_id String,
    partition_id UInt32,
    renderer_version LowCardinality(String),
    source_rows UInt64,
    rendered_rows UInt64,
    excluded_rows UInt64,
    source_chars UInt64,
    rendered_chars UInt64,
    output_parts UInt32,
    status LowCardinality(String),
    error String,
    wall_seconds Float64,
    updated_at_utc DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at_utc)
ORDER BY (run_id, partition_id)
"""
    )


def ensure_bundle_manifest_table(client: ClickHouseHttpClient, args: argparse.Namespace) -> None:
    client.execute(
        f"""
CREATE TABLE IF NOT EXISTS {table(args.database, args.bundle_manifest_table)}
(
    run_id String,
    partition_id UInt32,
    bundle_id UInt32,
    row_group_start UInt32,
    row_group_end UInt32,
    renderer_version LowCardinality(String),
    physical_rows UInt64,
    source_rows UInt64,
    rendered_rows UInt64,
    excluded_rows UInt64,
    source_chars UInt64,
    rendered_chars UInt64,
    output_parts UInt32,
    status LowCardinality(String),
    error String,
    wall_seconds Float64,
    updated_at_utc DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at_utc)
ORDER BY (run_id, partition_id, bundle_id)
"""
    )


def verify_clickhouse_file_root(client: ClickHouseHttpClient, args: argparse.Namespace, run_root: Path) -> None:
    probe_path = run_root / f"clickhouse_file_probe_{os.getpid()}.csv"
    probe_path.write_text("1\n", encoding="ascii")
    clickhouse_path = windows_path_to_clickhouse_path(probe_path, Path(args.file_root_win), args.file_root_ch)
    try:
        count = scalar_int(
            client,
            f"SELECT count() FROM file({sql_string(clickhouse_path)}, 'CSV', 'value UInt8') WHERE value=1",
        )
        if count != 1:
            raise RuntimeError(f"ClickHouse file-root probe returned {count}, expected 1")
    except Exception as exc:
        raise RuntimeError(
            "ClickHouse and the Python worker do not share the configured file root. "
            "Run this script on the workstation that hosts /mnt/d and D:\\market-data."
        ) from exc
    finally:
        probe_path.unlink(missing_ok=True)


def ensure_staging_table(client: ClickHouseHttpClient, args: argparse.Namespace) -> None:
    exists = scalar_int(
        client,
        f"SELECT count() FROM system.tables WHERE database={sql_string(args.database)} AND name={sql_string(args.staging_table)}",
    )
    if not exists:
        create_rendered_table(
            client,
            database=args.database,
            table_name=args.staging_table,
            schema_table=args.target_table,
            partition_key=BUILD_PARTITION_KEY,
            deduplication_window=100000,
        )
    layout = load_table_layout(client, args.database, args.staging_table)
    if normalized_layout_key(layout["partition_key"]) == normalized_layout_key(FINAL_PARTITION_KEY):
        migrate_hash_staging_to_monthly(client, args)
        layout = load_table_layout(client, args.database, args.staging_table)
    if normalized_layout_key(layout["partition_key"]) != normalized_layout_key(BUILD_PARTITION_KEY):
        raise RuntimeError(
            f"rebuild staging table has unsupported partition key {layout['partition_key']!r}; "
            f"expected {BUILD_PARTITION_KEY!r}"
        )
    legacy_table = f"{args.staging_table}_hash_legacy"
    if table_exists(client, args.database, legacy_table):
        client.execute(f"SYSTEM STOP MERGES {table(args.database, legacy_table)}")
    # Bundle retries use deterministic insert tokens. Persist enough recent
    # block ids for the complete rebuild so a crash between insert and
    # checkpoint cannot create physical duplicate parts.
    client.execute(
        f"ALTER TABLE {table(args.database, args.staging_table)} MODIFY SETTING "
        "non_replicated_deduplication_window=100000"
    )


def create_rendered_table(
    client: ClickHouseHttpClient,
    *,
    database: str,
    table_name: str,
    schema_table: str,
    partition_key: str,
    deduplication_window: int = 0,
) -> None:
    storage_policy = load_table_layout(client, database, schema_table)["storage_policy"]
    settings = "index_granularity=8192"
    if storage_policy:
        settings += f", storage_policy={sql_string(storage_policy)}"
    if deduplication_window:
        settings += f", non_replicated_deduplication_window={deduplication_window}"
    client.execute(
        f"""
CREATE TABLE {table(database, table_name)} AS {table(database, schema_table)}
ENGINE = ReplacingMergeTree(source_revision_rank)
PARTITION BY {partition_key}
ORDER BY ({RENDERED_SORTING_KEY})
SETTINGS {settings}
"""
    )


def load_table_layout(client: ClickHouseHttpClient, database: str, table_name: str) -> dict[str, str]:
    return query_one(
        client,
        f"""
SELECT partition_key, sorting_key, storage_policy
FROM system.tables
WHERE database={sql_string(database)} AND name={sql_string(table_name)}
""",
    )


def normalized_layout_key(value: str) -> str:
    return re.sub(r"[\s`()]+", "", str(value or "")).lower()


def table_exists(client: ClickHouseHttpClient, database: str, table_name: str) -> bool:
    return bool(
        scalar_int(
            client,
            f"SELECT count() FROM system.tables WHERE database={sql_string(database)} "
            f"AND name={sql_string(table_name)}",
        )
    )


def rendered_table_stats(client: ClickHouseHttpClient, database: str, table_name: str, where: str = "") -> tuple[int, int]:
    row = query_one(
        client,
        f"""
SELECT count() AS rows,
       sum(cityHash64(cik, accession_number, document_id, text_kind, source_version_key, text_sha256)) AS checksum
FROM {table(database, table_name)} FINAL
{where}
""",
    )
    return int(row["rows"]), int(row["checksum"] or 0)


def rendered_table_stats_bounded(
    client: ClickHouseHttpClient, database: str, table_name: str
) -> tuple[int, int]:
    rows = 0
    checksum = 0
    for bucket in range(64):
        bucket_rows, bucket_checksum = rendered_table_stats(
            client,
            database,
            table_name,
            f"PREWHERE cityHash64(cik) % 64={bucket} "
            "SETTINGS do_not_merge_across_partitions_select_final=0, "
            "max_threads=1, max_memory_usage=8589934592",
        )
        rows += bucket_rows
        checksum += bucket_checksum
    return rows, checksum


def migrate_hash_staging_to_monthly(client: ClickHouseHttpClient, args: argparse.Namespace) -> None:
    migration_table = f"{args.staging_table}_monthly_migration"
    legacy_table = f"{args.staging_table}_hash_legacy"
    # Freeze the merge storm before reading the legacy table. The table remains
    # queryable and insert-free while its validated logical rows are migrated.
    client.execute(f"SYSTEM STOP MERGES {table(args.database, args.staging_table)}")
    if table_exists(client, args.database, legacy_table):
        raise RuntimeError(
            f"legacy staging table already exists while active staging is still hash partitioned: {legacy_table}"
        )
    if not table_exists(client, args.database, migration_table):
        create_rendered_table(
            client,
            database=args.database,
            table_name=migration_table,
            schema_table=args.staging_table,
            partition_key=BUILD_PARTITION_KEY,
            deduplication_window=100000,
        )
    source_stats = rendered_table_stats(client, args.database, args.staging_table)
    migration_stats = rendered_table_stats(client, args.database, migration_table)
    if migration_stats != source_stats:
        if migration_stats[0]:
            client.execute(f"TRUNCATE TABLE {table(args.database, migration_table)}")
        columns = ", ".join(quote_ident(column) for column in TARGET_COLUMNS)
        print(
            f"staging_layout=migrate_hash_to_monthly status=active rows={source_stats[0]:,}",
            flush=True,
        )
        client.execute(
            f"""
INSERT INTO {table(args.database, migration_table)} ({columns})
SELECT {columns} FROM {table(args.database, args.staging_table)}
SETTINGS max_threads=2, max_insert_threads=1, max_memory_usage={parse_size_bytes(args.max_memory_usage)}
"""
        )
        migration_stats = rendered_table_stats(client, args.database, migration_table)
    if migration_stats != source_stats:
        raise RuntimeError(
            f"staging layout migration validation failed source={source_stats} migration={migration_stats}"
        )
    client.execute(
        f"RENAME TABLE {table(args.database, args.staging_table)} TO {table(args.database, legacy_table)}, "
        f"{table(args.database, migration_table)} TO {table(args.database, args.staging_table)}"
    )
    client.execute(f"SYSTEM STOP MERGES {table(args.database, legacy_table)}")
    print(
        f"staging_layout=migrate_hash_to_monthly status=completed rows={source_stats[0]:,} "
        f"legacy={args.database}.{legacy_table}",
        flush=True,
    )


def reset_authority_drift_partitions(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    run_id: str,
    run_root: Path,
    affected_partitions: set[int],
    source_watermark: SourceWatermark,
    filing_watermark: FilingWatermark,
    partitions: list[dict[str, int]],
) -> None:
    ordered = sorted(affected_partitions)
    if not ordered:
        return
    print(
        f"source_authority_reset status=active partitions={len(ordered)} "
        f"first={ordered[0]} last={ordered[-1]}",
        flush=True,
    )
    active_staging_partitions = {
        int(line)
        for line in client.execute(
            f"SELECT DISTINCT partition_id FROM system.parts WHERE database={sql_string(args.database)} "
            f"AND table={sql_string(args.staging_table)} AND active FORMAT TSV"
        ).splitlines()
        if line.strip()
    }
    for partition_id in ordered:
        if partition_id in active_staging_partitions:
            client.execute(f"ALTER TABLE {table(args.database, args.staging_table)} DROP PARTITION {partition_id}")
    partition_sql = ",".join(str(value) for value in ordered)
    for manifest_table in (args.manifest_table, args.bundle_manifest_table):
        client.execute(
            f"ALTER TABLE {table(args.database, manifest_table)} DELETE WHERE "
            f"run_id={sql_string(run_id)} AND partition_id IN ({partition_sql}) SETTINGS mutations_sync=2"
        )
    for partition_id in ordered:
        partition_root = run_root / "partitions" / str(partition_id)
        if partition_root.exists():
            cleanup_temp_root(partition_root, keep_source=False)
            try:
                partition_root.rmdir()
            except OSError:
                pass
    for name in (
        "render_lookup.sqlite",
        "render_lookup.sqlite.tmp",
        "filing_form_map.parquet",
        "source_authority.parquet",
        "STOP_REQUESTED.json",
        "partition_results.json",
        "validation.json",
    ):
        (run_root / name).unlink(missing_ok=True)
    rebase_run_manifest_after_source_migration(
        run_root,
        args,
        run_id,
        source_watermark,
        filing_watermark,
        partitions,
        ordered,
    )
    print(f"source_authority_reset status=completed partitions={len(ordered)}", flush=True)


def rebase_run_manifest_after_source_migration(
    run_root: Path,
    args: argparse.Namespace,
    run_id: str,
    source_watermark: SourceWatermark,
    filing_watermark: FilingWatermark,
    partitions: list[dict[str, int]],
    affected_partitions: list[int],
) -> None:
    manifest_path = run_root / "run_manifest.json"
    if not manifest_path.exists():
        return
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = {
        "run_id": run_id,
        "database": args.database,
        "source_table": args.source_table,
        "target_table": args.target_table,
        "staging_table": args.staging_table,
    }
    actual = {key: payload.get(key) for key in expected}
    if actual != expected:
        raise RuntimeError(f"cannot rebase mismatched run manifest expected={expected} actual={actual}")
    payload["source_watermark"] = asdict(source_watermark)
    payload["filing_watermark"] = asdict(filing_watermark)
    payload["partitions"] = partitions
    payload["source_authority_version"] = SOURCE_AUTHORITY_VERSION
    payload["source_engine_repair_partitions"] = affected_partitions
    payload["source_engine_repaired_at_utc"] = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    temporary = manifest_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(manifest_path)


def load_completed_partitions(client: ClickHouseHttpClient, args: argparse.Namespace, run_id: str) -> set[int]:
    text = client.execute(
        f"SELECT partition_id FROM {table(args.database, args.manifest_table)} FINAL "
        f"WHERE run_id={sql_string(run_id)} AND status='ok' FORMAT TSV"
    )
    return {int(line) for line in text.splitlines() if line.strip()}


def build_job(
    args: argparse.Namespace,
    run_id: str,
    run_root: Path,
    lookup_database_path: Path,
    row: dict[str, int],
) -> PartitionJob:
    return PartitionJob(
        partition_id=int(row["partition_id"]),
        expected_rows=int(row["source_rows"]),
        expected_source_chars=int(row["source_chars"]),
        run_id=run_id,
        run_root=str(run_root),
        database=args.database,
        source_table=args.source_table,
        staging_table=args.staging_table,
        manifest_table=args.manifest_table,
        bundle_manifest_table=args.bundle_manifest_table,
        clickhouse_url=args.clickhouse_url,
        clickhouse_user=args.user,
        clickhouse_password=args.password,
        file_root_win=args.file_root_win,
        file_root_ch=args.file_root_ch,
        lookup_database_path=str(lookup_database_path),
        export_threads=args.export_threads,
        insert_threads=args.insert_threads,
        max_memory_usage=parse_size_bytes(args.max_memory_usage),
        parquet_row_group_bytes=args.parquet_row_group_mib * 1024**2,
        parquet_file_bytes=args.parquet_file_mib * 1024**2,
        row_groups_per_bundle=args.row_groups_per_bundle,
        max_rows_per_partition=args.max_rows_per_partition,
        keep_temp_files=bool(args.keep_temp_files),
    )


def initialize_rebuild_worker(insert_semaphore: Any) -> None:
    global _INSERT_SEMAPHORE
    _INSERT_SEMAPHORE = insert_semaphore


@contextlib.contextmanager
def clickhouse_insert_slot() -> Any:
    semaphore = _INSERT_SEMAPHORE
    if semaphore is None:
        yield
        return
    semaphore.acquire()
    try:
        yield
    finally:
        semaphore.release()


def run_jobs(
    client: ClickHouseHttpClient,
    jobs: list[PartitionJob],
    *,
    max_workers: int,
    max_concurrent_inserts: int = 2,
    total_partitions: int,
    already_completed: int,
) -> list[PartitionResult]:
    if not jobs:
        return []
    stop_path = rebuild_stop_path(Path(jobs[0].run_root))
    stop_path.unlink(missing_ok=True)
    results: list[PartitionResult] = []
    completed = already_completed
    started = time.perf_counter()
    next_job = iter(jobs)
    no_more_jobs = False
    first_failure: PartitionResult | None = None

    insert_semaphore = multiprocessing.get_context().BoundedSemaphore(max_concurrent_inserts)
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=max_workers,
        initializer=initialize_rebuild_worker,
        initargs=(insert_semaphore,),
    ) as executor:
        futures: dict[concurrent.futures.Future[PartitionResult], PartitionJob] = {}
        try:
            while futures or (not no_more_jobs and first_failure is None):
                while len(futures) < max_workers and not no_more_jobs and first_failure is None:
                    completed_futures = [future for future in futures if future.done()]
                    if completed_futures:
                        first_failure = collect_partition_results(
                            client,
                            futures,
                            completed_futures,
                            results,
                            completed,
                            already_completed,
                            total_partitions,
                            started,
                        )
                        completed += len(completed_futures)
                        if first_failure is not None:
                            print_run_failure(first_failure, active_renderers=len(futures))
                            break

                    try:
                        job = next(next_job)
                    except StopIteration:
                        no_more_jobs = True
                        break

                    print(f"partition={job.partition_id} stage=export status=active", flush=True)
                    export_started = time.perf_counter()
                    try:
                        reused_export = prepare_partition_export(client, job)
                    except Exception as exc:  # noqa: BLE001
                        result = failure_result(job, "export", exc, export_started)
                        insert_partition_manifest(client, job, result)
                        results.append(result)
                        completed += 1
                        print_partition_result(
                            result,
                            completed=completed,
                            already_completed=already_completed,
                            total_partitions=total_partitions,
                            started=started,
                        )
                        first_failure = result
                        print_run_failure(first_failure, active_renderers=len(futures))
                        break
                    print(
                        f"partition={job.partition_id} stage=export status=completed "
                        f"reused={str(bool(reused_export)).lower()} "
                        f"wall={time.perf_counter() - export_started:.1f}s",
                        flush=True,
                    )
                    futures[executor.submit(process_exported_partition, job)] = job

                if not futures:
                    break

                done, _ = concurrent.futures.wait(
                    futures,
                    return_when=(
                        concurrent.futures.ALL_COMPLETED
                        if first_failure is not None
                        else concurrent.futures.FIRST_COMPLETED
                    ),
                )
                new_failure = collect_partition_results(
                    client,
                    futures,
                    list(done),
                    results,
                    completed,
                    already_completed,
                    total_partitions,
                    started,
                )
                completed += len(done)
                if first_failure is None and new_failure is not None:
                    first_failure = new_failure
                    print_run_failure(first_failure, active_renderers=len(futures))
        except KeyboardInterrupt:
            for future in futures:
                future.cancel()
            raise

    if first_failure is not None:
        write_results(Path(jobs[0].run_root), sorted(results, key=lambda item: item.partition_id))
        raise RuntimeError(f"partition {first_failure.partition_id} failed: {first_failure.error}")
    return sorted(results, key=lambda item: item.partition_id)


def collect_partition_results(
    client: ClickHouseHttpClient,
    futures: dict[concurrent.futures.Future[PartitionResult], PartitionJob],
    done: list[concurrent.futures.Future[PartitionResult]],
    results: list[PartitionResult],
    completed: int,
    already_completed: int,
    total_partitions: int,
    started: float,
) -> PartitionResult | None:
    first_failure: PartitionResult | None = None
    for offset, future in enumerate(done, start=1):
        job = futures.pop(future)
        try:
            result = future.result()
        except Exception as exc:  # noqa: BLE001
            result = failure_result(job, "render_or_insert", exc, started_at=None)
            insert_partition_manifest(client, job, result)
        else:
            if result.status != "ok":
                insert_partition_manifest(client, job, result)
        results.append(result)
        print_partition_result(
            result,
            completed=completed + offset,
            already_completed=already_completed,
            total_partitions=total_partitions,
            started=started,
        )
        if result.status == "error" and first_failure is None:
            first_failure = result
    return first_failure


def print_partition_result(
    result: PartitionResult,
    *,
    completed: int,
    already_completed: int,
    total_partitions: int,
    started: float,
) -> None:
    elapsed = time.perf_counter() - started
    rate = (completed - already_completed) / elapsed if elapsed > 0 else 0.0
    print(
        f"partition={result.partition_id} status={result.status} overall={completed}/{total_partitions} "
        f"source={result.source_rows:,} rendered={result.rendered_rows:,} excluded={result.excluded_rows:,} "
        f"wall={result.wall_seconds:.1f}s rate={rate:.3f}_partitions/s error={result.error!r}",
        flush=True,
    )


def print_run_failure(result: PartitionResult, *, active_renderers: int) -> None:
    print(
        f"run_status=failed first_partition={result.partition_id} action=cooperative_stop_at_bundle_boundary "
        f"active_renderers={active_renderers} error={result.error!r}",
        flush=True,
    )


def failure_result(
    job: PartitionJob,
    stage: str,
    exc: Exception,
    started_at: float | None,
) -> PartitionResult:
    wall_seconds = 0.0 if started_at is None else round(time.perf_counter() - started_at, 3)
    return PartitionResult(
        job.partition_id,
        0,
        0,
        0,
        0,
        0,
        0,
        wall_seconds,
        "error",
        f"stage={stage} {type(exc).__name__}: {exc}",
    )


def prepare_partition_export(client: ClickHouseHttpClient, job: PartitionJob) -> bool:
    partition_root = Path(job.run_root) / "partitions" / str(job.partition_id)
    source_path = partition_root / f"source_{job.partition_id}.parquet"
    receipt_path = partition_root / "source_export.json"
    partition_root.mkdir(parents=True, exist_ok=True)
    reset_invalidated_partition(client, job, partition_root)
    cleanup_temp_root(partition_root, keep_source=True)
    if validate_source_export(job, source_path, receipt_path):
        return True
    source_path.unlink(missing_ok=True)
    receipt_path.unlink(missing_ok=True)
    export_source_partition(client, job, source_path)
    write_source_export_receipt(job, source_path, receipt_path)
    return False


def validate_source_export(job: PartitionJob, source_path: Path, receipt_path: Path) -> bool:
    if not source_path.is_file() or source_path.stat().st_size <= 0:
        return False
    try:
        parquet = pq.ParquetFile(source_path)
        try:
            physical_rows = int(parquet.metadata.num_rows)
            schema_names = list(parquet.schema_arrow.names)
        finally:
            parquet.close()
    except Exception:  # noqa: BLE001
        return False
    if schema_names != SOURCE_COLUMNS or physical_rows < minimum_export_rows(job):
        return False

    expected = source_export_identity(job, source_path, physical_rows)
    if receipt_path.exists():
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return all(receipt.get(key) == value for key, value in expected.items())

    # Runs created before export receipts are safe to adopt because the run
    # manifest locks source and filing watermarks and ClickHouse writes the
    # Parquet path atomically enough to require a valid footer here.
    write_source_export_receipt(job, source_path, receipt_path, physical_rows=physical_rows)
    return True


def write_source_export_receipt(
    job: PartitionJob,
    source_path: Path,
    receipt_path: Path,
    *,
    physical_rows: int | None = None,
) -> None:
    if physical_rows is None:
        parquet = pq.ParquetFile(source_path)
        try:
            physical_rows = int(parquet.metadata.num_rows)
            if list(parquet.schema_arrow.names) != SOURCE_COLUMNS:
                raise RuntimeError(f"source export schema mismatch: {source_path}")
        finally:
            parquet.close()
    minimum_rows = minimum_export_rows(job)
    if physical_rows < minimum_rows:
        raise RuntimeError(
            f"source export row count is incomplete partition={job.partition_id} "
            f"expected_at_least={minimum_rows} actual={physical_rows}"
        )
    payload = {
        **source_export_identity(job, source_path, physical_rows),
        "schema": SOURCE_COLUMNS,
        "completed_at_utc": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    temporary_path = receipt_path.with_suffix(".json.tmp")
    temporary_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary_path.replace(receipt_path)


def source_export_identity(job: PartitionJob, source_path: Path, physical_rows: int) -> dict[str, Any]:
    return {
        "format_version": 1,
        "run_id": job.run_id,
        "database": job.database,
        "source_table": job.source_table,
        "partition_id": job.partition_id,
        "expected_rows": job.expected_rows,
        "expected_source_chars": job.expected_source_chars,
        "file_name": source_path.name,
        "file_size": source_path.stat().st_size,
        "physical_rows": physical_rows,
    }


def minimum_export_rows(job: PartitionJob) -> int:
    row_limit = int(getattr(job, "max_rows_per_partition", 0) or 0)
    return min(job.expected_rows, row_limit) if row_limit else job.expected_rows


def invalidate_source_export(partition_root: Path, source_path: Path) -> None:
    source_path.unlink(missing_ok=True)
    (partition_root / "source_export.json").unlink(missing_ok=True)
    (partition_root / "source_export_requires_bundle_reset").write_text(
        "Source transport was invalidated; staged rows and bundle checkpoints must be reset before re-export.\n",
        encoding="utf-8",
    )


def reset_invalidated_partition(client: ClickHouseHttpClient, job: PartitionJob, partition_root: Path) -> None:
    marker = partition_root / "source_export_requires_bundle_reset"
    if not marker.exists():
        return
    cleanup_partition_staging(client, job)
    client.execute(
        f"ALTER TABLE {table(job.database, job.bundle_manifest_table)} DELETE WHERE "
        f"run_id={sql_string(job.run_id)} AND partition_id={job.partition_id} SETTINGS mutations_sync=2"
    )
    marker.unlink()


def process_exported_partition(job: PartitionJob) -> PartitionResult:
    started = time.perf_counter()
    try:
        return _process_exported_partition(job)
    except Exception as exc:  # noqa: BLE001
        request_rebuild_stop(Path(job.run_root), job.partition_id, exc)
        return failure_result(job, "render_or_insert", exc, started)


def _process_exported_partition(job: PartitionJob) -> PartitionResult:
    started = time.perf_counter()
    client = ClickHouseHttpClient(job.clickhouse_url, job.clickhouse_user, job.clickhouse_password)
    partition_root = Path(job.run_root) / "partitions" / str(job.partition_id)
    source_path = partition_root / f"source_{job.partition_id}.parquet"
    try:
        source_parquet = pq.ParquetFile(source_path)
        try:
            physical_rows = int(source_parquet.metadata.num_rows)
            row_group_count = int(source_parquet.metadata.num_row_groups)
        finally:
            source_parquet.close()
    except Exception:  # noqa: BLE001
        invalidate_source_export(partition_root, source_path)
        raise
    authority = load_partition_authority(Path(job.lookup_database_path), job.partition_id)
    filing_forms = load_filing_forms(
        Path(job.lookup_database_path),
        {row[2] for row in authority.values()},
    )
    bundles = build_row_group_bundles(row_group_count, job.row_groups_per_bundle)
    completed_bundles = load_completed_bundles(client, job)
    validate_completed_bundle_prefix(completed_bundles, total_bundles=len(bundles))
    seen_authority_keys = load_completed_bundle_authority_keys(
        source_path,
        bundles,
        completed_bundles,
        authority,
    )
    for bundle_id, row_group_start, row_group_end in bundles:
        if bundle_id in completed_bundles:
            continue
        if rebuild_stop_path(Path(job.run_root)).exists():
            return PartitionResult(
                job.partition_id, 0, 0, 0, 0, 0, 0,
                round(time.perf_counter() - started, 3), "stopped", "another worker requested stop",
            )
        try:
            bundle = process_row_group_bundle(
                client,
                job,
                source_path,
                authority,
                filing_forms,
                seen_authority_keys,
                bundle_id,
                row_group_start,
                row_group_end,
            )
            insert_bundle_manifest(client, job, bundle)
        except Exception as exc:  # noqa: BLE001
            request_rebuild_stop(Path(job.run_root), job.partition_id, exc)
            failure = BundleResult(
                job.partition_id, bundle_id, row_group_start, row_group_end,
                0, 0, 0, 0, 0, 0, 0, 0.0, "error", f"{type(exc).__name__}: {exc}",
            )
            insert_bundle_manifest(client, job, failure)
            return failure_result(job, f"bundle_{bundle_id}", exc, started)

    try:
        aggregate = load_bundle_aggregate(client, job, expected_bundles=len(bundles))
        if not job.max_rows_per_partition and aggregate.source_rows != job.expected_rows:
            raise RuntimeError(
                f"authoritative source row mismatch expected={job.expected_rows} actual={aggregate.source_rows} "
                f"physical={physical_rows}"
            )
        if aggregate.rendered_rows + aggregate.excluded_rows != aggregate.source_rows:
            raise RuntimeError(
                "partition accounting mismatch "
                f"source={aggregate.source_rows} rendered={aggregate.rendered_rows} excluded={aggregate.excluded_rows}"
            )
    except Exception as exc:  # noqa: BLE001
        request_rebuild_stop(Path(job.run_root), job.partition_id, exc)
        return failure_result(job, "partition_accounting", exc, started)
    result = PartitionResult(
        partition_id=job.partition_id,
        source_rows=aggregate.source_rows,
        rendered_rows=aggregate.rendered_rows,
        excluded_rows=aggregate.excluded_rows,
        source_chars=aggregate.source_chars,
        rendered_chars=aggregate.rendered_chars,
        output_parts=aggregate.output_parts,
        wall_seconds=round(time.perf_counter() - started, 3),
        status="ok",
    )
    insert_partition_manifest(client, job, result)
    if not job.keep_temp_files:
        cleanup_temp_root(partition_root, keep_source=False)
    return result


def build_row_group_bundles(row_group_count: int, row_groups_per_bundle: int) -> list[tuple[int, int, int]]:
    return [
        (bundle_id, start, min(start + row_groups_per_bundle, row_group_count))
        for bundle_id, start in enumerate(range(0, row_group_count, row_groups_per_bundle), start=1)
    ]


def validate_completed_bundle_prefix(completed_bundles: set[int], *, total_bundles: int) -> None:
    if not completed_bundles:
        return
    if min(completed_bundles) < 1 or max(completed_bundles) > total_bundles:
        raise RuntimeError(
            f"completed bundle checkpoints are outside current inventory total={total_bundles} "
            f"completed={sorted(completed_bundles)}"
        )
    expected = set(range(1, max(completed_bundles) + 1))
    if completed_bundles != expected:
        raise RuntimeError(
            "completed bundle checkpoints are not a contiguous prefix; "
            f"completed={sorted(completed_bundles)}"
        )


def load_completed_bundle_authority_keys(
    source_path: Path,
    bundles: list[tuple[int, int, int]],
    completed_bundles: set[int],
    authority: dict[tuple[str, str, str, str], tuple[str, int, str]],
) -> set[tuple[str, str, str, str]]:
    if not completed_bundles:
        return set()
    completed_row_groups = [
        row_group
        for bundle_id, start, end in bundles
        if bundle_id in completed_bundles
        for row_group in range(start, end)
    ]
    seen: set[tuple[str, str, str, str]] = set()
    parquet = pq.ParquetFile(source_path)
    try:
        columns = [
            "cik",
            "accession_number",
            "document_id",
            "content_format",
            "source_version_key",
            "source_revision_rank",
        ]
        for batch in parquet.iter_batches(batch_size=8192, row_groups=completed_row_groups, columns=columns):
            for source in batch.to_pylist():
                key = source_authority_key(source)
                authority_row = authority.get(key)
                if authority_row is None:
                    continue
                source_version_key, source_revision_rank, _ = authority_row
                if (
                    str(source.get("source_version_key") or "") == source_version_key
                    and int(source.get("source_revision_rank") or 0) == source_revision_rank
                ):
                    seen.add(key)
    finally:
        parquet.close()
    return seen


def process_row_group_bundle(
    client: ClickHouseHttpClient,
    job: PartitionJob,
    source_path: Path,
    authority: dict[tuple[str, str, str, str], tuple[str, int, str]],
    filing_forms: dict[str, str],
    seen_authority_keys: set[tuple[str, str, str, str]],
    bundle_id: int,
    row_group_start: int,
    row_group_end: int,
) -> BundleResult:
    started = time.perf_counter()
    bundle_root = source_path.parent / "rendered" / f"bundle_{bundle_id:05d}"
    if bundle_root.exists():
        cleanup_temp_root(bundle_root, keep_source=False)
    writer = ParquetShardWriter(
        dataset_name="rendered_text_v3",
        target_table=job.staging_table,
        output_directory=bundle_root,
        filename_prefix=f"rendered_{job.partition_id}_{bundle_id:05d}",
        columns=TARGET_COLUMNS,
        archive_index=bundle_id,
        row_group_bytes=job.parquet_row_group_bytes,
        file_bytes=job.parquet_file_bytes,
        compression_level=1,
    )
    physical_rows = source_rows = excluded_rows = source_chars = rendered_chars = 0
    extracted_at = datetime.now(UTC)
    try:
        parquet = pq.ParquetFile(source_path)
        source_read_error: Exception | None = None
        try:
            row_groups = list(range(row_group_start, row_group_end))
            batches = iter(parquet.iter_batches(batch_size=8, row_groups=row_groups))
            while True:
                try:
                    batch = next(batches)
                except StopIteration:
                    break
                except Exception as exc:  # noqa: BLE001
                    source_read_error = exc
                    break
                sources = batch.to_pylist()
                physical_rows += len(sources)
                for source in sources:
                    key = source_authority_key(source)
                    if key in seen_authority_keys:
                        continue
                    authority_row = authority.get(key)
                    if authority_row is None:
                        continue
                    source_version_key, source_revision_rank, filing_id = authority_row
                    if (
                        str(source.get("source_version_key") or "") != source_version_key
                        or int(source.get("source_revision_rank") or 0) != source_revision_rank
                    ):
                        continue
                    seen_authority_keys.add(key)
                    source_rows += 1
                    source_text = str(source.get("source_text") or "")
                    source_chars += len(source_text)
                    rendered = render_sec_packed_text(
                        source_text,
                        str(source.get("content_format") or ""),
                        document_name=str(source.get("document_name") or ""),
                        document_type=str(source.get("document_type") or ""),
                        form_type=filing_forms[filing_id],
                        text_kind=str(source.get("text_kind") or ""),
                        include_intermediate=False,
                    )
                    text = rendered.packed_text
                    if STRUCTURED_XML_EXCLUDED_QUALITY_FLAG in rendered.quality_flags:
                        excluded_rows += 1
                        continue
                    if not text:
                        raise RuntimeError(
                            "renderer produced an unexpectedly empty non-structured document "
                            f"document_id={source.get('document_id')} accession={source.get('accession_number')} "
                            f"content_format={source.get('content_format')} source_chars={len(source_text)} "
                            f"quality_flags={rendered.quality_flags}"
                        )
                    rendered_chars += len(text)
                    writer.append(build_rendered_row(source, text, rendered.quality_flags, job.run_id, extracted_at))
        finally:
            parquet.close()
        if source_read_error is not None:
            invalidate_source_export(source_path.parent, source_path)
            raise RuntimeError(
                f"source Parquet read failed partition={job.partition_id} bundle={bundle_id}; "
                "export invalidated for retry"
            ) from source_read_error
        parts = writer.close()
    except Exception:
        writer.abort()
        raise
    rendered_rows = sum(int(part["rows"]) for part in parts)
    if rendered_rows + excluded_rows != source_rows:
        raise RuntimeError(
            f"bundle accounting mismatch source={source_rows} rendered={rendered_rows} excluded={excluded_rows}"
        )
    for part_index, part in enumerate(parts, start=1):
        insert_rendered_part(client, job, Path(part["path"]), bundle_id=bundle_id, part_index=part_index)
    wall_seconds = round(time.perf_counter() - started, 3)
    result = BundleResult(
        job.partition_id, bundle_id, row_group_start, row_group_end, physical_rows,
        source_rows, rendered_rows, excluded_rows, source_chars, rendered_chars,
        len(parts), wall_seconds, "ok",
    )
    print(
        f"partition={job.partition_id} bundle={bundle_id} "
        f"row_groups={row_group_start}:{row_group_end} source={source_rows:,} rendered={rendered_rows:,} "
        f"chars_per_second={source_chars / wall_seconds if wall_seconds else 0:.0f} wall={wall_seconds:.1f}s",
        flush=True,
    )
    if not job.keep_temp_files:
        cleanup_temp_root(bundle_root, keep_source=False)
    return result


def load_filing_forms(lookup_database_path: Path, filing_ids: set[str]) -> dict[str, str]:
    forms: dict[str, str] = {}
    connection = sqlite3.connect(f"{lookup_database_path.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
    try:
        ordered = sorted(filing_ids)
        chunk_size = 500
        for start in range(0, len(ordered), chunk_size):
            chunk = ordered[start : start + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            forms.update(
                (str(filing_id), str(form_type or ""))
                for filing_id, form_type in connection.execute(
                    f"SELECT filing_id, form_type FROM filing_forms WHERE filing_id IN ({placeholders})",
                    chunk,
                )
            )
    finally:
        connection.close()
    missing = filing_ids.difference(forms)
    if missing:
        raise RuntimeError(
            f"source partition has {len(missing):,} filing_id values absent from sec_filing_v3; "
            f"sample={sorted(missing)[:10]}"
        )
    return forms


def load_partition_authority(
    lookup_database_path: Path,
    partition_id: int,
) -> dict[tuple[str, str, str, str], tuple[str, int, str]]:
    connection = sqlite3.connect(f"{lookup_database_path.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
    try:
        rows = connection.execute(
            "SELECT cik, accession_number, document_id, content_format, source_version_key, "
            "source_revision_rank, filing_id FROM source_authority WHERE partition_id=?",
            (partition_id,),
        )
        authority = {
            (str(cik), str(accession), str(document_id), str(content_format)): (
                str(source_version_key), int(source_revision_rank), str(filing_id)
            )
            for cik, accession, document_id, content_format, source_version_key, source_revision_rank, filing_id in rows
        }
    finally:
        connection.close()
    return authority


def source_authority_key(source: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(source.get("cik") or ""),
        str(source.get("accession_number") or ""),
        str(source.get("document_id") or ""),
        str(source.get("content_format") or ""),
    )


def cleanup_partition_staging(client: ClickHouseHttpClient, job: PartitionJob) -> None:
    client.execute(
        f"ALTER TABLE {table(job.database, job.staging_table)} DELETE WHERE "
        f"toYYYYMM(source_archive_date)={job.partition_id} SETTINGS mutations_sync=2"
    )


def export_source_partition(client: ClickHouseHttpClient, job: PartitionJob, source_path: Path) -> None:
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.unlink(missing_ok=True)
    clickhouse_path = windows_path_to_clickhouse_path(source_path, Path(job.file_root_win), job.file_root_ch)
    source_columns = ", ".join(f"s.{quote_ident(column)}" for column in SOURCE_COLUMNS)
    limit_sql = f" LIMIT {job.max_rows_per_partition}" if job.max_rows_per_partition else ""
    client.execute(
        f"""
INSERT INTO TABLE FUNCTION file({sql_string(clickhouse_path)}, 'Parquet')
SELECT {source_columns}
FROM {table(job.database, job.source_table)} AS s
WHERE toYYYYMM(s.source_archive_date)={job.partition_id}
{limit_sql}
SETTINGS max_threads={job.export_threads}, max_memory_usage={job.max_memory_usage},
         max_block_size=8, preferred_block_size_bytes=16777216,
         output_format_parquet_batch_size=1,
         output_format_parquet_row_group_size=1024,
         output_format_parquet_row_group_size_bytes=268435456,
         output_format_parquet_parallel_encoding=0,
         output_format_parquet_write_bloom_filter=0,
         output_format_parquet_compression_method='zstd', output_format_parquet_compliant_nested_types=1
"""
    )


def build_rendered_row(source: dict[str, Any], text: str, quality_flags: list[str], run_id: str, extracted_at: datetime) -> dict[str, Any]:
    return {
        "document_id": source["document_id"],
        "filing_id": source["filing_id"],
        "accession_number": source["accession_number"],
        "accession_number_compact": source["accession_number_compact"],
        "cik": source["cik"],
        "text_kind": source["text_kind"],
        "text": text,
        "text_char_count": len(text),
        "text_byte_count": len(text.encode("utf-8")),
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "extraction_method": SEC_PACKED_TEXT_RENDERER_VERSION,
        "normalizer_version": SEC_PACKED_TEXT_RENDERER_VERSION,
        "quality_flags": sorted(set(quality_flags)),
        "source_archive_date": source["source_archive_date"],
        "source_archive_member": source["source_archive_member"],
        "source_version_key": source["source_version_key"],
        "source_revision_at": source["source_revision_at"],
        "source_revision_rank": source["source_revision_rank"],
        "source_revision_kind": source["source_revision_kind"],
        "pac_event_id": source.get("pac_event_id"),
        "extracted_at_utc": extracted_at,
        "source_run_id": run_id,
        "inserted_at": extracted_at,
    }


def insert_rendered_part(
    client: ClickHouseHttpClient,
    job: PartitionJob,
    path: Path,
    *,
    bundle_id: int,
    part_index: int,
) -> None:
    clickhouse_path = windows_path_to_clickhouse_path(path, Path(job.file_root_win), job.file_root_ch)
    columns = ", ".join(quote_ident(column) for column in TARGET_COLUMNS)
    print(
        f"partition={job.partition_id} bundle={bundle_id} part={part_index} stage=insert status=waiting",
        flush=True,
    )
    started = time.perf_counter()
    with clickhouse_insert_slot():
        print(
            f"partition={job.partition_id} bundle={bundle_id} part={part_index} stage=insert status=active",
            flush=True,
        )
        client.execute(
            f"""
INSERT INTO {table(job.database, job.staging_table)} ({columns})
SELECT {columns}
FROM file({sql_string(clickhouse_path)}, 'Parquet')
SETTINGS max_threads={job.insert_threads}, max_memory_usage={job.max_memory_usage},
         input_format_parquet_use_native_reader_v3=1, input_format_parquet_verify_checksums=1,
         insert_deduplication_token={sql_string(f'{job.run_id}:{job.partition_id}:{bundle_id}:{part_index}')}
"""
        )
    print(
        f"partition={job.partition_id} bundle={bundle_id} part={part_index} stage=insert status=completed "
        f"wall={time.perf_counter() - started:.1f}s",
        flush=True,
    )


def load_completed_bundles(client: ClickHouseHttpClient, job: PartitionJob) -> set[int]:
    text = client.execute(
        f"SELECT bundle_id FROM {table(job.database, job.bundle_manifest_table)} FINAL "
        f"WHERE run_id={sql_string(job.run_id)} AND partition_id={job.partition_id} "
        f"AND renderer_version={sql_string(SEC_PACKED_TEXT_RENDERER_VERSION)} AND status='ok' FORMAT TSV"
    )
    return {int(line) for line in text.splitlines() if line.strip()}


def insert_bundle_manifest(client: ClickHouseHttpClient, job: PartitionJob, result: BundleResult) -> None:
    row = {
        **asdict(result),
        "run_id": job.run_id,
        "renderer_version": SEC_PACKED_TEXT_RENDERER_VERSION,
        "updated_at_utc": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    }
    client.execute(
        f"INSERT INTO {table(job.database, job.bundle_manifest_table)} "
        "SETTINGS date_time_input_format='best_effort' FORMAT JSONEachRow\n"
        f"{json.dumps(row, separators=(',', ':'))}"
    )


def load_bundle_aggregate(
    client: ClickHouseHttpClient,
    job: PartitionJob,
    *,
    expected_bundles: int,
) -> PartitionResult:
    row = query_one(
        client,
        f"""
SELECT count() AS bundles, sum(source_rows) AS source_rows, sum(rendered_rows) AS rendered_rows,
       sum(excluded_rows) AS excluded_rows, sum(source_chars) AS source_chars,
       sum(rendered_chars) AS rendered_chars, sum(output_parts) AS output_parts,
       sum(wall_seconds) AS wall_seconds
FROM {table(job.database, job.bundle_manifest_table)} FINAL
WHERE run_id={sql_string(job.run_id)} AND partition_id={job.partition_id}
  AND renderer_version={sql_string(SEC_PACKED_TEXT_RENDERER_VERSION)} AND status='ok'
""",
    )
    if int(row["bundles"]) != expected_bundles:
        raise RuntimeError(
            f"bundle checkpoint mismatch expected={expected_bundles} actual={row['bundles']}"
        )
    return PartitionResult(
        job.partition_id,
        int(row["source_rows"]),
        int(row["rendered_rows"]),
        int(row["excluded_rows"]),
        int(row["source_chars"]),
        int(row["rendered_chars"]),
        int(row["output_parts"]),
        float(row["wall_seconds"]),
        "ok",
    )


def rebuild_stop_path(run_root: Path) -> Path:
    return run_root / "STOP_REQUESTED.json"


def request_rebuild_stop(run_root: Path, partition_id: int, exc: Exception) -> None:
    path = rebuild_stop_path(run_root)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    payload = {
        "partition_id": partition_id,
        "error": f"{type(exc).__name__}: {exc}",
        "requested_at_utc": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    }
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        temporary.replace(path)
    except OSError:
        temporary.unlink(missing_ok=True)


def insert_partition_manifest(client: ClickHouseHttpClient, job: PartitionJob, result: PartitionResult) -> None:
    row = {
        "run_id": job.run_id,
        "partition_id": result.partition_id,
        "renderer_version": SEC_PACKED_TEXT_RENDERER_VERSION,
        "source_rows": result.source_rows,
        "rendered_rows": result.rendered_rows,
        "excluded_rows": result.excluded_rows,
        "source_chars": result.source_chars,
        "rendered_chars": result.rendered_chars,
        "output_parts": result.output_parts,
        "status": result.status,
        "error": result.error,
        "wall_seconds": result.wall_seconds,
        "updated_at_utc": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    }
    client.execute(
        f"INSERT INTO {table(job.database, job.manifest_table)} SETTINGS date_time_input_format='best_effort' "
        f"FORMAT JSONEachRow\n{json.dumps(row, separators=(',', ':'))}"
    )


def validate_staging(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    run_id: str,
    partitions: list[dict[str, int]],
    initial_watermark: SourceWatermark,
    initial_filing_watermark: FilingWatermark,
) -> dict[str, Any]:
    current_watermark = load_source_watermark(client, args)
    current_filing_watermark = load_filing_watermark(client, args)
    manifest = query_one(
        client,
        f"""
SELECT count() AS partitions, sum(source_rows) AS source_rows, sum(rendered_rows) AS rendered_rows,
       sum(excluded_rows) AS excluded_rows, sum(source_chars) AS source_chars, sum(rendered_chars) AS rendered_chars,
       countIf(renderer_version!={sql_string(SEC_PACKED_TEXT_RENDERER_VERSION)} OR status!='ok') AS invalid_partitions
FROM {table(args.database, args.manifest_table)} FINAL
WHERE run_id={sql_string(run_id)}
""",
    )
    staging = validate_staging_rows_bounded(client, args, partitions)
    expected_source_rows = sum(int(row["source_rows"]) for row in partitions)
    errors: list[str] = []
    if current_watermark != initial_watermark:
        errors.append(f"source watermark changed initial={asdict(initial_watermark)} current={asdict(current_watermark)}")
    if current_filing_watermark != initial_filing_watermark:
        errors.append(
            "filing watermark changed "
            f"initial={asdict(initial_filing_watermark)} current={asdict(current_filing_watermark)}"
        )
    if int(manifest["partitions"]) != len(partitions):
        errors.append(f"manifest partitions expected={len(partitions)} actual={manifest['partitions']}")
    if int(manifest["source_rows"]) != expected_source_rows:
        errors.append(f"manifest source rows expected={expected_source_rows} actual={manifest['source_rows']}")
    if int(manifest["rendered_rows"]) + int(manifest["excluded_rows"]) != int(manifest["source_rows"]):
        errors.append("manifest rendered plus excluded rows do not equal source rows")
    if int(staging["rows"]) != int(manifest["rendered_rows"]):
        errors.append(f"staging rows expected={manifest['rendered_rows']} actual={staging['rows']}")
    for key in ("invalid_partitions",):
        if int(manifest[key]):
            errors.append(f"{key}={manifest[key]}")
    for key in ("stale_rows", "stale_methods", "empty_rows", "bad_char_counts", "bad_byte_counts", "bad_hashes"):
        if int(staging[key]):
            errors.append(f"{key}={staging[key]}")
    if int(staging["global_final_rows"]) != int(staging["rows"]):
        errors.append(
            "cross-partition duplicate logical keys "
            f"partition_rows={staging['rows']} global_final_rows={staging['global_final_rows']}"
        )
    if int(staging["unique_keys"]) != int(staging["global_final_rows"]):
        errors.append(f"duplicate logical keys rows={staging['rows']} unique={staging['unique_keys']}")
    validation = {
        "status": "ok" if not errors else "error",
        "renderer_version": SEC_PACKED_TEXT_RENDERER_VERSION,
        "initial_source_watermark": asdict(initial_watermark),
        "current_source_watermark": asdict(current_watermark),
        "initial_filing_watermark": asdict(initial_filing_watermark),
        "current_filing_watermark": asdict(current_filing_watermark),
        "manifest": manifest,
        "staging": staging,
        "errors": errors,
    }
    if errors:
        raise RuntimeError("staging validation failed: " + "; ".join(errors))
    return validation


def validate_staging_rows_bounded(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    partitions: list[dict[str, int]],
) -> dict[str, int]:
    validation_workers = 8
    validation_query_memory = 8589934592
    metric_names = (
        "rows",
        "stale_rows",
        "stale_methods",
        "empty_rows",
        "bad_char_counts",
        "bad_byte_counts",
        "bad_hashes",
    )
    totals = {name: 0 for name in metric_names}

    def validate_partition(partition: dict[str, int]) -> tuple[int, dict[str, Any]]:
        partition_id = int(partition["partition_id"])
        stats = query_one(
            client,
            f"""
SELECT count() AS rows, countIf(normalizer_version!={sql_string(SEC_PACKED_TEXT_RENDERER_VERSION)}) AS stale_rows,
       countIf(extraction_method!={sql_string(SEC_PACKED_TEXT_RENDERER_VERSION)}) AS stale_methods,
       countIf(text='' OR text_char_count=0) AS empty_rows,
       countIf(text_char_count!=lengthUTF8(text)) AS bad_char_counts,
       countIf(text_byte_count!=length(text)) AS bad_byte_counts,
       countIf(lower(hex(SHA256(text)))!=text_sha256) AS bad_hashes
FROM {table(args.database, args.staging_table)} FINAL
PREWHERE _partition_id={sql_string(str(partition_id))}
SETTINGS max_threads=1, max_memory_usage={validation_query_memory}
""",
        )
        return partition_id, stats

    with concurrent.futures.ThreadPoolExecutor(max_workers=validation_workers) as executor:
        partition_futures = {
            executor.submit(validate_partition, partition): int(partition["partition_id"])
            for partition in partitions
        }
        completed_partitions = 0
        for future in concurrent.futures.as_completed(partition_futures):
            partition_id, stats = future.result()
            completed_partitions += 1
            for name in metric_names:
                totals[name] += int(stats[name])
            print(
                f"validation_partition={completed_partitions}/{len(partitions)} partition={partition_id} "
                f"rows={int(stats['rows']):,} bad_hashes={int(stats['bad_hashes']):,}",
                flush=True,
            )

    def validate_key_bucket(bucket: int) -> tuple[int, dict[str, Any]]:
        stats = query_one(
            client,
            f"""
SELECT count() AS rows,
       uniqExact(tuple(cik, accession_number, document_id, text_kind)) AS unique_keys
FROM {table(args.database, args.staging_table)} FINAL
PREWHERE cityHash64(cik) % 64={bucket}
SETTINGS do_not_merge_across_partitions_select_final=0,
         max_threads=1, max_memory_usage={validation_query_memory}
""",
        )
        return bucket, stats

    global_final_rows = 0
    unique_keys = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=validation_workers) as executor:
        bucket_futures = [executor.submit(validate_key_bucket, bucket) for bucket in range(64)]
        completed_buckets = 0
        for future in concurrent.futures.as_completed(bucket_futures):
            bucket, stats = future.result()
            completed_buckets += 1
            global_final_rows += int(stats["rows"])
            unique_keys += int(stats["unique_keys"])
            print(
                f"validation_key_bucket={completed_buckets}/64 bucket={bucket} "
                f"rows={int(stats['rows']):,} unique_keys={int(stats['unique_keys']):,}",
                flush=True,
            )

    totals["global_final_rows"] = global_final_rows
    totals["unique_keys"] = unique_keys
    totals["validated_partitions"] = len(partitions)
    totals["validated_key_buckets"] = 64
    return totals


def cutover(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    run_id: str,
    watermark: SourceWatermark,
    filing_watermark: FilingWatermark,
) -> str:
    if load_source_watermark(client, args) != watermark:
        raise RuntimeError("source watermark changed after validation; refusing cutover")
    if load_filing_watermark(client, args) != filing_watermark:
        raise RuntimeError("filing metadata watermark changed after validation; refusing cutover")
    cutover_table = prepare_hash_cutover_table(client, args)
    suffix = re.sub(r"[^0-9]", "", run_id)[-14:] or datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    backup_table = f"sec_filing_text_rendered_pre_v8_{suffix}_v3"
    if scalar_int(
        client,
        f"SELECT count() FROM system.tables WHERE database={sql_string(args.database)} AND name={sql_string(backup_table)}",
    ):
        raise RuntimeError(f"backup table already exists: {args.database}.{backup_table}")
    client.execute(
        f"EXCHANGE TABLES {table(args.database, args.target_table)} AND {table(args.database, cutover_table)}"
    )
    client.execute(
        f"RENAME TABLE {table(args.database, cutover_table)} TO {table(args.database, backup_table)}"
    )
    target_stats = rendered_table_stats_bounded(client, args.database, args.target_table)
    staging_stats = rendered_table_stats_bounded(client, args.database, args.staging_table)
    if target_stats != staging_stats:
        raise RuntimeError(f"post-cutover row validation failed target={target_stats} staging={staging_stats}")
    client.execute(f"DROP TABLE {table(args.database, args.staging_table)}")
    legacy_table = f"{args.staging_table}_hash_legacy"
    if table_exists(client, args.database, legacy_table):
        client.execute(f"DROP TABLE {table(args.database, legacy_table)}")
    return backup_table


def prepare_hash_cutover_table(client: ClickHouseHttpClient, args: argparse.Namespace) -> str:
    cutover_table = f"{args.staging_table}_final_hash"
    if not table_exists(client, args.database, cutover_table):
        create_rendered_table(
            client,
            database=args.database,
            table_name=cutover_table,
            schema_table=args.staging_table,
            partition_key=FINAL_PARTITION_KEY,
            deduplication_window=100000,
        )
    layout = load_table_layout(client, args.database, cutover_table)
    if normalized_layout_key(layout["partition_key"]) != normalized_layout_key(FINAL_PARTITION_KEY):
        raise RuntimeError(f"cutover table has wrong partition key: {layout['partition_key']}")
    columns = ", ".join(quote_ident(column) for column in TARGET_COLUMNS)
    for partition_id in range(64):
        predicate = f"cityHash64(cik) % 64={partition_id}"
        source_stats = rendered_table_stats(
            client,
            args.database,
            args.staging_table,
            f"PREWHERE {predicate}",
        )
        target_stats = rendered_table_stats(
            client,
            args.database,
            cutover_table,
            f"PREWHERE {predicate}",
        )
        if target_stats == source_stats:
            print(
                f"cutover_repartition={partition_id + 1}/64 status=completed reused=true rows={source_stats[0]:,}",
                flush=True,
            )
            continue
        if target_stats[0]:
            client.execute(
                f"ALTER TABLE {table(args.database, cutover_table)} DROP PARTITION {partition_id}"
            )
        print(
            f"cutover_repartition={partition_id + 1}/64 status=active rows={source_stats[0]:,}",
            flush=True,
        )
        client.execute(
            f"""
INSERT INTO {table(args.database, cutover_table)} ({columns})
SELECT {columns}
FROM {table(args.database, args.staging_table)} FINAL
PREWHERE {predicate}
ORDER BY {RENDERED_SORTING_KEY}
SETTINGS max_threads=2, max_insert_threads=1, max_memory_usage={parse_size_bytes(args.max_memory_usage)}
"""
        )
        target_stats = rendered_table_stats(
            client,
            args.database,
            cutover_table,
            f"PREWHERE {predicate}",
        )
        if target_stats != source_stats:
            raise RuntimeError(
                f"cutover partition validation failed partition={partition_id} "
                f"source={source_stats} target={target_stats}"
            )
        print(
            f"cutover_repartition={partition_id + 1}/64 status=completed reused=false rows={source_stats[0]:,}",
            flush=True,
        )
    source_stats = rendered_table_stats_bounded(client, args.database, args.staging_table)
    target_stats = rendered_table_stats_bounded(client, args.database, cutover_table)
    if target_stats != source_stats:
        raise RuntimeError(f"cutover table validation failed source={source_stats} target={target_stats}")
    return cutover_table


def cleanup_temp_root(partition_root: Path, *, keep_source: bool) -> None:
    resolved = partition_root.resolve()
    if "sec_filing_text_rendered_v3_rebuild" not in str(resolved).lower():
        raise RuntimeError(f"refusing temporary cleanup outside rebuild root: {resolved}")
    for path in resolved.rglob("*"):
        if path.is_file() and (not keep_source or not path.name.startswith("source_")):
            path.unlink(missing_ok=True)
    for path in sorted((item for item in resolved.rglob("*") if item.is_dir()), reverse=True):
        try:
            path.rmdir()
        except OSError:
            pass


def load_or_create_run_manifest(
    run_root: Path,
    args: argparse.Namespace,
    run_id: str,
    loaded_env: list[Path],
    watermark: SourceWatermark,
    filing_watermark: FilingWatermark,
    partitions: list[dict[str, int]],
) -> SourceWatermark:
    manifest_path = run_root / "run_manifest.json"
    if manifest_path.exists():
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected_identity = {
            "run_id": run_id,
            "renderer_version": SEC_PACKED_TEXT_RENDERER_VERSION,
            "source_authority_version": SOURCE_AUTHORITY_VERSION,
            "database": args.database,
            "source_table": args.source_table,
            "target_table": args.target_table,
            "staging_table": args.staging_table,
        }
        actual_identity = {key: payload.get(key) for key in expected_identity}
        if actual_identity != expected_identity:
            raise RuntimeError(
                f"resume configuration differs from run manifest expected={expected_identity} actual={actual_identity}"
            )
        recorded_bundle_size = payload.get("row_groups_per_bundle")
        if recorded_bundle_size is None:
            payload["row_groups_per_bundle"] = args.row_groups_per_bundle
            manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        elif int(recorded_bundle_size) != args.row_groups_per_bundle:
            raise RuntimeError(
                "resume bundle size differs from run manifest "
                f"expected={recorded_bundle_size} actual={args.row_groups_per_bundle}"
            )
        if payload.get("max_concurrent_inserts") != args.max_concurrent_inserts:
            payload["max_concurrent_inserts"] = args.max_concurrent_inserts
            manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        if "filing_watermark" not in payload:
            raise RuntimeError(
                "this run predates the bounded filing-form lookup; start a new run without --run-id"
            )
        original = SourceWatermark(**payload["source_watermark"])
        if watermark != original:
            raise RuntimeError(
                f"source changed since run started; refusing resume original={asdict(original)} current={asdict(watermark)}"
            )
        original_filing = FilingWatermark(**payload["filing_watermark"])
        if filing_watermark != original_filing:
            raise RuntimeError(
                "filing metadata changed since run started; refusing resume "
                f"original={asdict(original_filing)} current={asdict(filing_watermark)}"
            )
        original_partitions = payload.get("partitions", [])
        if original_partitions != partitions:
            raise RuntimeError("source partition inventory changed since run started; refusing resume")
        return original

    payload = {
        "run_id": run_id,
        "renderer_version": SEC_PACKED_TEXT_RENDERER_VERSION,
        "source_authority_version": SOURCE_AUTHORITY_VERSION,
        "database": args.database,
        "source_table": args.source_table,
        "target_table": args.target_table,
        "staging_table": args.staging_table,
        "manifest_table": args.manifest_table,
        "workers": args.workers,
        "row_groups_per_bundle": args.row_groups_per_bundle,
        "max_concurrent_inserts": args.max_concurrent_inserts,
        "source_watermark": asdict(watermark),
        "filing_watermark": asdict(filing_watermark),
        "partitions": partitions,
        "loaded_env_files": [str(path) for path in loaded_env],
        "secret_status": secret_status(["CLICKHOUSE_PASSWORD", "SEC_CLICKHOUSE_PASSWORD", "QMD_CLICKHOUSE_PASSWORD"]),
        "created_at_utc": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return watermark


def write_results(run_root: Path, results: list[PartitionResult]) -> None:
    (run_root / "partition_results.json").write_text(
        json.dumps([asdict(result) for result in results], indent=2), encoding="utf-8"
    )


def print_header(
    args: argparse.Namespace,
    run_id: str,
    run_root: Path,
    watermark: SourceWatermark,
    partitions: list[dict[str, int]],
) -> None:
    print("=" * 96, flush=True)
    print("SEC rendered text v3 rebuild", flush=True)
    print(f"run_id={run_id} renderer={SEC_PACKED_TEXT_RENDERER_VERSION}", flush=True)
    print(f"source={args.database}.{args.source_table} target={args.database}.{args.target_table}", flush=True)
    print(
        f"staging={args.database}.{args.staging_table} workers={args.workers} "
        f"max_concurrent_inserts={args.max_concurrent_inserts} partitions={len(partitions)}",
        flush=True,
    )
    print(f"source_rows={watermark.rows:,} source_bytes={watermark.source_bytes:,}", flush=True)
    print(f"run_root={run_root}", flush=True)
    print("=" * 96, flush=True)


def print_validation(validation: dict[str, Any]) -> None:
    manifest = validation["manifest"]
    print("=" * 96, flush=True)
    print(f"validation={validation['status']} renderer={validation['renderer_version']}", flush=True)
    print(
        f"source={int(manifest['source_rows']):,} rendered={int(manifest['rendered_rows']):,} "
        f"excluded={int(manifest['excluded_rows']):,} rendered_chars={int(manifest['rendered_chars']):,}",
        flush=True,
    )
    print("=" * 96, flush=True)


def query_rows(client: ClickHouseHttpClient, sql: str) -> list[dict[str, Any]]:
    text = client.execute(sql.strip().rstrip(";") + " FORMAT JSONEachRow")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def query_one(client: ClickHouseHttpClient, sql: str) -> dict[str, Any]:
    rows = query_rows(client, sql)
    if len(rows) != 1:
        raise RuntimeError(f"expected one ClickHouse row, got {len(rows)}")
    return rows[0]


def scalar_int(client: ClickHouseHttpClient, sql: str) -> int:
    return int(client.execute(sql.strip().rstrip(";") + " FORMAT TSV").strip() or "0")


def table(database: str, name: str) -> str:
    return f"{quote_ident(database)}.{quote_ident(name)}"


def staging_table_for_run(run_id: str) -> str:
    token = re.sub(r"[^A-Za-z0-9]+", "_", run_id).strip("_")
    if not token:
        raise SystemExit("--run-id must contain at least one letter or number")
    return f"sec_filing_text_rendered_stage_{token}_v3"


if __name__ == "__main__":
    raise SystemExit(main())
