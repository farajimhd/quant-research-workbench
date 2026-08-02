from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from research.text_intelligence.scoped_labeling_v1.news_identity import NewsIssuerResolver
from research.text_intelligence.semantic_label_authority_v1.schema import SemanticDocument

from .deterministic_v6 import _deduplicate_labels
from .deterministic_v6_config import DIRECTION_RULES
from .deterministic_v7 import _extraction_decision
from .deterministic_v8 import classify_news_document_v8
from .deterministic_v8_config import DIRECTION_RULES_V8
from .deterministic_v9_config import (
    ARTICLE_ROLE_OVERRIDES,
    CALIBRATION_VERSION,
    DENIED_UNIT_ROLES,
    DETERMINISTIC_V9_VERSION,
    DIRECTION_BASE_SCALE,
    DIRECTION_RULE_WEIGHTS,
    ELIGIBILITY_FALSE_KEYS,
    ELIGIBILITY_TRUE_KEYS,
    MIXED_COMPONENT_THRESHOLD,
    MIXED_DOMINANCE_MARGIN,
    NEGATIVE_THRESHOLD,
    POSITIVE_THRESHOLD,
    SINGLE_TICKER_CONCEPT_ADDITIONS,
    SOURCE_ORIGIN_OVERRIDES,
)
from .deterministic_v9_signals import article_signals_from_parts


_DEFAULT_DIRECTION_WEIGHTS = {
    rule.rule_id: float(rule.weight) for rule in (*DIRECTION_RULES, *DIRECTION_RULES_V8)
}


@dataclass(frozen=True, slots=True)
class DeterministicNewsResultV9:
    version: str
    calibration_version: str
    extraction_decision: str
    content_role: str
    source_origin: str
    labels: tuple[dict[str, Any], ...]
    evidence: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "calibration_version": self.calibration_version,
            "extraction_decision": self.extraction_decision,
            "content_role": self.content_role,
            "source_origin": self.source_origin,
            "labels": list(self.labels),
            "evidence": list(self.evidence),
        }


def classify_news_document_v9(
    document: SemanticDocument,
    *,
    issuer_resolver: NewsIssuerResolver | None = None,
) -> DeterministicNewsResultV9:
    """Apply the frozen, transparent V9 calibration over deterministic V8."""
    v8 = classify_news_document_v8(document, issuer_resolver=issuer_resolver)
    signals = article_signals_from_parts(
        title=document.title,
        provider_tickers=document.tickers,
        provider_tags=document.metadata.get("provider_tags") or (),
        channels=document.metadata.get("channels") or (),
        evidence=v8.evidence,
    )
    role, role_signal = _override(v8.content_role, signals, ARTICLE_ROLE_OVERRIDES)
    origin, origin_signal = _override(v8.source_origin, signals, SOURCE_ORIGIN_OVERRIDES)
    concept_additions = set()
    if len(tuple(value for value in document.tickers if value)) == 1:
        for signal in signals:
            concept_additions.update(SINGLE_TICKER_CONCEPT_ADDITIONS.get(signal, ()))

    labels: list[dict[str, Any]] = []
    for source in v8.labels:
        if str(source.get("unit_role") or "") in DENIED_UNIT_ROLES:
            continue
        label = dict(source)
        classification = dict(label.get("classification") or {})
        direction = _recalibrate_direction(classification)
        concepts = set(classification.get("event_concepts") or ())
        concepts.update(concept_additions)
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
                "deterministic_v9_teacher_calibrated_rule_only",
            ))),
        })
        has_event = bool(concepts)
        key = "|".join((
            role,
            origin,
            str(label.get("unit_role") or "__missing__"),
            str(int(has_event)),
        ))
        eligible = bool(label.get("forecast_trigger_eligible"))
        if key in ELIGIBILITY_TRUE_KEYS:
            eligible = True
        elif key in ELIGIBILITY_FALSE_KEYS:
            eligible = False
        label.update({
            "classification": classification,
            "forecast_trigger_eligible": eligible,
            "reaction_evaluation_eligible": eligible,
        })
        labels.append(label)
    labels = _deduplicate_labels(labels)
    decision = _extraction_decision(document, role, labels)
    return DeterministicNewsResultV9(
        version=DETERMINISTIC_V9_VERSION,
        calibration_version=CALIBRATION_VERSION,
        extraction_decision=decision,
        content_role=role,
        source_origin=origin,
        labels=tuple(labels),
        evidence=tuple(filter(None, (
            *v8.evidence,
            f"v9_role:{role_signal}" if role_signal else "",
            f"v9_origin:{origin_signal}" if origin_signal else "",
        ))),
    )


def _override(current: str, signals: tuple[str, ...], table: dict[str, str]) -> tuple[str, str]:
    for signal, value in table.items():
        if signal in signals:
            return value, signal
    return current, ""


def _recalibrate_direction(classification: dict[str, Any]) -> dict[str, Any]:
    matched_values = list(classification.get("deterministic_direction_evidence") or ())
    matched_ids = [str(value).split(":", 1)[0] for value in matched_values]
    v8_added = sum(_DEFAULT_DIRECTION_WEIGHTS.get(rule_id, 0.0) for rule_id in matched_ids)
    base = float(classification.get("semantic_score_raw") or 0.0) - v8_added
    raw = DIRECTION_BASE_SCALE * base
    positive = max(raw, 0.0)
    negative = max(-raw, 0.0)
    evidence = []
    for rule_id in matched_ids:
        weight = DIRECTION_RULE_WEIGHTS.get(rule_id, _DEFAULT_DIRECTION_WEIGHTS.get(rule_id, 0.0))
        raw += weight
        positive += max(weight, 0.0)
        negative += max(-weight, 0.0)
        evidence.append(f"{rule_id}:{weight:+.2f}")
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
    return {
        "direction": direction,
        "raw_score": round(raw, 4),
        "normalized_score": round(max(-1.0, min(1.0, raw / 4.0)), 4),
        "confidence": round(min(0.99, 0.50 + min(strength, 4.0) / 8.0), 4),
        "matched_rules": evidence,
    }
