from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from research.mlops.clickhouse import ClickHouseHttpClient, quote_ident, sql_string
from services.reference_gateway.config import ReferenceGatewayConfig
from services.reference_gateway.market_publications import market_publication_audit
from services.reference_gateway.table_groups import OWNED_REFERENCE_TABLES, REFERENCE_TABLE_GROUPS


@dataclass(frozen=True, slots=True)
class AuditCheck:
    name: str
    severity: str
    status: str
    count: int
    message: str
    sample_rows: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ReferenceAuditReport:
    status: str
    checked_at_utc: str
    database: str
    wall_seconds: float
    checks: list[AuditCheck]

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_reference_audit(config: ReferenceGatewayConfig) -> ReferenceAuditReport:
    started = time.perf_counter()
    client = ClickHouseHttpClient(config.clickhouse_url, config.clickhouse_user, _clickhouse_password())
    database = config.clickhouse_database
    checks = [
        check_required_tables(client, database),
        check_table_group_counts(client, database),
        check_issuer_identifier_coverage(client, database),
        check_duplicate_issuer_identifiers(client, database),
        check_security_parent_integrity(client, database),
        check_active_candidates_without_durable_issuer_id(client, database),
        check_missing_or_invalid_conid(client, database),
        check_open_mapping_issues(client, database),
        check_unsupported_us_stock_shape(client, database),
        check_active_symbols_without_tradable_universe(client, database),
        check_tradable_universe_blocked_rows(client, database),
        check_market_publication_recency(client, database),
    ]
    status = "ok" if all(check.status == "ok" for check in checks if check.severity == "error") else "failed"
    if any(check.status != "ok" for check in checks if check.severity == "warning") and status == "ok":
        status = "warning"
    return ReferenceAuditReport(
        status=status,
        checked_at_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        database=database,
        wall_seconds=time.perf_counter() - started,
        checks=checks,
    )


def write_report(report: ReferenceAuditReport, root: Path) -> Path:
    run_id = datetime.now(UTC).strftime("reference_audit_%Y%m%d_%H%M%S")
    run_root = root / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    path = run_root / "reference_gateway_audit.json"
    path.write_text(json.dumps(report.public_dict(), indent=2, sort_keys=True), encoding="utf-8")
    return path


def check_required_tables(client: ClickHouseHttpClient, database: str) -> AuditCheck:
    required = list(OWNED_REFERENCE_TABLES)
    rows = query_json_each_row(
        client,
        "SELECT name FROM system.tables "
        f"WHERE database = {sql_string(database)} AND name IN ({','.join(sql_string(item) for item in required)})",
    )
    found = {str(row["name"]) for row in rows}
    missing = sorted(set(required) - found)
    return AuditCheck(
        name="required_tables",
        severity="error",
        status="ok" if not missing else "failed",
        count=len(missing),
        message="All required reference tables exist." if not missing else "Missing required tables: " + ", ".join(missing),
        sample_rows=[{"missing_table": item} for item in missing],
    )


def check_table_group_counts(client: ClickHouseHttpClient, database: str) -> AuditCheck:
    rows: list[dict[str, Any]] = []
    existing = existing_tables(client, database)
    for group in REFERENCE_TABLE_GROUPS:
        for table_name in group.tables:
            if table_name not in existing:
                rows.append({"group_id": group.group_id, "table": table_name, "rows": 0, "status": "missing"})
                continue
            rows.append(
                {
                    "group_id": group.group_id,
                    "table": table_name,
                    "rows": scalar_int(client, f"SELECT count() FROM {table(database, table_name)}"),
                    "status": "ok",
                }
            )
    zero = [row for row in rows if row.get("status") == "missing" or int(row.get("rows") or 0) == 0]
    return AuditCheck(
        name="reference_table_group_counts",
        severity="error",
        status="ok" if not zero else "failed",
        count=len(zero),
        message="All owned reference table groups have rows." if not zero else "Some owned reference tables are empty.",
        sample_rows=rows,
    )


