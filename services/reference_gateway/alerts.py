from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from research.mlops.clickhouse import ClickHouseHttpClient, default_clickhouse_password, quote_ident
from services.reference_gateway.active_tickers import ActiveTickerPlan, MissingTickerCandidate
from services.reference_gateway.audit import ReferenceAuditReport
from services.reference_gateway.canonical_graph_writer import GraphWriteIssue
from services.reference_gateway.config import ReferenceGatewayConfig
from services.reference_gateway.market_publications import mergetree_settings
from services.reference_gateway.tradable_blocker import TradabilityBlockResult


ALERT_TABLE = "market_reference_alert_v1"
ALERT_CONSUMER_STATE_TABLE = "market_reference_alert_consumer_state_v1"


@dataclass(frozen=True, slots=True)
class AlertRule:
    alert_family: str
    alert_group: str
    alert_type: str
    default_severity: str
    consumer_groups: tuple[str, ...]
    requires_recompute: bool
    recompute_scope: str
    affects_tradability: bool = False
    requires_review: bool = False
    time_sensitivity: str = "intraday"


@dataclass(frozen=True, slots=True)
class ReferenceAlert:
    alert_family: str
    alert_group: str
    alert_type: str
    alert_subtype: str
    severity: str
    status: str
    source_system: str
    source_provider: str
    source_table: str
    source_event_id: str
    source_timestamp_utc: datetime
    detected_at_utc: datetime
    title: str
    message: str
    issuer_id: str | None = None
    security_id: str | None = None
    listing_id: str | None = None
    symbol_id: str | None = None
    provider_ticker: str | None = None
    cik: str | None = None
    accession_number: str | None = None
    ibkr_conid: str | None = None
    direction: str = "unknown"
    event_status: str = "detected"
    impact_scope: str = "provider_only"
    time_sensitivity: str = "intraday"
    confidence_score: float | None = None
    impact_score: float | None = None
    requires_recompute: bool = False
    recompute_scope: str = "none"
    affects_tradability: bool = False
    requires_review: bool = False
    primary_label: str = ""
    secondary_labels: tuple[str, ...] = field(default_factory=tuple)
    consumer_groups: tuple[str, ...] = field(default_factory=tuple)
    action_flags: tuple[str, ...] = field(default_factory=tuple)
    source_event_version: str = "1"
    source_evidence_ref: str = ""
    source_content_sha256: str = ""
    expires_at_utc: datetime | None = None

    def alert_id(self) -> str:
        parts = [
            self.source_system,
            self.source_table,
            self.source_event_id,
            self.alert_type,
            self.alert_subtype,
            self.issuer_id or "",
            self.security_id or "",
            self.listing_id or "",
            self.symbol_id or "",
            self.provider_ticker or "",
        ]
        return "reference_alert:" + sha256_text("|".join(parts))[:32]

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AlertWriteResult:
    attempted: int
    written: int
    table: str
    reason: str


ALERT_RULES: tuple[AlertRule, ...] = (
    AlertRule("data_quality", "provider_health", "source_sync_saturated", "warning", ("operations",), False, "none"),
    AlertRule("tradability_guardrail", "identity_mapping", "mapping_issue_opened", "warning", ("scanner", "tradability", "review"), True, "symbol", True, True, "immediate"),
    AlertRule("tradability_guardrail", "identity_mapping", "graph_mapping_issue", "warning", ("scanner", "tradability", "review"), True, "symbol", True, True, "immediate"),
    AlertRule("tradability_guardrail", "identity_mapping", "tradability_block_published", "warning", ("scanner", "tradability"), True, "symbol", True, False, "immediate"),
    AlertRule("data_quality", "reference_audit", "reference_audit_check_failed", "error", ("operations", "review"), False, "none", False, True),
    AlertRule("market_publication", "publication_gap", "market_publication_gap_fill_failed", "warning", ("operations", "scanner"), True, "market", False, True),
)


