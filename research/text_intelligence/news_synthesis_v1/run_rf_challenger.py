from __future__ import annotations

import argparse
from pathlib import Path

from .rf_challenger import (
    DEFAULT_FEATURES, DEFAULT_HOLDOUT, DEFAULT_OUTPUT, DEFAULT_TEXTS,
    evaluate_holdout, train_and_freeze, validate_artifacts,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Leakage-controlled metadata plus TF-IDF Random Forest challenger")
    sub = parser.add_subparsers(dest="command", required=True)
    train = sub.add_parser("train")
    train.add_argument("--features", type=Path, default=DEFAULT_FEATURES)
    train.add_argument("--texts", type=Path, default=DEFAULT_TEXTS)
    train.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    evaluate.add_argument("--holdout-root", type=Path, default=DEFAULT_HOLDOUT)
    validate = sub.add_parser("validate")
    validate.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    if args.command == "train":
        report = train_and_freeze(feature_path=args.features, text_path=args.texts, output_root=args.output_root)
    elif args.command == "evaluate":
        report = evaluate_holdout(output_root=args.output_root, holdout_root=args.holdout_root)
    else:
        report = validate_artifacts(output_root=args.output_root)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
