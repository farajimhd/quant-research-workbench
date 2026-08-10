from __future__ import annotations

import argparse
import json
from pathlib import Path

from .gold_label_consolidation import consolidate_gold_labels, validate_consolidated_gold


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build or validate a lineage-preserving consolidated News Synthesis gold dataset."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--runtime-root", type=Path, required=True)
    build.add_argument("--manual-certification-root", type=Path, required=True)
    build.add_argument("--sol-reviewed-root", type=Path, required=True)
    build.add_argument("--sol-split-root", type=Path, required=True)
    build.add_argument("--forecast-root", type=Path, action="append", required=True)
    build.add_argument("--output-root", type=Path, required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "build":
        report = consolidate_gold_labels(
            runtime_root=args.runtime_root,
            manual_certification_root=args.manual_certification_root,
            sol_reviewed_root=args.sol_reviewed_root,
            sol_split_root=args.sol_split_root,
            forecast_roots=args.forecast_root,
            output_root=args.output_root,
        )
    else:
        report = validate_consolidated_gold(args.output_root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
