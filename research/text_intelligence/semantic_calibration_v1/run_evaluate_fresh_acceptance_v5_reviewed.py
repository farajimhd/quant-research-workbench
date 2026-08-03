from __future__ import annotations

import argparse
from pathlib import Path

from research.mlops.paths import MLOpsPathConfig

from .fresh_acceptance_v5 import LOCKED_SPLIT
from .fresh_acceptance_v5_gold_repairs import REPAIR_CONTRACT
from .run_evaluate_fresh_acceptance_v2 import evaluate_acceptance
from .storage import read_json


CONTRACT = "news_fresh_acceptance_v5_reviewed_v9_evaluation"


def main(argv: list[str] | None = None) -> int:
    base = MLOpsPathConfig.from_env().runtimes_root / "text_intelligence" / "semantic_calibration_v1"
    parser = argparse.ArgumentParser(description="Evaluate V9 against reviewed N1501-N2000 gold.")
    parser.add_argument("--acceptance-root", type=Path, default=base / "news_acceptance_500_v5_reviewed")
    parser.add_argument(
        "--output-root", type=Path,
        default=base / "news_acceptance_500_v5_reviewed" / "candidate41_evaluation",
    )
    args = parser.parse_args(argv)
    manifest = read_json(args.acceptance_root / "reviewed_gold_manifest.json")
    if manifest.get("contract") != REPAIR_CONTRACT:
        raise RuntimeError("reviewed V5 gold manifest contract mismatch")
    return evaluate_acceptance(
        acceptance_root=args.acceptance_root,
        output_root=args.output_root,
        contract=CONTRACT,
        split=LOCKED_SPLIT,
        gold_authority_sha256=str(manifest["manifest_sha256"]),
    )


if __name__ == "__main__":
    raise SystemExit(main())
