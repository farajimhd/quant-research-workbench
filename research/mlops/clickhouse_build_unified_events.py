from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.clickhouse_delete_compact_audit_rows import default_clickhouse_url_with_network_fallback  # noqa: E402
from research.mlops.clickhouse_ingest_sip_compact_codec import DEFAULT_DATABASE, env_status_keys  # noqa: E402
from research.mlops.clickhouse_ingest_sip_flatfiles import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT_WIN,
    ClickHouseHttpClient,
    QueryProfile,
    default_clickhouse_password,
    default_clickhouse_user,
    default_storage_policy,
    discover_clickhouse_env_files,
    parse_size_bytes,
    quote_ident,
    run_profiled,
    sql_string,
)
from research.mlops.env import load_env_files, secret_status  # noqa: E402


DEFAULT_EVENTS_TABLE = "events"
DEFAULT_MANIFEST_TABLE = "events_build_manifest"
DEFAULT_CONTINUITY_TABLE = "events_ordinal_continuity"
DEFAULT_TRAIN_INDEX_TABLE = "train_2019_to_2025"
DEFAULT_VALIDATION_INDEX_TABLE = "validation_2026"
DEFAULT_SOURCE_START_DATE = "2019-01-01"
DEFAULT_SOURCE_END_DATE = "2099-12-31"
DEFAULT_TRAIN_START_DATE = "2019-01-01"
DEFAULT_TRAIN_END_DATE = "2025-12-31"
DEFAULT_VALIDATION_START_DATE = "2026-01-01"
DEFAULT_VALIDATION_END_DATE = "2099-12-31"
DEFAULT_PARTITION_BUCKETS = 256
DEFAULT_PARTITION_MODE = "month"
DEFAULT_EVENTS_PER_CHUNK = 128
DEFAULT_MAX_PARTITIONS_PER_INSERT_BLOCK = 1024


@dataclass(frozen=True, slots=True)
class TickerJob:
    ticker: str


@dataclass(frozen=True, slots=True)
class DayJob:
    source_date: str
    build_step: int


@dataclass(frozen=True, slots=True)
class IndexJob:
    table: str
    start_date: str
    end_date: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the final unified ClickHouse event table from compact SIP quotes/trades, one source day at a time."
    )
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url_with_network_fallback())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--quote-table", default="quotes")
    parser.add_argument("--trade-table", default="trades")
    parser.add_argument("--events-table", default=DEFAULT_EVENTS_TABLE)
    parser.add_argument("--manifest-table", default=DEFAULT_MANIFEST_TABLE)
    parser.add_argument("--continuity-table", default=DEFAULT_CONTINUITY_TABLE)
    parser.add_argument("--train-index-table", default=DEFAULT_TRAIN_INDEX_TABLE)
    parser.add_argument("--validation-index-table", default=DEFAULT_VALIDATION_INDEX_TABLE)
    parser.add_argument("--source-start-date", default=DEFAULT_SOURCE_START_DATE)
    parser.add_argument("--source-end-date", default=DEFAULT_SOURCE_END_DATE)
    parser.add_argument("--train-start-date", default=DEFAULT_TRAIN_START_DATE)
    parser.add_argument("--train-end-date", default=DEFAULT_TRAIN_END_DATE)
    parser.add_argument("--validation-start-date", default=DEFAULT_VALIDATION_START_DATE)
    parser.add_argument("--validation-end-date", default=DEFAULT_VALIDATION_END_DATE)
    parser.add_argument("--events-per-chunk", type=int, default=DEFAULT_EVENTS_PER_CHUNK)
    parser.add_argument("--partition-buckets", type=int, default=DEFAULT_PARTITION_BUCKETS)
    parser.add_argument(
        "--partition-mode",
        choices=("month", "ticker_hash", "none"),
        default=DEFAULT_PARTITION_MODE,
        help="Physical MergeTree partitioning. Default month avoids excessive active parts during daily appends.",
    )
    parser.add_argument("--tickers", default="", help="Optional comma-separated ticker subset. Empty means discover all tickers.")
    parser.add_argument("--ticker-file", default="", help="Optional newline-delimited ticker list.")
    parser.add_argument("--ticker-offset", type=int, default=0)
    parser.add_argument("--limit-tickers", type=int, default=0)
    parser.add_argument("--day-offset", type=int, default=0)
    parser.add_argument("--limit-days", type=int, default=0)
    parser.add_argument("--storage-policy", default=default_live_storage_policy())
    parser.add_argument("--max-memory-usage", default="400G")
    parser.add_argument("--max-threads", type=int, default=32)
    parser.add_argument("--max-partitions-per-insert-block", type=int, default=DEFAULT_MAX_PARTITIONS_PER_INSERT_BLOCK)
    parser.add_argument("--output-root-win", default=str(DEFAULT_OUTPUT_ROOT_WIN / "unified_events"))
    parser.add_argument("--clean-mode", choices=("issue_flags_zero", "structural"), default="issue_flags_zero")
    parser.add_argument("--rebuild", action="store_true", help="Drop event/index/manifest tables before building.")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--retry-started", action="store_true")
    parser.add_argument("--force-ticker-delete", action="store_true", help="Delete existing rows for a ticker before retrying it.")
    parser.add_argument("--force-day-delete", action="store_true", help="Delete existing rows for a day before retrying it.")
    parser.add_argument("--no-build-events", action="store_true", help="Skip event table ticker inserts.")
    parser.add_argument("--no-build-index", action="store_true", help="Skip per-ticker train/validation index row writes.")
    parser.add_argument("--optimize-final", action="store_true", help="Run OPTIMIZE FINAL on the events table after building.")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def default_live_storage_policy() -> str:
    return os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY") or default_storage_policy()


def query_settings(args: argparse.Namespace) -> str:
    settings = []
    if args.max_threads > 0:
        settings.append(f"max_threads = {int(args.max_threads)}")
    if str(args.max_memory_usage) != "0":
        settings.append(f"max_memory_usage = {parse_size_bytes(str(args.max_memory_usage))}")
    if int(args.max_partitions_per_insert_block) > 0:
        settings.append(f"max_partitions_per_insert_block = {int(args.max_partitions_per_insert_block)}")
    return "\nSETTINGS " + ", ".join(settings) if settings else ""


def mutation_settings(args: argparse.Namespace) -> str:
    settings = ["mutations_sync = 2"]
    if args.max_threads > 0:
        settings.append(f"max_threads = {int(args.max_threads)}")
    if str(args.max_memory_usage) != "0":
        settings.append(f"max_memory_usage = {parse_size_bytes(str(args.max_memory_usage))}")
    return "\nSETTINGS " + ", ".join(settings)


def mergetree_settings(storage_policy: str) -> str:
    settings = ["index_granularity = 8192"]
    if storage_policy.strip():
        settings.append(f"storage_policy = {sql_string(storage_policy.strip())}")
    return "SETTINGS " + ", ".join(settings)


def event_partition_clause(args: argparse.Namespace) -> str:
    if args.partition_mode == "month":
        return "PARTITION BY toYYYYMM(event_date)"
    if args.partition_mode == "ticker_hash":
        return f"PARTITION BY cityHash64(ticker) % {int(args.partition_buckets)}"
    return "PARTITION BY tuple()"


