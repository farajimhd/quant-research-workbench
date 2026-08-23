from __future__ import annotations

import argparse
import json
from pathlib import Path

from .structured_rf_disagreement_audit import validate_review
from .structured_rf_reverse_disagreement_funnel import (
    collect_primary,
    collect_qa_expansion,
    collect_qa_general,
    finalize,
    prepare_full,
    prepare_full_agent_assignments,
    prepare_primary,
    prepare_qa,
    prepare_qa_expansion,
    promote_successor_authority,
    validate_artifacts,
)


RUNTIME = Path(r"D:\TradingML\runtimes\text_intelligence")
DEFAULT_PREDICTIONS = RUNTIME / "news_synthesis_v1" / "structured_metadata_rf_reverse_2026_to_2025_v1" / "LABEL_DISAGREEMENTS_2025.jsonl"
DEFAULT_AUTHORITY = RUNTIME / "llm_issuer_labeling_v4" / "forecast_eligibility_sentiment_authority_structured_rf_priority_v1"
DEFAULT_FEATURES = RUNTIME / "news_synthesis_v1" / "provider_filter_feature_audit_v6_provider_path_exceptions_final" / "ARTICLE_FEATURES.jsonl"
DEFAULT_RENDERED = RUNTIME / "llm_issuer_labeling_v4" / "forecast_eligibility_rf_comparison_v1" / "rendered_texts.jsonl"
DEFAULT_OUTPUT = RUNTIME / "news_synthesis_v1" / "structured_rf_reverse_disagreement_blind_audit_v1"
DEFAULT_SUCCESSOR = RUNTIME / "llm_issuer_labeling_v4" / "forecast_eligibility_sentiment_authority_structured_rf_reverse_audit_v1"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Adaptive blind audit of reverse-RF 2025 disagreements")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("prepare-primary", "collect-primary", "prepare-qa", "collect-qa", "prepare-qa-expansion", "collect-qa-expansion", "prepare-full", "prepare-full-assignments", "finalize", "validate"):
        item = sub.add_parser(command); item.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    promote = sub.add_parser("promote-authority")
    promote.add_argument("--audit-root", type=Path, default=DEFAULT_OUTPUT)
    promote.add_argument("--parent-authority", type=Path, default=DEFAULT_AUTHORITY)
    promote.add_argument("--successor-authority", type=Path, default=DEFAULT_SUCCESSOR)
    review = sub.add_parser("validate-review")
    review.add_argument("--packet", type=Path, required=True); review.add_argument("--review", type=Path, required=True)
    review.add_argument("--full-text", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "prepare-primary":
        result = prepare_primary(predictions_path=DEFAULT_PREDICTIONS, authority_root=DEFAULT_AUTHORITY,
                                 feature_path=DEFAULT_FEATURES, rendered_texts_path=DEFAULT_RENDERED,
                                 output_root=args.output_root)
    elif args.command == "collect-primary": result = collect_primary(output_root=args.output_root)
    elif args.command == "prepare-qa": result = prepare_qa(output_root=args.output_root)
    elif args.command == "collect-qa": result = collect_qa_general(output_root=args.output_root)
    elif args.command == "prepare-qa-expansion": result = prepare_qa_expansion(output_root=args.output_root)
    elif args.command == "collect-qa-expansion": result = collect_qa_expansion(output_root=args.output_root)
    elif args.command == "prepare-full": result = prepare_full(output_root=args.output_root, rendered_texts_path=DEFAULT_RENDERED)
    elif args.command == "prepare-full-assignments": result = prepare_full_agent_assignments(output_root=args.output_root)
    elif args.command == "finalize": result = finalize(output_root=args.output_root)
    elif args.command == "validate": result = validate_artifacts(output_root=args.output_root)
    elif args.command == "promote-authority":
        result = promote_successor_authority(audit_root=args.audit_root, parent_authority=args.parent_authority,
                                             successor_authority=args.successor_authority)
    else: result = validate_review(packet_path=args.packet, review_path=args.review, full_text=args.full_text)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True)); return 0


if __name__ == "__main__": raise SystemExit(main())
