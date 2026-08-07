from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


from .source_authority import (
    SOURCE_COLLECTION_NAMES,
    default_source_authority_config,
    discover_pairs,
    load_json,
    sha256_file,
)


DEFAULT_COLLECTIONS = SOURCE_COLLECTION_NAMES


@dataclass(frozen=True, slots=True)
class AuditConfig:
    collection_roots: tuple[Path, ...]
    output_root: Path
    expected_articles: int = 2_000


def default_config() -> AuditConfig:
    authority = default_source_authority_config()
    return AuditConfig(
        collection_roots=authority.collection_roots,
        output_root=(
            authority.runtime_root
            / "text_intelligence"
            / "news_synthesis_v1"
            / "taxonomy_audit_2000"
        ),
    )


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def iter_leaf_values(value: Any, path: str = "$") -> Iterable[tuple[str, Any]]:
    if isinstance(value, Mapping):
        for key in sorted(value):
            yield from iter_leaf_values(value[key], f"{path}.{key}")
    elif isinstance(value, list):
        if not value:
            yield f"{path}[]", "<empty>"
        else:
            for item in value:
                yield from iter_leaf_values(item, f"{path}[]")
    else:
        yield path, value


def _counter_rows(counter: Counter[Any]) -> list[dict[str, Any]]:
    return [
        {"value": str(value), "count": count}
        for value, count in sorted(counter.items(), key=lambda pair: (-pair[1], str(pair[0])))
    ]


def _cross_tab(rows: Iterable[tuple[Any, Any]]) -> list[dict[str, Any]]:
    counter = Counter((str(left), str(right)) for left, right in rows)
    return [
        {"left": left, "right": right, "count": count}
        for (left, right), count in sorted(
            counter.items(), key=lambda pair: (-pair[1], pair[0][0], pair[0][1])
        )
    ]


def _percentage(count: int, total: int) -> float:
    return round(100.0 * count / total, 3) if total else 0.0


def _validate_pair(annotation: Mapping[str, Any], article: Mapping[str, Any], label: str) -> None:
    for key in ("sample_id", "source_id", "source_timestamp", "source_text_sha256"):
        if annotation.get(key) != article.get(key):
            raise RuntimeError(f"Gold identity mismatch for {label}: {key}")


