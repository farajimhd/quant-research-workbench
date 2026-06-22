from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from research.mlops.clickhouse import ClickHouseHttpClient, quote_ident, sql_string
from services.reference_gateway.config import ReferenceGatewayConfig


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
        check_identity_graph_counts(client, database),
        check_missing_or_invalid_conid(client, database),
        check_open_mapping_issues(client, database),
        check_unsupported_us_stock_shape(client, database),
        check_active_symbols_without_tradable_universe(client, database),
        check_tradable_universe_blocked_rows(client, database),
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
    required = [
        "ref_exchange_v1",
        "id_issuer_v1",
        "id_security_v1",
        "id_listing_v1",
        "id_symbol_v1",
        "id_source_mapping_v1",
        "id_mapping_issue_v1",
        "feature_tradable_universe_v1",
    ]
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


def check_identity_graph_counts(client: ClickHouseHttpClient, database: str) -> AuditCheck:
    rows = query_json_each_row(
        client,
        f"""
        SELECT 'issuers' AS entity, count() AS rows FROM {table(database, 'id_issuer_v1')}
        UNION ALL SELECT 'securities', count() FROM {table(database, 'id_security_v1')}
        UNION ALL SELECT 'listings', count() FROM {table(database, 'id_listing_v1')}
        UNION ALL SELECT 'symbols', count() FROM {table(database, 'id_symbol_v1')}
        UNION ALL SELECT 'tradable_universe', count() FROM {table(database, 'feature_tradable_universe_v1')}
        """,
    )
    zero = [row for row in rows if int(row.get("rows") or 0) == 0]
    return AuditCheck(
        name="identity_graph_counts",
        severity="error",
        status="ok" if not zero else "failed",
        count=len(zero),
        message="Canonical identity graph has rows in every core table." if not zero else "Some core identity tables are empty.",
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
