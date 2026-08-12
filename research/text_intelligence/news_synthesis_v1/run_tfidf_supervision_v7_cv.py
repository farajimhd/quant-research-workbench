from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from .embedding_supervision import (
    DEFAULT_DATA_ROOT,
    TFIDF_V7_DATASET_VERSION,
    assert_runtime_path,
    canonical_json_sha256,
    dataset_file_manifest,
    l2_normalize,
    match_issuer_embedding,
    read_jsonl,
    save_array,
    validate_prepared_dataset,
    write_json,
    write_jsonl,
    _binary_class_metrics,
    _multiclass_metrics,
    _multilabel_metrics,
)
from .run_embedding_supervision import _clickhouse_client, train_model
from .run_tfidf_supervision_v2 import _metrics
from .run_tfidf_supervision_v5 import _train_args
from .storage import load_identity_index
from .tfidf_supervision_v4 import _load_canonical_documents
from .tfidf_supervision_v5 import (
    DEFAULT_RAW_DRIVE_ROOT,
    _load_original_documents,
    original_body_text,
)
from .tfidf_supervision_v7 import (
    invariant_metadata_features,
    fit_v7_vocabulary_from_document_frequency,
    point_in_time_aliases,
    tfidf_v7_feature_counts,
    transform_v7_counts,
    v7_view_indexes,
    V7_FIELD_BUDGETS,
)


DEFAULT_TFIDF_V7_CV_ROOT = (
    Path(r"D:\TradingML\runtimes")
    / "text_intelligence"
    / "news_synthesis_v1"
    / "tfidf_supervision_v7_cv5"
)
DEFAULT_FOLDS = 5
DEFAULT_CV_SEED = "news-synthesis-tfidf-v7-cv5"
DEFAULT_NEWS_SYNTHESIS_AUDIT_DOCUMENTS = (
    Path(r"D:\TradingML\runtimes")
    / "text_intelligence"
    / "news_synthesis_v1"
    / "consolidated_gold_audit_v1_v48_baseline_20260810"
    / "audit_evaluation_v48_final_frozen_v7_20260811"
    / "audit_documents.jsonl"
)
DEFAULT_NEWS_SYNTHESIS_GENERALIZATION_ROOT = (
    Path(r"D:\TradingML\runtimes")
    / "text_intelligence"
    / "news_synthesis_v1"
    / "consolidated_generalization_evaluation_v48_20260812"
)


@dataclass(frozen=True)
class CrossValidationFeatureSpec:
    dataset_version: str
    experiment: str
    comparison_key: str
    representation_kind: str
    feature_counter: Callable[..., Counter[str]]
    budgets: Mapping[str, int]
    view_indexes: Callable[[Mapping[str, int]], Mapping[str, np.ndarray]]
    feature_metadata: Mapping[str, Any]
    vocabulary_fitter: Callable[..., tuple[tuple[str, ...], np.ndarray, dict[str, Any]]] | None = None


V7_CV_FEATURE_SPEC = CrossValidationFeatureSpec(
    dataset_version=TFIDF_V7_DATASET_VERSION,
    experiment="tfidf_v7_grouped_stratified_cv5",
    comparison_key="tfidf_v7_cross_validated",
    representation_kind="tfidf_v7",
    feature_counter=tfidf_v7_feature_counts,
    budgets=V7_FIELD_BUDGETS,
    view_indexes=v7_view_indexes,
    feature_metadata={
        "feature_version": "v7",
        "feature_only_change": True,
    },
)


def deterministic_stratified_folds(
    source_ids: Sequence[str],
    labels: Sequence[int],
    *,
    fold_count: int,
    seed: str,
) -> dict[str, int]:
    if fold_count < 2:
        raise ValueError("Cross-validation requires at least two folds")
    if len(source_ids) != len(labels) or len(set(source_ids)) != len(source_ids):
        raise ValueError("Source IDs and labels must be aligned and unique")
    by_label: dict[int, list[str]] = defaultdict(list)
    for source_id, label in zip(source_ids, labels):
        by_label[int(label)].append(str(source_id))
    result: dict[str, int] = {}
    for label, values in sorted(by_label.items()):
        ordered = sorted(
            values,
            key=lambda value: hashlib.sha256(
                f"{seed}\0{label}\0{value}".encode("utf-8")
            ).digest(),
        )
        for index, source_id in enumerate(ordered):
            result[source_id] = index % fold_count
    return result


