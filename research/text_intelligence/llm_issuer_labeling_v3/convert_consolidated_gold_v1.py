from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .pipeline import _concept_tags
from .schema_v4 import (
    SCHEMA_VERSION,
    canonicalize_output,
    derive_article_forecast_eligible,
    validate_output,
)


DEFAULT_INPUT_ROOT = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\gold_certified_news_labels_consolidated_v1"
)
DEFAULT_OUTPUT_ROOT = Path(
    r"D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4\legacy_consolidated_gold_conversion_v1"
)
DATASET_VERSION = "llm_issuer_labels_v4_legacy_consolidated_conversion_v1"
SOL_ELIGIBILITY_UNREVIEWED = frozenset(
    {
        "sol_teacher_forecast_reviewed_gold_v2",
        "sol_teacher_forecast_sealed_test_v1",
    }
)


def _report_text(report: Mapping[str, Any]) -> str:
    population = report["population"]
    conversion = report["conversion"]
    nulls = conversion["legacy_null_counts"]
    warnings = conversion["eligibility_authority_warnings"]
    warning_count = sum(int(value) for value in warnings.values())
    return "\n".join(
        (
            "# Legacy consolidated gold conversion V1",
            "",
            "This is a schema conversion of the latest confirmed consolidated legacy authority. "
            "It is **not** a semantic relabeling and does not repair incorrect legacy labels.",
            "",
            "## Population",
            "",
            f"- Articles: {population['articles']:,}",
            f"- Issuer units: {population['issuer_units']:,}",
            f"- Article eligible: {population['article_forecast_eligibility']['eligible']:,}",
            f"- Article ineligible: {population['article_forecast_eligibility']['ineligible']:,}",
            "",
            "## Schema changes",
            "",
            "- Removed `evidence_sentence_ids`.",
            "- Added `article_forecast_eligible`, derived as any issuer with "
            "`forecast_relevance_probability >= 0.5`.",
            "- Categorical legacy eligibility and sentiment are encoded as endpoint values.",
            "- Null means the legacy authority did not establish that field; it never means zero.",
            "",
            "## Authority limitations",
            "",
            f"- Sol-derived articles with inherited, unreviewed eligibility warning: {warning_count:,}",
            f"- Missing issuer names: {nulls.get('issuer_name', 0):,}",
            f"- Missing identity-confidence probabilities: {nulls.get('identity_confidence_probability', 0):,}",
            f"- Missing event-tag authority: {nulls.get('event_tags', 0):,}",
            f"- Missing implication probabilities: {nulls.get('positive_implication_probability', 0):,}",
            f"- Missing roles, time scope, and claim source: {nulls.get('issuer_roles', 0):,} each",
            "",
            "The converted data therefore clarifies what the legacy authorities actually contain, "
            "but it must not be described as fully populated V4 gold.",
            "",
        )
    )


def _read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sentiment_probabilities(sentiment: str) -> tuple[float | None, float | None]:
    return {
        "positive": (1.0, 0.0),
        "negative": (0.0, 1.0),
        "mixed": (1.0, 1.0),
        "neutral": (0.0, 0.0),
        "not_applicable": (None, None),
        "unknown": (None, None),
    }[sentiment]


