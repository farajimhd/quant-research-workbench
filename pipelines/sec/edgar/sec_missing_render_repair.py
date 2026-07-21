from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import hashlib
import json
import multiprocessing
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

from pipelines.sec.edgar.sec_filing_text_clickhouse_file_ingest import windows_path_to_clickhouse_path  # noqa: E402
from pipelines.sec.edgar.sec_parquet_parts import ParquetShardWriter  # noqa: E402
from pipelines.sec.edgar.sec_pipeline.text_renderer import (  # noqa: E402
    SEC_PACKED_TEXT_RENDERER_VERSION,
    render_sec_packed_text,
)
from research.mlops.clickhouse import (  # noqa: E402
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    mergetree_settings_sql,
    parse_size_bytes,
    quote_ident,
    sql_string,
)
from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402


DEFAULT_DATABASE = "q_live"
DEFAULT_SOURCE_TABLE = "sec_filing_text_v3"
DEFAULT_RENDERED_TABLE = "sec_filing_text_rendered_v3"
DEFAULT_DOCUMENT_TABLE = "sec_filing_document_v3"
DEFAULT_SKIP_TABLE = "sec_filing_document_skip_v3"
DEFAULT_LIVE_MANIFEST_TABLE = "sec_filing_live_ingest_manifest_v3"
DEFAULT_CANDIDATE_TABLE = "sec_filing_text_render_candidate_v3"
DEFAULT_REPAIR_MANIFEST_TABLE = "sec_filing_text_render_repair_manifest_v3"
DEFAULT_OUTPUT_ROOT = Path("D:/market-data/prepared/sec_missing_render_repair")
DEFAULT_FILE_ROOT_WIN = Path("D:/market-data")
DEFAULT_FILE_ROOT_CH = "/mnt/d/market-data"
SUPPORTED_FORMATS = ("html", "plain_text", "xml")
LEGACY_EXCLUSION_REASON = "structured_xml_model_excluded"

SOURCE_EXPORT_COLUMNS = [
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
    "source_text_char_count",
    "source_archive_date",
    "source_archive_member",
    "source_version_key",
    "source_revision_at",
    "source_revision_rank",
    "source_revision_kind",
    "pac_event_id",
]
RENDERED_COLUMNS = [
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

_EXPORT_SEMAPHORE: Any | None = None
_INSERT_SEMAPHORE: Any | None = None


@dataclass(frozen=True, slots=True)
class SourceWatermark:
    rows: int
    source_bytes: int
    max_revision_rank: int
    metadata_hash: int


@dataclass(frozen=True, slots=True)
class RepairUnit:
    run_id: str
    database: str
    source_table: str
    rendered_table: str
    candidate_table: str
    manifest_table: str
    month: int
    bucket: int
    bucket_count: int
    expected_rows: int
    expected_chars: int
    output_root: str
    file_root_win: str
    file_root_ch: str
    clickhouse_url: str
    clickhouse_user: str
    clickhouse_password: str
    export_threads: int
    insert_threads: int
    max_memory_usage: int
    parquet_row_group_bytes: int
    parquet_file_bytes: int
    keep_temp_files: bool


@dataclass(frozen=True, slots=True)
class UnitResult:
    month: int
    bucket: int
    source_rows: int
    source_chars: int
    rendered_rows: int
    rendered_chars: int
    output_parts: int
    wall_seconds: float
    status: str
    error: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Discover authoritative SEC source-text rows missing their canonical rendered derivative and repair only "
            "those rows with bounded, resumable parallel workers. Dry-run is the default."
        )
    )
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--database", default=os.environ.get("SEC_CLICKHOUSE_DATABASE", DEFAULT_DATABASE))
    parser.add_argument("--source-table", default=DEFAULT_SOURCE_TABLE)
    parser.add_argument("--rendered-table", default=DEFAULT_RENDERED_TABLE)
    parser.add_argument("--document-table", default=DEFAULT_DOCUMENT_TABLE)
    parser.add_argument("--skip-table", default=DEFAULT_SKIP_TABLE)
    parser.add_argument("--live-manifest-table", default=DEFAULT_LIVE_MANIFEST_TABLE)
    parser.add_argument("--candidate-table", default=DEFAULT_CANDIDATE_TABLE)
    parser.add_argument("--repair-manifest-table", default=DEFAULT_REPAIR_MANIFEST_TABLE)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--output-root-win", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--file-root-win", default=str(DEFAULT_FILE_ROOT_WIN))
    parser.add_argument("--file-root-ch", default=DEFAULT_FILE_ROOT_CH)
    parser.add_argument("--workers", type=int, default=int(os.environ.get("SEC_MISSING_RENDER_WORKERS", "16")))
    parser.add_argument("--work-buckets", type=int, default=int(os.environ.get("SEC_MISSING_RENDER_BUCKETS", "8")))
    parser.add_argument("--max-concurrent-exports", type=int, default=4)
    parser.add_argument("--max-concurrent-inserts", type=int, default=2)
    parser.add_argument("--export-threads", type=int, default=2)
    parser.add_argument("--insert-threads", type=int, default=2)
    parser.add_argument("--max-memory-usage", default=os.environ.get("SEC_MISSING_RENDER_MAX_MEMORY", "24G"))
    parser.add_argument("--parquet-row-group-mib", type=int, default=128)
    parser.add_argument("--parquet-file-mib", type=int, default=1024)
    parser.add_argument("--limit-units", type=int, default=0, help="Testing only; process at most this many month/bucket units.")
    parser.add_argument("--keep-temp-files", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--confirm-sec-gateway-stopped",
        action="store_true",
        help="Required for execution so the candidate snapshot and source watermark remain stable.",
    )
    return parser.parse_args()


