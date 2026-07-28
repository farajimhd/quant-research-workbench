from __future__ import annotations

import hashlib
from collections import Counter

from research.text_intelligence.classification_authority_v2.authority import (
    classify_document,
)
from research.text_intelligence.semantic_label_authority_v1.labeler import (
    label_document,
)
from research.text_intelligence.semantic_label_authority_v1.schema import (
    SemanticDocument,
)

from .news_extractor import analyze_news_scope
from .news_identity import NewsIssuerResolver
from .schema import RelevantTextUnit, ScopedLabel
from .sec_extractor import extract_sec_units


CONTEXT_ONLY_NEWS_ROLES = {
    "ticker_market_observation",
    "editorial_reaction_explanation",
    "ticker_scoped_editorial_context",
    "ticker_scoped_analyst_context",
}

M_AND_A_CONCEPTS = {
    "ma_transaction.acquisition",
    "ma_transaction.merger_agreement",
}


def classify_news_document(
    document: SemanticDocument,
    *,
    issuer_resolver: NewsIssuerResolver | None = None,
) -> tuple[ScopedLabel, ...]:
    analysis = analyze_news_scope(
        source_id=document.source_id,
        title=document.title,
        text=document.text,
        tickers=document.tickers,
        timestamp=document.timestamp,
        issuer_resolver=issuer_resolver,
        metadata=document.metadata,
    )
    labels: list[ScopedLabel] = []
    for unit in analysis.units:
        for ticker in unit.tickers:
            labels.append(
                _label_unit(
                    document,
                    unit,
                    ticker=ticker,
                )
            )
    return tuple(labels)


def classify_sec_document(document: SemanticDocument) -> tuple[ScopedLabel, ...]:
    ticker = document.tickers[0] if document.tickers else ""
    units = extract_sec_units(
        source_id=document.source_id,
        title=document.title,
        text=document.text,
        ticker=ticker,
        metadata=document.metadata,
    )
    return tuple(_label_unit(document, unit, ticker=ticker) for unit in units)


def summarize_scoped_labels(labels: tuple[ScopedLabel, ...]) -> dict:
    return {
        "units": len(labels),
        "tickers": len({label.ticker for label in labels if label.ticker}),
        "roles": dict(sorted(Counter(label.unit_role for label in labels).items())),
        "event_concepts": dict(sorted(Counter(
            concept
            for label in labels
            for concept in label.classification["event_concepts"]
        ).items())),
        "forecast_trigger_eligible": sum(
            label.forecast_trigger_eligible for label in labels
        ),
        "reaction_evaluation_eligible": sum(
            label.reaction_evaluation_eligible for label in labels
        ),
    }


