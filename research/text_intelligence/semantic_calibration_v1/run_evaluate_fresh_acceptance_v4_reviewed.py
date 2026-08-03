from __future__ import annotations

import argparse
from pathlib import Path

from research.mlops.paths import MLOpsPathConfig

from .fresh_acceptance_v4_gold_repairs_v2 import REPAIR_CONTRACT
from .run_evaluate_fresh_acceptance_v2 import evaluate_acceptance
from .storage import read_json


CONTRACT = "news_fresh_acceptance_v4_reviewed_deterministic_v9_evaluation_v2"
SPLIT = "fresh_acceptance_v4_untouched"


def main(argv: list[str] | None = None) -> int:
    base = MLOpsPathConfig.from_env().runtimes_root / "text_intelligence" / "semantic_calibration_v1"
    parser = argparse.ArgumentParser(description="Evaluate current V9 against reviewed N1301-N1500 gold.")
    parser.add_argument("--acceptance-root", type=Path, default=base / "news_acceptance_200_v4_reviewed_v2")
    parser.add_argument("--output-root", type=Path, default=base / "news_acceptance_200_v4_reviewed_v2" / "current_v9_evaluation")
    args = parser.parse_args(argv)
    manifest = read_json(args.acceptance_root / "reviewed_gold_v2_manifest.json")
    if manifest.get("contract") != REPAIR_CONTRACT:
        raise RuntimeError("reviewed V4 gold manifest contract mismatch")
    return evaluate_acceptance(
        acceptance_root=args.acceptance_root,
        output_root=args.output_root,
        contract=CONTRACT,
        split=SPLIT,
        gold_authority_sha256=str(manifest["annotation_state_sha256"]),
    )


if __name__ == "__main__":
    raise SystemExit(main())
