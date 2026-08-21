from __future__ import annotations

import argparse
from pathlib import Path

from .provider_context_evaluation import DEFAULT_CORRECTED_AUTHORITY_ROOT
from .provider_context_rule_audit import finalize_rule_audit, prepare_adjudication, prepare_rule_audit
from .provider_filter_analysis import DEFAULT_METADATA_ROOT


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare blind eligible-exception review for provider-context V2 rules.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--authority-root", type=Path, default=DEFAULT_CORRECTED_AUTHORITY_ROOT)
    prepare.add_argument("--metadata-root", type=Path, default=DEFAULT_METADATA_ROOT)
    prepare.add_argument("--article-features", type=Path, required=True)
    prepare.add_argument("--output-root", type=Path, required=True)
    adjudicate = subparsers.add_parser("prepare-adjudication")
    adjudicate.add_argument("--output-root", type=Path, required=True)
    adjudicate.add_argument("--review-one", type=Path, required=True)
    adjudicate.add_argument("--review-two", type=Path, required=True)
    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("--output-root", type=Path, required=True)
    finalize.add_argument("--review-one", type=Path, required=True)
    finalize.add_argument("--review-two", type=Path, required=True)
    finalize.add_argument("--adjudication", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        manifest = prepare_rule_audit(
            authority_root=args.authority_root,
            metadata_root=args.metadata_root,
            article_features=args.article_features,
            output_root=args.output_root,
        )
        print(
            f"{manifest['audit_version']} prepared | candidates={len(manifest['candidate_metrics'])} "
            f"eligible_exceptions={manifest['eligible_exception_articles']} output={args.output_root}"
        )
    elif args.command == "prepare-adjudication":
        report = prepare_adjudication(args.output_root, args.review_one, args.review_two)
        print(
            f"{report['audit_version']} adjudication | disagreements={report['disagreements']} "
            f"packet={report['adjudication_packet']}"
        )
    else:
        report = finalize_rule_audit(
            args.output_root, args.review_one, args.review_two, args.adjudication
        )
        print(
            f"{report['audit_version']} complete | articles={report['articles']} "
            f"final_labels={report['final_labels']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
