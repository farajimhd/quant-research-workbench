from __future__ import annotations

from datetime import UTC, datetime

from research.mlops.clickhouse import sql_string
from src.backend.query_plans.historical_scanner_materialization_v1 import (
    SCANNER_SCHEMA_VERSION,
    SCANNER_TABLE,
    SCANNER_TECHNICAL_SCHEMA_VERSION,
    SCANNER_TECHNICAL_TABLE,
)


QUERY_PLAN_ID = "market.historical_scanner_cache.v1"
QUERY_PLAN_VERSION = 1
SCANNER_QMD_SCHEMA_VERSION = "canvas_historical_qmd_snapshot_v3"
SCANNER_QMD_TABLE = "q_live.canvas_historical_qmd_scanner_v1"
SCANNER_QMD_EVENT_TABLE = "q_live.canvas_historical_qmd_signal_event_v1"
SCANNER_QMD_META_TABLE = "q_live.canvas_historical_qmd_snapshot_meta_v1"

_CACHE_TABLES = {
    SCANNER_TABLE,
    SCANNER_TECHNICAL_TABLE,
    SCANNER_QMD_TABLE,
    SCANNER_QMD_EVENT_TABLE,
    SCANNER_QMD_META_TABLE,
}


def snapshot_table_schema() -> tuple[str, ...]:
    return (
        f"""
        CREATE TABLE IF NOT EXISTS {SCANNER_TABLE}
        (
            snapshot_at_utc DateTime64(6, 'UTC'),
            lookback_minutes UInt16,
            schema_version LowCardinality(String),
            source_revision String,
            symbol LowCardinality(String),
            last Float64,
            change_pct Float64,
            change_5m_pct Float64,
            volume Float64,
            trade_count UInt64,
            quote_count UInt64,
            materialized_at_utc DateTime64(6, 'UTC') DEFAULT now64(6)
        )
        ENGINE = ReplacingMergeTree(materialized_at_utc)
        PARTITION BY toYYYYMM(snapshot_at_utc)
        ORDER BY (snapshot_at_utc, lookback_minutes, source_revision, symbol)
        """,
        f"ALTER TABLE {SCANNER_TABLE} ADD COLUMN IF NOT EXISTS schema_version LowCardinality(String) DEFAULT '' AFTER lookback_minutes",
    )


def qmd_snapshot_table_schemas() -> tuple[str, ...]:
    return (
        f"""
        CREATE TABLE IF NOT EXISTS {SCANNER_QMD_TABLE}
        (
            snapshot_at_utc DateTime64(6, 'UTC'),
            schema_version LowCardinality(String),
            source_revision String,
            ticker LowCardinality(String),
            indicator_json String,
            active_signals_json String,
            materialized_at_utc DateTime64(6, 'UTC') DEFAULT now64(6)
        )
        ENGINE = ReplacingMergeTree(materialized_at_utc)
        PARTITION BY toYYYYMM(snapshot_at_utc)
        ORDER BY (snapshot_at_utc, schema_version, source_revision, ticker)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {SCANNER_QMD_EVENT_TABLE}
        (
            snapshot_at_utc DateTime64(6, 'UTC'),
            schema_version LowCardinality(String),
            source_revision String,
            event_id String,
            event_json String,
            materialized_at_utc DateTime64(6, 'UTC') DEFAULT now64(6)
        )
        ENGINE = ReplacingMergeTree(materialized_at_utc)
        PARTITION BY toYYYYMM(snapshot_at_utc)
        ORDER BY (snapshot_at_utc, schema_version, source_revision, event_id)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {SCANNER_QMD_META_TABLE}
        (
            snapshot_at_utc DateTime64(6, 'UTC'),
            schema_version LowCardinality(String),
            source_revision String,
            engine_version String,
            event_count UInt64,
            indicator_count UInt32,
            active_signal_count UInt32,
            signal_event_count UInt32,
            complete UInt8,
            materialized_at_utc DateTime64(6, 'UTC') DEFAULT now64(6)
        )
        ENGINE = ReplacingMergeTree(materialized_at_utc)
        PARTITION BY toYYYYMM(snapshot_at_utc)
        ORDER BY (snapshot_at_utc, schema_version, source_revision)
        """,
    )


def technical_snapshot_table_schema() -> str:
    return f"""
    CREATE TABLE IF NOT EXISTS {SCANNER_TECHNICAL_TABLE}
    (
        snapshot_at_utc DateTime64(6, 'UTC'),
        calculation_window LowCardinality(String),
        schema_version LowCardinality(String),
        source_revision String,
        symbol LowCardinality(String),
        open Float64,
        high Float64,
        low Float64,
        change_pct Float64,
        volume Float64,
        dollar_volume Float64,
        trade_count UInt64,
        quote_count UInt64,
        vwap Float64,
        vwap_distance_pct Float64,
        vwap_trade Float64,
        vwap_trade_distance_pct Float64,
        relative_volume Nullable(Float64),
        range_pct Float64,
        average_daily_volume Nullable(Float64),
        materialized_at_utc DateTime64(6, 'UTC') DEFAULT now64(6)
    )
    ENGINE = ReplacingMergeTree(materialized_at_utc)
    PARTITION BY toYYYYMM(snapshot_at_utc)
    ORDER BY (snapshot_at_utc, calculation_window, source_revision, symbol)
    """


