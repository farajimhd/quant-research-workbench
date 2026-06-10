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
DEFAULT_TRAIN_INDEX_TABLE = "train_2019_to_2025"
DEFAULT_VALIDATION_INDEX_TABLE = "validation_2026"
DEFAULT_SOURCE_START_DATE = "2019-01-01"
DEFAULT_SOURCE_END_DATE = "2099-12-31"
DEFAULT_TRAIN_START_DATE = "2019-01-01"
DEFAULT_TRAIN_END_DATE = "2025-12-31"
DEFAULT_VALIDATION_START_DATE = "2026-01-01"
DEFAULT_VALIDATION_END_DATE = "2099-12-31"
DEFAULT_PARTITION_BUCKETS = 256
DEFAULT_EVENTS_PER_CHUNK = 128


@dataclass(frozen=True, slots=True)
class TickerJob:
    ticker: str


@dataclass(frozen=True, slots=True)
class IndexJob:
    table: str
    start_date: str
    end_date: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the final unified ClickHouse event table from compact SIP quotes/trades, one ticker at a time."
    )
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url_with_network_fallback())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--quote-table", default="quotes")
    parser.add_argument("--trade-table", default="trades")
    parser.add_argument("--events-table", default=DEFAULT_EVENTS_TABLE)
    parser.add_argument("--manifest-table", default=DEFAULT_MANIFEST_TABLE)
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
    parser.add_argument("--tickers", default="", help="Optional comma-separated ticker subset. Empty means discover all tickers.")
    parser.add_argument("--ticker-file", default="", help="Optional newline-delimited ticker list.")
    parser.add_argument("--ticker-offset", type=int, default=0)
    parser.add_argument("--limit-tickers", type=int, default=0)
    parser.add_argument("--storage-policy", default=default_live_storage_policy())
    parser.add_argument("--max-memory-usage", default="400G")
    parser.add_argument("--max-threads", type=int, default=32)
    parser.add_argument("--output-root-win", default=str(DEFAULT_OUTPUT_ROOT_WIN / "unified_events"))
    parser.add_argument("--clean-mode", choices=("issue_flags_zero", "structural"), default="issue_flags_zero")
    parser.add_argument("--rebuild", action="store_true", help="Drop event/index/manifest tables before building.")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--retry-started", action="store_true")
    parser.add_argument("--force-ticker-delete", action="store_true", help="Delete existing rows for a ticker before retrying it.")
    parser.add_argument("--no-build-events", action="store_true", help="Skip event table ticker inserts.")
    parser.add_argument("--no-build-index", action="store_true", help="Skip train/validation index table rebuild.")
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
    return "\nSETTINGS " + ", ".join(settings) if settings else ""


def mergetree_settings(storage_policy: str) -> str:
    settings = ["index_granularity = 8192"]
    if storage_policy.strip():
        settings.append(f"storage_policy = {sql_string(storage_policy.strip())}")
    return "SETTINGS " + ", ".join(settings)


def create_events_table_sql(args: argparse.Namespace) -> str:
    db = quote_ident(args.database)
    table = quote_ident(args.events_table)
    partition_buckets = int(args.partition_buckets)
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
PARTITION BY cityHash64(ticker) % {partition_buckets}
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


def create_index_table_sql(args: argparse.Namespace, table: str) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(args.database)}.{quote_ident(table)}
(
    ticker LowCardinality(String),
    event_count UInt64,
    first_sip_timestamp_us UInt64,
    last_sip_timestamp_us UInt64,
    min_valid_ordinal UInt64,
    max_valid_ordinal UInt64
)
ENGINE = MergeTree
ORDER BY ticker
{mergetree_settings(args.storage_policy)}
"""


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


def should_skip_status(status: str, args: argparse.Namespace) -> bool:
    if status == "ok":
        return True
    if status == "failed" and not args.retry_failed:
        return True
    if status == "started" and not args.retry_started:
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


def delete_ticker_sql(args: argparse.Namespace, job: TickerJob) -> str:
    return f"""
