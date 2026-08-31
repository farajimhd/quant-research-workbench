from __future__ import annotations

from datetime import datetime

from research.mlops.clickhouse import quote_ident, sql_string


QUERY_PLAN_ID = "news.canvas_asof.v1"
QUERY_PLAN_VERSION = 2


def trading_news_queries(
    *,
    before: str,
    before_id: str,
    cutoff: datetime,
    cursor: datetime,
    eligibility_filters: dict[str, str],
    engine_version: str,
    exact_source_id: str,
    safe_content: str,
    safe_direction: str,
    safe_eligibility: str,
    safe_kind: str,
    safe_label_state: str,
    safe_limit: int,
    safe_origin: str,
    safe_role: str,
    safe_ticker: str,
    search_term: str,
    window_start: datetime,
) -> tuple[str, str]:
    database = "q_live"
    normalized_table = "benzinga_news_event_v2"
    rendered_table = "benzinga_news_rendered_v3"
    start_sql = f"toDateTime64({sql_string(window_start.strftime('%Y-%m-%d %H:%M:%S.%f'))}, 6, 'UTC')"
    end_sql = f"toDateTime64({sql_string(cutoff.strftime('%Y-%m-%d %H:%M:%S.%f'))}, 6, 'UTC')"
    start_date_sql = sql_string(window_start.date().isoformat())
    end_date_sql = sql_string(cutoff.date().isoformat())
    cursor_sql = f"toDateTime64({sql_string(min(cursor, cutoff).strftime('%Y-%m-%d %H:%M:%S.%f'))}, 6, 'UTC')"
    cursor_id = before_id.strip()
    cursor_filter = "n.published_at_utc < page_before"
    if before.strip() and cursor_id:
        cursor_filter = f"(n.published_at_utc < page_before OR (n.published_at_utc = page_before AND n.canonical_news_id < {sql_string(cursor_id)}))"
    base_filters = [
        "n.published_date >= toDate(window_start)",
        "n.published_date <= toDate(window_end)",
        "n.published_at_utc >= window_start",
        "n.published_at_utc <= window_end",
    ]
    filters = [*base_filters, cursor_filter]
    if safe_ticker and not exact_source_id:
        filters.append(f"has(n.tickers, {sql_string(safe_ticker)})")

    def label_predicates(ticker_scope: str = "") -> tuple[str, str]:
        """Build V1-only semantic predicates; canonical News remains independently visible."""
        conditions = [
            f"l.engine_version = {sql_string(engine_version)}",
            "l.published_at_utc >= window_start",
            "l.published_at_utc <= window_end",
        ]
        ticker_sql = sql_string(ticker_scope) if ticker_scope else ""
        if ticker_scope:
            conditions.append(f"has(l.tickers, {ticker_sql})")
        if safe_role:
            conditions.append(f"l.communication_purpose = {sql_string(safe_role)}")
        if safe_origin:
            conditions.append(f"l.information_origin = {sql_string(safe_origin)}")
        if safe_direction:
            if ticker_scope:
                scoped_sentiment = f"arrayElement(l.sentiments, indexOf(l.tickers, {ticker_sql}))"
                conditions.append(f"{scoped_sentiment} = {sql_string(safe_direction)}")
            elif safe_direction == "mixed":
                conditions.append("(has(l.sentiments, 'mixed') OR length(arrayDistinct(l.sentiments)) > 1)")
            else:
                conditions.append(f"has(l.sentiments, {sql_string(safe_direction)})")
        product_columns = {
            "forecast": "forecast_tickers",
            "reaction": "reaction_tickers",
            "history": "history_tickers",
            "analyst": "analyst_tickers",
        }
        if safe_eligibility:
            column = product_columns[safe_eligibility]
            conditions.append(f"has(l.{column}, {ticker_sql})" if ticker_scope else f"notEmpty(l.{column})")
        eligibility_columns = {
            "forecast_eligible": "forecast_tickers",
            "reaction_eligible": "reaction_tickers",
            "history_eligible": "history_tickers",
            "analyst_eligible": "analyst_tickers",
        }
        for name, value in eligibility_filters.items():
            if not value:
                continue
            column = eligibility_columns[name]
            expression = f"has(l.{column}, {ticker_sql})" if ticker_scope else f"notEmpty(l.{column})"
            conditions.append(expression if value == "eligible" else f"NOT ({expression})")
        where = " AND ".join(conditions)
        label_exists_sql = (
            "n.canonical_news_id IN (SELECT canonical_news_id "
            "FROM q_live.news_synthesis_v1 AS l FINAL "
            f"WHERE {where})"
        )
        quality_exists_sql = (
            "n.canonical_news_id IN (SELECT canonical_news_id "
            "FROM q_live.news_synthesis_v1 AS l FINAL "
            f"WHERE l.engine_version = {sql_string(engine_version)} "
            "AND l.published_at_utc >= window_start AND l.published_at_utc <= window_end "
            "AND notEmpty(l.quality_flags))"
        )
        return label_exists_sql, quality_exists_sql

    label_exists, quality_label_exists = label_predicates(safe_ticker)
    facet_label_exists, facet_quality_label_exists = label_predicates()
    has_label_filters = bool(safe_role or safe_origin or safe_direction or safe_eligibility or any(eligibility_filters.values()))
    facet_filters = list(base_filters)
    if has_label_filters and not exact_source_id:
        filters.append(label_exists)
        facet_filters.append(facet_label_exists)
    if safe_label_state == "classified" and not exact_source_id:
        filters.append(label_exists)
        facet_filters.append(facet_label_exists)
    elif safe_label_state == "pending" and not exact_source_id:
        filters.append(f"NOT ({label_exists})")
        facet_filters.append(f"NOT ({facet_label_exists})")
    elif safe_label_state == "quality" and not exact_source_id:
        filters.append(quality_label_exists)
        facet_filters.append(facet_quality_label_exists)
    if search_term:
        if exact_source_id:
            filters.append(f"n.canonical_news_id = {sql_string(exact_source_id)}")
            facet_filters.append(f"n.canonical_news_id = {sql_string(exact_source_id)}")
        else:
            escaped = sql_string(search_term)
            search_filter = (
                "positionCaseInsensitiveUTF8(concat("
                "ifNull(n.canonical_news_id, ''), ' ', ifNull(n.provider_article_id, ''), ' ', "
                "arrayStringConcat(n.tickers, ' '), ' ', ifNull(n.title, ''), ' ', "
                "ifNull(r.canonical_body_text, ''), ' ', ifNull(n.author, ''), ' ', "
                f"ifNull(n.url_domain, '')), {escaped}) > 0"
            )
            filters.append(search_filter)
            facet_filters.append(search_filter)
    # Exact source identity is authoritative. Completeness is presentation
    # metadata and must never make a known record undiscoverable.
    if safe_content == "full" and not exact_source_id:
        filters.append("ifNull(r.body_status, 'missing') IN ('complete', 'partial')")
        facet_filters.append("ifNull(r.body_status, 'missing') IN ('complete', 'partial')")
    elif safe_content == "title" and not exact_source_id:
        filters.append("ifNull(r.body_status, 'missing') = 'missing'")
        facet_filters.append("ifNull(r.body_status, 'missing') = 'missing'")
    ticker_links_sql = (
        "arraySort(arrayDistinct(arrayFilter(value -> notEmpty(value), "
        "arrayMap(value -> upperUTF8(trimBoth(value)), n.tickers))))"
    )
    classification_sql = {
        "kind": "'market'",
        "scope": f"multiIf(length({ticker_links_sql})=1,'single_ticker',length({ticker_links_sql})>1,'multi_ticker','market_wide')",
        "origin": "'unknown'",
        "format": "'general'",
        "topics": "CAST([], 'Array(String)')",
        "company": "toUInt8(0)",
        "confidence": "toFloat64(0)",
        "evidence": "['news_synthesis_pending']",
    }
    news_kind_sql = classification_sql["kind"]
    if safe_kind != "all" and not exact_source_id:
        kind_conditions = {
            "why_moving": "l.communication_purpose='explain_move'",
            "analyst": "l.information_origin='analyst'",
            "regulatory": "l.information_origin='regulator'",
            "market": "l.document_structure IN ('market_overview','reference_list')",
            "multi": "l.document_structure='multi_subject_digest'",
            "company": "l.information_origin='issuer'",
            "editorial": "l.information_origin IN ('editorial','mixed','unknown')",
        }
        condition = kind_conditions.get(safe_kind, "0")
        kind_filter = (
            "n.canonical_news_id IN (SELECT canonical_news_id "
            "FROM q_live.news_synthesis_v1 AS l FINAL "
            f"WHERE l.engine_version={sql_string(engine_version)} "
            f"AND l.published_at_utc>=window_start AND l.published_at_utc<=window_end AND ({condition}))"
        )
        filters.append(kind_filter)
        facet_filters.append(kind_filter)
    where_sql = " AND ".join(filters)
    facet_where_sql = " AND ".join(facet_filters)
    source_cursor_filter = "published_at_utc < page_before"
    if before.strip() and cursor_id:
        source_cursor_filter = (
            "(published_at_utc < page_before OR "
            f"(published_at_utc = page_before AND canonical_news_id < {sql_string(cursor_id)}))"
        )
    source_ticker_filter = (
        f"AND has(tickers, {sql_string(safe_ticker)})" if safe_ticker and not exact_source_id else ""
    )
    if exact_source_id:
        source_ticker_filter += f"\n              AND canonical_news_id = {sql_string(exact_source_id)}"
    rendered_source_filter = (
        f"AND canonical_news_id = {sql_string(exact_source_id)}" if exact_source_id else ""
    )
    source_label_filter = label_exists.replace("n.canonical_news_id", "canonical_news_id")
    if (has_label_filters or safe_label_state == "classified") and not exact_source_id:
        source_ticker_filter += f"\n              AND {source_label_filter}"
    elif safe_label_state == "pending" and not exact_source_id:
        source_ticker_filter += f"\n              AND NOT ({source_label_filter})"
    elif safe_label_state == "quality" and not exact_source_id:
        source_ticker_filter += f"\n              AND {quality_label_exists.replace('n.canonical_news_id', 'canonical_news_id')}"
    can_limit_event_source = not any(
        (
            search_term,
            safe_content != "all",
            safe_kind != "all",
        )
    )
    source_limit_sql = (
        f"ORDER BY published_at_utc DESC, canonical_news_id DESC LIMIT {safe_limit + 1}"
        if can_limit_event_source
        else ""
    )
    query = f"""
        WITH
            {start_sql} AS window_start,
            {end_sql} AS window_end,
            {cursor_sql} AS page_before
        SELECT
            n.canonical_news_id,
            formatDateTime(n.published_at_utc, '%Y-%m-%dT%H:%i:%S.%fZ', 'UTC') AS published_at_utc,
            n.title, n.article_url, n.url_domain, n.author, n.channels, n.provider_tags,
            {ticker_links_sql} AS ticker_link_sample,
            length(ticker_link_sample) AS ticker_link_count,
            {news_kind_sql} AS news_kind,
            {classification_sql["scope"]} AS news_scope,
            {classification_sql["origin"]} AS news_origin,
            {classification_sql["format"]} AS news_format,
            {classification_sql["topics"]} AS news_topics,
            {classification_sql["company"]} AS is_company_news,
            {classification_sql["confidence"]} AS classification_confidence,
            {classification_sql["evidence"]} AS classification_evidence,
            has(n.content_quality_flags, 'external_text') AS has_external_text,
            has(n.content_quality_flags, 'pdf_text') AS has_pdf,
            ifNull(r.body_status, 'missing') = 'missing' AS is_title_only,
            ifNull(r.body_status, 'missing') AS render_status,
            lengthUTF8(ifNull(r.canonical_body_text, '')) AS full_text_chars,
            substring(ifNull(r.canonical_body_text, ''), 1, 320) AS text_preview
        FROM
        (
            SELECT *
            FROM {quote_ident(database)}.{quote_ident(normalized_table)} FINAL
            PREWHERE published_date >= toDate({start_date_sql})
              AND published_date <= toDate({end_date_sql})
            WHERE published_at_utc >= {start_sql}
              AND published_at_utc <= {end_sql}
              AND {source_cursor_filter}
              {source_ticker_filter}
            {source_limit_sql}
        ) AS n
        LEFT JOIN
        (
            SELECT *
            FROM {quote_ident(database)}.{quote_ident(rendered_table)} FINAL
            PREWHERE published_date >= toDate({start_date_sql})
              AND published_date <= toDate({end_date_sql})
            WHERE published_at_utc >= {start_sql}
              AND published_at_utc <= {end_sql}
              {rendered_source_filter}
        ) AS r
            ON r.published_date=n.published_date
            AND r.provider_article_id=n.provider_article_id
        WHERE {where_sql}
        ORDER BY n.published_at_utc DESC, n.canonical_news_id DESC
        LIMIT {safe_limit + 1}
        FORMAT JSONEachRow
    """
    facet_query = f"""
        WITH
            {start_sql} AS window_start,
            {end_sql} AS window_end
        SELECT arraySort(groupUniqArray(ticker)) AS ticker_options
        FROM
        (
            SELECT arrayJoin({ticker_links_sql}) AS ticker
            FROM
            (
                SELECT *
                FROM {quote_ident(database)}.{quote_ident(normalized_table)} FINAL
                PREWHERE published_date >= toDate({start_date_sql})
                  AND published_date <= toDate({end_date_sql})
                WHERE published_at_utc >= {start_sql}
                  AND published_at_utc <= {end_sql}
            ) AS n
            LEFT JOIN
            (
                SELECT *
                FROM {quote_ident(database)}.{quote_ident(rendered_table)} FINAL
                PREWHERE published_date >= toDate({start_date_sql})
                  AND published_date <= toDate({end_date_sql})
                WHERE published_at_utc >= {start_sql}
                  AND published_at_utc <= {end_sql}
            ) AS r
                ON r.published_date=n.published_date
                AND r.provider_article_id=n.provider_article_id
            WHERE {facet_where_sql}
        )
        FORMAT JSONEachRow
    """
    return query, facet_query
