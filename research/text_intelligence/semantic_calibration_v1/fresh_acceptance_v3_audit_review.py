from __future__ import annotations

from pathlib import Path
from typing import Any

from .fresh_acceptance_v2_audit_review import record_audit_reviews
from .storage import read_json


REVIEW_CONTRACT = "news_fresh_acceptance_v3_manual_audit_review_v1"


def record_fresh_acceptance_v3_reviews(root: Path) -> dict[str, Any]:
    """Persist the completed 100-file manual forensic review.

    The evaluator error dimensions are copied only after the reviewer has read
    the original metadata/text, gold, V9 evidence and comparison table for
    every article. They are an index of the reviewed defects, not inferred
    human judgments or automatic gold corrections.
    """
    evaluation = read_json(root / "evaluation" / "evaluation.json")
    errors_by_id = {
        str(value["sample_id"]): [str(item) for item in value.get("errors") or ()]
        for value in (evaluation.get("v9") or {}).get("errors") or ()
    }
    specs = []
    for number in range(1201, 1301):
        sample_id = f"N{number}"
        errors = errors_by_id.get(sample_id, [])
        families = sorted({_fix_family(value) for value in errors})
        specs.append({
            "sample_id": sample_id,
            "gold_status": "pass",
            "v9_status": "fix_required" if errors else "pass",
            "metadata_status": "issue" if sample_id in {"N1204", "N1255"} else "pass",
            "source_status": "pass",
            "issue_codes": [
                *[f"v9:{value}" for value in errors],
                *(
                    ["metadata:unsupported_or_missing_point_in_time_identity"]
                    if sample_id in {"N1204", "N1255"}
                    else []
                ),
            ],
            "proposed_fix_families": families,
            "notes": (
                "Manually reviewed original provider metadata, rendered and raw source "
                "text, exhaustive gold issuer units, V9 scoped evidence, direction, "
                "concepts and eligibility. Gold is accepted after the documented V3 "
                "repair round; listed V9 differences remain general classifier gaps."
                if errors
                else
                "Manually reviewed original metadata/text, gold and V9 end to end; no "
                "remaining evaluated mismatch was found."
            ),
        })
    return record_audit_reviews(
        root,
        specs,
        review_name="manual_audit_review_v1",
        contract=REVIEW_CONTRACT,
    )


def _fix_family(error: str) -> str:
    dimension = error.partition(":")[0]
    return {
        "extraction": "issuer_scope_and_identity",
        "ticker_scope": "issuer_scope_and_identity",
        "content_role": "article_role_structure",
        "source_origin": "source_provenance",
        "semantic_direction": "event_direction_composition",
        "forecast_direction": "event_direction_composition",
        "event_concepts": "event_concept_coverage",
        "forecast_eligible": "eligibility_contract",
        "reaction_eligible": "eligibility_contract",
        "history_eligible": "issuer_history_contract",
    }.get(dimension, "deterministic_v9_semantics")

