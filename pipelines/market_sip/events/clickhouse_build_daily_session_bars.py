from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.market_sip.events.clickhouse_build_unified_events import (  # noqa: E402
    events_table_for_year,
    events_table_uses_year_suffix,
)
from pipelines.market_sip.events.session_bar_contract import (  # noqa: E402
    DEFAULT_DAILY_SESSION_BARS_TABLE,
    DEFAULT_DAILY_SESSION_MANIFEST_TABLE,
    FEATURE_NAMES,
    SESSION_BAR_FEATURE_VERSION,
    SESSION_BAR_SCHEMA_VERSION,
    session_table_columns,
)
from pipelines.market_sip.validation.clickhouse_delete_compact_audit_rows import (  # noqa: E402
    default_clickhouse_url_with_network_fallback,
)
from research.mlops.clickhouse import (  # noqa: E402
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_user,
    discover_clickhouse_env_files,
    mergetree_settings_sql,
    quote_ident,
    sql_string,
)
from research.mlops.env import load_env_files  # noqa: E402


BUILD_VERSION = "sip_daily_sessions_v1"
DEFAULT_DATABASE = "market_sip_compact"
DEFAULT_EVENTS_TABLE_BASE = "events"
DEFAULT_IDENTITY_DATABASE = "q_live"
DEFAULT_RUNTIME_ROOT = Path(r"D:\TradingML\runtimes\market_sip\daily_session_bars_v1")
SESSION_TIMEZONE = "America/New_York"


@dataclass(frozen=True, slots=True)
class ChunkResult:
    start: dt.date
    end: dt.date
    rows: int
    source_events: int
    mapped_rows: int
    unmapped_rows: int
    seconds: float


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build certified three-session SIP event-geometry bars.")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--start-date", default="2019-01-01")
    parser.add_argument("--end-date", default="auto", help="Exclusive New York date; auto uses event-index coverage.")
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--events-table-base", default=DEFAULT_EVENTS_TABLE_BASE)
    parser.add_argument("--index-table", default="events_ticker_day_index")
    parser.add_argument("--tickers", default="", help="Optional comma-separated source-ticker restriction.")
    parser.add_argument("--target-table", default=DEFAULT_DAILY_SESSION_BARS_TABLE)
    parser.add_argument("--manifest-table", default=DEFAULT_DAILY_SESSION_MANIFEST_TABLE)
    parser.add_argument("--identity-database", default=DEFAULT_IDENTITY_DATABASE)
    parser.add_argument("--symbol-interval-table", default="id_symbol_interval_v1")
    parser.add_argument("--ticker-entity-table", default="market_ticker_event_entity_v1")
    parser.add_argument("--storage-policy", default=os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY", ""))
    parser.add_argument("--allow-empty-storage-policy", action="store_true")
    parser.add_argument("--chunk-days", type=int, default=7)
    parser.add_argument("--replace-range", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--verify-source-count",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Run a second raw-event scan per chunk for independent source-count reconciliation.",
    )
    parser.add_argument("--max-threads", type=int, default=16)
    parser.add_argument("--max-memory-usage", default="96G")
    parser.add_argument("--max-bytes-before-external-group-by", default="24G")
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url_with_network_fallback())
    parser.add_argument("--clickhouse-user", default=default_clickhouse_user())
    parser.add_argument("--clickhouse-password", default=default_clickhouse_password())
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text", "none"), default="auto")
    return parser.parse_args(argv)


def _size_literal(value: str) -> int:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([KMGTP]?)(?:i?B)?\s*", str(value), re.IGNORECASE)
    if not match:
        raise ValueError(f"invalid byte size {value!r}")
    scale = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4, "P": 1024**5}
    return int(float(match.group(1)) * scale[match.group(2).upper()])


def query_settings(args: argparse.Namespace, *, mutation: bool = False) -> str:
    values = {
        "max_threads": max(1, int(args.max_threads)),
        "max_memory_usage": _size_literal(args.max_memory_usage),
        "max_bytes_before_external_group_by": _size_literal(args.max_bytes_before_external_group_by),
        "max_execution_time": 0,
        "log_queries": 1,
        "join_use_nulls": 0,
    }
    if mutation:
        values["mutations_sync"] = 2
    return "\nSETTINGS " + ", ".join(f"{key} = {value}" for key, value in values.items())


