from __future__ import annotations

import hashlib
import json
import shutil
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from .contracts import sha256_json
from .direct_trading_sentiment_audit import (
    article_source,
    build_benchmark_identity_snapshot,
)
from .engine import ENGINE_VERSION, NewsSynthesisEngine, _normalize_ticker_identifier
from .forecast_trigger_eligibility_audit import binary_metrics
from .gold_label_consolidation import DATASET_VERSION, validate_consolidated_gold
from .registry import ConceptRegistry


SPLIT_VERSION = "news_synthesis_consolidated_gold_split_v1"
INFERENCE_VERSION = "news_synthesis_consolidated_gold_inference_v1"
AUDIT_VERSION = "news_synthesis_consolidated_gold_audit_v1"
COMPARISON_VERSION = "news_synthesis_consolidated_gold_comparison_v1"
SOURCE_CATALOG_VERSION = "news_synthesis_canonical_source_catalog_v2"
DEFAULT_SEED = "news-synthesis-consolidated-gold-audit-v1"
_KNOWN_SENTIMENTS = ("positive", "negative", "neutral", "mixed")
_FORBIDDEN_SOURCE_FIELDS = frozenset({
    "eligibility",
    "evaluation_target_tickers",
    "forecast_eligibility",
    "gold",
    "gold_labels",
    "issuer_units",
    "issuer_views",
    "raw_gold_payload",
    "sentiment",
})
_PARTITION_FILES = {
    "audit": "audit_gold.jsonl",
    "development_test": "development_test_gold.jsonl",
    "final_test": "final_test_gold.jsonl",
}
_INFERENCE_CODE_AUTHORITY_FILES = (
    "engine.py",
    "synthesis.py",
    "facts.py",
    "concept_registry.json",
    "contracts.py",
    "direct_trading_sentiment_audit.py",
    "registry.py",
    "consolidated_gold_evaluator.py",
)
_AUDIT_CODE_AUTHORITY_FILES = (
    "consolidated_gold_evaluator.py",
    "forecast_trigger_eligibility_audit.py",
    "engine.py",
    "contracts.py",
)


def create_frozen_split(
    consolidated_root: Path,
    output_root: Path,
    *,
    runtime_root: Path,
    development_test_fraction: float = 0.20,
    seed: str = DEFAULT_SEED,
) -> dict[str, Any]:
    """Create an article-grouped, prediction-blind audit/test split.

    Existing final_evaluation_only records are preserved as a separate final
    test. Only model-development records participate in the deterministic
    audit/development-test allocation.
    """
    consolidated_root = consolidated_root.resolve()
    output_root = output_root.resolve()
    runtime_root = runtime_root.resolve()
    _require_within(consolidated_root, runtime_root)
    _require_within(output_root, runtime_root)
    if not 0.0 < development_test_fraction < 0.5:
        raise ValueError("development_test_fraction must be between 0 and 0.5")
    if output_root.exists():
        raise RuntimeError(f"Refusing to overwrite frozen split: {output_root}")
    validation = validate_consolidated_gold(consolidated_root)
    if validation["status"] != "pass":
        raise RuntimeError("Consolidated gold validation did not pass")
    consolidated_manifest = _read_json(consolidated_root / "manifest.json")
    rows = list(_iter_jsonl(consolidated_root / "gold_labels.jsonl"))
    development = [row for row in rows if row["usage_policy"] == "model_development_allowed"]
    final_test = [row for row in rows if row["usage_policy"] == "final_evaluation_only"]
    if len(development) + len(final_test) != len(rows):
        raise RuntimeError("Consolidated gold contains an unsupported usage policy")

    by_stratum: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in development:
        by_stratum[_split_stratum(row)].append(row)
    audit: list[dict[str, Any]] = []
    development_test: list[dict[str, Any]] = []
    for stratum in sorted(by_stratum):
        candidates = sorted(
            by_stratum[stratum],
            key=lambda row: (_split_digest(seed, str(row["source_id"])), str(row["source_id"])),
        )
        holdout_count = min(round(len(candidates) * development_test_fraction), len(candidates) - 1)
        development_test.extend(candidates[:holdout_count])
        audit.extend(candidates[holdout_count:])
    partitions = {
        "audit": sorted(audit, key=lambda row: str(row["source_id"])),
        "development_test": sorted(development_test, key=lambda row: str(row["source_id"])),
        "final_test": sorted(final_test, key=lambda row: str(row["source_id"])),
    }

    building = output_root.with_name(f"{output_root.name}.building")
    if building.exists():
        raise RuntimeError(f"Prior split staging exists: {building}")
    building.mkdir(parents=True)
    try:
        for partition, filename in _PARTITION_FILES.items():
            _write_jsonl(building / filename, partitions[partition])
            _write_json(
                building / f"{partition}_ids.json",
                {
                    "version": SPLIT_VERSION,
                    "partition": partition,
                    "source_ids": [str(row["source_id"]) for row in partitions[partition]],
                },
            )
        manifest = {
            "version": SPLIT_VERSION,
            "created_at_utc": datetime.now(UTC).isoformat(),
            "status": "complete",
            "prediction_blind": True,
            "article_grouped": True,
            "seed": seed,
            "development_test_fraction": development_test_fraction,
            "contract": {
                "iterative_partition": "audit",
                "development_holdout_partition": "development_test",
                "final_only_partition": "final_test",
                "final_test_requires_explicit_release": True,
                "issuer_units_follow_article_partition": True,
            },
            "authority": {
                "consolidated_version": consolidated_manifest["version"],
                "consolidated_manifest_sha256": _sha256_file(consolidated_root / "manifest.json"),
                "consolidated_article_ids_sha256": consolidated_manifest["article_source_ids_sha256"],
                "consolidated_unit_ids_sha256": consolidated_manifest["issuer_unit_ids_sha256"],
            },
            "population": {
                partition: _population_summary(partitions[partition])
                for partition in _PARTITION_FILES
            },
            "files": {
                name: _file_summary(building / name)
                for name in sorted((*_PARTITION_FILES.values(), *[f"{key}_ids.json" for key in _PARTITION_FILES]))
            },
        }
        manifest["partition_source_ids_sha256"] = {
            partition: sha256_json([str(row["source_id"]) for row in partitions[partition]])
            for partition in _PARTITION_FILES
        }
        _write_json(building / "manifest.json", manifest)
        validate_frozen_split(consolidated_root, building, runtime_root=runtime_root)
        building.replace(output_root)
    except Exception:
        shutil.rmtree(building, ignore_errors=True)
        raise
    return manifest


