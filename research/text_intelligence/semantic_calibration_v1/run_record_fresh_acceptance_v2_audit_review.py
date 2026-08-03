from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

from research.mlops.paths import MLOpsPathConfig
from .fresh_acceptance_v2_audit_review import record_audit_reviews


def main() -> int:
    runtime = MLOpsPathConfig.from_env().runtimes_root
    default_root = runtime / "text_intelligence" / "semantic_calibration_v1" / "news_acceptance_100_v2"
    parser = argparse.ArgumentParser(description="Record explicit second-pass reviews for fresh acceptance V2 audits.")
    parser.add_argument("--runtime-root", type=Path, default=default_root)
    parser.add_argument("--reviews-base64", required=True)
    parser.add_argument("--review-name", default="manual_audit_review_v1")
    parser.add_argument(
        "--contract", default="news_fresh_acceptance_v2_manual_audit_review_v1"
    )
    args = parser.parse_args()
    raw = base64.b64decode(args.reviews_base64).decode("utf-8")
    payload = json.loads(raw)
    specs = payload if isinstance(payload, list) else [payload]
    result = record_audit_reviews(
        args.runtime_root,
        specs,
        review_name=args.review_name,
        contract=args.contract,
    )
    state = result["state"]
    print(
        "RECORDED | "
        f"written={len(result['written'])} reviewed={state['reviewed_count']}/{state['sample_count']} "
        f"gold_fixes={state['gold_corrections_required']} v9_fixes={state['v9_fixes_required']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