def create_target_table_sql(args: argparse.Namespace) -> str:
    columns = ",\n    ".join(f"{quote_ident(name)} {kind}" for name, kind in session_table_columns())
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(args.database)}.{quote_ident(args.target_table)}
(
    {columns}
)
ENGINE = ReplacingMergeTree(built_at)
PARTITION BY toYYYYMM(session_date)
ORDER BY (source_ticker, session_date, session_kind)
{mergetree_settings_sql(args.storage_policy)}
"""


def create_manifest_table_sql(args: argparse.Namespace) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(args.database)}.{quote_ident(args.manifest_table)}
(
    artifact_name LowCardinality(String),
    unit_id String,
    chunk_start Date,
    chunk_end Date,
    status LowCardinality(String),
    build_version LowCardinality(String),
    feature_version LowCardinality(String),
    output_row_count UInt64,
    source_event_count UInt64,
    mapped_row_count UInt64,
    unmapped_row_count UInt64,
    message String,
    updated_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at)
PARTITION BY toYYYYMM(chunk_start)
ORDER BY (artifact_name, unit_id)
{mergetree_settings_sql(args.storage_policy)}
"""


def _event_source(args: argparse.Namespace, start: dt.date, end: dt.date) -> str:
    years = list(range(start.year, (end - dt.timedelta(days=1)).year + 1))
    tables = [events_table_for_year(args.events_table_base, year) for year in years]
    if not events_table_uses_year_suffix(args.events_table_base):
        return f"{quote_ident(args.database)}.{quote_ident(args.events_table_base)}"
    if len(tables) == 1:
        return f"{quote_ident(args.database)}.{quote_ident(tables[0])}"
    pattern = "^(" + "|".join(re.escape(table) for table in tables) + ")$"
    return f"merge({sql_string(args.database)}, {sql_string(pattern)})"


def _ohlc(prefix: str, value: str, condition: str) -> list[str]:
    order = "tuple(sip_timestamp_us, ordinal)"
    return [
        f"toFloat32(argMinIf({value}, {order}, {condition})) AS {quote_ident(prefix + '_open')}",
        f"toFloat32(maxIf({value}, {condition})) AS {quote_ident(prefix + '_high')}",
        f"toFloat32(minIf({value}, {condition})) AS {quote_ident(prefix + '_low')}",
        f"toFloat32(argMaxIf({value}, {order}, {condition})) AS {quote_ident(prefix + '_close')}",
    ]


def _family_aggregates(prefix: str, price: str, size: str, condition: str) -> list[str]:
    order = "tuple(sip_timestamp_us, ordinal)"
    return [
        f"toUInt8(countIf({condition}) > 0) AS {quote_ident(prefix + '_present')}",
        *_ohlc(prefix, price, condition),
        f"toFloat64(sumIf({size}, {condition})) AS {quote_ident(prefix + '_size_sum')}",
        f"toFloat64(argMinIf({size}, {order}, {condition})) AS {quote_ident(prefix + '_size_open')}",
        f"toFloat64(maxIf({size}, {condition})) AS {quote_ident(prefix + '_size_high')}",
        f"toFloat64(minIf({size}, {condition})) AS {quote_ident(prefix + '_size_low')}",
        f"toFloat64(argMaxIf({size}, {order}, {condition})) AS {quote_ident(prefix + '_size_close')}",
        f"toFloat64(sumIf({size} * {size}, {condition})) AS {quote_ident(prefix + '_size_squared_sum')}",
        f"toFloat64(sumIf({price} * {size}, {condition})) AS {quote_ident(prefix + '_price_size_sum')}",
        f"toUInt64(countIf({condition})) AS {quote_ident(prefix + '_event_count')}",
    ]


def _relation_aggregates(prefix: str, value: str, condition: str) -> list[str]:
    return [
        *_ohlc(prefix, value, condition),
        f"toFloat64(sumIf({value}, {condition})) AS {quote_ident(prefix + '_sum')}",
        f"toFloat64(sumIf({value} * {value}, {condition})) AS {quote_ident(prefix + '_squared_sum')}",
    ]


