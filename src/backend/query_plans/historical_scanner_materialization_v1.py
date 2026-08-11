from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from research.mlops.clickhouse import sql_string
from src.backend.query_plans.market_daily_bars_v1 import (
    daily_session_trade_bars_relation_sql,
)


QUERY_PLAN_ID = "market.historical_scanner_materialization.v1"
QUERY_PLAN_VERSION = 1
SCANNER_SCHEMA_VERSION = "canvas_historical_scanner_v1"
SCANNER_TABLE = "q_live.canvas_historical_scanner_v1"
SCANNER_TECHNICAL_SCHEMA_VERSION = "canvas_scanner_technical_v3"
SCANNER_TECHNICAL_TABLE = "q_live.canvas_scanner_technical_v3"
EXTENDED_SESSION_DURATION_US = 16 * 60 * 60 * 1_000_000
NEW_YORK = ZoneInfo("America/New_York")


def technical_snapshot_materialization(
    *,
    source_database: str,
    table_prefix: str,
    snapshot_at: datetime,
    window_start: datetime,
    calculation_window: str,
    source_revision: str,
) -> str:
    start_us = int(window_start.timestamp() * 1_000_000)
    end_us = int(snapshot_at.timestamp() * 1_000_000)
    elapsed_us = max(1, end_us - start_us)
    start_date = window_start.date().isoformat()
    end_date = snapshot_at.date().isoformat()
    selects = [
        f"""
        SELECT ticker, ordinal, event_meta, sip_timestamp_us, price_primary_int, size_primary
        FROM {source_database}.{table_prefix}{year}
        PREWHERE event_date >= toDate({sql_string(start_date)})
          AND event_date <= toDate({sql_string(end_date)})
        WHERE sip_timestamp_us >= {start_us} AND sip_timestamp_us < {end_us}
        """
        for year in range(window_start.year, snapshot_at.year + 1)
    ]
    source = " UNION ALL ".join(selects)
    prior_daily_bars = daily_session_trade_bars_relation_sql(
        database=source_database,
        start_date=snapshot_at.astimezone(NEW_YORK).date() - timedelta(days=35),
        end_date=snapshot_at.astimezone(NEW_YORK).date(),
        as_of=snapshot_at,
    )
    return f"""
    INSERT INTO {SCANNER_TECHNICAL_TABLE}
    (
        snapshot_at_utc, calculation_window, schema_version, source_revision, symbol,
        open, high, low, change_pct, volume, dollar_volume, trade_count, quote_count,
        vwap, vwap_distance_pct, vwap_trade, vwap_trade_distance_pct,
        relative_volume, range_pct, average_daily_volume
    )
    WITH
        {elapsed_us} AS elapsed_us,
        {EXTENDED_SESSION_DURATION_US} AS session_us
    SELECT
        parseDateTime64BestEffort({sql_string(_clock(snapshot_at))}),
        {sql_string(calculation_window)},
        {sql_string(SCANNER_TECHNICAL_SCHEMA_VERSION)},
        {sql_string(source_revision)},
        current.symbol,
        current.open,
        current.high,
        current.low,
        if(current.open = 0, 0, (current.last / current.open - 1) * 100),
        current.volume,
        current.dollar_volume,
        current.trade_count,
        current.quote_count,
        current.vwap,
        if(current.vwap = 0, 0, (current.last / current.vwap - 1) * 100),
        current.vwap_trade,
        if(current.vwap_trade = 0, 0, (current.last / current.vwap_trade - 1) * 100),
        if(prior.average_daily_volume > 0,
           current.volume / (prior.average_daily_volume * elapsed_us / session_us),
           NULL),
        if(current.low = 0, 0, (current.high / current.low - 1) * 100),
        prior.average_daily_volume
    FROM
    (
        SELECT
            ticker AS symbol,
            argMinIf(bar_open, minute_index, bar_trade_count > 0) AS open,
            maxIf(bar_high, bar_trade_count > 0) AS high,
            minIf(bar_low, bar_trade_count > 0) AS low,
            argMaxIf(bar_close, minute_index, bar_trade_count > 0) AS last,
            sum(bar_volume) AS volume,
            sum(bar_dollar_volume) AS dollar_volume,
            sum(bar_trade_count) AS trade_count,
            sum(bar_quote_count) AS quote_count,
            if(volume = 0, 0, sum(((bar_high + bar_low + bar_close) / 3) * bar_volume) / volume) AS vwap,
            if(volume = 0, 0, dollar_volume / volume) AS vwap_trade
        FROM
        (
            SELECT
                ticker,
                intDiv(sip_timestamp_us - {start_us}, 60000000) AS minute_index,
                argMinIf(price, tuple(sip_timestamp_us, ordinal), is_trade) AS bar_open,
                maxIf(price, is_trade) AS bar_high,
                minIf(price, is_trade) AS bar_low,
                argMaxIf(price, tuple(sip_timestamp_us, ordinal), is_trade) AS bar_close,
                sumIf(toFloat64(size_primary), is_trade) AS bar_volume,
                sumIf(price * toFloat64(size_primary), is_trade) AS bar_dollar_volume,
                countIf(is_trade) AS bar_trade_count,
                countIf(is_quote) AS bar_quote_count
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
            GROUP BY ticker, minute_index
        )
        GROUP BY ticker
        HAVING trade_count > 0
    ) AS current
    LEFT JOIN
    (
        SELECT sym, avg(size_sum) AS average_daily_volume
        FROM
        (
            SELECT source_sym AS sym, session_date, size_sum
            FROM ({prior_daily_bars})
            ORDER BY session_date DESC
            LIMIT 20 BY sym
        )
        GROUP BY sym
    ) AS prior ON prior.sym = current.symbol
    """


def scanner_snapshot_materialization(
    *,
    source_database: str,
    table_prefix: str,
    snapshot_at: datetime,
    window_start: datetime,
    lookback_minutes: int,
    source_revision: str,
) -> str:
    start_us = int(window_start.timestamp() * 1_000_000)
    end_us = int(snapshot_at.timestamp() * 1_000_000)
    five_minute_us = int((snapshot_at - timedelta(minutes=5)).timestamp() * 1_000_000)
    source = " UNION ALL ".join(
        f"""
        SELECT ticker, ordinal, event_meta, sip_timestamp_us, price_primary_int, size_primary
        FROM {source_database}.{table_prefix}{year}
        PREWHERE sip_timestamp_us >= {start_us} AND sip_timestamp_us < {end_us}
        """
        for year in range(window_start.year, snapshot_at.year + 1)
    )
    return f"""
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


def source_revision_query(*, database: str, snapshot_at: datetime) -> str:
    return f"""
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
        WHERE source_date = toDate({sql_string(snapshot_at.date().isoformat())})
        GROUP BY ticker
    )
    FORMAT JSONEachRow
    """


def _clock(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
