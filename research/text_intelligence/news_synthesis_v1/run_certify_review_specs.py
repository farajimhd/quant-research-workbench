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
from research.text_intelligence.news_synthesis_v1.review_spec import compile_review_spec
from research.text_intelligence.news_synthesis_v1.taxonomy_audit import discover_pairs, load_json


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile and certify manually authored V1-only review specifications.")
    parser.add_argument("input", nargs="?", type=Path, help="JSONL rows containing sample_id, review_notes, envelope, entities and atomic statements. Reads stdin when omitted.")
    parser.add_argument("--reviewer", default="Codex manual review")
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help=(
            "Certify independently valid rows and report every rejected row. "
            "Rejected specifications are never persisted."
        ),
    )
    args = parser.parse_args()
    config = default_certification_config()
    articles = {
        str(article["sample_id"]): article
        for _annotation, article_path, _collection in discover_pairs(config.collection_roots)
        for article in (load_json(article_path),)
    }
    reviews = []
    specs = []
    if args.input and args.input.is_dir():
        source_specs = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(args.input.glob("*.json"))]
        payload = ""
    else:
        payload = args.input.read_text(encoding="utf-8") if args.input else sys.stdin.read()
    stripped = payload.strip()
    if not args.input or not args.input.is_dir():
        if not stripped:
            raise RuntimeError("No review specifications were provided.")
        if stripped.startswith("["):
            parsed = json.loads(stripped)
            source_specs = parsed if isinstance(parsed, list) else [parsed]
        elif stripped.startswith("{") and "\n{" not in stripped:
            source_specs = [json.loads(stripped)]
        else:
            source_specs = [json.loads(line) for line in payload.splitlines() if line.strip()]
    if not source_specs:
        raise RuntimeError("No review specifications were provided.")
    rejected: list[tuple[str, str]] = []
    for spec in source_specs:
        if not isinstance(spec, dict):
            raise RuntimeError("Each review specification must be a JSON object.")
        sample_id = str(spec.get("sample_id", "<missing>"))
        try:
            if sample_id not in articles:
                raise RuntimeError(f"Unknown sample_id: {sample_id}")
            document = compile_review_spec(articles[sample_id], spec)
        except (KeyError, RuntimeError, TypeError, ValueError) as exc:
            if not args.continue_on_error:
                raise
            rejected.append((sample_id, str(exc)))
            continue
        specs.append(spec)
        reviews.append(
            {
                "sample_id": sample_id,
                "review_notes": str(spec["review_notes"]),
                "document": document,
            }
        )
    if not reviews:
        details = "; ".join(f"{sample_id}: {reason}" for sample_id, reason in rejected)
        raise RuntimeError(f"No valid review specifications were provided. {details}")
    outputs = certify_documents(config, reviews, reviewer=args.reviewer)
    spec_root = config.output_root / "reviewed_specs"
    spec_root.mkdir(parents=True, exist_ok=True)
    for spec in specs:
        target = spec_root / f"{spec['sample_id']}.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(target)
    audit_root = config.output_root / "certified_reviews"
    audit_root.mkdir(parents=True, exist_ok=True)
    for certified in outputs:
        target = audit_root / f"{certified['sample_id']}.md"
        temporary = target.with_suffix(".md.tmp")
        temporary.write_text(
            render_review_packet(articles[certified["sample_id"]], certified, certified=True),
            encoding="utf-8",
        )
        temporary.replace(target)
    print(f"CERTIFIED REVIEW SPECS | records={len(outputs):,} rejected={len(rejected):,}")
    for sample_id, reason in rejected:
        print(f"REJECTED {sample_id} | {reason}", file=sys.stderr)
    return 1 if rejected else 0


if __name__ == "__main__":
    raise SystemExit(main())
