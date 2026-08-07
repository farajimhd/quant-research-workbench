from __future__ import annotations

import argparse
import json

from .certification import default_certification_config
from .review_spec import compile_review_spec, materialize_review_spec
from .source_authority import discover_pairs, load_json


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replace incomplete migration-dependent review specs with complete source-bound specs."
    )
    parser.add_argument("--all", action="store_true", help="Materialize every reviewed specification.")
    parser.add_argument(
        "--allow-derived-changes",
        action="store_true",
        help="Permit current issuer-view or eligibility derivation to replace stale stored derivation.",
    )
    args = parser.parse_args()
    config = default_certification_config()
    articles = {
        article_path.stem: load_json(article_path)
        for _annotation_path, article_path, _collection in discover_pairs(config.collection_roots)
    }
    changed = 0
    derived_changes: list[dict[str, object]] = []
    for certified_path in sorted((config.output_root / "certified_labels").glob("*.json")):
        sample_id = certified_path.stem
        spec_path = config.output_root / "reviewed_specs" / f"{sample_id}.json"
        existing = load_json(spec_path)
        if not args.all and "envelope" in existing:
            continue
        certified = load_json(certified_path)
        spec = materialize_review_spec(
            articles[sample_id],
            certified,
            allow_derived_changes=args.allow_derived_changes,
        )
        rebuilt = compile_review_spec(articles[sample_id], spec)
        old_views = {
            str(row["entity_id"]): str(row["composite_sentiment"])
            for row in certified.get("issuer_views", [])
        }
        new_views = {
            str(row["entity_id"]): str(row["composite_sentiment"])
            for row in rebuilt.get("issuer_views", [])
        }
        old_eligibility = {
            f"{row['entity_id']}:{row['product']}": bool(row["eligible"])
            for row in certified.get("eligibility", [])
        }
        new_eligibility = {
            f"{row['entity_id']}:{row['product']}": bool(row["eligible"])
            for row in rebuilt.get("eligibility", [])
        }
        if old_views != new_views or old_eligibility != new_eligibility:
            derived_changes.append({
                "sample_id": sample_id,
                "issuer_views_before": old_views,
                "issuer_views_after": new_views,
                "eligibility_before": old_eligibility,
                "eligibility_after": new_eligibility,
            })
        temporary = spec_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        temporary.replace(spec_path)
        changed += 1
    print(f"MATERIALIZED REVIEW SPECS | changed={changed:,}")
    if derived_changes:
        print(f"DERIVED AUTHORITY CHANGES | records={len(derived_changes):,}")
        for row in derived_changes:
            print(
                f"CHANGED {row['sample_id']} | issuer_views="
                f"{row['issuer_views_before']} -> {row['issuer_views_after']} | "
                f"eligibility={row['eligibility_before']} -> {row['eligibility_after']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
