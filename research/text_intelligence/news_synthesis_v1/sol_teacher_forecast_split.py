from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from .contracts import canonical_json, sha256_json, validate_document
from .sol_teacher_evaluation import load_json, write_json_atomic


SPLIT_VERSION = "news_synthesis_sol_teacher_forecast_split_v1"
REFERENCE_AUDIT_ARTICLES = 700
REFERENCE_TOTAL_ARTICLES = 1_045


def create_forecast_split(
    evaluation_root: Path,
    teacher_root: Path,
    output_root: Path,
    *,
    expected_units: int = 5_528,
) -> dict[str, Any]:
    evaluation_manifest = load_json(evaluation_root / "manifest.json")
    converted_paths = {
        path.stem: path
        for path in (evaluation_root / "converted_labels").glob("*.json")
    }
    item_paths = {
        path.stem: path for path in (teacher_root / "items").glob("*.json")
    }
    if set(converted_paths) - set(item_paths):
        raise RuntimeError("Converted labels exist without source articles")

    units: list[dict[str, Any]] = []
    article_rows: dict[str, dict[str, Any]] = {}
    for sample_id in sorted(converted_paths):
        document = load_json(converted_paths[sample_id])
        validation = validate_document(document)
        if not validation.valid:
            raise RuntimeError(
                f"Invalid converted document {sample_id}: {validation.issues}"
            )
        article = load_json(item_paths[sample_id])
        if not _source_identity_matches(document, article):
            raise RuntimeError(f"Converted/source identity mismatch: {sample_id}")
        document_units = _forecast_units(document)
        if not document_units:
            continue
        converted_sha256 = sha256_json(document)
        publication = article.get("publication", {})
        article_rows[sample_id] = {
            "sample_id": sample_id,
            "source_id": str(article.get("source_id") or ""),
            "source_timestamp": str(article.get("source_timestamp") or ""),
            "source_text_sha256": str(article.get("source_text_sha256") or ""),
            "provider": str(publication.get("provider") or "unknown"),
            "year": _year(str(article.get("source_timestamp") or "")),
            "converted_label_sha256": converted_sha256,
            "unit_ids": [row["unit_id"] for row in document_units],
        }
        for row in document_units:
            units.append(
                {
                    **row,
                    "source_id": str(article.get("source_id") or ""),
                    "source_timestamp": str(article.get("source_timestamp") or ""),
                    "source_text_sha256": str(
                        article.get("source_text_sha256") or ""
                    ),
                    "provider": str(publication.get("provider") or "unknown"),
                    "year": _year(str(article.get("source_timestamp") or "")),
                    "converted_label_sha256": converted_sha256,
                }
            )

    if len(units) != expected_units:
        raise RuntimeError(
            f"Forecast unit count mismatch: {len(units)} != {expected_units}"
        )
    split = build_article_grouped_split(article_rows, units)
    audit_set = _set_document("audit", split["audit_article_ids"], article_rows, units)
    test_set = _set_document("test", split["test_article_ids"], article_rows, units)
    _validate_partition(article_rows, units, audit_set, test_set)

    output_root.mkdir(parents=True, exist_ok=True)
    write_json_atomic(output_root / "audit_set.json", audit_set)
    write_json_atomic(output_root / "test_set.json", test_set)
    authority = evaluation_manifest.get("authority", {})
    manifest = {
        "version": SPLIT_VERSION,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "method": (
            "chronological article order with deterministic 2-of-3 audit "
            "interleave; earliest test positions moved to audit until the "
            "prior 700/1045 article ratio is reached"
        ),
        "selection_inputs": [
            "source_timestamp",
            "sample_id",
            "gold_forecast_eligibility",
        ],
        "prediction_blind": True,
        "grouping_key": "sample_id",
        "reference_ratio": {
            "audit_articles": REFERENCE_AUDIT_ARTICLES,
            "total_articles": REFERENCE_TOTAL_ARTICLES,
            "ratio": REFERENCE_AUDIT_ARTICLES / REFERENCE_TOTAL_ARTICLES,
        },
        "population": {
            "articles": len(article_rows),
            "issuer_units": len(units),
        },
        "audit": _set_summary(audit_set),
        "test": _set_summary(test_set),
        "authority": {
            "source_evaluation_version": str(
                evaluation_manifest.get("version") or ""
            ),
            "source_conversion_version": str(
                authority.get("conversion_version") or ""
            ),
            "source_contract_version": str(
                authority.get("contract_version") or ""
            ),
            "source_concept_registry_version": str(
                authority.get("concept_registry_version") or ""
            ),
            "teacher_items_sha256": str(
                authority.get("teacher_items_sha256") or ""
            ),
            "teacher_labels_sha256": str(
                authority.get("teacher_labels_sha256") or ""
            ),
            "converted_labels_sha256": str(
                authority.get("converted_labels_sha256") or ""
            ),
            "eligible_units_sha256": _rows_sha256(units),
            "audit_set_sha256": sha256_json(audit_set),
            "test_set_sha256": sha256_json(test_set),
            "article_partition_sha256": sha256_json(
                {
                    "audit": split["audit_article_ids"],
                    "test": split["test_article_ids"],
                }
            ),
        },
    }
    write_json_atomic(output_root / "split_manifest.json", manifest)
    (output_root / "SUMMARY.md").write_text(
        render_split_summary(manifest), encoding="utf-8"
    )
    return manifest