def audit_gold_authority(config: AuditConfig) -> dict[str, Any]:
    pairs = discover_pairs(config.collection_roots)
    if len(pairs) != config.expected_articles:
        raise RuntimeError(f"Expected {config.expected_articles} paired articles, found {len(pairs)}")

    annotations: list[dict[str, Any]] = []
    articles: list[dict[str, Any]] = []
    source_files: list[dict[str, str]] = []
    for annotation_path, article_path, collection in pairs:
        annotation = load_json(annotation_path)
        article = load_json(article_path)
        _validate_pair(annotation, article, annotation_path.stem)
        annotation["_collection"] = collection
        article["_collection"] = collection
        annotations.append(annotation)
        articles.append(article)
        source_files.extend(
            (
                {"path": str(annotation_path), "sha256": sha256_file(annotation_path)},
                {"path": str(article_path), "sha256": sha256_file(article_path)},
            )
        )

    sample_ids = [str(row["sample_id"]) for row in annotations]
    source_ids = [str(row["source_id"]) for row in annotations]
    if len(set(sample_ids)) != len(sample_ids):
        raise RuntimeError("Duplicate sample_id across gold collections")
    if len(set(source_ids)) != len(source_ids):
        raise RuntimeError("Duplicate source_id across gold collections")

    field_values: dict[str, Counter[str]] = defaultdict(Counter)
    field_types: dict[str, Counter[str]] = defaultdict(Counter)
    for annotation in annotations:
        for path, value in iter_leaf_values({k: v for k, v in annotation.items() if k != "_collection"}):
            field_types[path][type(value).__name__] += 1
            serialized = canonical_json(value)
            field_values[path][serialized] += 1

    inventory: list[dict[str, Any]] = []
    for path in sorted(field_values):
        values = field_values[path]
        row: dict[str, Any] = {
            "path": path,
            "observations": sum(values.values()),
            "unique_values": len(values),
            "types": dict(sorted(field_types[path].items())),
        }
        if len(values) <= 200:
            row["values"] = _counter_rows(values)
        inventory.append(row)

    issuer_units = [
        (annotation, unit)
        for annotation in annotations
        for unit in annotation.get("issuer_units", [])
        if isinstance(unit, dict)
    ]
    ticker_dispositions = [
        disposition
        for annotation in annotations
        for disposition in annotation.get("ticker_dispositions", [])
        if isinstance(disposition, dict)
    ]

    article_dimensions = {
        field: _counter_rows(Counter(str(row.get(field, "<missing>")) for row in annotations))
        for field in ("_collection", "extraction_decision", "content_role", "source_origin")
    }
    unit_fields = (
        "issuer_role",
        "evidence_scope",
        "modality",
        "time_orientation",
        "semantic_direction",
        "forecast_trigger_eligible",
        "reaction_evaluation_eligible",
        "issuer_history_context_eligible",
        "analyst_context_eligible",
        "analyst_evaluation_eligible",
    )
    issuer_dimensions = {
        field: _counter_rows(Counter(str(unit.get(field, "<missing>")) for _, unit in issuer_units))
        for field in unit_fields
    }
    concept_counter = Counter(
        str(concept)
        for _, unit in issuer_units
        for concept in unit.get("event_concepts", [])
    )
    disposition_counter = Counter(str(row.get("disposition", "<missing>")) for row in ticker_dispositions)

    decision_unit_issues = []
    for annotation in annotations:
        decision = annotation.get("extraction_decision")
        units = annotation.get("issuer_units", [])
        if decision == "labeled" and not units:
            decision_unit_issues.append({"sample_id": annotation["sample_id"], "issue": "labeled_without_units"})
        elif decision != "labeled" and units:
            decision_unit_issues.append({"sample_id": annotation["sample_id"], "issue": "non_labeled_with_units"})

    eligibility_combinations = Counter(
        (
            bool(unit.get("forecast_trigger_eligible")),
            bool(unit.get("reaction_evaluation_eligible")),
            bool(unit.get("issuer_history_context_eligible")),
        )
        for _, unit in issuer_units
    )
    eligibility_rows = [
        {
            "forecast": combo[0],
            "reaction": combo[1],
            "history": combo[2],
            "count": count,
            "percentage": _percentage(count, len(issuer_units)),
        }
        for combo, count in sorted(eligibility_combinations.items(), key=lambda pair: (-pair[1], pair[0]))
    ]

    same_named_dimensions = {
        "automated_summary_as_content_role": sum(row.get("content_role") == "automated_summary" for row in annotations),
        "automated_summary_as_source_origin": sum(row.get("source_origin") == "automated_summary" for row in annotations),
        "automated_summary_in_both": sum(
            row.get("content_role") == "automated_summary" and row.get("source_origin") == "automated_summary"
            for row in annotations
        ),
    }
    mixed_units = {
        field: sum(unit.get(field) == "mixed" for _, unit in issuer_units)
        for field in ("modality", "time_orientation", "semantic_direction")
    }
    mentioned_subject_units = sum(unit.get("issuer_role") == "mentioned_subject" for _, unit in issuer_units)
    no_evidence_units = sum(not unit.get("evidence_spans") and not unit.get("evidence_quotes") for _, unit in issuer_units)
    analyst_without_opinion = sum(
        annotation.get("content_role") == "analyst_event"
        and not any(unit.get("analyst_opinions") for unit in annotation.get("issuer_units", []))
        for annotation in annotations
    )

    result: dict[str, Any] = {
        "audit_version": "news_synthesis_v1_taxonomy_audit_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": {
            "collections": [str(path) for path in config.collection_roots],
            "articles": len(annotations),
            "issuer_units": len(issuer_units),
            "ticker_dispositions": len(ticker_dispositions),
            "unique_sample_ids": len(set(sample_ids)),
            "unique_source_ids": len(set(source_ids)),
            "source_files_sha256": sha256_bytes(canonical_json(source_files).encode("utf-8")),
        },
        "article_dimensions": article_dimensions,
        "issuer_dimensions": issuer_dimensions,
        "event_concepts": {
            "unique": len(concept_counter),
            "observations": sum(concept_counter.values()),
            "values": _counter_rows(concept_counter),
        },
        "ticker_dispositions": _counter_rows(disposition_counter),
        "cross_tabs": {
            "content_role_by_source_origin": _cross_tab(
                (row.get("content_role"), row.get("source_origin")) for row in annotations
            ),
            "content_role_by_extraction_decision": _cross_tab(
                (row.get("content_role"), row.get("extraction_decision")) for row in annotations
            ),
            "content_role_by_unit_direction": _cross_tab(
                (annotation.get("content_role"), unit.get("semantic_direction"))
                for annotation, unit in issuer_units
            ),
            "source_origin_by_unit_direction": _cross_tab(
                (annotation.get("source_origin"), unit.get("semantic_direction"))
                for annotation, unit in issuer_units
            ),
        },
        "contract_findings": {
            "decision_unit_inconsistencies": decision_unit_issues,
            "same_named_dimensions": same_named_dimensions,
            "mixed_unit_values": mixed_units,
            "mentioned_subject_units": mentioned_subject_units,
            "issuer_units_without_evidence": no_evidence_units,
            "analyst_articles_without_structured_opinion": analyst_without_opinion,
            "eligibility_combinations": eligibility_rows,
        },
        "field_inventory": inventory,
    }
    result["result_sha256"] = sha256_bytes(canonical_json(result).encode("utf-8"))
    return result