def check_issuer_identifier_coverage(client: ClickHouseHttpClient, database: str) -> AuditCheck:
    count = scalar_int(
        client,
        f"""
        SELECT count()
        FROM {table(database, 'id_issuer_v1')} issuer FINAL
        WHERE issuer.status = 'active'
          AND issuer.issuer_id NOT IN (
              SELECT issuer_id
              FROM {table(database, 'id_issuer_identifier_v1')} FINAL
              WHERE lower(identifier_kind) IN ('cik', 'lei', 'ein')
                AND identifier_value_normalized != ''
          )
        """,
    )
    rows = query_json_each_row(
        client,
        f"""
        SELECT issuer_id, issuer_name, status
        FROM {table(database, 'id_issuer_v1')} issuer FINAL
        WHERE issuer.status = 'active'
          AND issuer.issuer_id NOT IN (
              SELECT issuer_id
              FROM {table(database, 'id_issuer_identifier_v1')} FINAL
              WHERE lower(identifier_kind) IN ('cik', 'lei', 'ein')
                AND identifier_value_normalized != ''
          )
        ORDER BY issuer_name
        LIMIT 25
        """,
    )
    return AuditCheck(
        name="active_issuers_missing_durable_identifier",
        severity="warning",
        status="ok" if count == 0 else "failed",
        count=count,
        message=(
            "Every active issuer has at least one durable identifier."
            if count == 0
            else "Active issuers without CIK/LEI/EIN must be treated as weak identity rows."
        ),
        sample_rows=rows,
    )


def check_duplicate_issuer_identifiers(client: ClickHouseHttpClient, database: str) -> AuditCheck:
    rows = query_json_each_row(
        client,
        f"""
        SELECT identifier_kind, identifier_value_normalized, uniqExact(issuer_id) AS issuer_count, groupArray(issuer_id) AS issuer_ids
        FROM {table(database, 'id_issuer_identifier_v1')} FINAL
        WHERE lower(identifier_kind) IN ('cik', 'lei', 'ein')
          AND identifier_value_normalized != ''
        GROUP BY identifier_kind, identifier_value_normalized
        HAVING issuer_count > 1
        ORDER BY issuer_count DESC, identifier_kind, identifier_value_normalized
        LIMIT 25
        """,
    )
    count = scalar_int(
        client,
        f"""
        SELECT count()
        FROM
        (
            SELECT identifier_kind, identifier_value_normalized, uniqExact(issuer_id) AS issuer_count
            FROM {table(database, 'id_issuer_identifier_v1')} FINAL
            WHERE lower(identifier_kind) IN ('cik', 'lei', 'ein')
              AND identifier_value_normalized != ''
            GROUP BY identifier_kind, identifier_value_normalized
            HAVING issuer_count > 1
        )
        """,
    )
    return AuditCheck(
        name="duplicate_durable_issuer_identifiers",
        severity="error",
        status="ok" if count == 0 else "failed",
        count=count,
        message=(
            "Durable issuer identifiers map to one issuer each."
            if count == 0
            else "A durable issuer identifier maps to multiple issuers; affected securities must not be tradable."
        ),
        sample_rows=rows,
    )


def check_security_parent_integrity(client: ClickHouseHttpClient, database: str) -> AuditCheck:
    rows = query_json_each_row(
        client,
        f"""
        SELECT sec.security_id, sec.issuer_id, sec.security_name, sec.status
        FROM {table(database, 'id_security_v1')} sec FINAL
        LEFT JOIN {table(database, 'id_issuer_v1')} issuer FINAL ON issuer.issuer_id = sec.issuer_id
        WHERE issuer.issuer_id = ''
        ORDER BY sec.security_id
        LIMIT 25
        """,
    )
    count = scalar_int(
        client,
        f"""
        SELECT count()
        FROM {table(database, 'id_security_v1')} sec FINAL
        LEFT JOIN {table(database, 'id_issuer_v1')} issuer FINAL ON issuer.issuer_id = sec.issuer_id
        WHERE issuer.issuer_id = ''
        """,
    )
    return AuditCheck(
        name="securities_missing_issuer_parent",
        severity="error",
        status="ok" if count == 0 else "failed",
        count=count,
        message=(
            "Every security points to an issuer parent."
            if count == 0
            else "Some securities point to a missing issuer parent."
        ),
        sample_rows=rows,
    )


