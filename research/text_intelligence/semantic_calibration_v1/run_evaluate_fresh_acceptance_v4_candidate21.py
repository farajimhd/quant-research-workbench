from __future__ import annotations

import argparse
from pathlib import Path

from research.mlops.paths import MLOpsPathConfig

from .run_evaluate_fresh_acceptance_v2 import evaluate_acceptance


CONTRACT = "news_fresh_acceptance_v4_candidate21_reviewed_evaluation"
SPLIT = "fresh_acceptance_v4_untouched"


def main(argv: list[str] | None = None) -> int:
    runtime = MLOpsPathConfig.from_env().runtimes_root
    base = runtime / "text_intelligence" / "semantic_calibration_v1"
    parser = argparse.ArgumentParser(
        description="Evaluate deterministic V9 candidate 21 against reviewed N1301-N1500 gold."
    )
    parser.add_argument(
        "--acceptance-root",
        type=Path,
        default=base / "news_acceptance_200_v4_reviewed",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=base / "news_acceptance_200_v4_reviewed" / "candidate21_evaluation",
    )
    args = parser.parse_args(argv)
    return evaluate_acceptance(
        acceptance_root=args.acceptance_root,
        output_root=args.output_root,
        contract=CONTRACT,
        split=SPLIT,
    )


if __name__ == "__main__":
    raise SystemExit(main())
