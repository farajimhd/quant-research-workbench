from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research.text_intelligence.news_synthesis_v1.certification import (
    default_certification_config,
    render_review_packet,
)
from research.text_intelligence.news_synthesis_v1.contracts import canonical_json, sha256_json
from research.text_intelligence.news_synthesis_v1.migration import migrate_record
from research.text_intelligence.news_synthesis_v1.registry import ConceptRegistry
from research.text_intelligence.news_synthesis_v1.source_authority import (
    discover_pairs,
    load_json,
    sha256_file,
)


def default_output_root() -> Path:
    runtime_root = Path(os.environ.get("QW_MLOPS_ROOT", "D:/TradingML")) / "runtimes"
    return runtime_root / "text_intelligence" / "news_synthesis_v1" / "manual_conversion_v2"


def prepare_pending_conversion(output_root: Path | None = None) -> dict[str, Any]:
    """Prepare non-authoritative review candidates for every uncertified item.

    Existing certified labels and reviewed specifications are read as the
    immutable exclusion authority. This function never writes into the manual
    certification workspace and never certifies a candidate.
    """
    certification = default_certification_config()
    target_root = output_root or default_output_root()
    certified_root = certification.output_root / "certified_labels"
    certified_ids = {path.stem for path in certified_root.glob("*.json")}
    pairs = discover_pairs(certification.collection_roots)
    if len(pairs) != certification.expected_articles:
        raise RuntimeError(
            f"Expected {certification.expected_articles} source pairs, found {len(pairs)}"
        )

    registry = ConceptRegistry.load()
    candidates: list[dict[str, Any]] = []
    source_rows: list[dict[str, str]] = []
    counts = Counter()
    issue_counts = Counter()
    for annotation_path, article_path, collection in pairs:
        annotation = load_json(annotation_path)
        article = load_json(article_path)
        sample_id = str(article["sample_id"])
        if sample_id in certified_ids:
            continue
        candidate, audit = migrate_record(annotation, article, registry)
        candidates.append(candidate)
        counts["documents"] += 1
        counts["issuer_units"] += len(annotation.get("issuer_units", []))
        counts["statements"] += len(candidate["statements"])
        counts["fallback_statements"] += sum(
            row["concept_leaf"] == registry.fallback_leaf
            for row in candidate["statements"]
        )
        if any(row["concept_leaf"] == registry.fallback_leaf for row in candidate["statements"]):
            counts["fallback_documents"] += 1
        if candidate["quality_flags"]:
            counts["quality_flag_documents"] += 1
        issue_counts.update(audit["issues"])
        source_rows.append(
            {
                "sample_id": sample_id,
                "collection": collection,
                "annotation_sha256": sha256_file(annotation_path),
                "article_sha256": sha256_file(article_path),
            }
        )

    candidates.sort(key=lambda row: _sample_sort_key(str(row["sample_id"])))
    source_rows.sort(key=lambda row: _sample_sort_key(row["sample_id"]))
    expected_pending = certification.expected_articles - len(certified_ids)
    if len(candidates) != expected_pending:
        raise RuntimeError(
            f"Pending authority mismatch: candidates={len(candidates)} expected={expected_pending}"
        )

    target_root.mkdir(parents=True, exist_ok=True)
    packet_root = target_root / "review_packets"
    packet_root.mkdir(parents=True, exist_ok=True)
    candidate_path = target_root / "pending_candidates.jsonl"
    candidate_tmp = candidate_path.with_suffix(".jsonl.tmp")
    with candidate_tmp.open("w", encoding="utf-8", newline="\n") as handle:
        for candidate in candidates:
            handle.write(canonical_json(candidate) + "\n")
    candidate_tmp.replace(candidate_path)

    article_by_id = {
        str(load_json(article_path)["sample_id"]): load_json(article_path)
        for _annotation_path, article_path, _collection in pairs
    }
    packet_hashes = []
    for candidate in candidates:
        sample_id = str(candidate["sample_id"])
        packet_path = packet_root / f"{sample_id}.md"
        temporary = packet_path.with_suffix(".md.tmp")
        temporary.write_text(
            render_review_packet(article_by_id[sample_id], candidate),
            encoding="utf-8",
        )
        temporary.replace(packet_path)
        packet_hashes.append({"sample_id": sample_id, "sha256": sha256_file(packet_path)})

    manifest: dict[str, Any] = {
        "conversion_version": "news_synthesis_v1_manual_conversion_v2",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_authority_articles": certification.expected_articles,
        "excluded_certified_articles": len(certified_ids),
        "pending_articles": len(candidates),
        "counts": dict(sorted(counts.items())),
        "top_review_issues": [
            {"issue": issue, "documents_or_units": count}
            for issue, count in issue_counts.most_common(100)
        ],
        "certified_ids_sha256": sha256_json(sorted(certified_ids)),
        "source_rows_sha256": sha256_json(source_rows),
        "candidate_file_sha256": sha256_file(candidate_path),
        "review_packets_sha256": sha256_json(packet_hashes),
    }
    manifest["manifest_sha256"] = sha256_json(manifest)
    manifest_path = target_root / "manifest.json"
    temporary = manifest_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    temporary.replace(manifest_path)
    return manifest


def _sample_sort_key(value: str) -> tuple[str, int, str]:
    prefix = value.rstrip("0123456789")
    suffix = value[len(prefix):]
    return prefix, int(suffix) if suffix else -1, value
