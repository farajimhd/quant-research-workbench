from __future__ import annotations

import argparse
from pathlib import Path

from .benchmark_revalidation import run_revalidation


DEFAULT_BASE = Path(
    r"D:\TradingML\runtimes\text_intelligence\semantic_calibration_v1"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Revalidate stored V4/V1 outputs under corrected candidate contract V2."
    )
    parser.add_argument("--collection-root", type=Path, default=DEFAULT_BASE / "news_1000")
    parser.add_argument(
        "--selection-path",
        type=Path,
        default=DEFAULT_BASE / "openai_gold_100_v4" / "selection.json",
    )
    parser.add_argument(
        "--openai-runtime",
        type=Path,
        default=DEFAULT_BASE / "openai_gold_100_v4",
    )
    parser.add_argument(
        "--oss-runtime",
        type=Path,
        default=DEFAULT_BASE / "oss_gold_100_v1",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_BASE / "gold_candidate_revalidation_v1",
    )
    args = parser.parse_args(argv)
    report = run_revalidation(
        collection_root=args.collection_root,
        selection_path=args.selection_path,
        openai_runtime=args.openai_runtime,
        oss_runtime=args.oss_runtime,
        output_root=args.output_root,
    )
    print(f"COMPLETED | report={report}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
