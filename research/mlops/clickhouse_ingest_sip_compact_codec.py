from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.env import load_env_files, secret_status  # noqa: E402
from research.mlops.clickhouse_compact_schema_codec_benchmark import (  # noqa: E402
    EVENT_DATE_TIMEZONE,
    event_date_expr,
    insert_quote_sql,
    insert_trade_sql,
    quote_table_sql,
    trade_table_sql,
)
from research.mlops.clickhouse_ingest_sip_flatfiles import (  # noqa: E402
    CLICKHOUSE_ENDPOINT_ENV,
    CLICKHOUSE_FILE_ROOT_ENV,
    CLICKHOUSE_HISTORICAL_STORAGE_POLICY_ENV,
    CLICKHOUSE_PASSWORD_ENV,
    CLICKHOUSE_PASSWORD_SIMPLE_ENV,
    CLICKHOUSE_STORAGE_POLICY_ENV,
    CLICKHOUSE_STORAGE_POLICY_SIMPLE_ENV,
    CLICKHOUSE_URL_ENV,
    CLICKHOUSE_USER_ENV,
    CLICKHOUSE_USER_SIMPLE_ENV,
    CLICKHOUSE_WORKSTATION_PASSWORD_ENV,
    CLICKHOUSE_WORKSTATION_USER_ENV,
    DEFAULT_FLATFILES_ROOT_WIN,
    DEFAULT_OUTPUT_ROOT_WIN,
    HISTORICAL_CLICKHOUSE_DATABASE_ENV,
    ClickHouseHttpClient,
    QueryProfile,
    default_clickhouse_file_root,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    default_preflight_processes,
    default_storage_policy,
    discover_clickhouse_env_files,
    discover_source_files,
    format_optional_int,
    normalize_clickhouse_file_path,
    parse_kinds,
    parse_size_bytes,
    quote_ident,
    run_profiled,
    sql_string,
)


DEFAULT_DATABASE = "market_sip_compact"
DEFAULT_MANIFEST_TABLE = "ingest_manifest"
DEFAULT_START_DATE = "2024-01-01"
DEFAULT_END_DATE = "2026-12-31"
DEFAULT_INSERT_CONCURRENCY = 12
DEFAULT_MAX_THREADS = 4
MANIFEST_WRITE_ATTEMPTS = 6
MANIFEST_WRITE_RETRY_SECONDS = 2.0
STATUS_PRIORITY_SQL = (
    "multiIf(status = 'should_delete', 100, status = 'failed', 90, status = 'ok', 80, "
    "status = 'started', 70, status = 'discovered', 60, 0)"
)

REQUIRED_QUOTE_COLUMN_TYPES = {
    "sip_timestamp_us": "UInt64",
    "participant_delta_us": "Int32",
    "sequence_number": "UInt32",
    "bid_price_int": "UInt32",
    "ask_price_int": "UInt32",
    "bid_size": "UInt32",
    "ask_size": "UInt32",
    "bid_exchange": "UInt8",
    "ask_exchange": "UInt8",
    "quote_flags": "UInt8",
    "issue_flags": "UInt16",
}
REQUIRED_TRADE_COLUMN_TYPES = {
    "sip_timestamp_us": "UInt64",
    "participant_delta_us": "Int32",
    "sequence_number": "UInt32",
    "price_int": "UInt32",
    "size": "Float32",
    "exchange": "UInt8",
    "trade_flags": "UInt8",
    "issue_flags": "UInt16",
}


@dataclass(frozen=True, slots=True)
class CompactIngestJob:
    kind: str
    date: str
    source_file: str
    source_path_win: str
    source_path_ch: str
    file_bytes: int
    table: str
    sql: str
    settings: str
    clickhouse_url: str
    user: str
    password: str


@dataclass(frozen=True, slots=True)
class CompactIngestResult:
    job: CompactIngestJob
    profile: QueryProfile


@dataclass(frozen=True, slots=True)
class RowStats:
    rows: int
    min_sip_timestamp: int
    max_sip_timestamp: int


