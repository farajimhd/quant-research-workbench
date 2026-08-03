from __future__ import annotations

from research.mlops.paths import MLOpsPathConfig

from .fresh_acceptance_v2_audit_review import record_audit_reviews
from .fresh_acceptance_v4_audit_review import REVIEW_CONTRACT
from .fresh_acceptance_v4_review_findings import build_review_specs


def main() -> int:
    root = (
        MLOpsPathConfig.from_env().runtimes_root
        / "text_intelligence" / "semantic_calibration_v1" / "news_acceptance_200_v4"
    )
    evaluation = root / "untouched_candidate20_evaluation"
    result = record_audit_reviews(
        root,
        build_review_specs(),
        review_name="manual_audit_review_v1",
        contract=REVIEW_CONTRACT,
        prediction_root=evaluation / "v9_predictions",
        audit_root=root / "candidate20_article_audits" / "articles",
        item_root=root / "blinded_articles",
    )
    print(result["state"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