def _subset_development_authority(
    source_data_root: Path,
    v7_data_root: Path,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, np.ndarray],
    dict[str, Any],
]:
    article_metadata_all = read_jsonl(v7_data_root / "article_metadata.jsonl")
    issuer_metadata_all = read_jsonl(v7_data_root / "issuer_metadata.jsonl")
    article_indexes = [
        index for index, row in enumerate(article_metadata_all) if row["split"] == "train"
    ]
    development_sources = {
        str(article_metadata_all[index]["source_id"]) for index in article_indexes
    }
    issuer_indexes = [
        index
        for index, row in enumerate(issuer_metadata_all)
        if str(row["source_id"]) in development_sources
    ]
    arrays = {
        "article_eligibility.npy": np.load(v7_data_root / "article_eligibility.npy")[
            article_indexes
        ],
        "issuer_eligibility.npy": np.load(v7_data_root / "issuer_eligibility.npy")[
            issuer_indexes
        ],
        "issuer_sentiment.npy": np.load(v7_data_root / "issuer_sentiment.npy")[issuer_indexes],
        "issuer_concepts.npy": np.load(v7_data_root / "issuer_concepts.npy")[issuer_indexes],
    }
    label_contract = json.loads(
        (source_data_root / "label_contract.json").read_text(encoding="utf-8")
    )
    return (
        [article_metadata_all[index] for index in article_indexes],
        [issuer_metadata_all[index] for index in issuer_indexes],
        arrays,
        label_contract,
    )


def _load_development_documents(
    *,
    client,
    source_ids: Sequence[str],
    issuer_metadata: Sequence[Mapping[str, Any]],
    raw_drive_root: Path,
    source_database: str,
    identity_database: str,
    source_batch_size: int,
    feature_counter: Callable[..., Counter[str]],
) -> tuple[
    dict[tuple[str, str], Counter[str]],
    set[tuple[str, str]],
    list[dict[str, Any]],
    dict[str, Any],
]:
    _, payloads, raw_authority_rows, raw_report = _load_original_documents(
        client,
        source_ids,
        database=source_database,
        source_batch_size=source_batch_size,
        raw_drive_root=raw_drive_root,
        allow_revised_original_artifacts=False,
    )
    normalized_documents, _, normalized_report = _load_canonical_documents(
        client,
        source_ids,
        database=source_database,
        source_batch_size=source_batch_size,
    )
    article_document_keys = set(normalized_documents)
    fixed_keys = set(normalized_documents)
    for row in issuer_metadata:
        fixed_keys.add(
            (
                str(row["source_id"]),
                str(row["ticker"]).rsplit(":", 1)[-1].upper(),
            )
        )
    identity_index = load_identity_index(client, identity_database)
    feature_counts: dict[tuple[str, str], Counter[str]] = {}
    identity_rows: list[dict[str, Any]] = []
    for source_id, ticker in sorted(fixed_keys):
        source_keys = sorted(key for key in normalized_documents if key[0] == source_id)
        normalized = normalized_documents.get(
            (source_id, ticker), normalized_documents[source_keys[0]]
        )
        payload = payloads[source_id]
        aliases = point_in_time_aliases(
            identity_index,
            ticker=ticker,
            published_at_utc=str(normalized["published_at_utc"]),
        )
        metadata_text, metadata_structural = invariant_metadata_features(
            payload,
            target_ticker=ticker,
            has_external=bool(normalized.get("external")),
            has_pdf=bool(normalized.get("pdf")),
        )
        feature_counts[(source_id, ticker)] = feature_counter(
            original_fields={
                "title": str(payload.get("title") or ""),
                "teaser": str(payload.get("teaser") or ""),
                "body": original_body_text(payload.get("body")),
            },
            normalized_fields=normalized,
            metadata_text=metadata_text,
            metadata_structural=metadata_structural,
            ticker=ticker,
            aliases=aliases,
        )
        identity_rows.append(
            {
                "source_id": source_id,
                "ticker": ticker,
                "published_at_utc": str(normalized["published_at_utc"]),
                "aliases": list(aliases),
                "point_in_time_status": "resolved" if len(aliases) > 1 else "ticker_only",
            }
        )
    report = {
        "raw": raw_report,
        "normalized": normalized_report,
        "raw_authority_rows": len(raw_authority_rows),
        "feature_count_documents": len(feature_counts),
    }
    return feature_counts, article_document_keys, identity_rows, report