def ensure_alert_schema(client: ClickHouseHttpClient, *, database: str, storage_policy: str = "") -> None:
    client.execute(f"CREATE DATABASE IF NOT EXISTS {quote_ident(database)}")
    settings = mergetree_settings(storage_policy)
    client.execute(
        f"""
CREATE TABLE IF NOT EXISTS {table(database, ALERT_TABLE)}
(
    alert_id String,
    alert_version UInt32,
    alert_family LowCardinality(String),
    alert_group LowCardinality(String),
    alert_type LowCardinality(String),
    alert_subtype LowCardinality(String),
    severity LowCardinality(String),
    status LowCardinality(String),
    source_system LowCardinality(String),
    source_provider LowCardinality(String),
    source_table String,
    source_event_id String,
    source_event_version String,
    source_timestamp_utc DateTime64(3, 'UTC'),
    detected_at_utc DateTime64(3, 'UTC'),
    source_evidence_ref String,
    source_content_sha256 String,
    issuer_id Nullable(String),
    security_id Nullable(String),
    listing_id Nullable(String),
    symbol_id Nullable(String),
    provider_ticker Nullable(String),
    cik Nullable(String),
    accession_number Nullable(String),
    ibkr_conid Nullable(String),
    direction LowCardinality(String),
    event_status LowCardinality(String),
    impact_scope LowCardinality(String),
    time_sensitivity LowCardinality(String),
    confidence_score Nullable(Float64),
    impact_score Nullable(Float64),
    requires_recompute UInt8,
    recompute_scope LowCardinality(String),
    affects_tradability UInt8,
    requires_review UInt8,
    title String,
    message String,
    primary_label LowCardinality(String),
    secondary_labels Array(String),
    consumer_groups Array(String),
    action_flags Array(String),
    first_seen_at_utc DateTime64(3, 'UTC'),
    last_seen_at_utc DateTime64(3, 'UTC'),
    processed_at_utc Nullable(DateTime64(3, 'UTC')),
    expires_at_utc Nullable(DateTime64(3, 'UTC')),
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(detected_at_utc)
ORDER BY (alert_family, alert_group, alert_type, ifNull(symbol_id, ''), source_timestamp_utc, alert_id)
SETTINGS {settings}
""".strip()
    )
    client.execute(
        f"""
CREATE TABLE IF NOT EXISTS {table(database, ALERT_CONSUMER_STATE_TABLE)}
(
    consumer_id String,
    alert_id String,
    consumer_group LowCardinality(String),
    status LowCardinality(String),
    claimed_at_utc Nullable(DateTime64(3, 'UTC')),
    processed_at_utc Nullable(DateTime64(3, 'UTC')),
    last_error Nullable(String),
    attempt_count UInt32,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(inserted_at)
ORDER BY (consumer_id, alert_id)
SETTINGS {settings}
""".strip()
    )


def write_alerts(config: ReferenceGatewayConfig, alerts: list[ReferenceAlert], *, reason: str) -> AlertWriteResult:
    target = table(config.clickhouse_write_database, ALERT_TABLE)
    if not alerts:
        return AlertWriteResult(0, 0, target, "no_alerts_for_" + reason)
    client = ClickHouseHttpClient(config.clickhouse_url, config.clickhouse_user, default_clickhouse_password())
    ensure_alert_schema(
        client,
        database=config.clickhouse_write_database,
        storage_policy=_storage_policy(),
    )
    rows = [alert_row(alert) for alert in alerts]
    body = "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str) for row in rows)
    client.execute(f"INSERT INTO {target} FORMAT JSONEachRow\n{body}")
    return AlertWriteResult(len(rows), len(rows), target, "inserted_alerts_for_" + reason)


