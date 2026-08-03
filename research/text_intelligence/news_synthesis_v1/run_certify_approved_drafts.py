from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from research.text_intelligence.news_synthesis_v1.certification import (
    certify_documents,
    default_certification_config,
    render_review_packet,
)
from research.text_intelligence.news_synthesis_v1.review_spec import compile_approved_draft
from research.text_intelligence.news_synthesis_v1.taxonomy_audit import discover_pairs, load_json


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Certify manually reviewed V1 drafts that require no semantic correction."
    )
    parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        help="JSONL rows containing sample_id and review_notes. Reads stdin when omitted.",
    )
    parser.add_argument("--reviewer", default="Codex manual review")
    args = parser.parse_args()
    payload = args.input.read_text(encoding="utf-8") if args.input else sys.stdin.read()
    if not payload.strip():
        raise RuntimeError("No approved-draft reviews were provided.")
    approvals = [json.loads(line) for line in payload.splitlines() if line.strip()]
    if any(set(row) != {"sample_id", "review_notes"} for row in approvals):
        raise RuntimeError("Each approval row must contain only sample_id and review_notes.")

    config = default_certification_config()
    articles = {
        str(article["sample_id"]): article
        for _annotation, article_path, _collection in discover_pairs(config.collection_roots)
        for article in (load_json(article_path),)
    }
    drafts = {}
    with config.draft_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            drafts[str(row["sample_id"])] = row

    reviews = []
    for approval in approvals:
        sample_id = str(approval["sample_id"])
        if sample_id not in articles or sample_id not in drafts:
            raise RuntimeError(f"Unknown approved-draft sample: {sample_id}")
        reviews.append(
            {
                "sample_id": sample_id,
                "review_notes": str(approval["review_notes"]),
                "document": compile_approved_draft(articles[sample_id], drafts[sample_id]),
            }
        )

    outputs = certify_documents(config, reviews, reviewer=args.reviewer)
    spec_root = config.output_root / "reviewed_specs"
    audit_root = config.output_root / "certified_reviews"
    spec_root.mkdir(parents=True, exist_ok=True)
    audit_root.mkdir(parents=True, exist_ok=True)
    for approval, certified in zip(approvals, outputs, strict=True):
        target = spec_root / f"{certified['sample_id']}.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                {
                    "sample_id": certified["sample_id"],
                    "review_notes": approval["review_notes"],
                    "approval": "manually_reviewed_v1_draft_without_semantic_changes",
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)
        audit_target = audit_root / f"{certified['sample_id']}.md"
        audit_temporary = audit_target.with_suffix(".md.tmp")
        audit_temporary.write_text(
            render_review_packet(articles[certified["sample_id"]], certified, certified=True),
            encoding="utf-8",
        )
        audit_temporary.replace(audit_target)
    print(f"CERTIFIED APPROVED V1 DRAFTS | records={len(outputs):,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