def qmd_snapshot_complete_queries(
    *, snapshot_at: datetime, source_revision: str
) -> tuple[str, str]:
    where = _qmd_identity(snapshot_at, source_revision)
    return (
        f"""
        SELECT complete, indicator_count
        FROM {SCANNER_QMD_META_TABLE} FINAL
        WHERE {where}
        LIMIT 1
        FORMAT JSONEachRow
        """,
        f"""
        SELECT count() AS indicator_count
        FROM {SCANNER_QMD_TABLE} FINAL
        WHERE {where}
        FORMAT JSONEachRow
        """,
    )


def cached_qmd_rows_query(*, snapshot_at: datetime, source_revision: str) -> str:
    return f"""
    SELECT ticker, indicator_json, active_signals_json
    FROM {SCANNER_QMD_TABLE} FINAL
    WHERE {_qmd_identity(snapshot_at, source_revision)}
    ORDER BY ticker
    LIMIT 20000
    FORMAT JSONEachRow
    """


def cached_qmd_signal_events_query(
    *, snapshot_at: datetime, source_revision: str
) -> str:
    return f"""
    SELECT event_json
    FROM {SCANNER_QMD_EVENT_TABLE} FINAL
    WHERE {_qmd_identity(snapshot_at, source_revision)}
    ORDER BY event_id
    LIMIT 20000
    FORMAT JSONEachRow
    """


def cached_technical_rows_query(
    *, snapshot_at: datetime, calculation_window: str, source_revision: str
) -> str:
    return f"""
    SELECT
        symbol, open, high, low, change_pct, volume, dollar_volume,
        trade_count, quote_count, vwap, vwap_distance_pct,
        vwap_trade, vwap_trade_distance_pct, relative_volume, range_pct
    FROM {SCANNER_TECHNICAL_TABLE} FINAL
    WHERE snapshot_at_utc = parseDateTime64BestEffort({sql_string(_clock(snapshot_at))})
      AND calculation_window = {sql_string(calculation_window)}
      AND schema_version = {sql_string(SCANNER_TECHNICAL_SCHEMA_VERSION)}
      AND source_revision = {sql_string(source_revision)}
    ORDER BY abs(change_pct) DESC, symbol ASC
    LIMIT 20000
    FORMAT JSONEachRow
    """


def cached_scanner_rows_query(
    *, snapshot_at: datetime, lookback_minutes: int, source_revision: str
) -> str:
    return f"""
    SELECT symbol, last, change_pct, change_5m_pct, volume, trade_count, quote_count
    FROM {SCANNER_TABLE} FINAL
    WHERE snapshot_at_utc = parseDateTime64BestEffort({sql_string(_clock(snapshot_at))})
      AND lookback_minutes = {lookback_minutes}
      AND schema_version = {sql_string(SCANNER_SCHEMA_VERSION)}
      AND source_revision = {sql_string(source_revision)}
    ORDER BY abs(change_5m_pct) DESC, symbol ASC
    LIMIT 20000
    FORMAT JSONEachRow
    """


def latest_cached_scanner_snapshot_query(
    *, snapshot_at: datetime, lookback_minutes: int, source_revision: str
) -> str:
    clock = sql_string(_clock(snapshot_at))
    return f"""
    SELECT toString(maxOrNull(snapshot_at_utc)) AS latest_snapshot_at_utc
    FROM {SCANNER_TABLE} FINAL
    WHERE snapshot_at_utc < parseDateTime64BestEffort({clock})
      AND toDate(snapshot_at_utc, 'UTC') = toDate(parseDateTime64BestEffort({clock}), 'UTC')
      AND lookback_minutes = {lookback_minutes}
      AND schema_version = {sql_string(SCANNER_SCHEMA_VERSION)}
      AND source_revision = {sql_string(source_revision)}
    FORMAT JSONEachRow
    """


def json_each_row_insert(table: str) -> str:
    if table not in _CACHE_TABLES:
        raise ValueError(f"Historical Scanner cache table is not registered: {table}")
    return f"INSERT INTO {table} FORMAT JSONEachRow"


def _qmd_identity(snapshot_at: datetime, source_revision: str) -> str:
    return (
        f"snapshot_at_utc = toDateTime64({sql_string(_clock(snapshot_at))}, 6, 'UTC') "
        f"AND schema_version = {sql_string(SCANNER_QMD_SCHEMA_VERSION)} "
        f"AND source_revision = {sql_string(source_revision)}"
    )


def _clock(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
