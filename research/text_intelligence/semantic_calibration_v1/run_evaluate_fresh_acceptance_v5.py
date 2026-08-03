from __future__ import annotations

import argparse
from pathlib import Path

from research.mlops.paths import MLOpsPathConfig

from .freeze_acceptance import read_frozen_gold_authority
from .fresh_acceptance_v5 import LOCKED_SPLIT, SAMPLE_SIZE
from .run_evaluate_fresh_acceptance_v2 import evaluate_acceptance
from .run_freeze_fresh_acceptance_v5 import CONTRACT as GOLD_CONTRACT


CONTRACT = "news_fresh_acceptance_v5_untouched_v9_evaluation"


def main(argv: list[str] | None = None) -> int:
    base = (
        MLOpsPathConfig.from_env().runtimes_root
        / "text_intelligence"
        / "semantic_calibration_v1"
    )
    parser = argparse.ArgumentParser(
        description="Evaluate V9 against the frozen untouched N1501-N2000 authority."
    )
    parser.add_argument(
        "--acceptance-root", type=Path, default=base / "news_acceptance_500_v5"
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=base / "news_acceptance_500_v5" / "untouched_v9_evaluation",
    )
    args = parser.parse_args(argv)
    authority = read_frozen_gold_authority(
        args.acceptance_root,
        contract=GOLD_CONTRACT,
        expected_count=SAMPLE_SIZE,
    )
    return evaluate_acceptance(
        acceptance_root=args.acceptance_root,
        output_root=args.output_root,
        contract=CONTRACT,
        split=LOCKED_SPLIT,
        gold_authority_sha256=str(authority["gold_authority_sha256"]),
    )


if __name__ == "__main__":
    raise SystemExit(main())
