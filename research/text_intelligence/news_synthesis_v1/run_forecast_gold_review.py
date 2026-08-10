from __future__ import annotations

import argparse
import json
from pathlib import Path

from .forecast_gold_review import (
    certify_consensus,
    evaluate_article_forecast_eligibility,
    prepare_adjudication,
    prepare_full_source_review,
    validate_certified_authority,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and certify focused full-source forecast gold.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--sampling-root", type=Path, required=True)
    prepare.add_argument("--output-root", type=Path, required=True)
    prepare.add_argument(
        "--screening-label",
        action="append",
        choices=("eligible", "unresolved"),
        default=None,
        help="Silver screening label to retain; repeat to preserve unresolved high-recall candidates.",
    )
    adjudicate = subparsers.add_parser("adjudicate")
    adjudicate.add_argument("--review-root", type=Path, required=True)
    adjudicate.add_argument("--pass-one", type=Path, action="append", required=True)
    adjudicate.add_argument("--pass-two", type=Path, action="append", required=True)
    certify = subparsers.add_parser("certify")
    certify.add_argument("--review-root", type=Path, required=True)
    certify.add_argument("--pass-three", type=Path, action="append", default=[])
    certify.add_argument("--manual-adjudication", type=Path, action="append", default=[])
    validate = subparsers.add_parser("validate")
    validate.add_argument("--review-root", type=Path, required=True)
    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument("--review-root", type=Path, required=True)
    evaluate.add_argument("--predictions", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "prepare":
        manifest = prepare_full_source_review(
            args.sampling_root.resolve(),
            args.output_root.resolve(),
            screening_labels=args.screening_label or ("eligible",),
        )
    elif args.command == "adjudicate":
        manifest = prepare_adjudication(
            args.review_root.resolve(), _expand(args.pass_one), _expand(args.pass_two)
        )
    elif args.command == "certify":
        manifest = certify_consensus(
            args.review_root.resolve(),
            _expand(args.pass_three),
            _expand(args.manual_adjudication),
        )
    elif args.command == "validate":
        manifest = validate_certified_authority(args.review_root.resolve())
    else:
        manifest = evaluate_article_forecast_eligibility(
            args.review_root.resolve(), args.predictions.resolve()
        )
    print(json.dumps({key: value for key, value in manifest.items() if key != "assignments"}, indent=2))
    return 0


def _expand(paths: list[Path]) -> list[Path]:
    values = []
    for path in paths:
        resolved = path.resolve()
        values.extend(sorted(resolved.glob("*.jsonl")) if resolved.is_dir() else [resolved])
    return values


if __name__ == "__main__":
    raise SystemExit(main())
