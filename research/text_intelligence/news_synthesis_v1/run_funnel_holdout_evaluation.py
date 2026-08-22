from __future__ import annotations

import argparse
from pathlib import Path

from research.text_intelligence.llm_issuer_labeling_v3.codex_2026 import clickhouse_client

from .funnel_holdout_evaluation import DEFAULT_MARKET_CAP_FEATURES, run_final_evaluation


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="One-time final News Synthesis funnel holdout evaluation")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--database", default="q_live")
    parser.add_argument("--output-name", default="final_evaluation_v5_market_cap_regression")
    parser.add_argument("--market-cap-features", type=Path, default=DEFAULT_MARKET_CAP_FEATURES)
    args = parser.parse_args(argv)
    client = clickhouse_client()
    try:
        report = run_final_evaluation(
            root=args.root,
            client=client,
            database=args.database,
            output_name=args.output_name,
            market_cap_features=args.market_cap_features,
        )
    finally:
        client.close()
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
