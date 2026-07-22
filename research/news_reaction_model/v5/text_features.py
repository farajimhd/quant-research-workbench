from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from research.news_reaction_model.v5.config import FeatureConfig, LoaderConfig


def article_model_text(row: dict[str, Any], *, max_chars: int) -> str:
    """Build the publication-time text used by the lexical model."""
    parts = [
        "NEWS",
        f"provider: {row.get('provider', '') or ''}",
        f"ticker: {row.get('ticker', '') or ''}",
        f"published_at_utc: {row.get('published_at_utc', '') or ''}",
        f"title: {row.get('title', '') or ''}",
        f"teaser: {row.get('teaser', '') or ''}",
        f"channels: {row.get('channels', '') or ''}",
        f"tags: {row.get('provider_tags', '') or ''}",
    ]
    for label, key in (("BODY", "body_text"), ("EXTERNAL_TEXT", "external_text"), ("PDF_TEXT", "pdf_text")):
        value = str(row.get(key, "") or "")
        if value:
            parts.extend((label, value))
    return "\n".join(part for part in parts if str(part).strip())[: max(1, int(max_chars))]


def compact_char_text(row: dict[str, Any], *, max_chars: int) -> str:
    """Prioritize wording-rich fields for the more numerous character n-grams."""
    text = "\n".join(
        str(value or "")
        for value in (
            row.get("title"), row.get("teaser"), row.get("channels"), row.get("provider_tags"),
            row.get("body_text"), row.get("external_text"), row.get("pdf_text"),
        )
        if str(value or "").strip()
    )
    return text[: max(1, int(max_chars))]


@dataclass(slots=True)
class SparseFeatureRows:
    word_ids: list[list[int]]
    word_weights: list[list[float]]
    char_ids: list[list[int]]
    char_weights: list[list[float]]


@dataclass(slots=True)
class SparseTfidfBundle:
    """Frozen train-only IDF authority over bounded hashed n-gram vocabularies."""

    feature_config: FeatureConfig
    training_documents: int
    word_hash_buckets: np.ndarray
    word_mapping: np.ndarray
    word_idf: np.ndarray
    char_hash_buckets: np.ndarray
    char_mapping: np.ndarray
    char_idf: np.ndarray

    @classmethod
    def fit(
        cls,
        batches: Iterable[list[dict[str, Any]]],
        config: FeatureConfig,
    ) -> "SparseTfidfBundle":
        word_vectorizer, char_vectorizer = _vectorizers(config, binary=True)
        word_df = np.zeros(config.hash_buckets, dtype=np.uint32)
        char_df = np.zeros(config.hash_buckets, dtype=np.uint32)
        documents = 0
        for batch_index, rows in enumerate(batches, start=1):
            word = word_vectorizer.transform(
                [article_model_text(row, max_chars=config.max_text_chars) for row in rows]
            )
            char = char_vectorizer.transform(
                [compact_char_text(row, max_chars=config.char_text_chars) for row in rows]
            )
            word_df += np.bincount(word.indices, minlength=config.hash_buckets).astype(np.uint32, copy=False)
            char_df += np.bincount(char.indices, minlength=config.hash_buckets).astype(np.uint32, copy=False)
            documents += len(rows)
            if batch_index == 1 or batch_index % 25 == 0:
                print(
                    f"TF-IDF FIT batches={batch_index:,} articles={documents:,} "
                    f"word_buckets={(word_df > 0).sum():,} char_buckets={(char_df > 0).sum():,}",
                    flush=True,
                )
        if documents < 3:
            raise ValueError("At least three training articles are required to fit V5 TF-IDF features.")
        word_buckets, word_idf = _select_vocabulary(word_df, documents, config.word_vocab_size, config.min_df)
        char_buckets, char_idf = _select_vocabulary(char_df, documents, config.char_vocab_size, config.min_df)
        print(
            f"TF-IDF FIT COMPLETE articles={documents:,} word_vocab={len(word_buckets):,} "
            f"char_vocab={len(char_buckets):,}",
            flush=True,
        )
        return cls(
            config, documents,
            word_buckets, _bucket_mapping(word_buckets, config.hash_buckets), word_idf,
            char_buckets, _bucket_mapping(char_buckets, config.hash_buckets), char_idf,
        )

    def transform(self, rows: list[dict[str, Any]]) -> SparseFeatureRows:
        if not rows:
            return SparseFeatureRows([], [], [], [])
        word_vectorizer, char_vectorizer = _vectorizers(self.feature_config, binary=False)
        word = word_vectorizer.transform(
            [article_model_text(row, max_chars=self.feature_config.max_text_chars) for row in rows]
        )
        char = char_vectorizer.transform(
            [compact_char_text(row, max_chars=self.feature_config.char_text_chars) for row in rows]
        )
        word_ids, word_weights = _project_selected_tfidf(
            word, self.word_mapping, self.word_idf, len(self.word_hash_buckets)
        )
        char_ids, char_weights = _project_selected_tfidf(
            char, self.char_mapping, self.char_idf, len(self.char_hash_buckets)
        )
        return SparseFeatureRows(word_ids, word_weights, char_ids, char_weights)


def _vectorizers(config: FeatureConfig, *, binary: bool) -> tuple[Any, Any]:
    try:
        from sklearn.feature_extraction.text import HashingVectorizer
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("V5 sparse TF-IDF preparation requires scikit-learn and scipy.") from exc
    common = {
        "n_features": config.hash_buckets,
        "alternate_sign": False,
        "binary": binary,
        "norm": None,
        "lowercase": True,
        "strip_accents": "unicode",
        "dtype": np.float32,
    }
    word = HashingVectorizer(
        analyzer="word",
        token_pattern=r"(?u)\b[\w][\w.$%+\-:/]*\b",
        ngram_range=(config.word_ngram_min, config.word_ngram_max),
        **common,
    )
    char = HashingVectorizer(
        analyzer="char_wb",
        ngram_range=(config.char_ngram_min, config.char_ngram_max),
        **common,
    )
    return word, char