def validate_frozen_split(
    consolidated_root: Path,
    split_root: Path,
    *,
    runtime_root: Path,
) -> dict[str, Any]:
    consolidated_root = consolidated_root.resolve()
    split_root = split_root.resolve()
    runtime_root = runtime_root.resolve()
    _require_within(consolidated_root, runtime_root)
    _require_within(split_root, runtime_root)
    manifest = _read_json(split_root / "manifest.json")
    if manifest.get("version") != SPLIT_VERSION or manifest.get("status") != "complete":
        raise RuntimeError("Frozen split manifest is not complete")
    if _sha256_file(consolidated_root / "manifest.json") != manifest["authority"]["consolidated_manifest_sha256"]:
        raise RuntimeError("Frozen split consolidated authority changed")
    for name, expected in manifest["files"].items():
        if _file_summary(split_root / name) != expected:
            raise RuntimeError(f"Frozen split file integrity mismatch: {name}")
    consolidated = {str(row["source_id"]): row for row in _iter_jsonl(consolidated_root / "gold_labels.jsonl")}
    if len(consolidated) != sum(1 for _ in _iter_jsonl(consolidated_root / "gold_labels.jsonl")):
        raise RuntimeError("Consolidated authority contains duplicate source IDs")
    seen: set[str] = set()
    partition_counts: dict[str, dict[str, int]] = {}
    for partition, filename in _PARTITION_FILES.items():
        rows = list(_iter_jsonl(split_root / filename))
        ids = [str(row["source_id"]) for row in rows]
        if len(ids) != len(set(ids)):
            raise RuntimeError(f"Frozen split partition contains duplicate IDs: {partition}")
        if seen & set(ids):
            raise RuntimeError("Frozen split partitions overlap")
        seen.update(ids)
        if any(consolidated.get(source_id) != row for source_id, row in zip(ids, rows)):
            raise RuntimeError(f"Frozen split gold differs from consolidated authority: {partition}")
        if partition == "final_test" and any(row["usage_policy"] != "final_evaluation_only" for row in rows):
            raise RuntimeError("Final test includes model-development gold")
        if partition != "final_test" and any(row["usage_policy"] != "model_development_allowed" for row in rows):
            raise RuntimeError(f"Development partition includes final-only gold: {partition}")
        if sha256_json(ids) != manifest["partition_source_ids_sha256"][partition]:
            raise RuntimeError(f"Frozen split identity hash mismatch: {partition}")
        summary = _population_summary(rows)
        if summary != manifest["population"][partition]:
            raise RuntimeError(f"Frozen split population mismatch: {partition}")
        partition_counts[partition] = summary
    if seen != set(consolidated):
        raise RuntimeError("Frozen split does not cover the consolidated authority")
    report = {
        "version": f"{SPLIT_VERSION}_validation_v1",
        "validated_at_utc": datetime.now(UTC).isoformat(),
        "status": "pass",
        "partitions": partition_counts,
        "disjoint": True,
        "complete": True,
        "authority_hashes_verified": True,
    }
    _write_json(split_root / "VALIDATION.json", report)
    return report


