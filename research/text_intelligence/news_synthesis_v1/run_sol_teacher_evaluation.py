from __future__ import annotations

import argparse
from pathlib import Path

from research.mlops.paths import MLOpsPathConfig

from .sol_teacher_evaluation import (
    evaluate_sol_teacher_population,
    finalize_sol_teacher_evaluation,
)


def main() -> int:
    runtimes = MLOpsPathConfig.from_env().runtimes_root
    parser = argparse.ArgumentParser(
        description=(
            "Convert the frozen Sol teacher corpus to unreviewed News Synthesis "
            "documents and compare current-engine direction among eligible units."
        )
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
            / "sol_teacher_direction_evaluation_v1"
        ),
    )
    parser.add_argument("--expected-items", type=int, default=10_000)
    parser.add_argument("--expected-labels", type=int, default=9_997)
    parser.add_argument(
        "--finalize-existing",
        action="store_true",
        help=(
            "Validate and finalize durable conversion/prediction outputs from an "
            "interrupted run without rebuilding the identity snapshot."
        ),
    )
    args = parser.parse_args()
    operation = (
        finalize_sol_teacher_evaluation
        if args.finalize_existing
        else evaluate_sol_teacher_population
    )
    manifest = operation(
        args.teacher_root.resolve(),
        args.output_root.resolve(),
        expected_items=args.expected_items,
        expected_labels=args.expected_labels,
    )
    print(
        f"COMPLETE output={args.output_root.resolve()} "
        f"converted={manifest['population']['converted_labels']:,} "
        f"missing={manifest['population']['missing_teacher_labels']:,} "
        f"failures={manifest['population']['engine_failures']:,}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
