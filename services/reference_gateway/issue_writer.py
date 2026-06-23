from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from research.mlops.clickhouse import ClickHouseHttpClient, default_clickhouse_password, quote_ident
from services.reference_gateway.active_tickers import ActiveTickerPlan, MissingTickerCandidate
from services.reference_gateway.canonical_graph_writer import GraphWriteIssue
from services.reference_gateway.config import ReferenceGatewayConfig
from services.reference_gateway.market_publications import clone_table_schema, table_exists


@dataclass(frozen=True, slots=True)
class IssueWriteResult:
    attempted: int
    written: int
    skipped: int
    table: str
    reason: str


def write_active_ticker_mapping_issues(config: ReferenceGatewayConfig, plan: ActiveTickerPlan) -> IssueWriteResult:
    rows = [active_ticker_issue_row(candidate, plan.checked_at_utc) for candidate in plan.candidates if should_open_issue(candidate)]
    if not rows:
        return IssueWriteResult(attempted=0, written=0, skipped=len(plan.candidates), table=issue_table(config.clickhouse_write_database), reason="no_open_issue_candidates")
    client = ClickHouseHttpClient(config.clickhouse_url, config.clickhouse_user, default_clickhouse_password())
    ensure_issue_table_available(client, config)
    body = "\n".join(json.dumps(row, separators=(",", ":"), sort_keys=True) for row in rows)
    client.execute(f"INSERT INTO {issue_table(config.clickhouse_write_database)} FORMAT JSONEachRow\n{body}")
    return IssueWriteResult(attempted=len(rows), written=len(rows), skipped=len(plan.candidates) - len(rows), table=issue_table(config.clickhouse_write_database), reason="inserted_open_mapping_issues")


def write_graph_mapping_issues(config: ReferenceGatewayConfig, issues: list[GraphWriteIssue]) -> IssueWriteResult:
    if not issues:
        return IssueWriteResult(attempted=0, written=0, skipped=0, table=issue_table(config.clickhouse_write_database), reason="no_graph_issues")
    client = ClickHouseHttpClient(config.clickhouse_url, config.clickhouse_user, default_clickhouse_password())
    ensure_issue_table_available(client, config)
    rows = [graph_issue_row(issue) for issue in issues]
    body = "\n".join(json.dumps(row, separators=(",", ":"), sort_keys=True) for row in rows)
    client.execute(f"INSERT INTO {issue_table(config.clickhouse_write_database)} FORMAT JSONEachRow\n{body}")
    return IssueWriteResult(attempted=len(rows), written=len(rows), skipped=0, table=issue_table(config.clickhouse_write_database), reason="inserted_graph_mapping_issues")


def should_open_issue(candidate: MissingTickerCandidate) -> bool:
    action = candidate.proposed_action.strip().lower()
    return action.startswith("open_mapping_issue")


def active_ticker_issue_row(candidate: MissingTickerCandidate, checked_at_utc: str) -> dict[str, Any]:
    opened_at = clickhouse_datetime64(parse_utc(checked_at_utc))
    inserted_at = clickhouse_datetime64(datetime.now(UTC))
    evidence = {
        "checked_at_utc": checked_at_utc,
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
        "missing_reason": candidate.missing_reason,
        "proposed_action": candidate.proposed_action,
        "overview": candidate.overview,
        "ibkr_candidates": candidate.ibkr_candidates,
    }
    evidence_json = json.dumps(evidence, separators=(",", ":"), sort_keys=True, default=str)
    source_content_sha256 = sha256_text(evidence_json)
    issue_type = candidate.proposed_action or candidate.missing_reason
    return {
        "mapping_issue_id": "reference_issue:active_ticker:" + sha256_text(f"{candidate.ticker}|{issue_type}")[:32],
        "source_mapping_id": "",
        "source_system": "reference_gateway",
        "source_entity_kind": "massive_active_ticker",
        "source_entity_key": candidate.ticker.upper(),
        "mapped_entity_kind": "market_symbol",
        "issue_type": issue_type,
        "issue_status": "open",
        "issue_message": issue_message(candidate),
        "evidence_json": evidence_json,
        "opened_at_utc": opened_at,
        "resolved_at_utc": None,
        "source_run_id": "reference_gateway_active_ticker_" + checked_at_utc.replace(":", "").replace("-", "").replace(".", "_"),
        "source_content_sha256": source_content_sha256,
        "inserted_at": inserted_at,
    }


def graph_issue_row(issue: GraphWriteIssue) -> dict[str, Any]:
    now = datetime.now(UTC)
    opened_at = clickhouse_datetime64(now)
    evidence_json = json.dumps(issue.evidence, separators=(",", ":"), sort_keys=True, default=str)
    source_content_sha256 = sha256_text(evidence_json)
    return {
        "mapping_issue_id": "reference_issue:graph_writer:" + sha256_text(f"{issue.ticker}|{issue.issue_type}")[:32],
        "source_mapping_id": "",
        "source_system": "reference_gateway",
        "source_entity_kind": "massive_active_ticker",
        "source_entity_key": issue.ticker.upper(),
        "mapped_entity_kind": "market_symbol",
        "issue_type": issue.issue_type,
        "issue_status": "open",
        "issue_message": issue.message,
        "evidence_json": evidence_json,
        "opened_at_utc": opened_at,
        "resolved_at_utc": None,
        "source_run_id": "reference_gateway_graph_writer_" + now.strftime("%Y%m%d_%H%M%S"),
        "source_content_sha256": source_content_sha256,
        "inserted_at": opened_at,
    }


def issue_message(candidate: MissingTickerCandidate) -> str:
    if candidate.proposed_action == "open_mapping_issue_missing_massive_overview":
        return f"Massive active ticker {candidate.ticker} is missing from q_live and its company overview could not be read."
    if candidate.proposed_action == "open_mapping_issue_ibkr_lookup_failed":
        return f"Massive active ticker {candidate.ticker} is missing from q_live and IBKR lookup failed."
    if candidate.proposed_action == "open_mapping_issue_ambiguous_ibkr_contract":
        return f"Massive active ticker {candidate.ticker} is missing from q_live and IBKR returned multiple plausible stock contracts."
    return f"Massive active ticker {candidate.ticker} is missing from q_live and needs mapping before it can become tradable."


def ensure_issue_table_available(client: ClickHouseHttpClient, config: ReferenceGatewayConfig) -> None:
    if table_exists(client, config.clickhouse_write_database, "id_mapping_issue_v1"):
        return
    if not table_exists(client, config.clickhouse_read_database, "id_mapping_issue_v1"):
        raise RuntimeError(f"Source issue table is missing: {issue_table(config.clickhouse_read_database)}")
    clone_table_schema(
        client,
        source_database=config.clickhouse_read_database,
        target_database=config.clickhouse_write_database,
        table_name="id_mapping_issue_v1",
    )


def issue_table(database: str) -> str:
    return f"{quote_ident(database)}.{quote_ident('id_mapping_issue_v1')}"


def parse_utc(value: str) -> datetime:
    text = (value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def clickhouse_datetime64(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:23]


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