def build_article_grouped_split(
    article_rows: Mapping[str, Mapping[str, Any]],
    units: Sequence[Mapping[str, Any]],
) -> dict[str, list[str]]:
    unit_samples = {str(row["sample_id"]) for row in units}
    if unit_samples != set(article_rows):
        raise RuntimeError("Forecast units and article population do not agree")
    ordered = sorted(
        article_rows,
        key=lambda sample_id: (
            str(article_rows[sample_id]["source_timestamp"]),
            sample_id,
        ),
    )
    target_audit = round(
        len(ordered) * REFERENCE_AUDIT_ARTICLES / REFERENCE_TOTAL_ARTICLES
    )
    audit = [sample_id for index, sample_id in enumerate(ordered) if index % 3 != 2]
    test = [sample_id for index, sample_id in enumerate(ordered) if index % 3 == 2]
    if len(audit) < target_audit:
        moved = test[: target_audit - len(audit)]
        audit.extend(moved)
        test = test[len(moved) :]
    elif len(audit) > target_audit:
        moved = audit[target_audit:]
        audit = audit[:target_audit]
        test.extend(moved)
    order_index = {sample_id: index for index, sample_id in enumerate(ordered)}
    audit.sort(key=order_index.__getitem__)
    test.sort(key=order_index.__getitem__)
    return {"audit_article_ids": audit, "test_article_ids": test}


def render_split_summary(manifest: Mapping[str, Any]) -> str:
    population = manifest["population"]
    audit = manifest["audit"]
    test = manifest["test"]
    lines = [
        "# Sol forecast gold audit/test split",
        "",
        "This split was created without reading News Synthesis predictions.",
        "All issuer units from one source article remain in the same partition.",
        "",
        f"- Forecast-eligible articles: {population['articles']:,}",
        f"- Forecast-eligible issuer units: {population['issuer_units']:,}",
        f"- Audit: {audit['articles']:,} articles / {audit['issuer_units']:,} units",
        f"- Test: {test['articles']:,} articles / {test['issuer_units']:,} units",
        "",
        "| Partition | Positive | Negative | Neutral | Mixed |",
        "|---|---:|---:|---:|---:|",
    ]
    for label, summary in (("Audit", audit), ("Test", test)):
        directions = summary["direction_distribution"]
        lines.append(
            f"| {label} | {directions.get('positive', 0):,} | "
            f"{directions.get('negative', 0):,} | "
            f"{directions.get('neutral', 0):,} | "
            f"{directions.get('mixed', 0):,} |"
        )
    return "\n".join(lines) + "\n"