def check_active_candidates_without_durable_issuer_id(client: ClickHouseHttpClient, database: str) -> AuditCheck:
    count = scalar_int(
        client,
        f"""
        SELECT count()
        FROM ({active_stock_base_query(database)}) candidate
        WHERE issuer_id NOT IN (
            SELECT issuer_id
            FROM {table(database, 'id_issuer_identifier_v1')} FINAL
            WHERE lower(identifier_kind) IN ('cik', 'lei', 'ein')
              AND identifier_value_normalized != ''
        )
        """,
    )
    rows = query_json_each_row(
        client,
        f"""
        SELECT ticker, issuer_id, security_id, listing_id, exchange_code, ibkr_conid
        FROM ({active_stock_base_query(database)}) candidate
        WHERE issuer_id NOT IN (
            SELECT issuer_id
            FROM {table(database, 'id_issuer_identifier_v1')} FINAL
            WHERE lower(identifier_kind) IN ('cik', 'lei', 'ein')
              AND identifier_value_normalized != ''
        )
        ORDER BY ticker
        LIMIT 25
        """,
    )
    return AuditCheck(
        name="active_candidates_without_durable_issuer_id",
        severity="warning",
        status="ok" if count == 0 else "failed",
        count=count,
        message=(
            "Every active tradable candidate has a durable issuer identifier."
            if count == 0
            else "Active candidates with weak issuer identity must remain non-tradable until resolved."
        ),
        sample_rows=rows,
    )


def check_missing_or_invalid_conid(client: ClickHouseHttpClient, database: str) -> AuditCheck:
    invalid_filter = "NOT match(ifNull(ibkr_conid, ''), '^[1-9][0-9]*$')"
    rows = query_json_each_row(
        client,
        f"SELECT * FROM ({active_stock_base_query(database)}) WHERE {invalid_filter} ORDER BY ticker LIMIT 25",
    )
    count = scalar_int(client, f"SELECT count() FROM ({active_stock_base_query(database)}) WHERE {invalid_filter}")
    return AuditCheck(
        name="missing_or_invalid_ibkr_conid",
        severity="warning",
        status="ok" if count == 0 else "failed",
        count=count,
        message=(
            "Every active US stock candidate has a valid IBKR conid."
            if count == 0
            else "These securities must remain non-tradable until a unique IBKR conid is resolved."
        ),
        sample_rows=rows,
    )


def check_open_mapping_issues(client: ClickHouseHttpClient, database: str) -> AuditCheck:
    count = scalar_int(
        client,
        f"""
        SELECT count()
        FROM {table(database, 'id_mapping_issue_v1')} FINAL
        WHERE lower(issue_status) NOT IN ('resolved', 'closed', 'ignored')
        """,
    )
    rows = query_json_each_row(
        client,
        f"""
        SELECT source_system, source_entity_kind, source_entity_key, mapped_entity_kind, issue_type, issue_status, issue_message
        FROM {table(database, 'id_mapping_issue_v1')} FINAL
        WHERE lower(issue_status) NOT IN ('resolved', 'closed', 'ignored')
        ORDER BY opened_at_utc DESC
        LIMIT 25
        """,
    )
    return AuditCheck(
        name="open_mapping_issues",
        severity="warning",
        status="ok" if count == 0 else "failed",
        count=count,
        message=(
            "No open mapping issues exist."
            if count == 0
            else "Any security/listing/symbol touched by an open mapping issue must be non-tradable."
        ),
        sample_rows=rows,
    )


def check_unsupported_us_stock_shape(client: ClickHouseHttpClient, database: str) -> AuditCheck:
    count = scalar_int(
        client,
        f"""
        SELECT count()
        FROM {table(database, 'id_symbol_v1')} s FINAL
        INNER JOIN {table(database, 'id_listing_v1')} l FINAL ON l.listing_id = s.listing_id
        INNER JOIN {table(database, 'id_security_v1')} sec FINAL ON sec.security_id = l.security_id
        LEFT JOIN {table(database, 'ref_exchange_v1')} ex FINAL ON ex.exchange_code = l.exchange_code
        WHERE s.status = 'active'
          AND s.primary_symbol_flag = 1
          AND l.listing_status = 'active'
          AND (
              upper(sec.product_type) NOT IN ('STK', 'STOCK', 'STOCKS')
              OR upper(l.currency_code) != 'USD'
              OR upper(ifNull(ex.iso_country_code, '')) != 'US'
          )
        """,
    )
    return AuditCheck(
        name="unsupported_us_stock_shape",
        severity="warning",
        status="ok" if count == 0 else "failed",
        count=count,
        message=(
            "No active primary symbols violate the US stock/USD/exchange shape."
            if count == 0
            else "Rows outside the supported US-stock shape are excluded from tradable universe."
        ),
    )


