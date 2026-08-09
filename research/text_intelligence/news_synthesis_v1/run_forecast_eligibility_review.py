from __future__ import annotations

import argparse
import json
from pathlib import Path

from .forecast_eligibility_review import finalize_screening, prepare_second_pass, prepare_third_pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Consolidate blind forecast-eligibility screening reviews.")
    parser.add_argument("command", choices=("prepare-second", "prepare-third", "finalize"))
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--review", type=Path, action="append", required=True)
    parser.add_argument("--qa-fraction", type=float, default=0.10)
    args = parser.parse_args()
    reviews: list[Path] = []
    for path in args.review:
        resolved = path.resolve()
        reviews.extend(sorted(resolved.glob("*.jsonl")) if resolved.is_dir() else [resolved])
    if args.command == "prepare-second":
        manifest = prepare_second_pass(args.run_root.resolve(), reviews, qa_fraction=args.qa_fraction)
    elif args.command == "prepare-third":
        manifest = prepare_third_pass(args.run_root.resolve(), reviews)
    else:
        manifest = finalize_screening(args.run_root.resolve(), reviews)
    print(json.dumps({key: value for key, value in manifest.items() if key != "selected"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
