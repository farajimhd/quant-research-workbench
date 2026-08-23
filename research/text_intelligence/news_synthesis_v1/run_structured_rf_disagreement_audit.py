from __future__ import annotations

import argparse
import json
from pathlib import Path

from .structured_rf_disagreement_audit import (
    analyze,
    analyze_population,
    collect_compact_reviews,
    finalize,
    prepare,
    prepare_full,
    promote_successor_authority,
    validate_artifacts,
    validate_review,
)


RUNTIME = Path(r"D:\TradingML\runtimes\text_intelligence")
DEFAULT_OUTPUT = RUNTIME / "news_synthesis_v1" / "structured_rf_disagreement_blind_audit_v1"
DEFAULT_DISAGREEMENTS = RUNTIME / "news_synthesis_v1" / "structured_metadata_rf_v1" / "LABEL_DISAGREEMENTS_2026.jsonl"
DEFAULT_FEATURES = RUNTIME / "news_synthesis_v1" / "provider_filter_feature_audit_v6_provider_path_exceptions_final" / "ARTICLE_FEATURES.jsonl"
DEFAULT_CAPS = RUNTIME / "news_synthesis_v1" / "provider_market_cap_context_analysis_v3" / "ARTICLE_MARKET_CAP_FEATURES.jsonl"
DEFAULT_RENDERED = RUNTIME / "llm_issuer_labeling_v4" / "forecast_eligibility_rf_comparison_v1" / "rendered_texts.jsonl"
DEFAULT_PARENT_AUTHORITY = RUNTIME / "llm_issuer_labeling_v4" / "forecast_eligibility_sentiment_authority_provider_path_exceptions_v2"
DEFAULT_SUCCESSOR_AUTHORITY = RUNTIME / "llm_issuer_labeling_v4" / "forecast_eligibility_sentiment_authority_structured_rf_audit_v1"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prediction-blind audit of structured RF label disagreements")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("prepare")
    build.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    compact = sub.add_parser("collect-compact")
    compact.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    full = sub.add_parser("prepare-full")
    full.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    validate = sub.add_parser("validate-review")
    validate.add_argument("--packet", type=Path, required=True)
    validate.add_argument("--review", type=Path, required=True)
    validate.add_argument("--full-text", action="store_true")
    finish = sub.add_parser("finalize")
    finish.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    analysis = sub.add_parser("analyze")
    analysis.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    seal = sub.add_parser("validate")
    seal.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    population = sub.add_parser("analyze-population")
    population.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    promote = sub.add_parser("promote-authority")
    promote.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    promote.add_argument("--parent-authority", type=Path, default=DEFAULT_PARENT_AUTHORITY)
    promote.add_argument("--successor-authority", type=Path, default=DEFAULT_SUCCESSOR_AUTHORITY)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        result = prepare(
            disagreements_path=DEFAULT_DISAGREEMENTS, article_features_path=DEFAULT_FEATURES,
            market_cap_path=DEFAULT_CAPS, rendered_texts_path=DEFAULT_RENDERED,
            output_root=args.output_root,
        )
    elif args.command == "collect-compact":
        result = collect_compact_reviews(output_root=args.output_root)
    elif args.command == "prepare-full":
        result = prepare_full(output_root=args.output_root, rendered_texts_path=DEFAULT_RENDERED)
    elif args.command == "validate-review":
        result = validate_review(packet_path=args.packet, review_path=args.review, full_text=args.full_text)
    elif args.command == "finalize":
        result = finalize(output_root=args.output_root)
    elif args.command == "analyze":
        result = analyze(output_root=args.output_root)
    elif args.command == "validate":
        result = validate_artifacts(output_root=args.output_root)
    elif args.command == "analyze-population":
        result = analyze_population(output_root=args.output_root)
    else:
        result = promote_successor_authority(
            audit_root=args.output_root, parent_authority=args.parent_authority,
            successor_authority=args.successor_authority,
        )
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