def write_source_requirements(
    split_root: Path,
    output_path: Path,
    *,
    runtime_root: Path,
    partition: str = "audit",
) -> dict[str, Any]:
    split_root = split_root.resolve()
    output_path = output_path.resolve()
    runtime_root = runtime_root.resolve()
    _require_within(split_root, runtime_root)
    _require_within(output_path, runtime_root)
    rows = _load_partition(split_root, partition, allow_test=False)
    requirements = [
        {
            "source_id": row["source_id"],
            "source_timestamp": row["source_timestamp"],
            "authority_id": row["authority_id"],
            "authority_article_id": row["authority_article_id"],
            "source_hashes": row["source_hashes"],
            "lineage": row["lineage"],
            "evaluation_target_tickers": [
                unit["ticker"] for unit in row["issuer_units"] if unit.get("ticker")
            ],
        }
        for row in rows
    ]
    if output_path.exists():
        raise RuntimeError(f"Refusing to overwrite source requirements: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_jsonl(output_path, requirements)
    return _file_summary(output_path)


def certify_source_catalog(
    source_catalog_path: Path,
    source_artifacts: Iterable[Path],
    output_manifest_path: Path,
    *,
    runtime_root: Path,
) -> dict[str, Any]:
    """Freeze a source catalog against explicitly designated runtime artifacts.

    The caller owns the trust decision that the declared artifacts are the
    authoritative source corpus. This function proves exact record membership,
    lineage, and immutability; it does not promote an arbitrary runtime file to
    an independent upstream authority.
    """
    source_catalog_path = source_catalog_path.resolve()
    output_manifest_path = output_manifest_path.resolve()
    runtime_root = runtime_root.resolve()
    _require_within(source_catalog_path, runtime_root)
    _require_within(output_manifest_path, runtime_root)
    if output_manifest_path.exists():
        raise RuntimeError(
            f"Refusing to overwrite source catalog manifest: {output_manifest_path}"
        )
    rows = list(_iter_jsonl(source_catalog_path))
    source_ids = [str(row.get("source_id") or "") for row in rows]
    if "" in source_ids or len(source_ids) != len(set(source_ids)):
        raise RuntimeError("Source catalog has missing or duplicate source IDs")
    for row in rows:
        _validate_source_catalog_row(row)
    artifact_rows = []
    artifact_record_hashes: dict[str, set[str]] = {}
    seen_artifacts: set[Path] = set()
    declared_paths = [path.resolve() for path in source_artifacts]
    for declared in declared_paths:
        _require_within(declared, runtime_root)
        if not declared.exists():
            raise RuntimeError(f"Declared source artifact does not exist: {declared}")
    expanded_paths = sorted({
        path
        for declared in declared_paths
        for path in (
            (
                candidate.resolve()
                for candidate in declared.rglob("*")
                if candidate.is_file() and candidate.suffix.lower() in {".json", ".jsonl"}
            )
            if declared.is_dir()
            else (declared,)
        )
    })
    for path in expanded_paths:
        if path in seen_artifacts:
            continue
        seen_artifacts.add(path)
        if not path.is_file() or path in {source_catalog_path, output_manifest_path}:
            raise RuntimeError(f"Invalid canonical source artifact: {path}")
        relative = path.relative_to(runtime_root).as_posix()
        record_hash_list = [
            sha256_json(record) for record in _iter_source_artifact_records(path)
        ]
        record_hashes = set(record_hash_list)
        if not record_hashes:
            raise RuntimeError(f"Canonical source artifact has no records: {path}")
        if len(record_hashes) != len(record_hash_list):
            raise RuntimeError(f"Canonical source artifact has duplicate records: {path}")
        artifact_record_hashes[relative] = record_hashes
        artifact_rows.append({
            "runtime_relative_path": relative,
            "sha256": _sha256_file(path),
            "bytes": path.stat().st_size,
            "records": len(record_hashes),
            "record_hashes_sha256": sha256_json(sorted(record_hashes)),
        })
    if not artifact_rows:
        raise RuntimeError("At least one canonical source artifact is required")
    artifact_rows.sort(key=lambda row: str(row["runtime_relative_path"]))
    for row in rows:
        lineage = row["source_lineage"]
        relative = str(lineage["runtime_relative_path"])
        record_hash = str(lineage["artifact_record_sha256"])
        if relative not in artifact_record_hashes:
            raise RuntimeError(
                f"Source catalog row references an undeclared artifact: {relative}"
            )
        if record_hash != sha256_json(row["source_record"]):
            raise RuntimeError(
                f"Source catalog row record hash mismatch: {row['source_id']}"
            )
        if record_hash not in artifact_record_hashes[relative]:
            raise RuntimeError(
                f"Source catalog row is absent from its declared artifact: {row['source_id']}"
            )
    canonical_rows = [
        {"source_id": str(row["source_id"]), "sha256": sha256_json(row)}
        for row in sorted(rows, key=lambda item: str(item["source_id"]))
    ]
    manifest = {
        "version": SOURCE_CATALOG_VERSION,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "status": "complete",
        "contract": {
            "schema": "lineage-bound source record with deterministic adapter",
            "prediction_fields_forbidden": True,
            "gold_fields_forbidden": True,
            "source_artifacts_explicit": True,
            "source_record_membership_verified": True,
        },
        "source_catalog_file": _file_summary(source_catalog_path),
        "source_ids_sha256": sha256_json(sorted(source_ids)),
        "canonical_rows_sha256": sha256_json(canonical_rows),
        "source_artifacts": artifact_rows,
        "source_artifacts_sha256": sha256_json(artifact_rows),
    }
    _write_json(output_manifest_path, manifest)
    return manifest


def run_inference(
    split_root: Path,
    consolidated_root: Path,
    source_catalog_path: Path,
    source_catalog_manifest_path: Path,
    output_root: Path,
    *,
    runtime_root: Path,
    partition: str = "audit",
    allow_test: bool = False,
    workers: int = 1,
) -> dict[str, Any]:
    """Run the current engine on a lineage-verified source catalog.

    Gold tickers enter only as evaluation_target_tickers and never alter
    provider ticker candidates. The source manifest proves membership in the
    caller-designated source artifacts, whose upstream authority remains an
    explicit external trust decision.
    """
    split_root = split_root.resolve()
    consolidated_root = consolidated_root.resolve()
    source_catalog_path = source_catalog_path.resolve()
    source_catalog_manifest_path = source_catalog_manifest_path.resolve()
    output_root = output_root.resolve()
    runtime_root = runtime_root.resolve()
    for path in (
        split_root,
        consolidated_root,
        source_catalog_path,
        source_catalog_manifest_path,
        output_root,
    ):
        _require_within(path, runtime_root)
    validate_frozen_split(
        consolidated_root,
        split_root,
        runtime_root=runtime_root,
    )
    gold_rows = _load_partition(split_root, partition, allow_test=allow_test)
    source_rows = list(_iter_jsonl(source_catalog_path))
    source_catalog_manifest = _validate_source_catalog(
        source_catalog_path,
        source_catalog_manifest_path,
        source_rows,
        runtime_root,
    )
    catalog_by_id = {str(row.get("source_id") or ""): row for row in source_rows}
    if "" in catalog_by_id or len(catalog_by_id) != len(source_rows):
        raise RuntimeError("Source catalog has a missing or duplicate source_id")
    expected_ids = {str(row["source_id"]) for row in gold_rows}
    if set(catalog_by_id) != expected_ids:
        raise RuntimeError(
            "Source catalog identity mismatch: "
            f"missing={sorted(expected_ids - set(catalog_by_id))[:10]} "
            f"extra={sorted(set(catalog_by_id) - expected_ids)[:10]}"
        )
    gold_by_id = {str(row["source_id"]): row for row in gold_rows}
    source_by_id = {
        source_id: _canonical_source_from_catalog_row(catalog, gold_by_id[source_id])
        for source_id, catalog in catalog_by_id.items()
    }
    for gold in gold_rows:
        _validate_source_article(source_by_id[str(gold["source_id"])], gold)
    if workers < 1:
        raise ValueError("workers must be at least one")

    code_authority = _code_authority(_INFERENCE_CODE_AUTHORITY_FILES)
    partition_gold_sha256 = _sha256_file(split_root / _PARTITION_FILES[partition])
    evaluation_targets = [
        {
            "source_id": row["source_id"],
            "tickers": sorted(
                _normalize_ticker_identifier(unit.get("ticker"))
                for unit in row["issuer_units"]
                if _normalize_ticker_identifier(unit.get("ticker"))
            ),
        }
        for row in gold_rows
    ]
    evaluation_targets_sha256 = sha256_json(evaluation_targets)
    inference_fingerprint = sha256_json({
        "version": INFERENCE_VERSION,
        "engine_version": ENGINE_VERSION,
        "concept_registry_version": ConceptRegistry.load().version,
        "partition": partition,
        "partition_source_ids_sha256": sha256_json(sorted(expected_ids)),
        "split_manifest_sha256": _sha256_file(split_root / "manifest.json"),
        "partition_gold_sha256": partition_gold_sha256,
        "evaluation_targets_sha256": evaluation_targets_sha256,
        "source_catalog_sha256": _sha256_file(source_catalog_path),
        "source_catalog_manifest_sha256": _sha256_file(source_catalog_manifest_path),
        "code_authority": code_authority,
    })
    building = output_root.with_name(f"{output_root.name}.building")
    if output_root.exists():
        raise RuntimeError(f"Refusing to overwrite inference output: {output_root}")
    building.mkdir(parents=True, exist_ok=True)
    checkpoint_root = building / "checkpoints"
    checkpoint_root.mkdir(exist_ok=True)

    canonical_sources = [source_by_id[source_id] for source_id in sorted(source_by_id)]
    identity_index, identity_snapshot = build_benchmark_identity_snapshot(canonical_sources)
    engine = NewsSynthesisEngine(identity_index)
    gold_by_id = {str(row["source_id"]): row for row in gold_rows}

    def infer(source_id: str) -> dict[str, Any]:
        checkpoint_name = hashlib.sha256(source_id.encode()).hexdigest()
        checkpoint_path = checkpoint_root / f"{checkpoint_name}.json"
        if checkpoint_path.exists():
            checkpoint = _read_json(checkpoint_path)
            if checkpoint.get("inference_fingerprint") != inference_fingerprint:
                raise RuntimeError(f"Stale inference checkpoint: {source_id}")
            if checkpoint.get("source_id") != source_id:
                raise RuntimeError(f"Inference checkpoint identity mismatch: {source_id}")
            return checkpoint
        source_row = source_by_id[source_id]
        gold = gold_by_id[source_id]
        target_tickers = [str(unit["ticker"]) for unit in gold["issuer_units"] if unit.get("ticker")]
        try:
            prediction = engine.synthesize(
                article_source(source_row, additional_tickers=target_tickers)
            )
            error = None
        except Exception as exc:  # preserve every failure in the completed population
            prediction = None
            error = f"{type(exc).__name__}: {exc}"
        checkpoint = {
            "version": INFERENCE_VERSION,
            "inference_fingerprint": inference_fingerprint,
            "source_id": source_id,
            "source": source_row,
            "gold_source_hashes": gold["source_hashes"],
            "engine_version": ENGINE_VERSION,
            "prediction": prediction,
            "error": error,
        }
        _write_json_atomic(checkpoint_path, checkpoint)
        return checkpoint

    try:
        results: dict[str, dict[str, Any]] = {}
        if workers == 1:
            for source_id in sorted(expected_ids):
                results[source_id] = infer(source_id)
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(infer, source_id): source_id for source_id in sorted(expected_ids)}
                for future in as_completed(futures):
                    results[futures[future]] = future.result()
        ordered = [results[source_id] for source_id in sorted(expected_ids)]
        _write_jsonl(building / "predictions.jsonl", ordered)
        _write_json(building / "identity_snapshot.json", identity_snapshot)
        failures = [
            {"source_id": row["source_id"], "error": row["error"]}
            for row in ordered
            if row["error"]
        ]
        manifest = {
            "version": INFERENCE_VERSION,
            "generated_at_utc": datetime.now(UTC).isoformat(),
            "status": "complete",
            "partition": partition,
            "population": {
                "expected_articles": len(expected_ids),
                "predictions": len(ordered) - len(failures),
                "engine_failures": len(failures),
            },
            "authority": {
                "engine_version": ENGINE_VERSION,
                "concept_registry_version": ConceptRegistry.load().version,
                "inference_fingerprint": inference_fingerprint,
                "code_authority": code_authority,
                "source_catalog_sha256": _sha256_file(source_catalog_path),
                "source_catalog_manifest_sha256": _sha256_file(
                    source_catalog_manifest_path
                ),
                "source_catalog_authority_sha256": source_catalog_manifest[
                    "canonical_rows_sha256"
                ],
                "split_manifest_sha256": _sha256_file(split_root / "manifest.json"),
                "partition_gold_sha256": partition_gold_sha256,
                "evaluation_targets_sha256": evaluation_targets_sha256,
                "partition_source_ids_sha256": sha256_json(sorted(expected_ids)),
            },
            "files": {
                "predictions.jsonl": _file_summary(building / "predictions.jsonl"),
                "identity_snapshot.json": _file_summary(building / "identity_snapshot.json"),
            },
            "engine_failures": failures,
        }
        _write_json(building / "manifest.json", manifest)
        building.replace(output_root)
        return manifest
    except Exception:
        # Keep hash-bound checkpoints for a safe resume. A completed output is
        # never published until the entire expected population is accounted.
        raise