def main() -> int:
    loaded_env = load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    validate_args(args)
    run_id = args.run_id.strip() or f"sec_missing_render_repair_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    run_root = Path(args.output_root_win) / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    started = time.perf_counter()

    require_tables(client, args)
    before = load_missing_summary(client, args)
    if int(before["missing_rows"]) != int(before["missing_render_keys"]):
        raise RuntimeError(
            "multiple authoritative source rows map to the same rendered identity; "
            f"rows={before['missing_rows']} keys={before['missing_render_keys']}"
        )
    print_header(args, run_id, run_root, before)
    if not args.execute:
        write_summary(
            run_root / "summary.json",
            args,
            run_id,
            loaded_env,
            before,
            before,
            [],
            SourceWatermark(0, 0, 0, 0),
            SourceWatermark(0, 0, 0, 0),
            "dry_run",
            time.perf_counter() - started,
        )
        print(f"dry_run=true report={run_root / 'summary.json'}", flush=True)
        return 0

    ensure_operational_tables(client, args, infer_storage_policy(client, args.database, args.source_table))
    source_before = load_source_watermark(client, args)
    candidate_rows = candidate_count(client, args, run_id)
    resumed = candidate_rows > 0
    if not resumed:
        snapshot_candidates(client, args, run_id)
        candidate_rows = candidate_count(client, args, run_id)
    unresolved_candidates = candidate_missing_count(client, args, run_id)
    expected_current_missing = unresolved_candidates if resumed else candidate_rows
    if expected_current_missing != int(before["missing_rows"]):
        raise RuntimeError(
            f"candidate snapshot mismatch discovered={before['missing_rows']} "
            f"candidate_rows={candidate_rows} candidate_still_missing={unresolved_candidates}; "
            "the source changed after this run was snapshotted or the SEC gateway is still writing; use a new run id"
        )

    units = load_units(client, args, run_id)
    completed = load_completed_units(client, args, run_id)
    pending = [unit for unit in units if (unit.month, unit.bucket) not in completed]
    if args.limit_units:
        pending = pending[: args.limit_units]
    print(
        f"candidate_snapshot={candidate_rows:,} units={len(units):,} completed={len(completed):,} pending={len(pending):,}",
        flush=True,
    )
    results = run_units(args, run_id, run_root, pending, len(units), len(completed))
    failures = [result for result in results if result.status != "ok"]
    if failures:
        write_unit_results(run_root / "unit_results.jsonl", results)
        raise RuntimeError(f"missing-render repair failed units={[(r.month, r.bucket) for r in failures]}")
    if args.limit_units:
        write_unit_results(run_root / "unit_results.jsonl", results)
        print("limited_run=true; global reconciliation and final validation were intentionally skipped", flush=True)
        return 0

    source_after_workers = load_source_watermark(client, args)
    if source_after_workers != source_before:
        raise RuntimeError(f"source watermark changed during repair before={source_before} after={source_after_workers}")

    verify_candidate_renders(client, args, run_id, expected_rows=candidate_rows)
    reconcile_document_rows(client, args, run_id)
    stale_skips = cleanup_stale_skip_rows(client, args, run_id)
    reconcile_live_manifests(client, args, run_id)
    verify_reconciled_state(client, args, run_id)
    after = load_missing_summary(client, args)
    source_after = load_source_watermark(client, args)
    if source_after != source_before:
        raise RuntimeError(f"source watermark changed during finalization before={source_before} after={source_after}")
    if int(after["missing_rows"]) != 0:
        raise RuntimeError(f"missing rendered rows remain after repair: {after}")

    write_unit_results(run_root / "unit_results.jsonl", results)
    write_summary(
        run_root / "summary.json",
        args,
        run_id,
        loaded_env,
        before,
        {**after, "stale_skips_deleted": stale_skips},
        results,
        source_before,
        source_after,
        "ok",
        time.perf_counter() - started,
    )
    print(f"repair=complete rows={candidate_rows:,} report={run_root / 'summary.json'}", flush=True)
    return 0


def validate_args(args: argparse.Namespace) -> None:
    for name in (
        "database",
        "source_table",
        "rendered_table",
        "document_table",
        "skip_table",
        "live_manifest_table",
        "candidate_table",
        "repair_manifest_table",
    ):
        value = str(getattr(args, name))
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            raise SystemExit(f"--{name.replace('_', '-')} must be a simple ClickHouse identifier: {value!r}")
    numeric = (
        args.workers,
        args.work_buckets,
        args.max_concurrent_exports,
        args.max_concurrent_inserts,
        args.export_threads,
        args.insert_threads,
        args.parquet_row_group_mib,
        args.parquet_file_mib,
    )
    if any(value < 1 for value in numeric):
        raise SystemExit("worker, bucket, thread, concurrency, and Parquet size values must be positive")
    if args.max_concurrent_exports > args.workers or args.max_concurrent_inserts > args.workers:
        raise SystemExit("export/insert concurrency cannot exceed --workers")
    if args.parquet_file_mib < args.parquet_row_group_mib:
        raise SystemExit("--parquet-file-mib must be at least --parquet-row-group-mib")
    if args.execute and not args.confirm_sec_gateway_stopped:
        raise SystemExit("--execute requires --confirm-sec-gateway-stopped")
    output_root = Path(args.output_root_win).resolve()
    file_root = Path(args.file_root_win).resolve()
    try:
        output_root.relative_to(file_root)
    except ValueError as exc:
        raise SystemExit(f"--output-root-win must be under --file-root-win: {output_root}") from exc


