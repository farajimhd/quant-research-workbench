from __future__ import annotations

from typing import Any

from research.text_intelligence.semantic_label_authority_v1.labeler import (
    label_document,
)
from research.text_intelligence.semantic_label_authority_v1.schema import (
    SemanticDocument,
    SemanticResult,
)
from .schema import CLASSIFICATION_AUTHORITY_VERSION, ClassificationResult


PRIMARY_ROLES = {
    "primary_event",
    "regulatory_event",
    "regulatory_primary",
    "analyst_event",
}


def classify_document(
    document: SemanticDocument,
    *,
    semantic_result: SemanticResult | None = None,
) -> ClassificationResult:
    """Classify one rendered document without conflating source and meaning."""
    semantic = semantic_result or label_document(
        document,
        include_discovery_evidence=False,
    )
    if document.corpus == "news":
        return _classify_news(document, semantic)
    if document.corpus == "sec":
        return _classify_sec(document, semantic)
    raise ValueError(f"unsupported corpus {document.corpus!r}")


def _classify_news(document: SemanticDocument, semantic) -> ClassificationResult:
    raise RuntimeError(
        "The classification_authority_v2 News path is retired; use "
        "research.text_intelligence.news_synthesis_v1.NewsSynthesisEngine"
    )


def _classify_sec(document: SemanticDocument, semantic) -> ClassificationResult:
    metadata = document.metadata
    form_type = str(metadata.get("form_type") or "").strip().upper()
    document_type = str(metadata.get("document_type") or "").strip().upper()
    text_kind = str(metadata.get("text_kind") or "").strip()
    document_role = str(metadata.get("document_role") or "").strip()
    event_concepts = tuple(
        f"{label.family}.{label.subtype}" for label in semantic.labels
    )
    content_role = semantic.content_role
    source_subtype = " / ".join(
        value for value in (document_type, text_kind, document_role) if value
    )
    exact_acceptance = bool(
        metadata.get("accepted_at_utc")
        or metadata.get("source_timestamp")
        or document.timestamp
    )
    forecast = (
        exact_acceptance
        and content_role in PRIMARY_ROLES
        and bool(event_concepts)
        and document_role not in {"administrative", "cover"}
    )
    evidence = (
        f"SEC form: {form_type or 'unknown'}",
        f"document type: {document_type or 'unknown'}",
        f"renderer text kind: {text_kind or 'unknown'}",
        f"semantic role: {semantic.content_role}",
        *(f"event: {value}" for value in event_concepts),
    )
    confidence = _combined_confidence(
        0.99 if form_type and document_type else 0.90,
        semantic.labels,
        (),
    )
    return ClassificationResult(
        authority_version=CLASSIFICATION_AUTHORITY_VERSION,
        corpus=document.corpus,
        source_id=document.source_id,
        source_type=form_type or "unknown_sec_form",
        source_subtype=source_subtype or "unknown_sec_document",
        source_origin="regulatory_primary",
        content_role=content_role,
        issuer_relationship="direct_regulatory_disclosure",
        scope="single_issuer",
        event_concepts=event_concepts,
        semantic_direction=semantic.sentiment,
        semantic_score=semantic.sentiment_score,
        modality=semantic.modality,
        time_orientation=semantic.time_orientation,
        forecast_trigger_eligible=forecast,
        reaction_evaluation_eligible=forecast,
        prior_primary_context_eligible=forecast,
        episode_followup_eligible=False,
        confidence=confidence,
        evidence=tuple(evidence),
        quality_flags=semantic.quality_flags,
    )


def _combined_confidence(
    source_confidence: float,
    labels: tuple[Any, ...],
    conflicts: tuple[str, ...] | list[str],
) -> float:
    semantic_confidence = (
        sum(float(label.confidence) for label in labels) / len(labels)
        if labels
        else 0.60
    )
    value = 0.58 * float(source_confidence) + 0.42 * semantic_confidence
    value -= 0.12 * len(conflicts)
    return round(min(0.99, max(0.0, value)), 4)
