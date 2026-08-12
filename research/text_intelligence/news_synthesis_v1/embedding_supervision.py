from __future__ import annotations

import hashlib
import json
import math
import os
import random
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np


DATASET_VERSION = "news_synthesis_qwen_embedding_supervision_v1"
MODEL_VERSION = "news_synthesis_qwen_multitask_mlp_v1"
TFIDF_DATASET_VERSION = "news_synthesis_tfidf_supervision_v1"
TFIDF_MODEL_VERSION = "news_synthesis_tfidf_multitask_mlp_v1"
TFIDF_V2_DATASET_VERSION = "news_synthesis_tfidf_supervision_v2"
TFIDF_V2_MODEL_VERSION = "news_synthesis_tfidf_multitask_mlp_v2"
TFIDF_V3_DATASET_VERSION = "news_synthesis_tfidf_supervision_v3"
TFIDF_V3_MODEL_VERSION = "news_synthesis_tfidf_multitask_mlp_v3"
TFIDF_V4_DATASET_VERSION = "news_synthesis_tfidf_supervision_v4"
TFIDF_V4_MODEL_VERSION = "news_synthesis_tfidf_multitask_mlp_v4"
COMPARISON_DATASET_VERSION = "news_synthesis_common_representation_supervision_v1"
OPENAI_MODEL_VERSION = "news_synthesis_openai_multitask_mlp_v1"
SUPPORTED_DATASET_VERSIONS = {
    DATASET_VERSION,
    TFIDF_DATASET_VERSION,
    TFIDF_V2_DATASET_VERSION,
    TFIDF_V3_DATASET_VERSION,
    TFIDF_V4_DATASET_VERSION,
    COMPARISON_DATASET_VERSION,
}
DEFAULT_EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"
DEFAULT_EMBEDDING_DIM = 1024
DEFAULT_SPLIT_SEED = "news-synthesis-qwen-supervision-v1"
SENTIMENT_LABELS = ("positive", "negative", "neutral", "mixed")
RUNTIME_ROOT = Path(r"D:\TradingML\runtimes")
DEFAULT_GOLD_PATH = (
    RUNTIME_ROOT
    / "text_intelligence"
    / "news_synthesis_v1"
    / "gold_certified_news_labels_consolidated_v1"
    / "gold_labels.jsonl"
)
DEFAULT_DATA_ROOT = (
    RUNTIME_ROOT
    / "text_intelligence"
    / "news_synthesis_v1"
    / "qwen_embedding_supervision_v1"
    / "data"
)
DEFAULT_RUN_ROOT = (
    RUNTIME_ROOT
    / "text_intelligence"
    / "news_synthesis_v1"
    / "qwen_embedding_supervision_v1"
    / "run"
)


@dataclass(frozen=True, slots=True)
class TrainConfig:
    seed: int = 20260812
    hidden_dim: int = 384
    residual_blocks: int = 2
    dropout: float = 0.20
    batch_size: int = 256
    learning_rate: float = 8.0e-4
    weight_decay: float = 1.0e-4
    max_epochs: int = 50
    patience: int = 7
    min_delta: float = 1.0e-4
    tuning_fraction: float = 0.10
    sentiment_loss_weight: float = 0.75
    concept_loss_weight: float = 1.0
    workers: int = 0
    torch_threads: int = 0


def assert_runtime_path(path: Path, *, runtime_root: Path = RUNTIME_ROOT) -> Path:
    resolved = path.resolve()
    root = runtime_root.resolve()
    if resolved != root and root not in resolved.parents:
        raise RuntimeError(f"Generated artifact must remain under {root}: {resolved}")
    return resolved


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise RuntimeError(f"Expected object at {path}:{line_number}")
            rows.append(value)
    return rows


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    os.replace(temporary, path)


def save_array(path: Path, value: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("wb") as handle:
        np.save(handle, value, allow_pickle=False)
    os.replace(temporary, path)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def l2_normalize(vector: np.ndarray) -> np.ndarray:
    value = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(value))
    if not math.isfinite(norm) or norm <= 0.0:
        raise RuntimeError("Embedding vector has non-finite or zero norm")
    return value / norm


