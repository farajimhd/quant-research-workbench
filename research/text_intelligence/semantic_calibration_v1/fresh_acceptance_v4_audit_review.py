from __future__ import annotations

from pathlib import Path
from typing import Any

from .fresh_acceptance_v2_audit_review import record_audit_reviews
from .storage import read_json


REVIEW_CONTRACT = "news_fresh_acceptance_v4_manual_audit_review_v1"


def record_fresh_acceptance_v4_reviews(
    root: Path, review_manifest: Path
) -> dict[str, Any]:
    """Persist the completed N1301-N1500 forensic review.

    Candidate-20 predictions and their rendered audits are immutable inputs;
    this recorder writes only reviewer-authored decisions and hashes them.
    """
    payload = read_json(review_manifest)
    specs = payload.get("reviews") if isinstance(payload, dict) else payload
    if not isinstance(specs, list):
        raise TypeError("manual review manifest must be a JSON array or {reviews: [...]}")
    expected = {f"N{number}" for number in range(1301, 1501)}
    actual = {str(value.get("sample_id") or "").upper() for value in specs}
    if actual != expected or len(specs) != len(expected):
        raise ValueError(
            "manual review manifest must contain N1301-N1500 exactly; "
            f"missing={sorted(expected-actual)} extra={sorted(actual-expected)}"
        )
    evaluation_root = root / "untouched_candidate20_evaluation"
    return record_audit_reviews(
        root,
        specs,
        review_name="manual_audit_review_v1",
        contract=REVIEW_CONTRACT,
        prediction_root=evaluation_root / "v9_predictions",
        audit_root=root / "candidate20_article_audits" / "articles",
        item_root=root / "blinded_articles",
    )