ALTER TABLE {quote_ident(args.database)}.{quote_ident(args.events_table)}
DELETE WHERE ticker = {sql_string(job.ticker)}
{query_settings(args)}
"""


def index_insert_sql(args: argparse.Namespace, job: IndexJob) -> str:
    valid = f"event_date BETWEEN toDate({sql_string(job.start_date)}) AND toDate({sql_string(job.end_date)}) AND ordinal >= {int(args.events_per_chunk - 1)}"
    return f"""
INSERT INTO {quote_ident(args.database)}.{quote_ident(job.table)}
SELECT
    ticker,
    countIf({valid}) AS event_count,
    minIf(sip_timestamp_us, {valid}) AS first_sip_timestamp_us,
    maxIf(sip_timestamp_us, {valid}) AS last_sip_timestamp_us,
    minIf(ordinal, {valid}) AS min_valid_ordinal,
    maxIf(ordinal, {valid}) AS max_valid_ordinal
FROM {quote_ident(args.database)}.{quote_ident(args.events_table)}
GROUP BY ticker
HAVING event_count > 0
{query_settings(args)}
"""


def run_ticker(client: ClickHouseHttpClient, args: argparse.Namespace, job: TickerJob, run_id: str, report_path: Path) -> str:
    status = latest_ticker_status(client, args, job)
    if should_skip_status(status, args):
        print(f"SKIP ticker={job.ticker} latest_status={status}", flush=True)
        return "skipped"
    print("=" * 96, flush=True)
    print(f"TICKER START {job.ticker}", flush=True)
    if status in {"failed", "started"} and args.force_ticker_delete:
        print(f"TICKER DELETE ticker={job.ticker} before retry", flush=True)
        run_profiled(client, f"delete_events_ticker_{job.ticker}", delete_ticker_sql(args, job))
    insert_manifest(client, args, job, status="started", run_id=run_id)
    try:
        profile = run_profiled(client, f"build_events_ticker_{job.ticker}", insert_ticker_sql(args, job))
        insert_manifest(client, args, job, status="ok", run_id=run_id, profile=profile)
        append_jsonl(report_path, {"type": "ticker", "job": asdict(job), "status": "ok", "profile": asdict(profile)})
        print_ticker_profile(job, profile)
        return "ok"
    except Exception as exc:  # noqa: BLE001
        profile = QueryProfile(label=f"build_events_ticker_{job.ticker}", query_id="", wall_seconds=0.0, exception=repr(exc))
        insert_manifest(client, args, job, status="failed", run_id=run_id, profile=profile, exception=repr(exc))
        append_jsonl(report_path, {"type": "ticker", "job": asdict(job), "status": "failed", "exception": repr(exc)})
        print(f"TICKER FAILED ticker={job.ticker}: {exc!r}", flush=True)
        raise


def build_indexes(client: ClickHouseHttpClient, args: argparse.Namespace, report_path: Path) -> None:
    jobs = [
        IndexJob(args.train_index_table, args.train_start_date, args.train_end_date),
        IndexJob(args.validation_index_table, args.validation_start_date, args.validation_end_date),
    ]
    for job in jobs:
        print("=" * 96, flush=True)
        print(f"INDEX START table={job.table} range={job.start_date}->{job.end_date}", flush=True)
        client.execute(create_index_table_sql(args, job.table))
        client.execute(f"TRUNCATE TABLE {quote_ident(args.database)}.{quote_ident(job.table)}")
        profile = run_profiled(client, f"build_event_index_{job.table}", index_insert_sql(args, job))
        summary = summarize_index(client, args, job.table)
        append_jsonl(report_path, {"type": "index", "job": asdict(job), "profile": asdict(profile), "summary": summary})
        print(f"INDEX DONE table={job.table} summary={summary}", flush=True)


def summarize_index(client: ClickHouseHttpClient, args: argparse.Namespace, table: str) -> dict[str, int]:
    row = client.query_tsv(
        f"""
SELECT
    count(),
    if(count() = 0, 0, sum(event_count)),
    if(count() = 0, 0, min(event_count)),
    if(count() = 0, 0, max(event_count))
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


