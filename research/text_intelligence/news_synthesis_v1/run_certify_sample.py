from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from research.text_intelligence.news_synthesis_v1.certification import (
    certify_document,
    default_certification_config,
    refresh_certification_state,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify one fully reviewed News Synthesis V1 document.")
    parser.add_argument("--sample-id", required=True)
    parser.add_argument("--reviewer", default="Codex manual review")
    parser.add_argument("--review-notes", required=True)
    parser.add_argument("--input", type=Path, help="V1-only draft JSON. Reads stdin when omitted.")
    args = parser.parse_args()
    document = json.loads(args.input.read_text(encoding="utf-8") if args.input else sys.stdin.read())
    config = default_certification_config()
    certified = certify_document(
        config,
        args.sample_id,
        document,
        reviewer=args.reviewer,
        review_notes=args.review_notes,
    )
    manifest = refresh_certification_state(config)
    print(
        f"CERTIFIED {args.sample_id} | document_sha256={manifest['ledger_sha256']} "
        f"total={manifest['certified']}/{manifest['expected_articles']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