def require_tables(client: ClickHouseHttpClient, args: argparse.Namespace) -> None:
    required = [args.source_table, args.rendered_table, args.document_table, args.skip_table]
    rows = int(
        client.execute(
            f"SELECT count() FROM system.tables WHERE database={sql_string(args.database)} "
            f"AND name IN ({','.join(sql_string(name) for name in required)})"
        ).strip()
        or "0"
    )
    if rows != len(required):
        raise RuntimeError(f"required SEC tables are missing from {args.database}: {required}")


def table(database: str, name: str) -> str:
    return f"{quote_ident(database)}.{quote_ident(name)}"


def missing_ctes_sql(args: argparse.Namespace) -> str:
    formats = ",".join(sql_string(value) for value in SUPPORTED_FORMATS)
    return f"""
source_rows AS
(
    SELECT cik, accession_number, document_id, text_kind, document_type, content_format,
           source_archive_date, source_text_char_count, source_version_key, source_revision_rank
    FROM {table(args.database, args.source_table)} FINAL
    WHERE content_format IN ({formats})
),
rendered_rows AS
(
    SELECT cik, accession_number, document_id, text_kind
    FROM {table(args.database, args.rendered_table)} FINAL
),
missing_rows AS
(
    SELECT s.*
    FROM source_rows AS s
    LEFT JOIN rendered_rows AS r USING (cik, accession_number, document_id, text_kind)
    WHERE r.document_id=''
)
"""


def audit_query_settings(args: argparse.Namespace) -> str:
    return (
        "SETTINGS join_algorithm='full_sorting_merge', max_threads="
        f"{max(1, int(args.export_threads))}, max_memory_usage={parse_size_bytes(args.max_memory_usage)}, "
        "max_bytes_before_external_sort=2147483648"
    )


def load_missing_summary(client: ClickHouseHttpClient, args: argparse.Namespace) -> dict[str, Any]:
    row = query_one(
        client,
        f"""
WITH {missing_ctes_sql(args)}
SELECT count() AS missing_rows,
       uniqExact(tuple(cik, accession_number, document_id, text_kind)) AS missing_render_keys,
       sum(source_text_char_count) AS missing_source_chars,
       uniqExact(accession_number) AS missing_accessions,
       min(source_archive_date) AS min_source_date,
       max(source_archive_date) AS max_source_date,
       countIf(content_format='xml') AS xml_rows
FROM missing_rows
{audit_query_settings(args)}
""",
    )
    return {
        "missing_rows": int(row.get("missing_rows") or 0),
        "missing_render_keys": int(row.get("missing_render_keys") or 0),
        "missing_source_chars": int(row.get("missing_source_chars") or 0),
        "missing_accessions": int(row.get("missing_accessions") or 0),
        "min_source_date": str(row.get("min_source_date") or ""),
        "max_source_date": str(row.get("max_source_date") or ""),
        "xml_rows": int(row.get("xml_rows") or 0),
    }


def ensure_operational_tables(client: ClickHouseHttpClient, args: argparse.Namespace, storage_policy: str) -> None:
    client.execute(
        f"""
CREATE TABLE IF NOT EXISTS {table(args.database, args.candidate_table)}
(
    run_id String,
    cik String,
    accession_number String,
    document_id String,
    text_kind LowCardinality(String),
    content_format LowCardinality(String),
    document_type LowCardinality(String),
    source_archive_date Date,
    source_month UInt32,
    work_bucket UInt16,
    source_text_char_count UInt64,
    source_version_key String,
    source_revision_rank UInt64,
    had_legacy_exclusion_skip UInt8,
    discovered_at_utc DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(discovered_at_utc)
PARTITION BY cityHash64(cik) % 64
ORDER BY (run_id, cik, accession_number, document_id, text_kind, content_format)
{mergetree_settings_sql(storage_policy)}
"""
    )
    client.execute(
        f"""
CREATE TABLE IF NOT EXISTS {table(args.database, args.repair_manifest_table)}
(
    run_id String,
    source_month UInt32,
    work_bucket UInt16,
    expected_rows UInt64,
    expected_source_chars UInt64,
    rendered_rows UInt64,
    rendered_chars UInt64,
    output_parts UInt32,
    status LowCardinality(String),
    error String,
    renderer_version LowCardinality(String),
    completed_at_utc DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(completed_at_utc)
ORDER BY (run_id, source_month, work_bucket)
{mergetree_settings_sql(storage_policy)}
"""
    )


