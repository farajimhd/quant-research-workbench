from __future__ import annotations

import argparse
from pathlib import Path

from research.mlops.paths import MLOpsPathConfig

from .sol_teacher_forecast_gold_review import create_gold_review_packets


def main() -> int:
    runtimes = MLOpsPathConfig.from_env().runtimes_root
    parser = argparse.ArgumentParser(
        description="Create prediction-blind audit-set gold review packets."
    )
    parser.add_argument(
        "--split-root",
        type=Path,
        default=(
            runtimes
            / "text_intelligence"
            / "news_synthesis_v1"
            / "sol_teacher_forecast_split_v1"
        ),
    )
    parser.add_argument(
        "--evaluation-root",
        type=Path,
        default=(
            runtimes
            / "text_intelligence"
            / "news_synthesis_v1"
            / "sol_teacher_direction_evaluation_v1"
        ),
    )
    parser.add_argument(
        "--teacher-root",
        type=Path,
        default=(
            runtimes
            / "text_intelligence"
            / "semantic_calibration_v1"
            / "sol_teacher_10000_v1"
        ),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=(
            runtimes
            / "text_intelligence"
            / "news_synthesis_v1"
            / "sol_teacher_forecast_gold_review_v1"
        ),
    )
    args = parser.parse_args()
    manifest = create_gold_review_packets(
        args.split_root.resolve(),
        args.evaluation_root.resolve(),
        args.teacher_root.resolve(),
        args.output_root.resolve(),
    )
    print(
        f"COMPLETE output={args.output_root.resolve()} "
        f"packets={manifest['packet_count']:,}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
