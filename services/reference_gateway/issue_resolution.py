from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from research.mlops.clickhouse import ClickHouseHttpClient, default_clickhouse_password, quote_ident, sql_string
from services.reference_gateway.config import ReferenceGatewayConfig
from services.reference_gateway.market_publications import clone_table_schema, table_exists


OPEN_STATUS_FILTER = "lower(issue_status) NOT IN ('resolved', 'closed', 'ignored')"
BLOCKING_REVIEW_TYPES = {
    "missing_durable_issuer_identifier",
    "missing_figi_security_identifier",
    "missing_unique_ibkr_conid",
    "ambiguous_ibkr_contract",
    "unmapped_exchange",
    "provider_metadata_conflict",
}


@dataclass(frozen=True, slots=True)
class IssueResolutionRuleResult:
    rule: str
    category: str
    scanned: int
    resolved: int
    action: str
    example: str


@dataclass(frozen=True, slots=True)
class IssueResolutionResult:
    scanned: int
    resolved: int
    table: str
    reason: str
    auto_resolvable: int = 0
    auto_block_until_resolved: int = 0
    human_review_required: int = 0
    historical_repair: int = 0
    rule_results: list[IssueResolutionRuleResult] = field(default_factory=list)


def resolve_stale_active_ticker_issues(config: ReferenceGatewayConfig) -> IssueResolutionResult:
    return resolve_mapping_issues(config)


def resolve_mapping_issues(config: ReferenceGatewayConfig) -> IssueResolutionResult:
    client = ClickHouseHttpClient(config.clickhouse_url, config.clickhouse_user, default_clickhouse_password())
    ensure_issue_table_available(client, config)
    table_name = table(config.clickhouse_write_database, "id_mapping_issue_v1")

    rule_results = [
        resolve_massive_active_ticker_issues(client, config),
        resolve_weak_issuer_identity_with_durable_id(client, config),
        close_stale_weak_issuer_identity_issues(client, config),
    ]
    classified = classify_remaining_open_issues(client, config.clickhouse_read_database)
    scanned = classified["scanned"]
    resolved = sum(item.resolved for item in rule_results)
    reason = "issue_resolution_completed" if resolved else "no_deterministically_resolvable_issues"
    return IssueResolutionResult(
        scanned=scanned,
        resolved=resolved,
        table=table_name,
        reason=reason,
        auto_resolvable=classified["auto_resolvable"],
        auto_block_until_resolved=classified["auto_block_until_resolved"],
        human_review_required=classified["human_review_required"],
        historical_repair=classified["historical_repair"],
        rule_results=rule_results,
    )


def resolve_massive_active_ticker_issues(client: ClickHouseHttpClient, config: ReferenceGatewayConfig) -> IssueResolutionRuleResult:
    open_issues = query_json_each_row(
        client,
        f"""
        SELECT *
        FROM {table(config.clickhouse_read_database, 'id_mapping_issue_v1')} FINAL
        WHERE source_system = 'reference_gateway'
          AND source_entity_kind = 'massive_active_ticker'
          AND {OPEN_STATUS_FILTER}
        """,
    )
    if not open_issues:
        return rule_result("massive_active_ticker_now_resolved", "automatically_resolvable", 0, 0, "none")
    tickers = sorted({str(row.get("source_entity_key") or "").upper() for row in open_issues if str(row.get("source_entity_key") or "").strip()})
    resolved_tickers = load_resolved_tickers(client, config.clickhouse_read_database, tickers)
    rows = [resolved_issue_row(row, "canonical_symbol_now_exists") for row in open_issues if str(row.get("source_entity_key") or "").upper() in resolved_tickers]
    write_resolved_rows_and_delete_open(
        client,
        config,
        rows,
        where_sql=f"""
source_system = 'reference_gateway'
AND source_entity_kind = 'massive_active_ticker'
AND {OPEN_STATUS_FILTER}
AND upper(source_entity_key) IN ({csv_sql_strings(sorted(resolved_tickers)) if resolved_tickers else "''"})
""",
    )
    return rule_result("massive_active_ticker_now_resolved", "automatically_resolvable", len(open_issues), len(rows), "resolved_rows_inserted_then_open_rows_deleted")


def resolve_weak_issuer_identity_with_durable_id(client: ClickHouseHttpClient, config: ReferenceGatewayConfig) -> IssueResolutionRuleResult:
    scanned = count_scalar(
        client,
        f"""
        SELECT count()
        FROM {table(config.clickhouse_read_database, 'id_mapping_issue_v1')} FINAL
        WHERE source_system = 'q_live_migration'
          AND source_entity_kind = 'issuer'
          AND issue_type = 'weak_issuer_identity'
          AND {OPEN_STATUS_FILTER}
        """,
    )
    if scanned == 0:
        return rule_result("weak_issuer_identity_has_durable_id", "automatically_resolvable", 0, 0, "none")
    resolved = count_scalar(
        client,
        f"""
        SELECT count()
        FROM {table(config.clickhouse_read_database, 'id_mapping_issue_v1')} FINAL
        WHERE source_system = 'q_live_migration'
          AND source_entity_kind = 'issuer'
          AND issue_type = 'weak_issuer_identity'
          AND {OPEN_STATUS_FILTER}
          AND source_entity_key IN ({durable_issuer_subquery(config.clickhouse_read_database)})
        """,
    )
    if resolved:
        insert_resolved_weak_issuer_rows(
            client,
            config,
            where_suffix=f"source_entity_key IN ({durable_issuer_subquery(config.clickhouse_read_database)})",
            resolution="issuer_now_has_cik_lei_or_ein",
        )
        delete_open_issues(
            client,
            config,
            f"""
source_system = 'q_live_migration'
AND source_entity_kind = 'issuer'
AND issue_type = 'weak_issuer_identity'
AND {OPEN_STATUS_FILTER}
AND source_entity_key IN ({durable_issuer_subquery(config.clickhouse_read_database)})
""",
        )
    return rule_result("weak_issuer_identity_has_durable_id", "automatically_resolvable", scanned, resolved, "resolved_if_cik_lei_or_ein_exists")


def close_stale_weak_issuer_identity_issues(client: ClickHouseHttpClient, config: ReferenceGatewayConfig) -> IssueResolutionRuleResult:
    scanned = count_scalar(
        client,
        f"""
        SELECT count()
        FROM {table(config.clickhouse_read_database, 'id_mapping_issue_v1')} FINAL
        WHERE source_system = 'q_live_migration'
          AND source_entity_kind = 'issuer'
          AND issue_type = 'weak_issuer_identity'
          AND {OPEN_STATUS_FILTER}
        """,
    )
    stale = count_scalar(
        client,
        f"""
        SELECT count()
        FROM {table(config.clickhouse_read_database, 'id_mapping_issue_v1')} FINAL
        WHERE source_system = 'q_live_migration'
          AND source_entity_kind = 'issuer'
          AND issue_type = 'weak_issuer_identity'
          AND {OPEN_STATUS_FILTER}
          AND source_entity_key NOT IN ({active_weak_candidate_issuer_subquery(config.clickhouse_read_database)})
        """,
    )
    if stale:
        insert_resolved_weak_issuer_rows(
            client,
            config,
            where_suffix=f"source_entity_key NOT IN ({active_weak_candidate_issuer_subquery(config.clickhouse_read_database)})",
            resolution="issuer_no_longer_has_active_us_stock_candidate",
        )
        delete_open_issues(
            client,
            config,
            f"""
source_system = 'q_live_migration'
AND source_entity_kind = 'issuer'
AND issue_type = 'weak_issuer_identity'
AND {OPEN_STATUS_FILTER}
AND source_entity_key NOT IN ({active_weak_candidate_issuer_subquery(config.clickhouse_read_database)})
""",
        )
    return rule_result("weak_issuer_identity_no_longer_active_candidate", "historical_repair", scanned, stale, "closed_if_no_current_active_candidate")


