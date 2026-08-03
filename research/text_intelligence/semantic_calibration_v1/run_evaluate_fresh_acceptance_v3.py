from __future__ import annotations

import argparse
from pathlib import Path

from research.mlops.paths import MLOpsPathConfig

from .run_evaluate_fresh_acceptance_v2 import evaluate_acceptance


CONTRACT = "news_fresh_acceptance_v3_v9_evaluation"
SPLIT = "fresh_acceptance_v3"


def main(argv: list[str] | None = None) -> int:
    runtime = MLOpsPathConfig.from_env().runtimes_root
    base = runtime / "text_intelligence" / "semantic_calibration_v1"
    parser = argparse.ArgumentParser(
        description="Evaluate V9 after the third 100-label acceptance round is frozen."
    )
    parser.add_argument(
        "--acceptance-root", type=Path, default=base / "news_acceptance_100_v3"
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=base / "news_acceptance_100_v3" / "evaluation",
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
