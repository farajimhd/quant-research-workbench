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
CERTIFICATION_VERSION = "news_synthesis_v1_manual_certification_v1"
PRODUCTION_VERSION = "news_synthesis_v1_production_v1"


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
        "contract_version", "concept_registry_version", "sample_id", "source_id", "source_timestamp",
        "source_text_sha256", "envelope", "entities", "statements",
        "participations", "issuer_views", "synthesis", "eligibility", "quality_flags",
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
        if entity.get("entity_kind") not in {
            "issuer", "security", "index", "fund", "commodity", "currency",
            "person", "organization", "place", "product",
        }:
            issues.append(f"invalid:entity_kind:{entity_id}")
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
    participation_keys: set[tuple[Any, Any]] = set()
    for index, row in enumerate(_rows(document, "participations", issues)):
        sid, eid = row.get("statement_id"), row.get("entity_id")
        key = (sid, eid)
        if key in participation_keys:
            issues.append(f"duplicate:participation:{sid}:{eid}")
        participation_keys.add(key)
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
        elif row.get("discourse_role") == "claim_source" and entities.get(eid, {}).get("entity_kind") not in {"issuer", "person", "organization"}:
            issues.append(f"invalid:claim_source_entity_kind:{index}")
        if sentiment not in SENTIMENTS:
            issues.append(f"invalid:semantic_sentiment:{index}")
        if not isinstance(strength, int) or not 0 <= strength <= 4:
            issues.append(f"invalid:sentiment_strength:{index}")
        elif sentiment == "neutral" and strength != 0:
            issues.append(f"neutral_strength_nonzero:{index}")
        if row.get("semantic_role") == "none" and (sentiment != "neutral" or strength != 0):
            issues.append(f"nonsemantic_role_has_sentiment:{index}")
    for row in _rows(document, "issuer_views", issues):
        if row.get("entity_id") not in entities:
            issues.append("dangling:issuer_view_entity")
        if row.get("composite_sentiment") not in COMPOSITE_SENTIMENTS:
            issues.append("invalid:composite_sentiment")
        all_ids = set(row.get("statement_ids", []))
        sentiment_ids = set(row.get("positive_statement_ids", [])) | set(row.get("negative_statement_ids", [])) | set(row.get("neutral_statement_ids", []))
        if all_ids != sentiment_ids or not all_ids.issubset(statements):
            issues.append("invalid:issuer_view_statement_partition")
    synthesis = document.get("synthesis")
    if not isinstance(synthesis, Mapping):
        issues.append("invalid:synthesis")
    else:
        if synthesis.get("renderer_version") != "news_synthesis_renderer_v1":
            issues.append("invalid:synthesis_renderer_version")
        if set(synthesis.get("document_statement_ids", [])) != set(statements):
            issues.append("invalid:synthesis_statement_coverage")
    eligibility_keys: set[tuple[Any, Any]] = set()
    for row in _rows(document, "eligibility", issues):
        if row.get("entity_id") not in entities:
            issues.append("dangling:eligibility_entity")
        if row.get("product") not in PRODUCTS:
            issues.append("invalid:eligibility_product")
        if not isinstance(row.get("eligible"), bool):
            issues.append("invalid:eligible")
        key = (row.get("entity_id"), row.get("product"))
        if key in eligibility_keys:
            issues.append(f"duplicate:eligibility:{key[0]}:{key[1]}")
        eligibility_keys.add(key)
    issuer_ids = {
        entity_id for entity_id, entity in entities.items()
        if entity.get("entity_kind") in {"issuer", "security"}
    }
    expected_eligibility = {(entity_id, product) for entity_id in issuer_ids for product in PRODUCTS}
    if eligibility_keys != expected_eligibility:
        issues.append("invalid:eligibility_coverage")
    has_migration = isinstance(document.get("migration"), Mapping)
    has_certification = isinstance(document.get("certification"), Mapping)
    has_production = isinstance(document.get("production"), Mapping)
    if sum((has_migration, has_certification, has_production)) != 1:
        issues.append("invalid:provenance_exactly_one_required")
    elif has_migration and document["migration"].get("status") not in MIGRATION_STATUSES:
        issues.append("invalid:migration_status")
    elif has_certification:
        certification = document["certification"]
        if certification.get("certification_version") != CERTIFICATION_VERSION:
            issues.append("invalid:certification_version")
        if certification.get("status") != "certified":
            issues.append("invalid:certification_status")
        if not certification.get("reviewer") or not certification.get("review_notes"):
            issues.append("invalid:certification_review")
    elif has_production:
        production = document["production"]
        if production.get("production_version") != PRODUCTION_VERSION:
            issues.append("invalid:production_version")
        if not production.get("engine_version") or not production.get("generated_at_utc"):
            issues.append("invalid:production_provenance")
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
