from __future__ import annotations

import argparse
import json
from pathlib import Path

from .structured_metadata_rf_pre_holdout import (
    evaluate_training,
    train_and_evaluate,
    validate_artifacts,
)


DEFAULT_PARENT = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\structured_metadata_rf_v1"
)
DEFAULT_AUTHORITY = Path(
    r"D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4"
    r"\forecast_eligibility_sentiment_authority_structured_rf_reverse_audit_v1"
)
DEFAULT_HOLDOUT = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\forecast_eligibility_august_2026_temporal_holdout_v1"
)
DEFAULT_OUTPUT = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\structured_metadata_rf_2025_through_2026_aug13_to_august_holdout_v1"
)
DEFAULT_TRAINING_EVALUATION = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\structured_metadata_rf_2025_through_2026_aug13_training_evaluation_v1"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Train through August 13, 2026 and evaluate the sealed August tail"
    )
    parser.add_argument(
        "command", choices=("train-evaluate", "validate", "evaluate-training")
    )
    parser.add_argument("--parent-root", type=Path, default=DEFAULT_PARENT)
    parser.add_argument("--authority-root", type=Path, default=DEFAULT_AUTHORITY)
    parser.add_argument("--holdout-root", type=Path, default=DEFAULT_HOLDOUT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--training-evaluation-root", type=Path,
        default=DEFAULT_TRAINING_EVALUATION,
    )
    args = parser.parse_args(argv)
    if args.command == "train-evaluate":
        result = train_and_evaluate(
            parent_root=args.parent_root,
            authority_root=args.authority_root,
            holdout_root=args.holdout_root,
            output_root=args.output_root,
        )
    elif args.command == "validate":
        result = validate_artifacts(output_root=args.output_root)
    else:
        result = evaluate_training(
            parent_root=args.parent_root,
            authority_root=args.authority_root,
            model_root=args.output_root,
            output_root=args.training_evaluation_root,
        )
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
