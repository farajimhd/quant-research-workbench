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


def scanner_company_news(
    cutoff: datetime,
    *,
    engine_version: str,
    synthesis_table: str,
    tickers: Iterable[str] = (),
) -> str:
    start = cutoff - timedelta(days=3)
    ticker_values = tuple(
        sorted({str(value).strip().upper() for value in tickers if str(value).strip()})
    )
    ticker_filter = (
        f" AND ticker IN ({', '.join(sql_string(value) for value in ticker_values)})"
        if ticker_values else ""
    )
    return f"""
        SELECT
            canonical_news_id,
            formatDateTime(published_at_utc, '%Y-%m-%dT%H:%i:%S.%fZ', 'UTC') AS published_at_utc,
            title,
            tupleElement(issuer_view, 1) AS ticker,
            tupleElement(issuer_view, 2) AS synthesis_direction,
            document_structure,
            communication_purpose,
            information_origin,
            text_availability,
            concepts AS news_labels,
            quality_flags
        FROM
        (
            SELECT
                n.canonical_news_id,
                n.published_at_utc,
                n.title,
                arrayJoin(arrayZip(s.tickers, s.sentiments)) AS issuer_view,
                s.document_structure,
                s.communication_purpose,
                s.information_origin,
                s.text_availability,
                s.concepts,
                s.quality_flags
            FROM q_live.benzinga_news_event_v2 AS n FINAL
            INNER JOIN q_live.{synthesis_table} AS s FINAL
                ON s.canonical_news_id=n.canonical_news_id
                AND s.engine_version={sql_string(engine_version)}
            WHERE n.published_at_utc BETWEEN toDateTime64({_utc_sql(start)}, 3, 'UTC')
                AND toDateTime64({_utc_sql(cutoff)}, 3, 'UTC')
        )
        WHERE notEmpty(ticker){ticker_filter}
        ORDER BY published_at_utc DESC, canonical_news_id
        LIMIT 10000
    """


def ticker_news_recency(
    cutoff: datetime,
    *,
    tickers: Iterable[str],
) -> str:
    """Return recency for any authoritative news item linked to each ticker."""

    start = cutoff - timedelta(days=3)
    ticker_values = tuple(
        sorted({str(value).strip().upper() for value in tickers if str(value).strip()})
    )
    if not ticker_values:
        raise ValueError("Ticker news recency requires at least one ticker")
    return f"""
        SELECT
            ticker,
            uniqExact(canonical_news_id) AS live_news_count,
            formatDateTime(max(published_at_utc), '%Y-%m-%dT%H:%i:%S.%fZ', 'UTC') AS latest_news_at
        FROM
        (
            SELECT
                canonical_news_id,
                published_at_utc,
                arrayJoin(arrayMap(value -> upperUTF8(trimBoth(value)), tickers)) AS ticker
            FROM q_live.benzinga_news_event_v2 FINAL
            PREWHERE published_date BETWEEN toDate({_utc_sql(start)}) AND toDate({_utc_sql(cutoff)})
            WHERE published_at_utc BETWEEN toDateTime64({_utc_sql(start)}, 3, 'UTC')
                AND toDateTime64({_utc_sql(cutoff)}, 3, 'UTC')
                AND hasAny(
                    arrayMap(value -> upperUTF8(trimBoth(value)), tickers),
                    [{', '.join(sql_string(value) for value in ticker_values)}]
                )
        )
        WHERE ticker IN ({', '.join(sql_string(value) for value in ticker_values)})
        GROUP BY ticker
    """


def scanner_sec_filings(cutoff: datetime, *, tickers: Iterable[str] = ()) -> str:
    start = cutoff - timedelta(days=45)
    ticker_values = tuple(
        sorted({str(value).strip().upper() for value in tickers if str(value).strip()})
    )
    ticker_filter = (
        f" AND upperUTF8(trimBoth(b.ticker)) IN ({', '.join(sql_string(value) for value in ticker_values)})"
        if ticker_values else ""
    )
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
            {ticker_filter}
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
