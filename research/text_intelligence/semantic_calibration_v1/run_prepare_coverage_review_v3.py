from __future__ import annotations

import argparse
from pathlib import Path

from .coverage_review_v3 import (
    initialize_structurally_complete_decisions,
    prepare_coverage_review,
)
from .run_deterministic_news_v6 import DEFAULT_ROOT


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare exhaustive issuer-coverage review packages.")
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument(
        "--initialize-structural-decisions",
        action="store_true",
        help="Persist only records that require no semantic reviewer judgment.",
    )
    args = parser.parse_args()
    print(prepare_coverage_review(args.runtime_root), flush=True)
    if args.initialize_structural_decisions:
        print(initialize_structurally_complete_decisions(args.runtime_root), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
