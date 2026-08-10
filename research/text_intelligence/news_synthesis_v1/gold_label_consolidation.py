from __future__ import annotations

import hashlib
import json
import shutil
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from .contracts import sha256_json, validate_document


DATASET_VERSION = "news_synthesis_certified_gold_consolidated_v1"
_FORECAST_GOLD_VERSION = "news_synthesis_forecast_eligibility_gold_v1"
_SOL_REVIEWED_VERSION = "news_synthesis_sol_forecast_reviewed_gold_v2"
_SOL_SPLIT_VERSION = "news_synthesis_sol_teacher_forecast_split_v1"
_SENTIMENTS = {"positive", "negative", "neutral", "mixed"}


def consolidate_gold_labels(
    *,
    runtime_root: Path,
    manual_certification_root: Path,
    sol_reviewed_root: Path,
    sol_split_root: Path,
    forecast_roots: Iterable[Path],
    output_root: Path,
) -> dict[str, Any]:
    """Consolidate current gold authorities without discarding source lineage."""
    runtime_root = runtime_root.resolve()
    output_root = output_root.resolve()
    inputs = [
        manual_certification_root.resolve(),
        sol_reviewed_root.resolve(),
        sol_split_root.resolve(),
        *[path.resolve() for path in forecast_roots],
    ]
    for path in [*inputs, output_root]:
        _require_within(path, runtime_root)
    if output_root.exists():
        raise RuntimeError(f"Consolidated output already exists: {output_root}")
    building_root = output_root.with_name(f"{output_root.name}.building")
    if building_root.exists():
        raise RuntimeError(f"Prior consolidation staging exists: {building_root}")

    forecast_authorities = _expand_forecast_roots(inputs[3:])
    authority_factories = [
        _manual_authority(manual_certification_root.resolve(), runtime_root),
        _sol_reviewed_authority(sol_reviewed_root.resolve(), runtime_root),
        _sol_test_authority(sol_split_root.resolve(), runtime_root),
        *[
            _forecast_authority(path, runtime_root, index)
            for index, path in enumerate(forecast_authorities, start=1)
        ],
    ]

    building_root.mkdir(parents=True)
    try:
        manifest = _write_dataset(building_root, authority_factories)
        validation = validate_consolidated_gold(building_root)
        if validation["status"] != "pass":
            raise RuntimeError("Consolidated gold validation did not pass")
        building_root.replace(output_root)
    except Exception:
        shutil.rmtree(building_root, ignore_errors=True)
        raise
    return manifest