def insert_session_bars_sql(args: argparse.Namespace, start: dt.date, end: dt.date) -> str:
    source = _event_source(args, start, end)
    target = f"{quote_ident(args.database)}.{quote_ident(args.target_table)}"
    trade_valid = "event_type = 1 AND trade_price > 0 AND trade_size > 0"
    bid_valid = "event_type = 0 AND bid_price > 0 AND bid_size > 0"
    ask_valid = "event_type = 0 AND ask_price > 0 AND ask_size > 0"
    pair_valid = "event_type = 0 AND bid_price > 0 AND ask_price > 0 AND bid_size > 0 AND ask_size > 0 AND bid_price <= ask_price"
    aggregates = [
        *_family_aggregates("trade", "trade_price", "trade_size", trade_valid),
        *_family_aggregates("bid", "bid_price", "bid_size", bid_valid),
        *_family_aggregates("ask", "ask_price", "ask_size", ask_valid),
        f"toUInt8(countIf({pair_valid}) > 0) AS quote_pair_present",
        f"toUInt64(countIf({pair_valid})) AS quote_pair_count",
        *_relation_aggregates("spread", "spread", pair_valid),
        *_relation_aggregates("midpoint", "midpoint", pair_valid),
        *_relation_aggregates("microprice", "microprice", pair_valid),
        *_relation_aggregates("queue_imbalance", "queue_imbalance", pair_valid),
        "toUInt64(countIf(event_type = 0 AND bid_price = ask_price AND bid_price > 0)) AS locked_quote_count",
        "toUInt64(countIf(event_type = 0 AND bid_price > ask_price AND ask_price > 0)) AS crossed_quote_count",
        "toUInt64(sum(toUInt8(condition_token_1 > 0) + toUInt8(condition_token_2 > 0) + toUInt8(condition_token_3 > 0) + toUInt8(condition_token_4 > 0) + toUInt8(condition_token_5 > 0))) AS condition_nonzero_count",
        "toUInt64(count()) AS source_event_count",
    ]
    aggregate_sql = ",\n        ".join(aggregates)
    feature_select = ",\n    ".join(f"a.{quote_ident(name)}" for name in FEATURE_NAMES)
    columns = ",\n    ".join(quote_ident(name) for name, _ in session_table_columns())
    identity_db = quote_ident(args.identity_database)
    requested = tuple(sorted({item.strip().upper() for item in str(getattr(args, "tickers", "")).split(",") if item.strip()}))
    ticker_filter = "" if not requested else " AND upper(ticker) IN (" + ", ".join(sql_string(item) for item in requested) + ")"
    return f"""
INSERT INTO {target}
(
    {columns}
)
WITH
events AS
(
    SELECT
        upper(ticker) AS source_ticker,
        ordinal,
        sip_timestamp_us,
        condition_token_1, condition_token_2, condition_token_3, condition_token_4, condition_token_5,
        toTimeZone(fromUnixTimestamp64Micro(sip_timestamp_us, 'UTC'), {sql_string(SESSION_TIMEZONE)}) AS ts_local,
        toDate(ts_local) AS session_date,
        dateDiff('second', toStartOfDay(ts_local), ts_local) AS local_second,
        multiIf(local_second < 34200, 'premarket', local_second < 57600, 'regular', 'after_hours') AS session_kind,
        bitAnd(event_meta, 1) AS event_type,
        toFloat64(if(price_primary_int > 0, price_primary_int / if(bitAnd(event_meta, 2) = 2, 10000.0, 100.0), 0.0)) AS primary_price,
        toFloat64(if(price_secondary_int > 0, price_secondary_int / if(bitAnd(event_meta, 4) = 4, 10000.0, 100.0), 0.0)) AS secondary_price,
        if(event_type = 1, primary_price, 0.0) AS trade_price,
        if(event_type = 1, toFloat64(size_primary), 0.0) AS trade_size,
        if(event_type = 0, primary_price, 0.0) AS ask_price,
        if(event_type = 0, secondary_price, 0.0) AS bid_price,
        if(event_type = 0, toFloat64(size_primary), 0.0) AS ask_size,
        if(event_type = 0, toFloat64(size_secondary), 0.0) AS bid_size,
        ask_price - bid_price AS spread,
        (ask_price + bid_price) / 2.0 AS midpoint,
        if(ask_size + bid_size > 0, (ask_price * bid_size + bid_price * ask_size) / (ask_size + bid_size), 0.0) AS microprice,
        if(ask_size + bid_size > 0, (bid_size - ask_size) / (bid_size + ask_size), 0.0) AS queue_imbalance
    FROM {source}
    PREWHERE event_date >= toDate({sql_string(start.isoformat())})
      AND event_date <= toDate({sql_string(end.isoformat())})
    WHERE ticker != '' AND sip_timestamp_us > 0{ticker_filter}
      AND session_date >= toDate({sql_string(start.isoformat())})
      AND session_date < toDate({sql_string(end.isoformat())})
      AND local_second >= 14400 AND local_second < 72000
),
aggregated AS
(
    SELECT
        session_date,
        session_kind,
        source_ticker,
        min(toUInt64(ordinal)) AS source_first_ordinal,
        max(toUInt64(ordinal)) AS source_last_ordinal,
        min(toUInt64(sip_timestamp_us)) AS source_first_timestamp_us,
        max(toUInt64(sip_timestamp_us)) AS source_last_timestamp_us,
        {aggregate_sql}
    FROM events
    GROUP BY session_date, session_kind, source_ticker
),
active_symbols AS
(
    SELECT DISTINCT session_date, source_ticker FROM events
),
session_grid AS
(
    SELECT session_date, source_ticker, session_kind
    FROM active_symbols
    ARRAY JOIN ['premarket', 'regular', 'after_hours'] AS session_kind
),
intervals AS
(
    SELECT provider_entity_key, security_id, listing_id, ticker_normalized,
           valid_from_date, valid_to_date_exclusive, is_current, observed_at_utc
    FROM {identity_db}.{quote_ident(args.symbol_interval_table)} FINAL
    WHERE is_deleted = 0 AND mapping_status = 'mapped'
),
identity_starts AS
(
    SELECT ticker_normalized, valid_from_date,
           if(uniqExact(provider_entity_key)=1 OR countIf(is_current=1)=1, 1, uniqExact(provider_entity_key)) AS resolved_count,
           argMax(provider_entity_key, tuple(is_current,observed_at_utc,provider_entity_key)) AS resolved_provider_entity_key,
           argMax(security_id, tuple(is_current,observed_at_utc,provider_entity_key)) AS resolved_security_id,
           argMax(listing_id, tuple(is_current,observed_at_utc,provider_entity_key)) AS resolved_listing_id,
           argMax(valid_to_date_exclusive, tuple(is_current,observed_at_utc,provider_entity_key)) AS resolved_valid_to_date_exclusive
    FROM intervals
    GROUP BY ticker_normalized,valid_from_date
),
identity_matches AS
(
    SELECT a.session_date, a.source_ticker,
           if(i.valid_from_date<=a.session_date AND (i.resolved_valid_to_date_exclusive IS NULL OR a.session_date<i.resolved_valid_to_date_exclusive),
              i.resolved_count, 0) AS match_count,
           if(match_count>0, i.resolved_provider_entity_key, '') AS provider_entity_key,
           if(match_count>0, i.resolved_security_id, '') AS security_id,
           if(match_count>0, i.resolved_listing_id, '') AS listing_id
    FROM active_symbols AS a
    ASOF LEFT JOIN identity_starts AS i
      ON i.ticker_normalized=a.source_ticker AND a.session_date>=i.valid_from_date
),
entities AS
(
    SELECT provider_entity_key, current_ticker
    FROM {identity_db}.{quote_ident(args.ticker_entity_table)} FINAL
    WHERE is_deleted = 0
)
SELECT
    toUInt16({SESSION_BAR_SCHEMA_VERSION}) AS schema_version,
    {sql_string(SESSION_BAR_FEATURE_VERSION)} AS feature_version,
    g.session_date,
    g.session_kind,
    g.source_ticker,
    if(m.match_count=1, nullIf(e.current_ticker, ''), CAST(NULL, 'Nullable(String)')) AS canonical_ticker,
    if(m.match_count=1, nullIf(m.security_id, ''), CAST(NULL, 'Nullable(String)')) AS security_id,
    if(m.match_count=1, nullIf(m.listing_id, ''), CAST(NULL, 'Nullable(String)')) AS listing_id,
    multiIf(m.match_count=0, 'unmapped_source_ticker', m.match_count=1, 'mapped', 'ambiguous_source_ticker') AS identity_status,
    'ordered_sip_events_unadjusted' AS source_contract,
    toUInt8(0) AS adjusted,
    CAST(NULL, 'Nullable(Date)') AS adjustment_asof_date,
    CAST(NULL, 'Nullable(String)') AS split_schedule_sha256,
    toUInt64(toUnixTimestamp64Micro(toTimeZone(toDateTime64(concat(toString(g.session_date), multiIf(g.session_kind='premarket',' 04:00:00',g.session_kind='regular',' 09:30:00',' 16:00:00')), 6, {sql_string(SESSION_TIMEZONE)}), 'UTC'))) AS bar_start_us,
    toUInt64(toUnixTimestamp64Micro(toTimeZone(toDateTime64(concat(toString(g.session_date), multiIf(g.session_kind='premarket',' 09:30:00',g.session_kind='regular',' 16:00:00',' 20:00:00')), 6, {sql_string(SESSION_TIMEZONE)}), 'UTC'))) AS bar_end_us,
    bar_end_us AS available_at_us,
    a.source_first_ordinal,
    a.source_last_ordinal,
    a.source_first_timestamp_us,
    a.source_last_timestamp_us,
    {feature_select},
    now64(3, 'UTC') AS built_at
FROM session_grid AS g
ANY LEFT JOIN aggregated AS a
    ON a.source_ticker = g.source_ticker AND a.session_date = g.session_date AND a.session_kind = g.session_kind
ANY LEFT JOIN identity_matches AS m
    ON m.source_ticker = g.source_ticker AND m.session_date = g.session_date
ANY LEFT JOIN entities AS e ON e.provider_entity_key = m.provider_entity_key AND m.match_count=1
{query_settings(args)}
"""


