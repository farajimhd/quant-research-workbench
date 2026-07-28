from __future__ import annotations

from typing import Any

from research.text_intelligence.semantic_label_authority_v1.labeler import (
    label_document,
)
from research.text_intelligence.semantic_label_authority_v1.schema import (
    SemanticDocument,
    SemanticResult,
)
from src.backend.news_classification import classify_news

from .schema import CLASSIFICATION_AUTHORITY_VERSION, ClassificationResult


AGGREGATION_ROLES = {
    "market_roundup",
    "mover_recap",
    "why_moving_followup",
}
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
    ticker_count = len(document.tickers)
    row = dict(document.metadata)
    row.setdefault("title", document.title)
    row["text"] = semantic.normalized_semantic_text
    legacy = classify_news(row, ticker_count)

    source_origin = _news_origin(legacy.origin, semantic.origin)
    content_role = _news_role(legacy.format, semantic.content_role)
    if source_origin == "automated_summary":
        content_role = "automated_summary"
    issuer_relationship = _news_relationship(
        source_origin=source_origin,
        content_role=content_role,
        scope=legacy.scope,
        has_events=bool(semantic.labels),
    )
    ticker_scoped_semantics_required = content_role in AGGREGATION_ROLES
    promoted_labels = (
        () if ticker_scoped_semantics_required else semantic.labels
    )
    event_concepts = tuple(
        f"{label.family}.{label.subtype}" for label in promoted_labels
    )
    conflicts: list[str] = []
    if legacy.is_company_news and source_origin not in {
        "issuer_direct",
        "regulatory_primary",
    }:
        conflicts.append("legacy_company_without_direct_source_origin")
    if content_role in AGGREGATION_ROLES and legacy.is_company_news:
        conflicts.append("aggregation_role_conflicts_with_company_label")
    if source_origin == "issuer_direct" and issuer_relationship != "direct_announcement":
        conflicts.append("issuer_origin_relationship_conflict")
    if ticker_scoped_semantics_required:
        conflicts.append("ticker_scoped_semantics_required")

    forecast = (
        content_role in PRIMARY_ROLES
        and issuer_relationship
        in {
            "direct_announcement",
            "direct_regulatory_disclosure",
            "reported_issuer_event",
            "analyst_opinion",
        }
        and bool(event_concepts)
    )
    prior_primary = (
        content_role in PRIMARY_ROLES
        and issuer_relationship
        in {
            "direct_announcement",
            "direct_regulatory_disclosure",
            "reported_issuer_event",
        }
        and bool(event_concepts)
    )
    followup = content_role in AGGREGATION_ROLES or issuer_relationship in {
        "market_reaction_story",
        "third_party_multi_issuer",
    }
    confidence = _combined_confidence(
        legacy.confidence,
        promoted_labels,
        conflicts,
    )
    evidence = tuple(
        dict.fromkeys(
            (
                *legacy.evidence,
                f"semantic role: {semantic.content_role}",
                f"semantic origin: {semantic.origin}",
                *(
                    ("document-wide events withheld pending ticker-scoped extraction",)
                    if ticker_scoped_semantics_required
                    else tuple(f"event: {value}" for value in event_concepts)
                ),
            )
        )
    )
    semantic_direction = (
        "neutral"
        if ticker_scoped_semantics_required
        else semantic.sentiment
    )
    semantic_score = (
        0.0
        if ticker_scoped_semantics_required
        else semantic.sentiment_score
    )
    return ClassificationResult(
        authority_version=CLASSIFICATION_AUTHORITY_VERSION,
        corpus=document.corpus,
        source_id=document.source_id,
        source_type=legacy.format,
        source_subtype=legacy.kind,
        source_origin=source_origin,
        content_role=content_role,
        issuer_relationship=issuer_relationship,
        scope=legacy.scope,
        event_concepts=event_concepts,
        semantic_direction=semantic_direction,
        semantic_score=semantic_score,
        modality=semantic.modality,
        time_orientation=semantic.time_orientation,
        forecast_trigger_eligible=forecast,
        reaction_evaluation_eligible=forecast,
        prior_primary_context_eligible=prior_primary,
        episode_followup_eligible=followup,
        confidence=confidence,
        evidence=evidence,
        quality_flags=tuple(
            dict.fromkeys((*semantic.quality_flags, *conflicts))
        ),
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


def _news_origin(legacy_origin: str, semantic_origin: str) -> str:
    if legacy_origin == "issuer":
        return "issuer_direct"
    if legacy_origin == "regulatory":
        return "regulatory_primary"
    if legacy_origin == "analyst":
        return "analyst_research"
    if semantic_origin == "analyst_research":
        # Exact analyst language in the issuer-scoped text is stronger
        # evidence than an incomplete provider author/channel classification.
        return "analyst_research"
    if legacy_origin == "automated" or semantic_origin == "automated_summary":
        return "automated_summary"
    if semantic_origin == "editorial_aggregation":
        return "editorial_aggregation"
    return "editorial_original" if legacy_origin == "editorial" else "unknown"


def _news_role(legacy_format: str, semantic_role: str) -> str:
    explicit = {
        "why_moving": "why_moving_followup",
        "analyst_action": "analyst_event",
        "regulatory_filing": "regulatory_event",
        "trading_halt": "regulatory_event",
        "macro_release": "regulatory_event",
        "company_announcement": "primary_event",
        "earnings_flash": "primary_event",
        "ai_generated": "automated_summary",
        "insights": "automated_summary",
    }.get(legacy_format)
    if semantic_role in AGGREGATION_ROLES:
        return semantic_role
    return explicit or semantic_role


def _news_relationship(
    *,
    source_origin: str,
    content_role: str,
    scope: str,
    has_events: bool,
) -> str:
    if source_origin == "issuer_direct":
        return "direct_announcement"
    if source_origin == "regulatory_primary":
        return "direct_regulatory_disclosure"
    if source_origin == "analyst_research":
        return "analyst_opinion"
    if content_role in AGGREGATION_ROLES:
        return "market_reaction_story"
    if scope == "multi_ticker":
        return "third_party_multi_issuer"
    if scope == "market_wide":
        return "sector_macro_context"
    if has_events:
        return "reported_issuer_event"
    return "unrelated_or_ambiguous"


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