@dataclass(frozen=True, slots=True)
class SourcePreflight:
    source_key: str
    stats: RowStats
    wall_seconds: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Production compact-codec ClickHouse ingest for Massive SIP quote/trade flatfiles."
    )
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--manifest-table", default=DEFAULT_MANIFEST_TABLE)
    parser.add_argument("--flatfiles-root-win", default=str(DEFAULT_FLATFILES_ROOT_WIN))
    parser.add_argument("--flatfiles-root-ch", default=default_clickhouse_file_root())
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--end-date", default=DEFAULT_END_DATE)
    parser.add_argument("--kinds", default="quotes,trades")
    parser.add_argument("--quote-table", default="quotes")
    parser.add_argument("--trade-table", default="trades")
    parser.add_argument("--storage-policy", default=default_storage_policy())
    parser.add_argument("--insert-concurrency", type=int, default=DEFAULT_INSERT_CONCURRENCY)
    parser.add_argument("--max-threads", type=int, default=DEFAULT_MAX_THREADS)
    parser.add_argument(
        "--preflight-processes",
        type=int,
        default=default_preflight_processes(),
        help="Worker processes for Polars streaming row/min/max preflight. Use 0 to skip.",
    )
    parser.add_argument("--max-memory-usage", default="400G")
    parser.add_argument("--output-root-win", default=str(DEFAULT_OUTPUT_ROOT_WIN / "compact_codec_ingest"))
    parser.add_argument("--limit-files", type=int, default=0)
    parser.add_argument(
        "--max-files-per-kind",
        type=int,
        default=0,
        help="Maximum files to ingest per source kind after date filtering. 0 means all.",
    )
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--retry-started", action="store_true")
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help=(
            "Keep submitting new files after an insert or manifest error. By default the script stops "
            "submitting new jobs after the first error and only drains jobs already in flight."
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def env_status_keys() -> list[str]:
    return [
        CLICKHOUSE_URL_ENV,
        CLICKHOUSE_WORKSTATION_USER_ENV,
        CLICKHOUSE_WORKSTATION_PASSWORD_ENV,
        CLICKHOUSE_USER_SIMPLE_ENV,
        CLICKHOUSE_PASSWORD_SIMPLE_ENV,
        HISTORICAL_CLICKHOUSE_DATABASE_ENV,
        CLICKHOUSE_HISTORICAL_STORAGE_POLICY_ENV,
        CLICKHOUSE_STORAGE_POLICY_SIMPLE_ENV,
        CLICKHOUSE_ENDPOINT_ENV,
        CLICKHOUSE_USER_ENV,
        CLICKHOUSE_PASSWORD_ENV,
        CLICKHOUSE_FILE_ROOT_ENV,
        "CLICKHOUSE_FLATFILES_ROOT",
        CLICKHOUSE_STORAGE_POLICY_ENV,
    ]


def query_settings(args: argparse.Namespace) -> str:
    settings = [
        "input_format_csv_empty_as_default = 1",
        "input_format_skip_unknown_fields = 1",
        "date_time_input_format = 'best_effort'",
    ]
    if args.max_threads > 0:
        settings.append(f"max_threads = {int(args.max_threads)}")
    if str(args.max_memory_usage) != "0":
        settings.append(f"max_memory_usage = {parse_size_bytes(str(args.max_memory_usage))}")
    return "\nSETTINGS " + ", ".join(settings)


def create_manifest_table_sql(database: str, manifest_table: str, storage_policy: str) -> str:
    db = quote_ident(database)
    table = quote_ident(manifest_table)
    settings = ["index_granularity = 8192"]
    if storage_policy.strip():
        settings.append(f"storage_policy = {sql_string(storage_policy.strip())}")
    return f"""
CREATE TABLE IF NOT EXISTS {db}.{table}
(
    kind LowCardinality(String),
    source_date Date,
    source_file String,
    source_path_ch String,
    file_bytes UInt64,
    target_table LowCardinality(String),
    expected_rows UInt64,
    expected_min_sip_timestamp UInt64,
    expected_max_sip_timestamp UInt64,
    actual_rows UInt64,
    status LowCardinality(String),
    run_id String,
    query_id String,
    wall_seconds Float64,
    query_duration_ms UInt64,
    memory_usage_bytes UInt64,
    read_rows UInt64,
    read_bytes UInt64,
    written_rows UInt64,
    written_bytes UInt64,
    exception String,
    audit_status LowCardinality(String) DEFAULT '',
    audit_run_id String DEFAULT '',
    audit_actual_rows UInt64 DEFAULT 0,
    audit_note String DEFAULT '',
    audit_checked_at DateTime DEFAULT toDateTime(0),
    updated_at DateTime DEFAULT now()
)
ENGINE = MergeTree
ORDER BY (kind, source_date, source_file, updated_at)
SETTINGS {", ".join(settings)}
"""


def create_database_and_tables(client: ClickHouseHttpClient, args: argparse.Namespace) -> None:
    client.execute(f"CREATE DATABASE IF NOT EXISTS {quote_ident(args.database)}")
    client.execute(quote_table_sql(args.database, args.quote_table, codecs=True, storage_policy=args.storage_policy))
    client.execute(trade_table_sql(args.database, args.trade_table, codecs=True, storage_policy=args.storage_policy))
    client.execute(create_manifest_table_sql(args.database, args.manifest_table, args.storage_policy))
    ensure_manifest_columns(client, args.database, args.manifest_table)
    validate_target_schema(client, args.database, args.quote_table, REQUIRED_QUOTE_COLUMN_TYPES)
    validate_target_schema(client, args.database, args.trade_table, REQUIRED_TRADE_COLUMN_TYPES)
    validate_event_date_expression(client, args.database, args.quote_table)
    validate_event_date_expression(client, args.database, args.trade_table)


def ensure_manifest_columns(client: ClickHouseHttpClient, database: str, manifest_table: str) -> None:
    table = f"{quote_ident(database)}.{quote_ident(manifest_table)}"
    columns = [
        ("expected_rows", "UInt64"),
        ("expected_min_sip_timestamp", "UInt64"),
        ("expected_max_sip_timestamp", "UInt64"),
        ("actual_rows", "UInt64"),
        ("audit_status", "LowCardinality(String) DEFAULT ''"),
        ("audit_run_id", "String DEFAULT ''"),
        ("audit_actual_rows", "UInt64 DEFAULT 0"),
        ("audit_note", "String DEFAULT ''"),
        ("audit_checked_at", "DateTime DEFAULT toDateTime(0)"),
    ]
    for name, dtype in columns:
        client.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {quote_ident(name)} {dtype}")


def validate_target_schema(client: ClickHouseHttpClient, database: str, table: str, required_types: dict[str, str]) -> None:
    rows = client.query_tsv(
        "SELECT name, type "
        "FROM system.columns "
        f"WHERE database = {sql_string(database)} AND table = {sql_string(table)}"
    ).strip().splitlines()
    actual = {}
    for row in rows:
        parts = row.split("\t")
        if len(parts) >= 2:
            actual[parts[0]] = parts[1]
    problems = []
    for column, expected_type in required_types.items():
        actual_type = actual.get(column)
        if actual_type is None:
            problems.append(f"{column}: missing, expected {expected_type}")
        elif actual_type != expected_type:
            problems.append(f"{column}: expected {expected_type}, found {actual_type}")
    if problems:
        formatted = "\n  ".join(problems)
        raise RuntimeError(
            f"Target table {database}.{table} does not match the validated compact schema.\n"
            f"  {formatted}\n"
            "Use a fresh table/database or migrate/drop the stale table before ingesting."
        )
    print(f"SCHEMA OK {database}.{table}", flush=True)


def validate_event_date_expression(client: ClickHouseHttpClient, database: str, table: str) -> None:
    rows = client.query_tsv(
        "SELECT default_kind, default_expression "
        "FROM system.columns "
        f"WHERE database = {sql_string(database)} "
        f"AND table = {sql_string(table)} "
        "AND name = 'event_date'"
    ).strip().splitlines()
    if not rows:
        raise RuntimeError(f"Target table {database}.{table} is missing materialized event_date.")
    parts = rows[0].split("\t", 1)
    default_kind = parts[0] if parts else ""
    default_expression = (parts[1] if len(parts) > 1 else "").replace("\\'", "'")
    if default_kind != "MATERIALIZED" or "toTimeZone" not in default_expression or f"'{EVENT_DATE_TIMEZONE}'" not in default_expression:
        raise RuntimeError(
            f"Target table {database}.{table} has stale event_date expression: {default_expression!r}.\n"
            f"Expected UTC materialization: {event_date_expr()}.\n"
            "Run research/mlops/clickhouse_fix_compact_event_date.py to rebuild/swap the existing compact tables "
            "before ingesting more data into this database."
        )
    print(f"EVENT_DATE OK {database}.{table} timezone={EVENT_DATE_TIMEZONE}", flush=True)


def latest_manifest_status(
    client: ClickHouseHttpClient,
    database: str,
    manifest_table: str,
    kind: str,
    date: str,
    source_file: str,
    target_table: str,
) -> str:
    try:
        rows = client.query_tsv(
            "SELECT status, audit_status FROM "
            f"{quote_ident(database)}.{quote_ident(manifest_table)} "
            f"WHERE kind = {sql_string(kind)} "
            f"AND source_date = toDate({sql_string(date)}) "
            f"AND source_file = {sql_string(source_file)} "
            f"AND target_table = {sql_string(target_table)} "
            "ORDER BY updated_at DESC, "
            f"{STATUS_PRIORITY_SQL} DESC "
            "LIMIT 1"
        ).strip().splitlines()
    except Exception:
        return ""
    if not rows:
        return ""
    parts = rows[0].split("\t")
    status = parts[0]
    audit_status = parts[1] if len(parts) > 1 else ""
    if audit_status in {"should_delete", "orphan_should_delete"}:
        return f"audit_{audit_status}"
    return status


def latest_manifest_statuses(
    client: ClickHouseHttpClient,
    database: str,
    manifest_table: str,
    jobs: list[CompactIngestJob],
) -> dict[tuple[str, str, str, str], str]:
    if not jobs:
        return {}
    kinds = sorted({job.kind for job in jobs})
    target_tables = sorted({job.table for job in jobs})
    start_date = min(job.date for job in jobs)
    end_date = max(job.date for job in jobs)
    query = (
        "SELECT "
        "kind, toString(source_date) AS source_date_text, source_file, target_table, "
        f"argMax(status, tuple(updated_at, {STATUS_PRIORITY_SQL})) AS status, "
        f"argMax(audit_status, tuple(updated_at, {STATUS_PRIORITY_SQL})) AS audit_status "
        f"FROM {quote_ident(database)}.{quote_ident(manifest_table)} "
        f"WHERE source_date BETWEEN toDate({sql_string(start_date)}) AND toDate({sql_string(end_date)}) "
        f"AND kind IN ({', '.join(sql_string(kind) for kind in kinds)}) "
        f"AND target_table IN ({', '.join(sql_string(table) for table in target_tables)}) "
        "GROUP BY kind, source_date, source_file, target_table"
    )
    try:
        rows = client.query_tsv(query).strip().splitlines()
    except Exception as exc:  # noqa: BLE001
        print(f"WARN bulk manifest status query failed; falling back to per-file checks: {exc!r}", flush=True)
        statuses = {}
        for job in jobs:
            statuses[(job.kind, job.date, job.source_file, job.table)] = latest_manifest_status(
                client,
                database,
                manifest_table,
                job.kind,
                job.date,
                job.source_file,
                job.table,
            )
        return statuses
    statuses: dict[tuple[str, str, str, str], str] = {}
    for row in rows:
        parts = row.split("\t")
        if len(parts) < 5:
            continue
        kind, source_date_text, source_file, target_table, status = parts[:5]
        audit_status = parts[5] if len(parts) > 5 else ""
        if audit_status in {"should_delete", "orphan_should_delete"}:
            status = f"audit_{audit_status}"
        statuses[(kind, source_date_text, source_file, target_table)] = status
    return statuses


def is_transient_clickhouse_write_error(exc: Exception) -> bool:
    text = repr(exc)
    transient_markers = (
        "ConnectionResetError",
        "ConnectionAbortedError",
        "RemoteDisconnected",
        "TimeoutError",
        "temporarily unavailable",
        "Connection reset",
        "forcibly closed by the remote host",
    )
    return any(marker in text for marker in transient_markers)


def execute_manifest_write(client: ClickHouseHttpClient, sql: str, *, label: str) -> None:
    for attempt in range(1, MANIFEST_WRITE_ATTEMPTS + 1):
        try:
            client.execute(sql)
            return
        except Exception as exc:  # noqa: BLE001
            if attempt >= MANIFEST_WRITE_ATTEMPTS or not is_transient_clickhouse_write_error(exc):
                raise
            sleep_seconds = MANIFEST_WRITE_RETRY_SECONDS * attempt
            print(
                f"WARN manifest write failed label={label} attempt={attempt}/{MANIFEST_WRITE_ATTEMPTS}; "
                f"retrying in {sleep_seconds:.1f}s: {exc!r}",
                flush=True,
            )
            time.sleep(sleep_seconds)


def insert_manifest(
    client: ClickHouseHttpClient,
    database: str,
    manifest_table: str,
    job: CompactIngestJob,
    *,
    status: str,
    run_id: str,
    profile: QueryProfile | None = None,
    expected_stats: RowStats | None = None,
    exception: str = "",
) -> None:
    profile = profile or QueryProfile(label="", query_id="", wall_seconds=0.0)
    expected_stats = expected_stats or RowStats(rows=0, min_sip_timestamp=0, max_sip_timestamp=0)
    actual_rows = int(profile.written_rows or 0)
    sql = f"""
INSERT INTO {quote_ident(database)}.{quote_ident(manifest_table)}
(
    kind, source_date, source_file, source_path_ch, file_bytes, target_table,
    expected_rows, expected_min_sip_timestamp, expected_max_sip_timestamp, actual_rows,
    status, run_id, query_id, wall_seconds, query_duration_ms, memory_usage_bytes,
    read_rows, read_bytes, written_rows, written_bytes, exception
)
VALUES
(
    {sql_string(job.kind)},
    toDate({sql_string(job.date)}),
    {sql_string(job.source_file)},
    {sql_string(job.source_path_ch)},
    {int(job.file_bytes)},
    {sql_string(job.table)},
    {int(expected_stats.rows)},
    {int(expected_stats.min_sip_timestamp)},
    {int(expected_stats.max_sip_timestamp)},
    {actual_rows},
    {sql_string(status)},
    {sql_string(run_id)},
    {sql_string(profile.query_id or "")},
    {float(profile.wall_seconds or 0.0)},
    {int(profile.query_duration_ms or 0)},
    {int(profile.memory_usage_bytes or 0)},
    {int(profile.read_rows or 0)},
    {int(profile.read_bytes or 0)},
    {int(profile.written_rows or 0)},
    {int(profile.written_bytes or 0)},
    {sql_string(exception or profile.exception or "")}
)
"""
    execute_manifest_write(client, sql, label=f"{status}:{job.kind}:{job.date}")


def manifest_values_sql(
    job: CompactIngestJob,
    *,
    status: str,
    run_id: str,
    profile: QueryProfile | None = None,
    expected_stats: RowStats | None = None,
    exception: str = "",
) -> str:
    profile = profile or QueryProfile(label="", query_id="", wall_seconds=0.0)
    expected_stats = expected_stats or RowStats(rows=0, min_sip_timestamp=0, max_sip_timestamp=0)
    actual_rows = int(profile.written_rows or 0)
    return (
        "("
        f"{sql_string(job.kind)}, "
        f"toDate({sql_string(job.date)}), "
        f"{sql_string(job.source_file)}, "
        f"{sql_string(job.source_path_ch)}, "
        f"{int(job.file_bytes)}, "
        f"{sql_string(job.table)}, "
        f"{int(expected_stats.rows)}, "
        f"{int(expected_stats.min_sip_timestamp)}, "
        f"{int(expected_stats.max_sip_timestamp)}, "
        f"{actual_rows}, "
        f"{sql_string(status)}, "
        f"{sql_string(run_id)}, "
        f"{sql_string(profile.query_id or '')}, "
        f"{float(profile.wall_seconds or 0.0)}, "
        f"{int(profile.query_duration_ms or 0)}, "
        f"{int(profile.memory_usage_bytes or 0)}, "
        f"{int(profile.read_rows or 0)}, "
        f"{int(profile.read_bytes or 0)}, "
        f"{int(profile.written_rows or 0)}, "
        f"{int(profile.written_bytes or 0)}, "
        f"{sql_string(exception or profile.exception or '')}"
        ")"
    )


def insert_manifest_many(
    client: ClickHouseHttpClient,
    database: str,
    manifest_table: str,
    entries: list[tuple[CompactIngestJob, str, RowStats]],
    *,
    run_id: str,
    batch_size: int = 1000,
) -> None:
    if not entries:
        return
    columns = """
(
    kind, source_date, source_file, source_path_ch, file_bytes, target_table,
    expected_rows, expected_min_sip_timestamp, expected_max_sip_timestamp, actual_rows,
    status, run_id, query_id, wall_seconds, query_duration_ms, memory_usage_bytes,
    read_rows, read_bytes, written_rows, written_bytes, exception
)
""".strip()
    total = len(entries)
    started_at = time.perf_counter()
    for offset in range(0, total, batch_size):
        chunk = entries[offset : offset + batch_size]
        values = ",\n".join(
            manifest_values_sql(job, status=status, run_id=run_id, expected_stats=expected_stats)
            for job, status, expected_stats in chunk
        )
        sql = f"INSERT INTO {quote_ident(database)}.{quote_ident(manifest_table)}\n{columns}\nVALUES\n{values}"
        execute_manifest_write(
            client,
            sql,
            label=f"bulk:{min(offset + len(chunk), total)}/{total}",
        )
        print(
            f"BULK MANIFEST inserted={min(offset + len(chunk), total):,}/{total:,} "
            f"statuses={sorted({status for _, status, _ in chunk})}",
            flush=True,
        )
    print(f"DONE bulk manifest rows={total:,} elapsed_seconds={time.perf_counter() - started_at:.2f}", flush=True)


def run_insert_job(job: CompactIngestJob) -> CompactIngestResult:
    client = ClickHouseHttpClient(job.clickhouse_url, job.user, job.password)
    profile = run_profiled(client, f"compact_insert_{job.kind}_{job.date}", job.sql, job.settings)
    return CompactIngestResult(job=job, profile=profile)


def print_profile(profile: QueryProfile) -> None:
    memory_gib = None if profile.memory_usage_bytes is None else profile.memory_usage_bytes / (1024**3)
    rows_per_second = None
    if profile.written_rows and profile.wall_seconds > 0:
        rows_per_second = round(profile.written_rows / profile.wall_seconds)
    print(
        f"{profile.label}: wall={profile.wall_seconds:.2f}s query_ms={profile.query_duration_ms} "
        f"memory_gib={None if memory_gib is None else round(memory_gib, 3)} "
        f"read_rows={format_optional_int(profile.read_rows)} written_rows={format_optional_int(profile.written_rows)} "
        f"rows_per_sec={format_optional_int(rows_per_second)}",
        flush=True,
    )


def validate_insert_profile_rows(profile: QueryProfile, expected_stats: RowStats) -> None:
    if expected_stats.rows <= 0:
        return
    mismatches = []
    if profile.read_rows is not None and int(profile.read_rows) != expected_stats.rows:
        mismatches.append(f"read_rows={profile.read_rows:,} expected_rows={expected_stats.rows:,}")
    if profile.written_rows is not None and int(profile.written_rows) != expected_stats.rows:
        mismatches.append(f"written_rows={profile.written_rows:,} expected_rows={expected_stats.rows:,}")
    if profile.read_rows is None and profile.written_rows is None:
        print(
            f"WARN {profile.label} has no query_log row counts; cannot compare against expected_rows={expected_stats.rows:,}",
            flush=True,
        )
        return
    if mismatches:
        raise RuntimeError(f"{profile.label} row-count validation failed: {', '.join(mismatches)}")


def build_jobs(args: argparse.Namespace, settings: str) -> list[CompactIngestJob]:
    kinds = parse_kinds(args.kinds)
    root_win = Path(args.flatfiles_root_win)
    root_ch = normalize_clickhouse_file_path(args.flatfiles_root_ch)
    sources = discover_source_files(root_win, root_ch, kinds, args.start_date, args.end_date)
    if args.max_files_per_kind > 0:
        limited_sources = []
        counts_by_kind: dict[str, int] = {}
        for source in sources:
            count = counts_by_kind.get(source.kind, 0)
            if count >= args.max_files_per_kind:
                continue
            limited_sources.append(source)
            counts_by_kind[source.kind] = count + 1
        sources = limited_sources
    if args.limit_files > 0:
        sources = sources[: args.limit_files]
    jobs: list[CompactIngestJob] = []
    for source in sources:
        table = args.quote_table if source.kind == "quotes" else args.trade_table
        sql = (
            insert_quote_sql(args.database, table, source.clickhouse_path)
            if source.kind == "quotes"
            else insert_trade_sql(args.database, table, source.clickhouse_path)
        )
        jobs.append(
            CompactIngestJob(
                kind=source.kind,
                date=source.date,
                source_file=source.windows_path.name,
                source_path_win=str(source.windows_path),
                source_path_ch=source.clickhouse_path,
                file_bytes=source.bytes,
                table=table,
                sql=sql,
                settings=settings,
                clickhouse_url=args.clickhouse_url,
                user=args.user,
                password=args.password,
            )
        )
    return jobs


def source_identity(job: CompactIngestJob) -> str:
    return f"{job.kind}|{job.date}|{job.source_file}|{job.table}"


def preflight_jobs(jobs: list[CompactIngestJob], processes: int) -> dict[str, SourcePreflight]:
    if processes <= 0:
        print("SKIP source preflight because preflight_processes=0", flush=True)
        return {source_identity(job): SourcePreflight(source_identity(job), RowStats(0, 0, 0), 0.0) for job in jobs}
    print("=" * 96, flush=True)
    print(f"START source preflight files={len(jobs):,} processes={processes}", flush=True)
    started_at = time.perf_counter()
    payloads = [(source_identity(job), job.source_path_win) for job in jobs]
    results: dict[str, SourcePreflight] = {}
    if processes <= 1:
        for index, payload in enumerate(payloads, start=1):
            result = preflight_source_worker(payload)
            results[result.source_key] = result
            print_preflight_progress(index, len(payloads), result, started_at)
    else:
        with concurrent.futures.ProcessPoolExecutor(max_workers=processes) as executor:
            future_to_payload = {executor.submit(preflight_source_worker, payload): payload for payload in payloads}
            for index, future in enumerate(concurrent.futures.as_completed(future_to_payload), start=1):
                result = future.result()
                results[result.source_key] = result
                print_preflight_progress(index, len(payloads), result, started_at)
    elapsed = time.perf_counter() - started_at
    print(f"DONE source preflight files={len(results):,} elapsed_seconds={elapsed:.1f}", flush=True)
    print("=" * 96, flush=True)
    return results


def preflight_source_worker(payload: tuple[str, str]) -> SourcePreflight:
    source_key, path_text = payload
    started_at = time.perf_counter()
    import polars as pl

    lazy = pl.scan_csv(
        path_text,
        has_header=True,
        schema_overrides={"sip_timestamp": pl.UInt64},
        ignore_errors=True,
    ).select(
        pl.len().alias("rows"),
        pl.col("sip_timestamp").cast(pl.UInt64, strict=False).min().fill_null(0).alias("min_sip_timestamp"),
        pl.col("sip_timestamp").cast(pl.UInt64, strict=False).max().fill_null(0).alias("max_sip_timestamp"),
    )
    frame = collect_polars_lazy(lazy)
    row = frame.row(0, named=True)
    return SourcePreflight(
        source_key=source_key,
        stats=RowStats(
            rows=int(row["rows"] or 0),
            min_sip_timestamp=int(row["min_sip_timestamp"] or 0),
            max_sip_timestamp=int(row["max_sip_timestamp"] or 0),
        ),
        wall_seconds=time.perf_counter() - started_at,
    )


def collect_polars_lazy(lazy: Any) -> Any:
    try:
        return lazy.collect(engine="streaming")
    except (TypeError, ValueError):
        return lazy.collect(streaming=True)


def print_preflight_progress(index: int, total: int, result: SourcePreflight, started_at: float) -> None:
    elapsed = time.perf_counter() - started_at
    rate = index / elapsed if elapsed > 0 else 0.0
    remaining = total - index
    eta_seconds = remaining / rate if rate > 0 else 0.0
    print(
        f"PREFLIGHT [{index:,}/{total:,}] {result.source_key} rows={result.stats.rows:,} "
        f"min={result.stats.min_sip_timestamp} max={result.stats.max_sip_timestamp} "
        f"file_seconds={result.wall_seconds:.1f} elapsed_min={elapsed / 60:.1f} eta_min={eta_seconds / 60:.1f}",
        flush=True,
    )


def append_jsonl(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def job_public(job: CompactIngestJob) -> dict[str, object]:
    return {
        "kind": job.kind,
        "date": job.date,
        "source_file": job.source_file,
        "source_path_win": job.source_path_win,
        "source_path_ch": job.source_path_ch,
        "file_bytes": job.file_bytes,
        "target_table": job.table,
    }


def main() -> None:
    loaded_env_files = load_env_files(discover_clickhouse_env_files(), verbose=True)
    args = parse_args()
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_root = Path(args.output_root_win)
    output_root.mkdir(parents=True, exist_ok=True)
    report_path = output_root / f"compact_codec_ingest_{run_id}.jsonl"
    settings = query_settings(args)
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    insert_concurrency = max(1, int(args.insert_concurrency))

    print("=" * 96, flush=True)
    print("Production compact-codec ClickHouse SIP ingest", flush=True)
    print(f"database={args.database}", flush=True)
    print(f"tables={args.quote_table},{args.trade_table}", flush=True)
    print(f"manifest_table={args.manifest_table}", flush=True)
    print(f"kinds={args.kinds} start_date={args.start_date} end_date={args.end_date}", flush=True)
    print(f"limit_files={args.limit_files} max_files_per_kind={args.max_files_per_kind}", flush=True)
    print(f"flatfiles_root_win={args.flatfiles_root_win}", flush=True)
    print(f"flatfiles_root_ch={args.flatfiles_root_ch}", flush=True)
    print(f"storage_policy={args.storage_policy or '<default>'}", flush=True)
    print(f"insert_concurrency={insert_concurrency} max_threads_per_insert={args.max_threads}", flush=True)
    print(f"preflight_processes={args.preflight_processes}", flush=True)
    print(f"settings={settings.strip()}", flush=True)
    print(f"dry_run={args.dry_run} retry_failed={args.retry_failed} retry_started={args.retry_started}", flush=True)
    print(f"report={report_path}", flush=True)
    print(f"secret_status={secret_status(env_status_keys())}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print("=" * 96, flush=True)

    if not args.dry_run:
        create_database_and_tables(client, args)

    jobs = build_jobs(args, settings)
    print(f"Discovered {len(jobs):,} source files", flush=True)
    for preview in jobs[:5]:
        print(
            f"  preview {preview.kind} {preview.date} {preview.source_file} "
            f"{preview.file_bytes / (1024**3):.2f} GiB -> {preview.source_path_ch}",
            flush=True,
        )
    if args.dry_run:
        return

    append_jsonl(
        report_path,
        {
            "type": "config",
            "run_id": run_id,
            "database": args.database,
            "quote_table": args.quote_table,
            "trade_table": args.trade_table,
            "manifest_table": args.manifest_table,
            "start_date": args.start_date,
            "end_date": args.end_date,
            "limit_files": args.limit_files,
            "max_files_per_kind": args.max_files_per_kind,
            "insert_concurrency": insert_concurrency,
            "preflight_processes": args.preflight_processes,
            "settings": settings.strip(),
        },
    )
    print(f"Loading latest manifest statuses for {len(jobs):,} discovered files...", flush=True)
    manifest_started_at = time.perf_counter()
    manifest_statuses = latest_manifest_statuses(client, args.database, args.manifest_table, jobs)
    print(
        f"Loaded {len(manifest_statuses):,} manifest statuses in {time.perf_counter() - manifest_started_at:.2f}s",
        flush=True,
    )

    pending_jobs: list[CompactIngestJob] = []
    skipped = 0
    for index, job in enumerate(jobs, start=1):
        status = manifest_statuses.get((job.kind, job.date, job.source_file, job.table), "")
        should_retry = (status == "failed" and args.retry_failed) or (status == "started" and args.retry_started)
        if status == "ok" and not should_retry:
            skipped += 1
            print(f"[{index:,}/{len(jobs):,}] SKIP {job.kind}:{job.date} status=ok", flush=True)
            continue
        if status in {"should_delete", "audit_should_delete", "audit_orphan_should_delete"}:
            skipped += 1
            print(f"[{index:,}/{len(jobs):,}] SKIP {job.kind}:{job.date} status={status}; delete mismatched rows before retrying", flush=True)
            continue
        if status in {"failed", "started"} and not should_retry:
            skipped += 1
            print(f"[{index:,}/{len(jobs):,}] SKIP {job.kind}:{job.date} status={status} use retry flag to rerun", flush=True)
            continue
        pending_jobs.append(job)
    print(f"Pending inserts={len(pending_jobs):,} skipped={skipped:,}", flush=True)

    source_preflight = preflight_jobs(pending_jobs, args.preflight_processes)
    append_jsonl(
        report_path,
        {
            "type": "preflight",
            "run_id": run_id,
            "rows": [
                {"job": job_public(job), "expected_stats": asdict(source_preflight[source_identity(job)].stats)}
                for job in pending_jobs
            ],
        },
    )
    insert_manifest_many(
        client,
        args.database,
        args.manifest_table,
        [(job, "discovered", source_preflight[source_identity(job)].stats) for job in pending_jobs],
        run_id=run_id,
    )

    completed = 0
    failed = 0
    manifest_failed = 0
    aggregate_rows = 0
    started_at = time.perf_counter()

    if pending_jobs:
        max_workers = min(insert_concurrency, len(pending_jobs))
        next_job_index = 0
        stop_submitting = False
        future_to_job: dict[concurrent.futures.Future[CompactIngestResult], CompactIngestJob] = {}

        def submit_next_job(executor: concurrent.futures.ThreadPoolExecutor) -> bool:
            nonlocal next_job_index, manifest_failed, stop_submitting
            if stop_submitting or next_job_index >= len(pending_jobs):
                return False
            job = pending_jobs[next_job_index]
            next_job_index += 1
            expected_stats = source_preflight[source_identity(job)].stats
            try:
                insert_manifest(
                    client,
                    args.database,
                    args.manifest_table,
                    job,
                    status="started",
                    run_id=run_id,
                    expected_stats=expected_stats,
                )
            except Exception as exc:  # noqa: BLE001
                manifest_failed += 1
                stop_submitting = not args.continue_on_error
                append_jsonl(
                    report_path,
                    {
                        "type": "manifest_write_failure",
                        "job": job_public(job),
                        "expected_stats": asdict(expected_stats),
                        "status": "started",
                        "exception": repr(exc),
                    },
                )
                print(f"MANIFEST FAILED before submit {job.kind}:{job.date}: {exc!r}", flush=True)
                return args.continue_on_error
            future_to_job[executor.submit(run_insert_job, job)] = job
            return True

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            while len(future_to_job) < max_workers and submit_next_job(executor):
                pass

            while future_to_job:
                done_futures, _ = concurrent.futures.wait(
                    future_to_job,
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                for future in done_futures:
                    job = future_to_job.pop(future)
                    expected_stats = source_preflight[source_identity(job)].stats
                    try:
                        result = future.result()
                        validate_insert_profile_rows(result.profile, expected_stats)
                        completed += 1
                        aggregate_rows += int(result.profile.written_rows or 0)
                        append_jsonl(
                            report_path,
                            {
                                "type": "insert_profile",
                                "job": job_public(job),
                                "expected_stats": asdict(expected_stats),
                                "profile": asdict(result.profile),
                                "status": "ok_query_finished",
                            },
                        )
                        try:
                            insert_manifest(
                                client,
                                args.database,
                                args.manifest_table,
                                job,
                                status="ok",
                                run_id=run_id,
                                profile=result.profile,
                                expected_stats=expected_stats,
                            )
                            append_jsonl(
                                report_path,
                                {
                                    "type": "manifest_write",
                                    "job": job_public(job),
                                    "status": "ok",
                                    "query_id": result.profile.query_id,
                                },
                            )
                        except Exception as exc:  # noqa: BLE001
                            manifest_failed += 1
                            stop_submitting = not args.continue_on_error
                            append_jsonl(
                                report_path,
                                {
                                    "type": "manifest_write_failure",
                                    "job": job_public(job),
                                    "expected_stats": asdict(expected_stats),
                                    "profile": asdict(result.profile),
                                    "status": "ok",
                                    "exception": repr(exc),
                                },
                            )
                            print(f"MANIFEST FAILED after ok insert {job.kind}:{job.date}: {exc!r}", flush=True)
                        print_profile(result.profile)
                    except Exception as exc:  # noqa: BLE001
                        failed += 1
                        stop_submitting = not args.continue_on_error
                        try:
                            insert_manifest(
                                client,
                                args.database,
                                args.manifest_table,
                                job,
                                status="failed",
                                run_id=run_id,
                                expected_stats=expected_stats,
                                exception=repr(exc),
                            )
                        except Exception as manifest_exc:  # noqa: BLE001
                            manifest_failed += 1
                            append_jsonl(
                                report_path,
                                {
                                    "type": "manifest_write_failure",
                                    "job": job_public(job),
                                    "expected_stats": asdict(expected_stats),
                                    "status": "failed",
                                    "insert_exception": repr(exc),
                                    "exception": repr(manifest_exc),
                                },
                            )
                            print(
                                f"MANIFEST FAILED after failed insert {job.kind}:{job.date}: {manifest_exc!r}",
                                flush=True,
                            )
                        append_jsonl(
                            report_path,
                            {
                                "type": "insert_profile",
                                "job": job_public(job),
                                "expected_stats": asdict(expected_stats),
                                "status": "failed",
                                "exception": repr(exc),
                            },
                        )
                        print(f"FAILED {job.kind}:{job.date}: {exc!r}", flush=True)

                    while len(future_to_job) < max_workers and submit_next_job(executor):
                        pass

                    elapsed = time.perf_counter() - started_at
                    rate = max(completed + failed, 1) / elapsed if elapsed > 0 else 0.0
                    remaining = len(pending_jobs) - next_job_index + len(future_to_job)
                    eta_seconds = remaining / rate if rate > 0 else 0.0
                    rows_per_second = aggregate_rows / elapsed if elapsed > 0 else 0.0
                    print(
                        f"PROGRESS submitted={next_job_index:,}/{len(pending_jobs):,} "
                        f"completed={completed:,} failed={failed:,} manifest_failed={manifest_failed:,} "
                        f"remaining={remaining:,} elapsed_min={elapsed / 60:.1f} eta_min={eta_seconds / 60:.1f} "
                        f"rows_per_sec={round(rows_per_second):,}",
                        flush=True,
                    )
        if stop_submitting and next_job_index < len(pending_jobs):
            not_submitted = len(pending_jobs) - next_job_index
            print(
                f"STOPPED submitting new jobs after first error; not_submitted={not_submitted:,}. "
                "Rerun after resolving the failed file.",
                flush=True,
            )

    elapsed = time.perf_counter() - started_at
    print("=" * 96, flush=True)
    print(
        f"DONE completed={completed:,} failed={failed:,} manifest_failed={manifest_failed:,} skipped={skipped:,} "
        f"elapsed_min={elapsed / 60:.1f} rows={aggregate_rows:,}",
        flush=True,
    )
    print(f"report={report_path}", flush=True)
    print("=" * 96, flush=True)
    if failed or manifest_failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