def classify_remaining_open_issues(client: ClickHouseHttpClient, database: str) -> dict[str, int]:
    rows = query_json_each_row(
        client,
        f"""
        SELECT issue_type, source_system, source_entity_kind, count() AS rows
        FROM {table(database, 'id_mapping_issue_v1')} FINAL
        WHERE {OPEN_STATUS_FILTER}
        GROUP BY issue_type, source_system, source_entity_kind
        """,
    )
    counts = {
        "scanned": 0,
        "auto_resolvable": 0,
        "auto_block_until_resolved": 0,
        "human_review_required": 0,
        "historical_repair": 0,
    }
    for row in rows:
        row_count = int(row.get("rows") or 0)
        issue_type = str(row.get("issue_type") or "")
        source_kind = str(row.get("source_entity_kind") or "")
        counts["scanned"] += row_count
        if issue_type == "weak_issuer_identity" and source_kind == "issuer":
            counts["auto_block_until_resolved"] += row_count
        elif issue_type in BLOCKING_REVIEW_TYPES:
            counts["human_review_required"] += row_count
        elif issue_type.startswith("historical_"):
            counts["historical_repair"] += row_count
        else:
            counts["auto_block_until_resolved"] += row_count
    return counts


def load_resolved_tickers(client: ClickHouseHttpClient, database: str, tickers: list[str]) -> set[str]:
    if not tickers:
        return set()
    rows = query_json_each_row(
        client,
        f"""
        SELECT DISTINCT upper(s.ticker) AS ticker
        FROM {table(database, 'id_symbol_v1')} s FINAL
        INNER JOIN {table(database, 'id_listing_v1')} l FINAL ON l.listing_id = s.listing_id
        INNER JOIN {table(database, 'id_security_v1')} sec FINAL ON sec.security_id = l.security_id
        LEFT JOIN {table(database, 'ref_exchange_v1')} ex FINAL ON ex.exchange_code = l.exchange_code
        WHERE upper(s.ticker) IN ({csv_sql_strings(tickers)})
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


def insert_resolved_weak_issuer_rows(client: ClickHouseHttpClient, config: ReferenceGatewayConfig, *, where_suffix: str, resolution: str) -> None:
    now = clickhouse_now64()
    client.execute(
        f"""
        INSERT INTO {table(config.clickhouse_write_database, 'id_mapping_issue_v1')}
        (mapping_issue_id, source_mapping_id, source_system, source_entity_kind, source_entity_key, mapped_entity_kind, issue_type, issue_status, issue_message, evidence_json, opened_at_utc, resolved_at_utc, source_run_id, source_content_sha256, inserted_at)
        SELECT
            mapping_issue_id,
            source_mapping_id,
            source_system,
            source_entity_kind,
            source_entity_key,
            mapped_entity_kind,
            issue_type,
            'resolved' AS issue_status,
            issue_message,
            concat('{{"resolution":"{resolution}","resolved_by":"reference_gateway","previous_evidence":', toJSONString(evidence_json), '}}') AS evidence_json,
            opened_at_utc,
            toDateTime64({sql_string(now)}, 3, 'UTC') AS resolved_at_utc,
            'reference_gateway_issue_resolution' AS source_run_id,
            source_content_sha256,
            toDateTime64({sql_string(now)}, 3, 'UTC') AS inserted_at
        FROM {table(config.clickhouse_read_database, 'id_mapping_issue_v1')} FINAL
        WHERE source_system = 'q_live_migration'
          AND source_entity_kind = 'issuer'
          AND issue_type = 'weak_issuer_identity'
          AND {OPEN_STATUS_FILTER}
          AND {where_suffix}
        """
    )


def write_resolved_rows_and_delete_open(client: ClickHouseHttpClient, config: ReferenceGatewayConfig, rows: list[dict[str, Any]], *, where_sql: str) -> None:
    if not rows:
        return
    body = "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) for row in rows)
    client.execute(f"INSERT INTO {table(config.clickhouse_write_database, 'id_mapping_issue_v1')} FORMAT JSONEachRow\n{body}")
    delete_open_issues(client, config, where_sql)


def delete_open_issues(client: ClickHouseHttpClient, config: ReferenceGatewayConfig, where_sql: str) -> None:
    client.execute(
        f"""
        ALTER TABLE {table(config.clickhouse_write_database, 'id_mapping_issue_v1')}
        DELETE WHERE {where_sql}
        SETTINGS mutations_sync = 1
        """
    )


def resolved_issue_row(row: dict[str, Any], resolution: str) -> dict[str, Any]:
    now = clickhouse_now64()
    payload = dict(row)
    payload["issue_status"] = "resolved"
    payload["resolved_at_utc"] = now
    payload["source_run_id"] = "reference_gateway_issue_resolution"
    payload["inserted_at"] = now
    existing_evidence = str(payload.get("evidence_json") or "")
    try:
        evidence = json.loads(existing_evidence) if existing_evidence else {}
    except json.JSONDecodeError:
        evidence = {"previous_evidence_json": existing_evidence}
    evidence["resolution"] = {
        "resolution": resolution,
        "resolved_by": "reference_gateway",
        "resolved_at_utc": now,
    }
    payload["evidence_json"] = json.dumps(evidence, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)
    return payload


def durable_issuer_subquery(database: str) -> str:
    return f"""
    SELECT DISTINCT issuer_id
    FROM {table(database, 'id_issuer_identifier_v1')} FINAL
    WHERE lower(identifier_kind) IN ('cik', 'lei', 'ein')
      AND identifier_value_normalized != ''
    """


def active_weak_candidate_issuer_subquery(database: str) -> str:
    return f"""
    SELECT DISTINCT sec.issuer_id
    FROM {table(database, 'id_symbol_v1')} AS sym FINAL
    INNER JOIN {table(database, 'id_listing_v1')} AS listing FINAL ON listing.listing_id = sym.listing_id
    INNER JOIN {table(database, 'id_security_v1')} AS sec FINAL ON sec.security_id = listing.security_id
    LEFT JOIN {table(database, 'ref_exchange_v1')} AS ex FINAL ON ex.exchange_code = listing.exchange_code
    WHERE sym.status = 'active'
      AND sym.primary_symbol_flag = 1
      AND listing.listing_status = 'active'
      AND upper(listing.currency_code) = 'USD'
      AND upper(ifNull(ex.iso_country_code, '')) = 'US'
      AND upper(sec.product_type) IN ('STK', 'STOCK', 'STOCKS')
      AND sec.issuer_id NOT IN ({durable_issuer_subquery(database)})
    """


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


def count_scalar(client: ClickHouseHttpClient, sql: str) -> int:
    text = client.execute(sql.rstrip(";")).strip()
    return int(text or 0)


def csv_sql_strings(values: list[str]) -> str:
    return ", ".join(sql_string(value) for value in values) if values else "''"


def rule_result(rule: str, category: str, scanned: int, resolved: int, action: str) -> IssueResolutionRuleResult:
    examples = {
        "massive_active_ticker_now_resolved": "A new Massive ticker was missing yesterday, but today id_symbol_v1 contains the active ticker with a valid USD US stock listing and IBKR conid.",
        "weak_issuer_identity_has_durable_id": "An issuer was weak because it had no CIK/LEI/EIN; a later SEC bridge or identifier import added a CIK, so the weak-identity issue can close.",
        "weak_issuer_identity_no_longer_active_candidate": "A weak issuer no longer has any active US stock candidate, so the issue is historical housekeeping and no longer blocks current trading.",
    }
    return IssueResolutionRuleResult(rule, category, scanned, resolved, action, examples.get(rule, "No example registered."))


def clickhouse_now64() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def table(database: str, name: str) -> str:
    return f"{quote_ident(database)}.{quote_ident(name)}"
