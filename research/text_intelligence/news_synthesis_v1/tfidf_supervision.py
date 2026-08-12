from __future__ import annotations

import json
import math
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from research.mlops.clickhouse import ClickHouseHttpClient, sql_string

from .embedding_supervision import (
    DATASET_VERSION,
    DEFAULT_DATA_ROOT,
    DEFAULT_EMBEDDING_MODEL,
    TFIDF_DATASET_VERSION,
    assert_runtime_path,
    canonical_json_sha256,
    dataset_file_manifest,
    file_sha256,
    l2_normalize,
    match_issuer_embedding,
    read_jsonl,
    save_array,
    validate_prepared_dataset,
    write_json,
    write_jsonl,
)


DEFAULT_TOKENIZER_MODEL = "Qwen/Qwen3-0.6B"
DEFAULT_TFIDF_DATA_ROOT = (
    Path(r"D:\TradingML\runtimes")
    / "text_intelligence"
    / "news_synthesis_v1"
    / "tfidf_supervision_v1"
    / "data"
)
DEFAULT_TFIDF_RUN_ROOT = DEFAULT_TFIDF_DATA_ROOT.parent / "run"
TOKEN_PATTERN = re.compile(r"[\w][\w'-]*", flags=re.UNICODE)


def decode_qwen_token_documents(
    rows: Iterable[Mapping[str, Any]],
    *,
    tokenizer_model: str = DEFAULT_TOKENIZER_MODEL,
) -> tuple[dict[tuple[str, str], str], dict[str, Any]]:
    try:
        from transformers import AutoTokenizer
    except ImportError as exc:
        raise RuntimeError("TF-IDF preparation requires transformers") from exc
    tokenizer = AutoTokenizer.from_pretrained(
        tokenizer_model,
        trust_remote_code=True,
        local_files_only=True,
    )
    chunks: dict[tuple[str, str], list[tuple[int, list[int]]]] = defaultdict(list)
    logical_keys: set[tuple[str, str, int]] = set()
    row_count = 0
    for row in rows:
        source_id = str(row.get("source_id") or "")
        ticker = str(row.get("ticker") or "").upper()
        chunk_index = int(row.get("token_chunk_index") or 0)
        logical_key = (source_id, ticker, chunk_index)
        if not source_id or not ticker:
            raise RuntimeError("Token row is missing source_id or ticker")
        if logical_key in logical_keys:
            raise RuntimeError(f"Duplicate logical token chunk: {logical_key}")
        logical_keys.add(logical_key)
        token_count = int(row.get("token_count") or 0)
        input_ids = [int(value) for value in row.get("input_ids") or ()]
        if token_count <= 0 or token_count > len(input_ids):
            raise RuntimeError(f"Invalid token count for {logical_key}")
        chunks[(source_id, ticker)].append((chunk_index, input_ids[:token_count]))
        row_count += 1
    documents: dict[tuple[str, str], str] = {}
    for key, values in sorted(chunks.items()):
        ordered = sorted(values)
        if [index for index, _ in ordered] != list(range(len(ordered))):
            raise RuntimeError(f"Non-contiguous token chunks for {key}")
        decoded = [
            tokenizer.decode(ids, skip_special_tokens=True, clean_up_tokenization_spaces=False)
            for _, ids in ordered
        ]
        documents[key] = "\n".join(part for part in decoded if part.strip())
        if not documents[key].strip():
            raise RuntimeError(f"Decoded token document is empty for {key}")
    return documents, {
        "token_rows": row_count,
        "documents": len(documents),
        "source_ids": len({source_id for source_id, _ in documents}),
        "tokenizer_model": tokenizer_model,
    }


def tfidf_terms(text: str) -> list[str]:
    tokens = [match.group(0).lower() for match in TOKEN_PATTERN.finditer(text)]
    unigrams = [f"u:{token}" for token in tokens]
    bigrams = [f"b:{left}::{right}" for left, right in zip(tokens, tokens[1:])]
    return [*unigrams, *bigrams]


