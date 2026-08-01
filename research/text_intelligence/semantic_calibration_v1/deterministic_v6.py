from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from research.text_intelligence.scoped_labeling_v1.news_identity import (
    NewsIssuerResolver,
)
from research.text_intelligence.scoped_labeling_v1.pipeline import (
    classify_news_document,
)
from research.text_intelligence.semantic_label_authority_v1.schema import (
    SemanticDocument,
)

from .deterministic_v6_config import (
    DETERMINISTIC_V6_VERSION,
    DIRECTION_RULES,
    MIXED_COMPONENT_THRESHOLD,
    MIXED_DOMINANCE_MARGIN,
    NEGATIVE_THRESHOLD,
    ORIGIN_AUTOMATED_PATTERNS,
    ORIGIN_ISSUER_PATTERNS,
    ORIGIN_REGULATORY_PATTERNS,
    POSITIVE_THRESHOLD,
    ROLE_RULES,
)


CONTEXT_ONLY_ROLES = {
    "ticker_market_observation",
    "ticker_scoped_editorial_context",
    "ticker_scoped_analyst_context",
}
NON_TRIGGER_ROLES = {
    "analyst_event",
    "editorial_analysis",
    "automated_summary",
    "market_roundup",
    "mover_recap",
    "why_moving_followup",
    "preview",
}
REPORTED_MOVE_RE = re.compile(
    r"\b(?:shares?|stock)?\s*(?:is|are|was|were)?\s*"
    r"(?:up|down|higher|lower|rose|fell|gained|lost|jumped|dropped|"
    r"surged|plunged)\s+(?:by\s+)?\d+(?:\.\d+)?\s*%",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class DeterministicNewsResult:
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


def classify_news_document_v6(
    document: SemanticDocument,
    *,
    issuer_resolver: NewsIssuerResolver | None = None,
) -> DeterministicNewsResult:
    """Apply the rule-only V6 authority without human labels or learned state."""
    v5_labels = classify_news_document(
        document,
        issuer_resolver=issuer_resolver,
    )
    article_text = _article_text(document)
    role, role_rule = _classify_role(article_text, v5_labels)
    origin, origin_rule = _classify_origin(document, article_text, role, v5_labels)
    labels: list[dict[str, Any]] = []
    for raw in v5_labels:
        label = raw.as_dict()
        if not _meaningful_unit(label):
            continue
        classification = dict(label["classification"])
        evidence_text = str(label.get("semantic_evidence_text") or "")
        direction = _direction(evidence_text, classification)
        concepts = set(classification.get("event_concepts") or ())
        concepts.update(direction["concept_families"])
        classification.update(
            {
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
                    "deterministic_v6_rule_only",
                ))),
            }
        )
        event_bearing = bool(classification["event_concepts"])
        trigger = (
            role not in NON_TRIGGER_ROLES
            and event_bearing
            and bool(label.get("ticker"))
        )
        label.update(
            {
                "classification": classification,
                "forecast_trigger_eligible": trigger,
                "reaction_evaluation_eligible": trigger,
                "issuer_history_context_eligible": True,
            }
        )
        labels.append(label)
    labels = _deduplicate_labels(labels)
    decision = _extraction_decision(document, role, labels)
    return DeterministicNewsResult(
        version=DETERMINISTIC_V6_VERSION,
        extraction_decision=decision,
        content_role=role,
        source_origin=origin,
        labels=tuple(labels),
        evidence=(f"role:{role_rule}", f"origin:{origin_rule}"),
    )


def _article_text(document: SemanticDocument) -> str:
    metadata = document.metadata
    return "\n".join(
        (
            document.title,
            str(metadata.get("teaser") or ""),
            " ".join(str(value) for value in metadata.get("provider_tags") or ()),
            " ".join(str(value) for value in metadata.get("channels") or ()),
            str(metadata.get("author") or ""),
            document.text,
        )
    )


def _classify_role(text: str, labels: Iterable) -> tuple[str, str]:
    title = text.splitlines()[0] if text else ""
    for rule in ROLE_RULES:
        # Article type is primarily a headline/metadata property. Searching an
        # entire roundup for words such as "analyst" or "FDA" makes one inner
        # passage incorrectly redefine the whole publication.
        search_text = text[:2500] if rule.rule_id == "automated_summary" else title
        if any(re.search(pattern, search_text, re.IGNORECASE | re.DOTALL) for pattern in rule.patterns):
            return rule.value, rule.rule_id
    current = _majority(
        str(label.classification.get("content_role") or "")
        for label in labels
    )
    return (current if current and current != "__missing__" else "editorial_analysis"), "v5_fallback"


