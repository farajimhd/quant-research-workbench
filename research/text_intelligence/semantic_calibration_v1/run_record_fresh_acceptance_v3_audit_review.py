from __future__ import annotations

import argparse
from pathlib import Path

from research.mlops.paths import MLOpsPathConfig

from .fresh_acceptance_v3_audit_review import record_fresh_acceptance_v3_reviews


def main(argv: list[str] | None = None) -> int:
    runtime = MLOpsPathConfig.from_env().runtimes_root
    parser = argparse.ArgumentParser(description="Persist the third fresh-100 manual audit.")
    parser.add_argument(
        "--acceptance-root", type=Path,
        default=runtime / "text_intelligence" / "semantic_calibration_v1" / "news_acceptance_100_v3",
    )
    parser.add_argument(
        "--review-manifest", type=Path, required=True,
        help="Reviewer-authored JSON manifest outside the source repository.",
    )
    args = parser.parse_args(argv)
    result = record_fresh_acceptance_v3_reviews(
        args.acceptance_root, args.review_manifest
    )
    state = result["state"]
    print(
        f"COMPLETED | reviewed={state['reviewed_count']} "
        f"v9_fixes={state['v9_fixes_required']} gold_fixes={state['gold_corrections_required']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
