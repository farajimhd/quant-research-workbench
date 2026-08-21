from __future__ import annotations

import argparse
from pathlib import Path

from .funnel_holdout_review import finalize_gold, prepare_disagreements, prepare_review


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prediction-blind held-out gold review")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--root", type=Path, required=True)
    disagree = sub.add_parser("prepare-disagreements")
    disagree.add_argument("--root", type=Path, required=True)
    disagree.add_argument("--shard", type=int, required=True)
    disagree.add_argument("--first", type=Path, required=True)
    disagree.add_argument("--second", type=Path, required=True)
    finalize = sub.add_parser("finalize")
    finalize.add_argument("--root", type=Path, required=True)
    for shard in range(3):
        finalize.add_argument(f"--first-{shard}", type=Path, required=True)
        finalize.add_argument(f"--second-{shard}", type=Path, required=True)
        finalize.add_argument(f"--third-{shard}", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        report = prepare_review(args.root)
    elif args.command == "prepare-disagreements":
        report = prepare_disagreements(args.root, args.shard, args.first, args.second)
    else:
        assignments = [
            (getattr(args, f"first_{index}"), getattr(args, f"second_{index}"), getattr(args, f"third_{index}"))
            for index in range(3)
        ]
        report = finalize_gold(args.root, assignments)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
