from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from research.news_reaction_model.v5.config import FeatureConfig, LoaderConfig


def article_model_text(row: dict[str, Any], *, max_chars: int) -> str:
    """Match the existing news embedding field order without exposing post-publication data."""
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
    text = "\n".join(part for part in parts if str(part).strip())
    return text[: max(1, int(max_chars))]


def compact_char_text(row: dict[str, Any], *, max_chars: int) -> str:
    """Prioritize wording-rich publication-time fields for the costlier character channel."""
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
class TfidfLsaBundle:
    feature_config: FeatureConfig
    word_vectorizer: Any
    char_vectorizer: Any
    word_svd: Any
    char_svd: Any
    word_effective_dim: int
    char_effective_dim: int

    @classmethod
    def fit(cls, rows: list[dict[str, Any]], config: FeatureConfig) -> "TfidfLsaBundle":
        try:
            from sklearn.decomposition import TruncatedSVD
            from sklearn.feature_extraction.text import TfidfVectorizer
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("V5 TF-IDF preparation requires scikit-learn and scipy in the active environment.") from exc
        if len(rows) < 3:
            raise ValueError("At least three training articles are required to fit V5 TF-IDF features.")
        common = {
            "lowercase": True,
            "strip_accents": "unicode",
            "min_df": config.min_df,
            "max_df": config.max_df,
            "sublinear_tf": True,
            "norm": "l2",
            "dtype": np.float32,
        }
        word = TfidfVectorizer(
            analyzer="word",
            token_pattern=r"(?u)\b[\w][\w.$%+\-:/]*\b",
            ngram_range=(config.word_ngram_min, config.word_ngram_max),
            max_features=config.word_max_features,
            **common,
        )
        char = TfidfVectorizer(
            analyzer="char_wb",
            ngram_range=(config.char_ngram_min, config.char_ngram_max),
            max_features=config.char_max_features,
            **common,
        )
        word_texts = [article_model_text(row, max_chars=config.max_text_chars) for row in rows]
        print(f"TF-IDF WORD vectorize articles={len(word_texts):,} max_features={config.word_max_features:,}", flush=True)
        word_matrix = word.fit_transform(word_texts)
        word_dim = min(config.output_dim, max(1, min(word_matrix.shape) - 1))
        print(f"TF-IDF WORD reduce shape={word_matrix.shape} output_dim={word_dim}", flush=True)
        word_svd = TruncatedSVD(
            n_components=word_dim, n_iter=config.svd_iterations, random_state=config.random_seed,
        ).fit(word_matrix)
        del word_matrix, word_texts
        char_texts = [compact_char_text(row, max_chars=config.char_text_chars) for row in rows]
        print(f"TF-IDF CHAR vectorize articles={len(char_texts):,} max_features={config.char_max_features:,}", flush=True)
        char_matrix = char.fit_transform(char_texts)
        char_dim = min(config.output_dim, max(1, min(char_matrix.shape) - 1))
        print(f"TF-IDF CHAR reduce shape={char_matrix.shape} output_dim={char_dim}", flush=True)
        char_svd = TruncatedSVD(
            n_components=char_dim, n_iter=config.svd_iterations, random_state=config.random_seed,
        ).fit(char_matrix)
        return cls(config, word, char, word_svd, char_svd, word_dim, char_dim)

    def transform(self, rows: list[dict[str, Any]]) -> np.ndarray:
        if not rows:
            return np.empty((0, 2, self.feature_config.output_dim), dtype=np.float32)
        word_texts = [article_model_text(row, max_chars=self.feature_config.max_text_chars) for row in rows]
        char_texts = [compact_char_text(row, max_chars=self.feature_config.char_text_chars) for row in rows]
        word = self.word_svd.transform(self.word_vectorizer.transform(word_texts)).astype(np.float32, copy=False)
        char = self.char_svd.transform(self.char_vectorizer.transform(char_texts)).astype(np.float32, copy=False)
        word = _l2_normalize_and_pad(word, self.feature_config.output_dim)
        char = _l2_normalize_and_pad(char, self.feature_config.output_dim)
        return np.stack((word, char), axis=1)


def _l2_normalize_and_pad(values: np.ndarray, output_dim: int) -> np.ndarray:
    norms = np.linalg.norm(values, axis=1, keepdims=True)
    values = values / np.maximum(norms, np.float32(1e-12))
    if values.shape[1] == output_dim:
        return values.astype(np.float32, copy=False)
    result = np.zeros((values.shape[0], output_dim), dtype=np.float32)
    result[:, : values.shape[1]] = values
    return result


def save_bundle(bundle: TfidfLsaBundle, loader: LoaderConfig) -> dict[str, Any]:
    try:
        import joblib
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("V5 TF-IDF preparation requires joblib.") from exc
    root = Path(loader.feature_artifact_root)
    root.mkdir(parents=True, exist_ok=True)
    bundle_path = root / "tfidf_lsa_bundle.joblib"
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
        "word_vocabulary": len(bundle.word_vectorizer.vocabulary_),
        "char_vocabulary": len(bundle.char_vectorizer.vocabulary_),
        "word_effective_dim": bundle.word_effective_dim,
        "char_effective_dim": bundle.char_effective_dim,
        "bundle_sha256": digest,
        "bundle_file": bundle_path.name,
        "leakage_contract": "fit uses train range only; train and validation use one frozen bundle",
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def load_feature_manifest(loader: LoaderConfig) -> dict[str, Any]:
    """Validate the frozen representation identity without deserializing the estimators."""
    root = Path(loader.feature_artifact_root)
    manifest_path = root / "manifest.json"
    bundle_path = root / "tfidf_lsa_bundle.joblib"
    if not manifest_path.exists() or not bundle_path.exists():
        raise RuntimeError(f"V5 TF-IDF artifacts are missing under {root}. Run the V5 preparation command first.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("representation_name") != loader.representation_name:
        raise RuntimeError("V5 TF-IDF artifact representation does not match the configured representation.")
    if manifest.get("dataset_version") != loader.dataset_version:
        raise RuntimeError("V5 TF-IDF artifact dataset version does not match the configured dataset version.")
    if manifest.get("source_dataset_version") != loader.source_dataset_version:
        raise RuntimeError("V5 TF-IDF artifact source dataset version does not match the configured V4 source.")
    if manifest.get("fit_range") != [loader.train_start, loader.train_end_exclusive]:
        raise RuntimeError("V5 TF-IDF artifact training range does not match the configured chronological split.")
    digest = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
    if digest != manifest.get("bundle_sha256"):
        raise RuntimeError("V5 TF-IDF bundle checksum does not match its manifest.")
    return manifest


def load_bundle(loader: LoaderConfig) -> tuple[TfidfLsaBundle, dict[str, Any]]:
    try:
        import joblib
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("Loading V5 TF-IDF features requires joblib.") from exc
    root = Path(loader.feature_artifact_root)
    bundle_path = root / "tfidf_lsa_bundle.joblib"
    manifest = load_feature_manifest(loader)
    bundle = joblib.load(bundle_path)
    if int(bundle.feature_config.output_dim) != int(loader.embedding_dim):
        raise RuntimeError("V5 TF-IDF output dimension does not match the V4-compatible model input dimension.")
    return bundle, manifest


def batched(values: list[Any], size: int) -> Iterable[list[Any]]:
    size = max(1, int(size))
    for start in range(0, len(values), size):
        yield values[start : start + size]
