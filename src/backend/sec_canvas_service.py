from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from typing import Any

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    quote_ident,
    sql_string,
)


CIK_PATTERN = re.compile(r"^\d{1,10}$")
ACCESSION_PATTERN = re.compile(r"^[0-9A-Za-z-]{8,32}$")
TICKER_PATTERN = re.compile(r"^[A-Z][A-Z0-9.\-]{0,15}$")
DATABASE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
LABEL_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
DATE_ONLY_ACCEPTANCE_SOURCES = {
    "archive_date_midnight",
    "archive_filing_date_midnight",
    "filing_date_midnight_fallback",
    "xbrl_companyfacts_filed_at",
}
DEFAULT_TEXT_PAGE_CHARS = 32_000
MAX_TEXT_PAGE_CHARS = 100_000
DEFAULT_FACT_PAGE_ROWS = 100
MAX_FACT_PAGE_ROWS = 200
SEC_LABELS = {
    "other_disclosure": "Other disclosure",
}


def sec_filings_payload(
    *,
    as_of: str | None = None,
    before: str = "",
    before_accession: str = "",
    content: str = "all",
    database: str = "q_live",
    label: str = "",
    limit: int = 100,
    lookback_hours: int = 168,
    search: str = "",
    ticker: str = "",
) -> dict[str, Any]:
    cutoff = parse_as_of(as_of)
    safe_database = validate_database(database)
    safe_limit = max(1, min(int(limit), 200))
    safe_hours = max(1, min(int(lookback_hours), 24 * 366))
    safe_label = label.strip().lower()
    if safe_label and not LABEL_PATTERN.fullmatch(safe_label):
        raise ValueError("SEC filing label is invalid.")
    safe_content = content.strip().lower()
    if safe_content not in {"all", "readable", "xbrl"}:
        raise ValueError("SEC content must be all, readable, or xbrl.")
    safe_ticker = normalize_ticker(ticker) if ticker.strip() else ""
    before_time = parse_optional_as_of(before)
    client = clickhouse_client()
    rows = clickhouse_rows(
        client,
        filing_list_sql(
            cutoff=cutoff,
            database=safe_database,
            label=safe_label,
            limit=safe_limit + 1,
            lookback_hours=safe_hours,
            search=search,
            ticker=safe_ticker,
            before=before_time,
            before_accession=before_accession,
            content=safe_content,
        ),
    )
    has_more = len(rows) > safe_limit
    rows = rows[:safe_limit]
    if rows:
        enrich_filing_rows(client, rows, cutoff=cutoff, database=safe_database)
    try:
        labels = clickhouse_rows(client, taxonomy_labels_sql(safe_database))
    except Exception:
        labels = [{"id": key, "label": value} for key, value in SEC_LABELS.items()]
    last = rows[-1] if rows else {}
    return {
        "as_of": cutoff.isoformat(),
        "has_more": has_more,
        "labels": labels,
        "next_before": str(last.get("accepted_at_utc") or ""),
        "next_before_accession": str(last.get("accession_number") or ""),
        "rows": rows,
        "window_start": (cutoff - timedelta(hours=safe_hours)).isoformat(),
    }


