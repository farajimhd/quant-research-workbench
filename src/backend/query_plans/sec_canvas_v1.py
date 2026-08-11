from __future__ import annotations

from datetime import UTC, datetime, timedelta

from research.mlops.clickhouse import quote_ident, sql_string


QUERY_PLAN_ID = "sec.canvas_asof.v1"
QUERY_PLAN_VERSION = 1


def filing_document_ids_sql(
    keys: list[tuple[str, str]], cutoff: datetime, database: str
) -> str:
    if not keys:
        return "SELECT '' AS accession_number, '' AS document_id WHERE 0 FORMAT JSONEachRow"
    db = quote_ident(database)
    key_sql = ",".join(
        f"({sql_string(cik)},{sql_string(accession)})"
        for cik, accession in keys
    )
    partitions = ",".join(
        f"cityHash64({sql_string(cik)}) % 64"
        for cik in sorted({cik for cik, _ in keys if cik})
    )
    instant = sql_string(clickhouse_timestamp(cutoff))
    return f"""
SELECT accession_number, document_id
FROM {db}.sec_filing_document_v3
PREWHERE cityHash64(cik) % 64 IN ({partitions or '0'})
  AND (cik, accession_number) IN ({key_sql})
WHERE (cik, accession_number) IN ({key_sql})
  AND source_revision_at <= parseDateTime64BestEffort({instant})
ORDER BY source_revision_rank DESC, inserted_at DESC
LIMIT 1 BY cik, accession_number, document_id
FORMAT JSONEachRow
"""


def filing_label_sql(column: str = "form_type") -> str:
    _ = column
    return "'other_disclosure'"


def taxonomy_cte_sql(database: str) -> str:
    db = quote_ident(database)
    return f"""approved_form_taxonomy AS
        (
            SELECT upper(submitted_type) AS form_key,
                   argMax(category, updated_at_utc) AS category,
                   argMax(canonical_title, updated_at_utc) AS canonical_title,
                   argMax(impact_label, updated_at_utc) AS impact_label,
                   argMax(impact_score, updated_at_utc) AS impact_score,
                   argMax(affected_security_scope, updated_at_utc) AS affected_security_scope,
                   argMax(impact_rationale, updated_at_utc) AS impact_rationale,
                   argMax(taxonomy_version, updated_at_utc) AS taxonomy_version
            FROM {db}.sec_disclosure_taxonomy_v3
            WHERE taxonomy_scope = 'form' AND match_kind = 'exact' AND classification_status = 'approved'
            GROUP BY form_key
        )"""


def taxonomy_labels_sql(database: str) -> str:
    db = quote_ident(database)
    return f"""
        SELECT category AS id,
               concat(upper(substringUTF8(replaceAll(category, '_', ' '), 1, 1)), substringUTF8(replaceAll(category, '_', ' '), 2)) AS label
        FROM {db}.sec_disclosure_taxonomy_v3
        WHERE taxonomy_scope = 'form' AND match_kind = 'exact' AND classification_status = 'approved'
        GROUP BY category
        ORDER BY max(impact_score) DESC, label
        FORMAT JSONEachRow
    """