def convert_unit(unit: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    sentiment = str(unit["sentiment"])
    positive, negative = _sentiment_probabilities(sentiment)
    concepts = list(unit.get("concepts") or [])
    ticker = str(unit.get("ticker") or "").strip().upper() or None
    entity_id = str(unit.get("entity_id") or "").strip()
    legacy_identity_fallback = entity_id if not ticker and entity_id else None
    issuer = {
        # The legacy consolidated authority generally has no canonical issuer
        # name. Keep it null unless a no-ticker unit would otherwise be
        # indistinguishable; in that case retain the authority-local entity ID.
        "issuer_name": legacy_identity_fallback,
        "ticker": ticker,
        "exchange": None,
        "identity_source": "metadata",
        "identity_confidence_probability": None,
        "forecast_relevance_probability": (
            1.0 if unit["forecast_eligibility"] == "eligible" else 0.0
        ),
        "positive_implication_probability": positive,
        "negative_implication_probability": negative,
        "event_tags": _concept_tags(concepts) if concepts else None,
        "issuer_roles": None,
        "time_scope": None,
        "claim_source": None,
    }
    authority = {
        "issuer_name": (
            "legacy_entity_identifier_substituted_to_preserve_distinct_unit"
            if legacy_identity_fallback
            else "unavailable_in_legacy_authority"
        ),
        "ticker": "legacy_categorical_identity",
        "exchange": "unavailable_in_legacy_authority",
        "identity_source": "derived_from_legacy_metadata",
        "identity_confidence_probability": "unavailable_in_legacy_authority",
        "forecast_relevance_probability": "categorical_legacy_label_encoded_as_0_or_1",
        "positive_implication_probability": (
            "categorical_legacy_sentiment_encoded_as_0_or_1"
            if positive is not None
            else "unavailable_for_legacy_sentiment"
        ),
        "negative_implication_probability": (
            "categorical_legacy_sentiment_encoded_as_0_or_1"
            if negative is not None
            else "unavailable_for_legacy_sentiment"
        ),
        "event_tags": (
            "deterministically_mapped_from_legacy_concepts"
            if concepts
            else "unavailable_in_legacy_authority"
        ),
        "issuer_roles": "unavailable_in_legacy_authority",
        "time_scope": "unavailable_in_legacy_authority",
        "claim_source": "unavailable_in_legacy_authority",
        "legacy_entity_id": entity_id,
    }
    return issuer, authority


def convert_article(row: Mapping[str, Any]) -> dict[str, Any]:
    issuers: list[dict[str, Any]] = []
    field_authority: list[dict[str, Any]] = []
    for unit in row["issuer_units"]:
        issuer, authority = convert_unit(unit)
        issuers.append(issuer)
        field_authority.append(
            {
                "legacy_unit_id": str(unit["unit_id"]),
                "ticker": issuer["ticker"],
                "fields": authority,
            }
        )
    labels = canonicalize_output(
        {
            "schema_version": SCHEMA_VERSION,
            "article_forecast_eligible": False,
            "issuers": issuers,
            "unresolved_issuer_mentions": [],
        }
    )
    labels["article_forecast_eligible"] = derive_article_forecast_eligible(labels)
    errors = validate_output(labels, allow_legacy_nulls=True)
    if errors:
        raise RuntimeError(f"Invalid conversion for {row['source_id']}: {'; '.join(errors)}")
    authority_id = str(row["authority_id"])
    return {
        "source_id": str(row["source_id"]),
        "source_timestamp": str(row["source_timestamp"]),
        "labels": labels,
        "conversion_lineage": {
            "dataset_version": DATASET_VERSION,
            "legacy_authority_id": authority_id,
            "legacy_authority_version": str(row["authority_version"]),
            "legacy_certification_level": str(row["certification_level"]),
            "legacy_partition": str(row["partition"]),
            "legacy_usage_policy": str(row["usage_policy"]),
            "legacy_article_forecast_eligible": bool(row["article_forecast_eligible"]),
            "article_eligibility_derivation": "any issuer forecast_relevance_probability >= 0.5",
            "eligibility_authority_warning": (
                "eligibility_inherited_from_preselected_forecast_population_not_independently_reviewed"
                if authority_id in SOL_ELIGIBILITY_UNREVIEWED
                else None
            ),
            "probability_semantics": "categorical legacy values encoded as endpoints; null means unavailable, not zero",
            "field_authority": field_authority,
            "legacy_lineage": dict(row.get("lineage") or {}),
            "legacy_source_hashes": dict(row.get("source_hashes") or {}),
        },
    }


def convert(input_root: Path, output_root: Path) -> dict[str, Any]:
    input_root = input_root.resolve()
    output_root = output_root.resolve()
    source_manifest_path = input_root / "manifest.json"
    source_labels_path = input_root / "gold_labels.jsonl"
    source_authorities_path = input_root / "authorities.jsonl"
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    if (
        source_manifest.get("version") != "news_synthesis_certified_gold_consolidated_v1"
        or source_manifest.get("status") != "complete"
    ):
        raise RuntimeError("Input is not the confirmed consolidated V1 gold authority")
    expected = source_manifest["files"]["gold_labels.jsonl"]
    if _sha256(source_labels_path) != expected["sha256"]:
        raise RuntimeError("Consolidated gold label hash mismatch")
    if output_root.exists():
        raise RuntimeError(f"Refusing to overwrite output: {output_root}")
    output_root.mkdir(parents=True)

    converted_path = output_root / "labels.jsonl"
    authority_counts: Counter[str] = Counter()
    warning_counts: Counter[str] = Counter()
    article_counts: Counter[str] = Counter()
    null_counts: Counter[str] = Counter()
    issuer_count = 0
    source_ids: list[str] = []
    source_rows = sorted(_read_jsonl(source_labels_path), key=lambda row: str(row["source_id"]))
    with converted_path.open("w", encoding="utf-8", newline="\n") as output:
        for row in source_rows:
            converted = convert_article(row)
            output.write(json.dumps(converted, ensure_ascii=False, sort_keys=True) + "\n")
            source_ids.append(converted["source_id"])
            authority_id = converted["conversion_lineage"]["legacy_authority_id"]
            authority_counts[authority_id] += 1
            warning = converted["conversion_lineage"]["eligibility_authority_warning"]
            if warning:
                warning_counts[warning] += 1
            eligible = converted["labels"]["article_forecast_eligible"]
            article_counts["eligible" if eligible else "ineligible"] += 1
            for issuer in converted["labels"]["issuers"]:
                issuer_count += 1
                for field, value in issuer.items():
                    if value is None:
                        null_counts[field] += 1
    if source_ids != sorted(source_ids) or len(source_ids) != len(set(source_ids)):
        raise RuntimeError("Converted source IDs are not unique and sorted")
    if len(source_ids) != int(source_manifest["population"]["articles"]):
        raise RuntimeError("Converted article count disagrees with source authority")
    if issuer_count != int(source_manifest["population"]["issuer_units"]):
        raise RuntimeError("Converted issuer count disagrees with source authority")

    authorities = list(_read_jsonl(source_authorities_path))
    report = {
        "status": "complete",
        "dataset_version": DATASET_VERSION,
        "schema_version": SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_authority": {
            "root": str(input_root),
            "version": source_manifest["version"],
            "manifest_sha256": _sha256(source_manifest_path),
            "labels_sha256": _sha256(source_labels_path),
            "authorities_sha256": _sha256(source_authorities_path),
            "authorities": authorities,
        },
        "population": {
            "articles": len(source_ids),
            "issuer_units": issuer_count,
            "articles_by_authority": dict(sorted(authority_counts.items())),
            "article_forecast_eligibility": dict(sorted(article_counts.items())),
        },
        "conversion": {
            "evidence_sentence_ids_removed": True,
            "article_forecast_eligible_added": True,
            "article_forecast_eligible_policy": "any issuer forecast_relevance_probability >= 0.5",
            "legacy_null_counts": dict(sorted(null_counts.items())),
            "eligibility_authority_warnings": dict(sorted(warning_counts.items())),
            "no_llm_or_news_synthesis_predictions_used": True,
            "no_semantic_relabeling_performed": True,
        },
        "files": {
            "labels.jsonl": {
                "rows": len(source_ids),
                "bytes": converted_path.stat().st_size,
                "sha256": _sha256(converted_path),
            }
        },
    }
    report_path = output_root / "REPORT.md"
    report_path.write_text(_report_text(report), encoding="utf-8", newline="\n")
    report["files"]["REPORT.md"] = {
        "bytes": report_path.stat().st_size,
        "sha256": _sha256(report_path),
    }
    manifest_path = output_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert consolidated legacy gold to V4 labels")
    parser.add_argument("--input-root", type=Path, default=DEFAULT_INPUT_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args(argv)
    print(json.dumps(convert(args.input_root, args.output_root), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