def build_audit_alerts(report: ReferenceAuditReport, *, report_path: str, post_write: bool = False) -> list[ReferenceAlert]:
    detected = parse_utc(report.checked_at_utc)
    alerts: list[ReferenceAlert] = []
    for check in report.checks:
        if check.status == "ok":
            continue
        severity = check.severity or "warning"
        subtype = check.name
        payload = {
            "check": check.public_dict() if hasattr(check, "public_dict") else asdict(check),
            "report_path": report_path,
            "post_write": post_write,
        }
        alerts.append(
            ReferenceAlert(
                alert_family="data_quality",
                alert_group="reference_audit",
                alert_type="reference_audit_check_failed",
                alert_subtype=subtype,
                severity=severity,
                status="active",
                source_system="reference_gateway",
                source_provider="reference_gateway",
                source_table="reference_audit_report",
                source_event_id=f"{report.read_database}:{report.write_database}:{check.name}",
                source_timestamp_utc=detected,
                detected_at_utc=detected,
                title=f"Reference audit {severity}: {check.name}",
                message=check.message,
                impact_scope="market" if check.count else "provider_only",
                confidence_score=1.0,
                requires_review=severity == "error",
                primary_label="reference_audit",
                secondary_labels=(severity, check.name, "post_write" if post_write else "pre_write"),
                consumer_groups=("operations", "review"),
                action_flags=("review_required",) if severity == "error" else ("monitor",),
                source_evidence_ref=report_path,
                source_content_sha256=sha256_json(payload),
            )
        )
    return alerts


def build_source_sync_alerts(plan: ActiveTickerPlan) -> list[ReferenceAlert]:
    detected = parse_utc(plan.checked_at_utc)
    alerts: list[ReferenceAlert] = []
    if plan.provider_saturated:
        alerts.append(
            ReferenceAlert(
                alert_family="data_quality",
                alert_group="provider_health",
                alert_type="source_sync_saturated",
                alert_subtype="massive_active_ticker_list",
                severity="warning",
                status="active",
                source_system="massive",
                source_provider="massive",
                source_table="massive_active_ticker_list",
                source_event_id="massive_active_ticker_list_saturated",
                source_timestamp_utc=detected,
                detected_at_utc=detected,
                title="Massive active ticker sync saturated",
                message=f"Massive active ticker sync reached configured page limit after {plan.provider_pages:,} page(s).",
                impact_scope="market",
                time_sensitivity="intraday",
                confidence_score=1.0,
                primary_label="provider_saturated",
                secondary_labels=("massive", "active_tickers"),
                consumer_groups=("operations",),
                action_flags=("increase_page_limit", "monitor"),
                source_content_sha256=sha256_json(plan.public_dict()),
            )
        )
    alerts.extend(build_active_ticker_alerts(plan))
    return alerts


def build_active_ticker_alerts(plan: ActiveTickerPlan) -> list[ReferenceAlert]:
    detected = parse_utc(plan.checked_at_utc)
    alerts: list[ReferenceAlert] = []
    for candidate in plan.candidates:
        if not candidate.proposed_action.startswith("open_mapping_issue"):
            continue
        labels = ("massive_active_ticker", candidate.proposed_action, candidate.ticker_type or "unknown_type")
        alerts.append(
            ReferenceAlert(
                alert_family="tradability_guardrail",
                alert_group="identity_mapping",
                alert_type="mapping_issue_opened",
                alert_subtype=candidate.proposed_action,
                severity="warning",
                status="active",
                source_system="reference_gateway",
                source_provider="massive",
                source_table="massive_active_ticker_list",
                source_event_id=f"active_ticker:{candidate.ticker}:{candidate.proposed_action}",
                source_timestamp_utc=detected,
                detected_at_utc=detected,
                title=f"Active ticker needs mapping: {candidate.ticker}",
                message=active_ticker_alert_message(candidate),
                provider_ticker=candidate.ticker,
                cik=candidate.cik or None,
                ibkr_conid=unique_ibkr_conid(candidate),
                impact_scope="symbol",
                time_sensitivity="immediate",
                confidence_score=0.95,
                requires_recompute=True,
                recompute_scope="symbol",
                affects_tradability=True,
                requires_review=True,
                primary_label="mapping_issue",
                secondary_labels=labels,
                consumer_groups=("scanner", "tradability", "review"),
                action_flags=("block_tradability", "review_required"),
                source_evidence_ref="active_ticker_plan",
                source_content_sha256=sha256_json(candidate.public_dict() if hasattr(candidate, "public_dict") else asdict(candidate)),
            )
        )
    return alerts