def filing_list_sql(*, cutoff: datetime, database: str, label: str, limit: int, lookback_hours: int, search: str, ticker: str, before: datetime | None, before_accession: str, content: str = "all", window_start: datetime | None = None, impact: str = "", label_state: str = "", role: str = "", origin: str = "", direction: str = "", security_scope: str = "", ticker_label: str = "", eligibility_filters: dict[str, str] | None = None) -> str:
    db = quote_ident(database)
    instant = sql_string(clickhouse_timestamp(cutoff))
    start = sql_string(clickhouse_timestamp(window_start or (cutoff - timedelta(hours=lookback_hours))))
    ticker_bridge = ""
    ticker_source_filter = ""
    ticker_join = ""
    if ticker:
        ticker_sql = sql_string(ticker)
        ticker_bridge = f"""(
            SELECT cik, valid_from_date, valid_to_date_exclusive
            FROM {db}.id_sec_market_bridge_v3 FINAL
            WHERE upper(ifNull(ticker, '')) = {ticker_sql}
        )"""
        ticker_source_filter = f"""
          AND cik IN (SELECT cik FROM {ticker_bridge})"""
        ticker_join = f"""
            INNER JOIN {ticker_bridge} AS b ON b.cik = f.cik"""
    bounded_filings = f"""(
        SELECT *
        FROM {db}.sec_filing_v3 FINAL
        PREWHERE accepted_at_utc >= parseDateTime64BestEffort({start})
          AND accepted_at_utc <= parseDateTime64BestEffort({instant})
          {ticker_source_filter}
        WHERE accepted_at_utc >= parseDateTime64BestEffort({start})
          AND accepted_at_utc <= parseDateTime64BestEffort({instant})
    )"""
    conditions = [
        f"f.accepted_at_utc >= parseDateTime64BestEffort({start})",
        f"f.accepted_at_utc <= parseDateTime64BestEffort({instant})",
    ]
    if search.strip():
        value = sql_string(search.strip())
        conditions.append(f"(positionCaseInsensitiveUTF8(ifNull(f.company_name, ''), {value}) > 0 OR positionCaseInsensitiveUTF8(f.form_type, {value}) > 0 OR positionCaseInsensitiveUTF8(f.accession_number, {value}) > 0 OR positionCaseInsensitiveUTF8(ifNull(f.items, ''), {value}) > 0)")
    if ticker:
        conditions.append(
            "(b.valid_from_date IS NULL OR b.valid_from_date <= toDate(f.accepted_at_utc)) "
            "AND (b.valid_to_date_exclusive IS NULL OR "
            "toDate(f.accepted_at_utc) < b.valid_to_date_exclusive)"
        )
    if content == "readable":
        conditions.append(f"f.accession_number IN (SELECT accession_number FROM {db}.sec_filing_text_rendered_v3 WHERE source_archive_date BETWEEN toDate({start}) AND toDate({instant}) AND source_revision_at <= parseDateTime64BestEffort({instant}) GROUP BY accession_number HAVING count() > 0)")
    elif content == "xbrl":
        conditions.append(f"f.accession_number IN (SELECT accession_number FROM {db}.sec_xbrl_company_fact_v3 FINAL WHERE filed_at_utc BETWEEN parseDateTime64BestEffort({start}) AND parseDateTime64BestEffort({instant}) GROUP BY accession_number HAVING count() > 0)")
    if before:
        before_sql = sql_string(clickhouse_timestamp(before))
        accession_sql = sql_string(before_accession)
        conditions.append(f"(f.accepted_at_utc < parseDateTime64BestEffort({before_sql}) OR (f.accepted_at_utc = parseDateTime64BestEffort({before_sql}) AND f.accession_number < {accession_sql}))")
    active_eligibility = eligibility_filters or {}
    has_intelligence_filter = bool(role or origin or direction or any(active_eligibility.values()))
    if has_intelligence_filter or label_state in {"classified", "quality"}:
        conditions.append(
            f"f.accession_number IN ({sec_label_accessions_sql(cutoff=cutoff, database=database, direction=direction, eligibility_filters=active_eligibility, origin=origin, role=role, start=window_start or (cutoff - timedelta(hours=lookback_hours)), ticker=ticker_label, quality_only=label_state == 'quality')})"
        )
    elif label_state == "pending":
        conditions.append(
            f"f.accession_number NOT IN ({sec_label_accessions_sql(cutoff=cutoff, database=database, direction='', eligibility_filters={}, origin='', role='', start=window_start or (cutoff - timedelta(hours=lookback_hours)), ticker=ticker_label, quality_only=False)})"
        )
    outer_conditions: list[str] = []
    if label:
        outer_conditions.append(f"filing_label = {sql_string(label)}")
    if impact:
        outer_conditions.append(f"impact_score = {int(impact)}")
    if security_scope:
        outer_conditions.append(f"lowerUTF8(affected_security_scope) = {sql_string(security_scope)}")
    outer = f"WHERE {' AND '.join(outer_conditions)}" if outer_conditions else ""
    return f"""
        WITH {taxonomy_cte_sql(database)}
        SELECT *
        FROM
        (
            SELECT f.filing_id, f.accession_number, f.accession_number_compact, toString(f.cik) AS cik, f.company_name,
                   f.form_type, f.filing_date, f.report_date, f.accepted_at_utc, f.accepted_at_source, f.primary_document,
                   f.primary_document_url, f.filing_detail_url, f.filing_size, f.items, f.text_status,
                   if(empty(t.category), {filing_label_sql('f.form_type')}, t.category) AS filing_label,
                   t.canonical_title AS disclosure_title, t.impact_label, t.impact_score,
                   t.affected_security_scope, t.impact_rationale, t.taxonomy_version
            FROM {bounded_filings} AS f
            {ticker_join}
            LEFT JOIN approved_form_taxonomy AS t ON t.form_key = upper(f.form_type)
            WHERE {' AND '.join(conditions)}
        )
        {outer}
        ORDER BY accepted_at_utc DESC, accession_number DESC
        LIMIT {int(limit)}
        FORMAT JSONEachRow
    """


