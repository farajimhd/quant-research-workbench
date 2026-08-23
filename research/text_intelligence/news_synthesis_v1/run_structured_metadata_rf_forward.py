from __future__ import annotations

import argparse
import json
from pathlib import Path

from .structured_metadata_rf_forward import (
    DEFAULT_AUTHORITY,
    DEFAULT_OUTPUT,
    DEFAULT_PARENT,
    train_and_evaluate,
    validate_artifacts,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Forward-temporal structured RF diagnostic: revised 2025 to revised 2026"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    train = sub.add_parser("train-evaluate")
    train.add_argument("--parent-root", type=Path, default=DEFAULT_PARENT)
    train.add_argument("--authority-root", type=Path, default=DEFAULT_AUTHORITY)
    train.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    validate = sub.add_parser("validate")
    validate.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    if args.command == "train-evaluate":
        result = train_and_evaluate(
            parent_root=args.parent_root, authority_root=args.authority_root,
            output_root=args.output_root,
        )
    else:
        result = validate_artifacts(output_root=args.output_root)
    summary = {
        key: result[key] for key in (
            "experiment_version", "status", "selected_threshold", "training",
            "test", "disagreements", "train_seconds", "checks",
        ) if key in result
    }
    print(json.dumps(summary, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