def _forecast_units(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    entities = {
        str(row["entity_id"]): row for row in document.get("entities", ())
    }
    statements = {
        str(row["statement_id"]): row for row in document.get("statements", ())
    }
    eligible = {
        str(row["entity_id"])
        for row in document.get("eligibility", ())
        if row.get("product") == "forecast_trigger" and row.get("eligible")
    }
    output = []
    for view in document.get("issuer_views", ()):
        entity_id = str(view["entity_id"])
        if entity_id not in eligible:
            continue
        entity = entities[entity_id]
        ticker = str(entity.get("ticker") or "").upper()
        if not ticker:
            raise RuntimeError(
                f"Forecast-eligible entity has no ticker: {document['sample_id']}/{entity_id}"
            )
        concepts = sorted(
            {
                str(statements[statement_id]["concept_leaf"])
                for statement_id in view.get("statement_ids", ())
                if statement_id in statements
            }
        )
        output.append(
            {
                "unit_id": f"{document['sample_id']}::{ticker}",
                "sample_id": str(document["sample_id"]),
                "ticker": ticker,
                "entity_id": entity_id,
                "gold_sentiment": str(view["composite_sentiment"]),
                "concepts": concepts,
            }
        )
    return sorted(output, key=lambda row: row["unit_id"])


def _set_document(
    name: str,
    article_ids: Sequence[str],
    article_rows: Mapping[str, Mapping[str, Any]],
    units: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    selected = set(article_ids)
    selected_units = sorted(
        (dict(row) for row in units if str(row["sample_id"]) in selected),
        key=lambda row: row["unit_id"],
    )
    return {
        "version": SPLIT_VERSION,
        "partition": name,
        "prediction_blind": True,
        "article_ids": list(article_ids),
        "articles": [dict(article_rows[sample_id]) for sample_id in article_ids],
        "units": selected_units,
        "balance": _balance(selected_units),
    }


def _set_summary(document: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "articles": len(document["article_ids"]),
        "issuer_units": len(document["units"]),
        "direction_distribution": document["balance"]["directions"],
        "year_distribution": document["balance"]["years"],
        "provider_distribution": document["balance"]["providers"],
        "concept_distribution": document["balance"]["concepts"],
    }


def _balance(units: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    concepts = Counter(
        concept for row in units for concept in row.get("concepts", ())
    )
    return {
        "directions": dict(sorted(Counter(
            str(row["gold_sentiment"]) for row in units
        ).items())),
        "years": dict(sorted(Counter(str(row["year"]) for row in units).items())),
        "providers": dict(sorted(Counter(
            str(row["provider"]) for row in units
        ).items())),
        "concepts": dict(sorted(concepts.items())),
    }


def _validate_partition(
    article_rows: Mapping[str, Mapping[str, Any]],
    units: Sequence[Mapping[str, Any]],
    audit: Mapping[str, Any],
    test: Mapping[str, Any],
) -> None:
    audit_articles = set(audit["article_ids"])
    test_articles = set(test["article_ids"])
    if audit_articles & test_articles:
        raise RuntimeError("Audit and test article identities overlap")
    if audit_articles | test_articles != set(article_rows):
        raise RuntimeError("Audit and test articles do not cover the population")
    audit_units = {str(row["unit_id"]) for row in audit["units"]}
    test_units = {str(row["unit_id"]) for row in test["units"]}
    all_units = {str(row["unit_id"]) for row in units}
    if len(all_units) != len(units):
        raise RuntimeError("Forecast unit identities are not unique")
    if audit_units & test_units or audit_units | test_units != all_units:
        raise RuntimeError("Audit and test units are not a disjoint full partition")


def _source_identity_matches(
    document: Mapping[str, Any], article: Mapping[str, Any]
) -> bool:
    return all(
        str(document.get(field) or "") == str(article.get(field) or "")
        for field in (
            "sample_id",
            "source_id",
            "source_timestamp",
            "source_text_sha256",
        )
    )


def _year(timestamp: str) -> str:
    return timestamp[:4] if len(timestamp) >= 4 else "unknown"


def _rows_sha256(rows: Sequence[Mapping[str, Any]]) -> str:
    ordered = sorted(rows, key=lambda row: str(row["unit_id"]))
    return sha256_json(ordered)
