from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
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

from pipelines.market_sip.events.sec_packed_text_renderer import (  # noqa: E402
    SEC_PACKED_TEXT_RENDERER_VERSION,
    STRUCTURED_XML_EXCLUDED_QUALITY_FLAG,
    render_sec_packed_text,
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
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_filing_text_rendered_v3_rebuild")
DEFAULT_FILE_ROOT_WIN = Path("D:/market-data")
DEFAULT_FILE_ROOT_CH = "/mnt/d/market-data"

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
    "form_type",
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
    clickhouse_url: str
    clickhouse_user: str
    clickhouse_password: str
    file_root_win: str
    file_root_ch: str
    min_text_chars: int
    export_threads: int
    insert_threads: int
    max_memory_usage: int
    parquet_row_group_bytes: int
    parquet_file_bytes: int
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
    parser.add_argument("--run-id", default="")
    parser.add_argument("--output-root-win", default=str(DEFAULT_OUTPUT_ROOT_WIN))
    parser.add_argument("--file-root-win", default=str(DEFAULT_FILE_ROOT_WIN))
    parser.add_argument("--file-root-ch", default=DEFAULT_FILE_ROOT_CH)
    parser.add_argument("--workers", type=int, default=int(os.environ.get("SEC_RENDER_REBUILD_WORKERS", "4")))
    parser.add_argument("--export-threads", type=int, default=2)
    parser.add_argument("--insert-threads", type=int, default=2)
    parser.add_argument("--max-memory-usage", default=os.environ.get("SEC_RENDER_REBUILD_MAX_MEMORY", "32G"))
    parser.add_argument("--min-text-chars", type=int, default=1)
    parser.add_argument("--parquet-row-group-mib", type=int, default=128)
    parser.add_argument("--parquet-file-mib", type=int, default=1024)
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
    current_watermark = load_source_watermark(client, args)
    partitions = load_partitions(client, args)
    if args.limit_partitions:
        partitions = partitions[: args.limit_partitions]
    print_header(args, run_id, run_root, current_watermark, partitions)

    if not args.execute:
        print("dry_run=true; no ClickHouse tables or local run files were changed", flush=True)
        print(f"run with --execute --confirm-sec-gateway-stopped; add --cutover for validated atomic replacement", flush=True)
        return 0

    run_root.mkdir(parents=True, exist_ok=True)
    source_watermark = load_or_create_run_manifest(
        run_root, args, run_id, loaded_env, current_watermark, partitions
    )
    verify_clickhouse_file_root(client, args, run_root)
    ensure_manifest_table(client, args)
    ensure_staging_table(client, args)
    completed = load_completed_partitions(client, args, run_id)
    pending = [row for row in partitions if row["partition_id"] not in completed]
    print(f"resume completed={len(completed):,} pending={len(pending):,}", flush=True)

    jobs = [build_job(args, run_id, run_root, row) for row in pending]
    results = run_jobs(jobs, max_workers=args.workers, total_partitions=len(partitions), already_completed=len(completed))
    failures = [result for result in results if result.status != "ok"]
    if failures:
        write_results(run_root, results)
        raise RuntimeError(f"render rebuild failed partitions={[result.partition_id for result in failures]}")

    validation = validate_staging(client, args, run_id, partitions, source_watermark)
    (run_root / "validation.json").write_text(json.dumps(validation, indent=2, sort_keys=True), encoding="utf-8")
    write_results(run_root, results)
    print_validation(validation)

    if args.cutover:
        backup_table = cutover(client, args, run_id, source_watermark)
        print(f"cutover=complete target={args.database}.{args.target_table} backup={args.database}.{backup_table}", flush=True)
    else:
        print(f"cutover=pending staging={args.database}.{args.staging_table}", flush=True)
        print(f"resume validation and cutover with --run-id {run_id} --execute --cutover --confirm-sec-gateway-stopped", flush=True)
    return 0


def validate_args(args: argparse.Namespace) -> None:
    for label in ("database", "source_table", "target_table", "staging_table", "manifest_table"):
        value = str(getattr(args, label))
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            raise SystemExit(f"--{label.replace('_', '-')} must be a simple ClickHouse identifier: {value!r}")
    if args.workers < 1 or args.export_threads < 1 or args.insert_threads < 1:
        raise SystemExit("worker and thread counts must be positive")
    if args.min_text_chars < 1:
        raise SystemExit("--min-text-chars must be positive")
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
""",
    )
    return SourceWatermark(
        rows=int(row["rows"]),
        source_bytes=int(row["source_bytes"]),
        max_revision_rank=int(row["max_revision_rank"]),
        max_inserted_at=str(row["max_inserted_at"]),
        source_metadata_hash=int(row["source_metadata_hash"]),
    )


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
""",
        )
    ]


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
    if exists:
        return
    client.execute(
        f"CREATE TABLE {table(args.database, args.staging_table)} AS {table(args.database, args.target_table)}"
    )


