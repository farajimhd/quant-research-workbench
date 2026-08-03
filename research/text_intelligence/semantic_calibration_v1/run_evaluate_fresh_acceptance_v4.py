from __future__ import annotations

import argparse
import os
from pathlib import Path

from research.mlops.paths import MLOpsPathConfig

from .freeze_acceptance import read_frozen_gold_authority
from .run_freeze_fresh_acceptance_v4 import CONTRACT as GOLD_CONTRACT
from .run_evaluate_fresh_acceptance_v2 import evaluate_acceptance


CONTRACT = "news_fresh_acceptance_v4_candidate20_untouched_evaluation"
SPLIT = "fresh_acceptance_v4_untouched"


def main(argv: list[str] | None = None) -> int:
    runtime = MLOpsPathConfig.from_env().runtimes_root
    base = runtime / "text_intelligence" / "semantic_calibration_v1"
    parser = argparse.ArgumentParser(
        description="Record the untouched candidate-20 baseline after all 200 labels freeze."
    )
    parser.add_argument(
        "--acceptance-root", type=Path, default=base / "news_acceptance_200_v4"
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=base / "news_acceptance_200_v4" / "untouched_candidate20_evaluation",
    )
    args = parser.parse_args(argv)
    if args.output_root.exists():
        raise RuntimeError(
            "untouched candidate-20 output already exists; refusing to overwrite baseline"
        )
    authority = read_frozen_gold_authority(
        args.acceptance_root,
        contract=GOLD_CONTRACT,
        expected_count=200,
    )
    staging = args.output_root.with_name(f".{args.output_root.name}.staging")
    if staging.exists():
        raise RuntimeError("candidate-20 staging output already exists; audit it before retry")
    result = evaluate_acceptance(
        acceptance_root=args.acceptance_root,
        output_root=staging,
        contract=CONTRACT,
        split=SPLIT,
        gold_authority_sha256=str(authority["gold_authority_sha256"]),
    )
    os.replace(staging, args.output_root)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
