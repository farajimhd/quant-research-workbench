from __future__ import annotations

import argparse
from pathlib import Path

from research.mlops.paths import MLOpsPathConfig

from .fresh_acceptance_gold_repairs import repair_fresh_acceptance_gold


def main(argv: list[str] | None = None) -> int:
    runtime = MLOpsPathConfig.from_env().runtimes_root
    parser = argparse.ArgumentParser(description="Apply reviewed Fresh-100 gold corrections with a durable receipt.")
    parser.add_argument(
        "--acceptance-root",
        type=Path,
        default=runtime / "text_intelligence" / "semantic_calibration_v1" / "news_acceptance_100_v1",
    )
    args = parser.parse_args(argv)
    result = repair_fresh_acceptance_gold(args.acceptance_root, report=lambda value: print(value, flush=True))
    print(
        f"COMPLETED | certified={result['certified_records']} "
        f"changed={result['changed_this_run']} contract={result['contract']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
