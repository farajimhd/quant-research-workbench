from __future__ import annotations

import json
import re
import uuid
from concurrent.futures import as_completed
from datetime import UTC, datetime, timedelta
from typing import Any

from src.request_context import ContextThreadPoolExecutor as ThreadPoolExecutor

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    sql_string,
)
from src.backend.query_plans.sec_canvas_v1 import (
    filing_document_ids_sql,
    filing_label_sql,
    taxonomy_cte_sql,
    taxonomy_labels_sql,
    filing_list_sql,
    sec_label_accessions_sql,
    coverage_sql,
    filing_entities_sql,
    detail_filing_entities_sql,
    identity_sql,
    filing_detail_sql,
    detail_documents_sql,
    detail_text_metadata_sql,
    detail_source_text_metadata_sql,
    detail_text_page_sql,
    detail_source_text_page_sql,
    detail_facts_sql,
    detail_fact_count_sql,
    clickhouse_timestamp,
)
from src.backend.scoped_text_labels import (
    load_scoped_sec_labels,
    scoped_sec_summary,
)
from src.backend.text_query_contract import TEXT_QUERY_SESSIONS, resolve_text_query_window


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
SEC_QUERY_TIMEOUT_SECONDS = 6.0
SEC_INTELLIGENCE_TIMEOUT_SECONDS = 1.5
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
    start_date: str = "",
    end_date: str = "",
    query_id: str = "",
    role: str = "",
    origin: str = "",
    direction: str = "",
    label_state: str = "",
    impact: str = "",
    security_scope: str = "",
    forecast_eligible: str = "",
    reaction_eligible: str = "",
    history_eligible: str = "",
    prior_context_eligible: str = "",
    followup_eligible: str = "",
) -> dict[str, Any]:
    window = resolve_text_query_window(
        as_of=as_of,
        lookback_hours=lookback_hours,
        start_date=start_date,
        end_date=end_date,
    )
    cutoff = window.end
    window_start = window.start
    safe_database = validate_database(database)
    safe_limit = max(1, min(int(limit), 200))
    safe_hours = max(1, int(((cutoff - window_start).total_seconds() + 3599) // 3600))
    safe_label = label.strip().lower()
    if safe_label and not LABEL_PATTERN.fullmatch(safe_label):
        raise ValueError("SEC filing label is invalid.")
    safe_content = content.strip().lower()
    if safe_content not in {"all", "readable", "xbrl"}:
        raise ValueError("SEC content must be all, readable, or xbrl.")
    safe_ticker = normalize_ticker(ticker) if ticker.strip() else ""
    token_pattern = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
    label_filters = {
        "role": role.strip().lower(),
        "origin": origin.strip().lower(),
        "direction": direction.strip().lower(),
    }
    for name, value in label_filters.items():
        if value and not token_pattern.fullmatch(value):
            raise ValueError(f"SEC {name} filter is invalid.")
    safe_security_scope = " ".join(security_scope.strip().lower().split())
    if safe_security_scope and not re.fullmatch(r"[a-z0-9][a-z0-9 ,/&()\-]{0,127}", safe_security_scope):
        raise ValueError("SEC security_scope filter is invalid.")
    safe_label_state = label_state.strip().lower()
    if safe_label_state not in {"", "classified", "pending", "quality"}:
        raise ValueError("SEC label_state filter is invalid.")
    safe_impact = impact.strip()
    if safe_impact and safe_impact not in {"1", "2", "3", "4", "5"}:
        raise ValueError("SEC impact filter is invalid.")
    eligibility_filters = {
        "forecast_eligible": forecast_eligible.strip().lower(),
        "reaction_eligible": reaction_eligible.strip().lower(),
        "history_eligible": history_eligible.strip().lower(),
        "prior_context_eligible": prior_context_eligible.strip().lower(),
        "followup_eligible": followup_eligible.strip().lower(),
    }
    for name, value in eligibility_filters.items():
        if value not in {"", "eligible", "ineligible"}:
            raise ValueError(f"SEC {name} filter is invalid.")
    before_time = parse_optional_as_of(before)
    query_params = {
        "content": safe_content,
        "end": cutoff.isoformat(),
        "label": safe_label,
        "limit": safe_limit,
        "search": search.strip(),
        "start": window_start.isoformat(),
        "ticker": safe_ticker,
        "impact": safe_impact,
        "label_state": safe_label_state,
        **label_filters,
        "security_scope": safe_security_scope,
        **eligibility_filters,
    }
    if query_id.strip():
        existing_session = TEXT_QUERY_SESSIONS.get(query_id, "sec")
        if existing_session is None:
            raise ValueError("This SEC query expired; run it again.")
        if existing_session.params != query_params:
            raise ValueError("This SEC page does not match its retained query.")
        effective_query_id = query_id
    else:
        effective_query_id = TEXT_QUERY_SESSIONS.create("sec", query_params)
    client = clickhouse_client(timeout_seconds=SEC_QUERY_TIMEOUT_SECONDS)
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
            window_start=window_start,
            impact=safe_impact,
            label_state=safe_label_state,
            role=label_filters["role"],
            origin=label_filters["origin"],
            direction=label_filters["direction"],
            security_scope=safe_security_scope,
            ticker_label=safe_ticker,
            eligibility_filters=eligibility_filters,
        ),
    )
    has_more = len(rows) > safe_limit
    rows = rows[:safe_limit]
    if rows:
        enrich_filing_rows(client, rows, cutoff=cutoff, database=safe_database)
    intelligence_status = enrich_sec_intelligence(
        clickhouse_client(timeout_seconds=SEC_INTELLIGENCE_TIMEOUT_SECONDS),
        rows,
        cutoff=cutoff,
        database=safe_database,
        ticker=safe_ticker,
    )
    try:
        labels = clickhouse_rows(
            clickhouse_client(timeout_seconds=SEC_INTELLIGENCE_TIMEOUT_SECONDS),
            taxonomy_labels_sql(safe_database),
        )
    except Exception:
        labels = [{"id": key, "label": value} for key, value in SEC_LABELS.items()]
    last = rows[-1] if rows else {}
    TEXT_QUERY_SESSIONS.remember(
        effective_query_id,
        "sec",
        {
            f"{row.get('cik') or ''}/{row.get('accession_number') or ''}": {
                "accepted_at_utc": str(row.get("accepted_at_utc") or "")
            }
            for row in rows
            if row.get("cik") and row.get("accession_number")
        },
    )
    return {
        "as_of": cutoff.isoformat(),
        "has_more": has_more,
        "labels": labels,
        "next_before": str(last.get("accepted_at_utc") or ""),
        "next_before_accession": str(last.get("accession_number") or ""),
        "query_id": effective_query_id,
        "rows": rows,
        "intelligence_status": intelligence_status,
        "window_start": window_start.isoformat(),
    }


