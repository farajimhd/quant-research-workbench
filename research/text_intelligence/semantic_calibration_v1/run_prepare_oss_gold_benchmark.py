from __future__ import annotations

import argparse
from pathlib import Path

from .oss_gold_benchmark import prepare_bundle


DEFAULT_BASE = Path(
    r"D:\TradingML\runtimes\text_intelligence\semantic_calibration_v1"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare the exact frozen 100-article OSS benchmark bundle."
    )
    parser.add_argument("--collection-root", type=Path, default=DEFAULT_BASE / "news_1000")
    parser.add_argument(
        "--selection-path",
        type=Path,
        default=DEFAULT_BASE / "openai_gold_100_v5" / "selection.json",
    )
    parser.add_argument(
        "--openai-comparison-path",
        type=Path,
        default=DEFAULT_BASE / "openai_gold_100_v5" / "comparison.json",
    )
    parser.add_argument(
        "--shared-root", type=Path, default=DEFAULT_BASE / "oss_gold_100_v2" / "shared"
    )
    args = parser.parse_args(argv)
    manifest = prepare_bundle(
        collection_root=args.collection_root,
        selection_path=args.selection_path,
        openai_comparison_path=(
            args.openai_comparison_path if args.openai_comparison_path.exists() else None
        ),
        shared_root=args.shared_root,
    )
    print(
        f"PREPARED | rows={manifest['sample_rows']} "
        f"bundle_sha256={manifest['bundle_sha256']} shared={args.shared_root}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
