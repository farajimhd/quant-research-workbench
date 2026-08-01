from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from research.text_intelligence.scoped_labeling_v1.news_identity import NewsIssuerResolver
from research.text_intelligence.scoped_labeling_v1.pipeline import classify_news_document
from research.text_intelligence.semantic_label_authority_v1.schema import SemanticDocument

from .deterministic_v6 import _deduplicate_labels
from .deterministic_v6_config import DIRECTION_RULES
from .deterministic_v7 import (
    AGGREGATION_ROLES,
    CONTEXT_ONLY_UNIT_ROLES,
    NON_TRIGGER_ROLES,
    _classify_origin,
    _classify_role,
    _extraction_decision,
    _has_high_value_event,
    _retain_unit,
)
from .deterministic_v8_config import (
    ANALYST_CHANNELS,
    AUTOMATED_TAGS,
    CONTEXT_EVENT_PATTERNS,
    DETERMINISTIC_V8_VERSION,
    DIRECTION_RULES_V8,
    MARKET_UPDATE_TAGS,
    MOVER_TAGS,
    PREVIEW_CHANNELS,
    PREVIEW_TAGS,
)


@dataclass(frozen=True, slots=True)
class DeterministicNewsResultV8:
    version: str
    extraction_decision: str
    content_role: str
    source_origin: str
    labels: tuple[dict[str, Any], ...]
    evidence: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "extraction_decision": self.extraction_decision,
            "content_role": self.content_role,
            "source_origin": self.source_origin,
            "labels": list(self.labels),
            "evidence": list(self.evidence),
        }


def classify_news_document_v8(
    document: SemanticDocument,
    *,
    issuer_resolver: NewsIssuerResolver | None = None,
) -> DeterministicNewsResultV8:
    """Classify News with teacher-discovered, human-audited candidate rules."""
    v5_labels = classify_news_document(document, issuer_resolver=issuer_resolver)
    role, role_rule = _classify_role_v8(document, v5_labels)
    origin, origin_rule = _classify_origin_v8(document, role, v5_labels)
    provider_tickers = {str(value).upper() for value in document.tickers if value}
    labels: list[dict[str, Any]] = []
    for raw in v5_labels:
        label = raw.as_dict()
        if not _retain_unit_v8(label, provider_tickers=provider_tickers):
            continue
        classification = dict(label["classification"])
        evidence_text = str(label.get("semantic_evidence_text") or "")
        direction = _direction_v8(evidence_text, classification)
        concepts = set(classification.get("event_concepts") or ())
        concepts.update(direction["concept_families"])
        classification.update({
            "content_role": role,
            "source_origin": origin,
            "event_concepts": sorted(concepts),
            "semantic_direction": direction["direction"],
            "semantic_score": direction["normalized_score"],
            "semantic_score_raw": direction["raw_score"],
            "direction_confidence": direction["confidence"],
            "deterministic_direction_evidence": direction["matched_rules"],
            "quality_flags": list(dict.fromkeys((
                *(classification.get("quality_flags") or ()),
                "deterministic_v8_rule_only",
            ))),
        })
        event_bearing = bool(classification["event_concepts"]) or _has_high_value_event(evidence_text)
        ticker = str(label.get("ticker") or "").upper()
        unit_role = str(label.get("unit_role") or "")
        strict_event = (
            role not in NON_TRIGGER_ROLES
            and unit_role not in CONTEXT_ONLY_UNIT_ROLES
            and event_bearing
            and ticker in provider_tickers
        )
        label.update({
            "classification": classification,
            "forecast_trigger_eligible": strict_event,
            "reaction_evaluation_eligible": strict_event,
            "issuer_history_context_eligible": bool(ticker),
        })
        labels.append(label)
    labels = _deduplicate_labels(labels)
    decision = _extraction_decision(document, role, labels)
    return DeterministicNewsResultV8(
        version=DETERMINISTIC_V8_VERSION,
        extraction_decision=decision,
        content_role=role,
        source_origin=origin,
        labels=tuple(labels),
        evidence=(f"role:{role_rule}", f"origin:{origin_rule}"),
    )