def _query_rows(client: ClickHouseHttpClient, sql: str) -> list[list[str]]:
    text = client.execute(sql.strip().rstrip(";") + "\nFORMAT TSVRaw")
    return [line.split("\t") for line in text.splitlines() if line]


def validate_preflight(client: ClickHouseHttpClient, args: argparse.Namespace) -> None:
    if not args.storage_policy and not args.allow_empty_storage_policy:
        raise RuntimeError("CLICKHOUSE_LIVE_STORAGE_POLICY/--storage-policy is required")
    required = (
        (args.database, args.index_table),
        (args.identity_database, args.symbol_interval_table),
        (args.identity_database, args.ticker_entity_table),
    )
    for database, table in required:
        rows = _query_rows(client, f"SELECT count() FROM system.tables WHERE database={sql_string(database)} AND name={sql_string(table)}")
        if not rows or int(rows[0][0]) != 1:
            raise RuntimeError(f"required table is missing: {database}.{table}")
    if args.storage_policy:
        rows = _query_rows(client, f"SELECT count() FROM system.storage_policies WHERE policy_name={sql_string(args.storage_policy)}")
        if not rows or int(rows[0][0]) != 1:
            raise RuntimeError(f"unknown ClickHouse storage policy {args.storage_policy!r}")


def validate_schema(client: ClickHouseHttpClient, args: argparse.Namespace) -> None:
    actual = dict(_query_rows(client, f"SELECT name,type FROM system.columns WHERE database={sql_string(args.database)} AND table={sql_string(args.target_table)} ORDER BY position"))
    mismatch = {name: kind for name, kind in session_table_columns() if actual.get(name) != kind}
    extras = sorted(set(actual) - {name for name, _ in session_table_columns()})
    if mismatch or extras:
        raise RuntimeError(f"{args.database}.{args.target_table} schema mismatch: expected={mismatch} extras={extras}")