def selected_ticker_jobs(tickers: list[str], args: argparse.Namespace) -> list[TickerJob]:
    selected = tickers[int(args.ticker_offset) :]
    if args.limit_tickers > 0:
        selected = selected[: int(args.limit_tickers)]
    return [TickerJob(ticker) for ticker in selected]


def validate_args(args: argparse.Namespace) -> None:
    if args.partition_buckets <= 0:
        raise SystemExit("--partition-buckets must be positive")
    if args.ticker_offset < 0:
        raise SystemExit("--ticker-offset must be non-negative")
    if args.events_per_chunk < 2:
        raise SystemExit("--events-per-chunk must be >= 2")


def initialize_tables(client: ClickHouseHttpClient, args: argparse.Namespace) -> None:
    db = quote_ident(args.database)
    client.execute(f"CREATE DATABASE IF NOT EXISTS {db}")
    if args.rebuild:
        for table in (args.events_table, args.manifest_table, args.train_index_table, args.validation_index_table):
            client.execute(f"DROP TABLE IF EXISTS {db}.{quote_ident(table)} SYNC")
            print(f"DROPPED {args.database}.{table}", flush=True)
    client.execute(create_events_table_sql(args))
    client.execute(create_manifest_table_sql(args))
    client.execute(create_index_table_sql(args, args.train_index_table))
    client.execute(create_index_table_sql(args, args.validation_index_table))


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
        tickers = parse_tickers(args.tickers) if args.tickers.strip() else ["AAPL"]
    else:
        assert client is not None
        tickers = discover_tickers(client, args)
    jobs = selected_ticker_jobs(tickers, args)

    print("=" * 96, flush=True)
    print("Unified ClickHouse event table builder", flush=True)
    print(f"database={args.database} events_table={args.events_table} manifest_table={args.manifest_table}", flush=True)
    print(f"source_range={args.source_start_date}->{args.source_end_date}", flush=True)
    print(f"ticker_jobs={len(jobs):,} ticker_offset={args.ticker_offset} limit_tickers={args.limit_tickers}", flush=True)
    print(f"preview_tickers={[job.ticker for job in jobs[:5]]}", flush=True)
    print(f"partition_buckets={args.partition_buckets} storage_policy={args.storage_policy or '<default>'}", flush=True)
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
        if jobs:
            print_sql_preview("insert_ticker", insert_ticker_sql(args, jobs[0]))
        if not args.no_build_index:
            print_sql_preview("insert_train_index", index_insert_sql(args, IndexJob(args.train_index_table, args.train_start_date, args.train_end_date)))
        return

    assert client is not None
    initialize_tables(client, args)
    append_jsonl(
        report_path,
        {
            "type": "run_start",
            "run_id": run_id,
            "args": vars(args),
            "ticker_count": len(jobs),
        },
    )

    started = time.perf_counter()
    completed = skipped = failed = 0
    if not args.no_build_events:
        for index, job in enumerate(jobs, start=1):
            print(f"PROGRESS ticker_step={index:,}/{len(jobs):,} completed={completed:,} skipped={skipped:,} failed={failed:,}", flush=True)
            try:
                result = run_ticker(client, args, job, run_id, report_path)
                if result == "skipped":
                    skipped += 1
                else:
                    completed += 1
            except Exception:
                failed += 1
                raise

    if not args.no_build_index:
        build_indexes(client, args, report_path)

    if args.optimize_final:
        run_profiled(client, f"optimize_{args.events_table}_final", f"OPTIMIZE TABLE {quote_ident(args.database)}.{quote_ident(args.events_table)} FINAL")

    elapsed = time.perf_counter() - started
    append_jsonl(report_path, {"type": "run_done", "elapsed_seconds": elapsed, "completed": completed, "skipped": skipped, "failed": failed})
    print("=" * 96, flush=True)
    print(f"DONE elapsed_minutes={elapsed / 60.0:.1f} completed={completed:,} skipped={skipped:,} failed={failed:,}", flush=True)
    print("=" * 96, flush=True)


if __name__ == "__main__":
    main()
