from __future__ import annotations

import argparse
from pathlib import Path

from research.mlops.paths import MLOpsPathConfig

from .fresh_acceptance_v4_audit_review import record_fresh_acceptance_v4_reviews


def main(argv: list[str] | None = None) -> int:
    runtime = MLOpsPathConfig.from_env().runtimes_root
    parser = argparse.ArgumentParser(description="Persist the fresh-200 manual audit.")
    parser.add_argument(
        "--acceptance-root",
        type=Path,
        default=runtime
        / "text_intelligence"
        / "semantic_calibration_v1"
        / "news_acceptance_200_v4",
    )
    parser.add_argument(
        "--review-manifest",
        type=Path,
        required=True,
        help="Reviewer-authored JSON manifest outside the source repository.",
    )
    args = parser.parse_args(argv)
    state = record_fresh_acceptance_v4_reviews(
        args.acceptance_root, args.review_manifest
    )["state"]
    print(
        f"COMPLETED | reviewed={state['reviewed_count']} "
        f"v9_fixes={state['v9_fixes_required']} "
        f"gold_fixes={state['gold_corrections_required']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
