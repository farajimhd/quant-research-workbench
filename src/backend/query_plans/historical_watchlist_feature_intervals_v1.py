from __future__ import annotations

from datetime import UTC, datetime
from typing import Iterable

from research.mlops.clickhouse import quote_ident, sql_string


QUERY_PLAN_ID = "watchlist.external_feature_intervals.v1"
QUERY_PLAN_VERSION = 3
MAX_CHANGE_CLOCKS = 512


def feature_change_clocks(
    *,
    cadence_ms: int,
    include_reference: bool,
    include_fundamentals: bool,
    start: datetime,
    end: datetime,
    database: str = "q_live",
    identity_tickers: Iterable[str] = (),
) -> str:
    """Return bounded clocks at which an approved as-of projection can change."""
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("historical Watchlist feature bounds must be timezone-aware")
    if end <= start:
        raise ValueError("historical Watchlist feature end must follow start")
    if not include_reference and not include_fundamentals:
        raise ValueError("historical Watchlist feature clocks require a source family")
    if cadence_ms <= 0:
        raise ValueError("historical Watchlist feature cadence must be positive")
    db = quote_ident(database)
    selects: list[str] = []
    identity_catalog = tuple(sorted({
        str(ticker).strip().upper()
        for ticker in identity_tickers
        if str(ticker).strip()
    }))
    if include_reference:
        if identity_catalog:
            ticker_values = ", ".join(sql_string(ticker) for ticker in identity_catalog)
            selects.append(
                f"SELECT inserted_at AS raw_available_at "
                f"FROM {db}.feature_tradable_universe_v1 FINAL "
                f"WHERE upper(ticker) IN ({ticker_values})"
            )
        else:
            for table in (
                "feature_tradable_universe_v1",
                "feature_scanner_static_v1",
                "id_security_v1",
                "id_issuer_v1",
            ):
                selects.append(
                    f"SELECT inserted_at AS raw_available_at FROM {db}.{quote_ident(table)} FINAL"
                )
            selects.extend(
                (
                    f"SELECT available_at_utc AS raw_available_at FROM {db}.market_security_country_v1 FINAL",
                    f"SELECT available_at_utc AS raw_available_at FROM {db}.market_issuer_company_profile_v1 FINAL",
                    f"SELECT greatest(observed_at_utc, inserted_at) AS raw_available_at FROM {db}.market_security_market_snapshot_v1 FINAL",
                    f"SELECT greatest(toDateTime64(effective_date, 3, 'UTC'), inserted_at) AS raw_available_at FROM {db}.market_security_float_v1 FINAL",
                    f"SELECT greatest(coalesce(published_at_utc, toDateTime64(publication_date, 3, 'UTC'), toDateTime64(settlement_date, 3, 'UTC')), inserted_at) AS raw_available_at FROM {db}.market_short_interest_v1 FINAL",
                    f"SELECT inserted_at AS raw_available_at FROM {db}.market_ipo_v1 FINAL",
                    f"SELECT inserted_at AS raw_available_at FROM {db}.market_stock_split_v1 FINAL",
                )
            )
    if include_fundamentals:
        selects.extend(
            (
                f"SELECT inserted_at AS raw_available_at FROM {db}.feature_tradable_universe_v1 FINAL",
                f"SELECT greatest(filed_at_utc, recorded_at_utc) AS raw_available_at FROM {db}.sec_xbrl_company_fact_v3 FINAL",
            )
        )
    start_value = sql_string(start.astimezone(UTC).isoformat(timespec="milliseconds"))
    end_value = sql_string(end.astimezone(UTC).isoformat(timespec="milliseconds"))
    return f"""
        SELECT formatDateTime(
            fromUnixTimestamp64Milli(
                intDiv(toUnixTimestamp64Milli(raw_available_at) + {cadence_ms - 1}, {cadence_ms}) * {cadence_ms}
            ),
            '%Y-%m-%dT%H:%i:%s.%fZ',
            'UTC'
        ) AS available_at
        FROM ({' UNION ALL '.join(selects)})
        WHERE raw_available_at > parseDateTime64BestEffort({start_value})
          AND raw_available_at < parseDateTime64BestEffort({end_value})
        GROUP BY available_at
        ORDER BY available_at
        LIMIT {MAX_CHANGE_CLOCKS + 1}
        FORMAT JSONEachRow
    """