def sec_label_accessions_sql(*, cutoff: datetime, database: str, direction: str, eligibility_filters: dict[str, str], origin: str, role: str, start: datetime, ticker: str, quality_only: bool) -> str:
    """Return filing accessions whose aggregate V5 document labels match the query."""
    db = quote_ident(database)
    instant = sql_string(clickhouse_timestamp(cutoff))
    start_sql = sql_string(clickhouse_timestamp(start))
    where = [
        "l.corpus = 'sec'",
        "l.labeling_version = 'scoped_text_labeling_v5'",
        f"l.source_timestamp >= parseDateTime64BestEffort({start_sql})",
        f"l.source_timestamp <= parseDateTime64BestEffort({instant})",
    ]
    if ticker:
        where.append(f"l.ticker = {sql_string(ticker)}")
    having: list[str] = []
    if role:
        having.append(f"countIf(l.content_role = {sql_string(role)}) > 0")
    if origin:
        having.append(f"countIf(l.source_origin = {sql_string(origin)}) > 0")
    if direction == "mixed":
        having.append("uniqExact(l.semantic_direction) > 1 OR countIf(l.semantic_direction = 'mixed') > 0")
    elif direction:
        having.append(f"uniqExact(l.semantic_direction) = 1 AND any(l.semantic_direction) = {sql_string(direction)}")
    expressions = {
        "forecast_eligible": "l.forecast_trigger_eligible",
        "reaction_eligible": "l.reaction_evaluation_eligible",
        "history_eligible": "l.issuer_history_context_eligible",
        "prior_context_eligible": "JSONExtractBool(l.classification_json, 'prior_primary_context_eligible')",
        "followup_eligible": "JSONExtractBool(l.classification_json, 'episode_followup_eligible')",
    }
    for name, value in eligibility_filters.items():
        if value:
            having.append(f"max({expressions[name]}) = {1 if value == 'eligible' else 0}")
    if quality_only:
        having.append("countIf(position(l.classification_json, 'quality_flags') > 0 AND position(l.classification_json, '[]') = 0) > 0")
    having_sql = f" HAVING {' AND '.join(f'({item})' for item in having)}" if having else ""
    return f"""
        SELECT d.accession_number
        FROM
        (
            SELECT accession_number, document_id
            FROM {db}.sec_filing_document_v3
            PREWHERE source_archive_date >= toDate(parseDateTime64BestEffort({start_sql}))
              AND source_archive_date <= toDate(parseDateTime64BestEffort({instant}))
            WHERE source_revision_at <= parseDateTime64BestEffort({instant})
            ORDER BY source_revision_rank DESC, inserted_at DESC
            LIMIT 1 BY cik, accession_number, document_id
        ) AS d
        INNER JOIN {db}.scoped_text_labels_v5 AS l FINAL ON l.source_id = d.document_id
        WHERE {' AND '.join(where)}
        GROUP BY d.accession_number{having_sql}
    """