def _write_dataset(
    output_root: Path,
    authorities: list[tuple[dict[str, Any], Iterator[dict[str, Any]]]],
) -> dict[str, Any]:
    article_path = output_root / "gold_labels.jsonl"
    unit_path = output_root / "gold_issuer_labels.jsonl"
    development_article_path = output_root / "development_gold_labels.jsonl"
    development_unit_path = output_root / "development_gold_issuer_labels.jsonl"
    authority_path = output_root / "authorities.jsonl"
    seen_source_ids: set[str] = set()
    seen_unit_ids: set[str] = set()
    authority_rows: list[dict[str, Any]] = []
    authority_articles: Counter[str] = Counter()
    authority_units: Counter[str] = Counter()
    partition_articles: Counter[str] = Counter()
    partition_units: Counter[str] = Counter()
    eligibility: Counter[str] = Counter()
    sentiment: Counter[str] = Counter()
    normalization: Counter[str] = Counter()

    with (
        article_path.open("w", encoding="utf-8", newline="\n") as article_handle,
        unit_path.open("w", encoding="utf-8", newline="\n") as unit_handle,
        development_article_path.open("w", encoding="utf-8", newline="\n") as development_article_handle,
        development_unit_path.open("w", encoding="utf-8", newline="\n") as development_unit_handle,
    ):
        for authority, records in authorities:
            authority_rows.append(authority)
            authority_id = str(authority["authority_id"])
            for record in records:
                source_id = str(record["source_id"])
                if source_id in seen_source_ids:
                    raise RuntimeError(f"Gold authority source_id overlap: {source_id}")
                seen_source_ids.add(source_id)
                issuer_units = list(record["issuer_units"])
                article_row = {
                    "dataset_version": DATASET_VERSION,
                    **{key: value for key, value in record.items() if key != "issuer_units"},
                    "issuer_units": issuer_units,
                }
                _write_jsonl_row(article_handle, article_row)
                if record["usage_policy"] == "model_development_allowed":
                    _write_jsonl_row(development_article_handle, article_row)
                authority_articles[authority_id] += 1
                partition_articles[str(record["partition"])] += 1
                for unit in issuer_units:
                    unit_id = str(unit["unit_id"])
                    if unit_id in seen_unit_ids:
                        raise RuntimeError(f"Duplicate consolidated issuer unit: {unit_id}")
                    seen_unit_ids.add(unit_id)
                    unit_row = {
                        "dataset_version": DATASET_VERSION,
                        "source_id": source_id,
                        "source_timestamp": record["source_timestamp"],
                        "authority_id": authority_id,
                        "authority_version": authority["authority_version"],
                        "certification_level": authority["certification_level"],
                        "partition": record["partition"],
                        "usage_policy": record["usage_policy"],
                        **unit,
                    }
                    _write_jsonl_row(unit_handle, unit_row)
                    if record["usage_policy"] == "model_development_allowed":
                        _write_jsonl_row(development_unit_handle, unit_row)
                    authority_units[authority_id] += 1
                    partition_units[str(record["partition"])] += 1
                    eligibility[str(unit["forecast_eligibility"])] += 1
                    sentiment[str(unit["sentiment"])] += 1
                    normalization[str(unit["normalization_status"])] += 1

    authority_rows.sort(key=lambda row: str(row["authority_id"]))
    with authority_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in authority_rows:
            _write_jsonl_row(handle, row)

    manifest = {
        "version": DATASET_VERSION,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "status": "complete",
        "contract": {
            "article_key": "source_id",
            "issuer_unit_key": "source_id plus authority-local entity or ticker key",
            "source_id_overlap_policy": "fail_closed",
            "raw_gold_payload_preserved": True,
            "sealed_test_training_policy": "excluded",
            "default_development_article_file": "development_gold_labels.jsonl",
            "default_development_issuer_file": "development_gold_issuer_labels.jsonl",
        },
        "population": {
            "articles": len(seen_source_ids),
            "issuer_units": len(seen_unit_ids),
            "source_id_overlaps": 0,
            "authorities": len(authority_rows),
            "development_articles": len(seen_source_ids) - partition_articles["sealed_test"],
            "development_issuer_units": len(seen_unit_ids) - partition_units["sealed_test"],
            "sealed_test_articles": partition_articles["sealed_test"],
            "sealed_test_issuer_units": partition_units["sealed_test"],
        },
        "distribution": {
            "articles_by_authority": dict(sorted(authority_articles.items())),
            "issuer_units_by_authority": dict(sorted(authority_units.items())),
            "articles_by_partition": dict(sorted(partition_articles.items())),
            "issuer_units_by_partition": dict(sorted(partition_units.items())),
            "forecast_eligibility": dict(sorted(eligibility.items())),
            "sentiment": dict(sorted(sentiment.items())),
            "normalization_status": dict(sorted(normalization.items())),
        },
        "files": {
            "gold_labels.jsonl": _file_summary(article_path),
            "gold_issuer_labels.jsonl": _file_summary(unit_path),
            "development_gold_labels.jsonl": _file_summary(development_article_path),
            "development_gold_issuer_labels.jsonl": _file_summary(development_unit_path),
            "authorities.jsonl": _file_summary(authority_path),
        },
        "authority_set_sha256": sha256_json(authority_rows),
        "article_source_ids_sha256": sha256_json(sorted(seen_source_ids)),
        "issuer_unit_ids_sha256": sha256_json(sorted(seen_unit_ids)),
    }
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def validate_consolidated_gold(output_root: Path) -> dict[str, Any]:
    output_root = output_root.resolve()
    manifest = json.loads((output_root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("version") != DATASET_VERSION or manifest.get("status") != "complete":
        raise RuntimeError("Consolidated manifest is not complete")
    for name, expected in manifest["files"].items():
        path = output_root / name
        actual = _file_summary(path)
        if actual != expected:
            raise RuntimeError(f"Consolidated file integrity mismatch: {name}")

    authority_rows = list(_iter_jsonl(output_root / "authorities.jsonl"))
    source_ids: list[str] = []
    seen_sources: set[str] = set()
    article_index: dict[str, tuple[str, str, str]] = {}
    embedded_unit_ids: set[str] = set()
    expected_development_sources: set[str] = set()
    expected_development_units: set[str] = set()
    articles_by_authority: Counter[str] = Counter()
    articles_by_partition: Counter[str] = Counter()
    sealed_count = 0
    for row in _iter_jsonl(output_root / "gold_labels.jsonl"):
        source_id = str(row["source_id"])
        if source_id in seen_sources:
            raise RuntimeError("Consolidated articles contain duplicate source IDs")
        seen_sources.add(source_id)
        source_ids.append(source_id)
        authority_id = str(row["authority_id"])
        partition = str(row["partition"])
        usage_policy = str(row["usage_policy"])
        article_index[source_id] = (authority_id, partition, usage_policy)
        if usage_policy == "model_development_allowed":
            expected_development_sources.add(source_id)
        articles_by_authority[authority_id] += 1
        articles_by_partition[partition] += 1
        units = list(row["issuer_units"])
        if bool(row["article_forecast_eligible"]) != any(
            unit["forecast_eligibility"] == "eligible" for unit in units
        ):
            raise RuntimeError(f"Consolidated article eligibility mismatch: {source_id}")
        for unit in units:
            unit_id = str(unit["unit_id"])
            if unit_id in embedded_unit_ids:
                raise RuntimeError(f"Duplicate embedded issuer unit: {unit_id}")
            embedded_unit_ids.add(unit_id)
            if usage_policy == "model_development_allowed":
                expected_development_units.add(unit_id)
        if row["partition"] == "sealed_test":
            sealed_count += 1
            if row["usage_policy"] != "final_evaluation_only":
                raise RuntimeError("Sealed-test rows are not excluded from development")
    unit_ids: list[str] = []
    seen_units: set[str] = set()
    units_by_authority: Counter[str] = Counter()
    units_by_partition: Counter[str] = Counter()
    eligibility: Counter[str] = Counter()
    sentiment: Counter[str] = Counter()
    normalization: Counter[str] = Counter()
    for row in _iter_jsonl(output_root / "gold_issuer_labels.jsonl"):
        unit_id = str(row["unit_id"])
        if unit_id in seen_units:
            raise RuntimeError("Consolidated issuer labels contain duplicate unit IDs")
        seen_units.add(unit_id)
        unit_ids.append(unit_id)
        source_id = str(row["source_id"])
        expected_article = article_index.get(source_id)
        actual_article = (
            str(row["authority_id"]),
            str(row["partition"]),
            str(row["usage_policy"]),
        )
        if expected_article != actual_article:
            raise RuntimeError(f"Issuer unit does not match its article authority: {unit_id}")
        units_by_authority[actual_article[0]] += 1
        units_by_partition[actual_article[1]] += 1
        eligibility[str(row["forecast_eligibility"])] += 1
        sentiment[str(row["sentiment"])] += 1
        normalization[str(row["normalization_status"])] += 1
    if len(source_ids) != manifest["population"]["articles"]:
        raise RuntimeError("Consolidated article count mismatch")
    if len(unit_ids) != manifest["population"]["issuer_units"]:
        raise RuntimeError("Consolidated issuer-unit count mismatch")
    development_source_ids = [
        str(row["source_id"])
        for row in _iter_jsonl(output_root / "development_gold_labels.jsonl")
    ]
    development_unit_ids = [
        str(row["unit_id"])
        for row in _iter_jsonl(output_root / "development_gold_issuer_labels.jsonl")
    ]
    development_sources = set(development_source_ids)
    development_units = set(development_unit_ids)
    if len(development_source_ids) != len(development_sources):
        raise RuntimeError("Development article view contains duplicate source IDs")
    if len(development_unit_ids) != len(development_units):
        raise RuntimeError("Development issuer view contains duplicate unit IDs")
    if development_sources != expected_development_sources:
        raise RuntimeError("Development article view does not enforce usage policy")
    if development_units != expected_development_units:
        raise RuntimeError("Development issuer view does not enforce usage policy")
    if len(development_sources) != manifest["population"]["development_articles"]:
        raise RuntimeError("Development article count mismatch")
    if len(development_units) != manifest["population"]["development_issuer_units"]:
        raise RuntimeError("Development issuer-unit count mismatch")
    if sealed_count != manifest["population"]["sealed_test_articles"]:
        raise RuntimeError("Sealed-test article count mismatch")
    if units_by_partition["sealed_test"] != manifest["population"]["sealed_test_issuer_units"]:
        raise RuntimeError("Sealed-test issuer-unit count mismatch")
    if seen_units != embedded_unit_ids:
        raise RuntimeError("Embedded and flattened issuer-unit identities differ")
    if sha256_json(sorted(source_ids)) != manifest["article_source_ids_sha256"]:
        raise RuntimeError("Consolidated article identity hash mismatch")
    if sha256_json(sorted(unit_ids)) != manifest["issuer_unit_ids_sha256"]:
        raise RuntimeError("Consolidated issuer-unit identity hash mismatch")
    if sha256_json(sorted(authority_rows, key=lambda row: str(row["authority_id"]))) != manifest[
        "authority_set_sha256"
    ]:
        raise RuntimeError("Consolidated authority hash mismatch")
    expected_distribution = manifest["distribution"]
    actual_distribution = {
        "articles_by_authority": dict(sorted(articles_by_authority.items())),
        "issuer_units_by_authority": dict(sorted(units_by_authority.items())),
        "articles_by_partition": dict(sorted(articles_by_partition.items())),
        "issuer_units_by_partition": dict(sorted(units_by_partition.items())),
        "forecast_eligibility": dict(sorted(eligibility.items())),
        "sentiment": dict(sorted(sentiment.items())),
        "normalization_status": dict(sorted(normalization.items())),
    }
    if actual_distribution != expected_distribution:
        raise RuntimeError("Consolidated distribution mismatch")
    report = {
        "version": f"{DATASET_VERSION}_validation_v1",
        "validated_at_utc": datetime.now(UTC).isoformat(),
        "status": "pass",
        "articles": len(source_ids),
        "issuer_units": len(unit_ids),
        "authorities": len(authority_rows),
        "sealed_test_articles": sealed_count,
        "development_articles": len(development_sources),
        "development_issuer_units": len(development_units),
        "authority_hashes_verified": True,
        "file_hashes_verified": True,
    }
    (output_root / "VALIDATION.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def _manual_authority(
    root: Path, runtime_root: Path
) -> tuple[dict[str, Any], Iterator[dict[str, Any]]]:
    manifest_path = root / "manifest.json"
    manifest = _read_json(manifest_path)
    expected_self_hash = str(manifest.get("manifest_sha256") or "")
    if sha256_json({key: value for key, value in manifest.items() if key != "manifest_sha256"}) != expected_self_hash:
        raise RuntimeError("Manual certification manifest hash mismatch")
    ledger_path = root / "certification_ledger.jsonl"
    if _sha256_file(ledger_path) != manifest.get("ledger_sha256"):
        raise RuntimeError("Manual certification ledger hash mismatch")
    paths = sorted((root / "certified_labels").glob("*.json"), key=lambda path: path.stem)
    if len(paths) != int(manifest["certified"]) or manifest.get("pending") != 0:
        raise RuntimeError("Manual certification population is incomplete")
    ledger = _read_jsonl(ledger_path)
    ledger_by_id = {str(row["sample_id"]): row for row in ledger}
    if len(ledger_by_id) != len(ledger) or set(ledger_by_id) != {path.stem for path in paths}:
        raise RuntimeError("Manual certification ledger identities do not match certified labels")
    for path in paths:
        row = ledger_by_id[path.stem]
        document = _read_json(path)
        if row.get("status") != "certified" or row.get("certified_sha256") != _sha256_file(path):
            raise RuntimeError(f"Manual certification ledger hash mismatch: {path.name}")
        if row.get("source_id") != document.get("source_id"):
            raise RuntimeError(f"Manual certification ledger source mismatch: {path.name}")
    current_input_hash = sha256_json([
        {"sample_id": path.stem, "sha256": sha256_json(_read_json(path))} for path in paths
    ])
    authority = _authority_row(
        authority_id="manual_certification_v1",
        authority_version=str(manifest.get("certification_version") or ""),
        certification_level="source_bound_manual_document",
        partition="manual_certified",
        usage_policy="model_development_allowed",
        root=root,
        manifest_path=manifest_path,
        runtime_root=runtime_root,
        articles=len(paths),
    )
    authority.update({
        "certification_ledger_sha256": _sha256_file(ledger_path),
        "verified_current_authority_sha256": current_input_hash,
        "manifest_input_authority_sha256": str(manifest.get("input_authority_sha256") or ""),
        "manifest_input_authority_matches_current": (
            current_input_hash == manifest.get("input_authority_sha256")
        ),
        "manifest_input_authority_semantics": "initialization_snapshot_not_continuing_authority",
    })

    def records() -> Iterator[dict[str, Any]]:
        for path in paths:
            document = _read_json(path)
            validation = validate_document(document)
            if not validation.valid or document.get("certification", {}).get("status") != "certified":
                raise RuntimeError(f"Invalid manual certified document {path.name}: {validation.issues}")
            units = _normalize_manual_units(document)
            yield _article_record(
                authority,
                source_id=str(document["source_id"]),
                source_timestamp=str(document["source_timestamp"]),
                authority_article_id=str(document["sample_id"]),
                partition="manual_certified",
                usage_policy="model_development_allowed",
                source_hashes={"source_text_sha256": str(document["source_text_sha256"])},
                issuer_units=units,
                source_relative_path=_relative(path, runtime_root),
                source_artifact_sha256=_sha256_file(path),
                raw_payload=document,
            )

    return authority, records()


def _sol_reviewed_authority(
    root: Path, runtime_root: Path
) -> tuple[dict[str, Any], Iterator[dict[str, Any]]]:
    manifest_path = root / "manifest.json"
    data_path = root / "reviewed_audit_set.json"
    manifest = _read_json(manifest_path)
    data = _read_json(data_path)
    if manifest.get("version") != _SOL_REVIEWED_VERSION or data.get("version") != _SOL_REVIEWED_VERSION:
        raise RuntimeError("Unexpected reviewed Sol gold version")
    if sha256_json(data) != manifest.get("authority", {}).get("reviewed_audit_set_sha256"):
        raise RuntimeError("Reviewed Sol gold hash mismatch")
    authority = _authority_row(
        authority_id="sol_teacher_forecast_reviewed_gold_v2",
        authority_version=_SOL_REVIEWED_VERSION,
        certification_level="prediction_blind_source_reviewed_direction",
        partition="reviewed_audit",
        usage_policy="model_development_allowed",
        root=root,
        manifest_path=manifest_path,
        runtime_root=runtime_root,
        articles=len(data["articles"]),
    )
    return authority, _sol_records(
        authority, data, data_path, runtime_root, "reviewed_audit", "model_development_allowed"
    )


def _sol_test_authority(
    root: Path, runtime_root: Path
) -> tuple[dict[str, Any], Iterator[dict[str, Any]]]:
    manifest_path = root / "split_manifest.json"
    data_path = root / "test_set.json"
    manifest = _read_json(manifest_path)
    data = _read_json(data_path)
    if manifest.get("version") != _SOL_SPLIT_VERSION or data.get("partition") != "test":
        raise RuntimeError("Unexpected Sol sealed-test authority")
    if sha256_json(data) != manifest.get("authority", {}).get("test_set_sha256"):
        raise RuntimeError("Sol sealed-test hash mismatch")
    authority = _authority_row(
        authority_id="sol_teacher_forecast_sealed_test_v1",
        authority_version=str(data.get("version") or _SOL_SPLIT_VERSION),
        certification_level="frozen_converted_teacher_gold",
        partition="sealed_test",
        usage_policy="final_evaluation_only",
        root=root,
        manifest_path=manifest_path,
        runtime_root=runtime_root,
        articles=len(data["articles"]),
    )
    return authority, _sol_records(
        authority, data, data_path, runtime_root, "sealed_test", "final_evaluation_only"
    )


def _sol_records(
    authority: Mapping[str, Any],
    data: Mapping[str, Any],
    data_path: Path,
    runtime_root: Path,
    partition: str,
    usage_policy: str,
) -> Iterator[dict[str, Any]]:
    articles = {str(row["source_id"]): dict(row) for row in data["articles"]}
    if len(articles) != len(data["articles"]):
        raise RuntimeError(f"Duplicate Sol article source IDs: {data_path}")
    units_by_source: dict[str, list[dict[str, Any]]] = {source_id: [] for source_id in articles}
    raw_units_by_source: dict[str, list[dict[str, Any]]] = {source_id: [] for source_id in articles}
    seen_unit_ids: set[str] = set()
    for raw in data["units"]:
        source_id = str(raw["source_id"])
        if source_id not in units_by_source:
            raise RuntimeError(f"Sol issuer unit has unknown source ID: {raw['unit_id']}")
        if str(raw["unit_id"]) in seen_unit_ids:
            raise RuntimeError(f"Duplicate Sol issuer unit: {raw['unit_id']}")
        seen_unit_ids.add(str(raw["unit_id"]))
        units_by_source[source_id].append(_normalize_sol_unit(raw))
        raw_units_by_source[source_id].append(dict(raw))
    artifact_hash = _sha256_file(data_path)
    for source_id in sorted(articles):
        article = articles[source_id]
        units = sorted(units_by_source[source_id], key=lambda row: str(row["unit_id"]))
        yield _article_record(
            authority,
            source_id=source_id,
            source_timestamp=str(article["source_timestamp"]),
            authority_article_id=str(article["sample_id"]),
            partition=partition,
            usage_policy=usage_policy,
            source_hashes={"source_text_sha256": str(article["source_text_sha256"])},
            issuer_units=units,
            source_relative_path=_relative(data_path, runtime_root),
            source_artifact_sha256=artifact_hash,
            raw_payload={"article": article, "units": raw_units_by_source[source_id]},
        )


def _forecast_authority(
    root: Path, runtime_root: Path, index: int
) -> tuple[dict[str, Any], Iterator[dict[str, Any]]]:
    manifest_path = root / "gold_manifest.json"
    validation_path = root / "VALIDATION.json"
    ledger_path = root / "certification_ledger.jsonl"
    manifest = _read_json(manifest_path)
    validation = _read_json(validation_path)
    ledger = _read_jsonl(ledger_path)
    if manifest.get("version") != _FORECAST_GOLD_VERSION or manifest.get("status") != "complete":
        raise RuntimeError(f"Incomplete forecast gold authority: {root}")
    if validation.get("status") != "pass" or validation.get("authority_hashes_verified") is not True:
        raise RuntimeError(f"Forecast gold validation did not pass: {root}")
    if _sha256_file(manifest_path) != validation.get("gold_manifest_sha256"):
        raise RuntimeError(f"Forecast gold manifest hash mismatch: {root}")
    if _sha256_file(ledger_path) != manifest.get("authority", {}).get("ledger_sha256"):
        raise RuntimeError(f"Forecast gold ledger file hash mismatch: {root}")
    if sha256_json(ledger) != manifest.get("authority", {}).get("certified_set_sha256"):
        raise RuntimeError(f"Forecast certified-set hash mismatch: {root}")
    paths = sorted((root / "certified_labels").glob("*.json"), key=lambda path: path.stem)
    if len(paths) != int(manifest["population"]["certified_articles"]):
        raise RuntimeError(f"Forecast certified-label count mismatch: {root}")
    authority_id = f"forecast_full_source_gold_{index:02d}_{root.name}"
    authority = _authority_row(
        authority_id=authority_id,
        authority_version=_FORECAST_GOLD_VERSION,
        certification_level="blind_multi_pass_full_source_consensus",
        partition="full_source_gold",
        usage_policy="model_development_allowed",
        root=root,
        manifest_path=manifest_path,
        runtime_root=runtime_root,
        articles=len(paths),
    )

    def records() -> Iterator[dict[str, Any]]:
        for path in paths:
            document = _read_json(path)
            if document.get("contract_version") != _FORECAST_GOLD_VERSION:
                raise RuntimeError(f"Invalid forecast label contract: {path}")
            if document.get("certification", {}).get("status") != "certified":
                raise RuntimeError(f"Uncertified forecast label: {path}")
            units = [_normalize_forecast_unit(unit, str(document["source_id"])) for unit in document["issuer_units"]]
            yield _article_record(
                authority,
                source_id=str(document["source_id"]),
                source_timestamp=str(document["source_timestamp"]),
                authority_article_id=str(document["review_id"]),
                partition="full_source_gold",
                usage_policy="model_development_allowed",
                source_hashes={
                    "title_sha256": str(document["title_sha256"]),
                    "body_sha256": str(document["body_sha256"]),
                },
                issuer_units=units,
                source_relative_path=_relative(path, runtime_root),
                source_artifact_sha256=_sha256_file(path),
                raw_payload=document,
            )

    return authority, records()


def _normalize_manual_units(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    entities = {str(row["entity_id"]): row for row in document["entities"]}
    views = {str(row["entity_id"]): row for row in document["issuer_views"]}
    units = []
    for eligibility in document["eligibility"]:
        if eligibility.get("product") != "forecast_trigger":
            continue
        entity_id = str(eligibility["entity_id"])
        entity = entities.get(entity_id)
        if entity is None:
            raise RuntimeError(f"Manual eligibility has unknown entity: {document['sample_id']} {entity_id}")
        eligible = bool(eligibility["eligible"])
        view = views.get(entity_id)
        if eligible and view is None:
            label_sentiment = "unknown"
            normalization_status = "missing_eligible_sentiment"
        elif eligible:
            label_sentiment = str(view["composite_sentiment"])
            normalization_status = "complete"
        else:
            label_sentiment = "not_applicable"
            normalization_status = "complete"
        ticker = str(entity.get("ticker") or "")
        local_key = ticker or entity_id
        units.append({
            "unit_id": f"{document['source_id']}::{local_key}",
            "authority_unit_id": f"{document['sample_id']}::{entity_id}",
            "ticker": ticker,
            "entity_id": entity_id,
            "entity_kind": str(entity.get("entity_kind") or ""),
            "identity_status": str(entity.get("identity_status") or ""),
            "forecast_eligibility": "eligible" if eligible else "ineligible",
            "sentiment": label_sentiment,
            "reason_codes": list(eligibility.get("reasons", ())),
            "concepts": [],
            "gold_resolution": "source_bound_manual_certification",
            "normalization_status": normalization_status,
        })
    return sorted(units, key=lambda row: str(row["unit_id"]))


def _normalize_sol_unit(raw: Mapping[str, Any]) -> dict[str, Any]:
    gold = str(raw["gold_sentiment"])
    if gold not in _SENTIMENTS:
        raise RuntimeError(f"Invalid Sol gold sentiment: {raw['unit_id']} {gold}")
    return {
        "unit_id": f"{raw['source_id']}::{raw['entity_id']}",
        "authority_unit_id": str(raw["unit_id"]),
        "ticker": str(raw.get("ticker") or ""),
        "entity_id": str(raw["entity_id"]),
        "entity_kind": "security",
        "identity_status": "resolved",
        "forecast_eligibility": "eligible",
        "sentiment": gold,
        "reason_codes": ["source_forecast_eligible_population"],
        "concepts": list(raw.get("concepts", ())),
        "gold_resolution": str(raw.get("gold_resolution") or "frozen_converted_gold"),
        "normalization_status": "complete",
    }


def _normalize_forecast_unit(raw: Mapping[str, Any], source_id: str) -> dict[str, Any]:
    eligibility = str(raw["forecast_eligibility"])
    sentiment = str(raw["sentiment"])
    if eligibility not in {"eligible", "ineligible"}:
        raise RuntimeError(f"Unresolved forecast gold unit: {source_id} {raw['ticker']}")
    if eligibility == "ineligible" and sentiment != "not_applicable":
        raise RuntimeError(f"Ineligible forecast unit has sentiment: {source_id} {raw['ticker']}")
    if eligibility == "eligible" and sentiment not in _SENTIMENTS:
        raise RuntimeError(f"Eligible forecast unit lacks sentiment: {source_id} {raw['ticker']}")
    ticker = str(raw["ticker"])
    return {
        "unit_id": f"{source_id}::{ticker}",
        "authority_unit_id": f"{source_id}::{ticker}",
        "ticker": ticker,
        "entity_id": f"security:{ticker}",
        "entity_kind": "security",
        "identity_status": str(raw["identity_status"]),
        "forecast_eligibility": eligibility,
        "sentiment": sentiment,
        "reason_codes": list(raw.get("reason_codes", ())),
        "concepts": [],
        "gold_resolution": str(raw.get("gold_status") or "certified"),
        "normalization_status": "complete",
    }


def _article_record(
    authority: Mapping[str, Any],
    *,
    source_id: str,
    source_timestamp: str,
    authority_article_id: str,
    partition: str,
    usage_policy: str,
    source_hashes: Mapping[str, str],
    issuer_units: list[dict[str, Any]],
    source_relative_path: str,
    source_artifact_sha256: str,
    raw_payload: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "source_timestamp": source_timestamp,
        "authority_article_id": authority_article_id,
        "authority_id": authority["authority_id"],
        "authority_version": authority["authority_version"],
        "certification_level": authority["certification_level"],
        "partition": partition,
        "usage_policy": usage_policy,
        "source_hashes": dict(source_hashes),
        "article_forecast_eligible": any(
            unit["forecast_eligibility"] == "eligible" for unit in issuer_units
        ),
        "issuer_units": issuer_units,
        "lineage": {
            "source_relative_path": source_relative_path,
            "source_artifact_sha256": source_artifact_sha256,
            "authority_manifest_sha256": authority["manifest_sha256"],
        },
        "raw_gold_payload": raw_payload,
    }


def _authority_row(
    *,
    authority_id: str,
    authority_version: str,
    certification_level: str,
    partition: str,
    usage_policy: str,
    root: Path,
    manifest_path: Path,
    runtime_root: Path,
    articles: int,
) -> dict[str, Any]:
    return {
        "authority_id": authority_id,
        "authority_version": authority_version,
        "certification_level": certification_level,
        "partition": partition,
        "usage_policy": usage_policy,
        "articles": articles,
        "root_relative_path": _relative(root, runtime_root),
        "manifest_relative_path": _relative(manifest_path, runtime_root),
        "manifest_sha256": _sha256_file(manifest_path),
    }


def _expand_forecast_roots(paths: Iterable[Path]) -> list[Path]:
    expanded = []
    for path in paths:
        if (path / "gold_manifest.json").exists():
            expanded.append(path)
            continue
        chunks = sorted(
            child for child in path.glob("chunk_*") if (child / "gold_manifest.json").exists()
        )
        if not chunks:
            raise RuntimeError(f"No forecast gold authority found: {path}")
        expanded.extend(chunks)
    return expanded


def _file_summary(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        lines = sum(1 for _line in handle)
    return {"bytes": path.stat().st_size, "rows": lines, "sha256": _sha256_file(path)}


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected JSON object: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return list(_iter_jsonl(path))


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise RuntimeError(f"Expected JSON object: {path}:{line_number}")
            yield value


def _write_jsonl_row(handle: Any, value: Mapping[str, Any]) -> None:
    handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative(path: Path, runtime_root: Path) -> str:
    return path.resolve().relative_to(runtime_root.resolve()).as_posix()


def _require_within(path: Path, root: Path) -> None:
    try:
        path.relative_to(root)
    except ValueError as error:
        raise RuntimeError(f"Gold consolidation path is outside runtime root: {path}") from error
