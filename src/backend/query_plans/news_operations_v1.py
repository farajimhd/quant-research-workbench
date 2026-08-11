from __future__ import annotations

from datetime import UTC, datetime

from research.mlops.clickhouse import sql_string


PLAN_VERSION = 1


def intraday_histogram(
    window_start_utc: datetime,
    window_end_utc: datetime,
    *,
    bin_seconds: int,
) -> str:
    safe_bin = max(1, int(bin_seconds))
    bin_count = int(
        ((window_end_utc - window_start_utc).total_seconds() + safe_bin - 1)
        // safe_bin
    )
    return f"""
        WITH
            {_datetime64(window_start_utc)} AS window_start,
            {_datetime64(window_end_utc)} AS window_end,
            news_counts AS
            (
                SELECT
                    toUInt64(intDiv(dateDiff('second', window_start, n.published_at_utc) + {safe_bin // 2}, {safe_bin})) AS bucket_index,
                    toUInt64(countIf(length(n.tickers) = 1)) AS single_ticker_rows,
                    toUInt64(countIf(length(n.tickers) != 1)) AS broad_or_none_rows,
                    toUInt64(count()) AS total_rows
                FROM `q_live`.`benzinga_news_event_v2` AS n FINAL
                WHERE n.published_at_utc >= window_start
                  AND n.published_at_utc < window_end
                GROUP BY bucket_index
            )
        SELECT
            formatDateTime(
                window_start + toIntervalSecond(toInt64(b.bucket_index) * {safe_bin}),
                '%Y-%m-%dT%H:%i:%S.000Z',
                'UTC'
            ) AS bucket_utc,
            toUInt64(ifNull(c.single_ticker_rows, 0)) AS single_ticker_rows,
            toUInt64(ifNull(c.broad_or_none_rows, 0)) AS broad_or_none_rows,
            toUInt64(ifNull(c.total_rows, 0)) AS total_rows
        FROM
        (
            SELECT toUInt64(number) AS bucket_index
            FROM numbers({bin_count + 1})
        ) AS b
        LEFT JOIN news_counts AS c
            ON c.bucket_index = b.bucket_index
        ORDER BY b.bucket_index
        FORMAT JSONEachRow
    """


def today_summary(window_start_utc: datetime, window_end_utc: datetime) -> str:
    return f"""
        WITH
            {_datetime64(window_start_utc)} AS window_start,
            {_datetime64(window_end_utc)} AS window_end
        SELECT
            toUInt64(count()) AS total_rows,
            toUInt64(countIf(length(n.tickers) = 1)) AS one_ticker_rows,
            toUInt64(countIf(length(n.tickers) > 1)) AS multi_ticker_rows,
            toUInt64(countIf(length(n.tickers) = 0)) AS no_ticker_rows,
            toUInt64(countIf(length(n.tickers) > 0)) AS with_ticker_rows,
            toUInt64(countIf(has(n.content_quality_flags, 'external_text'))) AS external_text_rows,
            toUInt64(countIf(has(n.content_quality_flags, 'pdf_text'))) AS pdf_rows,
            formatDateTime(max(n.published_at_utc), '%Y-%m-%dT%H:%i:%S.%fZ', 'UTC') AS latest_published_at_utc
        FROM `q_live`.`benzinga_news_event_v2` AS n FINAL
        WHERE n.published_at_utc >= window_start
          AND n.published_at_utc < window_end
        FORMAT JSONEachRow
    """


def today_rows(
    window_start_utc: datetime,
    window_end_utc: datetime,
    *,
    limit: int,
    ascending: bool,
) -> str:
    safe_limit = max(1, min(int(limit), 1_000))
    direction = "ASC" if ascending else "DESC"
    return f"""
        WITH
            {_datetime64(window_start_utc)} AS window_start,
            {_datetime64(window_end_utc)} AS window_end
        SELECT
            n.canonical_news_id,
            n.provider_article_id,
            formatDateTime(n.published_at_utc, '%Y-%m-%dT%H:%i:%S.%fZ', 'UTC') AS published_at_utc,
            formatDateTime(n.downloaded_at_utc, '%Y-%m-%dT%H:%i:%S.%fZ', 'UTC') AS downloaded_at_utc,
            n.title,
            n.normalized_title,
            n.article_url,
            n.url_domain,
            n.author,
            n.tickers,
            n.channels,
            n.provider_tags,
            length(n.tickers) AS ticker_link_count,
            arraySort(n.tickers) AS ticker_link_sample,
            ifNull(r.source_count, 0) > 0 AS has_body,
            notEmpty(r.canonical_news_id) AND ifNull(r.source_count, 0) = 0 AS is_title_only,
            has(n.content_quality_flags, 'external_text') AS has_external_text,
            has(n.content_quality_flags, 'pdf_text') AS has_pdf,
            '' AS external_fetch_status,
            '' AS pdf_extract_status,
            n.content_quality_flags,
            0 AS body_chars,
            0 AS external_chars,
            0 AS pdf_chars,
            lengthUTF8(ifNull(r.rendered_text, '')) AS full_text_chars,
            substring(ifNull(r.rendered_text, ''), 1, 240) AS text_preview
        FROM `q_live`.`benzinga_news_event_v2` AS n FINAL
        LEFT JOIN `q_live`.`benzinga_news_rendered_v2` AS r FINAL
            ON r.published_date=n.published_date
            AND r.provider_article_id=n.provider_article_id
            AND r.source_revision_key=n.source_revision_key
        WHERE n.published_at_utc >= window_start
          AND n.published_at_utc < window_end
        ORDER BY n.published_at_utc {direction}, n.provider_article_id {direction}
        LIMIT {safe_limit}
        FORMAT JSONEachRow
    """


def _datetime64(value: datetime) -> str:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    formatted = aware.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
    return f"toDateTime64({sql_string(formatted)}, 6, 'UTC')"