def _prepare_fold_dataset(
    *,
    fold: int,
    fold_assignments: Mapping[str, int],
    feature_counts: Mapping[tuple[str, str], Counter[str]],
    article_document_keys: set[tuple[str, str]],
    identity_rows: Sequence[Mapping[str, Any]],
    article_metadata: Sequence[Mapping[str, Any]],
    issuer_metadata: Sequence[Mapping[str, Any]],
    label_arrays: Mapping[str, np.ndarray],
    label_contract: Mapping[str, Any],
    output_root: Path,
    min_document_frequency: int,
    feature_spec: CrossValidationFeatureSpec,
) -> dict[str, Any]:
    fold_root = output_root / "staging" / f"fold_{fold}"
    if fold_root.exists():
        raise RuntimeError(f"Refusing to overwrite CV fold dataset: {fold_root}")
    training_sources = {
        source_id for source_id, value in fold_assignments.items() if value != fold
    }
    document_frequency: dict[str, Counter[str]] = defaultdict(Counter)
    training_document_count = 0
    for (source_id, _), counts in feature_counts.items():
        if source_id not in training_sources:
            continue
        training_document_count += 1
        for term in counts:
            document_frequency[term.split("|", 1)[0]][term] += 1
    if feature_spec.vocabulary_fitter is None:
        terms, idf, feature_report = fit_v7_vocabulary_from_document_frequency(
            document_frequency,
            training_document_count=training_document_count,
            min_document_frequency=min_document_frequency,
            budgets=feature_spec.budgets,
        )
    else:
        terms, idf, feature_report = feature_spec.vocabulary_fitter(
            document_frequency=document_frequency,
            training_document_count=training_document_count,
            min_document_frequency=min_document_frequency,
            budgets=feature_spec.budgets,
            feature_counts=feature_counts,
            training_sources=training_sources,
        )
    vocabulary = {term: index for index, term in enumerate(terms)}
    view_indexes = feature_spec.view_indexes(vocabulary)
    vectors = {
        key: transform_v7_counts(
            counts,
            vocabulary=vocabulary,
            idf=idf,
            view_indexes=view_indexes,
        )
        for key, counts in feature_counts.items()
    }
    vectors_by_source: dict[str, list[np.ndarray]] = defaultdict(list)
    for key, vector in vectors.items():
        if key in article_document_keys:
            vectors_by_source[key[0]].append(vector)
    article_vectors = np.stack(
        [
            l2_normalize(np.mean(vectors_by_source[str(row["source_id"])], axis=0))
            for row in article_metadata
        ]
    ).astype(np.float32)
    issuer_vectors: list[np.ndarray] = []
    issuer_match_counts: Counter[str] = Counter()
    for row in issuer_metadata:
        vector, status = match_issuer_embedding(str(row["source_id"]), str(row["ticker"]), vectors)
        if vector is None:
            raise RuntimeError(f"Missing CV issuer vector: {row['unit_id']}")
        issuer_vectors.append(vector)
        issuer_match_counts[status] += 1
    fold_article_metadata = [
        {
            **row,
            "split": (
                "validation" if fold_assignments[str(row["source_id"])] == fold else "train"
            ),
        }
        for row in article_metadata
    ]
    split_by_source = {
        str(row["source_id"]): str(row["split"]) for row in fold_article_metadata
    }
    fold_issuer_metadata = [
        {**row, "split": split_by_source[str(row["source_id"])]}
        for row in issuer_metadata
    ]
    fold_root.mkdir(parents=True)
    save_array(fold_root / "article_embeddings.npy", article_vectors)
    save_array(fold_root / "issuer_embeddings.npy", np.stack(issuer_vectors).astype(np.float32))
    for name, values in label_arrays.items():
        save_array(fold_root / name, values)
    write_jsonl(fold_root / "article_metadata.jsonl", fold_article_metadata)
    write_jsonl(fold_root / "issuer_metadata.jsonl", fold_issuer_metadata)
    write_jsonl(fold_root / "identity_features.jsonl", identity_rows)
    write_json(fold_root / "label_contract.json", label_contract)
    write_json(
        fold_root / "vocabulary.json",
        {"terms": list(terms), "idf": [float(value) for value in idf]},
    )
    files = (
        "article_embeddings.npy",
        "article_eligibility.npy",
        "issuer_embeddings.npy",
        "issuer_eligibility.npy",
        "issuer_sentiment.npy",
        "issuer_concepts.npy",
        "article_metadata.jsonl",
        "issuer_metadata.jsonl",
        "identity_features.jsonl",
        "label_contract.json",
        "vocabulary.json",
    )
    validation_articles = sum(
        row["split"] == "validation" for row in fold_article_metadata
    )
    manifest = {
        "version": feature_spec.dataset_version,
        "status": "complete",
        "representation": {
            "kind": feature_spec.representation_kind,
            "cross_validation_fold": fold,
            **feature_report,
            **feature_spec.feature_metadata,
        },
        "split": {
            "authority": "deterministic_stratified_grouped_development_cv",
            "fold": fold,
            "train_articles": len(fold_article_metadata) - validation_articles,
            "validation_articles": validation_articles,
        },
        "issuer_vector_matching": dict(sorted(issuer_match_counts.items())),
        "files": dataset_file_manifest(fold_root, files),
    }
    manifest["contract_sha256"] = canonical_json_sha256(manifest)
    write_json(fold_root / "manifest.json", manifest)
    validation = validate_prepared_dataset(fold_root)
    write_json(fold_root / "VALIDATION.json", validation)
    return {
        "data_root": fold_root,
        "manifest": manifest,
        "validation": validation,
        "vocabulary": {"terms": list(terms), "idf": [float(value) for value in idf]},
    }