def sec_filing_detail_payload(cik: str, accession_number: str, *, as_of: str | None = None, database: str = "q_live") -> dict[str, Any]:
    cutoff = parse_as_of(as_of)
    safe_database = validate_database(database)
    normalized_cik = normalize_cik(cik)
    accession = normalize_accession(accession_number)
    client = clickhouse_client()
    filing_rows = clickhouse_rows(client, filing_detail_sql(normalized_cik, accession, cutoff, safe_database))
    if not filing_rows:
        return {"as_of": cutoff.isoformat(), "status": "not_found", "cik": normalized_cik, "accession_number": accession}
    filing = filing_rows[0]
    queries = {
        "documents": detail_documents_sql(normalized_cik, accession, cutoff, safe_database),
        "entities": filing_entities_sql([(normalized_cik, accession)], cutoff, safe_database),
        "facts": detail_facts_sql(normalized_cik, accession, cutoff, safe_database, limit=DEFAULT_FACT_PAGE_ROWS + 1, offset=0),
        "fact_count": detail_fact_count_sql(normalized_cik, accession, cutoff, safe_database),
        "texts": detail_text_metadata_sql(normalized_cik, accession, cutoff, safe_database),
    }
    results: dict[str, list[dict[str, Any]]] = {}
    errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(clickhouse_rows, client, sql): name for name, sql in queries.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception:
                results[name] = []
                errors[name] = f"{name.title()} are temporarily unavailable."
    normalize_sec_filing_row(filing)
    related_ciks = {normalized_cik} | {
        str(row.get("entity_cik") or "") for row in results.get("entities", []) if row.get("entity_cik")
    }
    try:
        identity_rows = clickhouse_rows(client, identity_sql(sorted(related_ciks), cutoff, safe_database))
    except Exception:
        identity_rows = []
        errors["identity"] = "Identity is temporarily unavailable."
    accepted_date = str(filing.get("accepted_at_utc") or "")[:10]
    filing["tickers"] = sorted({
        str(row.get("ticker") or "")
        for row in identity_rows
        if row.get("ticker") and bridge_valid_on(row, accepted_date)
    })
    facts = results.get("facts", [])
    fact_total = int((results.get("fact_count") or [{}])[0].get("row_count") or len(facts))
    facts = facts[:DEFAULT_FACT_PAGE_ROWS]
    return {
        "accession_number": accession,
        "as_of": cutoff.isoformat(),
        "cik": normalized_cik,
        "documents": results.get("documents", []),
        "entities": results.get("entities", []),
        "errors": errors,
        "facts": facts,
        "facts_has_more": fact_total > len(facts),
        "facts_next_offset": len(facts),
        "facts_total": fact_total,
        "filing": filing,
        "identity": summarize_identity(identity_rows),
        "status": "partial" if errors else "ready",
        "texts": results.get("texts", []),
    }


def sec_document_text_payload(
    cik: str,
    accession_number: str,
    document_id: str,
    *,
    as_of: str | None = None,
    database: str = "q_live",
    limit: int = DEFAULT_TEXT_PAGE_CHARS,
    offset: int = 0,
) -> dict[str, Any]:
    cutoff = parse_as_of(as_of)
    safe_database = validate_database(database)
    normalized_cik = normalize_cik(cik)
    accession = normalize_accession(accession_number)
    safe_document_id = document_id.strip()
    if not safe_document_id or len(safe_document_id) > 256:
        raise ValueError("SEC document identifier is invalid.")
    safe_limit = max(1_000, min(int(limit), MAX_TEXT_PAGE_CHARS))
    safe_offset = max(0, int(offset))
    rows = clickhouse_rows(
        clickhouse_client(),
        detail_text_page_sql(normalized_cik, accession, safe_document_id, cutoff, safe_database, limit=safe_limit, offset=safe_offset),
    )
    if not rows:
        return {"status": "not_found", "cik": normalized_cik, "accession_number": accession, "document_id": safe_document_id}
    row = rows[0]
    total = int(row.get("text_char_count") or 0)
    returned = len(str(row.get("text") or ""))
    next_offset = safe_offset + returned
    return {
        **row,
        "accession_number": accession,
        "as_of": cutoff.isoformat(),
        "cik": normalized_cik,
        "has_more": next_offset < total,
        "limit": safe_limit,
        "next_offset": next_offset,
        "offset": safe_offset,
        "status": "ready",
    }


def sec_filing_facts_payload(
    cik: str,
    accession_number: str,
    *,
    as_of: str | None = None,
    database: str = "q_live",
    limit: int = DEFAULT_FACT_PAGE_ROWS,
    offset: int = 0,
) -> dict[str, Any]:
    cutoff = parse_as_of(as_of)
    safe_database = validate_database(database)
    normalized_cik = normalize_cik(cik)
    accession = normalize_accession(accession_number)
    safe_limit = max(1, min(int(limit), MAX_FACT_PAGE_ROWS))
    safe_offset = max(0, int(offset))
    client = clickhouse_client()
    rows = clickhouse_rows(client, detail_facts_sql(normalized_cik, accession, cutoff, safe_database, limit=safe_limit + 1, offset=safe_offset))
    count_rows = clickhouse_rows(client, detail_fact_count_sql(normalized_cik, accession, cutoff, safe_database))
    total = int((count_rows or [{}])[0].get("row_count") or 0)
    page = rows[:safe_limit]
    next_offset = safe_offset + len(page)
    return {
        "accession_number": accession,
        "as_of": cutoff.isoformat(),
        "cik": normalized_cik,
        "has_more": next_offset < total,
        "limit": safe_limit,
        "next_offset": next_offset,
        "offset": safe_offset,
        "rows": page,
        "row_count": total,
    }


