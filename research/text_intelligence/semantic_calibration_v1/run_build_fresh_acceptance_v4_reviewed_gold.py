from __future__ import annotations

import argparse
from pathlib import Path

from research.mlops.paths import MLOpsPathConfig

from .fresh_acceptance_v4_gold_repairs import build_reviewed_gold


def main(argv: list[str] | None = None) -> int:
    runtime = MLOpsPathConfig.from_env().runtimes_root
    base = runtime / "text_intelligence" / "semantic_calibration_v1"
    parser = argparse.ArgumentParser(description="Build the reviewed N1301-N1500 gold authority non-destructively.")
    parser.add_argument("--source-root", type=Path, default=base / "news_acceptance_200_v4")
    parser.add_argument("--target-root", type=Path, default=base / "news_acceptance_200_v4_reviewed")
    args = parser.parse_args(argv)
    manifest = build_reviewed_gold(args.source_root, args.target_root, report=print)
    print(f"COMPLETED reviewed_gold_changes={len(manifest['changes'])}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