def load_completed_partitions(client: ClickHouseHttpClient, args: argparse.Namespace, run_id: str) -> set[int]:
    text = client.execute(
        f"SELECT partition_id FROM {table(args.database, args.manifest_table)} FINAL "
        f"WHERE run_id={sql_string(run_id)} AND status='ok' FORMAT TSV"
    )
    return {int(line) for line in text.splitlines() if line.strip()}


def build_job(args: argparse.Namespace, run_id: str, run_root: Path, row: dict[str, int]) -> PartitionJob:
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
        clickhouse_url=args.clickhouse_url,
        clickhouse_user=args.user,
        clickhouse_password=args.password,
        file_root_win=args.file_root_win,
        file_root_ch=args.file_root_ch,
        min_text_chars=args.min_text_chars,
        export_threads=args.export_threads,
        insert_threads=args.insert_threads,
        max_memory_usage=parse_size_bytes(args.max_memory_usage),
        parquet_row_group_bytes=args.parquet_row_group_mib * 1024**2,
        parquet_file_bytes=args.parquet_file_mib * 1024**2,
        max_rows_per_partition=args.max_rows_per_partition,
        keep_temp_files=bool(args.keep_temp_files),
    )


def run_jobs(jobs: list[PartitionJob], *, max_workers: int, total_partitions: int, already_completed: int) -> list[PartitionResult]:
    if not jobs:
        return []
    results: list[PartitionResult] = []
    completed = already_completed
    started = time.perf_counter()
    with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_partition, job): job for job in jobs}
        try:
            for future in concurrent.futures.as_completed(futures):
                job = futures[future]
                try:
                    result = future.result()
                except Exception as exc:  # noqa: BLE001
                    result = PartitionResult(job.partition_id, 0, 0, 0, 0, 0, 0, 0.0, "error", repr(exc))
                results.append(result)
                completed += 1
                elapsed = time.perf_counter() - started
                rate = (completed - already_completed) / elapsed if elapsed > 0 else 0.0
                print(
                    f"partition={result.partition_id} status={result.status} overall={completed}/{total_partitions} "
                    f"source={result.source_rows:,} rendered={result.rendered_rows:,} excluded={result.excluded_rows:,} "
                    f"wall={result.wall_seconds:.1f}s rate={rate:.3f}_partitions/s",
                    flush=True,
                )
                if result.status != "ok":
                    for pending in futures:
                        pending.cancel()
                    raise RuntimeError(f"partition {result.partition_id} failed: {result.error}")
        except KeyboardInterrupt:
            for future in futures:
                future.cancel()
            raise
    return sorted(results, key=lambda item: item.partition_id)


