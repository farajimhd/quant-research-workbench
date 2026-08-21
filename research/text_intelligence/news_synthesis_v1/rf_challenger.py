from __future__ import annotations

import hashlib
import json
import math
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

import joblib
import numpy as np
from scipy import sparse
from sklearn.ensemble import RandomForestClassifier
from sklearn.feature_extraction import DictVectorizer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import (
    accuracy_score, average_precision_score, balanced_accuracy_score,
    brier_score_loss, confusion_matrix, f1_score, log_loss,
    precision_score, recall_score, roc_auc_score,
)

from .funnel_holdout_review import REVIEW_VERSION
from .provider_filter_analysis import (
    NEW_YORK, canonical_json, iter_jsonl, parse_utc, session_date, session_segment,
    sha256_path, text_flags,
)


CHALLENGER_VERSION = "news_synthesis_forecast_eligibility_rf_challenger_v1"
SEED = 20260821
DEFAULT_FEATURES = Path(r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\provider_filter_feature_audit_v3_corrected\ARTICLE_FEATURES.jsonl")
DEFAULT_TEXTS = Path(r"D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4\forecast_eligibility_rf_comparison_v1\rendered_texts.jsonl")
DEFAULT_HOLDOUT = Path(r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\funnel_fresh_holdout_v1")
DEFAULT_OUTPUT = Path(r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\forecast_eligibility_rf_challenger_v1")
RF_PARAMETERS = {
    "n_estimators": 200, "max_depth": 32, "min_samples_leaf": 2,
    "max_features": "sqrt", "bootstrap": True, "max_samples": 0.65,
    "class_weight": "balanced_subsample", "n_jobs": 12, "random_state": SEED,
}
TFIDF_PARAMETERS = {
    "lowercase": True, "strip_accents": "unicode", "ngram_range": (1, 2),
    "min_df": 3, "max_df": 0.995, "max_features": 75_000,
    "sublinear_tf": True, "norm": "l2", "dtype": np.float32,
}
BOOL_FIELDS = (
    "analyst_rating", "earnings_preview", "halt", "index_or_listing",
    "list_or_screener", "macro", "market_recap", "material_event",
    "price_target", "question_title", "short_interest", "technical_or_valuation",
    "title_only", "why_moving", "any_ticker_first_session",
    "all_tickers_first_session", "any_ticker_news_within_5m",
    "any_ticker_news_within_30m",
)
NUMERIC_FIELDS = (
    "ticker_count", "rendered_chars", "update_delay_seconds",
    "min_ticker_session_ordinal", "max_ticker_session_ordinal",
    "min_seconds_since_previous_ticker_news", "max_seconds_since_previous_ticker_news",
)


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def metadata_features(row: Mapping[str, Any]) -> dict[str, float]:
    published = parse_utc(str(row.get("published_at_text") or row.get("published_at_utc") or ""))
    hour = published.hour + published.minute / 60 + published.second / 3600
    dow = published.weekday()
    day = published.timetuple().tm_yday
    result: dict[str, float] = {
        "hour_sin": math.sin(2 * math.pi * hour / 24),
        "hour_cos": math.cos(2 * math.pi * hour / 24),
        "dow_sin": math.sin(2 * math.pi * dow / 7),
        "dow_cos": math.cos(2 * math.pi * dow / 7),
        "year_sin": math.sin(2 * math.pi * day / 365.2425),
        "year_cos": math.cos(2 * math.pi * day / 365.2425),
        f"provider={str(row.get('provider') or '').casefold()}": 1.0,
        f"session_segment={row.get('session_segment') or session_segment(published)}": 1.0,
        f"month={published.month}": 1.0,
        f"weekday_et={row.get('weekday_et') or ''}": 1.0,
        f"hour_et={row.get('hour_et', '')}": 1.0,
    }
    for name in BOOL_FIELDS:
        result[f"bool:{name}"] = float(bool(row.get(name)))
    for name in NUMERIC_FIELDS:
        value = row.get(name)
        result[f"numeric:{name}:missing"] = float(value is None or value == "")
        if value is not None and value != "":
            parsed = max(0.0, float(value))
            result[f"numeric:{name}"] = math.log1p(parsed) if name != "ticker_count" else parsed
    tags = tuple(sorted({str(value).strip().casefold() for value in row.get("provider_tags") or () if str(value).strip()}))
    channels = tuple(sorted({str(value).strip().casefold() for value in row.get("channels") or () if str(value).strip()}))
    tickers = tuple(sorted({str(value).strip().upper() for value in row.get("tickers") or () if str(value).strip()}))
    quality = tuple(sorted({str(value).strip().casefold() for value in row.get("content_quality_flags") or () if str(value).strip()}))
    result[f"channel_set={'|'.join(channels)}"] = 1.0
    result[f"tag_set={'|'.join(tags)}"] = 1.0
    for prefix, values in (("channel", channels), ("tag", tags), ("ticker", tickers), ("quality", quality)):
        for value in values:
            result[f"{prefix}={value}"] = 1.0
    return result


def binary_metrics(y_true: np.ndarray, probability: np.ndarray, threshold: float) -> dict[str, Any]:
    prediction = (probability >= threshold).astype(np.int8)
    matrix = confusion_matrix(y_true, prediction, labels=[0, 1])
    return {
        "threshold": float(threshold), "articles": int(len(y_true)),
        "accuracy": float(accuracy_score(y_true, prediction)),
        "balanced_accuracy": float(balanced_accuracy_score(y_true, prediction)),
        "eligible_precision": float(precision_score(y_true, prediction, zero_division=0)),
        "eligible_recall": float(recall_score(y_true, prediction, zero_division=0)),
        "eligible_f1": float(f1_score(y_true, prediction, zero_division=0)),
        "macro_f1": float(f1_score(y_true, prediction, average="macro", zero_division=0)),
        "roc_auc": float(roc_auc_score(y_true, probability)),
        "average_precision": float(average_precision_score(y_true, probability)),
        "brier_score": float(brier_score_loss(y_true, probability)),
        "log_loss": float(log_loss(y_true, np.column_stack((1 - probability, probability)), labels=[0, 1])),
        "confusion_matrix_labels": ["ineligible", "eligible"],
        "confusion_matrix": matrix.tolist(),
    }


def select_threshold(y_true: np.ndarray, probability: np.ndarray) -> tuple[float, list[dict[str, float]]]:
    candidates = np.arange(0.20, 0.801, 0.01)
    scored = [{"threshold": float(value), "balanced_accuracy": float(balanced_accuracy_score(y_true, probability >= value))} for value in candidates]
    best = max(scored, key=lambda row: (row["balanced_accuracy"], -abs(row["threshold"] - 0.5)))
    return float(best["threshold"]), scored


def _text_iterator(path: Path, wanted: set[str]) -> Iterator[str]:
    for row in iter_jsonl(path):
        if str(row["source_id"]) in wanted:
            yield str(row["rendered_text"])


def _ordered_text_ids(path: Path, rows_by_id: Mapping[str, Any]) -> tuple[dict[str, list[str]], list[str]]:
    result: dict[str, list[str]] = defaultdict(list)
    all_ids: list[str] = []
    seen: set[str] = set()
    for row in iter_jsonl(path):
        source_id = str(row["source_id"])
        if source_id not in rows_by_id:
            continue
        text = str(row.get("rendered_text") or "")
        if hashlib.sha256(text.encode()).hexdigest() != str(row.get("rendered_text_hash") or ""):
            raise ValueError(f"rendered hash mismatch: {source_id}")
        if source_id in seen:
            raise ValueError(f"duplicate rendered source: {source_id}")
        seen.add(source_id)
        all_ids.append(source_id)
        result[str(rows_by_id[source_id]["split"])].append(source_id)
    if seen != set(rows_by_id):
        raise ValueError(f"feature/text membership mismatch: {len(set(rows_by_id) - seen)} missing")
    return dict(result), all_ids


def _fit_model(x: sparse.csr_matrix, y: np.ndarray) -> RandomForestClassifier:
    model = RandomForestClassifier(**RF_PARAMETERS)
    model.fit(x, y)
    return model


def _probability(model: RandomForestClassifier, x: sparse.csr_matrix) -> np.ndarray:
    return model.predict_proba(x)[:, list(model.classes_).index(1)]


def _matrix(metadata_vectorizer: DictVectorizer, rows: Sequence[Mapping[str, Any]], text_matrix: sparse.csr_matrix) -> sparse.csr_matrix:
    metadata = metadata_vectorizer.transform(metadata_features(row) for row in rows).astype(np.float32)
    return sparse.hstack((metadata, text_matrix), format="csr", dtype=np.float32)


def train_and_freeze(*, feature_path: Path, text_path: Path, output_root: Path) -> dict[str, Any]:
    if output_root.exists():
        raise FileExistsError(output_root)
    output_root.mkdir(parents=True)
    rows_by_id: dict[str, dict[str, Any]] = {}
    split_counts: Counter[str] = Counter()
    label_counts: Counter[tuple[str, str]] = Counter()
    last_timestamp: dict[str, str] = {}
    cutoff_session = ""
    cutoff_session_counts: Counter[str] = Counter()
    for row in iter_jsonl(feature_path):
        source_id = str(row["source_id"])
        if source_id in rows_by_id:
            raise ValueError(f"duplicate feature row: {source_id}")
        if row["label"] not in {"eligible", "ineligible"}:
            raise ValueError(f"nondecisive training label: {source_id}")
        rows_by_id[source_id] = row
        split_counts[str(row["split"])] += 1
        label_counts[(str(row["split"]), str(row["label"]))] += 1
        timestamp = str(row["published_at_text"])
        for ticker in row.get("tickers") or ():
            key = str(ticker).upper()
            if timestamp > last_timestamp.get(key, ""):
                last_timestamp[key] = timestamp
        row_session = str(row.get("session_date") or "")
        if row_session > cutoff_session:
            cutoff_session = row_session
            cutoff_session_counts.clear()
        if row_session == cutoff_session:
            for ticker in row.get("tickers") or ():
                cutoff_session_counts[str(ticker).upper()] += 1
    ordered, all_ids = _ordered_text_ids(text_path, rows_by_id)
    expected_splits = {"discovery_2025", "validation_2026_jan_apr", "final_2026_may_aug"}
    if set(ordered) != expected_splits:
        raise ValueError(f"unexpected temporal splits: {sorted(ordered)}")

    discovery_ids = ordered["discovery_2025"]
    validation_ids = ordered["validation_2026_jan_apr"]
    confirmation_ids = ordered["final_2026_may_aug"]
    discovery_rows = [rows_by_id[value] for value in discovery_ids]
    validation_rows = [rows_by_id[value] for value in validation_ids]
    confirmation_rows = [rows_by_id[value] for value in confirmation_ids]
    y_discovery = np.asarray([row["label"] == "eligible" for row in discovery_rows], dtype=np.int8)
    y_validation = np.asarray([row["label"] == "eligible" for row in validation_rows], dtype=np.int8)
    y_confirmation = np.asarray([row["label"] == "eligible" for row in confirmation_rows], dtype=np.int8)

    started = time.time()
    temporal_meta = DictVectorizer(sparse=True, sort=True)
    temporal_meta.fit(metadata_features(row) for row in discovery_rows)
    temporal_tfidf = TfidfVectorizer(**TFIDF_PARAMETERS)
    x_text_discovery = temporal_tfidf.fit_transform(_text_iterator(text_path, set(discovery_ids)))
    x_text_validation = temporal_tfidf.transform(_text_iterator(text_path, set(validation_ids)))
    x_text_confirmation = temporal_tfidf.transform(_text_iterator(text_path, set(confirmation_ids)))
    temporal_model = _fit_model(_matrix(temporal_meta, discovery_rows, x_text_discovery), y_discovery)
    validation_probability = _probability(temporal_model, _matrix(temporal_meta, validation_rows, x_text_validation))
    confirmation_probability = _probability(temporal_model, _matrix(temporal_meta, confirmation_rows, x_text_confirmation))
    threshold, threshold_curve = select_threshold(y_validation, validation_probability)
    temporal_report = {
        "training_split": "discovery_2025", "threshold_selection_split": "validation_2026_jan_apr",
        "untuned_confirmation_split": "final_2026_may_aug", "selected_threshold": threshold,
        "validation_at_0_5": binary_metrics(y_validation, validation_probability, 0.5),
        "validation_at_selected": binary_metrics(y_validation, validation_probability, threshold),
        "confirmation_at_0_5": binary_metrics(y_confirmation, confirmation_probability, 0.5),
        "confirmation_at_selected": binary_metrics(y_confirmation, confirmation_probability, threshold),
        "threshold_curve": threshold_curve,
    }
    _atomic_json(output_root / "TEMPORAL_VALIDATION_REPORT.json", temporal_report)
    del temporal_model, temporal_meta, temporal_tfidf, x_text_discovery, x_text_validation, x_text_confirmation

    all_rows = [rows_by_id[value] for value in all_ids]
    y_all = np.asarray([row["label"] == "eligible" for row in all_rows], dtype=np.int8)
    metadata_vectorizer = DictVectorizer(sparse=True, sort=True)
    x_metadata = metadata_vectorizer.fit_transform(metadata_features(row) for row in all_rows).astype(np.float32)
    tfidf = TfidfVectorizer(**TFIDF_PARAMETERS)
    x_text = tfidf.fit_transform(_text_iterator(text_path, set(all_ids)))
    x_all = sparse.hstack((x_metadata, x_text), format="csr", dtype=np.float32)
    final_model = _fit_model(x_all, y_all)
    joblib.dump(metadata_vectorizer, output_root / "metadata_vectorizer.joblib", compress=3)
    joblib.dump(tfidf, output_root / "tfidf_vectorizer.joblib", compress=3)
    joblib.dump(final_model, output_root / "random_forest.joblib", compress=3)
    state_path = output_root / "CAUSAL_STATE.json"
    _atomic_json(state_path, {"last_timestamp": last_timestamp, "cutoff_session": cutoff_session, "cutoff_session_counts": dict(cutoff_session_counts)})
    report = {
        "challenger_version": CHALLENGER_VERSION, "status": "frozen_before_holdout",
        "created_at_utc": datetime.now(UTC).isoformat(), "seed": SEED,
        "rf_parameters": RF_PARAMETERS,
        "tfidf_parameters": {**TFIDF_PARAMETERS, "dtype": "float32"},
        "feature_policy": "shared provider metadata, causal timing/history, structural flags, and rendered-source word TF-IDF; label provenance excluded",
        "split_counts": dict(split_counts),
        "split_label_counts": {f"{split}|{label}": count for (split, label), count in sorted(label_counts.items())},
        "selected_threshold": threshold, "final_training_articles": len(all_rows),
        "final_training_eligible": int(y_all.sum()), "final_matrix_shape": list(x_all.shape),
        "train_seconds": time.time() - started,
        "inputs": {"features": str(feature_path), "features_sha256": sha256_path(feature_path), "texts": str(text_path), "texts_sha256": sha256_path(text_path)},
    }
    _atomic_json(output_root / "FROZEN_MODEL_MANIFEST.json", report)
    return report


def _holdout_rows(root: Path, state: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    population = list(iter_jsonl(root / "SOURCE_POPULATION.jsonl"))
    population.sort(key=lambda row: (str(row["published_at_utc"]), str(row["source_id"])))
    last = {key: parse_utc(value) for key, value in state["last_timestamp"].items()}
    counts: Counter[tuple[str, str]] = Counter()
    counts.update({(ticker, str(state["cutoff_session"])): int(value) for ticker, value in state["cutoff_session_counts"].items()})
    index = 0
    while index < len(population):
        timestamp = parse_utc(str(population[index]["published_at_utc"]))
        end = index + 1
        while end < len(population) and parse_utc(str(population[end]["published_at_utc"])) == timestamp:
            end += 1
        for row in population[index:end]:
            tickers = tuple(sorted({str(value).upper() for value in row.get("tickers") or () if str(value)}))
            current_session = session_date(timestamp)
            seconds = [max(0.0, (timestamp - last[ticker]).total_seconds()) for ticker in tickers if ticker in last]
            ordinals = [counts[(ticker, current_session)] + 1 for ticker in tickers]
            eastern = timestamp.astimezone(NEW_YORK)
            row.update({
                "published_at_text": timestamp.isoformat(), "tickers": tickers,
                "ticker_count": len(tickers), "session_segment": session_segment(timestamp),
                "session_date": current_session, "hour_et": eastern.hour, "weekday_et": eastern.strftime("%a").casefold(),
                "update_delay_seconds": None,
                "any_ticker_first_session": bool(ordinals) and any(value == 1 for value in ordinals),
                "all_tickers_first_session": bool(ordinals) and all(value == 1 for value in ordinals),
                "min_ticker_session_ordinal": min(ordinals) if ordinals else 0,
                "max_ticker_session_ordinal": max(ordinals) if ordinals else 0,
                "min_seconds_since_previous_ticker_news": min(seconds) if seconds else None,
                "max_seconds_since_previous_ticker_news": max(seconds) if seconds else None,
                "any_ticker_news_within_5m": any(value < 300 for value in seconds),
                "any_ticker_news_within_30m": any(value < 1800 for value in seconds),
                "rendered_chars": len(str(row["rendered_text"])),
                **text_flags(str(row["rendered_text"])),
            })
        for row in population[index:end]:
            current_session = str(row["session_date"])
            for ticker in row["tickers"]:
                last[ticker] = timestamp
                counts[(ticker, current_session)] += 1
        index = end
    return {str(row["source_id"]): row for row in population}


def evaluate_holdout(*, output_root: Path, holdout_root: Path) -> dict[str, Any]:
    manifest_path = output_root / "FROZEN_MODEL_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "frozen_before_holdout" or manifest.get("challenger_version") != CHALLENGER_VERSION:
        raise RuntimeError("model was not frozen before holdout")
    evaluation_root = output_root / "heldout_evaluation_v1"
    if evaluation_root.exists():
        raise FileExistsError(evaluation_root)
    gold_manifest = json.loads((holdout_root / "gold_review_v1" / "GOLD_MANIFEST.json").read_text(encoding="utf-8"))
    if gold_manifest.get("status") != "gold_complete" or gold_manifest.get("review_version") != REVIEW_VERSION:
        raise RuntimeError("heldout gold is not complete")
    sample = list(iter_jsonl(holdout_root / "SEALED_SAMPLE.jsonl"))
    gold = list(iter_jsonl(Path(gold_manifest["gold_path"])))
    if [row["source_id"] for row in sample] != [row["source_id"] for row in gold]:
        raise RuntimeError("heldout sample/gold alignment mismatch")
    state = json.loads((output_root / "CAUSAL_STATE.json").read_text(encoding="utf-8"))
    population = _holdout_rows(holdout_root, state)
    rows = [population[str(row["source_id"])] for row in sample]
    metadata_vectorizer: DictVectorizer = joblib.load(output_root / "metadata_vectorizer.joblib")
    tfidf: TfidfVectorizer = joblib.load(output_root / "tfidf_vectorizer.joblib")
    model: RandomForestClassifier = joblib.load(output_root / "random_forest.joblib")
    x_metadata = metadata_vectorizer.transform(metadata_features(row) for row in rows).astype(np.float32)
    x_text = tfidf.transform(str(row["rendered_text"]) for row in rows)
    probability = _probability(model, sparse.hstack((x_metadata, x_text), format="csr", dtype=np.float32))
    y_true = np.asarray([row["article_label"] == "eligible" for row in gold], dtype=np.int8)
    threshold = float(manifest["selected_threshold"])
    prediction = (probability >= threshold).astype(np.int8)
    deterministic = json.loads((holdout_root / "final_evaluation_v1" / "REPORT.json").read_text(encoding="utf-8"))
    deterministic_predictions = list(iter_jsonl(holdout_root / "final_evaluation_v1" / "PREDICTIONS.jsonl"))
    deterministic_binary = np.asarray([row["article_label"] == "eligible" for row in deterministic_predictions], dtype=np.int8)
    rf_correct = prediction == y_true
    deterministic_correct = deterministic_binary == y_true
    comparison = {
        "both_correct": int(np.sum(rf_correct & deterministic_correct)),
        "rf_only_correct": int(np.sum(rf_correct & ~deterministic_correct)),
        "deterministic_only_correct": int(np.sum(~rf_correct & deterministic_correct)),
        "both_wrong": int(np.sum(~rf_correct & ~deterministic_correct)),
    }
    evaluation_root.mkdir()
    predictions_path = evaluation_root / "PREDICTIONS.jsonl"
    with predictions_path.open("x", encoding="utf-8", newline="\n") as handle:
        for source, score, predicted in zip(sample, probability, prediction):
            handle.write(canonical_json({"source_id": source["source_id"], "eligible_probability": float(score), "article_label": "eligible" if predicted else "ineligible"}) + "\n")
    report = {
        "challenger_version": CHALLENGER_VERSION, "status": "complete",
        "created_at_utc": datetime.now(UTC).isoformat(), "articles": len(gold),
        "primary_threshold_source": "validation_2026_jan_apr_balanced_accuracy",
        "rf_at_frozen_threshold": binary_metrics(y_true, probability, threshold),
        "rf_at_0_5": binary_metrics(y_true, probability, 0.5),
        "deterministic_funnel": {key: deterministic[key] for key in ("article_accuracy", "article_balanced_accuracy", "eligible_precision", "eligible_recall", "ineligible_recall", "confusion")},
        "paired_comparison": comparison,
        "lineage": {
            "frozen_model_manifest_sha256": sha256_path(manifest_path),
            "gold_sha256": gold_manifest["gold_sha256"],
            "sample_sha256": sha256_path(holdout_root / "SEALED_SAMPLE.jsonl"),
            "rf_predictions_sha256": sha256_path(predictions_path),
            "deterministic_predictions_sha256": deterministic["lineage"]["predictions_sha256"],
        },
    }
    _atomic_json(evaluation_root / "REPORT.json", report)
    return report


def validate_artifacts(*, output_root: Path) -> dict[str, Any]:
    manifest_path = output_root / "FROZEN_MODEL_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    metadata_path = output_root / "metadata_vectorizer.joblib"
    tfidf_path = output_root / "tfidf_vectorizer.joblib"
    model_path = output_root / "random_forest.joblib"
    metadata_vectorizer: DictVectorizer = joblib.load(metadata_path)
    tfidf: TfidfVectorizer = joblib.load(tfidf_path)
    model: RandomForestClassifier = joblib.load(model_path)
    metadata_columns = len(metadata_vectorizer.feature_names_)
    tfidf_columns = len(tfidf.get_feature_names_out())
    expected_columns = int(manifest["final_matrix_shape"][1])
    actual_columns = metadata_columns + tfidf_columns
    if actual_columns != expected_columns or int(model.n_features_in_) != expected_columns:
        raise RuntimeError(
            f"feature dimensionality mismatch: manifest={expected_columns}, "
            f"vectorizers={actual_columns}, model={model.n_features_in_}"
        )
    report = {
        "challenger_version": CHALLENGER_VERSION,
        "status": "valid",
        "validated_at_utc": datetime.now(UTC).isoformat(),
        "dimensions": {
            "metadata_columns": metadata_columns,
            "tfidf_columns": tfidf_columns,
            "total_columns": actual_columns,
            "model_columns": int(model.n_features_in_),
            "trees": len(model.estimators_),
        },
        "sha256": {
            "frozen_model_manifest": sha256_path(manifest_path),
            "metadata_vectorizer": sha256_path(metadata_path),
            "tfidf_vectorizer": sha256_path(tfidf_path),
            "random_forest": sha256_path(model_path),
        },
    }
    evaluation_path = output_root / "heldout_evaluation_v1" / "REPORT.json"
    if evaluation_path.exists():
        report["sha256"]["heldout_report"] = sha256_path(evaluation_path)
    _atomic_json(output_root / "ARTIFACT_VALIDATION.json", report)
    return report