def pool_embedding_chunks(
    rows: Iterable[Mapping[str, Any]],
    *,
    embedding_dim: int = DEFAULT_EMBEDDING_DIM,
) -> tuple[dict[str, np.ndarray], dict[tuple[str, str], np.ndarray], dict[str, int]]:
    chunks: dict[tuple[str, str], list[tuple[int, np.ndarray]]] = defaultdict(list)
    seen_keys: set[tuple[str, str, int]] = set()
    raw_rows = 0
    for row in rows:
        source_id = str(row.get("source_id") or "")
        ticker = str(row.get("ticker") or "").strip().upper()
        chunk_index = int(row.get("token_chunk_index") or 0)
        if not source_id or not ticker:
            raise RuntimeError("Embedding row is missing source_id or ticker")
        key = (source_id, ticker, chunk_index)
        if key in seen_keys:
            raise RuntimeError(f"Duplicate logical embedding chunk: {key}")
        seen_keys.add(key)
        vector = np.asarray(row.get("embedding"), dtype=np.float32)
        if vector.shape != (embedding_dim,) or not np.isfinite(vector).all():
            raise RuntimeError(f"Invalid embedding for {key}: shape={vector.shape}")
        chunks[(source_id, ticker)].append((chunk_index, vector))
        raw_rows += 1
    issuer_vectors: dict[tuple[str, str], np.ndarray] = {}
    by_source: dict[str, list[np.ndarray]] = defaultdict(list)
    for key, values in sorted(chunks.items()):
        values.sort(key=lambda item: item[0])
        vector = l2_normalize(np.mean([item[1] for item in values], axis=0))
        issuer_vectors[key] = vector
        by_source[key[0]].append(vector)
    article_vectors = {
        source_id: l2_normalize(np.mean(vectors, axis=0))
        for source_id, vectors in sorted(by_source.items())
    }
    return article_vectors, issuer_vectors, {
        "embedding_rows": raw_rows,
        "article_vectors": len(article_vectors),
        "issuer_vectors": len(issuer_vectors),
    }


def _unit_ticker_candidates(ticker: str) -> tuple[str, ...]:
    raw = str(ticker or "").strip().upper()
    values: list[str] = []
    for candidate in (raw, raw.rsplit(":", 1)[-1], raw.replace(".", "-")):
        if candidate and candidate not in values:
            values.append(candidate)
    return tuple(values)


def match_issuer_embedding(
    source_id: str,
    ticker: str,
    vectors: Mapping[tuple[str, str], np.ndarray],
) -> tuple[np.ndarray | None, str]:
    candidates = _unit_ticker_candidates(ticker)
    for index, candidate in enumerate(candidates):
        value = vectors.get((source_id, candidate))
        if value is not None:
            return value, "exact_ticker" if index == 0 else "normalized_ticker"
    return None, "no_matching_embedding_ticker"


def article_stratification_features(row: Mapping[str, Any]) -> tuple[str, ...]:
    features = {
        f"authority:{row.get('authority_id')}",
        f"article_eligible:{int(bool(row.get('article_forecast_eligible')))}",
    }
    for unit in row.get("issuer_units") or ():
        sentiment = str(unit.get("sentiment") or "")
        if sentiment in SENTIMENT_LABELS:
            features.add(f"sentiment:{sentiment}")
        features.add(
            "issuer_eligible:"
            + str(int(str(unit.get("forecast_eligibility")) == "eligible"))
        )
        for concept in unit.get("concepts") or ():
            features.add(f"concept:{concept}")
    return tuple(sorted(features))


