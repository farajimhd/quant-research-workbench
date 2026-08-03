from __future__ import annotations

import argparse
import json
from pathlib import Path

from research.text_intelligence.news_synthesis_v1.certification import (
    certify_documents,
    default_certification_config,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify a reviewed JSONL batch of News Synthesis V1 documents.")
    parser.add_argument("input", type=Path, help="JSONL rows with sample_id, review_notes and document.")
    parser.add_argument("--reviewer", default="Codex manual review")
    args = parser.parse_args()
    reviews = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines() if line.strip()]
    outputs = certify_documents(default_certification_config(), reviews, reviewer=args.reviewer)
    print(f"CERTIFIED BATCH | records={len(outputs):,} first={outputs[0]['sample_id'] if outputs else 'none'} last={outputs[-1]['sample_id'] if outputs else 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
