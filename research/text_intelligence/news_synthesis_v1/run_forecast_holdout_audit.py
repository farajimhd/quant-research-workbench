from __future__ import annotations

import argparse
import json
from pathlib import Path

from research.mlops.env import discover_env_files, load_env_files
from research.text_intelligence.llm_issuer_labeling_v3.codex_2026 import clickhouse_client

from .forecast_holdout_audit import (
    collect_compact,
    collect_full_expansion,
    collect_full_primary,
    finalize_labels,
    finalize_tertiary,
    freeze_population,
    prepare_full_primary,
    prepare_full_expansion,
    prepare_full_secondary,
    prepare_full_tertiary,
    score_models,
    validate_artifacts,
)
from .structured_rf_disagreement_audit import validate_review


DEFAULT_OUTPUT = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\forecast_eligibility_august_2026_temporal_holdout_v1"
)
FEATURE_ROOT = Path(r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\structured_metadata_rf_v1")
FORWARD_MODEL_ROOT = Path(r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\structured_metadata_rf_2025_to_2026_final_labels_v1")
REVERSE_MODEL_ROOT = Path(r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\structured_metadata_rf_2026_to_2025_final_labels_v1")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Blind August 2026 forecast-eligibility holdout audit")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in (
        "freeze", "collect-compact", "prepare-full-primary", "collect-full-primary",
        "prepare-full-expansion", "collect-full-expansion",
        "prepare-full-secondary", "finalize-labels", "prepare-full-tertiary",
        "finalize-tertiary", "score-models", "validate",
    ):
        item = sub.add_parser(name)
        item.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    review = sub.add_parser("validate-review")
    review.add_argument("--packet", type=Path, required=True)
    review.add_argument("--review", type=Path, required=True)
    review.add_argument("--full-text", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "freeze":
        load_env_files(discover_env_files(Path.cwd()))
        client = clickhouse_client()
        try:
            result = freeze_population(client=client, output_root=args.output_root)
        finally:
            client.close()
    elif args.command == "collect-compact":
        result = collect_compact(output_root=args.output_root)
    elif args.command == "prepare-full-primary":
        result = prepare_full_primary(output_root=args.output_root)
    elif args.command == "collect-full-primary":
        result = collect_full_primary(output_root=args.output_root)
    elif args.command == "prepare-full-expansion":
        result = prepare_full_expansion(output_root=args.output_root)
    elif args.command == "collect-full-expansion":
        result = collect_full_expansion(output_root=args.output_root)
    elif args.command == "prepare-full-secondary":
        result = prepare_full_secondary(output_root=args.output_root)
    elif args.command == "finalize-labels":
        result = finalize_labels(output_root=args.output_root)
    elif args.command == "prepare-full-tertiary":
        result = prepare_full_tertiary(output_root=args.output_root)
    elif args.command == "finalize-tertiary":
        result = finalize_tertiary(output_root=args.output_root)
    elif args.command == "score-models":
        result = score_models(
            output_root=args.output_root, feature_root=FEATURE_ROOT,
            forward_model_root=FORWARD_MODEL_ROOT, reverse_model_root=REVERSE_MODEL_ROOT,
        )
    elif args.command == "validate":
        result = validate_artifacts(output_root=args.output_root)
    else:
        result = validate_review(
            packet_path=args.packet, review_path=args.review, full_text=args.full_text
        )
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
