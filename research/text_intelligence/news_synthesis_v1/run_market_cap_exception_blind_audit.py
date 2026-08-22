from __future__ import annotations

import argparse
from pathlib import Path

from .market_cap_exception_blind_audit import finalize, prepare, prepare_full_confirmation


RUNTIME = Path(r"D:\TradingML\runtimes\text_intelligence")
DEFAULT_CANDIDATES = RUNTIME / "news_synthesis_v1" / "provider_market_cap_context_analysis_v3" / "CANDIDATE_ARTICLES.jsonl"
DEFAULT_FEATURES = RUNTIME / "news_synthesis_v1" / "provider_filter_feature_audit_v6_provider_path_exceptions_final" / "ARTICLE_FEATURES.jsonl"
DEFAULT_RENDERED = RUNTIME / "llm_issuer_labeling_v4" / "forecast_eligibility_rf_comparison_v1" / "rendered_texts.jsonl"
DEFAULT_OUTPUT = RUNTIME / "news_synthesis_v1" / "market_cap_high_precision_exception_blind_audit_v1"
DEFAULT_AUTHORITY = RUNTIME / "llm_issuer_labeling_v4" / "forecast_eligibility_sentiment_authority_provider_path_exceptions_v2"
DEFAULT_SUCCESSOR = RUNTIME / "llm_issuer_labeling_v4" / "forecast_eligibility_sentiment_authority_market_cap_exceptions_v1"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the staged blind audit of market-cap candidate exceptions.")
    parser.add_argument("stage", choices=("prepare", "prepare-full", "finalize"))
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--article-features", type=Path, default=DEFAULT_FEATURES)
    parser.add_argument("--rendered-texts", type=Path, default=DEFAULT_RENDERED)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--parent-authority", type=Path, default=DEFAULT_AUTHORITY)
    parser.add_argument("--successor-authority", type=Path, default=DEFAULT_SUCCESSOR)
    args = parser.parse_args(argv)
    if args.stage == "prepare":
        result = prepare(
            candidates_path=args.candidates,
            article_features_path=args.article_features,
            rendered_texts_path=args.rendered_texts,
            output_root=args.output_root,
        )
    elif args.stage == "prepare-full":
        result = prepare_full_confirmation(
            output_root=args.output_root,
            rendered_texts_path=args.rendered_texts,
        )
    else:
        result = finalize(
            output_root=args.output_root,
            parent_authority=args.parent_authority,
            successor_authority=args.successor_authority,
        )
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