def evaluate_inference(
    split_root: Path,
    consolidated_root: Path,
    inference_root: Path,
    output_root: Path,
    *,
    runtime_root: Path,
    partition: str = "audit",
    allow_test: bool = False,
    mismatch_chunk_size: int = 25,
) -> dict[str, Any]:
    split_root = split_root.resolve()
    consolidated_root = consolidated_root.resolve()
    inference_root = inference_root.resolve()
    output_root = output_root.resolve()
    runtime_root = runtime_root.resolve()
    for path in (split_root, consolidated_root, inference_root, output_root):
        _require_within(path, runtime_root)
    if output_root.exists():
        raise RuntimeError(f"Refusing to overwrite audit output: {output_root}")
    if mismatch_chunk_size < 1:
        raise ValueError("mismatch_chunk_size must be at least one")
    validate_frozen_split(
        consolidated_root,
        split_root,
        runtime_root=runtime_root,
    )
    gold_rows = _load_partition(split_root, partition, allow_test=allow_test)
    inference_manifest = _read_json(inference_root / "manifest.json")
    if (
        inference_manifest.get("version") != INFERENCE_VERSION
        or inference_manifest.get("status") != "complete"
        or inference_manifest.get("partition") != partition
    ):
        raise RuntimeError("Inference manifest is incomplete or for a different partition")
    for name, expected in inference_manifest.get("files", {}).items():
        if _file_summary(inference_root / name) != expected:
            raise RuntimeError(f"Inference file integrity mismatch: {name}")
    current_targets_sha256 = sha256_json([
        {
            "source_id": row["source_id"],
            "tickers": sorted(
                _normalize_ticker_identifier(unit.get("ticker"))
                for unit in row["issuer_units"]
                if _normalize_ticker_identifier(unit.get("ticker"))
            ),
        }
        for row in gold_rows
    ])
    expected_inference_authority = inference_manifest["authority"]
    if expected_inference_authority.get("split_manifest_sha256") != _sha256_file(
        split_root / "manifest.json"
    ):
        raise RuntimeError("Inference split authority changed")
    if expected_inference_authority.get("partition_gold_sha256") != _sha256_file(
        split_root / _PARTITION_FILES[partition]
    ):
        raise RuntimeError("Inference gold partition changed")
    if expected_inference_authority.get("evaluation_targets_sha256") != current_targets_sha256:
        raise RuntimeError("Inference evaluation target scope changed")
    predictions = list(_iter_jsonl(inference_root / "predictions.jsonl"))
    prediction_by_id = {str(row["source_id"]): row for row in predictions}
    gold_by_id = {str(row["source_id"]): row for row in gold_rows}
    if len(prediction_by_id) != len(predictions) or set(prediction_by_id) != set(gold_by_id):
        raise RuntimeError("Inference and frozen gold populations differ")
    if sha256_json(sorted(prediction_by_id)) != inference_manifest["authority"][
        "partition_source_ids_sha256"
    ]:
        raise RuntimeError("Inference population identity hash mismatch")

    article_rows: list[dict[str, Any]] = []
    unit_rows: list[dict[str, Any]] = []
    audit_documents: list[dict[str, Any]] = []
    extra_units: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for source_id in sorted(gold_by_id):
        gold = gold_by_id[source_id]
        inference = prediction_by_id[source_id]
        prediction = inference.get("prediction")
        error = inference.get("error")
        if error:
            failures.append({"source_id": source_id, "error": str(error)})
        article, units, extras = _score_article(gold, prediction)
        article_rows.append(article)
        unit_rows.extend(units)
        extra_units.extend(extras)
        mismatch_types = sorted({
            *([] if article["confusion"] in {"TP", "TN"} else [f"article_{article['confusion'].lower()}"]),
            *(
                f"eligibility_{row['confusion'].lower()}"
                for row in units
                if row["eligibility_scored"] and row["confusion"] not in {"TP", "TN"}
            ),
            *("sentiment_mismatch" for row in units if row["sentiment_scored"] and not row["sentiment_exact"]),
            *("identity_or_coverage" for row in units if row["scoring_status"] != "scored"),
            *(
                "extra_prediction_eligible_unit"
                for row in extras
                if row["predicted_forecast_eligibility"] == "eligible"
            ),
            *("engine_failure" for _row in [error] if error),
        })
        audit_document = {
            "version": AUDIT_VERSION,
            "source_id": source_id,
            "partition": partition,
            "authority_id": gold["authority_id"],
            "source": inference.get("source"),
            "gold": gold,
            "prediction": _prediction_projection(prediction),
            "article_result": article,
            "issuer_unit_results": units,
            "extra_prediction_units": extras,
            "mismatch_types": mismatch_types,
            "requires_review": bool(mismatch_types),
        }
        audit_documents.append(audit_document)
        if mismatch_types:
            mismatches.append(audit_document)

    unit_rows.sort(key=lambda row: str(row["unit_id"]))
    article_rows.sort(key=lambda row: str(row["source_id"]))
    extra_units.sort(key=lambda row: (str(row["source_id"]), str(row["ticker"])))
    audit_documents.sort(key=lambda row: str(row["source_id"]))
    mismatches.sort(key=lambda row: str(row["source_id"]))
    building = output_root.with_name(f"{output_root.name}.building")
    if building.exists():
        raise RuntimeError(f"Prior audit staging exists: {building}")
    building.mkdir(parents=True)
    try:
        _write_jsonl(building / "articles.jsonl", article_rows)
        _write_jsonl(building / "issuer_units.jsonl", unit_rows)
        _write_jsonl(building / "extra_prediction_units.jsonl", extra_units)
        _write_jsonl(building / "audit_documents.jsonl", audit_documents)
        _write_jsonl(building / "mismatches.jsonl", mismatches)
        chunk_root = building / "mismatch_chunks"
        chunk_root.mkdir()
        chunk_rows = []
        for index, start in enumerate(range(0, len(mismatches), mismatch_chunk_size), start=1):
            batch = mismatches[start:start + mismatch_chunk_size]
            path = chunk_root / f"chunk_{index:04d}.jsonl"
            _write_jsonl(path, batch)
            chunk_rows.append({
                "chunk": index,
                "file": path.name,
                "articles": len(batch),
                "source_ids_sha256": sha256_json([row["source_id"] for row in batch]),
                "sha256": _sha256_file(path),
                "bytes": path.stat().st_size,
            })
        _write_json(building / "mismatch_chunks.json", {"chunks": chunk_rows})
        manifest = _audit_manifest(
            partition=partition,
            split_root=split_root,
            inference_root=inference_root,
            articles=article_rows,
            units=unit_rows,
            extras=extra_units,
            mismatches=mismatches,
            failures=failures,
            chunk_rows=chunk_rows,
        )
        manifest["files"] = {
            name: _file_summary(building / name)
            for name in (
                "articles.jsonl",
                "issuer_units.jsonl",
                "extra_prediction_units.jsonl",
                "audit_documents.jsonl",
                "mismatches.jsonl",
                "mismatch_chunks.json",
            )
        }
        _write_json(building / "manifest.json", manifest)
        _write_json(
            building / "VALIDATION.json",
            validate_audit(building, runtime_root=runtime_root, write_report=False),
        )
        building.replace(output_root)
        return manifest
    except Exception:
        shutil.rmtree(building, ignore_errors=True)
        raise