def check_active_symbols_without_tradable_universe(client: ClickHouseHttpClient, database: str) -> AuditCheck:
    count = scalar_int(
        client,
        f"""
        SELECT count()
        FROM ({active_stock_base_query(database)}) candidate
        WHERE symbol_id NOT IN (
            SELECT symbol_id
            FROM {table(database, 'feature_tradable_universe_v1')} FINAL
            WHERE universe_date = (SELECT max(universe_date) FROM {table(database, 'feature_tradable_universe_v1')})
        )
        """,
    )
    return AuditCheck(
        name="active_candidates_missing_latest_universe",
        severity="warning",
        status="ok" if count == 0 else "failed",
        count=count,
        message=(
            "Latest tradable-universe snapshot covers every active US stock candidate."
            if count == 0
            else "Some active candidates are missing from the latest tradable-universe snapshot."
        ),
    )


def check_tradable_universe_blocked_rows(client: ClickHouseHttpClient, database: str) -> AuditCheck:
    rows = query_json_each_row(
        client,
        f"""
        SELECT exclusion_reason, count() AS rows
        FROM {table(database, 'feature_tradable_universe_v1')} FINAL
        WHERE universe_date = (SELECT max(universe_date) FROM {table(database, 'feature_tradable_universe_v1')})
          AND is_tradable = 0
        GROUP BY exclusion_reason
        ORDER BY rows DESC
        LIMIT 25
        """,
    )
    count = sum(int(row.get("rows") or 0) for row in rows)
    return AuditCheck(
        name="latest_universe_non_tradable_rows",
        severity="info",
        status="ok",
        count=count,
        message="Latest tradable-universe rows blocked by exclusion reason.",
        sample_rows=rows,
    )


def check_market_publication_recency(client: ClickHouseHttpClient, database: str) -> AuditCheck:
    rows = market_publication_audit(client, database=database)
    missing = [row for row in rows if row.get("status") == "missing"]
    empty = [row for row in rows if row.get("status") == "ok" and int(row.get("rows") or 0) == 0]
    status = "ok" if not missing else "failed"
    message = "Market reference publication tables are present."
    if missing:
        message = "Market reference publication schema is incomplete; run the schema ensure step before backfill."
    elif empty:
        message = "Market reference publication schema exists; some sources have no rows yet and need historical fill."
    return AuditCheck(
        name="market_reference_publication_tables",
        severity="warning",
        status=status,
        count=len(missing) + len(empty),
        message=message,
        sample_rows=rows[:50],
    )


def active_stock_base_query(database: str) -> str:
    return f"""
    SELECT
        s.symbol_id AS symbol_id,
        s.ticker AS ticker,
        s.status AS symbol_status,
        l.listing_id AS listing_id,
        l.listing_status AS listing_status,
        l.exchange_code AS exchange_code,
        l.currency_code AS currency_code,
        l.ibkr_conid AS ibkr_conid,
        sec.security_id AS security_id,
        sec.issuer_id AS issuer_id,
        sec.product_type AS product_type,
        sec.security_name AS security_name,
        ex.iso_country_code AS exchange_country
    FROM {table(database, 'id_symbol_v1')} s FINAL
    INNER JOIN {table(database, 'id_listing_v1')} l FINAL ON l.listing_id = s.listing_id
    INNER JOIN {table(database, 'id_security_v1')} sec FINAL ON sec.security_id = l.security_id
    LEFT JOIN {table(database, 'ref_exchange_v1')} ex FINAL ON ex.exchange_code = l.exchange_code
    WHERE s.status = 'active'
      AND s.primary_symbol_flag = 1
      AND l.listing_status = 'active'
      AND upper(sec.product_type) IN ('STK', 'STOCK', 'STOCKS')
      AND upper(l.currency_code) = 'USD'
      AND upper(ifNull(ex.iso_country_code, '')) = 'US'
    """


def scalar_int(client: ClickHouseHttpClient, sql: str) -> int:
    value = client.query_tsv(sql).strip()
    return int(value or "0")


def existing_tables(client: ClickHouseHttpClient, database: str) -> set[str]:
    rows = query_json_each_row(client, f"SELECT name FROM system.tables WHERE database = {sql_string(database)}")
    return {str(row.get("name") or "") for row in rows}


def query_json_each_row(client: ClickHouseHttpClient, sql: str) -> list[dict[str, Any]]:
    text = client.execute(sql.rstrip(";") + " FORMAT JSONEachRow").strip()
    if not text:
        return []
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def table(database: str, name: str) -> str:
    return f"{quote_ident(database)}.{quote_ident(name)}"


def _clickhouse_password() -> str:
    from research.mlops.clickhouse import default_clickhouse_password

    return default_clickhouse_password()