def resolve_range(client: ClickHouseHttpClient, args: argparse.Namespace) -> tuple[dt.date, dt.date]:
    start = dt.date.fromisoformat(args.start_date)
    if args.end_date == "auto":
        rows = _query_rows(client, f"SELECT addDays(max(source_date),1) FROM {quote_ident(args.database)}.{quote_ident(args.index_table)}")
        if not rows or rows[0][0] in {"", "1970-01-01", "\\N"}:
            raise RuntimeError("event index has no coverage")
        end = dt.date.fromisoformat(rows[0][0])
    else:
        end = dt.date.fromisoformat(args.end_date)
    if end <= start:
        raise ValueError("end-date must be later than start-date")
    return start, end


def date_chunks(start: dt.date, end: dt.date, days: int) -> list[tuple[dt.date, dt.date]]:
    output: list[tuple[dt.date, dt.date]] = []
    cursor = start
    while cursor < end:
        right = min(end, cursor + dt.timedelta(days=max(1, int(days))))
        output.append((cursor, right))
        cursor = right
    return output


def unit_id(start: dt.date, end: dt.date) -> str:
    return f"{start.isoformat()}__{end.isoformat()}"


def write_manifest(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    *,
    start: dt.date,
    end: dt.date,
    status: str,
    rows: int = 0,
    source_events: int = 0,
    mapped: int = 0,
    unmapped: int = 0,
    message: str = "",
) -> None:
    client.execute(
        f"""
INSERT INTO {quote_ident(args.database)}.{quote_ident(args.manifest_table)} VALUES
({sql_string(args.target_table)}, {sql_string(unit_id(start,end))}, toDate({sql_string(start.isoformat())}),
 toDate({sql_string(end.isoformat())}), {sql_string(status)}, {sql_string(BUILD_VERSION)},
 {sql_string(SESSION_BAR_FEATURE_VERSION)}, toUInt64({rows}), toUInt64({source_events}),
 toUInt64({mapped}), toUInt64({unmapped}), {sql_string(message)}, now64(3, 'UTC'))
"""
    )