def _rows_by_value(rows: Sequence[Mapping[str, Any]]) -> str:
    return "\n".join(f"| {row['value']} | {row['count']:,} |" for row in rows)


def render_markdown(result: Mapping[str, Any]) -> str:
    source = result["source"]
    article = result["article_dimensions"]
    findings = result["contract_findings"]
    lines = [
        "# News Synthesis V1: 2,000-Gold Taxonomy Audit",
        "",
        f"Authority: **{source['articles']:,} articles**, **{source['issuer_units']:,} issuer units**, "
        f"**{source['ticker_dispositions']:,} ticker dispositions**.",
        "",
        "## Existing article dimensions",
        "",
    ]
    for field in ("extraction_decision", "content_role", "source_origin"):
        lines.extend((f"### `{field}`", "", "| Value | Count |", "|---|---:|", _rows_by_value(article[field]), ""))
    lines.extend(
        (
            "## Measured contract defects and overlaps",
            "",
            f"- Decision/unit inconsistencies: **{len(findings['decision_unit_inconsistencies']):,}**.",
            f"- `automated_summary` used as content role: **{findings['same_named_dimensions']['automated_summary_as_content_role']:,}**.",
            f"- `automated_summary` used as source origin: **{findings['same_named_dimensions']['automated_summary_as_source_origin']:,}**.",
            f"- `automated_summary` present in both dimensions: **{findings['same_named_dimensions']['automated_summary_in_both']:,}**.",
            f"- `mentioned_subject` issuer units: **{findings['mentioned_subject_units']:,}**.",
            f"- Issuer units without evidence span or quote: **{findings['issuer_units_without_evidence']:,}**.",
            f"- Analyst articles without a structured analyst opinion: **{findings['analyst_articles_without_structured_opinion']:,}**.",
            "",
            "### Eligibility combinations",
            "",
            "| Forecast | Reaction | History | Units | Share |",
            "|---|---|---|---:|---:|",
        )
    )
    for row in findings["eligibility_combinations"]:
        lines.append(
            f"| {row['forecast']} | {row['reaction']} | {row['history']} | {row['count']:,} | {row['percentage']:.3f}% |"
        )
    lines.extend(
        (
            "",
            "## Interpretation boundary",
            "",
            "This audit inventories and measures the existing contract. It does not mutate gold records, "
            "approve a replacement taxonomy, or infer market reaction from language.",
            "",
            f"Result SHA-256: `{result['result_sha256']}`",
            "",
        )
    )
    return "\n".join(lines)


def write_audit(result: Mapping[str, Any], output_root: Path) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    json_path = output_root / "taxonomy_audit.json"
    markdown_path = output_root / "taxonomy_audit.md"
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(result), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    defaults = default_config()
    parser = argparse.ArgumentParser(description="Audit the 2,000-news gold taxonomy without changing it.")
    parser.add_argument("--collection-root", action="append", type=Path, dest="collection_roots")
    parser.add_argument("--output-root", type=Path, default=defaults.output_root)
    parser.add_argument("--expected-articles", type=int, default=defaults.expected_articles)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    defaults = default_config()
    config = AuditConfig(
        collection_roots=tuple(args.collection_roots or defaults.collection_roots),
        output_root=args.output_root,
        expected_articles=args.expected_articles,
    )
    print(
        "NEWS SYNTHESIS V1 TAXONOMY AUDIT | "
        f"collections={len(config.collection_roots)} expected={config.expected_articles:,} "
        f"output={config.output_root}"
    )
    result = audit_gold_authority(config)
    write_audit(result, config.output_root)
    print(
        f"COMPLETED | articles={result['source']['articles']:,} "
        f"issuer_units={result['source']['issuer_units']:,} sha256={result['result_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
