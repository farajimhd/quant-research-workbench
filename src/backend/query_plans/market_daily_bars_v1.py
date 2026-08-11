from __future__ import annotations

from datetime import date, datetime

from research.mlops.clickhouse import quote_ident, sql_string


DEFAULT_DAILY_SESSION_BARS_TABLE = "daily_session_bars_by_symbol_time_v1"


def daily_session_trade_bars(
    *,
    database: str,
    start_date: date,
    end_date: date,
    as_of: datetime,
    ticker: str | None = None,
    table: str = DEFAULT_DAILY_SESSION_BARS_TABLE,
) -> str:
    """Version-1 causal plan for fully closed daily trade bars.

    A daily result exists only when premarket, regular, and postmarket source
    rows are all present. The as-of availability guard and canonical/source
    ticker fallback are part of this plan's stable contract.
    """
    if start_date >= end_date:
        raise ValueError("daily-session range must have start_date before end_date")
    if as_of.tzinfo is None:
        raise ValueError("daily-session as_of must include a timezone")
    identity_cte = ""
    ticker_filter = ""
    if ticker:
        symbol = ticker.strip().upper()
        identity_cte = f"""
        WITH (
            SELECT count()
            FROM {quote_ident(database)}.{quote_ident(table)} FINAL
            PREWHERE session_date >= toDate({sql_string(start_date.isoformat())})
              AND session_date < toDate({sql_string(end_date.isoformat())})
            WHERE canonical_ticker = {sql_string(symbol)}
              AND identity_status != 'ambiguous_source_ticker'
              AND available_at_us <= toUInt64(toUnixTimestamp64Micro(parseDateTime64BestEffort({sql_string(as_of.isoformat())})))
        ) AS requested_canonical_count
        """
        ticker_filter = f"""
          AND (
              canonical_ticker = {sql_string(symbol)}
              OR (requested_canonical_count = 0 AND source_ticker = {sql_string(symbol)})
          )"""
    return f"""
        {identity_cte}
        SELECT
            ifNull(canonical_ticker, source_ticker) AS sym,
            argMax(source_ticker, bar_end_us) AS source_sym,
            session_date,
            fromUnixTimestamp64Micro(toInt64(min(bar_start_us)), 'UTC') AS bar_start,
            fromUnixTimestamp64Micro(toInt64(max(bar_end_us)), 'UTC') AS bar_end,
            argMinIf(trade_open, tuple(bar_start_us, source_first_timestamp_us), trade_present = 1) AS open,
            maxIf(trade_high, trade_present = 1) AS high,
            minIf(trade_low, trade_present = 1) AS low,
            argMaxIf(trade_close, tuple(bar_end_us, source_last_timestamp_us), trade_present = 1) AS close,
            sum(trade_size_sum) AS size_sum,
            sum(trade_event_count) AS event_count
        FROM {quote_ident(database)}.{quote_ident(table)} FINAL
        PREWHERE session_date >= toDate({sql_string(start_date.isoformat())})
          AND session_date < toDate({sql_string(end_date.isoformat())})
        WHERE adjusted = 0
          AND identity_status != 'ambiguous_source_ticker'
          AND available_at_us <= toUInt64(toUnixTimestamp64Micro(parseDateTime64BestEffort({sql_string(as_of.isoformat())})))
          {ticker_filter}
        GROUP BY sym, session_date
        HAVING uniqExact(session_kind) = 3 AND event_count > 0
    """


def daily_market_reference_projection(
    *,
    database: str,
    start_date: date,
    end_date: date,
    as_of: datetime,
    table: str = DEFAULT_DAILY_SESSION_BARS_TABLE,
) -> str:
    """Causal previous-close and average-volume projection for Watchlists."""
    relation = daily_session_trade_bars(
        database=database,
        start_date=start_date,
        end_date=end_date,
        as_of=as_of,
        table=table,
    )
    return f"""
        SELECT
            upper(sym) AS ticker,
            argMax(close, session_date) AS previous_close,
            avg(size_sum) AS average_daily_volume
        FROM ({relation})
        GROUP BY ticker
        FORMAT JSONEachRow
    """


# Compatibility name for existing service callers. The registered plan entry
# points at ``daily_session_trade_bars`` and new callers should use that name.
daily_session_trade_bars_relation_sql = daily_session_trade_bars
