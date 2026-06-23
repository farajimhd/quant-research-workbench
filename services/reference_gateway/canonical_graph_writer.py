from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from research.mlops.clickhouse import ClickHouseHttpClient, default_clickhouse_password, quote_ident, sql_string
from services.reference_gateway.active_tickers import ActiveTickerPlan, MissingTickerCandidate
from services.reference_gateway.config import ReferenceGatewayConfig
from services.reference_gateway.market_publications import clone_table_schema, table_exists


IDENTITY_TABLES: tuple[str, ...] = (
    "id_issuer_v1",
    "id_issuer_identifier_v1",
    "id_security_v1",
    "id_security_identifier_v1",
    "id_listing_v1",
    "id_symbol_v1",
    "id_source_mapping_v1",
)

MASSIVE_EXCHANGE_ALIASES: dict[str, str] = {
    "XNAS": "NASDAQ",
    "NASDAQ": "NASDAQ",
    "XNGS": "NASDAQ",
    "XNCM": "NASDAQ",
    "XNYQ": "NYSE",
    "XNYS": "NYSE",
    "NYSE": "NYSE",
    "ARCX": "NYSEARCA",
    "NYSEARCA": "NYSEARCA",
    "XASE": "AMEX",
    "AMEX": "AMEX",
    "BATS": "BATS",
    "EDGX": "EDGX",
    "EDGA": "EDGA",
    "IEXG": "IEX",
    "IEX": "IEX",
}


@dataclass(frozen=True, slots=True)
class GraphWriteIssue:
    ticker: str
    issue_type: str
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GraphWriteResult:
    attempted: int
    inserted_rows: int
    accepted_candidates: int
    issue_candidates: int
    table_counts: dict[str, int]
    issues: list[GraphWriteIssue]
    reason: str


@dataclass(frozen=True, slots=True)
class ExistingGraph:
    exchanges: dict[str, dict[str, Any]]
    ticker_type_id_by_provider_code: dict[str, str]
    issuer_by_cik: dict[str, str]
    duplicate_ciks: set[str]
    security_by_figi: dict[str, str]
    duplicate_figis: set[str]
    listing_keys: set[tuple[str, str, str]]
    symbol_tickers: set[str]


def write_canonical_graph_candidates(config: ReferenceGatewayConfig, plan: ActiveTickerPlan) -> GraphWriteResult:
    ready = [candidate for candidate in plan.candidates if candidate.proposed_action == "candidate_ready_for_dry_run_graph_resolution"]
    if not ready:
        return GraphWriteResult(
            attempted=0,
            inserted_rows=0,
            accepted_candidates=0,
            issue_candidates=0,
            table_counts={table: 0 for table in IDENTITY_TABLES},
            issues=[],
            reason="no_ready_candidates",
        )
    client = ClickHouseHttpClient(config.clickhouse_url, config.clickhouse_user, default_clickhouse_password())
    ensure_identity_tables_available(client, config)
    existing = load_existing_graph(client, config.clickhouse_read_database)
    now = datetime.now(UTC)
    run_id = "reference_gateway_graph_writer_" + now.strftime("%Y%m%d_%H%M%S")
    rows_by_table: dict[str, list[dict[str, Any]]] = {table: [] for table in IDENTITY_TABLES}
    issues: list[GraphWriteIssue] = []
    accepted = 0
    for candidate in ready:
        accepted_rows, candidate_issues = build_candidate_rows(candidate, existing, run_id, now)
        if candidate_issues:
            issues.extend(candidate_issues)
            continue
        accepted += 1
        for table_name, rows in accepted_rows.items():
            rows_by_table[table_name].extend(rows)
        update_existing_graph(existing, accepted_rows)
    inserted = 0
    for table_name in IDENTITY_TABLES:
        rows = rows_by_table[table_name]
        if not rows:
            continue
        inserted += insert_json_each_row(client, config.clickhouse_write_database, table_name, rows)
    return GraphWriteResult(
        attempted=len(ready),
        inserted_rows=inserted,
        accepted_candidates=accepted,
        issue_candidates=len(issues),
        table_counts={table: len(rows) for table, rows in rows_by_table.items()},
        issues=issues,
        reason="inserted_canonical_graph_rows" if inserted else "no_rows_inserted",
    )


