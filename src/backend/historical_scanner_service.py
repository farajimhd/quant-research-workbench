from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    sql_string,
)


SCANNER_SCHEMA_VERSION = "canvas_historical_scanner_v1"
SCANNER_TABLE = "q_live.canvas_historical_scanner_v1"
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def historical_scanner_snapshot(as_of: datetime, *, lookback_minutes: int = 15) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return a causal full-universe scanner snapshot, materializing it once per source revision."""
    if as_of.tzinfo is None:
        raise ValueError("Historical scanner clock must be timezone-aware.")
    lookback_minutes = max(5, min(int(lookback_minutes), 120))
    snapshot_at = as_of.astimezone(UTC).replace(second=0, microsecond=0)
    window_start = snapshot_at - timedelta(minutes=lookback_minutes)
    client = ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password())
    source_database = os.environ.get("QMD_HISTORY_CLICKHOUSE_DATABASE", "market_sip_compact")
    table_prefix = os.environ.get("QMD_HISTORY_TABLE_PREFIX", "events_")
    if not IDENTIFIER.fullmatch(source_database) or not IDENTIFIER.fullmatch(table_prefix):
        raise ValueError("Historical scanner source identifiers are invalid.")
    source_revision = _source_revision(client, source_database, snapshot_at)
    _ensure_snapshot_table(client)
    rows = _cached_rows(client, snapshot_at, lookback_minutes, source_revision)
    materialized = False
    if not rows:
        _materialize_snapshot(
            client,
            source_database=source_database,
            table_prefix=table_prefix,
            snapshot_at=snapshot_at,
            window_start=window_start,
            lookback_minutes=lookback_minutes,
            source_revision=source_revision,
        )
        rows = _cached_rows(client, snapshot_at, lookback_minutes, source_revision)
        materialized = True
    return rows, {
        "complete_universe": True,
        "lookback_minutes": lookback_minutes,
        "materialized": materialized,
        "row_count": len(rows),
        "schema_version": SCANNER_SCHEMA_VERSION,
        "snapshot_at_utc": snapshot_at.isoformat(),
        "source_revision": source_revision,
        "window_start_utc": window_start.isoformat(),
    }


def _ensure_snapshot_table(client: ClickHouseHttpClient) -> None:
    client.execute(
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
        """
    )
    client.execute(f"ALTER TABLE {SCANNER_TABLE} ADD COLUMN IF NOT EXISTS schema_version LowCardinality(String) DEFAULT '' AFTER lookback_minutes")


def _source_revision(client: ClickHouseHttpClient, database: str, snapshot_at: datetime) -> str:
    source_date = snapshot_at.date().isoformat()
    rows = _json_rows(
        client.execute(
            f"""
            SELECT
                sum(canonical_event_count) AS event_count,
                max(latest_build_step) AS build_step,
                toString(max(latest_updated_at)) AS updated_at
            FROM
            (
                SELECT
                    ticker,
                    argMax(event_count, tuple(build_step, updated_at)) AS canonical_event_count,
                    argMax(build_step, tuple(build_step, updated_at)) AS latest_build_step,
                    max(updated_at) AS latest_updated_at
                FROM {database}.events_ordinal_continuity
                WHERE source_date = toDate({sql_string(source_date)})
                GROUP BY ticker
            )
            FORMAT JSONEachRow
            """
        )
    )
    row = rows[0] if rows else {}
    return f"{int(row.get('build_step') or 0)}:{int(row.get('event_count') or 0)}:{row.get('updated_at') or ''}"


def _cached_rows(client: ClickHouseHttpClient, snapshot_at: datetime, lookback_minutes: int, source_revision: str) -> list[dict[str, Any]]:
    rows = _json_rows(
        client.execute(
            f"""
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
        )
    )
    return [{**row, "ticker": str(row.get("symbol") or "")} for row in rows]


def _materialize_snapshot(
    client: ClickHouseHttpClient,
    *,
    source_database: str,
    table_prefix: str,
    snapshot_at: datetime,
    window_start: datetime,
    lookback_minutes: int,
    source_revision: str,
) -> None:
    start_us = int(window_start.timestamp() * 1_000_000)
    end_us = int(snapshot_at.timestamp() * 1_000_000)
    five_minute_us = int((snapshot_at - timedelta(minutes=5)).timestamp() * 1_000_000)
    selects = []
    for year in range(window_start.year, snapshot_at.year + 1):
        selects.append(
            f"""
            SELECT ticker, ordinal, event_meta, sip_timestamp_us, price_primary_int, size_primary
            FROM {source_database}.{table_prefix}{year}
            PREWHERE sip_timestamp_us >= {start_us} AND sip_timestamp_us < {end_us}
            """
        )
    source = " UNION ALL ".join(selects)
    client.execute(
        f"""
        INSERT INTO {SCANNER_TABLE}
            (snapshot_at_utc, lookback_minutes, schema_version, source_revision, symbol, last, change_pct,
             change_5m_pct, volume, trade_count, quote_count)
        SELECT
            parseDateTime64BestEffort({sql_string(_clock(snapshot_at))}),
            {lookback_minutes},
            {sql_string(SCANNER_SCHEMA_VERSION)},
            {sql_string(source_revision)},
            ticker,
            last_price,
            if(first_price = 0, 0, (last_price / first_price - 1) * 100),
            if(first_5m_price = 0, 0, (last_price / first_5m_price - 1) * 100),
            volume,
            trade_count,
            quote_count
        FROM
        (
            SELECT
                ticker,
                argMaxIf(price, tuple(sip_timestamp_us, ordinal), is_trade) AS last_price,
                argMinIf(price, tuple(sip_timestamp_us, ordinal), is_trade) AS first_price,
                argMinIf(price, tuple(sip_timestamp_us, ordinal), is_trade AND sip_timestamp_us >= {five_minute_us}) AS first_5m_price,
                sumIf(toFloat64(size_primary), is_trade) AS volume,
                countIf(is_trade) AS trade_count,
                countIf(is_quote) AS quote_count
            FROM
            (
                SELECT
                    ticker,
                    ordinal,
                    sip_timestamp_us,
                    bitAnd(event_meta, 1) = 1 AND price_primary_int > 0 AND size_primary > 0 AS is_trade,
                    bitAnd(event_meta, 1) = 0 AS is_quote,
                    toFloat64(price_primary_int) / if(bitAnd(event_meta, 2) != 0, 10000., 100.) AS price,
                    size_primary
                FROM ({source})
            )
            GROUP BY ticker
        )
        WHERE trade_count > 0
        """
    )


def _clock(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")


def _json_rows(payload: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in payload.splitlines() if line.strip()]
