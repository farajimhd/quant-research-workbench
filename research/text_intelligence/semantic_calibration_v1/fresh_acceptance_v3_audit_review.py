from __future__ import annotations

from pathlib import Path
from typing import Any

from .fresh_acceptance_v2_audit_review import record_audit_reviews
from .storage import read_json


REVIEW_CONTRACT = "news_fresh_acceptance_v3_manual_audit_review_v1"


def record_fresh_acceptance_v3_reviews(root: Path, review_manifest: Path) -> dict[str, Any]:
    """Persist the completed 100-file manual forensic review.

    The manifest is reviewer-authored runtime evidence. Evaluator differences
    must never be relabeled as a manual review automatically.
    """
    payload = read_json(review_manifest)
    specs = payload.get("reviews") if isinstance(payload, dict) else payload
    if not isinstance(specs, list):
        raise TypeError("manual review manifest must be a JSON array or {reviews: [...]}")
    expected = {f"N{number}" for number in range(1201, 1301)}
    actual = {str(value.get("sample_id") or "").upper() for value in specs}
    if actual != expected or len(specs) != len(expected):
        raise ValueError(
            f"manual review manifest must contain N1201-N1300 exactly; "
            f"missing={sorted(expected-actual)} extra={sorted(actual-expected)}"
        )
    return record_audit_reviews(
        root,
        specs,
        review_name="manual_audit_review_v1",
        contract=REVIEW_CONTRACT,
    )
