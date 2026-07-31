from __future__ import annotations

from typing import Any, Mapping

from .schema import ANNOTATION_VERSION


def annotation_template(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "annotation_version": ANNOTATION_VERSION,
        "sample_id": item["sample_id"],
        "source_id": item["source_id"],
        "source_timestamp": item["source_timestamp"],
        "source_text_sha256": item["source_text_sha256"],
        "review_round": 1,
        "reviewer": "codex_primary",
        "extraction_decision": "labeled",
        "content_role": "primary_event",
        "source_origin": "editorial_original",
        "issuer_units": [
            {
                "ticker": "",
                "issuer_role": "primary_subject",
                "evidence_scope": "ticker_specific",
                "event_concepts": [],
                "evidence_quotes": [],
                "evidence_spans": [],
                "modality": "confirmed",
                "time_orientation": "current",
                "positive_evidence_level": 0,
                "negative_evidence_level": 0,
                "semantic_direction": "neutral",
                "forecast_trigger_eligible": False,
                "reaction_evaluation_eligible": False,
                "issuer_history_context_eligible": True,
                "analyst_context_eligible": False,
                "analyst_evaluation_eligible": False,
                "analyst_opinions": [],
                "eligibility_reason": "",
                "annotation_confidence": 0,
                "ambiguity_notes": "",
                "semantic_rationale": "",
            }
        ],
        "reviewer_confidence": 0,
        "review_notes": "",
        "taxonomy_proposals": [],
    }