def coverage_sql(keys: list[tuple[str, str]], cutoff: datetime, database: str) -> str:
    if not keys:
        return "SELECT 1 WHERE 0 FORMAT JSONEachRow"
    db = quote_ident(database)
    key_clause = ", ".join(f"({sql_string(cik)}, {sql_string(accession)})" for cik, accession in keys)
    instant = sql_string(clickhouse_timestamp(cutoff))
    return f"""
        SELECT cik, accession_number, max(document_rows) AS document_rows, max(text_rows) AS text_rows,
               max(text_chars) AS text_chars, max(xbrl_rows) AS xbrl_rows
        FROM
        (
            SELECT cik, accession_number, count() AS document_rows, 0 AS text_rows, 0 AS text_chars, 0 AS xbrl_rows
            FROM
            (
                SELECT toString(argMax(cik, tuple(source_revision_rank, inserted_at))) AS cik, accession_number, document_id
                FROM {db}.sec_filing_document_v3
                WHERE (toString(cik), accession_number) IN ({key_clause})
                  AND source_revision_at <= parseDateTime64BestEffort({instant})
                GROUP BY accession_number, document_id
            )
            GROUP BY cik, accession_number
            UNION ALL
            SELECT cik, accession_number, 0, count(), sum(text_char_count), 0
            FROM
            (
                SELECT toString(argMax(cik, tuple(source_revision_rank, inserted_at))) AS cik, accession_number, document_id,
                       argMax(text_char_count, tuple(source_revision_rank, inserted_at)) AS text_char_count
                FROM {db}.sec_filing_text_rendered_v3
                WHERE (toString(cik), accession_number) IN ({key_clause})
                  AND source_revision_at <= parseDateTime64BestEffort({instant})
                GROUP BY accession_number, document_id
            )
            GROUP BY cik, accession_number
            UNION ALL
            SELECT toString(cik), accession_number, 0, 0, 0, count()
            FROM {db}.sec_xbrl_company_fact_v3 FINAL
            WHERE (toString(cik), accession_number) IN ({key_clause}) AND filed_at_utc <= parseDateTime64BestEffort({instant})
            GROUP BY cik, accession_number
        )
        GROUP BY cik, accession_number
        FORMAT JSONEachRow
    """


def filing_entities_sql(keys: list[tuple[str, str]], cutoff: datetime, database: str) -> str:
    if not keys:
        return "SELECT 1 WHERE 0 FORMAT JSONEachRow"
    db = quote_ident(database)
    accessions = ", ".join(sql_string(accession) for _, accession in keys)
    instant = sql_string(clickhouse_timestamp(cutoff))
    return f"""
        SELECT accession_number,
               argMax(entity_cik, tuple(source_revision_rank, inserted_at)) AS entity_cik,
               argMax(entity_role, tuple(source_revision_rank, inserted_at)) AS entity_role
        FROM {db}.sec_filing_entity_v3
        WHERE accession_number IN ({accessions})
          AND source_revision_at <= parseDateTime64BestEffort({instant})
        GROUP BY accession_number, relationship_id
        HAVING entity_role IN ('issuer', 'subject_company')
        FORMAT JSONEachRow
    """


def detail_filing_entities_sql(cik: str, accession: str, cutoff: datetime, database: str) -> str:
    db = quote_ident(database)
    instant = sql_string(clickhouse_timestamp(cutoff))
    return f"""SELECT relationship_id,
        argMax(primary_cik, tuple(source_revision_rank, inserted_at)) AS filing_cik,
        argMax(entity_cik, tuple(source_revision_rank, inserted_at)) AS entity_cik,
        argMax(entity_role, tuple(source_revision_rank, inserted_at)) AS entity_role,
        argMax(entity_name, tuple(source_revision_rank, inserted_at)) AS entity_name,
        argMax(source_revision_kind, tuple(source_revision_rank, inserted_at)) AS source_revision_kind,
        argMax(source_revision_at, tuple(source_revision_rank, inserted_at)) AS latest_source_revision_at
        FROM {db}.sec_filing_entity_v3
        WHERE primary_cik = {sql_string(cik)} AND accession_number = {sql_string(accession)}
          AND source_revision_at <= parseDateTime64BestEffort({instant})
        GROUP BY relationship_id
        ORDER BY entity_role, entity_name, entity_cik FORMAT JSONEachRow"""