def sec_filing_detail_payload(cik: str, accession_number: str, *, as_of: str | None = None, database: str = "q_live", accepted_at: str = "", query_id: str = "") -> dict[str, Any]:
    cutoff = parse_as_of(as_of)
    safe_database = validate_database(database)
    normalized_cik = normalize_cik(cik)
    accession = normalize_accession(accession_number)
    hint = TEXT_QUERY_SESSIONS.hint(query_id, "sec", f"{normalized_cik}/{accession}")
    accepted_hint = accepted_at.strip() or hint.get("accepted_at_utc", "")
    accepted_date = ""
    if accepted_hint:
        try:
            accepted_date = datetime.fromisoformat(accepted_hint.replace("Z", "+00:00")).date().isoformat()
        except ValueError as exc:
            raise ValueError("accepted_at must be an ISO-8601 timestamp.") from exc
    client = clickhouse_client()
    filing_rows = clickhouse_rows(client, filing_detail_sql(normalized_cik, accession, cutoff, safe_database, accepted_date=accepted_date))
    if not filing_rows:
        return {"as_of": cutoff.isoformat(), "status": "not_found", "cik": normalized_cik, "accession_number": accession}
    filing = filing_rows[0]
    queries = {
        "documents": detail_documents_sql(normalized_cik, accession, cutoff, safe_database),
        "entities": detail_filing_entities_sql(normalized_cik, accession, cutoff, safe_database),
        "facts": detail_facts_sql(normalized_cik, accession, cutoff, safe_database, limit=DEFAULT_FACT_PAGE_ROWS + 1, offset=0),
        "fact_count": detail_fact_count_sql(normalized_cik, accession, cutoff, safe_database),
        "texts": detail_text_metadata_sql(normalized_cik, accession, cutoff, safe_database),
        "originals": detail_source_text_metadata_sql(normalized_cik, accession, cutoff, safe_database),
    }
    results: dict[str, list[dict[str, Any]]] = {}
    errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
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
    intelligence_status = enrich_sec_intelligence(
        client, [filing], cutoff=cutoff, database=safe_database
    )
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
        "intelligence_status": intelligence_status,
        "originals": results.get("originals", []),
        "status": "partial" if errors else "ready",
        "texts": results.get("texts", []),
    }