def completed_units(client: ClickHouseHttpClient, args: argparse.Namespace) -> set[str]:
    rows = _query_rows(
        client,
        f"""
SELECT unit_id FROM {quote_ident(args.database)}.{quote_ident(args.manifest_table)} FINAL
WHERE artifact_name={sql_string(args.target_table)} AND status='complete'
  AND build_version={sql_string(BUILD_VERSION)} AND feature_version={sql_string(SESSION_BAR_FEATURE_VERSION)}
""",
    )
    return {row[0] for row in rows}


def chunk_stats(client: ClickHouseHttpClient, args: argparse.Namespace, start: dt.date, end: dt.date) -> tuple[int, int, int, int]:
    rows = _query_rows(
        client,
        f"""
SELECT count(), uniqExact(tuple(source_ticker,session_date,session_kind)),
       sum(source_event_count), countIf(identity_status='mapped'), countIf(identity_status!='mapped'),
       countIf(available_at_us != bar_end_us), min(schema_version), max(schema_version)
FROM {quote_ident(args.database)}.{quote_ident(args.target_table)} FINAL
WHERE session_date >= toDate({sql_string(start.isoformat())}) AND session_date < toDate({sql_string(end.isoformat())})
""",
    )
    values = [int(value or 0) for value in rows[0]]
    total, unique, source_events, mapped, unmapped, bad_available, minimum_schema, maximum_schema = values
    if total != unique or bad_available or (total and (minimum_schema != SESSION_BAR_SCHEMA_VERSION or maximum_schema != SESSION_BAR_SCHEMA_VERSION)):
        raise RuntimeError(
            f"chunk audit failed [{start},{end}): rows={total} unique={unique} "
            f"bad_available={bad_available} schema={minimum_schema}..{maximum_schema}"
        )
    return total, source_events, mapped, unmapped