def build_candidate_rows(
    candidate: MissingTickerCandidate,
    existing: ExistingGraph,
    run_id: str,
    now: datetime,
) -> tuple[dict[str, list[dict[str, Any]]], list[GraphWriteIssue]]:
    ticker = candidate.ticker.upper()
    evidence = compact_candidate_evidence(candidate)
    issues: list[GraphWriteIssue] = []
    if ticker in existing.symbol_tickers:
        issues.append(issue(candidate, "symbol_already_exists", "Ticker already exists in the canonical symbol graph.", evidence))
    cik = normalize_cik(candidate.cik or candidate.overview.get("cik"))
    if not cik:
        issues.append(issue(candidate, "missing_durable_issuer_identifier", "Massive active ticker has no CIK; issuer identity is not durable enough.", evidence))
    elif cik in existing.duplicate_ciks:
        issues.append(issue(candidate, "duplicate_durable_issuer_identifier", "CIK maps to multiple issuers; cannot pick a parent issuer.", evidence))
    primary_exchange = str(candidate.primary_exchange or candidate.overview.get("primary_exchange") or "").strip().upper()
    exchange_code = resolve_exchange_code(primary_exchange, existing.exchanges)
    if not exchange_code:
        issues.append(issue(candidate, "unmapped_massive_exchange", f"Massive exchange {primary_exchange or '<empty>'} does not map to a canonical exchange.", evidence))
    elif str(existing.exchanges.get(exchange_code, {}).get("iso_country_code") or "").upper() != "US":
        issues.append(issue(candidate, "non_us_exchange", f"Resolved exchange {exchange_code} is not a US exchange.", evidence))
    currency = normalize_currency(candidate.currency_symbol or candidate.overview.get("currency_name"))
    if currency != "USD":
        issues.append(issue(candidate, "non_usd_currency", f"Candidate currency is {currency or '<empty>'}, not USD.", evidence))
    share_class_figi = str(candidate.share_class_figi or candidate.overview.get("share_class_figi") or "").strip()
    composite_figi = str(candidate.composite_figi or candidate.overview.get("composite_figi") or "").strip()
    figi = share_class_figi or composite_figi
    if not figi:
        issues.append(issue(candidate, "missing_figi_security_identifier", "Massive candidate has no FIGI/share-class evidence for security identity.", evidence))
    elif figi in existing.duplicate_figis:
        issues.append(issue(candidate, "duplicate_figi_security_identifier", "FIGI maps to multiple securities; cannot pick a security.", evidence))
    ibkr = select_exact_ibkr_contract(candidate)
    if ibkr is None:
        issues.append(issue(candidate, "missing_unique_ibkr_conid", "IBKR did not return exactly one compatible STK/USD contract.", evidence))
    if issues:
        return {table: [] for table in IDENTITY_TABLES}, issues

    assert cik
    assert exchange_code
    assert figi
    assert ibkr is not None

    issuer_id = existing.issuer_by_cik.get(cik) or f"issuer:cik:{cik}"
    security_id = existing.security_by_figi.get(figi) or f"security:figi:{stable_key(figi)}"
    listing_id = f"listing:{stable_key(security_id + ':' + exchange_code + ':USD')}"
    symbol_id = f"symbol:massive:{ticker}"
    ticker_type_id = existing.ticker_type_id_by_provider_code.get(candidate.ticker_type, "")
    inserted_at = dt64(now)
    first_seen = inserted_at
    evidence_json = json.dumps(evidence, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)
    digest = sha256_text(evidence_json)
    rows: dict[str, list[dict[str, Any]]] = {table: [] for table in IDENTITY_TABLES}
    if cik not in existing.issuer_by_cik:
        issuer_name = candidate.name or str(candidate.overview.get("name") or ticker)
        rows["id_issuer_v1"].append(
            {
                "issuer_id": issuer_id,
                "issuer_name": issuer_name,
                "issuer_name_normalized": normalize_name(issuer_name),
                "legal_name": None,
                "branding_name": None,
                "entity_type": None,
                "domicile_country_code": None,
                "state_of_incorporation": None,
                "sic_code": nullable_text(candidate.overview.get("sic_code")),
                "sic_description": nullable_text(candidate.overview.get("sic_description")),
                "sector": None,
                "industry": None,
                "industry_group": None,
                "website_url": None,
                "investor_website_url": None,
                "logo_asset_id": None,
                "status": "active",
                "first_seen_at_utc": first_seen,
                "last_seen_at_utc": first_seen,
                "last_verified_at_utc": first_seen,
                "source_run_id": run_id,
                "source_content_sha256": digest,
                "inserted_at": inserted_at,
            }
        )
        rows["id_issuer_identifier_v1"].append(
            {
                "issuer_identifier_id": f"issuer-id:cik:{cik}",
                "issuer_id": issuer_id,
                "identifier_kind": "cik",
                "identifier_value": cik,
                "identifier_value_normalized": cik,
                "source_system": "massive",
                "confidence_score": 0.9,
                "is_primary": 1,
                "valid_from_date": None,
                "valid_to_date_exclusive": None,
                "first_seen_at_utc": first_seen,
                "last_seen_at_utc": first_seen,
                "evidence_json": evidence_json,
                "source_run_id": run_id,
                "source_content_sha256": digest,
                "inserted_at": inserted_at,
            }
        )
    if figi not in existing.security_by_figi:
        rows["id_security_v1"].append(
            {
                "security_id": security_id,
                "issuer_id": issuer_id,
                "product_type": "STK",
                "asset_class": "equity",
                "instrument_type": "stock",
                "security_type": "common_stock" if candidate.ticker_type in {"CS", ""} else candidate.ticker_type,
                "security_name": candidate.name or str(candidate.overview.get("name") or ticker),
                "has_options": None,
                "status": "active",
                "first_seen_at_utc": first_seen,
                "last_seen_at_utc": first_seen,
                "source_run_id": run_id,
                "source_content_sha256": digest,
                "inserted_at": inserted_at,
            }
        )
        rows["id_security_identifier_v1"].append(
            {
                "security_identifier_id": f"security-id:figi:{stable_key(figi)}",
                "security_id": security_id,
                "identifier_kind": "figi",
                "identifier_value": figi,
                "identifier_value_normalized": figi.upper(),
                "source_system": "massive",
                "is_primary": 1,
                "valid_from_date": None,
                "valid_to_date_exclusive": None,
                "first_seen_at_utc": first_seen,
                "last_seen_at_utc": first_seen,
                "source_run_id": run_id,
                "source_content_sha256": digest,
                "inserted_at": inserted_at,
            }
        )
    if (security_id, exchange_code, "USD") not in existing.listing_keys:
        rows["id_listing_v1"].append(
            {
                "listing_id": listing_id,
                "security_id": security_id,
                "exchange_code": exchange_code,
                "currency_code": "USD",
                "ibkr_conid": str(ibkr["conid"]),
                "board_code": None,
                "segment_name": None,
                "listing_status": "active",
                "is_primary_listing": 1,
                "list_date": normalize_date(candidate.overview.get("list_date")),
                "delisted_date": None,
                "first_seen_at_utc": first_seen,
                "last_seen_at_utc": first_seen,
                "source_run_id": run_id,
                "source_content_sha256": digest,
                "inserted_at": inserted_at,
            }
        )
    rows["id_symbol_v1"].append(
        {
            "symbol_id": symbol_id,
            "listing_id": listing_id,
            "source_system": "massive",
            "ticker": ticker,
            "ticker_normalized": ticker,
            "display_name": ticker,
            "ticker_root": ticker_root(ticker),
            "ticker_suffix": ticker_suffix(ticker),
            "ticker_type_id": ticker_type_id or None,
            "asset_type": "stocks",
            "instrument_type": "stock",
            "security_type": "common_stock" if candidate.ticker_type in {"CS", ""} else candidate.ticker_type,
            "status": "active",
            "primary_symbol_flag": 1,
            "first_seen_at_utc": first_seen,
            "last_seen_at_utc": first_seen,
            "source_run_id": run_id,
            "source_content_sha256": digest,
            "inserted_at": inserted_at,
        }
    )
    rows["id_source_mapping_v1"].extend(
        [
            source_mapping_row("massive", "ticker", ticker, "market_symbol", symbol_id, 0.95, evidence_json, digest, run_id, inserted_at),
            source_mapping_row("ibkr", "conid", str(ibkr["conid"]), "market_listing", listing_id, 0.9, evidence_json, digest, run_id, inserted_at),
        ]
    )
    return rows, []


def load_existing_graph(client: ClickHouseHttpClient, database: str) -> ExistingGraph:
    exchanges = {
        str(row["exchange_code"]).upper(): row
        for row in query_json_each_row(
            client,
            f"""
            SELECT exchange_code, acronym, mic, operating_mic, iso_country_code, status
            FROM {table(database, 'ref_exchange_v1')} FINAL
            """,
        )
    }
    ticker_types = {
        str(row["provider_code"]).upper(): str(row["ticker_type_id"])
        for row in query_json_each_row(
            client,
            f"""
            SELECT ticker_type_id, provider_code
            FROM {table(database, 'ref_ticker_type_v1')} FINAL
            WHERE provider_code != ''
            """,
        )
    }
    issuer_rows = query_json_each_row(
        client,
        f"""
        SELECT identifier_value_normalized AS cik, groupArray(issuer_id) AS issuer_ids, uniqExact(issuer_id) AS issuer_count
        FROM {table(database, 'id_issuer_identifier_v1')} FINAL
        WHERE lower(identifier_kind) = 'cik' AND identifier_value_normalized != ''
        GROUP BY identifier_value_normalized
        """,
    )
    issuer_by_cik = {str(row["cik"]): str(row["issuer_ids"][0]) for row in issuer_rows if int(row.get("issuer_count") or 0) == 1}
    duplicate_ciks = {str(row["cik"]) for row in issuer_rows if int(row.get("issuer_count") or 0) > 1}
    security_rows = query_json_each_row(
        client,
        f"""
        SELECT identifier_value_normalized AS figi, groupArray(security_id) AS security_ids, uniqExact(security_id) AS security_count
        FROM {table(database, 'id_security_identifier_v1')} FINAL
        WHERE lower(identifier_kind) IN ('figi', 'composite_figi', 'share_class_figi') AND identifier_value_normalized != ''
        GROUP BY identifier_value_normalized
        """,
    )
    security_by_figi = {str(row["figi"]): str(row["security_ids"][0]) for row in security_rows if int(row.get("security_count") or 0) == 1}
    duplicate_figis = {str(row["figi"]) for row in security_rows if int(row.get("security_count") or 0) > 1}
    listing_keys = {
        (str(row["security_id"]), str(row["exchange_code"]).upper(), str(row["currency_code"]).upper())
        for row in query_json_each_row(
            client,
            f"""
            SELECT security_id, exchange_code, currency_code
            FROM {table(database, 'id_listing_v1')} FINAL
            WHERE listing_status = 'active'
            """,
        )
    }
    symbol_tickers = {
        str(row["ticker"]).upper()
        for row in query_json_each_row(
            client,
            f"""
            SELECT ticker
            FROM {table(database, 'id_symbol_v1')} FINAL
            WHERE status = 'active' AND primary_symbol_flag = 1
            """,
        )
    }
    return ExistingGraph(exchanges, ticker_types, issuer_by_cik, duplicate_ciks, security_by_figi, duplicate_figis, listing_keys, symbol_tickers)


def update_existing_graph(existing: ExistingGraph, rows_by_table: dict[str, list[dict[str, Any]]]) -> None:
    for row in rows_by_table.get("id_issuer_identifier_v1", []):
        if str(row.get("identifier_kind")).lower() == "cik":
            existing.issuer_by_cik[str(row["identifier_value_normalized"])] = str(row["issuer_id"])
    for row in rows_by_table.get("id_security_identifier_v1", []):
        if str(row.get("identifier_kind")).lower() == "figi":
            existing.security_by_figi[str(row["identifier_value_normalized"])] = str(row["security_id"])
    for row in rows_by_table.get("id_listing_v1", []):
        existing.listing_keys.add((str(row["security_id"]), str(row["exchange_code"]).upper(), str(row["currency_code"]).upper()))
    for row in rows_by_table.get("id_symbol_v1", []):
        existing.symbol_tickers.add(str(row["ticker"]).upper())


def ensure_identity_tables_available(client: ClickHouseHttpClient, config: ReferenceGatewayConfig) -> None:
    client.execute(f"CREATE DATABASE IF NOT EXISTS {quote_ident(config.clickhouse_write_database)}")
    for table_name in IDENTITY_TABLES:
        if table_exists(client, config.clickhouse_write_database, table_name):
            continue
        if not table_exists(client, config.clickhouse_read_database, table_name):
            raise RuntimeError(f"Source identity table is missing: {table(config.clickhouse_read_database, table_name)}")
        clone_table_schema(client, source_database=config.clickhouse_read_database, target_database=config.clickhouse_write_database, table_name=table_name)


def insert_json_each_row(client: ClickHouseHttpClient, database: str, table_name: str, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    body = "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) for row in rows)
    client.execute(f"INSERT INTO {table(database, table_name)} FORMAT JSONEachRow\n{body}")
    return len(rows)


def source_mapping_row(
    source_system: str,
    source_entity_kind: str,
    source_entity_key: str,
    mapped_entity_kind: str,
    mapped_entity_id: str,
    confidence: float,
    evidence_json: str,
    digest: str,
    run_id: str,
    inserted_at: str,
) -> dict[str, Any]:
    mapping_key = f"{source_system}:{source_entity_kind}:{source_entity_key}:{mapped_entity_kind}:{mapped_entity_id}"
    return {
        "source_mapping_id": "source-map:" + stable_key(mapping_key),
        "source_system": source_system,
        "source_entity_kind": source_entity_kind,
        "source_entity_key": source_entity_key,
        "source_identifier": source_entity_key,
        "mapped_entity_kind": mapped_entity_kind,
        "mapped_entity_id": mapped_entity_id,
        "mapping_status": "active",
        "confidence_score": confidence,
        "evidence_json": evidence_json,
        "resolved_at_utc": inserted_at,
        "source_run_id": run_id,
        "source_content_sha256": digest,
        "inserted_at": inserted_at,
    }


def issue(candidate: MissingTickerCandidate, issue_type: str, message: str, evidence: dict[str, Any]) -> GraphWriteIssue:
    return GraphWriteIssue(candidate.ticker.upper(), issue_type, message, evidence)


def compact_candidate_evidence(candidate: MissingTickerCandidate) -> dict[str, Any]:
    return {
        "ticker": candidate.ticker,
        "name": candidate.name,
        "market": candidate.market,
        "locale": candidate.locale,
        "primary_exchange": candidate.primary_exchange,
        "currency_symbol": candidate.currency_symbol,
        "cik": candidate.cik,
        "composite_figi": candidate.composite_figi,
        "share_class_figi": candidate.share_class_figi,
        "ticker_type": candidate.ticker_type,
        "overview": candidate.overview,
        "ibkr_candidates": candidate.ibkr_candidates,
    }


def select_exact_ibkr_contract(candidate: MissingTickerCandidate) -> dict[str, Any] | None:
    exact = []
    for row in candidate.ibkr_candidates:
        if not row.get("exact_symbol"):
            continue
        if not str(row.get("conid") or "").isdigit():
            continue
        sec_type = str(row.get("sec_type") or "").upper()
        currency = str(row.get("currency") or "").upper()
        if sec_type not in {"STK", "STOCK"} or currency != "USD":
            continue
        exact.append(row)
    return exact[0] if len(exact) == 1 else None


def resolve_exchange_code(source_code: str, exchanges: dict[str, dict[str, Any]]) -> str:
    code = source_code.strip().upper()
    candidates = [code, MASSIVE_EXCHANGE_ALIASES.get(code, "")]
    for exchange_code, row in exchanges.items():
        row_values = {exchange_code, str(row.get("acronym") or "").upper(), str(row.get("mic") or "").upper(), str(row.get("operating_mic") or "").upper()}
        if any(candidate and candidate in row_values for candidate in candidates):
            return exchange_code
    return ""


def normalize_cik(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits.zfill(10) if digits else ""


def normalize_currency(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {"USD", "$", "US DOLLAR", "UNITED STATES DOLLAR", "UNITED STATES DOLLARS"}:
        return "USD"
    return text


def normalize_name(value: str) -> str:
    return " ".join(value.upper().replace(".", " ").replace(",", " ").split())


def normalize_date(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return text[:10]


def nullable_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def ticker_root(ticker: str) -> str | None:
    return ticker.replace("-", ".").split(".", 1)[0] if ticker else None


def ticker_suffix(ticker: str) -> str | None:
    normalized = ticker.replace("-", ".")
    return normalized.split(".", 1)[1] if "." in normalized else None


def dt64(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def stable_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def query_json_each_row(client: ClickHouseHttpClient, sql: str) -> list[dict[str, Any]]:
    text = client.execute(sql.rstrip(";") + " FORMAT JSONEachRow").strip()
    if not text:
        return []
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def table(database: str, name: str) -> str:
    return f"{quote_ident(database)}.{quote_ident(name)}"