def enrich_sec_intelligence(
    client: ClickHouseHttpClient,
    rows: list[dict[str, Any]],
    *,
    cutoff: datetime,
    database: str,
    ticker: str = "",
) -> str:
    """Attach V5 document labels while preserving canonical filing availability."""
    if not rows:
        return "ready"
    keys = [
        (str(row.get("cik") or ""), str(row.get("accession_number") or ""))
        for row in rows
    ]
    try:
        documents = clickhouse_rows(
            client, filing_document_ids_sql(keys, cutoff, database)
        )
        source_ids = [
            str(document.get("document_id") or "") for document in documents
        ]
        labels_by_source = load_scoped_sec_labels(
            source_ids,
            query_rows=lambda sql: clickhouse_rows(client, sql),
            quote=sql_string,
            source_end=cutoff.isoformat(),
            source_start=min(
                (str(row.get("accepted_at_utc") or "") for row in rows),
                default="",
            ),
            ticker=ticker,
        )
    except Exception:
        for row in rows:
            row["scoped_labels"] = []
            row["scoped_summary"] = None
        return "unavailable"
    labels_by_accession: dict[str, list[dict[str, Any]]] = {}
    for document in documents:
        accession = str(document.get("accession_number") or "")
        labels_by_accession.setdefault(accession, []).extend(
            labels_by_source.get(str(document.get("document_id") or ""), [])
        )
    for row in rows:
        labels = labels_by_accession.get(
            str(row.get("accession_number") or ""), []
        )
        row["scoped_labels"] = labels
        row["scoped_summary"] = scoped_sec_summary(labels)
    return "ready"


def sec_document_text_payload(
    cik: str,
    accession_number: str,
    document_id: str,
    *,
    as_of: str | None = None,
    database: str = "q_live",
    limit: int = DEFAULT_TEXT_PAGE_CHARS,
    offset: int = 0,
    view: str = "rendered",
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
    safe_view = view.strip().lower()
    if safe_view not in {"rendered", "original"}:
        raise ValueError("SEC document view must be rendered or original.")
    page_sql = detail_text_page_sql if safe_view == "rendered" else detail_source_text_page_sql
    rows = clickhouse_rows(
        clickhouse_client(),
        page_sql(normalized_cik, accession, safe_document_id, cutoff, safe_database, limit=safe_limit, offset=safe_offset),
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
        "view": safe_view,
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


def clickhouse_client(
    *, timeout_seconds: float | None = None
) -> ClickHouseHttpClient:
    query_params = (
        {"max_execution_time": max(0.1, timeout_seconds - 0.5)}
        if timeout_seconds is not None
        else None
    )
    return ClickHouseHttpClient(
        default_clickhouse_url(),
        default_clickhouse_user(),
        default_clickhouse_password(),
        timeout_seconds=timeout_seconds,
        default_query_params=query_params,
    )


def clickhouse_rows(client: ClickHouseHttpClient, query: str) -> list[dict[str, Any]]:
    payload = client.execute(query, query_id=f"canvas-sec-{uuid.uuid4()}")
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


def normalize_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []
