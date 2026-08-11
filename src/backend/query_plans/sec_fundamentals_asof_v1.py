from __future__ import annotations

from datetime import UTC, datetime
from typing import Iterable

from research.mlops.clickhouse import quote_ident, sql_string


HISTORY_LIMIT = 10_000
XBRL_HISTORY_START = datetime(2019, 1, 1, tzinfo=UTC)


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
