from __future__ import annotations

import argparse
import sys
from pathlib import Path

from research.mlops.paths import MLOpsPathConfig

from .manual_audit_packet import (
    audit_path,
    render_compact_manual_review_packet,
    render_bounded_manual_review_packet,
    render_manual_review_digest,
    render_manual_review_scan,
    render_manual_review_packet,
)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    runtime = MLOpsPathConfig.from_env().runtimes_root
    default_root = (
        runtime
        / "text_intelligence"
        / "semantic_calibration_v1"
        / "news_acceptance_200_v4_reviewed"
        / "candidate21_article_audits"
        / "articles"
    )
    parser = argparse.ArgumentParser(
        description="Print exact review-critical sections from News audit Markdown files."
    )
    parser.add_argument("sample_ids", nargs="+", help="Sample IDs such as N1301 N1302")
    parser.add_argument("--full", action="store_true", help="Include duplicate retained text lanes.")
    parser.add_argument(
        "--bounded",
        action="store_true",
        help="Bound source text for navigation; open full packets for ambiguous cases.",
    )
    parser.add_argument("--source-chars", type=int, default=1_600)
    parser.add_argument("--digest", action="store_true", help="Print a dense first-pass audit digest.")
    parser.add_argument("--scan", action="store_true", help="Print the densest complete-comparison scan view.")
    parser.add_argument("--no-trace", action="store_true", help="Omit the V9 trace for first-pass reading.")
    parser.add_argument("--article-root", type=Path, default=default_root)
    args = parser.parse_args(argv)
    for index, sample_id in enumerate(args.sample_ids):
        if index:
            print("\n" + "=" * 100 + "\n")
        path = audit_path(args.article_root, sample_id.upper())
        if args.full:
            print(render_manual_review_packet(path))
        elif args.scan:
            print(render_manual_review_scan(path, source_chars=args.source_chars))
        elif args.digest:
            print(render_manual_review_digest(path, source_chars=args.source_chars))
        elif args.bounded:
            print(
                render_bounded_manual_review_packet(
                    path,
                    source_chars=args.source_chars,
                    include_trace=not args.no_trace,
                )
            )
        else:
            print(render_compact_manual_review_packet(path, include_trace=not args.no_trace))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
