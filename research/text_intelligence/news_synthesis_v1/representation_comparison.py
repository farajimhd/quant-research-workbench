from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from research.mlops.clickhouse import ClickHouseHttpClient, sql_string

from .embedding_supervision import (
    COMPARISON_DATASET_VERSION,
    assert_runtime_path,
    canonical_json_sha256,
    dataset_file_manifest,
    file_sha256,
    match_issuer_embedding,
    read_jsonl,
    save_array,
    validate_prepared_dataset,
    write_json,
    write_jsonl,
)


OPENAI_EMBEDDING_VERSION = "news_openai_text_embedding_3_large_3072_v1"
ARRAY_NAMES = (
    "article_eligibility.npy",
    "issuer_eligibility.npy",
    "issuer_sentiment.npy",
    "issuer_concepts.npy",
)


def iter_openai_embeddings(
    client: ClickHouseHttpClient,
    source_ids: Sequence[str],
    *,
    embedding_version: str = OPENAI_EMBEDDING_VERSION,
    source_batch_size: int = 500,
) -> Iterable[dict[str, Any]]:
    for start in range(0, len(source_ids), source_batch_size):
        batch = source_ids[start : start + source_batch_size]
        source_sql = ",".join(sql_string(value) for value in batch)
        query = f"""
SELECT canonical_news_id AS source_id, ticker, embedding
FROM market_sip_compact.news_openai_embeddings_v1 FINAL
WHERE canonical_news_id IN ({source_sql})
  AND embedding_version = {sql_string(embedding_version)}
ORDER BY canonical_news_id, ticker
FORMAT JSONEachRow
"""
        yield from client.iter_json_each_row(query)


def prepare_openai_dataset(
    *,
    source_root: Path,
    output_root: Path,
    client: ClickHouseHttpClient,
) -> dict[str, Any]:
    source_root = assert_runtime_path(source_root)
    output_root = assert_runtime_path(output_root)
    validate_prepared_dataset(source_root)
    if output_root.exists():
        raise RuntimeError(f"Refusing to overwrite OpenAI dataset: {output_root}")
    article_meta = read_jsonl(source_root / "article_metadata.jsonl")
    issuer_meta = read_jsonl(source_root / "issuer_metadata.jsonl")
    source_ids = [str(row["source_id"]) for row in article_meta]
    vectors: dict[tuple[str, str], np.ndarray] = {}
    for row in iter_openai_embeddings(client, source_ids):
        key = (str(row["source_id"]), str(row["ticker"]).upper())
        vector = np.asarray(row["embedding"], dtype=np.float32)
        if key in vectors or vector.shape != (3072,) or not np.isfinite(vector).all():
            raise RuntimeError(f"Invalid or duplicate OpenAI embedding: {key}")
        norm = float(np.linalg.norm(vector))
        vectors[key] = vector / norm if norm > 0 else vector
    by_source = {source_id: vector for (source_id, _), vector in vectors.items()}
    article_indexes = [i for i, row in enumerate(article_meta) if row["source_id"] in by_source]
    issuer_indexes: list[int] = []
    issuer_vectors: list[np.ndarray] = []
    for index, row in enumerate(issuer_meta):
        vector, _ = match_issuer_embedding(row["source_id"], row["ticker"], vectors)
        if vector is not None:
            issuer_indexes.append(index)
            issuer_vectors.append(vector)
    _write_subset(
        source_root=source_root,
        output_root=output_root,
        representation="openai",
        article_indexes=article_indexes,
        issuer_indexes=issuer_indexes,
        article_vectors=np.stack([by_source[article_meta[i]["source_id"]] for i in article_indexes]),
        issuer_vectors=np.stack(issuer_vectors),
        authority={"embedding_version": OPENAI_EMBEDDING_VERSION, "dimensions": 3072},
    )
    return validate_prepared_dataset(output_root)