def deterministic_stratified_split(
    rows: Sequence[Mapping[str, Any]],
    *,
    train_fraction: float = 0.75,
    seed: str = DEFAULT_SPLIT_SEED,
    candidate_count: int = 256,
) -> tuple[dict[str, str], dict[str, Any]]:
    if not 0.0 < train_fraction < 1.0:
        raise ValueError("train_fraction must be between zero and one")
    if len(rows) < 2:
        raise ValueError("At least two articles are required")
    feature_map = {
        str(row["source_id"]): article_stratification_features(row) for row in rows
    }
    if len(feature_map) != len(rows):
        raise RuntimeError("Gold rows contain duplicate source IDs")
    all_ids = sorted(feature_map)
    train_size = int(round(len(all_ids) * train_fraction))
    validation_size = len(all_ids) - train_size
    totals = Counter(feature for values in feature_map.values() for feature in values)
    target = {name: count * validation_size / len(all_ids) for name, count in totals.items()}

    def rank(source_id: str, candidate: int) -> bytes:
        return hashlib.sha256(f"{seed}:{candidate}:{source_id}".encode()).digest()

    def score(validation_ids: set[str]) -> float:
        observed = Counter(
            feature for source_id in validation_ids for feature in feature_map[source_id]
        )
        weighted = []
        for name, expected in target.items():
            scale = max(1.0, math.sqrt(expected))
            weighted.append(((observed[name] - expected) / scale) ** 2)
        return float(math.sqrt(sum(weighted) / max(1, len(weighted))))

    best: tuple[float, int, set[str]] | None = None
    for candidate in range(candidate_count):
        ordered = sorted(all_ids, key=lambda source_id: rank(source_id, candidate))
        validation_ids = set(ordered[:validation_size])
        result = (score(validation_ids), candidate, validation_ids)
        if best is None or result[:2] < best[:2]:
            best = result
    assert best is not None
    assignments = {
        source_id: "validation" if source_id in best[2] else "train"
        for source_id in all_ids
    }
    observed = Counter(
        feature for source_id in best[2] for feature in feature_map[source_id]
    )
    drift = {
        name: {
            "total": totals[name],
            "validation": observed[name],
            "target_validation": target[name],
        }
        for name in sorted(totals)
    }
    return assignments, {
        "seed": seed,
        "candidate_count": candidate_count,
        "selected_candidate": best[1],
        "stratification_score": best[0],
        "articles": len(all_ids),
        "train_articles": len(all_ids) - validation_size,
        "validation_articles": validation_size,
        "effective_train_fraction": (len(all_ids) - validation_size) / len(all_ids),
        "feature_balance": drift,
    }


def _concept_vocab(rows: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                str(concept)
                for row in rows
                for unit in row.get("issuer_units") or ()
                for concept in unit.get("concepts") or ()
                if str(concept)
            }
        )
    )


def build_supervision_arrays(
    gold_rows: Sequence[Mapping[str, Any]],
    article_vectors: Mapping[str, np.ndarray],
    issuer_vectors: Mapping[tuple[str, str], np.ndarray],
    assignments: Mapping[str, str],
) -> dict[str, Any]:
    available_rows = [row for row in gold_rows if str(row["source_id"]) in article_vectors]
    concepts = _concept_vocab(available_rows)
    concept_index = {name: index for index, name in enumerate(concepts)}
    article_x: list[np.ndarray] = []
    article_y: list[int] = []
    article_metadata: list[dict[str, Any]] = []
    issuer_x: list[np.ndarray] = []
    issuer_eligibility: list[int] = []
    issuer_sentiment: list[int] = []
    issuer_concepts: list[np.ndarray] = []
    issuer_metadata: list[dict[str, Any]] = []
    unmatched = Counter()
    sentiment_index = {name: index for index, name in enumerate(SENTIMENT_LABELS)}
    for row in sorted(available_rows, key=lambda value: str(value["source_id"])):
        source_id = str(row["source_id"])
        split = str(assignments[source_id])
        article_x.append(article_vectors[source_id])
        article_y.append(int(bool(row.get("article_forecast_eligible"))))
        article_metadata.append(
            {
                "index": len(article_metadata),
                "source_id": source_id,
                "split": split,
                "authority_id": str(row.get("authority_id") or ""),
            }
        )
        for unit in row.get("issuer_units") or ():
            ticker = str(unit.get("ticker") or "")
            vector, match_status = match_issuer_embedding(
                source_id, ticker, issuer_vectors
            )
            if vector is None:
                unmatched[match_status] += 1
                continue
            target = np.zeros(len(concepts), dtype=np.uint8)
            for concept in unit.get("concepts") or ():
                index = concept_index.get(str(concept))
                if index is not None:
                    target[index] = 1
            sentiment = sentiment_index.get(str(unit.get("sentiment") or ""), -1)
            issuer_x.append(vector)
            issuer_eligibility.append(
                int(str(unit.get("forecast_eligibility")) == "eligible")
            )
            issuer_sentiment.append(sentiment)
            issuer_concepts.append(target)
            issuer_metadata.append(
                {
                    "index": len(issuer_metadata),
                    "source_id": source_id,
                    "unit_id": str(unit.get("unit_id") or ""),
                    "ticker": ticker,
                    "split": split,
                    "authority_id": str(row.get("authority_id") or ""),
                    "embedding_match": match_status,
                }
            )
    if not article_x or not issuer_x:
        raise RuntimeError("No supervised article or issuer samples were materialized")
    return {
        "article_embeddings": np.stack(article_x).astype(np.float32),
        "article_eligibility": np.asarray(article_y, dtype=np.uint8),
        "article_metadata": article_metadata,
        "issuer_embeddings": np.stack(issuer_x).astype(np.float32),
        "issuer_eligibility": np.asarray(issuer_eligibility, dtype=np.uint8),
        "issuer_sentiment": np.asarray(issuer_sentiment, dtype=np.int8),
        "issuer_concepts": np.stack(issuer_concepts).astype(np.uint8),
        "issuer_metadata": issuer_metadata,
        "concept_labels": concepts,
        "unmatched_issuer_units": dict(sorted(unmatched.items())),
    }


