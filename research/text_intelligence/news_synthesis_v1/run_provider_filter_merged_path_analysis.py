from __future__ import annotations

import argparse
from pathlib import Path

from .provider_filter_merged_path_analysis import (
    DEFAULT_ARTICLE_FEATURES,
    DEFAULT_BASELINE_ARTICLE_FEATURES,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_RESIDUAL_PATHS,
    DEFAULT_SEMANTIC_PATHS,
    run_merged_path_analysis,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Merge the 709 semantic paths with 423 residual candidates and refresh their statistics."
    )
    parser.add_argument("--article-features", type=Path, default=DEFAULT_ARTICLE_FEATURES)
    parser.add_argument(
        "--baseline-article-features", type=Path, default=DEFAULT_BASELINE_ARTICLE_FEATURES
    )
    parser.add_argument("--semantic-paths", type=Path, default=DEFAULT_SEMANTIC_PATHS)
    parser.add_argument("--residual-paths", type=Path, default=DEFAULT_RESIDUAL_PATHS)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args(argv)
    report = run_merged_path_analysis(
        baseline_article_features=args.baseline_article_features,
        article_features=args.article_features,
        semantic_paths=args.semantic_paths,
        residual_paths=args.residual_paths,
        output_root=args.output_root,
    )
    print(
        f"{report['analysis_version']} complete | "
        f"paths={report['catalog']['merged_unique_paths']:,} "
        f"articles={report['population']['articles']:,} output={args.output_root}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
