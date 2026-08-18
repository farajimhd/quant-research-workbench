from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Iterable

from research.mlops.clickhouse import sql_string


PLAN_VERSION = 1


def company_news(cutoff: datetime, *, engine_version: str, synthesis_table: str) -> str:
    start = cutoff - timedelta(days=3)
    ticker_links = "arraySort(arrayDistinct(arrayFilter(value -> notEmpty(value), arrayMap(value -> upperUTF8(trimBoth(value)), n.tickers))))"
    return f"""
        SELECT
            n.canonical_news_id,
            formatDateTime(n.published_at_utc, '%Y-%m-%dT%H:%i:%S.%fZ', 'UTC') AS published_at_utc,
            n.title, n.teaser, {ticker_links} AS tickers, n.channels, n.provider_tags,
            toUInt8(s.information_origin = 'issuer') AS is_company_news,
            s.concepts AS news_topics
        FROM q_live.benzinga_news_event_v2 AS n FINAL
        LEFT JOIN q_live.{synthesis_table} AS s FINAL
            ON s.canonical_news_id=n.canonical_news_id
            AND s.engine_version={sql_string(engine_version)}
        WHERE n.published_at_utc BETWEEN toDateTime64({_utc_sql(start)}, 3, 'UTC')
            AND toDateTime64({_utc_sql(cutoff)}, 3, 'UTC')
        ORDER BY n.published_at_utc DESC
        LIMIT 30
    """


def sec_filings(cutoff: datetime) -> str:
    start = cutoff - timedelta(days=45)
    return f"""
        SELECT cik, accession_number, company_name, form_type,
            formatDateTime(accepted_at_raw, '%Y-%m-%dT%H:%i:%S.%fZ', 'UTC') AS accepted_at_utc
        FROM
        (
            SELECT cik, accession_number, company_name, form_type, accepted_at_utc AS accepted_at_raw
            FROM q_live.sec_filing_v3 FINAL
            WHERE accepted_at_utc BETWEEN toDateTime64({_utc_sql(start)}, 3, 'UTC')
                AND toDateTime64({_utc_sql(cutoff)}, 3, 'UTC')
            ORDER BY accepted_at_utc DESC
            LIMIT 30
        )
        ORDER BY accepted_at_raw DESC
        LIMIT 30
    """


def scanner_company_news(cutoff: datetime, *, engine_version: str, synthesis_table: str) -> str:
    start = cutoff - timedelta(days=3)
    return f"""
        SELECT
            ticker,
            uniqExact(canonical_news_id) AS live_news_count,
            formatDateTime(max(published_at_utc), '%Y-%m-%dT%H:%i:%S.%fZ', 'UTC') AS latest_news_at,
            arraySort(arrayDistinct(arrayFlatten(groupArray(news_topics)))) AS news_labels
        FROM
        (
            SELECT
                n.canonical_news_id,
                n.published_at_utc,
                arrayJoin(s.tickers) AS ticker,
                toUInt8(s.information_origin = 'issuer') AS is_company_news,
                s.concepts AS news_topics
            FROM q_live.benzinga_news_event_v2 AS n FINAL
            INNER JOIN q_live.{synthesis_table} AS s FINAL
                ON s.canonical_news_id=n.canonical_news_id
                AND s.engine_version={sql_string(engine_version)}
            WHERE n.published_at_utc BETWEEN toDateTime64({_utc_sql(start)}, 3, 'UTC')
                AND toDateTime64({_utc_sql(cutoff)}, 3, 'UTC')
        )
        WHERE is_company_news AND notEmpty(ticker)
        GROUP BY ticker
    """


def scanner_sec_filings(cutoff: datetime) -> str:
    start = cutoff - timedelta(days=45)
    return f"""
        SELECT
            upperUTF8(trimBoth(b.ticker)) AS ticker,
            uniqExact(f.accession_number) AS sec_count,
            formatDateTime(max(f.accepted_at_utc), '%Y-%m-%dT%H:%i:%S.%fZ', 'UTC') AS latest_sec_at,
            arraySort(groupUniqArray(f.form_type)) AS sec_labels
        FROM q_live.sec_filing_v3 AS f FINAL
        INNER JOIN q_live.id_sec_market_bridge_v3 AS b FINAL
            ON toString(b.cik) = toString(f.cik)
            AND (b.valid_from_date IS NULL OR b.valid_from_date <= toDate(f.accepted_at_utc))
            AND (b.valid_to_date_exclusive IS NULL OR toDate(f.accepted_at_utc) < b.valid_to_date_exclusive)
        WHERE f.accepted_at_utc BETWEEN toDateTime64({_utc_sql(start)}, 3, 'UTC')
            AND toDateTime64({_utc_sql(cutoff)}, 3, 'UTC')
            AND notEmpty(b.ticker)
        GROUP BY ticker
    """


def sec_ticker_identities(ciks: Iterable[str]) -> str:
    values = ", ".join(sql_string(str(cik)) for cik in sorted(set(ciks)))
    if not values:
        raise ValueError("SEC ticker identity query requires at least one CIK")
    return f"""
        SELECT toString(cik) AS cik, argMax(upper(ticker), confidence_score) AS mapped_ticker
        FROM q_live.id_sec_market_bridge_v3 FINAL
        WHERE toString(cik) IN ({values}) AND notEmpty(ticker)
        GROUP BY cik
    """


def _utc_sql(value: datetime) -> str:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return sql_string(aware.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3])