def source_event_count(client: ClickHouseHttpClient, args: argparse.Namespace, start: dt.date, end: dt.date) -> int:
    source = _event_source(args, start, end)
    requested = tuple(sorted({item.strip().upper() for item in str(getattr(args, "tickers", "")).split(",") if item.strip()}))
    ticker_filter = "" if not requested else " AND upper(ticker) IN (" + ", ".join(sql_string(item) for item in requested) + ")"
    rows = _query_rows(
        client,
        f"""
WITH toTimeZone(fromUnixTimestamp64Micro(sip_timestamp_us,'UTC'),{sql_string(SESSION_TIMEZONE)}) AS local_ts
SELECT count()
FROM {source}
PREWHERE event_date >= toDate({sql_string(start.isoformat())}) AND event_date <= toDate({sql_string(end.isoformat())})
WHERE ticker!='' AND sip_timestamp_us>0{ticker_filter}
  AND toDate(local_ts) >= toDate({sql_string(start.isoformat())}) AND toDate(local_ts) < toDate({sql_string(end.isoformat())})
  AND dateDiff('second',toStartOfDay(local_ts),local_ts) >= 14400
  AND dateDiff('second',toStartOfDay(local_ts),local_ts) < 72000
{query_settings(args)}
""",
    )
    return int(rows[0][0]) if rows else 0


class SessionBuildReporter:
    def __init__(self, args: argparse.Namespace, total: int, report_path: Path) -> None:
        self.args = args
        self.total = total
        self.report_path = report_path
        self.started = time.perf_counter()
        self.completed = 0
        self.skipped = 0
        self.rows = 0
        self.source_events = 0
        self.state = "starting"
        self.current = "-"
        self.message = "preflight"
        self.messages: deque[str] = deque(maxlen=5)
        self._live = None

    def __enter__(self) -> "SessionBuildReporter":
        interactive = self.args.progress_layout in {"auto", "rich"} and sys.stdout.isatty()
        if interactive:
            try:
                from rich.live import Live
                self._live = Live(self.render(), refresh_per_second=3, transient=False)
                self._live.start()
            except ImportError:
                self._live = None
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc is not None:
            self.state = "failed"
            self.message = str(exc)
        elif self.state != "interrupted":
            self.state = "complete"
            self.message = "all requested chunks are durable and certified"
        if self._live is not None:
            self._live.update(self.render(), refresh=True)
            self._live.stop()
        else:
            print(self.summary(), flush=True)

    def event(self, kind: str, **payload: object) -> None:
        record = {"event": kind, "utc": dt.datetime.now(dt.timezone.utc).isoformat(), **payload}
        with self.report_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")
        detail = str(payload.get("message") or payload.get("unit_id") or kind)
        self.messages.append(detail)
        if self._live is None and self.args.progress_layout != "none":
            print(f"[{kind}] {detail}", flush=True)
        elif self._live is not None:
            self._live.update(self.render(), refresh=True)

    def summary(self) -> str:
        elapsed = time.perf_counter() - self.started
        return (
            f"Bar authority state={self.state} chunks={self.completed}/{self.total} skipped={self.skipped} "
            f"rows={self.rows:,} source_events={self.source_events:,} elapsed={elapsed/60:.1f}m "
            f"current={self.current} message={self.message} evidence={self.report_path}"
        )

    def render(self):
        from rich.console import Group
        from rich.panel import Panel
        from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn
        from rich.table import Table
        table = Table.grid(padding=(0, 2))
        table.add_column(style="bold cyan", no_wrap=True)
        table.add_column(overflow="fold")
        table.add_row("state", self.state)
        table.add_row("current", self.current)
        table.add_row("durable", f"chunks {self.completed}/{self.total} · skipped {self.skipped} · rows {self.rows:,} · events {self.source_events:,}")
        table.add_row("elapsed", f"{(time.perf_counter()-self.started)/60:.1f} min")
        table.add_row("latest", self.message)
        table.add_row("evidence", str(self.report_path))
        progress = Progress(TextColumn("chunks"), BarColumn(), TaskProgressColumn(), expand=True)
        progress.add_task("chunks", total=max(1, self.total), completed=self.completed + self.skipped)
        messages = "\n".join(self.messages) if self.messages else "No completed chunks yet."
        return Group(Panel(table, title="SIP daily-session authority", border_style="cyan"), progress, Panel(messages, title="Recent durable events", border_style="green"))


