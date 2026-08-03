from __future__ import annotations

import argparse
from pathlib import Path

from research.mlops.paths import MLOpsPathConfig

from .fresh_acceptance_v5_gold_repairs import build_reviewed_gold


def main(argv: list[str] | None = None) -> int:
    base = MLOpsPathConfig.from_env().runtimes_root / "text_intelligence" / "semantic_calibration_v1"
    parser = argparse.ArgumentParser(description="Build reviewed N1501-N2000 gold non-destructively.")
    parser.add_argument("--source-root", type=Path, default=base / "news_acceptance_500_v5")
    parser.add_argument("--target-root", type=Path, default=base / "news_acceptance_500_v5_reviewed")
    parser.add_argument(
        "--prediction-root", type=Path,
        default=base / "news_acceptance_500_v5" / "untouched_v9_evaluation" / "v9_predictions",
    )
    args = parser.parse_args(argv)
    manifest = build_reviewed_gold(
        args.source_root, args.target_root, prediction_root=args.prediction_root, report=print
    )
    print(f"COMPLETED reviewed_gold_changes={len(manifest['changes'])}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