def fit_tfidf_vocabulary(
    documents: Sequence[str],
    *,
    max_features: int = 4096,
    min_document_frequency: int = 3,
    max_document_fraction: float = 0.995,
) -> tuple[tuple[str, ...], np.ndarray, dict[str, Any]]:
    if not documents:
        raise ValueError("Training documents are empty")
    if max_features <= 0 or min_document_frequency <= 0:
        raise ValueError("TF-IDF feature and frequency bounds must be positive")
    document_frequency: Counter[str] = Counter()
    for document in documents:
        document_frequency.update(set(tfidf_terms(document)))
    maximum_frequency = max(1, int(math.floor(len(documents) * max_document_fraction)))
    candidates = [
        (term, count)
        for term, count in document_frequency.items()
        if min_document_frequency <= count <= maximum_frequency
    ]
    candidates.sort(key=lambda item: (-item[1], item[0]))
    selected = candidates[:max_features]
    terms = tuple(term for term, _ in selected)
    idf = np.asarray(
        [math.log((1.0 + len(documents)) / (1.0 + count)) + 1.0 for _, count in selected],
        dtype=np.float32,
    )
    if not terms:
        raise RuntimeError("No TF-IDF terms survived the training-only vocabulary bounds")
    return terms, idf, {
        "training_documents": len(documents),
        "observed_terms": len(document_frequency),
        "selected_terms": len(terms),
        "min_document_frequency": min_document_frequency,
        "max_document_fraction": max_document_fraction,
        "unigrams_and_bigrams": True,
        "sublinear_term_frequency": True,
        "l2_normalized": True,
    }


def transform_tfidf(text: str, vocabulary: Mapping[str, int], idf: np.ndarray) -> np.ndarray:
    counts = Counter(tfidf_terms(text))
    vector = np.zeros(len(vocabulary), dtype=np.float32)
    for term, count in counts.items():
        index = vocabulary.get(term)
        if index is not None:
            vector[index] = (1.0 + math.log(count)) * float(idf[index])
    return l2_normalize(vector)


def prepare_tfidf_dataset(
    *,
    source_data_root: Path,
    output_root: Path,
    client: ClickHouseHttpClient,
    tokenizer_model: str = DEFAULT_TOKENIZER_MODEL,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    max_features: int = 4096,
    min_document_frequency: int = 3,
    source_batch_size: int = 500,
) -> dict[str, Any]:
    started = time.perf_counter()
    source_data_root = assert_runtime_path(source_data_root)
    output_root = assert_runtime_path(output_root)
    validate_prepared_dataset(source_data_root)
    if output_root.exists():
        raise RuntimeError(f"Refusing to overwrite TF-IDF dataset: {output_root}")
    source_manifest = json.loads(
        (source_data_root / "manifest.json").read_text(encoding="utf-8")
    )
    source_representation = str(
        (source_manifest.get("representation") or {}).get("kind") or "qwen"
    )
    if source_manifest.get("version") != DATASET_VERSION and source_representation != "qwen":
        raise RuntimeError("TF-IDF comparison requires the Qwen supervision dataset")
    article_metadata = read_jsonl(source_data_root / "article_metadata.jsonl")
    issuer_metadata = read_jsonl(source_data_root / "issuer_metadata.jsonl")
    source_ids = sorted({str(row["source_id"]) for row in article_metadata})
    token_rows = iter_qwen_token_rows(
        client,
        source_ids,
        tokenizer_model=tokenizer_model,
        embedding_model=embedding_model,
        source_batch_size=source_batch_size,
    )
    documents, token_report = decode_qwen_token_documents(
        token_rows, tokenizer_model=tokenizer_model
    )
    training_sources = {
        str(row["source_id"]) for row in article_metadata if row["split"] == "train"
    }
    training_documents = [
        text for (source_id, _), text in documents.items() if source_id in training_sources
    ]
    terms, idf, vocabulary_report = fit_tfidf_vocabulary(
        training_documents,
        max_features=max_features,
        min_document_frequency=min_document_frequency,
    )
    vocabulary = {term: index for index, term in enumerate(terms)}
    vectors = {
        key: transform_tfidf(text, vocabulary, idf) for key, text in documents.items()
    }
    vectors_by_source: dict[str, list[np.ndarray]] = defaultdict(list)
    for (source_id, _), vector in vectors.items():
        vectors_by_source[source_id].append(vector)
    missing_articles = [
        row["source_id"] for row in article_metadata if row["source_id"] not in vectors_by_source
    ]
    if missing_articles:
        raise RuntimeError(f"TF-IDF documents missing for {len(missing_articles)} articles")
    article_vectors = np.stack(
        [
            l2_normalize(np.mean(vectors_by_source[row["source_id"]], axis=0))
            for row in article_metadata
        ]
    ).astype(np.float32)
    issuer_vectors: list[np.ndarray] = []
    missing_issuers: list[str] = []
    for row in issuer_metadata:
        vector, _ = match_issuer_embedding(row["source_id"], row["ticker"], vectors)
        if vector is None:
            missing_issuers.append(str(row["unit_id"]))
        else:
            issuer_vectors.append(vector)
    if missing_issuers:
        raise RuntimeError(
            f"TF-IDF documents missing for {len(missing_issuers)} Qwen-matched issuer units"
        )
    output_root.mkdir(parents=True)
    save_array(output_root / "article_embeddings.npy", article_vectors)
    save_array(output_root / "issuer_embeddings.npy", np.stack(issuer_vectors).astype(np.float32))
    for name in (
        "article_eligibility.npy",
        "issuer_eligibility.npy",
        "issuer_sentiment.npy",
        "issuer_concepts.npy",
    ):
        save_array(output_root / name, np.load(source_data_root / name))
    write_jsonl(output_root / "article_metadata.jsonl", article_metadata)
    write_jsonl(output_root / "issuer_metadata.jsonl", issuer_metadata)
    label_contract = json.loads(
        (source_data_root / "label_contract.json").read_text(encoding="utf-8")
    )
    write_json(output_root / "label_contract.json", label_contract)
    write_json(
        output_root / "vocabulary.json",
        {"terms": list(terms), "idf": [float(value) for value in idf]},
    )
    file_names = (
        "article_embeddings.npy",
        "article_eligibility.npy",
        "issuer_embeddings.npy",
        "issuer_eligibility.npy",
        "issuer_sentiment.npy",
        "issuer_concepts.npy",
        "article_metadata.jsonl",
        "issuer_metadata.jsonl",
        "label_contract.json",
        "vocabulary.json",
    )
    manifest = {
        "version": TFIDF_DATASET_VERSION,
        "status": "complete",
        "representation": {
            "kind": "tfidf",
            "feature_count": len(terms),
            "source": "decoded durable Qwen token chunks",
            **vocabulary_report,
        },
        "comparison_authority": {
            "qwen_data_root": str(source_data_root),
            "qwen_manifest_sha256": file_sha256(source_data_root / "manifest.json"),
            "identical_article_metadata": article_metadata
            == read_jsonl(source_data_root / "article_metadata.jsonl"),
            "identical_issuer_metadata": issuer_metadata
            == read_jsonl(source_data_root / "issuer_metadata.jsonl"),
            "training_only_vocabulary": True,
        },
        "token_authority": token_report,
        "split": source_manifest.get("split")
        or {
            "articles": len(article_metadata),
            "train_articles": sum(row["split"] == "train" for row in article_metadata),
            "validation_articles": sum(
                row["split"] == "validation" for row in article_metadata
            ),
        },
        "files": dataset_file_manifest(output_root, file_names),
        "elapsed_seconds": time.perf_counter() - started,
    }
    manifest["contract_sha256"] = canonical_json_sha256(
        {key: value for key, value in manifest.items() if key != "elapsed_seconds"}
    )
    write_json(output_root / "manifest.json", manifest)
    validation = validate_prepared_dataset(output_root)
    write_json(output_root / "VALIDATION.json", validation)
    return {"manifest": manifest, "validation": validation}