def identity_sql(ciks: list[str], cutoff: datetime, database: str) -> str:
    if not ciks:
        return "SELECT 1 WHERE 0 FORMAT JSONEachRow"
    db = quote_ident(database)
    values = ", ".join(sql_string(cik) for cik in ciks)
    instant = sql_string(clickhouse_timestamp(cutoff))
    return f"""
        SELECT b.cik AS cik, b.ticker AS ticker, b.mapping_status AS mapping_status, b.confidence_score AS confidence_score,
               b.valid_from_date AS valid_from_date, b.valid_to_date_exclusive AS valid_to_date_exclusive,
               issuer.issuer_name, issuer.legal_name, issuer.sic_description,
               listing.exchange_code, listing.currency_code, listing.ibkr_conid,
               sym.primary_symbol_flag
        FROM {db}.id_sec_market_bridge_v3 AS b FINAL
        LEFT JOIN {db}.id_issuer_v1 AS issuer FINAL ON issuer.issuer_id = b.issuer_id
        LEFT JOIN {db}.id_listing_v1 AS listing FINAL ON listing.listing_id = ifNull(b.listing_id, '')
        LEFT JOIN {db}.id_symbol_v1 AS sym FINAL ON sym.symbol_id = ifNull(b.symbol_id, '')
        WHERE b.cik IN ({values})
        ORDER BY b.cik, sym.primary_symbol_flag DESC, b.confidence_score DESC, b.ticker
        FORMAT JSONEachRow
    """


def filing_detail_sql(cik: str, accession: str, cutoff: datetime, database: str, *, accepted_date: str = "") -> str:
    db = quote_ident(database)
    instant = sql_string(clickhouse_timestamp(cutoff))
    accepted_prewhere = f"PREWHERE toDate(f.accepted_at_utc) = toDate({sql_string(accepted_date)})" if accepted_date else ""
    return f"""
        WITH {taxonomy_cte_sql(database)}
        SELECT f.filing_id, f.accession_number, f.accession_number_compact, toString(f.cik) AS cik, f.company_name,
               f.form_type, f.filing_date, f.report_date, f.accepted_at_utc, f.acceptance_datetime_raw,
               f.accepted_at_source, f.primary_document, f.primary_document_url, f.filing_detail_url,
               f.filing_size, f.items, f.text_status,
               if(empty(t.category), {filing_label_sql('f.form_type')}, t.category) AS filing_label,
               t.canonical_title AS disclosure_title, t.impact_label, t.impact_score,
               t.affected_security_scope, t.impact_rationale, t.taxonomy_version
        FROM {db}.sec_filing_v3 AS f FINAL
        LEFT JOIN approved_form_taxonomy AS t ON t.form_key = upper(f.form_type)
        {accepted_prewhere}
        WHERE toString(f.cik) = {sql_string(cik)} AND f.accession_number = {sql_string(accession)}
          AND f.accepted_at_utc <= parseDateTime64BestEffort({instant})
        LIMIT 1 FORMAT JSONEachRow
    """