def dataset_file_manifest(root: Path, names: Sequence[str]) -> dict[str, Any]:
    return {
        name: {
            "bytes": (root / name).stat().st_size,
            "sha256": file_sha256(root / name),
        }
        for name in names
    }


def validate_prepared_dataset(root: Path) -> dict[str, Any]:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("version") not in SUPPORTED_DATASET_VERSIONS:
        raise RuntimeError("Prepared dataset version mismatch")
    for name, expected in manifest["files"].items():
        path = root / name
        if not path.is_file() or file_sha256(path) != expected["sha256"]:
            raise RuntimeError(f"Prepared dataset hash mismatch: {name}")
    article_x = np.load(root / "article_embeddings.npy", mmap_mode="r")
    article_y = np.load(root / "article_eligibility.npy", mmap_mode="r")
    issuer_x = np.load(root / "issuer_embeddings.npy", mmap_mode="r")
    issuer_y = np.load(root / "issuer_eligibility.npy", mmap_mode="r")
    sentiment = np.load(root / "issuer_sentiment.npy", mmap_mode="r")
    concepts = np.load(root / "issuer_concepts.npy", mmap_mode="r")
    article_meta = read_jsonl(root / "article_metadata.jsonl")
    issuer_meta = read_jsonl(root / "issuer_metadata.jsonl")
    if len(article_x) != len(article_y) or len(article_x) != len(article_meta):
        raise RuntimeError("Article array/metadata counts disagree")
    if not (
        len(issuer_x)
        == len(issuer_y)
        == len(sentiment)
        == len(concepts)
        == len(issuer_meta)
    ):
        raise RuntimeError("Issuer array/metadata counts disagree")
    article_sources = {row["source_id"] for row in article_meta}
    train_sources = {row["source_id"] for row in article_meta if row["split"] == "train"}
    validation_sources = article_sources - train_sources
    if train_sources & validation_sources or train_sources | validation_sources != article_sources:
        raise RuntimeError("Article split is not complete and disjoint")
    for row in issuer_meta:
        expected = "train" if row["source_id"] in train_sources else "validation"
        if row["split"] != expected:
            raise RuntimeError("Issuer sample crossed its article split boundary")
    return {
        "status": "pass",
        "articles": len(article_x),
        "issuer_units": len(issuer_x),
        "train_articles": len(train_sources),
        "validation_articles": len(validation_sources),
        "concept_labels": int(concepts.shape[1]),
        "article_group_leakage": 0,
        "file_hashes_verified": True,
    }


def _binary_class_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for value, name in ((0, "ineligible"), (1, "eligible")):
        tp = int(np.sum((y_true == value) & (y_pred == value)))
        fp = int(np.sum((y_true != value) & (y_pred == value)))
        fn = int(np.sum((y_true == value) & (y_pred != value)))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        result[name] = {
            "support": int(np.sum(y_true == value)),
            "precision": precision,
            "recall": recall,
            "f1": 2 * precision * recall / (precision + recall)
            if precision + recall
            else 0.0,
        }
    result["accuracy"] = float(np.mean(y_true == y_pred))
    result["macro_f1"] = float(np.mean([result[name]["f1"] for name in result if name in {"eligible", "ineligible"}]))
    result["confusion"] = {
        "TN": int(np.sum((y_true == 0) & (y_pred == 0))),
        "FP": int(np.sum((y_true == 0) & (y_pred == 1))),
        "FN": int(np.sum((y_true == 1) & (y_pred == 0))),
        "TP": int(np.sum((y_true == 1) & (y_pred == 1))),
    }
    return result