def _select_vocabulary(
    document_frequency: np.ndarray,
    documents: int,
    requested_size: int,
    min_df: int,
) -> tuple[np.ndarray, np.ndarray]:
    eligible = np.flatnonzero(document_frequency >= min_df)
    if len(eligible) < requested_size:
        raise RuntimeError(
            f"Only {len(eligible):,} hashed n-gram buckets satisfy min_df={min_df}; "
            f"the configured model requires {requested_size:,}."
        )
    frequencies = document_frequency[eligible]
    chosen = eligible[np.argpartition(frequencies, -requested_size)[-requested_size:]]
    order = np.lexsort((chosen, -document_frequency[chosen].astype(np.int64)))
    chosen = chosen[order].astype(np.uint32, copy=False)
    idf = (np.log((1.0 + documents) / (1.0 + document_frequency[chosen])) + 1.0).astype(np.float32)
    return chosen, idf


def _project_selected_tfidf(
    matrix: Any,
    mapping: np.ndarray,
    idf: np.ndarray,
    vocabulary_size: int,
) -> tuple[list[list[int]], list[list[float]]]:
    from scipy.sparse import coo_matrix
    from sklearn.preprocessing import normalize

    source = matrix.tocoo(copy=False)
    local_ids = mapping[source.col]
    keep = local_ids >= 0
    rows = source.row[keep]
    columns = local_ids[keep]
    values = (1.0 + np.log(np.maximum(source.data[keep], np.float32(1.0)))) * idf[columns]
    selected = coo_matrix(
        (values.astype(np.float32, copy=False), (rows, columns)),
        shape=(matrix.shape[0], vocabulary_size),
        dtype=np.float32,
    ).tocsr()
    normalize(selected, norm="l2", axis=1, copy=False)
    ids: list[list[int]] = []
    weights: list[list[float]] = []
    for row in range(selected.shape[0]):
        start, end = selected.indptr[row], selected.indptr[row + 1]
        ids.append(selected.indices[start:end].astype(np.uint32, copy=False).tolist())
        weights.append(selected.data[start:end].astype(np.float32, copy=False).tolist())
    return ids, weights


def _bucket_mapping(selected_hash_buckets: np.ndarray, hash_buckets: int) -> np.ndarray:
    mapping = np.full(hash_buckets, -1, dtype=np.int32)
    mapping[selected_hash_buckets] = np.arange(len(selected_hash_buckets), dtype=np.int32)
    return mapping


def save_bundle(bundle: SparseTfidfBundle, loader: LoaderConfig) -> dict[str, Any]:
    try:
        import joblib
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("V5 sparse TF-IDF preparation requires joblib.") from exc
    root = Path(loader.feature_artifact_root)
    root.mkdir(parents=True, exist_ok=True)
    bundle_path = root / "sparse_tfidf_bundle.joblib"
    temporary = bundle_path.with_suffix(".joblib.tmp")
    joblib.dump(bundle, temporary, compress=3)
    temporary.replace(bundle_path)
    digest = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
    manifest = {
        "representation_name": loader.representation_name,
        "dataset_version": loader.dataset_version,
        "source_dataset": f"{loader.dataset_database}.{loader.source_dataset_table}",
        "source_dataset_version": loader.source_dataset_version,
        "fit_range": [loader.train_start, loader.train_end_exclusive],
        "validation_range": [loader.validation_start, loader.validation_end_exclusive],
        "feature_config": asdict(bundle.feature_config),
        "training_documents": bundle.training_documents,
        "word_vocab_size": len(bundle.word_hash_buckets),
        "char_vocab_size": len(bundle.char_hash_buckets),
        "bundle_sha256": digest,
        "bundle_file": bundle_path.name,
        "leakage_contract": "IDF and selected hashed vocabularies use 2019-2025 only; 2026 is transform-only",
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def load_feature_manifest(loader: LoaderConfig) -> dict[str, Any]:
    root = Path(loader.feature_artifact_root)
    manifest_path = root / "manifest.json"
    bundle_path = root / "sparse_tfidf_bundle.joblib"
    if not manifest_path.exists() or not bundle_path.exists():
        raise RuntimeError(f"V5 sparse TF-IDF artifacts are missing under {root}. Run preparation first.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = {
        "representation_name": loader.representation_name,
        "dataset_version": loader.dataset_version,
        "source_dataset_version": loader.source_dataset_version,
        "fit_range": [loader.train_start, loader.train_end_exclusive],
        "word_vocab_size": loader.word_vocab_size,
        "char_vocab_size": loader.char_vocab_size,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise RuntimeError(f"V5 feature manifest mismatch for {key}: {manifest.get(key)!r} != {value!r}.")
    digest = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
    if digest != manifest.get("bundle_sha256"):
        raise RuntimeError("V5 sparse TF-IDF bundle checksum does not match its manifest.")
    return manifest


def load_bundle(loader: LoaderConfig) -> tuple[SparseTfidfBundle, dict[str, Any]]:
    try:
        import joblib
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("Loading V5 sparse TF-IDF features requires joblib.") from exc
    manifest = load_feature_manifest(loader)
    bundle = joblib.load(Path(loader.feature_artifact_root) / "sparse_tfidf_bundle.joblib")
    return bundle, manifest
