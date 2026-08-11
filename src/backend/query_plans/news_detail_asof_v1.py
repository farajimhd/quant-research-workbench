from __future__ import annotations

from research.mlops.clickhouse import sql_string


PLAN_VERSION = 1


def service_article(canonical_news_id: str) -> str:
    identity = _identity(canonical_news_id)
    return f"""
        SELECT
            n.* EXCEPT(published_at_utc, downloaded_at_utc, last_updated_at_utc, updated_at_utc),
            ifNull(r.rendered_text, '') AS normalized_full_text,
            ifNull(r.rendered_text_hash, '') AS text_hash,
            ifNull(r.source_count, 0) AS source_count,
            ifNull(r.block_count, 0) AS block_count,
            formatDateTime(n.published_at_utc, '%Y-%m-%dT%H:%i:%S.%fZ', 'UTC') AS published_at_utc,
            formatDateTime(n.downloaded_at_utc, '%Y-%m-%dT%H:%i:%S.%fZ', 'UTC') AS downloaded_at_utc,
            if(
                isNull(n.last_updated_at_utc),
                NULL,
                formatDateTime(assumeNotNull(n.last_updated_at_utc), '%Y-%m-%dT%H:%i:%S.%fZ', 'UTC')
            ) AS last_updated_at_utc,
            formatDateTime(n.updated_at_utc, '%Y-%m-%dT%H:%i:%S.%fZ', 'UTC') AS updated_at_utc
        FROM `q_live`.`benzinga_news_event_v2` AS n FINAL
        LEFT JOIN `q_live`.`benzinga_news_rendered_v2` AS r FINAL
            ON r.published_date=n.published_date
            AND r.provider_article_id=n.provider_article_id
            AND r.source_revision_key=n.source_revision_key
        WHERE n.canonical_news_id = {identity}
        LIMIT 1
        FORMAT JSONEachRow
    """


def service_tickers(canonical_news_id: str) -> str:
    identity = _identity(canonical_news_id)
    return f"""
        SELECT
            t.canonical_news_id, t.provider_article_id, t.ticker, t.ticker_index, t.ticker_count,
            formatDateTime(t.published_at_utc, '%Y-%m-%dT%H:%i:%S.%fZ', 'UTC') AS published_at_utc
        FROM `q_live`.`benzinga_news_ticker_v2` AS t FINAL
        INNER JOIN `q_live`.`benzinga_news_event_v2` AS n FINAL
            ON n.published_date=t.published_date
            AND n.provider_article_id=t.provider_article_id
            AND n.source_revision_key=t.source_revision_key
        WHERE t.canonical_news_id = {identity}
        ORDER BY t.ticker ASC
        FORMAT JSONEachRow
    """


def trading_article(canonical_news_id: str, *, published_date: str = "") -> str:
    identity = _identity(canonical_news_id)
    date_prewhere = (
        f"PREWHERE n.published_date = toDate({sql_string(published_date)})"
        if published_date
        else ""
    )
    return f"""
        SELECT
            n.published_date, n.provider_article_id, n.source_revision_key,
            n.title, n.article_url, n.url_domain, n.author, n.channels, n.provider_tags, n.links,
            formatDateTime(n.published_at_utc, '%Y-%m-%dT%H:%i:%S.%fZ', 'UTC') AS published_at_utc
        FROM q_live.benzinga_news_event_v2 AS n FINAL
        {date_prewhere}
        WHERE n.canonical_news_id = {identity}
        LIMIT 1
        FORMAT JSONEachRow
    """


def rendered_article(
    *, published_date: str, provider_article_id: str, source_revision_key: str
) -> str:
    if not published_date:
        raise ValueError("published_date is required for rendered News detail")
    if not provider_article_id or not source_revision_key:
        raise ValueError("provider article identity is incomplete")
    return f"""
        SELECT rendered_text AS text,
               if(source_count = 0, 'title_only', 'rendered') AS render_status
        FROM q_live.benzinga_news_rendered_v2 FINAL
        PREWHERE published_date = toDate({sql_string(published_date)})
        WHERE provider_article_id = {sql_string(provider_article_id)}
          AND source_revision_key = {sql_string(source_revision_key)}
        LIMIT 1
        FORMAT JSONEachRow
    """


def trading_tickers(canonical_news_id: str, *, published_date: str = "") -> str:
    identity = _identity(canonical_news_id)
    date_prewhere = (
        f"PREWHERE t.published_date = toDate({sql_string(published_date)})"
        if published_date
        else ""
    )
    return f"""
        SELECT ticker
        FROM q_live.benzinga_news_ticker_v2 AS t FINAL
        {date_prewhere}
        WHERE t.canonical_news_id = {identity}
        ORDER BY t.ticker ASC
        FORMAT JSONEachRow
    """


def _identity(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("canonical_news_id is required")
    return sql_string(normalized)