def validate_audit(
    audit_root: Path,
    *,
    runtime_root: Path,
    write_report: bool = True,
) -> dict[str, Any]:
    audit_root = audit_root.resolve()
    runtime_root = runtime_root.resolve()
    _require_within(audit_root, runtime_root)
    manifest = _read_json(audit_root / "manifest.json")
    if manifest.get("version") != AUDIT_VERSION or manifest.get("status") != "complete":
        raise RuntimeError("Audit manifest is not complete")
    audit_code_authority = manifest.get("authority", {}).get("audit_code_authority")
    if (
        not isinstance(audit_code_authority, list)
        or sha256_json(audit_code_authority)
        != manifest["authority"].get("audit_code_authority_sha256")
    ):
        raise RuntimeError("Audit scoring-code authority is invalid")
    for name, expected in manifest["files"].items():
        if _file_summary(audit_root / name) != expected:
            raise RuntimeError(f"Audit file integrity mismatch: {name}")
    articles = list(_iter_jsonl(audit_root / "articles.jsonl"))
    units = list(_iter_jsonl(audit_root / "issuer_units.jsonl"))
    documents = list(_iter_jsonl(audit_root / "audit_documents.jsonl"))
    mismatches = list(_iter_jsonl(audit_root / "mismatches.jsonl"))
    if len({row["source_id"] for row in articles}) != len(articles):
        raise RuntimeError("Audit articles contain duplicate source IDs")
    if len({row["unit_id"] for row in units}) != len(units):
        raise RuntimeError("Audit units contain duplicate unit IDs")
    if {row["source_id"] for row in articles} != {row["source_id"] for row in documents}:
        raise RuntimeError("Audit document population differs from article results")
    if sha256_json([row["source_id"] for row in articles]) != manifest["authority"]["article_population_sha256"]:
        raise RuntimeError("Audit article population hash mismatch")
    if sha256_json([row["unit_id"] for row in units]) != manifest["authority"]["issuer_unit_population_sha256"]:
        raise RuntimeError("Audit issuer-unit population hash mismatch")
    chunk_index = _read_json(audit_root / "mismatch_chunks.json")
    chunk_source_ids: list[str] = []
    seen_chunk_ids: set[str] = set()
    for expected_index, chunk in enumerate(chunk_index.get("chunks", ()), start=1):
        expected_name = f"chunk_{expected_index:04d}.jsonl"
        if chunk.get("chunk") != expected_index or chunk.get("file") != expected_name:
            raise RuntimeError("Mismatch chunk index is not contiguous and canonical")
        path = audit_root / "mismatch_chunks" / expected_name
        if not path.is_file():
            raise RuntimeError(f"Missing mismatch chunk: {expected_name}")
        if path.stat().st_size != chunk.get("bytes") or _sha256_file(path) != chunk.get("sha256"):
            raise RuntimeError(f"Mismatch chunk integrity mismatch: {expected_name}")
        rows = list(_iter_jsonl(path))
        ids = [str(row["source_id"]) for row in rows]
        if len(rows) != chunk.get("articles") or sha256_json(ids) != chunk.get("source_ids_sha256"):
            raise RuntimeError(f"Mismatch chunk population mismatch: {expected_name}")
        if seen_chunk_ids & set(ids):
            raise RuntimeError("Mismatch chunks overlap")
        seen_chunk_ids.update(ids)
        chunk_source_ids.extend(ids)
    mismatch_source_ids = [str(row["source_id"]) for row in mismatches]
    if chunk_source_ids != mismatch_source_ids:
        raise RuntimeError("Mismatch chunks do not exactly cover mismatch documents")
    report = {
        "version": f"{AUDIT_VERSION}_validation_v1",
        "validated_at_utc": datetime.now(UTC).isoformat(),
        "status": "pass",
        "articles": len(articles),
        "issuer_units": len(units),
        "mismatches": manifest["population"]["mismatch_articles"],
        "file_hashes_verified": True,
        "population_hashes_verified": True,
    }
    if write_report:
        _write_json(audit_root / "VALIDATION.json", report)
    return report


def compare_audits(
    previous_root: Path,
    current_root: Path,
    output_root: Path,
    *,
    runtime_root: Path,
) -> dict[str, Any]:
    previous_root = previous_root.resolve()
    current_root = current_root.resolve()
    output_root = output_root.resolve()
    runtime_root = runtime_root.resolve()
    for path in (previous_root, current_root, output_root):
        _require_within(path, runtime_root)
    if output_root.exists():
        raise RuntimeError(f"Refusing to overwrite audit comparison: {output_root}")
    previous_manifest = _read_json(previous_root / "manifest.json")
    current_manifest = _read_json(current_root / "manifest.json")
    if previous_manifest.get("partition") != current_manifest.get("partition"):
        raise RuntimeError("Audit comparison partitions differ")
    for key in ("article_population_sha256", "issuer_unit_population_sha256", "gold_labels_sha256"):
        if previous_manifest["authority"][key] != current_manifest["authority"][key]:
            raise RuntimeError(f"Audit comparison population or gold changed: {key}")
    validate_audit(previous_root, runtime_root=runtime_root, write_report=False)
    validate_audit(current_root, runtime_root=runtime_root, write_report=False)
    previous_units = {str(row["unit_id"]): row for row in _iter_jsonl(previous_root / "issuer_units.jsonl")}
    current_units = {str(row["unit_id"]): row for row in _iter_jsonl(current_root / "issuer_units.jsonl")}
    if set(previous_units) != set(current_units):
        raise RuntimeError("Audit comparison issuer-unit identities differ")
    previous_extras = list(_iter_jsonl(previous_root / "extra_prediction_units.jsonl"))
    current_extras = list(_iter_jsonl(current_root / "extra_prediction_units.jsonl"))
    changes = []
    for unit_id in sorted(previous_units):
        before, after = previous_units[unit_id], current_units[unit_id]
        before_error = _unit_error(before)
        after_error = _unit_error(after)
        eligibility_unchanged = before.get(
            "predicted_forecast_eligibility"
        ) == after.get("predicted_forecast_eligibility")
        sentiment_unchanged = before.get("predicted_sentiment") == after.get(
            "predicted_sentiment"
        )
        if before_error == after_error and eligibility_unchanged and sentiment_unchanged:
            continue
        changes.append({
            "unit_id": unit_id,
            "source_id": after["source_id"],
            "before_error": before_error,
            "after_error": after_error,
            "error_fixed": before_error and not after_error,
            "error_introduced": not before_error and after_error,
            "before": before,
            "after": after,
        })
    report = {
        "version": COMPARISON_VERSION,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "status": "complete",
        "authority": {
            "previous_manifest_sha256": _sha256_file(previous_root / "manifest.json"),
            "current_manifest_sha256": _sha256_file(current_root / "manifest.json"),
            "previous_audit_code_authority_sha256": previous_manifest["authority"][
                "audit_code_authority_sha256"
            ],
            "current_audit_code_authority_sha256": current_manifest["authority"][
                "audit_code_authority_sha256"
            ],
            "identical_population": True,
            "gold_changes": 0,
        },
        "summary": {
            "changed_units": len(changes),
            "errors_fixed": sum(bool(row["error_fixed"]) for row in changes),
            "errors_introduced": sum(bool(row["error_introduced"]) for row in changes),
            "extra_prediction_units_delta": len(current_extras) - len(previous_extras),
            "extra_prediction_eligible_units_delta": sum(
                row["predicted_forecast_eligibility"] == "eligible"
                for row in current_extras
            ) - sum(
                row["predicted_forecast_eligibility"] == "eligible"
                for row in previous_extras
            ),
        },
        "metric_delta": _metric_delta(previous_manifest["metrics"], current_manifest["metrics"]),
    }
    output_root.mkdir(parents=True)
    _write_jsonl(output_root / "changed_units.jsonl", changes)
    _write_json(output_root / "manifest.json", report)
    return report


