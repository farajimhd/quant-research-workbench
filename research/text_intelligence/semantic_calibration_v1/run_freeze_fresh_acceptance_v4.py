from __future__ import annotations

import argparse
import json
from pathlib import Path

from research.mlops.paths import MLOpsPathConfig

from .freeze_acceptance import freeze_gold_authority


CONTRACT = "news_fresh_acceptance_v4_prediction_blind_gold"


def main(argv: list[str] | None = None) -> int:
    runtime = MLOpsPathConfig.from_env().runtimes_root
    parser = argparse.ArgumentParser(description="Freeze the 200-article V4 gold authority.")
    parser.add_argument(
        "--acceptance-root",
        type=Path,
        default=(
            runtime
            / "text_intelligence"
            / "semantic_calibration_v1"
            / "news_acceptance_200_v4"
        ),
    )
    args = parser.parse_args(argv)
    authority = freeze_gold_authority(
        args.acceptance_root,
        contract=CONTRACT,
        expected_count=200,
    )
    print(json.dumps(authority, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