def _multiclass_metrics(
    y_true: np.ndarray, y_pred: np.ndarray, labels: Sequence[str]
) -> dict[str, Any]:
    per_label: dict[str, Any] = {}
    confusion = np.zeros((len(labels), len(labels)), dtype=np.int64)
    for truth, prediction in zip(y_true, y_pred, strict=True):
        confusion[int(truth), int(prediction)] += 1
    for index, name in enumerate(labels):
        tp = int(confusion[index, index])
        fp = int(confusion[:, index].sum() - tp)
        fn = int(confusion[index, :].sum() - tp)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        per_label[name] = {
            "support": int(confusion[index, :].sum()),
            "precision": precision,
            "recall": recall,
            "f1": 2 * precision * recall / (precision + recall)
            if precision + recall
            else 0.0,
        }
    return {
        "accuracy": float(np.mean(y_true == y_pred)),
        "macro_f1": float(np.mean([value["f1"] for value in per_label.values()])),
        "per_label": per_label,
        "confusion": {
            truth: {prediction: int(confusion[i, j]) for j, prediction in enumerate(labels)}
            for i, truth in enumerate(labels)
        },
    }


def _multilabel_metrics(
    y_true: np.ndarray, y_pred: np.ndarray, labels: Sequence[str]
) -> dict[str, Any]:
    per_label: dict[str, Any] = {}
    f1_values = []
    for index, name in enumerate(labels):
        truth = y_true[:, index]
        prediction = y_pred[:, index]
        tp = int(np.sum((truth == 1) & (prediction == 1)))
        fp = int(np.sum((truth == 0) & (prediction == 1)))
        fn = int(np.sum((truth == 1) & (prediction == 0)))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_label[name] = {
            "support": int(truth.sum()),
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "accuracy": float(np.mean(truth == prediction)),
        }
        if int(truth.sum()) > 0:
            f1_values.append(f1)
    return {
        "subset_accuracy": float(np.mean(np.all(y_true == y_pred, axis=1))),
        "micro_f1": _micro_multilabel_f1(y_true, y_pred),
        "macro_f1_supported_labels": float(np.mean(f1_values)) if f1_values else 0.0,
        "per_label": per_label,
    }


def _micro_multilabel_f1(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    tp = int(np.sum((y_true == 1) & (y_pred == 1)))
    fp = int(np.sum((y_true == 0) & (y_pred == 1)))
    fn = int(np.sum((y_true == 1) & (y_pred == 0)))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def evaluation_report(
    *,
    article_truth: np.ndarray,
    article_logits: np.ndarray,
    issuer_eligibility_truth: np.ndarray,
    issuer_eligibility_logits: np.ndarray,
    sentiment_truth: np.ndarray,
    sentiment_logits: np.ndarray,
    concept_truth: np.ndarray,
    concept_logits: np.ndarray,
    concept_labels: Sequence[str],
) -> dict[str, Any]:
    article_prediction = (article_logits >= 0.0).astype(np.uint8)
    issuer_prediction = (issuer_eligibility_logits >= 0.0).astype(np.uint8)
    sentiment_mask = sentiment_truth >= 0
    sentiment_prediction = np.argmax(sentiment_logits[sentiment_mask], axis=1)
    concept_prediction = (concept_logits >= 0.0).astype(np.uint8)
    report = {
        "article_forecast_eligibility": _binary_class_metrics(
            article_truth.astype(np.uint8), article_prediction
        ),
        "issuer_forecast_eligibility": _binary_class_metrics(
            issuer_eligibility_truth.astype(np.uint8), issuer_prediction
        ),
        "issuer_sentiment": _multiclass_metrics(
            sentiment_truth[sentiment_mask], sentiment_prediction, SENTIMENT_LABELS
        ),
        "issuer_concepts": _multilabel_metrics(
            concept_truth.astype(np.uint8), concept_prediction, concept_labels
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


def class_weight_binary(values: np.ndarray) -> float:
    positives = int(np.sum(values == 1))
    negatives = int(np.sum(values == 0))
    return float(np.clip(negatives / max(1, positives), 0.25, 20.0))


def class_weight_multiclass(values: np.ndarray, classes: int) -> np.ndarray:
    counts = np.bincount(values[values >= 0], minlength=classes).astype(np.float64)
    weights = np.sqrt(counts.sum() / np.maximum(1.0, counts * classes))
    return np.clip(weights, 0.25, 8.0).astype(np.float32)


def class_weight_multilabel(values: np.ndarray) -> np.ndarray:
    positives = values.sum(axis=0).astype(np.float64)
    negatives = len(values) - positives
    return np.clip(negatives / np.maximum(1.0, positives), 0.25, 20.0).astype(np.float32)


def set_reproducible_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    try:
        import torch

        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
    except ImportError:
        pass


def config_dict(config: TrainConfig) -> dict[str, Any]:
    return asdict(config)