def iter_qwen_token_rows(
    client: ClickHouseHttpClient,
    source_ids: Sequence[str],
    *,
    tokenizer_model: str,
    embedding_model: str,
    source_batch_size: int,
) -> Iterable[dict[str, Any]]:
    total_rows = 0
    for start in range(0, len(source_ids), source_batch_size):
        batch = source_ids[start : start + source_batch_size]
        source_sql = ",".join(sql_string(source_id) for source_id in batch)
        query = f"""
SELECT t.source_id, t.ticker, t.token_chunk_index, t.token_count, t.input_ids
FROM
(
    SELECT source_id, ticker, token_chunk_index, token_count, input_ids, tokenizer_model
    FROM market_sip_compact.news_text_tokens FINAL
    WHERE source_id IN ({source_sql})
) AS t
INNER JOIN
(
    SELECT DISTINCT source_id, ticker, token_chunk_index
    FROM market_sip_compact.news_text_embeddings FINAL
    WHERE source_id IN ({source_sql})
      AND embedding_model = {sql_string(embedding_model)}
) AS e USING (source_id, ticker, token_chunk_index)
WHERE t.tokenizer_model = {sql_string(tokenizer_model)}
ORDER BY t.source_id, t.ticker, t.token_chunk_index
FORMAT JSONEachRow
"""
        batch_rows = 0
        for row in client.iter_json_each_row(query):
            batch_rows += 1
            total_rows += 1
            yield row
        print(
            json.dumps(
                {
                    "stage": "tfidf_token_read",
                    "source_ids_completed": min(start + len(batch), len(source_ids)),
                    "source_ids_total": len(source_ids),
                    "batch_token_rows": batch_rows,
                    "token_rows_total": total_rows,
                },
                sort_keys=True,
            ),
            flush=True,
        )
