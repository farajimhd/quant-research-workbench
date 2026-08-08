from __future__ import annotations

import argparse
from pathlib import Path

from research.mlops.paths import MLOpsPathConfig

from .sol_teacher_forecast_gold_review_collection import collect_gold_reviews


def main() -> int:
    runtimes = MLOpsPathConfig.from_env().runtimes_root
    parser = argparse.ArgumentParser(
        description="Validate and durably collect prediction-blind gold reviews."
    )
    parser.add_argument(
        "--review-root",
        type=Path,
        default=(
            runtimes
            / "text_intelligence"
            / "news_synthesis_v1"
            / "sol_teacher_forecast_gold_review_v1"
        ),
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
        "--input-root",
        type=Path,
        default=Path("C:/tmp/sol_gold_reviews"),
    )
    args = parser.parse_args()
    progress = collect_gold_reviews(
        args.review_root.resolve(),
        args.split_root.resolve(),
        args.input_root.resolve(),
    )
    print(
        f"COLLECTED batches={progress['reviewed_batches']:,}/"
        f"{progress['total_batches']:,} units={progress['reviewed_issuer_units']:,}/"
        f"{progress['total_issuer_units']:,}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