def _classify_role_v8(document: SemanticDocument, labels: Iterable) -> tuple[str, str]:
    role, rule = _classify_role(document, labels)
    channels = {str(value).casefold() for value in document.metadata.get("channels") or ()}
    tags = {str(value).casefold() for value in document.metadata.get("provider_tags") or ()}
    if tags & MOVER_TAGS:
        return "mover_recap", "high_precision_mover_tag"
    if tags & MARKET_UPDATE_TAGS:
        return "market_roundup", "high_precision_market_update_tag"
    if tags & PREVIEW_TAGS:
        return "preview", "automated_earnings_preview_tag"
    if tags & AUTOMATED_TAGS:
        return "automated_summary", "automated_product_tag"
    if role not in AGGREGATION_ROLES and channels & ANALYST_CHANNELS:
        return "analyst_event", "analyst_distribution_channel"
    if role in {"editorial_analysis", "primary_event"} and channels & PREVIEW_CHANNELS:
        return "preview", "preview_distribution_channel"
    return role, rule


def _classify_origin_v8(
    document: SemanticDocument,
    role: str,
    labels: Iterable,
) -> tuple[str, str]:
    tags = {str(value).casefold() for value in document.metadata.get("provider_tags") or ()}
    channels = {str(value).casefold() for value in document.metadata.get("channels") or ()}
    if tags & AUTOMATED_TAGS or tags & PREVIEW_TAGS:
        return "automated_summary", "automated_product_tag"
    if role in AGGREGATION_ROLES:
        return "editorial_aggregation", "aggregation_role"
    if role == "analyst_event" and channels & ANALYST_CHANNELS:
        return "analyst_research", "analyst_distribution_channel"
    return _classify_origin(document, role, labels)


def _retain_unit_v8(label: Mapping[str, Any], *, provider_tickers: set[str]) -> bool:
    if _retain_unit(label, provider_tickers=provider_tickers):
        return True
    ticker = str(label.get("ticker") or "").upper()
    if not ticker or ticker not in provider_tickers:
        return False
    evidence = str(label.get("semantic_evidence_text") or "").strip()
    return (
        str(label.get("unit_role") or "") == "ticker_market_observation"
        and len(evidence.split()) >= 8
        and any(re.search(pattern, evidence, re.I | re.S) for pattern in CONTEXT_EVENT_PATTERNS)
    )


def _direction_v8(text: str, classification: Mapping[str, Any]) -> dict[str, Any]:
    raw = float(classification.get("semantic_score_raw", classification.get("semantic_score") or 0.0))
    positive = max(raw, 0.0)
    negative = max(-raw, 0.0)
    matched: list[str] = []
    concepts: set[str] = set()
    for rule in (*DIRECTION_RULES, *DIRECTION_RULES_V8):
        if not any(re.search(pattern, text, re.I | re.S) for pattern in rule.patterns):
            continue
        raw += rule.weight
        positive += max(rule.weight, 0.0)
        negative += max(-rule.weight, 0.0)
        matched.append(f"{rule.rule_id}:{rule.weight:+.2f}")
        if rule.concept_family:
            concepts.add(rule.concept_family)
    if positive >= 0.35 and negative >= 0.35 and abs(positive - negative) < 1.05:
        direction = "mixed"
    elif raw >= 0.35:
        direction = "positive"
    elif raw <= -0.35:
        direction = "negative"
    else:
        direction = "neutral"
    strength = positive + negative if direction == "mixed" else abs(raw)
    return {
        "direction": direction,
        "raw_score": round(raw, 4),
        "normalized_score": round(max(-1.0, min(1.0, raw / 4.0)), 4),
        "confidence": round(min(0.99, 0.50 + min(strength, 4.0) / 8.0), 4),
        "matched_rules": matched,
        "concept_families": concepts,
    }
