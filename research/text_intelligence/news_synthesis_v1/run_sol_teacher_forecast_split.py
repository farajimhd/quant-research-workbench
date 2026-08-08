from __future__ import annotations

import argparse
from pathlib import Path

from research.mlops.paths import MLOpsPathConfig

from .sol_teacher_forecast_split import create_forecast_split


def main() -> int:
    runtimes = MLOpsPathConfig.from_env().runtimes_root
    parser = argparse.ArgumentParser(
        description=(
            "Create a prediction-blind, article-grouped audit/test split for "
            "the converted Sol forecast-eligible authority."
        )
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
            / "sol_teacher_forecast_split_v1"
        ),
    )
    parser.add_argument("--expected-units", type=int, default=5_528)
    args = parser.parse_args()
    manifest = create_forecast_split(
        args.evaluation_root.resolve(),
        args.teacher_root.resolve(),
        args.output_root.resolve(),
        expected_units=args.expected_units,
    )
    print(
        f"COMPLETE output={args.output_root.resolve()} "
        f"audit={manifest['audit']['articles']:,}/"
        f"{manifest['audit']['issuer_units']:,} "
        f"test={manifest['test']['articles']:,}/"
        f"{manifest['test']['issuer_units']:,}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
