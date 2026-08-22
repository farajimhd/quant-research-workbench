from __future__ import annotations

import argparse
from pathlib import Path

from .provider_filter_residual_analysis import (
    DEFAULT_ARTICLE_FEATURES,
    DEFAULT_MISMATCH_CONTROLLER,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_SEMANTIC_LABELS,
    run_residual_analysis,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Discover prevalent metadata paths in articles unmatched by the prior 709 candidates."
    )
    parser.add_argument("--article-features", type=Path, default=DEFAULT_ARTICLE_FEATURES)
    parser.add_argument("--semantic-labels", type=Path, default=DEFAULT_SEMANTIC_LABELS)
    parser.add_argument("--mismatch-controller", type=Path, default=DEFAULT_MISMATCH_CONTROLLER)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args(argv)
    report = run_residual_analysis(
        article_features=args.article_features,
        semantic_labels=args.semantic_labels,
        mismatch_controller=args.mismatch_controller,
        output_root=args.output_root,
    )
    print(
        f"{report['analysis_version']} complete | "
        f"residual={report['population']['residual_articles']:,} "
        f"features={report['residual_feature_count']:,} "
        f"new_paths={report['new_candidate_path_count']:,} output={args.output_root}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