def _aggregate(fold_reports: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    flat = [_metrics(dict(report)) for report in fold_reports]
    keys = tuple(flat[0])
    return {
        key: {
            "mean": float(np.mean([row[key] for row in flat])),
            "std": float(np.std([row[key] for row in flat], ddof=1)),
            "min": float(np.min([row[key] for row in flat])),
            "max": float(np.max([row[key] for row in flat])),
        }
        for key in keys
    }


def _normalized_ticker(value: Any) -> str:
    return str(value or "").rsplit(":", 1)[-1].upper()


def _news_synthesis_report(
    *,
    article_truth: np.ndarray,
    article_prediction: np.ndarray,
    issuer_truth: np.ndarray,
    issuer_prediction: np.ndarray,
    sentiment_truth: np.ndarray,
    sentiment_prediction: np.ndarray,
    concept_truth: np.ndarray,
    concept_prediction: np.ndarray,
    concept_labels: Sequence[str],
) -> dict[str, Any]:
    sentiment_mask = sentiment_truth >= 0
    sentiment_labels = ("positive", "negative", "neutral", "mixed", "missing")
    sentiment = _multiclass_metrics(
        sentiment_truth[sentiment_mask],
        sentiment_prediction[sentiment_mask],
        sentiment_labels,
    )
    # Missing is a possible deterministic-engine output but never a gold class.
    # Keep it in the confusion matrix while macro-averaging the four gold classes.
    sentiment["macro_f1"] = float(
        np.mean([sentiment["per_label"][label]["f1"] for label in sentiment_labels[:4]])
    )
    report = {
        "article_forecast_eligibility": _binary_class_metrics(
            article_truth.astype(np.uint8), article_prediction.astype(np.uint8)
        ),
        "issuer_forecast_eligibility": _binary_class_metrics(
            issuer_truth.astype(np.uint8), issuer_prediction.astype(np.uint8)
        ),
        "issuer_sentiment": sentiment,
        "issuer_concepts": _multilabel_metrics(
            concept_truth.astype(np.uint8),
            concept_prediction.astype(np.uint8),
            concept_labels,
        ),
    }
    report["selection_score"] = float(
        np.mean(
            [
                report["article_forecast_eligibility"]["macro_f1"],
                report["issuer_forecast_eligibility"]["macro_f1"],
                report["issuer_sentiment"]["macro_f1"],
                report["issuer_concepts"]["macro_f1_supported_labels"],
            ]
        )
    )
    return report


def _load_news_synthesis_baseline(
    *,
    paths: Sequence[Path],
    source_ids: Sequence[str],
    article_metadata: Sequence[Mapping[str, Any]],
    issuer_metadata: Sequence[Mapping[str, Any]],
    label_arrays: Mapping[str, np.ndarray],
    concept_labels: Sequence[str],
) -> dict[str, Any]:
    required_sources = set(source_ids)
    documents: dict[str, dict[str, Any]] = {}
    engine_versions: Counter[str] = Counter()
    partitions: Counter[str] = Counter()
    for path in paths:
        for row in read_jsonl(path):
            source_id = str(row["source_id"])
            if source_id not in required_sources:
                continue
            if source_id in documents:
                raise RuntimeError(f"Duplicate News Synthesis source: {source_id}")
            documents[source_id] = row
            production = row.get("prediction", {}).get("production", {})
            engine_versions[str(production.get("engine_version") or "unknown")] += 1
            partitions[str(row.get("partition") or "unknown")] += 1
    missing_sources = sorted(required_sources - set(documents))
    if missing_sources:
        raise RuntimeError(
            f"Latest News Synthesis outputs miss {len(missing_sources)} CV sources; "
            f"first={missing_sources[:5]}"
        )

    article_prediction = np.asarray(
        [
            bool(documents[str(row["source_id"])]["article_result"]["predicted_forecast_eligible"])
            for row in article_metadata
        ],
        dtype=np.uint8,
    )
    issuer_prediction: list[int] = []
    sentiment_prediction: list[int] = []
    concept_prediction: list[np.ndarray] = []
    sentiment_index = {
        "positive": 0,
        "negative": 1,
        "neutral": 2,
        "mixed": 3,
        "missing": 4,
    }
    concept_index = {label: index for index, label in enumerate(concept_labels)}
    for metadata in issuer_metadata:
        source_id = str(metadata["source_id"])
        ticker = _normalized_ticker(metadata["ticker"])
        document = documents[source_id]
        exact_matches = [
            row
            for row in document["issuer_unit_results"]
            if str(row.get("unit_id")) == str(metadata.get("unit_id"))
        ]
        ticker_matches = [
            row
            for row in document["issuer_unit_results"]
            if _normalized_ticker(row["ticker"]) == ticker
        ]
        matches = exact_matches or ticker_matches
        if len(matches) != 1:
            raise RuntimeError(
                f"Expected one News Synthesis issuer unit for {source_id}::{ticker}; "
                f"found {len(matches)}"
            )
        unit = matches[0]
        issuer_prediction.append(unit["predicted_forecast_eligibility"] == "eligible")
        sentiment_prediction.append(
            sentiment_index.get(str(unit.get("predicted_sentiment") or "missing"), 4)
        )
        prediction = document["prediction"]
        entity_id = unit.get("prediction_entity_id")
        views = [
            view for view in prediction.get("issuer_views", []) if view["entity_id"] == entity_id
        ]
        statement_ids = set(views[0].get("statement_ids", [])) if len(views) == 1 else set()
        values = np.zeros(len(concept_labels), dtype=np.uint8)
        for statement in prediction.get("statements", []):
            if statement.get("statement_id") not in statement_ids:
                continue
            index = concept_index.get(str(statement.get("concept_leaf") or ""))
            if index is not None:
                values[index] = 1
        concept_prediction.append(values)

    arrays = {
        "article": article_prediction,
        "issuer": np.asarray(issuer_prediction, dtype=np.uint8),
        "sentiment": np.asarray(sentiment_prediction, dtype=np.int64),
        "concept": np.stack(concept_prediction),
    }
    report = _news_synthesis_report(
        article_truth=label_arrays["article_eligibility.npy"],
        article_prediction=arrays["article"],
        issuer_truth=label_arrays["issuer_eligibility.npy"],
        issuer_prediction=arrays["issuer"],
        sentiment_truth=label_arrays["issuer_sentiment.npy"],
        sentiment_prediction=arrays["sentiment"],
        concept_truth=label_arrays["issuer_concepts.npy"],
        concept_prediction=arrays["concept"],
        concept_labels=concept_labels,
    )
    return {
        "arrays": arrays,
        "overall_report": report,
        "authority": {
            "audit_document_paths": [str(path) for path in paths],
            "covered_sources": len(documents),
            "engine_versions": dict(sorted(engine_versions.items())),
            "partitions": dict(sorted(partitions.items())),
        },
    }


def _news_synthesis_fold_report(
    *,
    fold: int,
    fold_assignments: Mapping[str, int],
    article_metadata: Sequence[Mapping[str, Any]],
    issuer_metadata: Sequence[Mapping[str, Any]],
    label_arrays: Mapping[str, np.ndarray],
    concept_labels: Sequence[str],
    predictions: Mapping[str, np.ndarray],
) -> dict[str, Any]:
    article_indexes = np.asarray(
        [
            index
            for index, row in enumerate(article_metadata)
            if fold_assignments[str(row["source_id"])] == fold
        ]
    )
    issuer_indexes = np.asarray(
        [
            index
            for index, row in enumerate(issuer_metadata)
            if fold_assignments[str(row["source_id"])] == fold
        ]
    )
    return _news_synthesis_report(
        article_truth=label_arrays["article_eligibility.npy"][article_indexes],
        article_prediction=predictions["article"][article_indexes],
        issuer_truth=label_arrays["issuer_eligibility.npy"][issuer_indexes],
        issuer_prediction=predictions["issuer"][issuer_indexes],
        sentiment_truth=label_arrays["issuer_sentiment.npy"][issuer_indexes],
        sentiment_prediction=predictions["sentiment"][issuer_indexes],
        concept_truth=label_arrays["issuer_concepts.npy"][issuer_indexes],
        concept_prediction=predictions["concept"][issuer_indexes],
        concept_labels=concept_labels,
    )


def run_cross_validation(
    args: argparse.Namespace,
    *,
    feature_spec: CrossValidationFeatureSpec = V7_CV_FEATURE_SPEC,
) -> dict[str, Any]:
    started = time.perf_counter()
    output_root = assert_runtime_path(Path(args.root))
    source_data_root = assert_runtime_path(Path(args.source_data_root))
    v7_data_root = assert_runtime_path(Path(args.v7_data_root))
    if output_root.exists():
        raise RuntimeError(f"Refusing to overwrite V7 cross-validation: {output_root}")
    validate_prepared_dataset(v7_data_root)
    article_metadata, issuer_metadata, label_arrays, label_contract = (
        _subset_development_authority(source_data_root, v7_data_root)
    )
    source_ids = [str(row["source_id"]) for row in article_metadata]
    article_labels = label_arrays["article_eligibility.npy"].astype(int).tolist()
    fold_assignments = deterministic_stratified_folds(
        source_ids,
        article_labels,
        fold_count=int(args.folds),
        seed=str(args.cv_seed),
    )
    concept_labels = list(label_contract["issuer_concepts"])
    news_synthesis = _load_news_synthesis_baseline(
        paths=[Path(path) for path in args.news_synthesis_audit_documents],
        source_ids=source_ids,
        article_metadata=article_metadata,
        issuer_metadata=issuer_metadata,
        label_arrays=label_arrays,
        concept_labels=concept_labels,
    )
    output_root.mkdir(parents=True)
    write_jsonl(
        output_root / "fold_assignments.jsonl",
        [
            {
                "source_id": source_id,
                "fold": fold_assignments[source_id],
                "article_eligibility": int(label),
            }
            for source_id, label in zip(source_ids, article_labels)
        ],
    )
    client = _clickhouse_client(args)
    feature_counts, article_document_keys, identity_rows, source_report = (
        _load_development_documents(
            client=client,
            source_ids=source_ids,
            issuer_metadata=issuer_metadata,
            raw_drive_root=Path(args.raw_drive_root),
            source_database=str(args.source_database),
            identity_database=str(args.identity_database),
            source_batch_size=int(args.source_batch_size),
            feature_counter=feature_spec.feature_counter,
        )
    )
    fold_reports: list[dict[str, Any]] = []
    news_synthesis_fold_reports: list[dict[str, Any]] = []
    fold_summaries: list[dict[str, Any]] = []
    for fold in range(int(args.folds)):
        prepared = _prepare_fold_dataset(
            fold=fold,
            fold_assignments=fold_assignments,
            feature_counts=feature_counts,
            article_document_keys=article_document_keys,
            identity_rows=identity_rows,
            article_metadata=article_metadata,
            issuer_metadata=issuer_metadata,
            label_arrays=label_arrays,
            label_contract=label_contract,
            output_root=output_root,
            min_document_frequency=int(args.min_document_frequency),
            feature_spec=feature_spec,
        )
        run_root = output_root / "run" / f"fold_{fold}"
        report = train_model(
            _train_args(prepared["data_root"], run_root, int(args.torch_threads))
        )
        fold_reports.append(report)
        news_report = _news_synthesis_fold_report(
            fold=fold,
            fold_assignments=fold_assignments,
            article_metadata=article_metadata,
            issuer_metadata=issuer_metadata,
            label_arrays=label_arrays,
            concept_labels=concept_labels,
            predictions=news_synthesis["arrays"],
        )
        news_synthesis_fold_reports.append(news_report)
        write_json(run_root / "fold_data_manifest.json", prepared["manifest"])
        write_json(run_root / "fold_data_validation.json", prepared["validation"])
        write_json(run_root / "vocabulary.json", prepared["vocabulary"])
        fold_summary = {
            "fold": fold,
            "train_articles": prepared["validation"]["train_articles"],
            "validation_articles": prepared["validation"]["validation_articles"],
            "validation_issuer_units": report["partition_counts"]["validation_issuer_units"],
            "selected_features": prepared["manifest"]["representation"]["selected_features"],
            "best_epoch": report["best_epoch"],
            "metrics": _metrics(report),
            "news_synthesis_metrics": _metrics(news_report),
        }
        fold_summaries.append(fold_summary)
        shutil.rmtree(prepared["data_root"])
        write_json(output_root / "progress.json", {"completed_folds": fold + 1})

    result = {
        "status": "complete",
        "experiment": feature_spec.experiment,
        "folds": int(args.folds),
        "cv_seed": str(args.cv_seed),
        "population": {
            "development_articles": len(article_metadata),
            "development_issuer_units": len(issuer_metadata),
            "official_validation_articles_excluded": sum(
                row["split"] == "validation"
                for row in read_jsonl(v7_data_root / "article_metadata.jsonl")
            ),
        },
        "leakage_controls": {
            "article_grouping": True,
            "each_development_article_held_out_once": True,
            "official_validation_excluded": True,
            "vocabulary_and_idf_refit_per_fold": True,
            "internal_tuning_within_fold_training_only": True,
            "feature_definition_frozen_before_cv": True,
            "same_v7_feature_definition": feature_spec is V7_CV_FEATURE_SPEC,
            "same_model_and_training_configuration": True,
        },
        "source_report": source_report,
        "fold_summaries": fold_summaries,
        "comparison": {
            feature_spec.comparison_key: {
                "aggregate_fold_metrics": _aggregate(fold_reports),
            },
            "news_synthesis_v48_latest_fixed": {
                "aggregate_same_fold_metrics": _aggregate(news_synthesis_fold_reports),
                "overall_same_population_metrics": _metrics(
                    news_synthesis["overall_report"]
                ),
                "authority": news_synthesis["authority"],
                "interpretation": (
                    "Fixed deterministic baseline evaluated on the same held-out fold rows; "
                    "it is not trained or refit per fold and is not an independent CV estimate."
                ),
            },
        },
        "aggregate": _aggregate(fold_reports),
        "elapsed_seconds": time.perf_counter() - started,
        "staging_datasets_removed_after_training": True,
    }
    prior_cv_path = getattr(args, "prior_cv_path", None)
    prior_folds_identical = True
    if prior_cv_path:
        prior_path = Path(prior_cv_path)
        prior = json.loads(prior_path.read_text(encoding="utf-8"))
        for key, value in prior.get("comparison", {}).items():
            if key.startswith("tfidf_") and key not in result["comparison"]:
                result["comparison"][key] = value
        current_assignments = (output_root / "fold_assignments.jsonl").read_bytes()
        prior_assignments = (prior_path.parent / "fold_assignments.jsonl").read_bytes()
        prior_folds_identical = current_assignments == prior_assignments
        result["leakage_controls"]["same_fold_assignments_as_prior_cv"] = (
            prior_folds_identical
        )
    write_json(output_root / "cross_validation.json", result)
    validation = {
        "status": "pass",
        "fold_count": int(args.folds),
        "assigned_articles": len(fold_assignments),
        "unique_assigned_articles": len(set(fold_assignments)),
        "fold_validation_articles": [
            int(summary["validation_articles"]) for summary in fold_summaries
        ],
        "validation_articles_sum": int(
            sum(summary["validation_articles"] for summary in fold_summaries)
        ),
        "each_development_article_held_out_once": (
            sum(summary["validation_articles"] for summary in fold_summaries)
            == len(article_metadata)
        ),
        "news_synthesis_source_coverage_complete": (
            news_synthesis["authority"]["covered_sources"] == len(article_metadata)
        ),
        "fold_evaluations_present": all(
            (output_root / "run" / f"fold_{fold}" / "evaluation.json").is_file()
            for fold in range(int(args.folds))
        ),
        "official_validation_excluded": True,
        "same_fold_assignments_as_prior_cv": prior_folds_identical,
    }
    if not all(
        value
        for key, value in validation.items()
        if key
        in {
            "each_development_article_held_out_once",
            "news_synthesis_source_coverage_complete",
            "fold_evaluations_present",
            "official_validation_excluded",
            "same_fold_assignments_as_prior_cv",
        }
    ):
        raise RuntimeError(f"Cross-validation result failed validation: {validation}")
    write_json(output_root / "VALIDATION.json", validation)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run grouped stratified cross-validation for News Synthesis TF-IDF V7 "
            "with per-fold vocabulary and IDF fitting."
        )
    )
    parser.add_argument("--source-data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument(
        "--v7-data-root",
        type=Path,
        default=(
            Path(r"D:\TradingML\runtimes")
            / "text_intelligence"
            / "news_synthesis_v1"
            / "tfidf_supervision_v7"
            / "data"
        ),
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_TFIDF_V7_CV_ROOT)
    parser.add_argument("--raw-drive-root", type=Path, default=DEFAULT_RAW_DRIVE_ROOT)
    parser.add_argument("--source-database", default="q_live")
    parser.add_argument("--identity-database", default="q_live")
    parser.add_argument("--min-document-frequency", type=int, default=3)
    parser.add_argument("--source-batch-size", type=int, default=500)
    parser.add_argument("--folds", type=int, default=DEFAULT_FOLDS)
    parser.add_argument("--cv-seed", default=DEFAULT_CV_SEED)
    parser.add_argument("--clickhouse-url", default="")
    parser.add_argument("--user", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--torch-threads", type=int, default=8)
    parser.add_argument(
        "--news-synthesis-audit-documents",
        type=Path,
        nargs="+",
        default=[
            DEFAULT_NEWS_SYNTHESIS_AUDIT_DOCUMENTS,
            DEFAULT_NEWS_SYNTHESIS_GENERALIZATION_ROOT
            / "evaluation_current_development_test"
            / "audit_documents.jsonl",
            DEFAULT_NEWS_SYNTHESIS_GENERALIZATION_ROOT
            / "evaluation_current_final_test"
            / "audit_documents.jsonl",
        ],
    )
    args = parser.parse_args()
    print(json.dumps(run_cross_validation(args), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
