from __future__ import annotations

import argparse
import json
from pathlib import Path

from .structured_tfidf_deepfm_pre_holdout import train_and_evaluate, validate_artifacts


RUNTIME = Path(r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1")
DEFAULT_PARENT = RUNTIME / "structured_metadata_rf_v1"
DEFAULT_AUTHORITY = Path(
    r"D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4"
    r"\forecast_eligibility_sentiment_authority_structured_rf_reverse_audit_v1"
)
DEFAULT_HOLDOUT = RUNTIME / "forecast_eligibility_august_2026_temporal_holdout_v1"
DEFAULT_RF = RUNTIME / "structured_tfidf_rf_2025_through_2026_aug13_to_august_holdout_v1"
DEFAULT_TEXT_AUTHORITY = Path(
    r"D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4"
    r"\forecast_eligibility_rf_comparison_v1"
)
DEFAULT_OUTPUT = RUNTIME / "structured_tfidf_deepfm_2025_through_2026_aug13_to_august_holdout_v1"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Train sparse DeepFM on structured plus TF-IDF features"
    )
    parser.add_argument("command", choices=("train-evaluate", "validate"))
    parser.add_argument("--parent-root", type=Path, default=DEFAULT_PARENT)
    parser.add_argument("--authority-root", type=Path, default=DEFAULT_AUTHORITY)
    parser.add_argument("--holdout-root", type=Path, default=DEFAULT_HOLDOUT)
    parser.add_argument("--rf-root", type=Path, default=DEFAULT_RF)
    parser.add_argument("--text-authority-root", type=Path, default=DEFAULT_TEXT_AUTHORITY)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    if args.command == "train-evaluate":
        result = train_and_evaluate(
            parent_root=args.parent_root,
            authority_root=args.authority_root,
            holdout_root=args.holdout_root,
            rf_root=args.rf_root,
            text_authority_root=args.text_authority_root,
            output_root=args.output_root,
        )
    else:
        result = validate_artifacts(output_root=args.output_root)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
