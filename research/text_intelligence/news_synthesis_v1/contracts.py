from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


CONTRACT_VERSION = "news_synthesis_v1"
MIGRATION_VERSION = "news_synthesis_v1_gold_migration_v1"
DOCUMENT_STRUCTURES = frozenset(("single_subject", "multi_subject_digest", "market_overview", "reference_list"))
COMMUNICATION_PURPOSES = frozenset(("report", "analyze", "preview", "recap", "explain_move"))
INFORMATION_ORIGINS = frozenset(("issuer", "regulator", "analyst", "editorial", "mixed", "unknown"))
PRODUCTION_METHODS = frozenset(("original", "aggregated", "syndicated", "automated", "unknown"))
TEXT_AVAILABILITY = frozenset(("rendered", "title_only", "unrendered", "invalid"))
STATEMENT_KINDS = frozenset(("event", "assessment", "forecast", "market_observation", "background", "reference"))
EPISTEMIC_STATUSES = frozenset(("confirmed", "planned", "expected", "rumored", "conditional"))
TIME_RELATIONS = frozenset(("historical", "current", "forward"))
SEMANTIC_ROLES = frozenset(("affected_subject", "acquirer", "target", "counterparty", "none"))
DISCOURSE_ROLES = frozenset(("claim_source", "context_mention", "none"))
SENTIMENTS = frozenset(("positive", "negative", "neutral"))
COMPOSITE_SENTIMENTS = SENTIMENTS | {"mixed"}
IDENTITY_STATUSES = frozenset(("resolved", "ambiguous", "unresolved", "not_tradable_as_of"))
PRODUCTS = frozenset(("forecast_trigger", "reaction_study", "issuer_history", "analyst_evaluation"))
MIGRATION_STATUSES = frozenset(("exact", "rule_mapped", "review_required", "rejected"))


@dataclass(frozen=True, slots=True)
class ValidationResult:
    valid: bool
    issues: tuple[str, ...]


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def validate_document(document: Mapping[str, Any]) -> ValidationResult:
    issues: list[str] = []
    required = (
        "contract_version", "sample_id", "source_id", "source_timestamp",
        "source_text_sha256", "envelope", "entities", "statements",
        "participations", "issuer_views", "eligibility", "quality_flags", "migration",
    )
    for field in required:
        if field not in document:
            issues.append(f"missing:{field}")
    if issues:
        return ValidationResult(False, tuple(issues))
    if document["contract_version"] != CONTRACT_VERSION:
        issues.append("invalid:contract_version")
    if len(str(document["source_text_sha256"])) != 64:
        issues.append("invalid:source_text_sha256")
    envelope = document["envelope"]
    _decision_value(envelope, "document_structure", DOCUMENT_STRUCTURES, issues)
    _decision_value(envelope, "communication_purpose", COMMUNICATION_PURPOSES, issues)
    _decision_value(envelope, "information_origin", INFORMATION_ORIGINS, issues)
    _decision_value(envelope, "production_method", PRODUCTION_METHODS, issues)
    _decision_value(envelope, "text_availability", TEXT_AVAILABILITY, issues)

    entities = {row.get("entity_id"): row for row in _rows(document, "entities", issues)}
    statement_rows = _rows(document, "statements", issues)
    statements = {row.get("statement_id"): row for row in statement_rows}
    if len(entities) != len(document["entities"]):
        issues.append("duplicate:entity_id")
    if len(statements) != len(statement_rows):
        issues.append("duplicate:statement_id")
    for entity_id, entity in entities.items():
        if not entity_id:
            issues.append("invalid:entity_id")
        if entity.get("identity_status") not in IDENTITY_STATUSES:
            issues.append(f"invalid:identity_status:{entity_id}")
    for statement_id, statement in statements.items():
        if not statement_id:
            issues.append("invalid:statement_id")
        if statement.get("statement_kind") not in STATEMENT_KINDS:
            issues.append(f"invalid:statement_kind:{statement_id}")
        if statement.get("epistemic_status") not in EPISTEMIC_STATUSES:
            issues.append(f"invalid:epistemic_status:{statement_id}")
        if statement.get("time_relation") not in TIME_RELATIONS:
            issues.append(f"invalid:time_relation:{statement_id}")
        spans = statement.get("evidence_spans")
        if not isinstance(spans, list) or not spans:
            issues.append(f"missing:evidence_spans:{statement_id}")
        else:
            for index, span in enumerate(spans):
                _validate_span(span, f"{statement_id}:{index}", issues)
    for index, row in enumerate(_rows(document, "participations", issues)):
        sid, eid = row.get("statement_id"), row.get("entity_id")
        if sid not in statements:
            issues.append(f"dangling:participation_statement:{index}")
        if eid not in entities:
            issues.append(f"dangling:participation_entity:{index}")
        sentiment = row.get("semantic_sentiment")
        strength = row.get("sentiment_strength")
        if row.get("semantic_role") not in SEMANTIC_ROLES:
            issues.append(f"invalid:semantic_role:{index}")
        if row.get("discourse_role") not in DISCOURSE_ROLES:
            issues.append(f"invalid:discourse_role:{index}")
        if sentiment not in SENTIMENTS:
            issues.append(f"invalid:semantic_sentiment:{index}")
        if not isinstance(strength, int) or not 0 <= strength <= 4:
            issues.append(f"invalid:sentiment_strength:{index}")
        elif sentiment == "neutral" and strength != 0:
            issues.append(f"neutral_strength_nonzero:{index}")
    for row in _rows(document, "issuer_views", issues):
        if row.get("entity_id") not in entities:
            issues.append("dangling:issuer_view_entity")
        if row.get("composite_sentiment") not in COMPOSITE_SENTIMENTS:
            issues.append("invalid:composite_sentiment")
    for row in _rows(document, "eligibility", issues):
        if row.get("entity_id") not in entities:
            issues.append("dangling:eligibility_entity")
        if row.get("product") not in PRODUCTS:
            issues.append("invalid:eligibility_product")
        if not isinstance(row.get("eligible"), bool):
            issues.append("invalid:eligible")
    if document["migration"].get("status") not in MIGRATION_STATUSES:
        issues.append("invalid:migration_status")
    return ValidationResult(not issues, tuple(dict.fromkeys(issues)))


def _rows(document: Mapping[str, Any], field: str, issues: list[str]) -> list[Mapping[str, Any]]:
    value = document.get(field)
    if not isinstance(value, list):
        issues.append(f"invalid:{field}")
        return []
    rows = [row for row in value if isinstance(row, Mapping)]
    if len(rows) != len(value):
        issues.append(f"invalid:{field}:row")
    return rows


def _decision_value(envelope: Mapping[str, Any], field: str, allowed: Sequence[str], issues: list[str]) -> None:
    decision = envelope.get(field)
    if not isinstance(decision, Mapping) or decision.get("value") not in allowed:
        issues.append(f"invalid:envelope:{field}")


def _validate_span(span: Any, label: str, issues: list[str]) -> None:
    if not isinstance(span, Mapping):
        issues.append(f"invalid:evidence:{label}")
        return
    start, end, quote = span.get("start"), span.get("end"), span.get("quote")
    if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end < start:
        issues.append(f"invalid:evidence_offsets:{label}")
    if not isinstance(quote, str) or not quote:
        issues.append(f"invalid:evidence_quote:{label}")