def snapshot_candidates(client: ClickHouseHttpClient, args: argparse.Namespace, run_id: str) -> None:
    client.execute(
        f"""
INSERT INTO {table(args.database, args.candidate_table)}
WITH {missing_ctes_sql(args)}
SELECT {sql_string(run_id)} AS run_id,
       m.cik, m.accession_number, m.document_id, m.text_kind, m.content_format, m.document_type,
       m.source_archive_date, toUInt32(toYYYYMM(m.source_archive_date)) AS source_month,
       toUInt16(cityHash64(m.cik) % {int(args.work_buckets)}) AS work_bucket,
       m.source_text_char_count, m.source_version_key, m.source_revision_rank,
       toUInt8((m.cik, m.accession_number, m.document_id, m.source_version_key) IN
           (SELECT cik, accession_number, document_id, source_version_key
            FROM {table(args.database, args.skip_table)} FINAL
            WHERE skip_reason={sql_string(LEGACY_EXCLUSION_REASON)})) AS had_legacy_exclusion_skip,
       now64(3, 'UTC') AS discovered_at_utc
FROM missing_rows AS m
{audit_query_settings(args)}
"""
    )


def candidate_count(client: ClickHouseHttpClient, args: argparse.Namespace, run_id: str) -> int:
    return scalar_int(
        client,
        f"SELECT count() FROM {table(args.database, args.candidate_table)} FINAL "
        f"WHERE run_id={sql_string(run_id)}",
    )


def candidate_missing_count(client: ClickHouseHttpClient, args: argparse.Namespace, run_id: str) -> int:
    return scalar_int(
        client,
        f"""
WITH candidates AS
(
    SELECT cik, accession_number, document_id, text_kind
    FROM {table(args.database, args.candidate_table)} FINAL WHERE run_id={sql_string(run_id)}
), rendered AS
(
    SELECT cik, accession_number, document_id, text_kind
    FROM {table(args.database, args.rendered_table)} FINAL
)
SELECT count()
FROM candidates AS c LEFT JOIN rendered AS r USING (cik, accession_number, document_id, text_kind)
WHERE r.document_id=''
{audit_query_settings(args)}
""",
    )


def load_units(client: ClickHouseHttpClient, args: argparse.Namespace, run_id: str) -> list[RepairUnit]:
    rows = json_lines(
        client.execute(
            f"""
SELECT source_month, work_bucket, count() AS expected_rows,
       sum(source_text_char_count) AS expected_chars
FROM {table(args.database, args.candidate_table)} FINAL
WHERE run_id={sql_string(run_id)}
GROUP BY source_month, work_bucket
ORDER BY source_month, work_bucket
FORMAT JSONEachRow
"""
        )
    )
    return [
        RepairUnit(
            run_id=run_id,
            database=args.database,
            source_table=args.source_table,
            rendered_table=args.rendered_table,
            candidate_table=args.candidate_table,
            manifest_table=args.repair_manifest_table,
            month=int(row["source_month"]),
            bucket=int(row["work_bucket"]),
            bucket_count=int(args.work_buckets),
            expected_rows=int(row["expected_rows"]),
            expected_chars=int(row["expected_chars"]),
            output_root=str(Path(args.output_root_win) / run_id),
            file_root_win=args.file_root_win,
            file_root_ch=args.file_root_ch,
            clickhouse_url=args.clickhouse_url,
            clickhouse_user=args.user,
            clickhouse_password=args.password,
            export_threads=int(args.export_threads),
            insert_threads=int(args.insert_threads),
            max_memory_usage=parse_size_bytes(args.max_memory_usage),
            parquet_row_group_bytes=int(args.parquet_row_group_mib) * 1024**2,
            parquet_file_bytes=int(args.parquet_file_mib) * 1024**2,
            keep_temp_files=bool(args.keep_temp_files),
        )
        for row in rows
    ]


def load_completed_units(client: ClickHouseHttpClient, args: argparse.Namespace, run_id: str) -> set[tuple[int, int]]:
    text = client.execute(
        f"""
SELECT source_month, work_bucket
FROM {table(args.database, args.repair_manifest_table)} FINAL
WHERE run_id={sql_string(run_id)} AND status='ok'
  AND renderer_version={sql_string(SEC_PACKED_TEXT_RENDERER_VERSION)}
FORMAT TSV
"""
    )
    return {(int(line.split("\t")[0]), int(line.split("\t")[1])) for line in text.splitlines() if line.strip()}


def initialize_worker(export_semaphore: Any, insert_semaphore: Any) -> None:
    global _EXPORT_SEMAPHORE, _INSERT_SEMAPHORE
    _EXPORT_SEMAPHORE = export_semaphore
    _INSERT_SEMAPHORE = insert_semaphore


@contextlib.contextmanager
def semaphore_slot(semaphore: Any | None) -> Any:
    if semaphore is None:
        yield
        return
    semaphore.acquire()
    try:
        yield
    finally:
        semaphore.release()


