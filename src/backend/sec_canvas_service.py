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
SEC_LABELS = {
    "results": "Results & reports",
    "material_event": "Material event",
    "insider_ownership": "Insider ownership",
    "holder_ownership": "Large-holder ownership",
    "offering_capital": "Offering & capital",
    "governance_proxy": "Governance & proxy",
    "merger_tender": "M&A & tender",
    "registration": "Registration",
    "compliance_notice": "Compliance notice",
    "fund_disclosure": "Fund disclosure",
    "other": "Other filing",
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
    if safe_label and safe_label not in SEC_LABELS:
        raise ValueError("Unknown SEC filing label.")
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
    last = rows[-1] if rows else {}
    return {
        "as_of": cutoff.isoformat(),
        "has_more": has_more,
        "labels": [{"id": key, "label": value} for key, value in SEC_LABELS.items()],
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
        "facts": detail_facts_sql(normalized_cik, accession, cutoff, safe_database),
        "identity": identity_sql([normalized_cik], cutoff, safe_database),
        "texts": detail_texts_sql(normalized_cik, accession, cutoff, safe_database),
    }
    results: dict[str, list[dict[str, Any]]] = {}
    errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(clickhouse_rows, client, sql): name for name, sql in queries.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception:
                results[name] = []
                errors[name] = f"{name.title()} are temporarily unavailable."
    identity_rows = results.get("identity", [])
    filing.update(classify_sec_filing(str(filing.get("form_type") or ""), filing.get("items")))
    filing["tickers"] = sorted({str(row.get("ticker") or "") for row in identity_rows if row.get("ticker")})
    return {
        "accession_number": accession,
        "as_of": cutoff.isoformat(),
        "cik": normalized_cik,
        "documents": results.get("documents", []),
        "errors": errors,
        "facts": results.get("facts", []),
        "filing": filing,
        "identity": summarize_identity(identity_rows),
        "status": "partial" if errors else "ready",
        "texts": results.get("texts", []),
    }


def classify_sec_filing(form_type: str, items: Any = None) -> dict[str, Any]:
    form = form_type.strip().upper()
    if form in {"10-K", "10-K/A", "10-Q", "10-Q/A", "20-F", "20-F/A", "40-F", "40-F/A"}:
        key = "results"
    elif form in {"8-K", "8-K/A", "6-K", "6-K/A"}:
        key = "material_event"
    elif form in {"3", "3/A", "4", "4/A", "5", "5/A"}:
        key = "insider_ownership"
    elif form.startswith(("SC 13D", "SC 13G")) or form in {"13F-HR", "13F-HR/A", "13F-NT", "13F-NT/A"}:
        key = "holder_ownership"
    elif form.startswith(("424B", "FWP")) or form in {"S-1", "S-1/A", "S-3", "S-3/A", "F-1", "F-1/A", "F-3", "F-3/A", "EFFECT", "RW"}:
        key = "offering_capital"
    elif form.startswith(("SC TO", "SC 14D9")) or form in {"S-4", "S-4/A", "F-4", "F-4/A", "DEFM14A", "13E-3", "13E-3/A"}:
        key = "merger_tender"
    elif "14A" in form or form.startswith("PX14A"):
        key = "governance_proxy"
    elif form in {"10", "10/A", "8-A12B", "8-A12G", "S-8", "S-8 POS", "POS AM", "CERT"}:
        key = "registration"
    elif form.startswith("NT ") or form in {"CORRESP", "UPLOAD", "IRANNOTICE", "NSE", "25", "25-NSE"}:
        key = "compliance_notice"
    elif form.startswith("N-") or form.startswith("NPORT") or form.startswith("NCSR"):
        key = "fund_disclosure"
    else:
        key = "other"
    item_values = normalize_string_list(items)
    evidence = [f"Form {form or 'unknown'}"]
    if item_values:
        evidence.append(f"Items {', '.join(item_values[:4])}")
    return {"filing_label": key, "filing_label_text": SEC_LABELS[key], "label_evidence": evidence, "label_version": "sec_form_rules_v1"}


def filing_label_sql(column: str = "form_type") -> str:
    form = f"upper({column})"
    return f"""multiIf(
        {form} IN ('10-K','10-K/A','10-Q','10-Q/A','20-F','20-F/A','40-F','40-F/A'), 'results',
        {form} IN ('8-K','8-K/A','6-K','6-K/A'), 'material_event',
        {form} IN ('3','3/A','4','4/A','5','5/A'), 'insider_ownership',
        startsWith({form}, 'SC 13D') OR startsWith({form}, 'SC 13G') OR {form} IN ('13F-HR','13F-HR/A','13F-NT','13F-NT/A'), 'holder_ownership',
        startsWith({form}, '424B') OR startsWith({form}, 'FWP') OR {form} IN ('S-1','S-1/A','S-3','S-3/A','F-1','F-1/A','F-3','F-3/A','EFFECT','RW'), 'offering_capital',
        startsWith({form}, 'SC TO') OR startsWith({form}, 'SC 14D9') OR {form} IN ('S-4','S-4/A','F-4','F-4/A','DEFM14A','13E-3','13E-3/A'), 'merger_tender',
        position({form}, '14A') > 0 OR startsWith({form}, 'PX14A'), 'governance_proxy',
        {form} IN ('10','10/A','8-A12B','8-A12G','S-8','S-8 POS','POS AM','CERT'), 'registration',
        startsWith({form}, 'NT ') OR {form} IN ('CORRESP','UPLOAD','IRANNOTICE','NSE','25','25-NSE'), 'compliance_notice',
        startsWith({form}, 'N-') OR startsWith({form}, 'NPORT') OR startsWith({form}, 'NCSR'), 'fund_disclosure',
        'other')"""


def filing_list_sql(*, cutoff: datetime, database: str, label: str, limit: int, lookback_hours: int, search: str, ticker: str, before: datetime | None, before_accession: str, content: str = "all") -> str:
    db = quote_ident(database)
    instant = sql_string(clickhouse_timestamp(cutoff))
    start = sql_string(clickhouse_timestamp(cutoff - timedelta(hours=lookback_hours)))
    conditions = [
        f"accepted_at_utc >= parseDateTime64BestEffort({start})",
        f"accepted_at_utc <= parseDateTime64BestEffort({instant})",
        f"inserted_at <= parseDateTime64BestEffort({instant})",
    ]
    if search.strip():
        value = sql_string(search.strip())
        conditions.append(f"(positionCaseInsensitiveUTF8(company_name, {value}) > 0 OR positionCaseInsensitiveUTF8(form_type, {value}) > 0 OR positionCaseInsensitiveUTF8(accession_number, {value}) > 0 OR positionCaseInsensitiveUTF8(arrayStringConcat(items, ' '), {value}) > 0)")
    if ticker:
        conditions.append(f"cik IN (SELECT cik FROM {db}.id_sec_market_bridge_v3 FINAL WHERE upper(ticker) = {sql_string(ticker)} AND inserted_at <= parseDateTime64BestEffort({instant}))")
    if content == "readable":
        conditions.append(f"(toString(cik), accession_number) IN (SELECT toString(cik), accession_number FROM {db}.sec_filing_text_rendered_v3 FINAL WHERE inserted_at <= parseDateTime64BestEffort({instant}))")
    elif content == "xbrl":
        conditions.append(f"(toString(cik), accession_number) IN (SELECT toString(cik), accession_number FROM {db}.sec_xbrl_company_fact_v3 FINAL WHERE recorded_at_utc <= parseDateTime64BestEffort({instant}))")
    if before:
        before_sql = sql_string(clickhouse_timestamp(before))
        accession_sql = sql_string(before_accession)
        conditions.append(f"(accepted_at_utc < parseDateTime64BestEffort({before_sql}) OR (accepted_at_utc = parseDateTime64BestEffort({before_sql}) AND accession_number < {accession_sql}))")
    outer = f"WHERE filing_label = {sql_string(label)}" if label else ""
    return f"""
        SELECT *
        FROM
        (
            SELECT filing_id, accession_number, accession_number_compact, toString(cik) AS cik, company_name,
                   form_type, filing_date, report_date, accepted_at_utc, primary_document,
                   primary_document_url, filing_detail_url, filing_size, items, text_status,
                   {filing_label_sql()} AS filing_label
            FROM {db}.sec_filing_v3 FINAL
            WHERE {' AND '.join(conditions)}
        )
        {outer}
        ORDER BY accepted_at_utc DESC, accession_number DESC
        LIMIT {int(limit)}
        FORMAT JSONEachRow
    """


def enrich_filing_rows(client: ClickHouseHttpClient, rows: list[dict[str, Any]], *, cutoff: datetime, database: str) -> None:
    keys = [(str(row.get("cik") or ""), str(row.get("accession_number") or "")) for row in rows]
    ciks = sorted({cik for cik, _ in keys if cik})
    queries = {
        "coverage": coverage_sql(keys, cutoff, database),
        "identity": identity_sql(ciks, cutoff, database),
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
    tickers: dict[str, list[str]] = {}
    for identity in results.get("identity", []):
        cik = str(identity.get("cik") or "")
        ticker = str(identity.get("ticker") or "")
        if ticker and ticker not in tickers.setdefault(cik, []):
            tickers[cik].append(ticker)
    for row in rows:
        row.update(classify_sec_filing(str(row.get("form_type") or ""), row.get("items")))
        row.update(coverage.get((str(row.get("cik") or ""), str(row.get("accession_number") or "")), {}))
        row["tickers"] = tickers.get(str(row.get("cik") or ""), [])[:8]


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
            SELECT toString(cik) AS cik, accession_number, count() AS document_rows, 0 AS text_rows, 0 AS text_chars, 0 AS xbrl_rows
            FROM {db}.sec_filing_document_v3 FINAL
            WHERE (toString(cik), accession_number) IN ({key_clause}) AND inserted_at <= parseDateTime64BestEffort({instant})
            GROUP BY cik, accession_number
            UNION ALL
            SELECT toString(cik), accession_number, 0, count(), sum(text_char_count), 0
            FROM {db}.sec_filing_text_rendered_v3 FINAL
            WHERE (toString(cik), accession_number) IN ({key_clause}) AND inserted_at <= parseDateTime64BestEffort({instant})
            GROUP BY cik, accession_number
            UNION ALL
            SELECT toString(cik), accession_number, 0, 0, 0, count()
            FROM {db}.sec_xbrl_company_fact_v3 FINAL
            WHERE (toString(cik), accession_number) IN ({key_clause}) AND recorded_at_utc <= parseDateTime64BestEffort({instant})
            GROUP BY cik, accession_number
        )
        GROUP BY cik, accession_number
        FORMAT JSONEachRow
    """


def identity_sql(ciks: list[str], cutoff: datetime, database: str) -> str:
    if not ciks:
        return "SELECT 1 WHERE 0 FORMAT JSONEachRow"
    db = quote_ident(database)
    values = ", ".join(sql_string(cik) for cik in ciks)
    instant = sql_string(clickhouse_timestamp(cutoff))
    return f"""
        SELECT b.cik, b.ticker, b.mapping_status, b.confidence_score,
               issuer.issuer_name, issuer.legal_name, issuer.sic_description,
               listing.exchange_code, listing.currency_code, listing.ibkr_conid,
               sym.primary_symbol_flag
        FROM {db}.id_sec_market_bridge_v3 AS b FINAL
        LEFT JOIN {db}.id_issuer_v1 AS issuer FINAL ON issuer.issuer_id = b.issuer_id
        LEFT JOIN {db}.id_listing_v1 AS listing FINAL ON listing.listing_id = ifNull(b.listing_id, '')
        LEFT JOIN {db}.id_symbol_v1 AS sym FINAL ON sym.symbol_id = ifNull(b.symbol_id, '')
        WHERE b.cik IN ({values}) AND b.inserted_at <= parseDateTime64BestEffort({instant})
        ORDER BY b.cik, sym.primary_symbol_flag DESC, b.confidence_score DESC, b.ticker
        FORMAT JSONEachRow
    """


def filing_detail_sql(cik: str, accession: str, cutoff: datetime, database: str) -> str:
    db = quote_ident(database)
    instant = sql_string(clickhouse_timestamp(cutoff))
    return f"""
        SELECT filing_id, accession_number, accession_number_compact, toString(cik) AS cik, company_name,
               form_type, filing_date, report_date, accepted_at_utc, acceptance_datetime_raw,
               accepted_at_source, primary_document, primary_document_url, filing_detail_url,
               filing_size, items, text_status
        FROM {db}.sec_filing_v3 FINAL
        WHERE toString(cik) = {sql_string(cik)} AND accession_number = {sql_string(accession)}
          AND accepted_at_utc <= parseDateTime64BestEffort({instant}) AND inserted_at <= parseDateTime64BestEffort({instant})
        ORDER BY inserted_at DESC LIMIT 1 FORMAT JSONEachRow
    """


def detail_documents_sql(cik: str, accession: str, cutoff: datetime, database: str) -> str:
    db = quote_ident(database); instant = sql_string(clickhouse_timestamp(cutoff))
    return f"""SELECT document_id, sequence_number, document_name, document_type, document_role, description,
        document_url, file_extension, content_format, mime_type, byte_size, payload_char_count,
        has_normalized_text, extraction_status
        FROM {db}.sec_filing_document_v3 FINAL
        WHERE toString(cik) = {sql_string(cik)} AND accession_number = {sql_string(accession)}
          AND inserted_at <= parseDateTime64BestEffort({instant})
        ORDER BY sequence_number, document_name FORMAT JSONEachRow"""


def detail_texts_sql(cik: str, accession: str, cutoff: datetime, database: str) -> str:
    db = quote_ident(database); instant = sql_string(clickhouse_timestamp(cutoff))
    return f"""SELECT document_id, text_kind, text, text_char_count, extraction_method, quality_flags, extracted_at_utc
        FROM {db}.sec_filing_text_rendered_v3 FINAL
        WHERE toString(cik) = {sql_string(cik)} AND accession_number = {sql_string(accession)}
          AND inserted_at <= parseDateTime64BestEffort({instant})
        ORDER BY text_kind, document_id FORMAT JSONEachRow"""


def detail_facts_sql(cik: str, accession: str, cutoff: datetime, database: str) -> str:
    db = quote_ident(database); instant = sql_string(clickhouse_timestamp(cutoff))
    return f"""SELECT taxonomy, tag, unit_code, value, fiscal_year, fiscal_period,
        period_start_date, period_end_date, form_type, filed_at_utc
        FROM {db}.sec_xbrl_company_fact_v3 FINAL
        WHERE toString(cik) = {sql_string(cik)} AND accession_number = {sql_string(accession)}
          AND filed_at_utc <= parseDateTime64BestEffort({instant}) AND recorded_at_utc <= parseDateTime64BestEffort({instant})
        ORDER BY tag, period_end_date DESC, unit_code LIMIT 240 FORMAT JSONEachRow"""


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