def build_graph_issue_alerts(issues: list[GraphWriteIssue]) -> list[ReferenceAlert]:
    detected = datetime.now(UTC)
    alerts: list[ReferenceAlert] = []
    for issue in issues:
        alerts.append(
            ReferenceAlert(
                alert_family="tradability_guardrail",
                alert_group="identity_mapping",
                alert_type="graph_mapping_issue",
                alert_subtype=issue.issue_type,
                severity="warning",
                status="active",
                source_system="reference_gateway",
                source_provider="reference_gateway",
                source_table="canonical_graph_writer",
                source_event_id=f"graph_issue:{issue.ticker}:{issue.issue_type}",
                source_timestamp_utc=detected,
                detected_at_utc=detected,
                title=f"Canonical graph issue: {issue.ticker}",
                message=issue.message,
                provider_ticker=issue.ticker,
                impact_scope="symbol",
                time_sensitivity="immediate",
                confidence_score=0.95,
                requires_recompute=True,
                recompute_scope="symbol",
                affects_tradability=True,
                requires_review=True,
                primary_label="graph_mapping_issue",
                secondary_labels=(issue.issue_type,),
                consumer_groups=("scanner", "tradability", "review"),
                action_flags=("block_tradability", "review_required"),
                source_evidence_ref="canonical_graph_writer",
                source_content_sha256=sha256_json(issue.evidence),
            )
        )
    return alerts


def build_tradability_block_alert(result: TradabilityBlockResult, *, reason: str) -> list[ReferenceAlert]:
    if result.rows_blocked <= 0:
        return []
    detected = datetime.now(UTC)
    return [
        ReferenceAlert(
            alert_family="tradability_guardrail",
            alert_group="identity_mapping",
            alert_type="tradability_block_published",
            alert_subtype=reason,
            severity="warning",
            status="active",
            source_system="reference_gateway",
            source_provider="reference_gateway",
            source_table="feature_tradable_universe_v1",
            source_event_id=f"tradability_block:{reason}",
            source_timestamp_utc=detected,
            detected_at_utc=detected,
            title="Tradability block published",
            message=f"{result.rows_blocked:,} latest tradable-universe row(s) were marked non-tradable because of open reference issues.",
            impact_scope="market",
            time_sensitivity="immediate",
            confidence_score=1.0,
            impact_score=float(result.rows_blocked),
            requires_recompute=True,
            recompute_scope="market",
            affects_tradability=True,
            primary_label="tradability_block",
            secondary_labels=(reason,),
            consumer_groups=("scanner", "tradability"),
            action_flags=("tradability_updated",),
            source_evidence_ref=result.table,
            source_content_sha256=sha256_json(asdict(result)),
        )
    ]


def build_publication_maintenance_alert(status: str, reason: str, payload: dict[str, Any]) -> list[ReferenceAlert]:
    if status not in {"failed", "error"}:
        return []
    detected = datetime.now(UTC)
    return [
        ReferenceAlert(
            alert_family="market_publication",
            alert_group="publication_gap",
            alert_type="market_publication_gap_fill_failed",
            alert_subtype=str(payload.get("source") or "recent_publication_gap_fill"),
            severity="warning",
            status="active",
            source_system="reference_gateway",
            source_provider="reference_gateway",
            source_table="market_reference_publication_coverage_v1",
            source_event_id="market_publication_gap_fill_failed:" + sha256_json(payload)[:16],
            source_timestamp_utc=detected,
            detected_at_utc=detected,
            title="Market publication gap fill failed",
            message=reason,
            impact_scope="market",
            time_sensitivity="daily",
            confidence_score=1.0,
            requires_recompute=True,
            recompute_scope="market",
            requires_review=True,
            primary_label="publication_gap_fill_failed",
            secondary_labels=("market_publication",),
            consumer_groups=("operations", "scanner"),
            action_flags=("review_required",),
            source_content_sha256=sha256_json(payload),
        )
    ]