def process_partition(job: PartitionJob) -> PartitionResult:
    started = time.perf_counter()
    client = ClickHouseHttpClient(job.clickhouse_url, job.clickhouse_user, job.clickhouse_password)
    partition_root = Path(job.run_root) / "partitions" / str(job.partition_id)
    source_path = partition_root / f"source_{job.partition_id}.parquet"
    output_root = partition_root / "rendered"
    partition_root.mkdir(parents=True, exist_ok=True)
    cleanup_partition_staging(client, job)
    cleanup_temp_root(partition_root, keep_source=False)

    export_source_partition(client, job, source_path)
    source_rows = int(pq.ParquetFile(source_path).metadata.num_rows)
    if not job.max_rows_per_partition and source_rows != job.expected_rows:
        raise RuntimeError(f"source export row mismatch expected={job.expected_rows} actual={source_rows}")

    writer = ParquetShardWriter(
        dataset_name="rendered_text_v3",
        target_table=job.staging_table,
        output_directory=output_root,
        filename_prefix=f"rendered_{job.partition_id}",
        columns=TARGET_COLUMNS,
        archive_index=job.partition_id,
        row_group_bytes=job.parquet_row_group_bytes,
        file_bytes=job.parquet_file_bytes,
        compression_level=1,
    )
    excluded_rows = 0
    source_chars = 0
    rendered_chars = 0
    extracted_at = datetime.now(UTC)
    try:
        parquet = pq.ParquetFile(source_path)
        for batch in parquet.iter_batches(batch_size=8):
            for source in batch.to_pylist():
                source_text = str(source.get("source_text") or "")
                source_chars += len(source_text)
                rendered = render_sec_packed_text(
                    source_text,
                    str(source.get("content_format") or ""),
                    document_name=str(source.get("document_name") or ""),
                    document_type=str(source.get("document_type") or ""),
                    form_type=str(source.get("form_type") or ""),
                    text_kind=str(source.get("text_kind") or ""),
                    include_intermediate=False,
                )
                text = rendered.packed_text
                if STRUCTURED_XML_EXCLUDED_QUALITY_FLAG in rendered.quality_flags:
                    excluded_rows += 1
                    continue
                if len(text) < job.min_text_chars:
                    raise RuntimeError(
                        "renderer produced an unexpectedly empty non-structured document "
                        f"document_id={source.get('document_id')} content_format={source.get('content_format')} "
                        f"source_chars={len(source_text)} quality_flags={rendered.quality_flags}"
                    )
                rendered_chars += len(text)
                writer.append(build_rendered_row(source, text, rendered.quality_flags, job.run_id, extracted_at))
        parts = writer.close()
    except Exception:
        writer.abort()
        raise

    rendered_rows = sum(int(part["rows"]) for part in parts)
    if rendered_rows + excluded_rows != source_rows:
        raise RuntimeError(
            f"partition accounting mismatch source={source_rows} rendered={rendered_rows} excluded={excluded_rows}"
        )
    for part in parts:
        insert_rendered_part(client, job, Path(part["path"]))

    result = PartitionResult(
        partition_id=job.partition_id,
        source_rows=source_rows,
        rendered_rows=rendered_rows,
        excluded_rows=excluded_rows,
        source_chars=source_chars,
        rendered_chars=rendered_chars,
        output_parts=len(parts),
        wall_seconds=round(time.perf_counter() - started, 3),
        status="ok",
    )
    insert_partition_manifest(client, job, result)
    if not job.keep_temp_files:
        cleanup_temp_root(partition_root, keep_source=False)
    return result


def cleanup_partition_staging(client: ClickHouseHttpClient, job: PartitionJob) -> None:
    client.execute(
        f"ALTER TABLE {table(job.database, job.staging_table)} DELETE WHERE "
        f"toYYYYMM(source_archive_date)={job.partition_id} SETTINGS mutations_sync=2"
    )