def _label_unit(
    parent: SemanticDocument,
    unit: RelevantTextUnit,
    *,
    ticker: str,
) -> ScopedLabel:
    metadata = dict(parent.metadata)
    metadata.update(
        {
            "parent_source_id": parent.source_id,
            "unit_id": unit.unit_id,
            "unit_role": unit.role,
            "document_role": unit.document_role
                or metadata.get("document_role", ""),
        }
    )
    scoped = SemanticDocument(
        corpus=parent.corpus,
        source_id=unit.unit_id,
        timestamp=parent.timestamp,
        title=unit.heading or parent.title,
        text=unit.semantic_text,
        entity_terms=parent.entity_terms,
        tickers=(ticker,) if ticker else (),
        metadata=metadata,
    )
    semantic = label_document(scoped, include_discovery_evidence=True)
    classification = classify_document(scoped, semantic_result=semantic)
    classification_dict = classification.as_dict()
    if parent.corpus == "news":
        _apply_news_direction_context(
            classification_dict,
            semantic=semantic,
            unit=unit,
        )
    classification_dict["quality_flags"] = tuple(dict.fromkeys((
        *classification_dict["quality_flags"],
        *unit.quality_flags,
    )))
    automated_context = (
        parent.corpus == "news"
        and classification_dict["content_role"] == "automated_summary"
    )
    context_only = (
        parent.corpus == "news"
        and (
            unit.role in CONTEXT_ONLY_NEWS_ROLES
            or automated_context
        )
    )
    effective_unit_role = (
        "ticker_scoped_editorial_context"
        if automated_context
        else unit.role
    )
    if parent.corpus == "sec":
        exact_ticker = bool(ticker)
        role = str(parent.metadata.get("document_role") or "")
        document_type = str(
            parent.metadata.get("document_type") or ""
        ).upper()
        form_type = str(parent.metadata.get("form_type") or "").upper()
        ownership_form = form_type in {"3", "4", "5"} or document_type in {
            "3", "4", "5",
        }
        if ownership_form:
            classification_dict.update(
                {
                    "content_role": "ownership_transaction",
                    "event_concepts": tuple(
                        concept
                        for concept in classification_dict["event_concepts"]
                        if concept.startswith("ownership.")
                    ),
                    "semantic_direction": "neutral",
                    "semantic_score": 0.0,
                }
            )
        event_bearing_role = role in {
            "primary_document",
            "press_release_exhibit",
            "material_exhibit",
        }
        sec_eligible = (
            exact_ticker
            and event_bearing_role
            and not ownership_form
            and document_type not in {"XML", "XBRL"}
            and bool(classification_dict["event_concepts"])
        )
        classification_dict.update(
            {
                "forecast_trigger_eligible": sec_eligible,
                "reaction_evaluation_eligible": sec_eligible,
                "prior_primary_context_eligible": sec_eligible,
            }
        )
    if context_only:
        classification_dict.update(
            {
                "forecast_trigger_eligible": False,
                "reaction_evaluation_eligible": False,
                "prior_primary_context_eligible": False,
                "episode_followup_eligible": True,
            }
        )
    if parent.corpus == "news":
        event_concepts = tuple(classification_dict["event_concepts"])
        event_eligible = (
            unit.trigger_candidate
            and bool(event_concepts)
            and unit.role not in CONTEXT_ONLY_NEWS_ROLES
            and classification_dict["content_role"] not in {
                "market_roundup",
                "mover_recap",
                "why_moving_followup",
                "automated_market_statistics",
                "automated_summary",
            }
        )
        classification_dict.update(
            {
                "forecast_trigger_eligible": event_eligible,
                "reaction_evaluation_eligible": event_eligible,
                "prior_primary_context_eligible": event_eligible,
                "episode_followup_eligible": (
                    bool(classification_dict["episode_followup_eligible"])
                    or not event_eligible
                ),
                "quality_flags": tuple(dict.fromkeys((
                    *classification_dict["quality_flags"],
                    "event_scoped_eligibility_v4",
                ))),
            }
        )
    return ScopedLabel(
        corpus=parent.corpus,
        source_id=parent.source_id,
        unit_id=unit.unit_id,
        ticker=ticker,
        unit_role=effective_unit_role,
        event_id=unit.event_id,
        event_tickers=unit.event_tickers,
        issuer_role=unit.issuer_role,
        evidence_scope=unit.evidence_scope,
        publication_text_hash=hashlib.sha256(
            unit.text.encode("utf-8")
        ).hexdigest(),
        semantic_evidence_text=unit.semantic_text,
        classification=classification_dict,
        semantic=semantic.as_dict(),
        observed_reaction=unit.observed_reaction,
        reported_catalyst=unit.reported_catalyst,
        forecast_trigger_eligible=bool(
            classification_dict["forecast_trigger_eligible"]
        ),
        reaction_evaluation_eligible=bool(
            classification_dict["reaction_evaluation_eligible"]
        ),
        issuer_history_context_eligible=(
            context_only
            or bool(classification_dict["prior_primary_context_eligible"])
            or bool(classification_dict["episode_followup_eligible"])
        ),
    )


def _apply_news_direction_context(
    classification: dict,
    *,
    semantic,
    unit: RelevantTextUnit,
) -> None:
    """Synthesize issuer direction after issuer role is known.

    The lower semantic authority intentionally labels exact language without
    assuming which participant it affects. This scoped layer is the first
    authority that knows whether the issuer is an acquisition target,
    acquirer, or another participant. A signed acquisition is generally
    favorable language, with a stronger target-side prior, but explicit
    issuer-specific negatives remain visible and can produce a mixed or
    negative final result.
    """
    concepts = set(classification["event_concepts"])
    base_score = float(classification["semantic_score"])
    adjustment = 0.0
    basis = [
        f"{label.family}.{label.subtype}:{label.direction}"
        for label in semantic.labels
    ]
    directions = {
        label.direction
        for label in semantic.labels
        if label.direction in {"positive", "negative"}
    }
    if concepts & M_AND_A_CONCEPTS:
        basis.append(f"issuer_role:{unit.issuer_role or 'participant'}")
        if unit.issuer_role == "target":
            # A signed transaction normally embeds a target premium or
            # liquidity outcome. This contextual prior is deliberately not
            # applied to generic documents where the issuer role is unknown.
            adjustment += 0.8
            directions.add("positive")
            basis.append("target_transaction_prior:+0.8")
        elif unit.issuer_role == "acquirer":
            # The generic positive M&A concept is retained, but acquirers get
            # no extra premium: financing, dilution, integration, or margin
            # evidence must be allowed to offset the transaction.
            basis.append("acquirer_transaction_prior:+0.0")
        else:
            adjustment += 0.2
            directions.add("positive")
            basis.append("participant_transaction_prior:+0.2")
    score = round(base_score + adjustment, 4)
    direction = _direction_from_components(score, directions)
    classification.update(
        {
            "semantic_direction": direction,
            "semantic_score": score,
            "semantic_score_base": round(base_score, 4),
            "semantic_score_adjustment": round(adjustment, 4),
            "semantic_direction_basis": tuple(basis),
            "quality_flags": tuple(dict.fromkeys((
                *classification["quality_flags"],
                "issuer_context_direction_v4",
            ))),
        }
    )


def _direction_from_components(
    score: float,
    directions: set[str],
) -> str:
    if {"positive", "negative"} <= directions and abs(score) < 0.75:
        return "mixed"
    if score >= 0.5:
        return "positive"
    if score <= -0.5:
        return "negative"
    return "neutral"