def alert_row(alert: ReferenceAlert) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "alert_id": alert.alert_id(),
        "alert_version": 1,
        "alert_family": alert.alert_family,
        "alert_group": alert.alert_group,
        "alert_type": alert.alert_type,
        "alert_subtype": alert.alert_subtype,
        "severity": alert.severity,
        "status": alert.status,
        "source_system": alert.source_system,
        "source_provider": alert.source_provider,
        "source_table": alert.source_table,
        "source_event_id": alert.source_event_id,
        "source_event_version": alert.source_event_version,
        "source_timestamp_utc": dt64(alert.source_timestamp_utc),
        "detected_at_utc": dt64(alert.detected_at_utc),
        "source_evidence_ref": alert.source_evidence_ref,
        "source_content_sha256": alert.source_content_sha256 or sha256_json(alert.public_dict()),
        "issuer_id": alert.issuer_id,
        "security_id": alert.security_id,
        "listing_id": alert.listing_id,
        "symbol_id": alert.symbol_id,
        "provider_ticker": alert.provider_ticker,
        "cik": alert.cik,
        "accession_number": alert.accession_number,
        "ibkr_conid": alert.ibkr_conid,
        "direction": alert.direction,
        "event_status": alert.event_status,
        "impact_scope": alert.impact_scope,
        "time_sensitivity": alert.time_sensitivity,
        "confidence_score": alert.confidence_score,
        "impact_score": alert.impact_score,
        "requires_recompute": int(alert.requires_recompute),
        "recompute_scope": alert.recompute_scope,
        "affects_tradability": int(alert.affects_tradability),
        "requires_review": int(alert.requires_review),
        "title": alert.title,
        "message": alert.message,
        "primary_label": alert.primary_label,
        "secondary_labels": list(alert.secondary_labels),
        "consumer_groups": list(alert.consumer_groups),
        "action_flags": list(alert.action_flags),
        "first_seen_at_utc": dt64(alert.detected_at_utc),
        "last_seen_at_utc": dt64(alert.detected_at_utc),
        "processed_at_utc": None,
        "expires_at_utc": dt64(alert.expires_at_utc) if alert.expires_at_utc else None,
        "inserted_at": dt64(now),
    }


def active_ticker_alert_message(candidate: MissingTickerCandidate) -> str:
    action = candidate.proposed_action
    if action == "open_mapping_issue_missing_massive_overview":
        return f"Massive active ticker {candidate.ticker} is missing from q_live and Massive overview could not be read."
    if action == "open_mapping_issue_ibkr_lookup_failed":
        return f"Massive active ticker {candidate.ticker} is missing from q_live and IBKR lookup failed."
    if action == "open_mapping_issue_ambiguous_ibkr_contract":
        return f"Massive active ticker {candidate.ticker} is missing from q_live and IBKR returned multiple plausible contracts."
    if action == "open_mapping_issue_missing_unique_ibkr_conid":
        return f"Massive active ticker {candidate.ticker} is missing from q_live and has no unique IBKR conid."
    return f"Massive active ticker {candidate.ticker} is missing from q_live and needs reference mapping."


def unique_ibkr_conid(candidate: MissingTickerCandidate) -> str | None:
    rows = [row for row in candidate.ibkr_candidates if str(row.get("conid") or "").isdigit()]
    if len(rows) == 1:
        return str(rows[0].get("conid") or "")
    return None


def table(database: str, name: str) -> str:
    return f"{quote_ident(database)}.{quote_ident(name)}"


def parse_utc(value: str) -> datetime:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def dt64(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:23]


def sha256_json(value: Any) -> str:
    return sha256_text(json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _storage_policy() -> str:
    import os

    return os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY") or ""