def detail_documents_sql(cik: str, accession: str, cutoff: datetime, database: str) -> str:
    db = quote_ident(database); instant = sql_string(clickhouse_timestamp(cutoff))
    return f"""SELECT document_id,
        argMax(sequence_number, tuple(source_revision_rank, inserted_at)) AS sequence_number,
        argMax(document_name, tuple(source_revision_rank, inserted_at)) AS document_name,
        argMax(document_type, tuple(source_revision_rank, inserted_at)) AS document_type,
        argMax(document_role, tuple(source_revision_rank, inserted_at)) AS document_role,
        argMax(description, tuple(source_revision_rank, inserted_at)) AS description,
        argMax(document_url, tuple(source_revision_rank, inserted_at)) AS document_url,
        argMax(file_extension, tuple(source_revision_rank, inserted_at)) AS file_extension,
        argMax(content_format, tuple(source_revision_rank, inserted_at)) AS content_format,
        argMax(mime_type, tuple(source_revision_rank, inserted_at)) AS mime_type,
        argMax(byte_size, tuple(source_revision_rank, inserted_at)) AS byte_size,
        argMax(payload_char_count, tuple(source_revision_rank, inserted_at)) AS payload_char_count,
        argMax(content_sha256, tuple(source_revision_rank, inserted_at)) AS content_sha256,
        argMax(has_normalized_text, tuple(source_revision_rank, inserted_at)) AS has_normalized_text,
        argMax(extraction_status, tuple(source_revision_rank, inserted_at)) AS extraction_status,
        argMax(extraction_error, tuple(source_revision_rank, inserted_at)) AS extraction_error,
        argMax(normalizer_version, tuple(source_revision_rank, inserted_at)) AS normalizer_version,
        argMax(source_archive_member, tuple(source_revision_rank, inserted_at)) AS source_archive_member,
        argMax(source_revision_kind, tuple(source_revision_rank, inserted_at)) AS source_revision_kind,
        argMax(source_revision_at, tuple(source_revision_rank, inserted_at)) AS latest_source_revision_at
        FROM {db}.sec_filing_document_v3
        WHERE toString(cik) = {sql_string(cik)} AND accession_number = {sql_string(accession)}
          AND source_revision_at <= parseDateTime64BestEffort({instant})
        GROUP BY document_id
        ORDER BY sequence_number, document_name FORMAT JSONEachRow"""


def detail_text_metadata_sql(cik: str, accession: str, cutoff: datetime, database: str) -> str:
    db = quote_ident(database); instant = sql_string(clickhouse_timestamp(cutoff))
    return f"""SELECT document_id,
        argMax(text_kind, tuple(source_revision_rank, inserted_at)) AS text_kind,
        argMax(text_char_count, tuple(source_revision_rank, inserted_at)) AS text_char_count,
        argMax(extraction_method, tuple(source_revision_rank, inserted_at)) AS extraction_method,
        argMax(normalizer_version, tuple(source_revision_rank, inserted_at)) AS normalizer_version,
        argMax(quality_flags, tuple(source_revision_rank, inserted_at)) AS quality_flags,
        argMax(text_sha256, tuple(source_revision_rank, inserted_at)) AS text_sha256,
        argMax(source_archive_member, tuple(source_revision_rank, inserted_at)) AS source_archive_member,
        argMax(source_revision_kind, tuple(source_revision_rank, inserted_at)) AS source_revision_kind,
        argMax(source_revision_at, tuple(source_revision_rank, inserted_at)) AS latest_source_revision_at,
        argMax(extracted_at_utc, tuple(source_revision_rank, inserted_at)) AS extracted_at_utc
        FROM {db}.sec_filing_text_rendered_v3
        WHERE toString(cik) = {sql_string(cik)} AND accession_number = {sql_string(accession)}
          AND source_revision_at <= parseDateTime64BestEffort({instant})
        GROUP BY document_id
        ORDER BY text_kind, document_id FORMAT JSONEachRow"""


def detail_source_text_metadata_sql(cik: str, accession: str, cutoff: datetime, database: str) -> str:
    db = quote_ident(database); instant = sql_string(clickhouse_timestamp(cutoff))
    return f"""SELECT document_id,
        argMax(text_kind, tuple(source_revision_rank, inserted_at)) AS text_kind,
        argMax(source_text_char_count, tuple(source_revision_rank, inserted_at)) AS text_char_count,
        argMax(source_text_byte_count, tuple(source_revision_rank, inserted_at)) AS text_byte_count,
        argMax(file_extension, tuple(source_revision_rank, inserted_at)) AS file_extension,
        argMax(content_format, tuple(source_revision_rank, inserted_at)) AS content_format,
        argMax(mime_type, tuple(source_revision_rank, inserted_at)) AS mime_type,
        argMax(content_sha256, tuple(source_revision_rank, inserted_at)) AS content_sha256,
        argMax(normalizer_version, tuple(source_revision_rank, inserted_at)) AS normalizer_version,
        argMax(source_archive_member, tuple(source_revision_rank, inserted_at)) AS source_archive_member,
        argMax(source_revision_kind, tuple(source_revision_rank, inserted_at)) AS source_revision_kind,
        argMax(source_revision_at, tuple(source_revision_rank, inserted_at)) AS latest_source_revision_at
        FROM {db}.sec_filing_text_v3
        WHERE toString(cik) = {sql_string(cik)} AND accession_number = {sql_string(accession)}
          AND source_revision_at <= parseDateTime64BestEffort({instant})
        GROUP BY document_id
        ORDER BY text_kind, document_id FORMAT JSONEachRow"""


