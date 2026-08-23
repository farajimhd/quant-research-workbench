from __future__ import annotations

import argparse
import json
from pathlib import Path

from research.mlops.env import discover_env_files, load_env_files
from research.text_intelligence.llm_issuer_labeling_v3.codex_2026 import clickhouse_client

from .structured_metadata_rf import (
    DEFAULT_FEATURES,
    DEFAULT_MARKET_CAP,
    DEFAULT_OUTPUT,
    build_contract_and_matrices,
    train_and_evaluate,
    validate_artifacts,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Structured metadata-only Random Forest for news eligibility")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--features", type=Path, default=DEFAULT_FEATURES)
    build.add_argument("--market-cap", type=Path, default=DEFAULT_MARKET_CAP)
    build.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    build.add_argument("--database", default="q_live")
    train = subparsers.add_parser("train-evaluate")
    train.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    if args.command == "build":
        load_env_files(discover_env_files(Path.cwd()))
        client = clickhouse_client()
        try:
            result = build_contract_and_matrices(
                client=client, feature_path=args.features, market_cap_path=args.market_cap,
                output_root=args.output_root, database=args.database,
            )
        finally:
            client.close()
    elif args.command == "train-evaluate":
        result = train_and_evaluate(output_root=args.output_root)
    else:
        result = validate_artifacts(output_root=args.output_root)
    summary_keys = (
        "contract_version", "model_version", "status", "feature_count",
        "category_catalog_rows", "matrix", "selected_threshold", "test",
        "disagreements", "train_seconds", "train_rows", "test_rows", "features",
    )
    summary = {key: result[key] for key in summary_keys if key in result}
    print(json.dumps(summary, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