def export_source_partition(client: ClickHouseHttpClient, job: PartitionJob, source_path: Path) -> None:
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.unlink(missing_ok=True)
    clickhouse_path = windows_path_to_clickhouse_path(source_path, Path(job.file_root_win), job.file_root_ch)
    source_columns = ", ".join(f"s.{quote_ident(column)}" for column in SOURCE_COLUMNS if column != "form_type")
    limit_sql = f" LIMIT {job.max_rows_per_partition}" if job.max_rows_per_partition else ""
    client.execute(
        f"""
INSERT INTO TABLE FUNCTION file({sql_string(clickhouse_path)}, 'Parquet')
SELECT {source_columns}, ifNull(f.form_type, '') AS form_type
FROM {table(job.database, job.source_table)} AS s FINAL
LEFT ANY JOIN {table(job.database, 'sec_filing_v3')} AS f FINAL ON f.filing_id=s.filing_id
WHERE toYYYYMM(s.source_archive_date)={job.partition_id}
ORDER BY s.cik, s.accession_number, s.document_id, s.content_format
{limit_sql}
SETTINGS max_threads={job.export_threads}, max_memory_usage={job.max_memory_usage}, join_algorithm='grace_hash',
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


def insert_rendered_part(client: ClickHouseHttpClient, job: PartitionJob, path: Path) -> None:
    clickhouse_path = windows_path_to_clickhouse_path(path, Path(job.file_root_win), job.file_root_ch)
    columns = ", ".join(quote_ident(column) for column in TARGET_COLUMNS)
    client.execute(
        f"""
INSERT INTO {table(job.database, job.staging_table)} ({columns})
SELECT {columns}
FROM file({sql_string(clickhouse_path)}, 'Parquet')
SETTINGS max_threads={job.insert_threads}, max_memory_usage={job.max_memory_usage},
         input_format_parquet_use_native_reader_v3=1, input_format_parquet_verify_checksums=1
"""
    )


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
) -> dict[str, Any]:
    current_watermark = load_source_watermark(client, args)
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
    staging = query_one(
        client,
        f"""
SELECT count() AS rows, countIf(normalizer_version!={sql_string(SEC_PACKED_TEXT_RENDERER_VERSION)}) AS stale_rows,
       countIf(extraction_method!={sql_string(SEC_PACKED_TEXT_RENDERER_VERSION)}) AS stale_methods,
       countIf(text='' OR text_char_count=0) AS empty_rows,
       countIf(text_char_count!=lengthUTF8(text)) AS bad_char_counts,
       countIf(text_byte_count!=length(text)) AS bad_byte_counts,
       countIf(lower(hex(SHA256(text)))!=text_sha256) AS bad_hashes,
       uniqExact(tuple(cik,accession_number,document_id,text_kind)) AS unique_keys
FROM {table(args.database, args.staging_table)} FINAL
""",
    )
    expected_source_rows = sum(int(row["source_rows"]) for row in partitions)
    errors: list[str] = []
    if current_watermark != initial_watermark:
        errors.append(f"source watermark changed initial={asdict(initial_watermark)} current={asdict(current_watermark)}")
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
    if int(staging["unique_keys"]) != int(staging["rows"]):
        errors.append(f"duplicate logical keys rows={staging['rows']} unique={staging['unique_keys']}")
    validation = {
        "status": "ok" if not errors else "error",
        "renderer_version": SEC_PACKED_TEXT_RENDERER_VERSION,
        "initial_source_watermark": asdict(initial_watermark),
        "current_source_watermark": asdict(current_watermark),
        "manifest": manifest,
        "staging": staging,
        "errors": errors,
    }
    if errors:
        raise RuntimeError("staging validation failed: " + "; ".join(errors))
    return validation


def cutover(client: ClickHouseHttpClient, args: argparse.Namespace, run_id: str, watermark: SourceWatermark) -> str:
    if load_source_watermark(client, args) != watermark:
        raise RuntimeError("source watermark changed after validation; refusing cutover")
    suffix = re.sub(r"[^0-9]", "", run_id)[-14:] or datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    backup_table = f"sec_filing_text_rendered_pre_v8_{suffix}_v3"
    if scalar_int(
        client,
        f"SELECT count() FROM system.tables WHERE database={sql_string(args.database)} AND name={sql_string(backup_table)}",
    ):
        raise RuntimeError(f"backup table already exists: {args.database}.{backup_table}")
    client.execute(
        f"EXCHANGE TABLES {table(args.database, args.target_table)} AND {table(args.database, args.staging_table)}"
    )
    client.execute(
        f"RENAME TABLE {table(args.database, args.staging_table)} TO {table(args.database, backup_table)}"
    )
    return backup_table


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
    partitions: list[dict[str, int]],
) -> SourceWatermark:
    manifest_path = run_root / "run_manifest.json"
    if manifest_path.exists():
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected_identity = {
            "run_id": run_id,
            "renderer_version": SEC_PACKED_TEXT_RENDERER_VERSION,
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
        original = SourceWatermark(**payload["source_watermark"])
        if watermark != original:
            raise RuntimeError(
                f"source changed since run started; refusing resume original={asdict(original)} current={asdict(watermark)}"
            )
        original_partitions = payload.get("partitions", [])
        if original_partitions != partitions:
            raise RuntimeError("source partition inventory changed since run started; refusing resume")
        return original

    payload = {
        "run_id": run_id,
        "renderer_version": SEC_PACKED_TEXT_RENDERER_VERSION,
        "database": args.database,
        "source_table": args.source_table,
        "target_table": args.target_table,
        "staging_table": args.staging_table,
        "manifest_table": args.manifest_table,
        "workers": args.workers,
        "source_watermark": asdict(watermark),
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
    print(f"staging={args.database}.{args.staging_table} workers={args.workers} partitions={len(partitions)}", flush=True)
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