def create_events_table_sql(args: argparse.Namespace) -> str:
    db = quote_ident(args.database)
    table = quote_ident(args.events_table)
    return f"""
CREATE TABLE IF NOT EXISTS {db}.{table}
(
    ticker LowCardinality(String),
    ordinal UInt64 CODEC(T64, ZSTD(1)),
    event_type UInt8,
    sip_timestamp_us UInt64 CODEC(DoubleDelta, ZSTD(1)),
    price_primary_int UInt32 CODEC(T64, ZSTD(1)),
    price_secondary_int UInt32 CODEC(T64, ZSTD(1)),
    size_primary Float32 CODEC(ZSTD(1)),
    size_secondary Float32 CODEC(ZSTD(1)),
    exchange_primary UInt8,
    exchange_secondary UInt8,
    event_flags UInt8,
    conditions_packed UInt32 CODEC(T64, ZSTD(1)),
    event_date Date
)
ENGINE = MergeTree
{event_partition_clause(args)}
ORDER BY (ticker, ordinal)
{mergetree_settings(args.storage_policy)}
"""


def create_manifest_table_sql(args: argparse.Namespace) -> str:
    db = quote_ident(args.database)
    table = quote_ident(args.manifest_table)
    return f"""
CREATE TABLE IF NOT EXISTS {db}.{table}
(
    target_table LowCardinality(String),
    ticker LowCardinality(String),
    source_start_date Date,
    source_end_date Date,
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
    updated_at DateTime DEFAULT now()
)
ENGINE = MergeTree
ORDER BY (target_table, ticker, source_start_date, source_end_date, updated_at)
{mergetree_settings(args.storage_policy)}
"""


