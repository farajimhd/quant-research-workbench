from __future__ import annotations

import argparse
from pathlib import Path

from research.mlops.paths import MLOpsPathConfig

from .fresh_acceptance_v3_gold_repairs import repair_fresh_acceptance_v3_gold


def main(argv: list[str] | None = None) -> int:
    runtime = MLOpsPathConfig.from_env().runtimes_root
    parser = argparse.ArgumentParser(description="Apply audited gold corrections to third fresh-100.")
    parser.add_argument(
        "--acceptance-root", type=Path,
        default=runtime / "text_intelligence" / "semantic_calibration_v1" / "news_acceptance_100_v3",
    )
    args = parser.parse_args(argv)
    manifest = repair_fresh_acceptance_v3_gold(
        args.acceptance_root, report=lambda value: print(value, flush=True)
    )
    print(f"COMPLETED | changes={len(manifest['changes'])}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