def detail_text_page_sql(cik: str, accession: str, document_id: str, cutoff: datetime, database: str, *, limit: int, offset: int) -> str:
    db = quote_ident(database); instant = sql_string(clickhouse_timestamp(cutoff))
    return f"""SELECT document_id,
        argMax(text_kind, tuple(source_revision_rank, inserted_at)) AS text_kind,
        argMax(text_char_count, tuple(source_revision_rank, inserted_at)) AS text_char_count,
        substringUTF8(argMax(text, tuple(source_revision_rank, inserted_at)), {int(offset) + 1}, {int(limit)}) AS text
        FROM {db}.sec_filing_text_rendered_v3
        WHERE toString(cik) = {sql_string(cik)} AND accession_number = {sql_string(accession)}
          AND document_id = {sql_string(document_id)}
          AND source_revision_at <= parseDateTime64BestEffort({instant})
        GROUP BY document_id
        FORMAT JSONEachRow"""


def detail_source_text_page_sql(cik: str, accession: str, document_id: str, cutoff: datetime, database: str, *, limit: int, offset: int) -> str:
    db = quote_ident(database); instant = sql_string(clickhouse_timestamp(cutoff))
    return f"""SELECT document_id,
        argMax(text_kind, tuple(source_revision_rank, inserted_at)) AS text_kind,
        argMax(source_text_char_count, tuple(source_revision_rank, inserted_at)) AS text_char_count,
        argMax(content_format, tuple(source_revision_rank, inserted_at)) AS content_format,
        argMax(mime_type, tuple(source_revision_rank, inserted_at)) AS mime_type,
        substringUTF8(argMax(source_text, tuple(source_revision_rank, inserted_at)), {int(offset) + 1}, {int(limit)}) AS text
        FROM {db}.sec_filing_text_v3
        WHERE toString(cik) = {sql_string(cik)} AND accession_number = {sql_string(accession)}
          AND document_id = {sql_string(document_id)}
          AND source_revision_at <= parseDateTime64BestEffort({instant})
        GROUP BY document_id
        FORMAT JSONEachRow"""


def detail_facts_sql(cik: str, accession: str, cutoff: datetime, database: str, *, limit: int, offset: int) -> str:
    db = quote_ident(database); instant = sql_string(clickhouse_timestamp(cutoff))
    return f"""SELECT taxonomy, tag, unit_code, value, fiscal_year, fiscal_period,
        period_end_date, form_type, filed_at_utc
        FROM {db}.sec_xbrl_company_fact_v3 FINAL
        WHERE toString(cik) = {sql_string(cik)} AND accession_number = {sql_string(accession)}
          AND filed_at_utc <= parseDateTime64BestEffort({instant})
        ORDER BY tag, period_end_date DESC, unit_code LIMIT {int(limit)} OFFSET {int(offset)} FORMAT JSONEachRow"""


def detail_fact_count_sql(cik: str, accession: str, cutoff: datetime, database: str) -> str:
    db = quote_ident(database); instant = sql_string(clickhouse_timestamp(cutoff))
    return f"""SELECT count() AS row_count
        FROM {db}.sec_xbrl_company_fact_v3 FINAL
        WHERE toString(cik) = {sql_string(cik)} AND accession_number = {sql_string(accession)}
          AND filed_at_utc <= parseDateTime64BestEffort({instant})
        FORMAT JSONEachRow"""


def clickhouse_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds")
