import argparse
from dataclasses import replace
from pathlib import Path

from research.text_intelligence.news_synthesis_v1.certification import (
    default_certification_config,
    initialize_workspace,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Initialize certification from current certified authority or an explicit bootstrap draft."
    )
    parser.add_argument("--bootstrap-draft", type=Path)
    args = parser.parse_args()
    config = default_certification_config()
    if args.bootstrap_draft:
        config = replace(config, draft_path=args.bootstrap_draft.resolve())
    manifest = initialize_workspace(config)
    print(
        f"NEWS SYNTHESIS V1 CERTIFICATION | review_packets={manifest['review_packets']:,} "
        f"certified={manifest['certified']:,} pending={manifest['pending']:,}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
