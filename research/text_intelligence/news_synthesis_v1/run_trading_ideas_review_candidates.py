from __future__ import annotations

import argparse
from pathlib import Path

from .trading_ideas_review_candidates import (
    DEFAULT_ARTICLE_FEATURES,
    DEFAULT_MISMATCH_CONTROLLER,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_PRIOR_REVIEW,
    run_trading_ideas_analysis,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Designate eligible trading-idea articles for blind review.")
    parser.add_argument("--article-features", type=Path, default=DEFAULT_ARTICLE_FEATURES)
    parser.add_argument("--mismatch-controller", type=Path, default=DEFAULT_MISMATCH_CONTROLLER)
    parser.add_argument("--prior-review", type=Path, default=DEFAULT_PRIOR_REVIEW)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args(argv)
    report = run_trading_ideas_analysis(
        article_features=args.article_features,
        mismatch_controller=args.mismatch_controller,
        prior_review=args.prior_review,
        output_root=args.output_root,
    )
    print(
        f"{report['analysis_version']} complete | articles={report['population']['trading_ideas_articles']:,} "
        f"eligible={report['population']['eligible']:,} "
        f"unreviewed={report['population']['unreviewed_eligible_candidates']:,} output={args.output_root}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