def subset_to_common_authority(
    *,
    source_root: Path,
    authority_root: Path,
    output_root: Path,
    representation: str,
) -> dict[str, Any]:
    source_root = assert_runtime_path(source_root)
    authority_root = assert_runtime_path(authority_root)
    output_root = assert_runtime_path(output_root)
    validate_prepared_dataset(source_root)
    validate_prepared_dataset(authority_root)
    if output_root.exists():
        raise RuntimeError(f"Refusing to overwrite common dataset: {output_root}")
    source_article_meta = read_jsonl(source_root / "article_metadata.jsonl")
    source_issuer_meta = read_jsonl(source_root / "issuer_metadata.jsonl")
    authority_articles = {
        row["source_id"] for row in read_jsonl(authority_root / "article_metadata.jsonl")
    }
    authority_units = {
        row["unit_id"] for row in read_jsonl(authority_root / "issuer_metadata.jsonl")
    }
    article_indexes = [
        i for i, row in enumerate(source_article_meta) if row["source_id"] in authority_articles
    ]
    issuer_indexes = [
        i for i, row in enumerate(source_issuer_meta) if row["unit_id"] in authority_units
    ]
    article_x = np.load(source_root / "article_embeddings.npy", mmap_mode="r")
    issuer_x = np.load(source_root / "issuer_embeddings.npy", mmap_mode="r")
    _write_subset(
        source_root=source_root,
        output_root=output_root,
        representation=representation,
        article_indexes=article_indexes,
        issuer_indexes=issuer_indexes,
        article_vectors=np.asarray(article_x[article_indexes]),
        issuer_vectors=np.asarray(issuer_x[issuer_indexes]),
        authority={"common_authority_root": str(authority_root)},
    )
    return validate_prepared_dataset(output_root)


def _write_subset(
    *,
    source_root: Path,
    output_root: Path,
    representation: str,
    article_indexes: Sequence[int],
    issuer_indexes: Sequence[int],
    article_vectors: np.ndarray,
    issuer_vectors: np.ndarray,
    authority: Mapping[str, Any],
) -> None:
    if not article_indexes or not issuer_indexes:
        raise RuntimeError("Common representation subset is empty")
    output_root.mkdir(parents=True)
    article_meta = read_jsonl(source_root / "article_metadata.jsonl")
    issuer_meta = read_jsonl(source_root / "issuer_metadata.jsonl")
    save_array(output_root / "article_embeddings.npy", article_vectors.astype(np.float32))
    save_array(output_root / "issuer_embeddings.npy", issuer_vectors.astype(np.float32))
    for name in ARRAY_NAMES:
        values = np.load(source_root / name, mmap_mode="r")
        indexes = article_indexes if name.startswith("article_") else issuer_indexes
        save_array(output_root / name, np.asarray(values[indexes]))
    write_jsonl(output_root / "article_metadata.jsonl", [article_meta[i] for i in article_indexes])
    write_jsonl(output_root / "issuer_metadata.jsonl", [issuer_meta[i] for i in issuer_indexes])
    label_contract = json.loads((source_root / "label_contract.json").read_text(encoding="utf-8"))
    write_json(output_root / "label_contract.json", label_contract)
    files = (
        "article_embeddings.npy", "article_eligibility.npy", "issuer_embeddings.npy",
        "issuer_eligibility.npy", "issuer_sentiment.npy", "issuer_concepts.npy",
        "article_metadata.jsonl", "issuer_metadata.jsonl", "label_contract.json",
    )
    manifest = {
        "version": COMPARISON_DATASET_VERSION,
        "status": "complete",
        "representation": {"kind": representation, **dict(authority)},
        "source_manifest_sha256": file_sha256(source_root / "manifest.json"),
        "files": dataset_file_manifest(output_root, files),
    }
    manifest["contract_sha256"] = canonical_json_sha256(manifest)
    write_json(output_root / "manifest.json", manifest)
    write_json(output_root / "VALIDATION.json", validate_prepared_dataset(output_root))
