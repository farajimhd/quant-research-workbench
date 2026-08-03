from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from research.text_intelligence.scoped_labeling_v1.news_identity import NewsIssuerResolver
from research.text_intelligence.scoped_labeling_v1.pipeline import classify_news_document
from research.text_intelligence.semantic_label_authority_v1.schema import SemanticDocument

from .deterministic_v6 import _direction, _deduplicate_labels
from .deterministic_v7_config import (
    DETERMINISTIC_V7_VERSION,
    HIGH_VALUE_EVENT_PATTERNS,
    ISSUER_DIRECT_CHANNELS,
    ROLE_RULES,
)


NON_TRIGGER_ROLES = {
    "analyst_event", "editorial_analysis", "automated_summary",
    "market_roundup", "mover_recap", "why_moving_followup", "preview",
}
AGGREGATION_ROLES = {"market_roundup", "mover_recap"}
ANALYST_UNIT_ROLES = {"analyst_opinion", "ticker_scoped_analyst_context"}
CONTEXT_ONLY_UNIT_ROLES = {
    "ticker_market_observation",
    "ticker_scoped_editorial_context",
    "ticker_scoped_analyst_context",
}
REPORTED_MOVE_RE = re.compile(
    r"\b(?:shares?|stock)?\s*(?:is|are|was|were)?\s*"
    r"(?:up|down|higher|lower|rose|fell|gained|lost|jumped|dropped|surged|plunged)"
    r"\s+(?:by\s+)?\d+(?:\.\d+)?\s*%",
    re.I,
)


@dataclass(frozen=True, slots=True)
class DeterministicNewsResultV7:
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


def classify_news_document_v7(
    document: SemanticDocument,
    *,
    issuer_resolver: NewsIssuerResolver | None = None,
) -> DeterministicNewsResultV7:
    """Classify News with a versioned, rule-only, issuer-scoped authority."""
    v5_labels = classify_news_document(document, issuer_resolver=issuer_resolver)
    role, role_rule = _classify_role(document, v5_labels)
    origin, origin_rule = _classify_origin(document, role, v5_labels)
    provider_tickers = {str(value).upper() for value in document.tickers if value}
    labels: list[dict[str, Any]] = []
    for raw in v5_labels:
        label = raw.as_dict()
        if not _retain_unit(label, provider_tickers=provider_tickers):
            continue
        classification = dict(label["classification"])
        evidence_text = str(label.get("semantic_evidence_text") or "")
        direction = _direction(evidence_text, classification)
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
                "deterministic_v7_rule_only",
            ))),
        })
        event_bearing = bool(classification["event_concepts"]) or _has_high_value_event(evidence_text)
        ticker = str(label.get("ticker") or "").upper()
        strict_event = (
            role not in NON_TRIGGER_ROLES
            and event_bearing
            and ticker in provider_tickers
            and bool(label.get("forecast_trigger_eligible"))
        )
        # A typed historical unit is broader than a forecast trigger.  Keeping
        # this independent repairs V6's scope/history trade-off without making
        # context-only observations actionable.
        history = bool(ticker)
        label.update({
            "classification": classification,
            "forecast_trigger_eligible": strict_event,
            "reaction_evaluation_eligible": strict_event,
            "issuer_history_context_eligible": history,
        })
        labels.append(label)
    labels = _deduplicate_labels(labels)
    decision = _extraction_decision(document, role, labels)
    return DeterministicNewsResultV7(
        version=DETERMINISTIC_V7_VERSION,
        extraction_decision=decision,
        content_role=role,
        source_origin=origin,
        labels=tuple(labels),
        evidence=(f"role:{role_rule}", f"origin:{origin_rule}"),
    )


def _classify_role(document: SemanticDocument, labels: Iterable) -> tuple[str, str]:
    title = document.title.strip()
    metadata = " ".join((
        " ".join(str(v) for v in document.metadata.get("provider_tags") or ()),
        " ".join(str(v) for v in document.metadata.get("channels") or ()),
        str(document.metadata.get("author") or ""),
    ))
    for rule in ROLE_RULES:
        search = f"{title}\n{metadata}" if rule.rule_id in {"automated_summary", "analyst_event"} else title
        if any(re.search(pattern, search, re.I | re.S) for pattern in rule.patterns):
            return rule.value, rule.rule_id
    current = _majority(
        str(label.classification.get("content_role") or "") for label in labels
    )
    return (current if current and current != "__missing__" else "editorial_analysis"), "v5_fallback"


def _classify_origin(
    document: SemanticDocument,
    role: str,
    labels: Iterable,
) -> tuple[str, str]:
    channels = {str(value).casefold() for value in document.metadata.get("channels") or ()}
    tags = {str(value).casefold() for value in document.metadata.get("provider_tags") or ()}
    title = document.title
    if role == "analyst_event":
        return "analyst_research", "analyst_role"
    if role == "automated_summary" or any(value.startswith("bzi-") for value in tags):
        return "automated_summary", "automated_structure"
    if role in AGGREGATION_ROLES:
        return "editorial_aggregation", "aggregation_role"
    if role == "regulatory_event" and re.search(
        r"\b(?:sec|fda|nasdaq|nyse|court)\b.{0,120}\b(?:filing|approv|reject|notice|notification|subpoena|order|plan approved)",
        title,
        re.I,
    ):
        return "regulatory_primary", "regulatory_primary_title"
    if channels & ISSUER_DIRECT_CHANNELS and role in {"primary_event", "regulatory_event"}:
        return "issuer_direct", "issuer_distribution_channel"
    current = _majority(
        str(label.classification.get("source_origin") or "") for label in labels
    )
    if current == "issuer_direct" and role in {"primary_event", "regulatory_event"}:
        return current, "v5_direct_source_fallback"
    return "editorial_original", "editorial_default"


def _retain_unit(
    label: Mapping[str, Any],
    *,
    provider_tickers: set[str],
) -> bool:
    ticker = str(label.get("ticker") or "").upper()
    if not ticker or ticker not in provider_tickers:
        return False
    classification = label.get("classification") or {}
    concepts = tuple(classification.get("event_concepts") or ())
    evidence = str(label.get("semantic_evidence_text") or "").strip()
    unit_role = str(label.get("unit_role") or "")
    reaction = label.get("observed_reaction") or {}
    if concepts or bool(reaction.get("direction")) or REPORTED_MOVE_RE.search(evidence):
        return True
    if unit_role not in CONTEXT_ONLY_UNIT_ROLES:
        return len(evidence.split()) >= 8
    return False


def _has_high_value_event(text: str) -> bool:
    return any(re.search(pattern, text, re.I | re.S) for pattern in HIGH_VALUE_EVENT_PATTERNS)


def _extraction_decision(
    document: SemanticDocument,
    role: str,
    labels: list[dict[str, Any]],
) -> str:
    if labels:
        return "labeled"
    if not document.text.strip():
        return "empty_semantic_text"
    if role in AGGREGATION_ROLES:
        return "non_issuer_market_content"
    if document.tickers:
        return "no_supported_event"
    # No supplied or text-asserted security is ordinary non-issuer content,
    # not an identity-resolution failure.
    return "non_issuer_market_content"


def _majority(values: Iterable[str]) -> str:
    counts = Counter(value for value in values if value)
    if not counts:
        return "__missing__"
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