def _classify_origin(
    document: SemanticDocument,
    text: str,
    role: str,
    labels: Iterable,
) -> tuple[str, str]:
    metadata_text = " ".join(
        (
            str(document.metadata.get("author") or ""),
            " ".join(str(value) for value in document.metadata.get("provider_tags") or ()),
            " ".join(str(value) for value in document.metadata.get("channels") or ()),
            str(document.metadata.get("url_domain") or ""),
        )
    )
    combined = f"{metadata_text}\n{text[:2000]}"
    if role == "analyst_event":
        return "analyst_research", "analyst_role"
    if role == "automated_summary" or _matches_any(combined, ORIGIN_AUTOMATED_PATTERNS):
        return "automated_summary", "automated_structure"
    if role in {"market_roundup", "mover_recap"}:
        return "editorial_aggregation", "aggregation_role"
    if _matches_any(combined, ORIGIN_REGULATORY_PATTERNS):
        return "regulatory_primary", "regulatory_source"
    if _matches_any(metadata_text, ORIGIN_ISSUER_PATTERNS):
        return "issuer_direct", "issuer_distribution_channel"
    current = _majority(
        str(label.classification.get("source_origin") or "")
        for label in labels
    )
    if current in {"issuer_direct", "regulatory_primary"}:
        return current, "v5_direct_source_fallback"
    if role in {"why_moving_followup", "preview", "editorial_analysis"}:
        return "editorial_original", "editorial_role"
    return (current if current and current != "__missing__" else "editorial_original"), "v5_fallback"


def _meaningful_unit(label: Mapping[str, Any]) -> bool:
    classification = label.get("classification") or {}
    concepts = tuple(classification.get("event_concepts") or ())
    reaction = label.get("observed_reaction") or {}
    has_reaction = bool(reaction.get("direction"))
    evidence = str(label.get("semantic_evidence_text") or "").strip()
    if concepts or has_reaction or REPORTED_MOVE_RE.search(evidence):
        return True
    if str(label.get("unit_role") or "") not in CONTEXT_ONLY_ROLES:
        return len(evidence.split()) >= 8
    return False


def _direction(text: str, classification: Mapping[str, Any]) -> dict[str, Any]:
    raw = float(classification.get("semantic_score_raw", classification.get("semantic_score") or 0.0))
    positive = max(raw, 0.0)
    negative = max(-raw, 0.0)
    matched: list[str] = []
    concepts: set[str] = set()
    for rule in DIRECTION_RULES:
        if not _matches_any(text, rule.patterns):
            continue
        raw += rule.weight
        positive += max(rule.weight, 0.0)
        negative += max(-rule.weight, 0.0)
        matched.append(f"{rule.rule_id}:{rule.weight:+.2f}")
        if rule.concept_family:
            concepts.add(rule.concept_family)
    if (
        positive >= MIXED_COMPONENT_THRESHOLD
        and negative >= MIXED_COMPONENT_THRESHOLD
        and abs(positive - negative) < MIXED_DOMINANCE_MARGIN
    ):
        direction = "mixed"
    elif raw >= POSITIVE_THRESHOLD:
        direction = "positive"
    elif raw <= NEGATIVE_THRESHOLD:
        direction = "negative"
    else:
        direction = "neutral"
    strength = positive + negative if direction == "mixed" else abs(raw)
    confidence = round(min(0.99, 0.50 + min(strength, 4.0) / 8.0), 4)
    return {
        "direction": direction,
        "raw_score": round(raw, 4),
        "normalized_score": round(max(-1.0, min(1.0, raw / 4.0)), 4),
        "confidence": confidence,
        "matched_rules": matched,
        "concept_families": concepts,
    }


def _deduplicate_labels(labels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: dict[tuple[str, str], dict[str, Any]] = {}
    for label in labels:
        ticker = str(label.get("ticker") or "").upper()
        evidence = str(label.get("semantic_evidence_text") or "")
        key = (ticker, evidence)
        selected.setdefault(key, label)
    return list(selected.values())


def _extraction_decision(
    document: SemanticDocument,
    role: str,
    labels: list[dict[str, Any]],
) -> str:
    if labels:
        return "labeled"
    if not document.text.strip():
        return "empty_semantic_text"
    if role in {"market_roundup", "mover_recap"}:
        return "non_issuer_market_content"
    if document.tickers:
        return "no_supported_event"
    return "identity_not_found"


def _matches_any(text: str, patterns: Iterable[str]) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE | re.DOTALL) for pattern in patterns)


def _majority(values: Iterable[str]) -> str:
    counts = Counter(value for value in values if value)
    if not counts:
        return "__missing__"
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