def classify_sec_filing(form_type: str, items: Any = None) -> dict[str, Any]:
    form = form_type.strip().upper()
    key = "other_disclosure"
    item_values = normalize_string_list(items)
    evidence = [f"Form {form or 'unknown'} has no approved exact-form taxonomy match"]
    if item_values:
        evidence.append(f"Items {', '.join(item_values[:4])}")
    return {"filing_label": key, "filing_label_text": SEC_LABELS[key], "label_evidence": evidence, "label_version": "sec_disclosure_taxonomy_v3_fallback"}


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


def filing_list_sql(*, cutoff: datetime, database: str, label: str, limit: int, lookback_hours: int, search: str, ticker: str, before: datetime | None, before_accession: str, content: str = "all") -> str:
    db = quote_ident(database)
    instant = sql_string(clickhouse_timestamp(cutoff))
    start = sql_string(clickhouse_timestamp(cutoff - timedelta(hours=lookback_hours)))
    conditions = [
        f"f.accepted_at_utc >= parseDateTime64BestEffort({start})",
        f"f.accepted_at_utc <= parseDateTime64BestEffort({instant})",
    ]
    if search.strip():
        value = sql_string(search.strip())
        conditions.append(f"(positionCaseInsensitiveUTF8(ifNull(f.company_name, ''), {value}) > 0 OR positionCaseInsensitiveUTF8(f.form_type, {value}) > 0 OR positionCaseInsensitiveUTF8(f.accession_number, {value}) > 0 OR positionCaseInsensitiveUTF8(ifNull(f.items, ''), {value}) > 0)")
    if ticker:
        ticker_sql = sql_string(ticker)
        bridge_validity = "(b.valid_from_date IS NULL OR b.valid_from_date <= toDate(f2.accepted_at_utc)) AND (b.valid_to_date_exclusive IS NULL OR toDate(f2.accepted_at_utc) < b.valid_to_date_exclusive)"
        conditions.append(f"""f.accession_number IN
            (
                SELECT DISTINCT f2.accession_number
                FROM {db}.sec_filing_v3 AS f2 FINAL
                LEFT JOIN
                (
                    SELECT accession_number, entity_cik, entity_role
                    FROM
                    (
                        SELECT accession_number, relationship_id,
                               argMax(entity_cik, tuple(source_revision_rank, inserted_at)) AS entity_cik,
                               argMax(entity_role, tuple(source_revision_rank, inserted_at)) AS entity_role
                        FROM {db}.sec_filing_entity_v3
                        WHERE source_revision_at <= parseDateTime64BestEffort({instant})
                        GROUP BY accession_number, relationship_id
                    )
                    WHERE entity_role IN ('issuer', 'subject_company')
                ) AS e ON e.accession_number = f2.accession_number
                INNER JOIN {db}.id_sec_market_bridge_v3 AS b FINAL
                    ON b.cik = if(empty(e.entity_cik), f2.cik, e.entity_cik)
                WHERE f2.accepted_at_utc >= parseDateTime64BestEffort({start})
                  AND f2.accepted_at_utc <= parseDateTime64BestEffort({instant})
                  AND upper(ifNull(b.ticker, '')) = {ticker_sql} AND {bridge_validity}
            )""")
    if content == "readable":
        conditions.append(f"f.accession_number IN (SELECT accession_number FROM {db}.sec_filing_text_rendered_v3 WHERE source_archive_date BETWEEN toDate({start}) AND toDate({instant}) AND source_revision_at <= parseDateTime64BestEffort({instant}) GROUP BY accession_number HAVING count() > 0)")
    elif content == "xbrl":
        conditions.append(f"f.accession_number IN (SELECT accession_number FROM {db}.sec_xbrl_company_fact_v3 FINAL WHERE filed_at_utc BETWEEN parseDateTime64BestEffort({start}) AND parseDateTime64BestEffort({instant}) GROUP BY accession_number HAVING count() > 0)")
    if before:
        before_sql = sql_string(clickhouse_timestamp(before))
        accession_sql = sql_string(before_accession)
        conditions.append(f"(f.accepted_at_utc < parseDateTime64BestEffort({before_sql}) OR (f.accepted_at_utc = parseDateTime64BestEffort({before_sql}) AND f.accession_number < {accession_sql}))")
    outer = f"WHERE filing_label = {sql_string(label)}" if label else ""
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
            FROM {db}.sec_filing_v3 AS f FINAL
            LEFT JOIN approved_form_taxonomy AS t ON t.form_key = upper(f.form_type)
            WHERE {' AND '.join(conditions)}
        )
        {outer}
        ORDER BY accepted_at_utc DESC, accession_number DESC
        LIMIT {int(limit)}
        FORMAT JSONEachRow
    """


def enrich_filing_rows(client: ClickHouseHttpClient, rows: list[dict[str, Any]], *, cutoff: datetime, database: str) -> None:
    keys = [(str(row.get("cik") or ""), str(row.get("accession_number") or "")) for row in rows]
    queries = {
        "coverage": coverage_sql(keys, cutoff, database),
        "entities": filing_entities_sql(keys, cutoff, database),
    }
    results: dict[str, list[dict[str, Any]]] = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(clickhouse_rows, client, sql): name for name, sql in queries.items()}
        for future in as_completed(futures):
            try:
                results[futures[future]] = future.result()
            except Exception:
                results[futures[future]] = []
    coverage = {(str(row.get("cik") or ""), str(row.get("accession_number") or "")): row for row in results.get("coverage", [])}
    entity_ciks: dict[str, set[str]] = {}
    for entity in results.get("entities", []):
        entity_ciks.setdefault(str(entity.get("accession_number") or ""), set()).add(str(entity.get("entity_cik") or ""))
    all_ciks = sorted({cik for cik, _ in keys if cik} | {cik for values in entity_ciks.values() for cik in values if cik})
    try:
        identities = clickhouse_rows(client, identity_sql(all_ciks, cutoff, database))
    except Exception:
        identities = []
    identity_by_cik: dict[str, list[dict[str, Any]]] = {}
    for identity in identities:
        identity_by_cik.setdefault(str(identity.get("cik") or ""), []).append(identity)
    for row in rows:
        normalize_sec_filing_row(row)
        row.update(coverage.get((str(row.get("cik") or ""), str(row.get("accession_number") or "")), {}))
        accepted_date = str(row.get("accepted_at_utc") or "")[:10]
        related_ciks = {str(row.get("cik") or "")} | entity_ciks.get(str(row.get("accession_number") or ""), set())
        tickers: list[str] = []
        for cik in related_ciks:
            for identity in identity_by_cik.get(cik, []):
                if bridge_valid_on(identity, accepted_date):
                    ticker = str(identity.get("ticker") or "")
                    if ticker and ticker not in tickers:
                        tickers.append(ticker)
        row["tickers"] = tickers[:8]


def normalize_sec_filing_row(row: dict[str, Any]) -> dict[str, Any]:
    """Apply the public SEC filing schema at the service boundary."""
    row["accepted_at_utc"] = normalize_clickhouse_utc(row.get("accepted_at_utc"))
    items = normalize_string_list(row.get("items"))
    row["items"] = items
    if row.get("filing_label"):
        row["filing_label_text"] = humanize_label(str(row["filing_label"]))
        evidence = [f"Approved SEC taxonomy: {row.get('disclosure_title') or row.get('form_type') or 'unknown form'}"]
        if row.get("impact_label"):
            evidence.append(f"Impact {row['impact_score']}/5 · {row['impact_label']}")
        if items:
            evidence.append(f"Items {', '.join(items[:4])}")
        row["label_evidence"] = evidence
        row["label_version"] = row.get("taxonomy_version") or "sec-disclosure-taxonomy-v1"
    else:
        row.update(classify_sec_filing(str(row.get("form_type") or ""), items))
    source = str(row.get("accepted_at_source") or "")
    row["event_time_quality"] = "date_only" if any(token in source for token in DATE_ONLY_ACCEPTANCE_SOURCES) else "exact"
    return row


def bridge_valid_on(row: dict[str, Any], event_date: str) -> bool:
    valid_from = str(row.get("valid_from_date") or "")
    valid_to = str(row.get("valid_to_date_exclusive") or "")
    return (not valid_from or valid_from <= event_date) and (not valid_to or event_date < valid_to)


def humanize_label(value: str) -> str:
    return value.replace("_", " ").strip().capitalize()


def normalize_clickhouse_utc(value: Any) -> str:
    raw = str(value or "").strip()
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})(?:\.(\d+))?(?:Z|[+-]\d{2}:?\d{2})?", raw)
    if not match:
        return raw
    fraction = (match.group(3) or "")[:3].ljust(3, "0")
    return f"{match.group(1)}T{match.group(2)}.{fraction}Z"


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


def filing_detail_sql(cik: str, accession: str, cutoff: datetime, database: str) -> str:
    db = quote_ident(database)
    instant = sql_string(clickhouse_timestamp(cutoff))
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
        argMax(has_normalized_text, tuple(source_revision_rank, inserted_at)) AS has_normalized_text,
        argMax(extraction_status, tuple(source_revision_rank, inserted_at)) AS extraction_status
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
        argMax(quality_flags, tuple(source_revision_rank, inserted_at)) AS quality_flags,
        argMax(extracted_at_utc, tuple(source_revision_rank, inserted_at)) AS extracted_at_utc
        FROM {db}.sec_filing_text_rendered_v3
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


def summarize_identity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    primary = next((row for row in rows if int(row.get("primary_symbol_flag") or 0) == 1 and row.get("ticker")), rows[0] if rows else {})
    return {
        "company_name": primary.get("legal_name") or primary.get("issuer_name"),
        "currency_code": primary.get("currency_code"),
        "exchange_code": primary.get("exchange_code"),
        "ibkr_conid": primary.get("ibkr_conid"),
        "sic_description": primary.get("sic_description"),
        "ticker": primary.get("ticker"),
        "tickers": sorted({str(row.get("ticker") or "") for row in rows if row.get("ticker")}),
    }


def clickhouse_client() -> ClickHouseHttpClient:
    return ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password())


def clickhouse_rows(client: ClickHouseHttpClient, query: str) -> list[dict[str, Any]]:
    payload = client.execute(query)
    return [json.loads(line) for line in payload.splitlines() if line.strip()]


def parse_as_of(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("as_of must be an ISO-8601 timestamp.") from error
    if parsed.tzinfo is None:
        raise ValueError("as_of must include an explicit timezone.")
    return parsed.astimezone(UTC)


def parse_optional_as_of(value: str) -> datetime | None:
    return parse_as_of(value) if value.strip() else None


def normalize_ticker(value: str) -> str:
    ticker = value.strip().upper()
    if not TICKER_PATTERN.fullmatch(ticker):
        raise ValueError("Ticker must contain 1-16 letters, numbers, dots, or hyphens.")
    return ticker


def normalize_cik(value: str) -> str:
    cik = value.strip()
    if not CIK_PATTERN.fullmatch(cik):
        raise ValueError("CIK must contain 1-10 digits.")
    return cik.zfill(10)


def normalize_accession(value: str) -> str:
    accession = value.strip()
    if not ACCESSION_PATTERN.fullmatch(accession):
        raise ValueError("Accession number is invalid.")
    return accession


def validate_database(value: str) -> str:
    if not DATABASE_PATTERN.fullmatch(value):
        raise ValueError("SEC database is not a valid identifier.")
    return value


def clickhouse_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds")


def normalize_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []
