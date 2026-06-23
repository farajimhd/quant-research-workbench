from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from research.mlops.clickhouse import ClickHouseHttpClient, default_clickhouse_password, quote_ident, sql_string
from services.reference_gateway.config import ReferenceGatewayConfig
from services.reference_gateway.market_publications import clone_table_schema, table_exists


@dataclass(frozen=True, slots=True)
class IssueResolutionResult:
    scanned: int
    resolved: int
    table: str
    reason: str


def resolve_stale_active_ticker_issues(config: ReferenceGatewayConfig) -> IssueResolutionResult:
    client = ClickHouseHttpClient(config.clickhouse_url, config.clickhouse_user, default_clickhouse_password())
    ensure_issue_table_available(client, config)
    open_issues = query_json_each_row(
        client,
        f"""
        SELECT *
        FROM {table(config.clickhouse_read_database, 'id_mapping_issue_v1')} FINAL
        WHERE source_system = 'reference_gateway'
          AND source_entity_kind = 'massive_active_ticker'
          AND lower(issue_status) NOT IN ('resolved', 'closed', 'ignored')
        """,
    )
    if not open_issues:
        return IssueResolutionResult(0, 0, table(config.clickhouse_write_database, "id_mapping_issue_v1"), "no_open_active_ticker_issues")
    tickers = sorted({str(row.get("source_entity_key") or "").upper() for row in open_issues if str(row.get("source_entity_key") or "").strip()})
    resolved_tickers = load_resolved_tickers(client, config.clickhouse_read_database, tickers)
    rows = [resolved_issue_row(row) for row in open_issues if str(row.get("source_entity_key") or "").upper() in resolved_tickers]
    if not rows:
        return IssueResolutionResult(len(open_issues), 0, table(config.clickhouse_write_database, "id_mapping_issue_v1"), "no_resolved_symbols_found")
    body = "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) for row in rows)
    client.execute(f"INSERT INTO {table(config.clickhouse_write_database, 'id_mapping_issue_v1')} FORMAT JSONEachRow\n{body}")
    return IssueResolutionResult(len(open_issues), len(rows), table(config.clickhouse_write_database, "id_mapping_issue_v1"), "closed_issues_with_resolved_canonical_symbols")


def load_resolved_tickers(client: ClickHouseHttpClient, database: str, tickers: list[str]) -> set[str]:
    if not tickers:
        return set()
    values = ", ".join(sql_string(ticker) for ticker in tickers)
    rows = query_json_each_row(
        client,
        f"""
        SELECT DISTINCT upper(s.ticker) AS ticker
        FROM {table(database, 'id_symbol_v1')} s FINAL
        INNER JOIN {table(database, 'id_listing_v1')} l FINAL ON l.listing_id = s.listing_id
        INNER JOIN {table(database, 'id_security_v1')} sec FINAL ON sec.security_id = l.security_id
        LEFT JOIN {table(database, 'ref_exchange_v1')} ex FINAL ON ex.exchange_code = l.exchange_code
        WHERE upper(s.ticker) IN ({values})
          AND s.status = 'active'
          AND s.primary_symbol_flag = 1
          AND l.listing_status = 'active'
          AND match(ifNull(l.ibkr_conid, ''), '^[1-9][0-9]*$')
          AND upper(l.currency_code) = 'USD'
          AND upper(ifNull(ex.iso_country_code, '')) = 'US'
          AND upper(sec.product_type) IN ('STK', 'STOCK', 'STOCKS')
        """,
    )
    return {str(row.get("ticker") or "").upper() for row in rows}


def resolved_issue_row(row: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    payload = dict(row)
    payload["issue_status"] = "resolved"
    payload["resolved_at_utc"] = now
    payload["inserted_at"] = now
    details = {
        "resolution": "canonical_symbol_now_exists",
        "resolved_by": "reference_gateway",
        "resolved_at_utc": now,
    }
    existing_evidence = str(payload.get("evidence_json") or "")
    try:
        evidence = json.loads(existing_evidence) if existing_evidence else {}
    except json.JSONDecodeError:
        evidence = {"previous_evidence_json": existing_evidence}
    evidence["resolution"] = details
    payload["evidence_json"] = json.dumps(evidence, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)
    return payload


def ensure_issue_table_available(client: ClickHouseHttpClient, config: ReferenceGatewayConfig) -> None:
    if table_exists(client, config.clickhouse_write_database, "id_mapping_issue_v1"):
        return
    if not table_exists(client, config.clickhouse_read_database, "id_mapping_issue_v1"):
        raise RuntimeError(f"Source issue table is missing: {table(config.clickhouse_read_database, 'id_mapping_issue_v1')}")
    clone_table_schema(client, source_database=config.clickhouse_read_database, target_database=config.clickhouse_write_database, table_name="id_mapping_issue_v1")


def query_json_each_row(client: ClickHouseHttpClient, sql: str) -> list[dict[str, Any]]:
    text = client.execute(sql.rstrip(";") + " FORMAT JSONEachRow").strip()
    if not text:
        return []
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def table(database: str, name: str) -> str:
    return f"{quote_ident(database)}.{quote_ident(name)}"
