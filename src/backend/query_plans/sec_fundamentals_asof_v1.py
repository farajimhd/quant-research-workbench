from __future__ import annotations

from datetime import UTC, datetime
from typing import Iterable

from research.mlops.clickhouse import quote_ident, sql_string


HISTORY_LIMIT = 10_000
XBRL_HISTORY_START = datetime(2019, 1, 1, tzinfo=UTC)
QUERY_PLAN_ID = "sec.fundamentals_asof.v1"
QUERY_PLAN_VERSION = 1


def fundamental_fact_queries(
    *,
    cik: str,
    tags: Iterable[str],
    cutoff: datetime,
    database: str,
) -> dict[str, str]:
    """Build the bounded current and comparison-history SEC fact queries."""
    tag_catalog = tuple(tags)
    return {
        "current": fundamentals(cik, tag_catalog, cutoff, database),
        "history": fundamentals_history(cik, tag_catalog, cutoff, database),
    }


def scanner_fundamentals(
    tags: Iterable[str],
    cutoff: datetime,
    database: str,
    *,
    tickers: Iterable[str] = (),
) -> str:
    """Build the causal all-universe XBRL projection used by historical Scanner."""
    db = quote_ident(database)
    instant = sql_string(clickhouse_timestamp(cutoff))
    cutoff_date = sql_string(cutoff.astimezone(UTC).date().isoformat())
    history_start = sql_string(clickhouse_timestamp(XBRL_HISTORY_START))
    ticker_catalog = tuple(sorted({str(ticker).strip().upper() for ticker in tickers if str(ticker).strip()}))
    ticker_filter = f"\n                  AND upper(u.ticker) IN ({', '.join(sql_string(ticker) for ticker in ticker_catalog)})" if ticker_catalog else ""
    return f"""
        WITH
            parseDateTime64BestEffort({instant}) AS cutoff,
            toDate({cutoff_date}) AS cutoff_date,
            (
                SELECT max(universe_date)
                FROM {db}.feature_tradable_universe_v1 FINAL
                WHERE universe_date <= cutoff_date AND inserted_at <= cutoff
            ) AS latest_universe_date,
            universe AS
            (
                SELECT
                    upper(u.ticker) AS ticker,
                    argMax(
                        replaceOne(u.issuer_id, 'issuer:cik:', ''),
                        tuple(u.is_tradable, u.currency_code = 'USD', u.product_type = 'STK', u.inserted_at)
                    ) AS cik
                FROM {db}.feature_tradable_universe_v1 AS u FINAL
                WHERE u.universe_date = latest_universe_date
                  AND u.inserted_at <= cutoff
                  AND notEmpty(u.ticker)
                  AND startsWith(u.issuer_id, 'issuer:cik:')
                  {ticker_filter}
                GROUP BY upper(u.ticker)
            )
        SELECT *
        FROM
        (
            SELECT
                universe.ticker AS ticker,
                f.tag AS tag,
                f.taxonomy AS taxonomy,
                f.unit_code AS unit_code,
                f.value AS value,
                f.fiscal_year AS fiscal_year,
                f.fiscal_period AS fiscal_period,
                f.period_end_date AS period_end_date,
                f.filed_at_utc AS filed_at_utc,
                f.form_type AS form_type,
                f.accession_number AS accession_number,
                f.recorded_at_utc AS recorded_at_utc
            FROM {db}.sec_xbrl_company_fact_v3 AS f FINAL
            INNER JOIN universe ON universe.cik = toString(f.cik)
            WHERE f.tag IN ({_tag_clause(tags)})
              AND f.filed_at_utc >= parseDateTime64BestEffort({history_start})
              AND f.filed_at_utc <= cutoff
              AND f.recorded_at_utc <= cutoff
            ORDER BY ticker, tag, period_end_date DESC, filed_at_utc DESC, recorded_at_utc DESC
            LIMIT 1 BY ticker, tag, period_end_date, fiscal_period, unit_code
        )
        ORDER BY ticker, tag, period_end_date DESC, filed_at_utc DESC, recorded_at_utc DESC
        LIMIT 8 BY ticker, tag
        FORMAT JSONEachRow
    """


def fundamentals(
    cik: str,
    tags: Iterable[str],
    cutoff: datetime,
    database: str,
) -> str:
    db = quote_ident(database)
    tag_clause = _tag_clause(tags)
    instant = sql_string(clickhouse_timestamp(cutoff))
    history_start = sql_string(clickhouse_timestamp(XBRL_HISTORY_START))
    return f"""
        SELECT tag, taxonomy, unit_code, value, fiscal_year, fiscal_period, period_end_date,
               filed_at_utc, form_type, accession_number, recorded_at_utc
        FROM {db}.sec_xbrl_company_fact_v3 FINAL
        WHERE cik = {sql_string(cik)} AND tag IN ({tag_clause})
          AND filed_at_utc >= parseDateTime64BestEffort({history_start})
          AND filed_at_utc <= parseDateTime64BestEffort({instant})
          AND recorded_at_utc <= parseDateTime64BestEffort({instant})
        ORDER BY tag ASC, period_end_date DESC, filed_at_utc DESC, recorded_at_utc DESC
        LIMIT 1 BY tag, period_end_date, fiscal_period, unit_code
        FORMAT JSONEachRow
    """


def fundamental_history(
    cik: str,
    tag: str,
    cutoff: datetime,
    database: str,
    *,
    limit: int = HISTORY_LIMIT,
) -> str:
    db = quote_ident(database)
    instant = sql_string(clickhouse_timestamp(cutoff))
    return f"""
        SELECT tag, taxonomy, unit_code, value, fiscal_year, fiscal_period, period_end_date,
               filed_at_utc, form_type, accession_number, recorded_at_utc
        FROM {db}.sec_xbrl_company_fact_v3 FINAL
        WHERE cik = {sql_string(cik)} AND tag = {sql_string(tag)}
          AND filed_at_utc <= parseDateTime64BestEffort({instant})
          AND recorded_at_utc <= parseDateTime64BestEffort({instant})
        ORDER BY period_end_date DESC, filed_at_utc DESC, recorded_at_utc DESC
        LIMIT 1 BY period_end_date, fiscal_period, unit_code
        LIMIT {max(1, min(HISTORY_LIMIT, limit))}
        FORMAT JSONEachRow
    """


def fundamentals_history(
    cik: str,
    tags: Iterable[str],
    cutoff: datetime,
    database: str,
) -> str:
    db = quote_ident(database)
    instant = sql_string(clickhouse_timestamp(cutoff))
    return f"""
        SELECT tag, taxonomy, unit_code, value, fiscal_year, fiscal_period, period_end_date,
               filed_at_utc, form_type, accession_number, recorded_at_utc, inserted_at
        FROM {db}.sec_xbrl_company_fact_v3 FINAL
        WHERE cik = {sql_string(cik)} AND tag IN ({_tag_clause(tags)})
          AND filed_at_utc <= parseDateTime64BestEffort({instant})
          AND recorded_at_utc <= parseDateTime64BestEffort({instant})
        ORDER BY filed_at_utc DESC, period_end_date DESC, recorded_at_utc DESC
        LIMIT 64 BY tag
        FORMAT JSONEachRow
    """


def clickhouse_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("fundamental cutoff must include a timezone")
    return value.astimezone(UTC).isoformat(timespec="milliseconds")


def _tag_clause(tags: Iterable[str]) -> str:
    values = sorted({str(tag).strip() for tag in tags if str(tag).strip()})
    if not values:
        raise ValueError("fundamental query requires at least one XBRL tag")
    return ", ".join(sql_string(tag) for tag in values)