def build_chunk(client: ClickHouseHttpClient, args: argparse.Namespace, start: dt.date, end: dt.date) -> ChunkResult:
    started = time.perf_counter()
    write_manifest(client, args, start=start, end=end, status="started")
    if args.replace_range:
        client.execute(
            f"ALTER TABLE {quote_ident(args.database)}.{quote_ident(args.target_table)} DELETE "
            f"WHERE session_date >= toDate({sql_string(start.isoformat())}) AND session_date < toDate({sql_string(end.isoformat())})"
            + query_settings(args, mutation=True)
        )
    query_id = f"sip_daily_sessions_{start}_{uuid.uuid4().hex}"
    try:
        client.execute(insert_session_bars_sql(args, start, end), query_id=query_id)
    except KeyboardInterrupt:
        try:
            client.execute(f"KILL QUERY WHERE query_id={sql_string(query_id)} ASYNC")
        except Exception:
            pass
        raise
    rows, actual_events, mapped, unmapped = chunk_stats(client, args, start, end)
    verified = bool(getattr(args, "verify_source_count", False))
    expected_events = source_event_count(client, args, start, end) if verified else actual_events
    if actual_events != expected_events:
        raise RuntimeError(f"source coverage mismatch [{start},{end}): bars={actual_events} source={expected_events}")
    elapsed = time.perf_counter() - started
    write_manifest(
        client, args, start=start, end=end, status="complete", rows=rows, source_events=actual_events,
        mapped=mapped, unmapped=unmapped,
        message=f"certified in {elapsed:.3f}s; independent_source_count={int(verified)}",
    )
    return ChunkResult(start, end, rows, actual_events, mapped, unmapped, elapsed)


def run_build(client: ClickHouseHttpClient, args: argparse.Namespace, start: dt.date, end: dt.date, reporter: SessionBuildReporter) -> None:
    chunks = date_chunks(start, end, args.chunk_days)
    done = completed_units(client, args)
    for left, right in chunks:
        reporter.current = f"[{left}, {right})"
        if unit_id(left, right) in done:
            reporter.skipped += 1
            reporter.event("chunk_skipped", unit_id=unit_id(left, right), message=f"already certified [{left},{right})")
            continue
        reporter.state = "building"
        reporter.message = "aggregating ordered SIP events"
        result = build_chunk(client, args, left, right)
        reporter.completed += 1
        reporter.rows += result.rows
        reporter.source_events += result.source_events
        reporter.message = f"certified {result.rows:,} rows from {result.source_events:,} events in {result.seconds:.1f}s"
        reporter.event(
            "chunk_complete", unit_id=unit_id(left, right), rows=result.rows, source_events=result.source_events,
            mapped_rows=result.mapped_rows, unmapped_rows=result.unmapped_rows, seconds=result.seconds,
            message=reporter.message,
        )


def main(argv: list[str] | None = None) -> int:
    load_env_files(discover_clickhouse_env_files(), verbose=True)
    args = parse_args(argv)
    if str(args.tickers).strip() and (
        args.target_table == DEFAULT_DAILY_SESSION_BARS_TABLE or args.manifest_table == DEFAULT_DAILY_SESSION_MANIFEST_TABLE
    ):
        raise ValueError("Custom --tickers require custom --target-table and --manifest-table names")
    client = ClickHouseHttpClient(args.clickhouse_url, args.clickhouse_user, args.clickhouse_password)
    validate_preflight(client, args)
    start, end = resolve_range(client, args)
    chunks = date_chunks(start, end, args.chunk_days)
    if not args.execute:
        print(create_target_table_sql(args))
        print(create_manifest_table_sql(args))
        print(insert_session_bars_sql(args, chunks[0][0], chunks[0][1]))
        print(f"PLAN range=[{start},{end}) chunks={len(chunks)} target={args.database}.{args.target_table}")
        return 0
    args.runtime_root.mkdir(parents=True, exist_ok=True)
    run_dir = args.runtime_root / dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=False)
    report_path = run_dir / "build.jsonl"
    client.execute(f"CREATE DATABASE IF NOT EXISTS {quote_ident(args.database)}")
    client.execute(create_target_table_sql(args))
    client.execute(create_manifest_table_sql(args))
    validate_schema(client, args)
    try:
        with SessionBuildReporter(args, len(chunks), report_path) as reporter:
            reporter.event("build_started", message=f"range=[{start},{end}) chunks={len(chunks)}")
            run_build(client, args, start, end, reporter)
            reporter.event("build_complete", message="all chunks certified")
    except KeyboardInterrupt:
        print(f"Interrupted safely; restart with the same command. Evidence: {report_path}", flush=True)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