def run_units(
    args: argparse.Namespace,
    run_id: str,
    run_root: Path,
    units: list[RepairUnit],
    total_units: int,
    already_completed: int,
) -> list[UnitResult]:
    if not units:
        return []
    stop_path = run_root / "STOP_REQUESTED.json"
    stop_path.unlink(missing_ok=True)
    ctx = multiprocessing.get_context()
    export_semaphore = ctx.BoundedSemaphore(args.max_concurrent_exports)
    insert_semaphore = ctx.BoundedSemaphore(args.max_concurrent_inserts)
    results: list[UnitResult] = []
    completed = already_completed
    started = time.perf_counter()
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=args.workers,
        initializer=initialize_worker,
        initargs=(export_semaphore, insert_semaphore),
    ) as executor:
        unit_iterator = iter(units)
        futures: dict[concurrent.futures.Future[UnitResult], RepairUnit] = {}
        for _ in range(min(args.workers, len(units))):
            unit = next(unit_iterator, None)
            if unit is not None:
                futures[executor.submit(process_unit, unit)] = unit
        failed = False
        while futures:
            done, _ = concurrent.futures.wait(futures, return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                unit = futures.pop(future)
                try:
                    result = future.result()
                except Exception as exc:  # noqa: BLE001
                    result = UnitResult(
                        unit.month, unit.bucket, 0, 0, 0, 0, 0, 0.0, "error", f"{type(exc).__name__}: {exc}"
                    )
                results.append(result)
                completed += 1
                elapsed = max(0.001, time.perf_counter() - started)
                print(
                    f"unit={unit.month}/{unit.bucket} status={result.status} overall={completed}/{total_units} "
                    f"rows={result.rendered_rows:,}/{unit.expected_rows:,} chars={result.rendered_chars:,} "
                    f"rate={(completed - already_completed) / elapsed:.3f}_units/s wall={result.wall_seconds:.1f}s"
                    + (f" error={result.error!r}" if result.error else ""),
                    flush=True,
                )
                if result.status != "ok":
                    failed = True
                    if not stop_path.exists():
                        stop_path.write_text(json.dumps(asdict(result), indent=2), encoding="utf-8")
                if not failed:
                    next_unit = next(unit_iterator, None)
                    if next_unit is not None:
                        futures[executor.submit(process_unit, next_unit)] = next_unit
            if failed:
                for pending in futures:
                    pending.cancel()
                break
    return sorted(results, key=lambda result: (result.month, result.bucket))


def process_unit(unit: RepairUnit) -> UnitResult:
    started = time.perf_counter()
    client = ClickHouseHttpClient(unit.clickhouse_url, unit.clickhouse_user, unit.clickhouse_password)
    unit_root = Path(unit.output_root) / "units" / f"{unit.month}_{unit.bucket:02d}"
    source_path = unit_root / "source.parquet"
    rendered_root = unit_root / "rendered"
    unit_root.mkdir(parents=True, exist_ok=True)
    try:
        if not valid_source_export(source_path, unit.expected_rows):
            source_path.unlink(missing_ok=True)
            with semaphore_slot(_EXPORT_SEMAPHORE):
                export_unit_source(client, unit, source_path)
        source_rows, source_chars, rendered_rows, rendered_chars, parts = render_unit(unit, source_path, rendered_root)
        if source_rows != unit.expected_rows or source_chars != unit.expected_chars or rendered_rows != source_rows:
            raise RuntimeError(
                f"unit accounting mismatch expected_rows={unit.expected_rows} source_rows={source_rows} "
                f"expected_chars={unit.expected_chars} source_chars={source_chars} rendered_rows={rendered_rows}"
            )
        with semaphore_slot(_INSERT_SEMAPHORE):
            for part_index, part in enumerate(parts, start=1):
                insert_rendered_part(client, unit, Path(part["path"]), part_index)
        verify_unit(client, unit)
        result = UnitResult(
            unit.month,
            unit.bucket,
            source_rows,
            source_chars,
            rendered_rows,
            rendered_chars,
            len(parts),
            round(time.perf_counter() - started, 3),
            "ok",
        )
        insert_unit_manifest(client, unit, result)
        if not unit.keep_temp_files:
            source_path.unlink(missing_ok=True)
            for part in parts:
                Path(part["path"]).unlink(missing_ok=True)
        return result
    except Exception as exc:  # noqa: BLE001
        result = UnitResult(
            unit.month,
            unit.bucket,
            0,
            0,
            0,
            0,
            0,
            round(time.perf_counter() - started, 3),
            "error",
            f"{type(exc).__name__}: {exc}",
        )
        with contextlib.suppress(Exception):
            insert_unit_manifest(client, unit, result)
        return result


def valid_source_export(path: Path, expected_rows: int) -> bool:
    if not path.is_file() or path.stat().st_size <= 0:
        return False
    try:
        parquet = pq.ParquetFile(path)
        try:
            return int(parquet.metadata.num_rows) == expected_rows and list(parquet.schema_arrow.names) == SOURCE_EXPORT_COLUMNS
        finally:
            parquet.close()
    except Exception:  # noqa: BLE001
        return False


def export_unit_source(client: ClickHouseHttpClient, unit: RepairUnit, source_path: Path) -> None:
    source_path.parent.mkdir(parents=True, exist_ok=True)
    clickhouse_path = windows_path_to_clickhouse_path(source_path, Path(unit.file_root_win), unit.file_root_ch)
    columns = ", ".join(quote_ident(column) for column in SOURCE_EXPORT_COLUMNS)
    key_subquery = f"""
SELECT cik, accession_number, document_id, content_format
FROM {table(unit.database, unit.candidate_table)} FINAL
WHERE run_id={sql_string(unit.run_id)} AND source_month={unit.month} AND work_bucket={unit.bucket}
"""
    client.execute(
        f"""
INSERT INTO TABLE FUNCTION file({sql_string(clickhouse_path)}, 'Parquet')
SELECT {columns}
FROM {table(unit.database, unit.source_table)} FINAL
PREWHERE toYYYYMM(source_archive_date)={unit.month}
  AND cityHash64(cik) % {unit.bucket_count}={unit.bucket}
  AND (cik, accession_number, document_id, content_format) IN ({key_subquery})
SETTINGS max_threads={unit.export_threads}, max_memory_usage={unit.max_memory_usage},
         max_block_size=8, preferred_block_size_bytes=16777216,
         output_format_parquet_batch_size=1,
         output_format_parquet_row_group_size=1024,
         output_format_parquet_row_group_size_bytes=268435456,
         output_format_parquet_parallel_encoding=0,
         output_format_parquet_compression_method='zstd',
         output_format_parquet_compliant_nested_types=1
"""
    )
    if not valid_source_export(source_path, unit.expected_rows):
        raise RuntimeError(f"invalid source export path={source_path} expected_rows={unit.expected_rows}")


def render_unit(unit: RepairUnit, source_path: Path, output_root: Path) -> tuple[int, int, int, int, list[dict[str, Any]]]:
    writer = ParquetShardWriter(
        dataset_name="sec_filing_text_rendered_v3",
        target_table=unit.rendered_table,
        output_directory=output_root,
        filename_prefix=f"rendered_{unit.month}_{unit.bucket:02d}",
        columns=RENDERED_COLUMNS,
        archive_index=unit.month * 100 + unit.bucket,
        row_group_bytes=unit.parquet_row_group_bytes,
        file_bytes=unit.parquet_file_bytes,
        compression_level=1,
    )
    source_rows = source_chars = rendered_rows = rendered_chars = 0
    parquet = pq.ParquetFile(source_path)
    try:
        for row_group in range(parquet.metadata.num_row_groups):
            rows = parquet.read_row_group(row_group, columns=SOURCE_EXPORT_COLUMNS).to_pylist()
            for source in rows:
                source_text = str(source.get("source_text") or "")
                rendered = render_sec_packed_text(
                    source_text,
                    str(source.get("content_format") or ""),
                    document_name=str(source.get("document_name") or ""),
                    document_type=str(source.get("document_type") or ""),
                    form_type=str(source.get("document_type") or ""),
                    text_kind=str(source.get("text_kind") or ""),
                    include_intermediate=False,
                )
                if not rendered.packed_text:
                    raise RuntimeError(
                        f"renderer returned empty text accession={source.get('accession_number')} "
                        f"document_id={source.get('document_id')} format={source.get('content_format')}"
                    )
                extracted_at = datetime.now(UTC)
                row = build_rendered_row(source, rendered.packed_text, rendered.quality_flags, unit.run_id, extracted_at)
                writer.append(row)
                source_rows += 1
                source_chars += int(source.get("source_text_char_count") or 0)
                rendered_rows += 1
                rendered_chars += len(rendered.packed_text)
    except Exception:
        writer.abort()
        raise
    finally:
        parquet.close()
    return source_rows, source_chars, rendered_rows, rendered_chars, writer.close()


def build_rendered_row(
    source: dict[str, Any], text: str, quality_flags: list[str], run_id: str, extracted_at: datetime
) -> dict[str, Any]:
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


def insert_rendered_part(client: ClickHouseHttpClient, unit: RepairUnit, path: Path, part_index: int) -> None:
    clickhouse_path = windows_path_to_clickhouse_path(path, Path(unit.file_root_win), unit.file_root_ch)
    columns = ", ".join(quote_ident(column) for column in RENDERED_COLUMNS)
    client.execute(
        f"""
INSERT INTO {table(unit.database, unit.rendered_table)} ({columns})
SELECT {columns} FROM file({sql_string(clickhouse_path)}, 'Parquet')
SETTINGS max_threads={unit.insert_threads}, max_insert_threads=1,
         max_memory_usage={unit.max_memory_usage}, input_format_parquet_use_native_reader_v3=1,
         input_format_parquet_verify_checksums=1,
         insert_deduplication_token={sql_string(f'{unit.run_id}:{unit.month}:{unit.bucket}:{part_index}')}
"""
    )


def unit_missing_sql(unit: RepairUnit) -> str:
    return f"""
WITH candidates AS
(
    SELECT cik, accession_number, document_id, text_kind, source_version_key, source_revision_rank
    FROM {table(unit.database, unit.candidate_table)} FINAL
    WHERE run_id={sql_string(unit.run_id)} AND source_month={unit.month} AND work_bucket={unit.bucket}
), rendered AS
(
    SELECT cik, accession_number, document_id, text_kind, source_version_key, source_revision_rank,
           normalizer_version
    FROM {table(unit.database, unit.rendered_table)} FINAL
    PREWHERE cityHash64(cik) % {unit.bucket_count}={unit.bucket}
)
SELECT count()
FROM candidates AS c
LEFT JOIN rendered AS r USING (cik, accession_number, document_id, text_kind)
WHERE r.document_id='' OR r.source_revision_rank != c.source_revision_rank
   OR r.source_version_key != c.source_version_key
   OR r.normalizer_version != {sql_string(SEC_PACKED_TEXT_RENDERER_VERSION)}
"""


def verify_unit(client: ClickHouseHttpClient, unit: RepairUnit) -> None:
    remaining = scalar_int(client, unit_missing_sql(unit))
    if remaining:
        raise RuntimeError(f"unit verification found {remaining} missing or stale rendered rows")


def insert_unit_manifest(client: ClickHouseHttpClient, unit: RepairUnit, result: UnitResult) -> None:
    payload = {
        "run_id": unit.run_id,
        "source_month": unit.month,
        "work_bucket": unit.bucket,
        "expected_rows": unit.expected_rows,
        "expected_source_chars": unit.expected_chars,
        "rendered_rows": result.rendered_rows,
        "rendered_chars": result.rendered_chars,
        "output_parts": result.output_parts,
        "status": result.status,
        "error": result.error[:4000],
        "renderer_version": SEC_PACKED_TEXT_RENDERER_VERSION,
        "completed_at_utc": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    }
    client.execute(
        f"INSERT INTO {table(unit.database, unit.manifest_table)} SETTINGS date_time_input_format='best_effort' "
        "FORMAT JSONEachRow\n" + json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    )


def verify_candidate_renders(client: ClickHouseHttpClient, args: argparse.Namespace, run_id: str, expected_rows: int) -> None:
    row = query_one(
        client,
        f"""
WITH candidates AS
(
    SELECT cik, accession_number, document_id, text_kind, source_version_key, source_revision_rank
    FROM {table(args.database, args.candidate_table)} FINAL WHERE run_id={sql_string(run_id)}
), rendered AS
(
    SELECT cik, accession_number, document_id, text_kind, source_version_key, source_revision_rank,
           normalizer_version
    FROM {table(args.database, args.rendered_table)} FINAL
)
SELECT count() AS candidate_rows,
       countIf(r.document_id='' OR r.source_revision_rank != c.source_revision_rank
           OR r.source_version_key != c.source_version_key
           OR r.normalizer_version != {sql_string(SEC_PACKED_TEXT_RENDERER_VERSION)}) AS invalid_rows
FROM candidates AS c LEFT JOIN rendered AS r USING (cik, accession_number, document_id, text_kind)
{audit_query_settings(args)}
""",
    )
    if int(row.get("candidate_rows") or 0) != expected_rows or int(row.get("invalid_rows") or 0):
        raise RuntimeError(f"global rendered verification failed expected={expected_rows} actual={row}")


def reconcile_document_rows(client: ClickHouseHttpClient, args: argparse.Namespace, run_id: str) -> None:
    db = table(args.database, args.document_table)
    keys = f"""
SELECT cik, accession_number, document_id, source_version_key
FROM {table(args.database, args.candidate_table)} FINAL WHERE run_id={sql_string(run_id)}
"""
    client.execute(
        f"""
ALTER TABLE {db} UPDATE
    has_normalized_text=toUInt8(1),
    extraction_status='text_extracted',
    extraction_error=CAST(NULL, 'Nullable(String)'),
    normalizer_version={sql_string(SEC_PACKED_TEXT_RENDERER_VERSION)},
    source_run_id={sql_string(run_id)}
WHERE (cik, accession_number, document_id, source_version_key) IN ({keys})
SETTINGS mutations_sync=2
"""
    )


def cleanup_stale_skip_rows(client: ClickHouseHttpClient, args: argparse.Namespace, run_id: str) -> int:
    skip_table = table(args.database, args.skip_table)
    keys = f"""
SELECT cik, accession_number, document_id, source_version_key
FROM {table(args.database, args.candidate_table)} FINAL
WHERE run_id={sql_string(run_id)} AND had_legacy_exclusion_skip=1
"""
    stale = scalar_int(
        client,
        f"SELECT count() FROM {skip_table} FINAL "
        f"PREWHERE (cik, accession_number, document_id, source_version_key) IN ({keys}) "
        f"WHERE skip_reason={sql_string(LEGACY_EXCLUSION_REASON)}",
    )
    if stale:
        client.execute(
            f"ALTER TABLE {skip_table} DELETE WHERE skip_reason={sql_string(LEGACY_EXCLUSION_REASON)} "
            f"AND (cik, accession_number, document_id, source_version_key) IN ({keys}) "
            "SETTINGS mutations_sync=2"
        )
    return stale


def reconcile_live_manifests(client: ClickHouseHttpClient, args: argparse.Namespace, run_id: str) -> None:
    if not table_exists(client, args.database, args.live_manifest_table):
        return
    manifest = table(args.database, args.live_manifest_table)
    candidates = table(args.database, args.candidate_table)
    client.execute(
        f"""
INSERT INTO {manifest}
WITH repaired AS
(
    SELECT accession_number, source_version_key, sum(had_legacy_exclusion_skip) AS reclassified_rows
    FROM {candidates} FINAL
    WHERE run_id={sql_string(run_id)} AND had_legacy_exclusion_skip=1
    GROUP BY accession_number, source_version_key
)
SELECT m.* REPLACE(
    {sql_string(SEC_PACKED_TEXT_RENDERER_VERSION)} AS renderer_version,
    m.expected_rendered_text_rows + repaired.reclassified_rows AS expected_rendered_text_rows,
    if(m.expected_skip_rows >= repaired.reclassified_rows,
       m.expected_skip_rows - repaired.reclassified_rows, toUInt64(0)) AS expected_skip_rows,
    {sql_string(run_id)} AS source_run_id,
    now64(9, 'UTC') AS updated_at_utc
)
FROM {manifest} AS m FINAL
INNER JOIN repaired USING (accession_number, source_version_key)
SETTINGS max_threads=2, max_memory_usage={parse_size_bytes(args.max_memory_usage)}
"""
    )


def verify_reconciled_state(client: ClickHouseHttpClient, args: argparse.Namespace, run_id: str) -> None:
    candidates = table(args.database, args.candidate_table)
    keys = (
        "SELECT cik, accession_number, document_id, source_version_key "
        f"FROM {candidates} FINAL WHERE run_id={sql_string(run_id)}"
    )
    stale_keys = (
        "SELECT cik, accession_number, document_id, source_version_key "
        f"FROM {candidates} FINAL WHERE run_id={sql_string(run_id)} AND had_legacy_exclusion_skip=1"
    )
    stale_skips = scalar_int(
        client,
        f"SELECT count() FROM {table(args.database, args.skip_table)} FINAL "
        f"PREWHERE (cik, accession_number, document_id, source_version_key) IN ({stale_keys}) "
        f"WHERE skip_reason={sql_string(LEGACY_EXCLUSION_REASON)}",
    )
    bad_documents = scalar_int(
        client,
        f"""
SELECT count()
FROM {table(args.database, args.document_table)} FINAL
PREWHERE (cik, accession_number, document_id, source_version_key) IN ({keys})
WHERE has_normalized_text=0 OR extraction_status!='text_extracted'
   OR normalizer_version!={sql_string(SEC_PACKED_TEXT_RENDERER_VERSION)}
""",
    )
    if stale_skips or bad_documents:
        raise RuntimeError(f"reconciliation verification failed stale_skips={stale_skips} bad_documents={bad_documents}")


def load_source_watermark(client: ClickHouseHttpClient, args: argparse.Namespace) -> SourceWatermark:
    row = query_one(
        client,
        f"""
SELECT count() AS rows, sum(source_text_byte_count) AS source_bytes,
       max(source_revision_rank) AS max_revision_rank,
       groupBitXor(cityHash64(cik, accession_number, document_id, content_format,
                              source_version_key, source_revision_rank, content_sha256)) AS metadata_hash
FROM {table(args.database, args.source_table)} FINAL
SETTINGS do_not_merge_across_partitions_select_final=0, max_threads=4,
         max_memory_usage={parse_size_bytes(args.max_memory_usage)}
""",
    )
    return SourceWatermark(
        int(row.get("rows") or 0),
        int(row.get("source_bytes") or 0),
        int(row.get("max_revision_rank") or 0),
        int(row.get("metadata_hash") or 0),
    )


def print_header(args: argparse.Namespace, run_id: str, run_root: Path, summary: dict[str, Any]) -> None:
    print("=" * 100, flush=True)
    print("SEC missing rendered-text repair", flush=True)
    print(f"run_id={run_id} mode={'EXECUTE' if args.execute else 'DRY-RUN'}", flush=True)
    print(f"source={args.database}.{args.source_table} target={args.database}.{args.rendered_table}", flush=True)
    print(
        f"missing_rows={summary['missing_rows']:,} accessions={summary['missing_accessions']:,} "
        f"source_chars={summary['missing_source_chars']:,} xml_rows={summary['xml_rows']:,} "
        f"range={summary['min_source_date']}..{summary['max_source_date']}",
        flush=True,
    )
    print(
        f"workers={args.workers} buckets={args.work_buckets} export_gate={args.max_concurrent_exports} "
        f"insert_gate={args.max_concurrent_inserts} output={run_root}",
        flush=True,
    )
    print("=" * 100, flush=True)


def write_unit_results(path: Path, results: list[UnitResult]) -> None:
    path.write_text("".join(json.dumps(asdict(result), sort_keys=True) + "\n" for result in results), encoding="utf-8")


def write_summary(
    path: Path,
    args: argparse.Namespace,
    run_id: str,
    loaded_env: list[Path],
    before: dict[str, Any],
    after: dict[str, Any],
    results: list[UnitResult],
    source_before: SourceWatermark,
    source_after: SourceWatermark,
    status: str,
    wall_seconds: float,
) -> None:
    payload = {
        "run_id": run_id,
        "status": status,
        "execute": bool(args.execute),
        "renderer_version": SEC_PACKED_TEXT_RENDERER_VERSION,
        "before": before,
        "after": after,
        "source_watermark_before": asdict(source_before),
        "source_watermark_after": asdict(source_after),
        "units": {"completed": sum(result.status == "ok" for result in results), "failed": sum(result.status != "ok" for result in results)},
        "wall_seconds": round(wall_seconds, 3),
        "loaded_env_files": [str(item) for item in loaded_env],
        "secret_status": secret_status(["SEC_CLICKHOUSE_URL", "SEC_CLICKHOUSE_USER", "SEC_CLICKHOUSE_PASSWORD"]),
        "created_at_utc": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def table_exists(client: ClickHouseHttpClient, database: str, table_name: str) -> bool:
    return bool(
        scalar_int(
            client,
            f"SELECT count() FROM system.tables WHERE database={sql_string(database)} AND name={sql_string(table_name)}",
        )
    )


def infer_storage_policy(client: ClickHouseHttpClient, database: str, table_name: str) -> str:
    rows = json_lines(
        client.execute(
            f"SELECT storage_policy FROM system.tables WHERE database={sql_string(database)} "
            f"AND name={sql_string(table_name)} FORMAT JSONEachRow"
        )
    )
    return str(rows[0].get("storage_policy") or "") if rows else ""


def scalar_int(client: ClickHouseHttpClient, sql: str) -> int:
    return int((client.execute(sql).strip() or "0").splitlines()[0])


def query_one(client: ClickHouseHttpClient, sql: str) -> dict[str, Any]:
    rows = json_lines(client.execute(sql.rstrip() + "\nFORMAT JSONEachRow"))
    if len(rows) != 1:
        raise RuntimeError(f"expected one ClickHouse row, received {len(rows)}")
    return rows[0]


def json_lines(text: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


if __name__ == "__main__":
    raise SystemExit(main())