def _score_article(
    gold: Mapping[str, Any], prediction: Mapping[str, Any] | None
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    prediction = prediction or {}
    entities = {str(row.get("entity_id") or ""): row for row in prediction.get("entities", ())}
    views = {str(row.get("entity_id") or ""): row for row in prediction.get("issuer_views", ())}
    by_ticker: dict[str, list[dict[str, Any]]] = defaultdict(list)
    all_predicted = []
    for eligibility in prediction.get("eligibility", ()):
        if eligibility.get("product") != "forecast_trigger":
            continue
        entity_id = str(eligibility.get("entity_id") or "")
        entity = entities.get(entity_id, {})
        ticker = _normalize_ticker_identifier(entity.get("ticker"))
        if not ticker:
            continue
        row = {
            "entity_id": entity_id,
            "ticker": ticker,
            "eligible": bool(eligibility.get("eligible")),
            "sentiment": views.get(entity_id, {}).get("composite_sentiment"),
            "reasons": list(eligibility.get("reasons") or ()),
            "blocking_flags": list(eligibility.get("blocking_flags") or ()),
        }
        by_ticker[ticker].append(row)
        all_predicted.append(row)
    gold_tickers = {
        _normalize_ticker_identifier(unit.get("ticker"))
        for unit in gold["issuer_units"]
        if _normalize_ticker_identifier(unit.get("ticker"))
    }
    units = []
    for unit in gold["issuer_units"]:
        ticker = _normalize_ticker_identifier(unit.get("ticker"))
        candidates = by_ticker.get(ticker, [])
        eligibility_scored = bool(ticker)
        if not ticker:
            candidate = {}
            predicted_eligibility = "ineligible"
            predicted_sentiment = None
            scoring_status = "gold_unit_without_ticker"
        elif len(candidates) == 1:
            candidate = candidates[0]
            predicted_eligibility = "eligible" if candidate["eligible"] else "ineligible"
            predicted_sentiment = candidate["sentiment"]
            scoring_status = "scored"
        elif not candidates:
            candidate = {}
            predicted_eligibility = "ineligible"
            predicted_sentiment = None
            scoring_status = "engine_failure" if not prediction else "prediction_identity_unresolved"
        else:
            candidate = {}
            predicted_eligibility = "ineligible"
            predicted_sentiment = None
            scoring_status = "prediction_identity_ambiguous"
        gold_eligibility = str(unit["forecast_eligibility"])
        gold_sentiment = str(unit["sentiment"])
        sentiment_scored = gold_eligibility == "eligible" and gold_sentiment in _KNOWN_SENTIMENTS
        sentiment_exact = sentiment_scored and predicted_sentiment == gold_sentiment
        units.append({
            "unit_id": unit["unit_id"],
            "source_id": gold["source_id"],
            "authority_id": gold["authority_id"],
            "ticker": unit.get("ticker"),
            "gold_forecast_eligibility": gold_eligibility,
            "predicted_forecast_eligibility": predicted_eligibility,
            "confusion": _confusion(gold_eligibility == "eligible", predicted_eligibility == "eligible"),
            "eligibility_scored": eligibility_scored,
            "gold_sentiment": gold_sentiment,
            "predicted_sentiment": predicted_sentiment,
            "sentiment_scored": sentiment_scored,
            "sentiment_exact": sentiment_exact,
            "normalization_status": unit["normalization_status"],
            "scoring_status": scoring_status,
            "prediction_entity_id": candidate.get("entity_id", ""),
            "prediction_reasons": candidate.get("reasons", [scoring_status]),
            "prediction_blocking_flags": candidate.get("blocking_flags", []),
        })
    extras = [
        {
            "source_id": gold["source_id"],
            "authority_id": gold["authority_id"],
            "ticker": row["ticker"],
            "prediction_entity_id": row["entity_id"],
            "predicted_forecast_eligibility": "eligible" if row["eligible"] else "ineligible",
            "predicted_sentiment": row["sentiment"],
            "prediction_reasons": row["reasons"],
        }
        for row in all_predicted
        if row["ticker"] not in gold_tickers
    ]
    article_gold = bool(gold["article_forecast_eligible"])
    article_predicted = any(
        bool(row["eligible"])
        for ticker in gold_tickers
        for row in by_ticker.get(ticker, ())
    )
    article = {
        "source_id": gold["source_id"],
        "authority_id": gold["authority_id"],
        "gold_forecast_eligible": article_gold,
        "predicted_forecast_eligible": article_predicted,
        "confusion": _confusion(article_gold, article_predicted),
        "issuer_units": len(units),
        "extra_prediction_units": len(extras),
        "engine_failure": not bool(prediction),
    }
    return article, units, extras


def _audit_manifest(
    *,
    partition: str,
    split_root: Path,
    inference_root: Path,
    articles: Sequence[Mapping[str, Any]],
    units: Sequence[Mapping[str, Any]],
    extras: Sequence[Mapping[str, Any]],
    mismatches: Sequence[Mapping[str, Any]],
    failures: Sequence[Mapping[str, str]],
    chunk_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    inference_manifest = _read_json(inference_root / "manifest.json")
    sentiment_rows = [row for row in units if row["sentiment_scored"]]
    sentiment = _sentiment_metrics(sentiment_rows)
    eligibility_units = [
        {
            "confusion": row["confusion"],
        }
        for row in units
        if row["eligibility_scored"]
    ]
    by_authority = {}
    for authority in sorted({str(row["authority_id"]) for row in articles}):
        authority_articles = [row for row in articles if row["authority_id"] == authority]
        authority_units = [row for row in units if row["authority_id"] == authority]
        authority_scored_units = [row for row in authority_units if row["eligibility_scored"]]
        by_authority[authority] = {
            "articles": len(authority_articles),
            "issuer_units": len(authority_units),
            "article_eligibility": binary_metrics(authority_articles),
            "issuer_eligibility": binary_metrics(authority_scored_units),
            "sentiment": _sentiment_metrics([row for row in authority_units if row["sentiment_scored"]]),
        }
    audit_code_authority = _code_authority(_AUDIT_CODE_AUTHORITY_FILES)
    return {
        "version": AUDIT_VERSION,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "status": "complete",
        "partition": partition,
        "selection": (
            "Frozen prediction-blind consolidated-gold partition scored at article and "
            "issuer-unit levels. Article eligibility is computed over gold-scoped tickers; "
            "extra predicted tickers are unscored coverage diagnostics."
        ),
        "sentiment_scope": (
            "Gold-eligible issuer units with positive, negative, neutral, or mixed "
            "gold sentiment; unknown and not_applicable excluded."
        ),
        "authority": {
            "engine_version": inference_manifest["authority"]["engine_version"],
            "inference_code_authority": inference_manifest["authority"]["code_authority"],
            "audit_code_authority": audit_code_authority,
            "audit_code_authority_sha256": sha256_json(audit_code_authority),
            "split_manifest_sha256": _sha256_file(split_root / "manifest.json"),
            "inference_manifest_sha256": _sha256_file(inference_root / "manifest.json"),
            "gold_labels_sha256": _sha256_file(split_root / _PARTITION_FILES[partition]),
            "article_population_sha256": sha256_json([row["source_id"] for row in articles]),
            "issuer_unit_population_sha256": sha256_json([row["unit_id"] for row in units]),
        },
        "population": {
            "articles": len(articles),
            "issuer_units": len(units),
            "extra_prediction_units": len(extras),
            "mismatch_articles": len(mismatches),
            "mismatch_chunks": len(chunk_rows),
            "engine_failures": len(failures),
        },
        "metrics": {
            "article_forecast_eligibility": binary_metrics(articles),
            "issuer_forecast_eligibility": binary_metrics(eligibility_units),
            "issuer_sentiment": sentiment,
            "by_authority": by_authority,
        },
        "coverage": {
            "unit_scoring_status": dict(sorted(Counter(str(row["scoring_status"]) for row in units).items())),
            "eligibility_scored_units": len(eligibility_units),
            "eligibility_excluded_units": len(units) - len(eligibility_units),
            "sentiment_scored_units": len(sentiment_rows),
            "sentiment_excluded_units": len(units) - len(sentiment_rows),
            "extra_prediction_units": len(extras),
            "extra_prediction_eligible_units": sum(
                row["predicted_forecast_eligibility"] == "eligible" for row in extras
            ),
        },
        "engine_failures": list(failures),
    }


def _sentiment_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    confusion = {
        gold: {predicted: 0 for predicted in (*_KNOWN_SENTIMENTS, "missing")}
        for gold in _KNOWN_SENTIMENTS
    }
    for row in rows:
        gold = str(row["gold_sentiment"])
        predicted = str(row.get("predicted_sentiment") or "missing")
        if predicted not in _KNOWN_SENTIMENTS:
            predicted = "missing"
        confusion[gold][predicted] += 1
    per_class = {}
    for label in _KNOWN_SENTIMENTS:
        tp = confusion[label][label]
        fn = sum(confusion[label].values()) - tp
        fp = sum(confusion[other][label] for other in _KNOWN_SENTIMENTS if other != label)
        precision = _ratio(tp, tp + fp)
        recall = _ratio(tp, tp + fn)
        per_class[label] = {
            "support": sum(confusion[label].values()),
            "precision": precision,
            "recall": recall,
            "f1": _ratio(2 * precision * recall, precision + recall),
        }
    return {
        "scored_units": len(rows),
        "exact": sum(confusion[label][label] for label in _KNOWN_SENTIMENTS),
        "accuracy": _ratio(sum(confusion[label][label] for label in _KNOWN_SENTIMENTS), len(rows)),
        "macro_f1": _ratio(sum(per_class[label]["f1"] for label in _KNOWN_SENTIMENTS), len(_KNOWN_SENTIMENTS)),
        "confusion": confusion,
        "per_class": per_class,
    }


def _load_partition(split_root: Path, partition: str, *, allow_test: bool) -> list[dict[str, Any]]:
    if partition not in _PARTITION_FILES:
        raise ValueError(f"Unknown frozen partition: {partition}")
    if partition != "audit" and not allow_test:
        raise RuntimeError(
            f"Partition {partition} is sealed from iterative use; pass the explicit release flag"
        )
    manifest = _read_json(split_root / "manifest.json")
    if manifest.get("version") != SPLIT_VERSION or manifest.get("status") != "complete":
        raise RuntimeError("Frozen split manifest is incomplete")
    filename = _PARTITION_FILES[partition]
    expected_file = manifest.get("files", {}).get(filename)
    if expected_file != _file_summary(split_root / filename):
        raise RuntimeError(f"Frozen split partition integrity mismatch: {partition}")
    rows = list(_iter_jsonl(split_root / filename))
    source_ids = [str(row.get("source_id") or "") for row in rows]
    if "" in source_ids or len(source_ids) != len(set(source_ids)):
        raise RuntimeError(f"Frozen split partition has missing or duplicate IDs: {partition}")
    if sha256_json(source_ids) != manifest["partition_source_ids_sha256"][partition]:
        raise RuntimeError(f"Frozen split partition identity mismatch: {partition}")
    expected_policy = (
        "final_evaluation_only" if partition == "final_test" else "model_development_allowed"
    )
    if any(row.get("usage_policy") != expected_policy for row in rows):
        raise RuntimeError(f"Frozen split partition violates usage policy: {partition}")
    return rows


def _validate_source_article(source: Mapping[str, Any], gold: Mapping[str, Any]) -> None:
    _validate_source_catalog_row_canonical(source)
    if str(source.get("source_id") or "") != str(gold["source_id"]):
        raise RuntimeError("Source catalog row does not match gold source_id")
    if str(source.get("source_timestamp") or "") != str(gold["source_timestamp"]):
        raise RuntimeError(f"Source timestamp mismatch: {gold['source_id']}")
    publication = source.get("publication", {})
    rendered = source.get("rendered_product", {})
    title = str(publication.get("title") or "")
    text = str(rendered.get("text") or "")
    canonical_text = text or title
    hashes = gold["source_hashes"]
    checks = {
        "title_sha256": hashlib.sha256(title.encode()).hexdigest(),
        "body_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "source_text_sha256": hashlib.sha256(canonical_text.encode()).hexdigest(),
    }
    for name, expected in hashes.items():
        if name in checks and checks[name] != expected:
            raise RuntimeError(f"Source text hash mismatch: {gold['source_id']} {name}")
    if not text and not title:
        raise RuntimeError(f"Source catalog has no text: {gold['source_id']}")


def _validate_source_catalog_row(source: Mapping[str, Any]) -> None:
    source_id = str(source.get("source_id") or "")
    leaked = sorted(_FORBIDDEN_SOURCE_FIELDS & set(source))
    if leaked:
        raise RuntimeError(
            f"Source catalog contains prediction or gold fields: {source_id} {leaked}"
        )
    if not source_id:
        raise RuntimeError("Source catalog row lacks source_id")
    if source.get("source_schema") not in {
        "canonical_benchmark_article_v1",
        "forecast_blind_full_source_v1",
    }:
        raise RuntimeError(f"Unsupported source catalog schema: {source_id}")
    record = source.get("source_record")
    lineage = source.get("source_lineage")
    if not isinstance(record, Mapping) or not isinstance(lineage, Mapping):
        raise RuntimeError(f"Source catalog row lacks record lineage: {source_id}")
    if not str(lineage.get("runtime_relative_path") or "") or not str(
        lineage.get("artifact_record_sha256") or ""
    ):
        raise RuntimeError(f"Source catalog row has incomplete lineage: {source_id}")


def _canonical_source_from_catalog_row(
    catalog: Mapping[str, Any],
    gold: Mapping[str, Any],
) -> dict[str, Any]:
    """Derive the only permitted inference input from the lineage-bound record."""
    _validate_source_catalog_row(catalog)
    source_id = str(catalog["source_id"])
    record = dict(catalog["source_record"])
    schema = str(catalog["source_schema"])
    if schema == "canonical_benchmark_article_v1":
        if str(record.get("source_id") or "") != source_id:
            raise RuntimeError(f"Canonical source record identity mismatch: {source_id}")
        publication = record.get("publication") or {}
        rendered = record.get("rendered_product") or {}
        source = {
            "source_id": source_id,
            "source_timestamp": str(record.get("source_timestamp") or ""),
            "source_text_sha256": str(record.get("source_text_sha256") or ""),
            "publication": {
                key: publication[key]
                for key in (
                    "title",
                    "teaser",
                    "author",
                    "article_url",
                    "url_domain",
                    "provider_tickers",
                    "channels",
                    "provider_tags",
                    "content_quality_flags",
                )
                if key in publication
            },
            "rendered_product": {
                key: rendered[key]
                for key in ("text", "quality_flags", "source_count")
                if key in rendered
            },
            "point_in_time_issuer_candidates": list(
                record.get("point_in_time_issuer_candidates") or ()
            ),
        }
    else:
        review_id = str(record.get("review_id") or "")
        if review_id != str(gold.get("authority_article_id") or ""):
            raise RuntimeError(f"Forecast source review identity mismatch: {source_id}")
        title = str(record.get("title") or "")
        body = str(record.get("full_rendered_body") or "")
        source = {
            "source_id": source_id,
            "source_timestamp": str(record.get("published_at_utc") or ""),
            "publication": {
                "title": title,
                "author": str(record.get("author") or ""),
                "url_domain": str(record.get("provider_domain") or ""),
                "provider_tickers": list(record.get("provider_tickers") or ()),
                "channels": list(record.get("channels") or ()),
                "provider_tags": list(record.get("provider_tags") or ()),
            },
            "rendered_product": {
                "text": body,
                "quality_flags": list(record.get("quality_flags") or ()),
                "source_count": 1,
            },
        }
    _validate_source_catalog_row_canonical(source)
    return source


def _validate_source_catalog_row_canonical(source: Mapping[str, Any]) -> None:
    source_id = str(source.get("source_id") or "")
    if not source_id or not str(source.get("source_timestamp") or ""):
        raise RuntimeError("Canonical source lacks source_id or source_timestamp")
    publication = source.get("publication")
    rendered = source.get("rendered_product")
    if not isinstance(publication, Mapping) or not isinstance(rendered, Mapping):
        raise RuntimeError(f"Canonical source row is invalid: {source_id}")
    if not str(publication.get("title") or "") and not str(rendered.get("text") or ""):
        raise RuntimeError(f"Canonical source has no text: {source_id}")


def _iter_source_artifact_records(path: Path) -> Iterator[Mapping[str, Any]]:
    if path.suffix.lower() == ".jsonl":
        yield from _iter_jsonl(path)
        return
    value = _read_json(path)
    if isinstance(value, list):
        for record in value:
            if not isinstance(record, Mapping):
                raise RuntimeError(f"Source artifact has a non-object record: {path}")
            yield record
        return
    if isinstance(value, Mapping):
        yield value
        return
    raise RuntimeError(f"Unsupported source artifact structure: {path}")


def _validate_source_catalog(
    source_catalog_path: Path,
    manifest_path: Path,
    rows: Sequence[Mapping[str, Any]],
    runtime_root: Path,
) -> dict[str, Any]:
    manifest = _read_json(manifest_path)
    if (
        manifest.get("version") != SOURCE_CATALOG_VERSION
        or manifest.get("status") != "complete"
    ):
        raise RuntimeError("Source catalog authority manifest is incomplete")
    expected_file = manifest.get("source_catalog_file")
    if expected_file != _file_summary(source_catalog_path):
        raise RuntimeError("Source catalog file integrity mismatch")
    source_ids = [str(row.get("source_id") or "") for row in rows]
    if "" in source_ids or len(source_ids) != len(set(source_ids)):
        raise RuntimeError("Source catalog authority has missing or duplicate IDs")
    if sha256_json(sorted(source_ids)) != manifest.get("source_ids_sha256"):
        raise RuntimeError("Source catalog authority identity hash mismatch")
    canonical_rows = [
        {"source_id": str(row["source_id"]), "sha256": sha256_json(row)}
        for row in sorted(rows, key=lambda item: str(item["source_id"]))
    ]
    if sha256_json(canonical_rows) != manifest.get("canonical_rows_sha256"):
        raise RuntimeError("Source catalog canonical-row hash mismatch")
    artifacts = list(manifest.get("source_artifacts") or ())
    if not artifacts:
        raise RuntimeError("Source catalog authority has no source artifacts")
    seen_paths: set[str] = set()
    record_hashes_by_path: dict[str, set[str]] = {}
    for artifact in artifacts:
        relative = str(artifact.get("runtime_relative_path") or "")
        if not relative or relative in seen_paths:
            raise RuntimeError("Source catalog authority has invalid artifact lineage")
        seen_paths.add(relative)
        path = (runtime_root / relative).resolve()
        _require_within(path, runtime_root)
        if not path.is_file() or _sha256_file(path) != artifact.get("sha256"):
            raise RuntimeError(f"Source catalog artifact integrity mismatch: {relative}")
        record_hash_list = [
            sha256_json(record) for record in _iter_source_artifact_records(path)
        ]
        record_hashes = set(record_hash_list)
        if (
            len(record_hashes) != len(record_hash_list)
            or len(record_hashes) != artifact.get("records")
            or sha256_json(sorted(record_hashes)) != artifact.get("record_hashes_sha256")
        ):
            raise RuntimeError(f"Source catalog artifact record mismatch: {relative}")
        record_hashes_by_path[relative] = record_hashes
    for row in rows:
        _validate_source_catalog_row(row)
        lineage = row["source_lineage"]
        relative = str(lineage["runtime_relative_path"])
        record_hash = str(lineage["artifact_record_sha256"])
        if record_hash != sha256_json(row["source_record"]):
            raise RuntimeError(f"Source catalog row record hash mismatch: {row['source_id']}")
        if record_hash not in record_hashes_by_path.get(relative, set()):
            raise RuntimeError(
                f"Source catalog row is absent from its declared artifact: {row['source_id']}"
            )
    return manifest


def _prediction_projection(prediction: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not prediction:
        return None
    keys = (
        "contract_version",
        "concept_registry_version",
        "sample_id",
        "source_id",
        "source_timestamp",
        "source_text_sha256",
        "envelope",
        "entities",
        "statements",
        "participations",
        "issuer_views",
        "eligibility",
        "quality_flags",
        "production",
    )
    return {key: prediction[key] for key in keys if key in prediction}


def _split_stratum(row: Mapping[str, Any]) -> str:
    eligible_sentiments = sorted(
        str(unit["sentiment"])
        for unit in row["issuer_units"]
        if unit["forecast_eligibility"] == "eligible"
    )
    ineligible = sum(unit["forecast_eligibility"] == "ineligible" for unit in row["issuer_units"])
    return "|".join((
        str(row["authority_id"]),
        "eligible" if row["article_forecast_eligible"] else "ineligible",
        ",".join(eligible_sentiments) or "none",
        "has_ineligible" if ineligible else "no_ineligible",
    ))


def _population_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "articles": len(rows),
        "issuer_units": sum(len(row["issuer_units"]) for row in rows),
        "article_eligible": sum(bool(row["article_forecast_eligible"]) for row in rows),
        "article_ineligible": sum(not bool(row["article_forecast_eligible"]) for row in rows),
        "by_authority": dict(sorted(Counter(str(row["authority_id"]) for row in rows).items())),
    }


def _code_authority(files: Sequence[str]) -> list[dict[str, str]]:
    root = Path(__file__).parent
    return [
        {"file": name, "sha256": _sha256_file(root / name)}
        for name in files
    ]


def _metric_delta(previous: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, Any]:
    output = {}
    for section in ("article_forecast_eligibility", "issuer_forecast_eligibility"):
        output[section] = {
            key: float(current[section][key]) - float(previous[section][key])
            for key in ("precision", "recall", "specificity", "f1", "balanced_accuracy", "raw_accuracy")
        }
    output["issuer_sentiment"] = {
        key: float(current["issuer_sentiment"][key]) - float(previous["issuer_sentiment"][key])
        for key in ("accuracy", "macro_f1")
    }
    return output


def _unit_error(row: Mapping[str, Any]) -> bool:
    return row["confusion"] not in {"TP", "TN"} or (
        bool(row["sentiment_scored"]) and not bool(row["sentiment_exact"])
    ) or row["scoring_status"] != "scored"


def _confusion(gold: bool, predicted: bool) -> str:
    return "TP" if gold and predicted else "FN" if gold else "FP" if predicted else "TN"


def _ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _split_digest(seed: str, source_id: str) -> str:
    return hashlib.sha256(f"{seed}|{source_id}".encode()).hexdigest()


def _file_summary(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        rows = sum(1 for _line in handle)
    return {"bytes": path.stat().st_size, "rows": rows, "sha256": _sha256_file(path)}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected JSON object: {path}")
    return value


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise RuntimeError(f"Expected JSON object: {path}:{line_number}")
            yield value


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_json_atomic(path: Path, value: Any) -> None:
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_jsonl(path: Path, values: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for value in values:
            handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


def _require_within(path: Path, runtime_root: Path) -> None:
    try:
        path.resolve().relative_to(runtime_root.resolve())
    except ValueError as error:
        raise RuntimeError(f"Evaluator path is outside runtime root: {path}") from error