def create_continuity_table_sql(args: argparse.Namespace) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(args.database)}.{quote_ident(args.continuity_table)}
(
    ticker LowCardinality(String),
    build_step UInt32,
    source_date Date,
    event_count UInt64,
    next_ordinal UInt64,
    last_ordinal UInt64,
    first_sip_timestamp_us UInt64 DEFAULT 0,
    last_sip_timestamp_us UInt64,
    updated_at DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (ticker, build_step)
{mergetree_settings(args.storage_policy)}
"""


def create_index_table_sql(args: argparse.Namespace, table: str) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(args.database)}.{quote_ident(table)}
(
    ticker LowCardinality(String),
    split_start_date Date,
    split_end_date Date,
    context_events UInt32,
    split_event_count UInt64,
    valid_origin_count UInt64,
    first_ordinal UInt64,
    last_ordinal UInt64,
    max_valid_ordinal UInt64,
    first_sip_timestamp_us UInt64,
    last_sip_timestamp_us UInt64,
    built_at DateTime DEFAULT now()
)
ENGINE = MergeTree
ORDER BY ticker
{mergetree_settings(args.storage_policy)}
"""


def ensure_continuity_table_columns(client: ClickHouseHttpClient, args: argparse.Namespace) -> None:
    client.execute(
        f"""
ALTER TABLE {quote_ident(args.database)}.{quote_ident(args.continuity_table)}
ADD COLUMN IF NOT EXISTS first_sip_timestamp_us UInt64 DEFAULT 0 AFTER last_ordinal
"""
    )


def latest_day_status(client: ClickHouseHttpClient, args: argparse.Namespace, job: DayJob) -> str:
    try:
        rows = client.query_tsv(
            f"""
SELECT argMax(status, updated_at)
FROM {quote_ident(args.database)}.{quote_ident(args.manifest_table)}
WHERE target_table = {sql_string(args.events_table)}
  AND ticker = '__ALL__'
  AND source_start_date = toDate({sql_string(job.source_date)})
  AND source_end_date = toDate({sql_string(job.source_date)})
"""
        ).strip().splitlines()
    except Exception:
        return ""
    return rows[0] if rows else ""


def latest_ticker_status(client: ClickHouseHttpClient, args: argparse.Namespace, job: TickerJob) -> str:
    try:
        rows = client.query_tsv(
            f"""
SELECT argMax(status, updated_at)
FROM {quote_ident(args.database)}.{quote_ident(args.manifest_table)}
WHERE target_table = {sql_string(args.events_table)}
  AND ticker = {sql_string(job.ticker)}
  AND source_start_date = toDate({sql_string(args.source_start_date)})
  AND source_end_date = toDate({sql_string(args.source_end_date)})
"""
        ).strip().splitlines()
    except Exception:
        return ""
    return rows[0] if rows else ""


def day_event_and_continuity_counts(client: ClickHouseHttpClient, args: argparse.Namespace, job: DayJob) -> tuple[int, int]:
    rows = client.query_tsv(
        f"""
SELECT event_rows, continuity_rows
FROM
(
    SELECT count() AS event_rows
    FROM {quote_ident(args.database)}.{quote_ident(args.events_table)}
    WHERE event_date = toDate({sql_string(job.source_date)})
) AS e
CROSS JOIN
(
    SELECT coalesce(sum(event_count), 0) AS continuity_rows
    FROM
    (
        SELECT
            ticker,
            build_step,
            argMax(event_count, updated_at) AS event_count,
            argMax(source_date, updated_at) AS source_date
        FROM {quote_ident(args.database)}.{quote_ident(args.continuity_table)}
        GROUP BY ticker, build_step
    )
    WHERE source_date = toDate({sql_string(job.source_date)})
) AS c
"""
    ).strip().splitlines()
    if not rows:
        return 0, 0
    event_rows, continuity_rows = rows[0].split("\t")
    return int(event_rows or 0), int(continuity_rows or 0)


def later_built_day_count(client: ClickHouseHttpClient, args: argparse.Namespace, job: DayJob) -> int:
    rows = client.query_tsv(
        f"""
SELECT countDistinct(build_step)
FROM {quote_ident(args.database)}.{quote_ident(args.continuity_table)}
WHERE build_step > toUInt32({int(job.build_step)})
"""
    ).strip().splitlines()
    return int(rows[0] or 0) if rows else 0


def recover_completed_started_day(client: ClickHouseHttpClient, args: argparse.Namespace, job: DayJob, run_id: str) -> bool:
    event_rows, continuity_rows = day_event_and_continuity_counts(client, args, job)
    if event_rows <= 0 or event_rows != continuity_rows:
        return False
    profile = QueryProfile(
        label=f"recover_completed_day_{job.source_date}",
        query_id="",
        wall_seconds=0.0,
        written_rows=event_rows,
    )
    insert_day_manifest(client, args, job, status="ok", run_id=run_id, profile=profile)
    print(
        f"DAY RECOVERED day={job.source_date} latest_status was incomplete but "
        f"events_rows={event_rows:,} continuity_rows={continuity_rows:,}; wrote ok manifest row",
        flush=True,
    )
    return True


def should_skip_status(status: str, args: argparse.Namespace) -> bool:
    if status == "ok":
        return True
    if status == "failed" and not args.retry_failed:
        return True
    if status == "started" and not args.retry_started:
        return True
    if status == "interrupted" and not args.retry_started:
        return True
    return False


def needs_force_delete_before_retry(status: str, args: argparse.Namespace) -> bool:
    if status in {"started", "interrupted"} and args.retry_started:
        return True
    if status == "failed" and args.retry_failed:
        return True
    return False


def insert_manifest(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    job: TickerJob,
    *,
    status: str,
    run_id: str,
    profile: QueryProfile | None = None,
    exception: str = "",
) -> None:
    profile = profile or QueryProfile(label="", query_id="", wall_seconds=0.0)
    client.execute(
        f"""
INSERT INTO {quote_ident(args.database)}.{quote_ident(args.manifest_table)}
(
    target_table, ticker, source_start_date, source_end_date, status,
    run_id, query_id, wall_seconds, query_duration_ms, memory_usage_bytes,
    read_rows, read_bytes, written_rows, written_bytes, exception
)
VALUES
(
    {sql_string(args.events_table)},
    {sql_string(job.ticker)},
    toDate({sql_string(args.source_start_date)}),
    toDate({sql_string(args.source_end_date)}),
    {sql_string(status)},
    {sql_string(run_id)},
    {sql_string(profile.query_id)},
    {float(profile.wall_seconds)},
    {profile.query_duration_ms or 0},
    {profile.memory_usage_bytes or 0},
    {profile.read_rows or 0},
    {profile.read_bytes or 0},
    {profile.written_rows or 0},
    {profile.written_bytes or 0},
    {sql_string(exception or profile.exception)}
)
"""
    )


def insert_day_manifest(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    job: DayJob,
    *,
    status: str,
    run_id: str,
    profile: QueryProfile | None = None,
    exception: str = "",
) -> None:
    profile = profile or QueryProfile(label="", query_id="", wall_seconds=0.0)
    client.execute(
        f"""
INSERT INTO {quote_ident(args.database)}.{quote_ident(args.manifest_table)}
(
    target_table, ticker, source_start_date, source_end_date, status,
    run_id, query_id, wall_seconds, query_duration_ms, memory_usage_bytes,
    read_rows, read_bytes, written_rows, written_bytes, exception
)
VALUES
(
    {sql_string(args.events_table)},
    '__ALL__',
    toDate({sql_string(job.source_date)}),
    toDate({sql_string(job.source_date)}),
    {sql_string(status)},
    {sql_string(run_id)},
    {sql_string(profile.query_id)},
    {float(profile.wall_seconds)},
    {profile.query_duration_ms or 0},
    {profile.memory_usage_bytes or 0},
    {profile.read_rows or 0},
    {profile.read_bytes or 0},
    {profile.written_rows or 0},
    {profile.written_bytes or 0},
    {sql_string(exception or profile.exception)}
)
"""
    )


def condition_code_expr(slot: int) -> str:
    return f"toInt16OrZero(arrayElement(splitByChar(',', conditions), {slot}))"


def quote_condition_pack_expr() -> str:
    return """
bitOr(
    bitOr(toUInt32(coalesce(qc1.dense_id, 0)), bitShiftLeft(toUInt32(coalesce(qc2.dense_id, 0)), 8)),
    bitOr(bitShiftLeft(toUInt32(coalesce(qc3.dense_id, 0)), 16), bitShiftLeft(toUInt32(coalesce(qc4.dense_id, 0)), 24))
)
""".strip()


def trade_condition_pack_expr() -> str:
    return """
bitOr(
    bitOr(toUInt32(coalesce(tc1.dense_id, 0)), bitShiftLeft(toUInt32(coalesce(tc2.dense_id, 0)), 6)),
    bitOr(
        bitOr(bitShiftLeft(toUInt32(coalesce(tc3.dense_id, 0)), 12), bitShiftLeft(toUInt32(coalesce(tc4.dense_id, 0)), 18)),
        bitShiftLeft(toUInt32(coalesce(tc5.dense_id, 0)), 24)
    )
)
""".strip()


def condition_reference_subquery(args: argparse.Namespace, table: str) -> str:
    return (
        f"(SELECT modifier_int, min(dense_id) AS dense_id "
        f"FROM {quote_ident(args.database)}.{quote_ident(table)} "
        "GROUP BY modifier_int)"
    )


def quote_clean_predicate(args: argparse.Namespace) -> str:
    base = """
q.ticker != ''
AND q.sip_timestamp_us > 0
AND q.sequence_number > 0
AND q.bid_price_int > 0
AND q.ask_price_int > 0
AND q.bid_size > 0
AND q.ask_size > 0
AND if(bitAnd(q.quote_flags, 1) = 1, q.bid_price_int / 10000.0, q.bid_price_int / 100.0)
    <= if(bitAnd(bitShiftRight(q.quote_flags, 1), 1) = 1, q.ask_price_int / 10000.0, q.ask_price_int / 100.0)
""".strip()
    if args.clean_mode == "issue_flags_zero":
        base += "\nAND q.issue_flags = 0"
    return base


def trade_clean_predicate(args: argparse.Namespace) -> str:
    base = """
t.ticker != ''
AND t.sip_timestamp_us > 0
AND t.sequence_number > 0
AND t.price_int > 0
AND t.size > 0
""".strip()
    if args.clean_mode == "issue_flags_zero":
        base += "\nAND t.issue_flags = 0"
    return base


def ticker_filter_sql(args: argparse.Namespace, alias: str) -> str:
    tickers = discover_requested_tickers(args)
    if not tickers:
        return ""
    values = ", ".join(sql_string(ticker) for ticker in tickers)
    column = f"{alias}.ticker" if alias else "ticker"
    return f"\n          AND {column} IN ({values})"


def event_union_day_sql(args: argparse.Namespace, job: DayJob) -> str:
    db = quote_ident(args.database)
    quote_table = quote_ident(args.quote_table)
    trade_table = quote_ident(args.trade_table)
    quote_ticker_filter = ticker_filter_sql(args, "")
    trade_ticker_filter = ticker_filter_sql(args, "")
    return f"""
    SELECT
        q.ticker AS ticker,
        toUInt8(0) AS event_type,
        q.sip_timestamp_us AS sip_timestamp_us,
        q.sequence_number AS sequence_number,
        q.ask_price_int AS price_primary_int,
        q.bid_price_int AS price_secondary_int,
        toFloat32(q.ask_size) AS size_primary,
        toFloat32(q.bid_size) AS size_secondary,
        q.ask_exchange AS exchange_primary,
        q.bid_exchange AS exchange_secondary,
        toUInt8(
            bitOr(
                bitOr(bitAnd(bitShiftRight(q.quote_flags, 1), 1), bitShiftLeft(bitAnd(q.quote_flags, 1), 1)),
                bitShiftLeft(bitAnd(bitShiftRight(q.quote_flags, 2), 7), 2)
            )
        ) AS event_flags,
        {quote_condition_pack_expr()} AS conditions_packed,
        q.event_date AS event_date
    FROM
    (
        SELECT
            *,
            event_date AS event_date,
            {condition_code_expr(1)} AS condition_code_1,
            {condition_code_expr(2)} AS condition_code_2,
            {condition_code_expr(3)} AS condition_code_3,
            {condition_code_expr(4)} AS condition_code_4
        FROM {db}.{quote_table}
        WHERE event_date = toDate({sql_string(job.source_date)})
          {quote_ticker_filter}
    ) AS q
    LEFT JOIN {condition_reference_subquery(args, "ref_quote_conditions")} AS qc1 ON qc1.modifier_int = q.condition_code_1
    LEFT JOIN {condition_reference_subquery(args, "ref_quote_conditions")} AS qc2 ON qc2.modifier_int = q.condition_code_2
    LEFT JOIN {condition_reference_subquery(args, "ref_quote_conditions")} AS qc3 ON qc3.modifier_int = q.condition_code_3
    LEFT JOIN {condition_reference_subquery(args, "ref_quote_conditions")} AS qc4 ON qc4.modifier_int = q.condition_code_4
    WHERE {quote_clean_predicate(args)}

    UNION ALL

    SELECT
        t.ticker AS ticker,
        toUInt8(1) AS event_type,
        t.sip_timestamp_us AS sip_timestamp_us,
        t.sequence_number AS sequence_number,
        t.price_int AS price_primary_int,
        toUInt32(0) AS price_secondary_int,
        t.size AS size_primary,
        toFloat32(0) AS size_secondary,
        t.exchange AS exchange_primary,
        toUInt8(0) AS exchange_secondary,
        toUInt8(
            bitOr(
                bitAnd(t.trade_flags, 1),
                bitShiftLeft(bitAnd(bitShiftRight(t.trade_flags, 1), 7), 2)
            )
        ) AS event_flags,
        {trade_condition_pack_expr()} AS conditions_packed,
        t.event_date AS event_date
    FROM
    (
        SELECT
            *,
            event_date AS event_date,
            {condition_code_expr(1)} AS condition_code_1,
            {condition_code_expr(2)} AS condition_code_2,
            {condition_code_expr(3)} AS condition_code_3,
            {condition_code_expr(4)} AS condition_code_4,
            {condition_code_expr(5)} AS condition_code_5
        FROM {db}.{trade_table}
        WHERE event_date = toDate({sql_string(job.source_date)})
          {trade_ticker_filter}
    ) AS t
    LEFT JOIN {condition_reference_subquery(args, "ref_trade_conditions")} AS tc1 ON tc1.modifier_int = t.condition_code_1
    LEFT JOIN {condition_reference_subquery(args, "ref_trade_conditions")} AS tc2 ON tc2.modifier_int = t.condition_code_2
    LEFT JOIN {condition_reference_subquery(args, "ref_trade_conditions")} AS tc3 ON tc3.modifier_int = t.condition_code_3
    LEFT JOIN {condition_reference_subquery(args, "ref_trade_conditions")} AS tc4 ON tc4.modifier_int = t.condition_code_4
    LEFT JOIN {condition_reference_subquery(args, "ref_trade_conditions")} AS tc5 ON tc5.modifier_int = t.condition_code_5
    WHERE {trade_clean_predicate(args)}
"""


def event_union_sql(args: argparse.Namespace, job: TickerJob) -> str:
    db = quote_ident(args.database)
    quote_table = quote_ident(args.quote_table)
    trade_table = quote_ident(args.trade_table)
    ticker_filter = f"ticker = {sql_string(job.ticker)}"
    return f"""
    SELECT
        q.ticker AS ticker,
        toUInt8(0) AS event_type,
        q.sip_timestamp_us AS sip_timestamp_us,
        q.sequence_number AS sequence_number,
        q.ask_price_int AS price_primary_int,
        q.bid_price_int AS price_secondary_int,
        toFloat32(q.ask_size) AS size_primary,
        toFloat32(q.bid_size) AS size_secondary,
        q.ask_exchange AS exchange_primary,
        q.bid_exchange AS exchange_secondary,
        toUInt8(
            bitOr(
                bitOr(bitAnd(bitShiftRight(q.quote_flags, 1), 1), bitShiftLeft(bitAnd(q.quote_flags, 1), 1)),
                bitShiftLeft(bitAnd(bitShiftRight(q.quote_flags, 2), 7), 2)
            )
        ) AS event_flags,
        {quote_condition_pack_expr()} AS conditions_packed,
        q.event_date AS event_date
    FROM
    (
        SELECT
            *,
            event_date AS event_date,
            {condition_code_expr(1)} AS condition_code_1,
            {condition_code_expr(2)} AS condition_code_2,
            {condition_code_expr(3)} AS condition_code_3,
            {condition_code_expr(4)} AS condition_code_4
        FROM {db}.{quote_table}
        WHERE event_date BETWEEN toDate({sql_string(args.source_start_date)}) AND toDate({sql_string(args.source_end_date)})
          AND {ticker_filter}
    ) AS q
    LEFT JOIN {condition_reference_subquery(args, "ref_quote_conditions")} AS qc1 ON qc1.modifier_int = q.condition_code_1
    LEFT JOIN {condition_reference_subquery(args, "ref_quote_conditions")} AS qc2 ON qc2.modifier_int = q.condition_code_2
    LEFT JOIN {condition_reference_subquery(args, "ref_quote_conditions")} AS qc3 ON qc3.modifier_int = q.condition_code_3
    LEFT JOIN {condition_reference_subquery(args, "ref_quote_conditions")} AS qc4 ON qc4.modifier_int = q.condition_code_4
    WHERE {quote_clean_predicate(args)}

    UNION ALL

    SELECT
        t.ticker AS ticker,
        toUInt8(1) AS event_type,
        t.sip_timestamp_us AS sip_timestamp_us,
        t.sequence_number AS sequence_number,
        t.price_int AS price_primary_int,
        toUInt32(0) AS price_secondary_int,
        t.size AS size_primary,
        toFloat32(0) AS size_secondary,
        t.exchange AS exchange_primary,
        toUInt8(0) AS exchange_secondary,
        toUInt8(
            bitOr(
                bitAnd(t.trade_flags, 1),
                bitShiftLeft(bitAnd(bitShiftRight(t.trade_flags, 1), 7), 2)
            )
        ) AS event_flags,
        {trade_condition_pack_expr()} AS conditions_packed,
        t.event_date AS event_date
    FROM
    (
        SELECT
            *,
            event_date AS event_date,
            {condition_code_expr(1)} AS condition_code_1,
            {condition_code_expr(2)} AS condition_code_2,
            {condition_code_expr(3)} AS condition_code_3,
            {condition_code_expr(4)} AS condition_code_4,
            {condition_code_expr(5)} AS condition_code_5
        FROM {db}.{trade_table}
        WHERE event_date BETWEEN toDate({sql_string(args.source_start_date)}) AND toDate({sql_string(args.source_end_date)})
          AND {ticker_filter}
    ) AS t
    LEFT JOIN {condition_reference_subquery(args, "ref_trade_conditions")} AS tc1 ON tc1.modifier_int = t.condition_code_1
    LEFT JOIN {condition_reference_subquery(args, "ref_trade_conditions")} AS tc2 ON tc2.modifier_int = t.condition_code_2
    LEFT JOIN {condition_reference_subquery(args, "ref_trade_conditions")} AS tc3 ON tc3.modifier_int = t.condition_code_3
    LEFT JOIN {condition_reference_subquery(args, "ref_trade_conditions")} AS tc4 ON tc4.modifier_int = t.condition_code_4
    LEFT JOIN {condition_reference_subquery(args, "ref_trade_conditions")} AS tc5 ON tc5.modifier_int = t.condition_code_5
    WHERE {trade_clean_predicate(args)}
"""


def insert_ticker_sql(args: argparse.Namespace, job: TickerJob) -> str:
    db = quote_ident(args.database)
    table = quote_ident(args.events_table)
    return f"""
INSERT INTO {db}.{table}
(
    ticker,
    ordinal,
    event_type,
    sip_timestamp_us,
    price_primary_int,
    price_secondary_int,
    size_primary,
    size_secondary,
    exchange_primary,
    exchange_secondary,
    event_flags,
    conditions_packed,
    event_date
)
SELECT
    ticker,
    toUInt64(row_number() OVER (PARTITION BY ticker ORDER BY sip_timestamp_us, sequence_number, event_type) - 1) AS ordinal,
    event_type,
    sip_timestamp_us,
    price_primary_int,
    price_secondary_int,
    size_primary,
    size_secondary,
    exchange_primary,
    exchange_secondary,
    event_flags,
    conditions_packed,
    event_date
FROM
(
{event_union_sql(args, job)}
)
ORDER BY ticker, ordinal
{query_settings(args)}
"""


def insert_day_sql(args: argparse.Namespace, job: DayJob) -> str:
    db = quote_ident(args.database)
    table = quote_ident(args.events_table)
    continuity_table = quote_ident(args.continuity_table)
    return f"""
INSERT INTO {db}.{table}
(
    ticker,
    ordinal,
    event_type,
    sip_timestamp_us,
    price_primary_int,
    price_secondary_int,
    size_primary,
    size_secondary,
    exchange_primary,
    exchange_secondary,
    event_flags,
    conditions_packed,
    event_date
)
SELECT
    e.ticker,
    coalesce(c.ordinal_offset, toUInt64(0))
        + toUInt64(row_number() OVER (PARTITION BY e.ticker ORDER BY e.sip_timestamp_us, e.sequence_number, e.event_type) - 1) AS ordinal,
    e.event_type,
    e.sip_timestamp_us,
    e.price_primary_int,
    e.price_secondary_int,
    e.size_primary,
    e.size_secondary,
    e.exchange_primary,
    e.exchange_secondary,
    e.event_flags,
    e.conditions_packed,
    e.event_date
FROM
(
{event_union_day_sql(args, job)}
) AS e
LEFT JOIN
(
    SELECT
        ticker,
        argMax(next_ordinal, tuple(build_step, updated_at)) AS ordinal_offset
    FROM {db}.{continuity_table}
    WHERE build_step < toUInt32({int(job.build_step)})
    GROUP BY ticker
) AS c ON c.ticker = e.ticker
ORDER BY e.ticker, ordinal
{query_settings(args)}
"""


def insert_day_continuity_sql(args: argparse.Namespace, job: DayJob) -> str:
    db = quote_ident(args.database)
    continuity_table = quote_ident(args.continuity_table)
    return f"""
INSERT INTO {db}.{continuity_table}
(
    ticker,
    build_step,
    source_date,
    event_count,
    next_ordinal,
    last_ordinal,
    first_sip_timestamp_us,
    last_sip_timestamp_us
)
SELECT
    e.ticker,
    toUInt32({int(job.build_step)}) AS build_step,
    toDate({sql_string(job.source_date)}) AS source_date,
    count() AS event_count,
    coalesce(c.ordinal_offset, toUInt64(0)) + count() AS next_ordinal,
    coalesce(c.ordinal_offset, toUInt64(0)) + count() - 1 AS last_ordinal,
    min(e.sip_timestamp_us) AS first_sip_timestamp_us,
    max(e.sip_timestamp_us) AS last_sip_timestamp_us
FROM
(
{event_union_day_sql(args, job)}
) AS e
LEFT JOIN
(
    SELECT
        ticker,
        argMax(next_ordinal, tuple(build_step, updated_at)) AS ordinal_offset
    FROM {db}.{continuity_table}
    WHERE build_step < toUInt32({int(job.build_step)})
    GROUP BY ticker
) AS c ON c.ticker = e.ticker
GROUP BY e.ticker, c.ordinal_offset
{query_settings(args)}
"""


def delete_day_sql(args: argparse.Namespace, job: DayJob) -> str:
    return f"""
ALTER TABLE {quote_ident(args.database)}.{quote_ident(args.events_table)}
DELETE WHERE event_date = toDate({sql_string(job.source_date)})
{mutation_settings(args)}
"""


def delete_day_continuity_sql(args: argparse.Namespace, job: DayJob) -> str:
    return f"""
ALTER TABLE {quote_ident(args.database)}.{quote_ident(args.continuity_table)}
DELETE WHERE source_date = toDate({sql_string(job.source_date)})
{mutation_settings(args)}
"""


def delete_ticker_sql(args: argparse.Namespace, job: TickerJob) -> str:
    return f"""
ALTER TABLE {quote_ident(args.database)}.{quote_ident(args.events_table)}
DELETE WHERE ticker = {sql_string(job.ticker)}
{mutation_settings(args)}
"""


def delete_ticker_index_sql(args: argparse.Namespace, table: str, job: TickerJob) -> str:
    return f"""
ALTER TABLE {quote_ident(args.database)}.{quote_ident(table)}
DELETE WHERE ticker = {sql_string(job.ticker)}
{mutation_settings(args)}
"""


def insert_ticker_index_sql(args: argparse.Namespace, index_job: IndexJob, ticker_job: TickerJob) -> str:
    range_predicate = (
        f"event_date BETWEEN toDate({sql_string(index_job.start_date)}) "
        f"AND toDate({sql_string(index_job.end_date)})"
    )
    return f"""
INSERT INTO {quote_ident(args.database)}.{quote_ident(index_job.table)}
(
    ticker,
    split_start_date,
    split_end_date,
    context_events,
    split_event_count,
    valid_origin_count,
    first_ordinal,
    last_ordinal,
    max_valid_ordinal,
    first_sip_timestamp_us,
    last_sip_timestamp_us
)
SELECT
    ticker,
    toDate({sql_string(index_job.start_date)}) AS split_start_date,
    toDate({sql_string(index_job.end_date)}) AS split_end_date,
    toUInt32({int(args.events_per_chunk)}) AS context_events,
    split_event_count,
    split_event_count AS valid_origin_count,
    first_ordinal,
    last_ordinal,
    last_ordinal AS max_valid_ordinal,
    first_sip_timestamp_us,
    last_sip_timestamp_us
FROM
(
    SELECT
        ticker,
        countIf({range_predicate}) AS split_event_count,
        minIf(ordinal, {range_predicate}) AS first_ordinal,
        maxIf(ordinal, {range_predicate}) AS last_ordinal,
        minIf(sip_timestamp_us, {range_predicate}) AS first_sip_timestamp_us,
        maxIf(sip_timestamp_us, {range_predicate}) AS last_sip_timestamp_us
    FROM {quote_ident(args.database)}.{quote_ident(args.events_table)}
    WHERE ticker = {sql_string(ticker_job.ticker)}
    GROUP BY ticker
)
WHERE split_event_count > 0
{query_settings(args)}
"""


def insert_split_index_sql(args: argparse.Namespace, index_job: IndexJob) -> str:
    range_predicate = (
        f"source_date BETWEEN toDate({sql_string(index_job.start_date)}) "
        f"AND toDate({sql_string(index_job.end_date)})"
    )
    day_first_ordinal = "day_last_ordinal - day_event_count + 1"
    return f"""
INSERT INTO {quote_ident(args.database)}.{quote_ident(index_job.table)}
(
    ticker,
    split_start_date,
    split_end_date,
    context_events,
    split_event_count,
    valid_origin_count,
    first_ordinal,
    last_ordinal,
    max_valid_ordinal,
    first_sip_timestamp_us,
    last_sip_timestamp_us
)
SELECT
    ticker,
    toDate({sql_string(index_job.start_date)}) AS split_start_date,
    toDate({sql_string(index_job.end_date)}) AS split_end_date,
    toUInt32({int(args.events_per_chunk)}) AS context_events,
    split_event_count,
    split_event_count AS valid_origin_count,
    first_ordinal,
    last_ordinal,
    last_ordinal AS max_valid_ordinal,
    first_sip_timestamp_us,
    last_sip_timestamp_us
FROM
(
    SELECT
        ticker,
        sumIf(day_event_count, {range_predicate}) AS split_event_count,
        minIf({day_first_ordinal}, {range_predicate}) AS first_ordinal,
        maxIf(day_last_ordinal, {range_predicate}) AS last_ordinal,
        if(
            minIf(day_first_sip_timestamp_us, {range_predicate}) = 0,
            minIf(day_last_sip_timestamp_us, {range_predicate}),
            minIf(day_first_sip_timestamp_us, {range_predicate})
        ) AS first_sip_timestamp_us,
        maxIf(day_last_sip_timestamp_us, {range_predicate}) AS last_sip_timestamp_us
    FROM
    (
        SELECT
            ticker,
            build_step,
            argMax(source_date, updated_at) AS source_date,
            argMax(event_count, updated_at) AS day_event_count,
            argMax(last_ordinal, updated_at) AS day_last_ordinal,
            argMax(first_sip_timestamp_us, updated_at) AS day_first_sip_timestamp_us,
            argMax(last_sip_timestamp_us, updated_at) AS day_last_sip_timestamp_us
        FROM {quote_ident(args.database)}.{quote_ident(args.continuity_table)}
        GROUP BY ticker, build_step
    )
    GROUP BY ticker
)
WHERE split_event_count > 0
{query_settings(args)}
"""


def split_index_jobs(args: argparse.Namespace) -> list[IndexJob]:
    return [
        IndexJob(args.train_index_table, args.train_start_date, args.train_end_date),
        IndexJob(args.validation_index_table, args.validation_start_date, args.validation_end_date),
    ]


def delete_ticker_indexes(client: ClickHouseHttpClient, args: argparse.Namespace, job: TickerJob) -> None:
    for index_job in split_index_jobs(args):
        run_profiled(client, f"delete_{index_job.table}_{job.ticker}", delete_ticker_index_sql(args, index_job.table, job))


def insert_ticker_indexes(client: ClickHouseHttpClient, args: argparse.Namespace, job: TickerJob, report_path: Path) -> None:
    if args.no_build_index:
        return
    for index_job in split_index_jobs(args):
        profile = run_profiled(client, f"index_{index_job.table}_{job.ticker}", insert_ticker_index_sql(args, index_job, job))
        append_jsonl(report_path, {"type": "ticker_index", "job": asdict(index_job), "ticker": job.ticker, "profile": asdict(profile)})


def insert_split_indexes(client: ClickHouseHttpClient, args: argparse.Namespace, report_path: Path) -> None:
    if args.no_build_index:
        return
    for index_job in split_index_jobs(args):
        client.execute(f"TRUNCATE TABLE {quote_ident(args.database)}.{quote_ident(index_job.table)}")
        profile = run_profiled(client, f"index_{index_job.table}", insert_split_index_sql(args, index_job))
        append_jsonl(report_path, {"type": "split_index", "job": asdict(index_job), "profile": asdict(profile)})


def run_day(client: ClickHouseHttpClient, args: argparse.Namespace, job: DayJob, run_id: str, report_path: Path) -> str:
    status = latest_day_status(client, args, job)
    if status in {"failed", "started", "interrupted"} and recover_completed_started_day(client, args, job, run_id):
        return "skipped"
    if should_skip_status(status, args):
        print(f"SKIP day={job.source_date} latest_status={status}", flush=True)
        return "skipped"
    print("=" * 96, flush=True)
    print(f"DAY START {job.source_date} build_step={job.build_step}", flush=True)
    if needs_force_delete_before_retry(status, args) and not args.force_day_delete:
        raise RuntimeError(
            f"day={job.source_date} latest_status={status}; rerun with --force-day-delete "
            "when retrying a failed/interrupted/started day so duplicate rows cannot be created."
        )
    if needs_force_delete_before_retry(status, args):
        later_days = later_built_day_count(client, args, job)
        if later_days > 0:
            raise RuntimeError(
                f"day={job.source_date} latest_status={status} is not complete, but {later_days:,} later "
                "continuity days already exist. Refusing out-of-order retry because deleting/rebuilding an "
                "older day would make later ordinals inconsistent. Rebuild from this day forward or repair "
                "the manifest only if event/continuity counts match."
            )
    if status in {"failed", "started", "interrupted"} and args.force_day_delete:
        print(f"DAY DELETE day={job.source_date} before retry", flush=True)
        run_profiled(client, f"delete_events_day_{job.source_date}", delete_day_sql(args, job))
        run_profiled(client, f"delete_continuity_day_{job.source_date}", delete_day_continuity_sql(args, job))
    insert_day_manifest(client, args, job, status="started", run_id=run_id)
    try:
        profile = run_profiled(client, f"build_events_day_{job.source_date}", insert_day_sql(args, job))
        continuity_profile = run_profiled(client, f"continuity_day_{job.source_date}", insert_day_continuity_sql(args, job))
        insert_day_manifest(client, args, job, status="ok", run_id=run_id, profile=profile)
        append_jsonl(
            report_path,
            {
                "type": "day",
                "job": asdict(job),
                "status": "ok",
                "profile": asdict(profile),
                "continuity_profile": asdict(continuity_profile),
            },
        )
        print_day_profile(job, profile, continuity_profile)
        return "ok"
    except KeyboardInterrupt:
        profile = QueryProfile(label=f"build_events_day_{job.source_date}", query_id="", wall_seconds=0.0, exception="KeyboardInterrupt")
        insert_day_manifest(client, args, job, status="interrupted", run_id=run_id, profile=profile, exception="KeyboardInterrupt")
        append_jsonl(report_path, {"type": "day", "job": asdict(job), "status": "interrupted", "exception": "KeyboardInterrupt"})
        print(f"DAY INTERRUPTED day={job.source_date}; manifest status set to interrupted", flush=True)
        raise
    except Exception as exc:  # noqa: BLE001
        profile = QueryProfile(label=f"build_events_day_{job.source_date}", query_id="", wall_seconds=0.0, exception=repr(exc))
        insert_day_manifest(client, args, job, status="failed", run_id=run_id, profile=profile, exception=repr(exc))
        append_jsonl(report_path, {"type": "day", "job": asdict(job), "status": "failed", "exception": repr(exc)})
        print(f"DAY FAILED day={job.source_date}: {exc!r}", flush=True)
        raise


def run_ticker(client: ClickHouseHttpClient, args: argparse.Namespace, job: TickerJob, run_id: str, report_path: Path) -> str:
    status = latest_ticker_status(client, args, job)
    if should_skip_status(status, args):
        print(f"SKIP ticker={job.ticker} latest_status={status}", flush=True)
        return "skipped"
    print("=" * 96, flush=True)
    print(f"TICKER START {job.ticker}", flush=True)
    if needs_force_delete_before_retry(status, args) and not args.force_ticker_delete:
        raise RuntimeError(
            f"ticker={job.ticker} latest_status={status}; rerun with --force-ticker-delete "
            "when retrying a failed/interrupted/started ticker so duplicate rows cannot be created."
        )
    if status in {"failed", "started", "interrupted"} and args.force_ticker_delete:
        print(f"TICKER DELETE ticker={job.ticker} before retry", flush=True)
        run_profiled(client, f"delete_events_ticker_{job.ticker}", delete_ticker_sql(args, job))
        delete_ticker_indexes(client, args, job)
    insert_manifest(client, args, job, status="started", run_id=run_id)
    try:
        profile = run_profiled(client, f"build_events_ticker_{job.ticker}", insert_ticker_sql(args, job))
        insert_ticker_indexes(client, args, job, report_path)
        insert_manifest(client, args, job, status="ok", run_id=run_id, profile=profile)
        append_jsonl(report_path, {"type": "ticker", "job": asdict(job), "status": "ok", "profile": asdict(profile)})
        print_ticker_profile(job, profile)
        return "ok"
    except KeyboardInterrupt:
        profile = QueryProfile(label=f"build_events_ticker_{job.ticker}", query_id="", wall_seconds=0.0, exception="KeyboardInterrupt")
        insert_manifest(client, args, job, status="interrupted", run_id=run_id, profile=profile, exception="KeyboardInterrupt")
        append_jsonl(report_path, {"type": "ticker", "job": asdict(job), "status": "interrupted", "exception": "KeyboardInterrupt"})
        print(f"TICKER INTERRUPTED ticker={job.ticker}; manifest status set to interrupted", flush=True)
        raise
    except Exception as exc:  # noqa: BLE001
        profile = QueryProfile(label=f"build_events_ticker_{job.ticker}", query_id="", wall_seconds=0.0, exception=repr(exc))
        insert_manifest(client, args, job, status="failed", run_id=run_id, profile=profile, exception=repr(exc))
        append_jsonl(report_path, {"type": "ticker", "job": asdict(job), "status": "failed", "exception": repr(exc)})
        print(f"TICKER FAILED ticker={job.ticker}: {exc!r}", flush=True)
        raise


def summarize_index(client: ClickHouseHttpClient, args: argparse.Namespace, table: str) -> dict[str, int]:
    row = client.query_tsv(
        f"""
SELECT
    count(),
    if(count() = 0, 0, sum(valid_origin_count)),
    if(count() = 0, 0, min(valid_origin_count)),
    if(count() = 0, 0, max(valid_origin_count))
FROM {quote_ident(args.database)}.{quote_ident(table)}
"""
    ).strip()
    parts = row.split("\t") if row else ["0", "0", "0", "0"]
    return {
        "tickers": int(parts[0] or 0),
        "valid_origins": int(parts[1] or 0),
        "min_origins_per_ticker": int(parts[2] or 0),
        "max_origins_per_ticker": int(parts[3] or 0),
    }


def print_ticker_profile(job: TickerJob, profile: QueryProfile) -> None:
    memory_gib = None if profile.memory_usage_bytes is None else profile.memory_usage_bytes / (1024**3)
    rows_per_second = None
    if profile.written_rows and profile.wall_seconds > 0:
        rows_per_second = profile.written_rows / profile.wall_seconds
    rows_per_second_text = "unknown" if rows_per_second is None else f"{round(rows_per_second):,}"
    print(
        "TICKER OK "
        f"ticker={job.ticker} wall_seconds={profile.wall_seconds:.1f} "
        f"query_ms={profile.query_duration_ms} memory_gib={None if memory_gib is None else round(memory_gib, 3)} "
        f"read_rows={profile.read_rows} written_rows={profile.written_rows} "
        f"rows_per_sec={rows_per_second_text}",
        flush=True,
    )


def print_day_profile(job: DayJob, profile: QueryProfile, continuity_profile: QueryProfile) -> None:
    memory_gib = None if profile.memory_usage_bytes is None else profile.memory_usage_bytes / (1024**3)
    rows_per_second = None
    if profile.written_rows and profile.wall_seconds > 0:
        rows_per_second = profile.written_rows / profile.wall_seconds
    rows_per_second_text = "unknown" if rows_per_second is None else f"{round(rows_per_second):,}"
    print(
        "DAY OK "
        f"day={job.source_date} build_step={job.build_step} wall_seconds={profile.wall_seconds:.1f} "
        f"query_ms={profile.query_duration_ms} memory_gib={None if memory_gib is None else round(memory_gib, 3)} "
        f"read_rows={profile.read_rows} written_rows={profile.written_rows} rows_per_sec={rows_per_second_text} "
        f"continuity_written_rows={continuity_profile.written_rows}",
        flush=True,
    )


def format_duration(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    minutes, sec = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{sec:02d}s"
    if minutes:
        return f"{minutes}m{sec:02d}s"
    return f"{sec}s"


def print_progress(
    *,
    index: int,
    total: int,
    completed: int,
    skipped: int,
    failed: int,
    started_at: float,
    current_ticker: str,
) -> None:
    elapsed = time.perf_counter() - started_at
    done = completed + skipped + failed
    remaining = max(total - done, 0)
    rate = done / elapsed if done > 0 and elapsed > 0 else 0.0
    eta = remaining / rate if rate > 0 else 0.0
    pct = (done / total * 100.0) if total else 100.0
    print(
        "PROGRESS "
        f"ticker_step={index:,}/{total:,} current={current_ticker} "
        f"done={done:,} completed={completed:,} skipped={skipped:,} failed={failed:,} remaining={remaining:,} "
        f"pct={pct:.2f}% elapsed={format_duration(elapsed)} "
        f"rate={rate * 60.0:.2f}_tickers_per_min eta={format_duration(eta) if rate > 0 else 'unknown'}",
        flush=True,
    )


def print_day_progress(
    *,
    index: int,
    total: int,
    completed: int,
    skipped: int,
    failed: int,
    started_at: float,
    current_day: str,
) -> None:
    elapsed = time.perf_counter() - started_at
    done = completed + skipped + failed
    remaining = max(total - done, 0)
    rate = done / elapsed if done > 0 and elapsed > 0 else 0.0
    eta = remaining / rate if rate > 0 else 0.0
    pct = (done / total * 100.0) if total else 100.0
    print(
        "PROGRESS "
        f"day_step={index:,}/{total:,} current={current_day} "
        f"done={done:,} completed={completed:,} skipped={skipped:,} failed={failed:,} remaining={remaining:,} "
        f"pct={pct:.2f}% elapsed={format_duration(elapsed)} "
        f"rate={rate * 60.0:.2f}_days_per_min eta={format_duration(eta) if rate > 0 else 'unknown'}",
        flush=True,
    )


def append_jsonl(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def print_sql_preview(label: str, sql: str, *, limit: int = 3000) -> None:
    text = sql.strip()
    print(f"--- {label} SQL preview ---", flush=True)
    print(text[:limit] + ("\n..." if len(text) > limit else ""), flush=True)


def parse_tickers(text: str) -> list[str]:
    return sorted({item.strip().upper() for item in text.split(",") if item.strip()})


def read_ticker_file(path_text: str) -> list[str]:
    path = Path(path_text)
    if not path.exists():
        raise FileNotFoundError(path)
    return sorted({line.strip().upper() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()})


def discover_requested_tickers(args: argparse.Namespace) -> list[str]:
    if args.tickers.strip():
        return parse_tickers(args.tickers)
    if args.ticker_file.strip():
        return read_ticker_file(args.ticker_file)
    return []


def discover_tickers(client: ClickHouseHttpClient, args: argparse.Namespace) -> list[str]:
    if args.tickers.strip():
        return parse_tickers(args.tickers)
    if args.ticker_file.strip():
        return read_ticker_file(args.ticker_file)
    print("DISCOVER tickers from compact quote/trade tables", flush=True)
    rows = client.query_tsv(
        f"""
SELECT ticker
FROM
(
    SELECT ticker
    FROM {quote_ident(args.database)}.{quote_ident(args.quote_table)}
    WHERE event_date BETWEEN toDate({sql_string(args.source_start_date)}) AND toDate({sql_string(args.source_end_date)})
      AND ticker != ''
    GROUP BY ticker
    UNION DISTINCT
    SELECT ticker
    FROM {quote_ident(args.database)}.{quote_ident(args.trade_table)}
    WHERE event_date BETWEEN toDate({sql_string(args.source_start_date)}) AND toDate({sql_string(args.source_end_date)})
      AND ticker != ''
    GROUP BY ticker
)
ORDER BY ticker
{query_settings(args)}
"""
    ).strip()
    return [line.strip() for line in rows.splitlines() if line.strip()]


def discover_source_dates(client: ClickHouseHttpClient, args: argparse.Namespace) -> list[str]:
    ticker_filter_quote = ticker_filter_sql(args, "q")
    ticker_filter_trade = ticker_filter_sql(args, "t")
    print("DISCOVER source dates from compact quote/trade tables", flush=True)
    rows = client.query_tsv(
        f"""
SELECT event_date
FROM
(
    SELECT q.event_date AS event_date
    FROM {quote_ident(args.database)}.{quote_ident(args.quote_table)} AS q
    WHERE q.event_date BETWEEN toDate({sql_string(args.source_start_date)}) AND toDate({sql_string(args.source_end_date)})
      {ticker_filter_quote}
    GROUP BY q.event_date
    UNION DISTINCT
    SELECT t.event_date AS event_date
    FROM {quote_ident(args.database)}.{quote_ident(args.trade_table)} AS t
    WHERE t.event_date BETWEEN toDate({sql_string(args.source_start_date)}) AND toDate({sql_string(args.source_end_date)})
      {ticker_filter_trade}
    GROUP BY t.event_date
)
ORDER BY event_date
{query_settings(args)}
"""
    ).strip()
    return [line.strip() for line in rows.splitlines() if line.strip()]


def selected_day_jobs(source_dates: list[str], args: argparse.Namespace) -> list[DayJob]:
    offset = int(args.day_offset)
    selected = source_dates[offset:]
    if args.limit_days > 0:
        selected = selected[: int(args.limit_days)]
    return [DayJob(source_date=value, build_step=offset + index) for index, value in enumerate(selected, start=1)]


def selected_ticker_jobs(tickers: list[str], args: argparse.Namespace) -> list[TickerJob]:
    selected = tickers[int(args.ticker_offset) :]
    if args.limit_tickers > 0:
        selected = selected[: int(args.limit_tickers)]
    return [TickerJob(ticker) for ticker in selected]


def validate_args(args: argparse.Namespace) -> None:
    if args.partition_mode == "ticker_hash" and args.partition_buckets <= 0:
        raise SystemExit("--partition-buckets must be positive")
    if args.ticker_offset < 0:
        raise SystemExit("--ticker-offset must be non-negative")
    if args.day_offset < 0:
        raise SystemExit("--day-offset must be non-negative")
    if args.events_per_chunk < 2:
        raise SystemExit("--events-per-chunk must be >= 2")


def drop_table_for_rebuild_sql(args: argparse.Namespace, table: str) -> str:
    return f"""
DROP TABLE IF EXISTS {quote_ident(args.database)}.{quote_ident(table)} SYNC
SETTINGS max_table_size_to_drop = 0, max_partition_size_to_drop = 0
"""


def initialize_tables(client: ClickHouseHttpClient, args: argparse.Namespace) -> None:
    db = quote_ident(args.database)
    client.execute(f"CREATE DATABASE IF NOT EXISTS {db}")
    if args.rebuild:
        for table in (args.events_table, args.manifest_table, args.continuity_table, args.train_index_table, args.validation_index_table):
            print(f"DROP FOR REBUILD {args.database}.{table}", flush=True)
            client.execute(drop_table_for_rebuild_sql(args, table))
            print(f"DROPPED {args.database}.{table}", flush=True)
    client.execute(create_events_table_sql(args))
    client.execute(create_manifest_table_sql(args))
    client.execute(create_continuity_table_sql(args))
    ensure_continuity_table_columns(client, args)
    client.execute(create_index_table_sql(args, args.train_index_table))
    client.execute(create_index_table_sql(args, args.validation_index_table))


def table_count(client: ClickHouseHttpClient, args: argparse.Namespace, table: str) -> int:
    try:
        value = client.query_tsv(f"SELECT count() FROM {quote_ident(args.database)}.{quote_ident(table)}").strip()
        return int(value or 0)
    except Exception:
        return 0


def validate_daily_resume_state(client: ClickHouseHttpClient, args: argparse.Namespace) -> None:
    if args.rebuild or args.no_build_events:
        return
    event_count = table_count(client, args, args.events_table)
    continuity_count = table_count(client, args, args.continuity_table)
    if event_count > 0 and continuity_count == 0:
        raise RuntimeError(
            f"{args.database}.{args.events_table} already has {event_count:,} rows, but "
            f"{args.database}.{args.continuity_table} is empty. This looks like an old pre-continuity build. "
            "Run with --rebuild to avoid duplicate or corrupted ordinal rows."
        )


def main() -> None:
    loaded_env_files = load_env_files(discover_clickhouse_env_files(), verbose=True)
    args = parse_args()
    validate_args(args)
    output_root = Path(args.output_root_win)
    output_root.mkdir(parents=True, exist_ok=True)
    run_id = "unified_events_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = output_root / f"{run_id}.jsonl"

    client = None if args.dry_run else ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    if args.dry_run:
        source_dates = [args.source_start_date]
    else:
        assert client is not None
        source_dates = discover_source_dates(client, args)
    jobs = selected_day_jobs(source_dates, args)

    print("=" * 96, flush=True)
    print("Unified ClickHouse event table builder", flush=True)
    print(f"database={args.database} events_table={args.events_table} manifest_table={args.manifest_table} continuity_table={args.continuity_table}", flush=True)
    print(f"source_range={args.source_start_date}->{args.source_end_date}", flush=True)
    print(f"day_jobs={len(jobs):,} day_offset={args.day_offset} limit_days={args.limit_days}", flush=True)
    print(f"ticker_filter={discover_requested_tickers(args)[:10] or '<all>'}", flush=True)
    print(f"preview_days={[job.source_date for job in jobs[:5]]}", flush=True)
    print(
        f"partition_mode={args.partition_mode} partition_buckets={args.partition_buckets} "
        f"storage_policy={args.storage_policy or '<default>'}",
        flush=True,
    )
    print(f"settings={query_settings(args).strip() or '<none>'}", flush=True)
    print(f"clean_mode={args.clean_mode} events_per_chunk={args.events_per_chunk}", flush=True)
    print(f"build_events={not args.no_build_events} build_index={not args.no_build_index} rebuild={args.rebuild} dry_run={args.dry_run}", flush=True)
    print(f"secret_status={secret_status(env_status_keys() + ['CLICKHOUSE_LIVE_STORAGE_POLICY'])}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print(f"report={report_path}", flush=True)
    print("=" * 96, flush=True)

    if args.dry_run:
        print_sql_preview("create_events", create_events_table_sql(args))
        print_sql_preview("create_manifest", create_manifest_table_sql(args))
        print_sql_preview("create_continuity", create_continuity_table_sql(args))
        if jobs:
            print_sql_preview("insert_day", insert_day_sql(args, jobs[0]))
            print_sql_preview("insert_day_continuity", insert_day_continuity_sql(args, jobs[0]))
            if not args.no_build_index:
                print_sql_preview("insert_train_index", insert_split_index_sql(args, IndexJob(args.train_index_table, args.train_start_date, args.train_end_date)))
        return

    assert client is not None
    initialize_tables(client, args)
    validate_daily_resume_state(client, args)
    append_jsonl(
        report_path,
        {
            "type": "run_start",
            "run_id": run_id,
            "args": vars(args),
            "day_count": len(jobs),
        },
    )

    started = time.perf_counter()
    completed = skipped = failed = 0
    try:
        if not args.no_build_events:
            for index, job in enumerate(jobs, start=1):
                print_day_progress(
                    index=index,
                    total=len(jobs),
                    completed=completed,
                    skipped=skipped,
                    failed=failed,
                    started_at=started,
                    current_day=job.source_date,
                )
                try:
                    result = run_day(client, args, job, run_id, report_path)
                    if result == "skipped":
                        skipped += 1
                    else:
                        completed += 1
                except KeyboardInterrupt:
                    raise
                except Exception:
                    failed += 1
                    raise
                print_day_progress(
                    index=index,
                    total=len(jobs),
                    completed=completed,
                    skipped=skipped,
                    failed=failed,
                    started_at=started,
                    current_day=job.source_date,
                )

        insert_split_indexes(client, args, report_path)

        if args.optimize_final:
            run_profiled(client, f"optimize_{args.events_table}_final", f"OPTIMIZE TABLE {quote_ident(args.database)}.{quote_ident(args.events_table)} FINAL")

    except KeyboardInterrupt:
        elapsed = time.perf_counter() - started
        append_jsonl(
            report_path,
            {
                "type": "run_interrupted",
                "elapsed_seconds": elapsed,
                "completed": completed,
                "skipped": skipped,
                "failed": failed,
            },
        )
        print("=" * 96, flush=True)
        print(
            "INTERRUPTED "
            f"elapsed={format_duration(elapsed)} completed={completed:,} skipped={skipped:,} failed={failed:,}. "
            "The active day was marked interrupted when possible. Resume with --retry-started --force-day-delete.",
            flush=True,
        )
        print("=" * 96, flush=True)
        raise SystemExit(130)

    elapsed = time.perf_counter() - started
    append_jsonl(report_path, {"type": "run_done", "elapsed_seconds": elapsed, "completed": completed, "skipped": skipped, "failed": failed})
    print("=" * 96, flush=True)
    print(f"DONE elapsed={format_duration(elapsed)} completed={completed:,} skipped={skipped:,} failed={failed:,}", flush=True)
    print("=" * 96, flush=True)


if __name__ == "__main__":
    main()
