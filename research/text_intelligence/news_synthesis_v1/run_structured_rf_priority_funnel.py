from __future__ import annotations

import argparse
import json
from pathlib import Path

from .structured_rf_priority_funnel import (
    collect_primary,
    collect_qa,
    finalize,
    prepare_full,
    promote_successor_authority,
    validate_artifacts,
    prepare_primary,
    prepare_qa,
)
from .structured_rf_disagreement_audit import validate_review


RUNTIME = Path(r"D:\TradingML\runtimes\text_intelligence")
DEFAULT_CALIBRATION = RUNTIME / "news_synthesis_v1" / "structured_rf_disagreement_blind_audit_v1"
DEFAULT_AUTHORITY = RUNTIME / "llm_issuer_labeling_v4" / "forecast_eligibility_sentiment_authority_structured_rf_audit_v1"
DEFAULT_FEATURES = RUNTIME / "news_synthesis_v1" / "provider_filter_feature_audit_v6_provider_path_exceptions_final" / "ARTICLE_FEATURES.jsonl"
DEFAULT_RENDERED = RUNTIME / "llm_issuer_labeling_v4" / "forecast_eligibility_rf_comparison_v1" / "rendered_texts.jsonl"
DEFAULT_OUTPUT = RUNTIME / "news_synthesis_v1" / "structured_rf_priority_blind_review_v1"
DEFAULT_SUCCESSOR = RUNTIME / "llm_issuer_labeling_v4" / "forecast_eligibility_sentiment_authority_structured_rf_priority_v1"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Adaptive blind-review funnel for structured RF priority disagreements")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare-primary")
    prepare.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    validate = sub.add_parser("validate-review")
    validate.add_argument("--packet", type=Path, required=True)
    validate.add_argument("--review", type=Path, required=True)
    validate.add_argument("--full-text", action="store_true")
    collect = sub.add_parser("collect-primary")
    collect.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    prepare_qa_parser = sub.add_parser("prepare-qa")
    prepare_qa_parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    collect_qa_parser = sub.add_parser("collect-qa")
    collect_qa_parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    prepare_full_parser = sub.add_parser("prepare-full")
    prepare_full_parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    finalize_parser = sub.add_parser("finalize")
    finalize_parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    validate_artifacts_parser = sub.add_parser("validate-artifacts")
    validate_artifacts_parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    promote = sub.add_parser("promote-authority")
    promote.add_argument("--audit-root", type=Path, default=DEFAULT_OUTPUT)
    promote.add_argument("--parent-authority", type=Path, default=DEFAULT_AUTHORITY)
    promote.add_argument("--successor-authority", type=Path, default=DEFAULT_SUCCESSOR)
    args = parser.parse_args(argv)
    if args.command == "prepare-primary":
        result = prepare_primary(
            calibration_root=DEFAULT_CALIBRATION, successor_authority=DEFAULT_AUTHORITY,
            article_features_path=DEFAULT_FEATURES, rendered_texts_path=DEFAULT_RENDERED,
            output_root=args.output_root,
        )
    elif args.command == "validate-review":
        result = validate_review(packet_path=args.packet, review_path=args.review, full_text=args.full_text)
    elif args.command == "collect-primary":
        result = collect_primary(output_root=args.output_root)
    elif args.command == "prepare-qa":
        result = prepare_qa(output_root=args.output_root)
    elif args.command == "collect-qa":
        result = collect_qa(output_root=args.output_root)
    elif args.command == "prepare-full":
        result = prepare_full(output_root=args.output_root, rendered_texts_path=DEFAULT_RENDERED)
    elif args.command == "finalize":
        result = finalize(output_root=args.output_root)
    elif args.command == "validate-artifacts":
        result = validate_artifacts(output_root=args.output_root)
    else:
        result = promote_successor_authority(
            audit_root=args.audit_root, parent_authority=args.parent_authority,
            successor_authority=args.successor_authority,
        )
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
