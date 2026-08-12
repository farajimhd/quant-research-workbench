from __future__ import annotations

import json
import math
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from research.mlops.clickhouse import ClickHouseHttpClient, quote_ident, sql_string

from .embedding_supervision import (
    DATASET_VERSION,
    TFIDF_V4_DATASET_VERSION,
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
from .storage import load_identity_index
from .tfidf_supervision_v3 import (
    V3_FIELD_BUDGETS,
    point_in_time_aliases,
    tfidf_v3_feature_counts_from_fields,
)


DEFAULT_TFIDF_V4_ROOT = (
    Path(r"D:\TradingML\runtimes")
    / "text_intelligence"
    / "news_synthesis_v1"
    / "tfidf_supervision_v4"
)
CANONICAL_FIELD_NAMES = (
    "provider",
    "ticker",
    "published_at_utc",
    "title",
    "teaser",
    "channels",
    "tags",
    "body",
    "external",
    "pdf",
)


def _chunks(values: Sequence[str], size: int) -> Iterable[Sequence[str]]:
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


def iter_canonical_news_rows(
    client: ClickHouseHttpClient,
    source_ids: Sequence[str],
    *,
    database: str = "q_live",
    source_batch_size: int = 500,
) -> Iterable[dict[str, Any]]:
    """Read normalized source fields directly; no tokenizer tables are consulted."""

    db = quote_ident(database)
    for batch in _chunks(tuple(source_ids), source_batch_size):
        values = ", ".join(sql_string(source_id) for source_id in batch)
        sql = f"""
SELECT
    n.canonical_news_id AS source_id,
    arrayJoin(n.tickers) AS ticker,
    toString(n.published_at_utc) AS published_at_utc,
    n.provider AS provider,
    n.title AS title,
    n.teaser AS teaser,
    n.body_text AS body,
    n.external_text AS external,
    n.pdf_text AS pdf,
    arrayStringConcat(n.channels, ',') AS channels,
    arrayStringConcat(n.provider_tags, ',') AS tags,
    n.text_hash AS normalized_text_hash,
    n.normalizer_version AS normalizer_version,
    n.raw_payload_hash AS raw_payload_hash,
    lengthUTF8(n.body_text) AS body_char_count,
    lengthUTF8(n.external_text) AS external_char_count,
    lengthUTF8(n.pdf_text) AS pdf_char_count
FROM
(
    SELECT *
    FROM {db}.benzinga_news_normalized_v1 FINAL
    WHERE canonical_news_id IN ({values})
) AS n
ORDER BY source_id, ticker
FORMAT JSONEachRow
"""
        for line in client.execute(sql).splitlines():
            if line.strip():
                yield json.loads(line)


def canonical_feature_fields(row: Mapping[str, Any]) -> dict[str, str]:
    return {name: str(row.get(name, "") or "") for name in CANONICAL_FIELD_NAMES}


def _load_canonical_documents(
    client: ClickHouseHttpClient,
    source_ids: Sequence[str],
    *,
    database: str,
    source_batch_size: int,
) -> tuple[dict[tuple[str, str], dict[str, str]], list[dict[str, Any]], dict[str, Any]]:
    documents: dict[tuple[str, str], dict[str, str]] = {}
    authority_by_source: dict[str, dict[str, Any]] = {}
    ticker_counts: Counter[str] = Counter()
    for row in iter_canonical_news_rows(
        client,
        source_ids,
        database=database,
        source_batch_size=source_batch_size,
    ):
        source_id = str(row["source_id"])
        ticker = str(row["ticker"]).upper()
        key = (source_id, ticker)
        if key in documents:
            raise RuntimeError(f"Duplicate canonical source/ticker row: {key}")
        normalized_hash = str(row.get("normalized_text_hash") or "")
        if not normalized_hash:
            raise RuntimeError(f"Canonical normalized text hash is missing: {key}")
        fields = canonical_feature_fields(row)
        documents[key] = fields
        ticker_counts[source_id] += 1
        authority = {
            "source_id": source_id,
            "published_at_utc": fields["published_at_utc"],
            "normalizer_version": str(row.get("normalizer_version") or ""),
            "normalized_text_hash": normalized_hash,
            "raw_payload_hash": str(row.get("raw_payload_hash") or ""),
            "body_char_count": int(row.get("body_char_count") or 0),
            "external_char_count": int(row.get("external_char_count") or 0),
            "pdf_char_count": int(row.get("pdf_char_count") or 0),
        }
        previous = authority_by_source.setdefault(source_id, authority)
        if previous != authority:
            raise RuntimeError(f"Inconsistent canonical authority rows: {source_id}")
    missing = sorted(set(source_ids) - set(authority_by_source))
    if missing:
        raise RuntimeError(
            f"Canonical normalized source coverage missing {len(missing)} rows; first={missing[:5]}"
        )
    authority_rows = [
        {**authority_by_source[source_id], "ticker_count": ticker_counts[source_id]}
        for source_id in sorted(authority_by_source)
    ]
    report = {
        "database": database,
        "normalized_table": "benzinga_news_normalized_v1",
        "input_authority": "direct_normalized_fields",
        "tokenizer_dependency": False,
        "token_ids_read": False,
        "requested_sources": len(source_ids),
        "covered_sources": len(authority_by_source),
        "source_ticker_documents": len(documents),
        "multi_ticker_sources": sum(count > 1 for count in ticker_counts.values()),
        "full_text_without_qwen_prefix_truncation": True,
    }
    return documents, authority_rows, report


def fit_v4_vocabulary(
    documents: Sequence[tuple[str, Mapping[str, str], Sequence[str]]],
    *,
    min_document_frequency: int = 3,
    budgets: Mapping[str, int] = V3_FIELD_BUDGETS,
) -> tuple[tuple[str, ...], np.ndarray, dict[str, Any]]:
    if not documents:
        raise ValueError("Training documents are empty")
    document_frequency: dict[str, Counter[str]] = defaultdict(Counter)
    for ticker, fields, aliases in documents:
        for term in tfidf_v3_feature_counts_from_fields(
            fields, ticker=ticker, aliases=aliases
        ):
            document_frequency[term.split("|", 1)[0]][term] += 1
    selected: list[tuple[str, int]] = []
    family_report: dict[str, Any] = {}
    for family, budget in budgets.items():
        minimum = 1 if family in {"structural", "economic_relation"} else min_document_frequency
        candidates = [
            item for item in document_frequency[family].items() if item[1] >= minimum
        ]
        candidates.sort(key=lambda item: (-item[1], item[0]))
        chosen = candidates[:budget]
        selected.extend(chosen)
        family_report[family] = {
            "observed": len(document_frequency[family]),
            "selected": len(chosen),
            "budget": budget,
            "min_document_frequency": minimum,
        }
    terms = tuple(term for term, _ in selected)
    idf = np.asarray(
        [math.log((1.0 + len(documents)) / (1.0 + count)) + 1.0 for _, count in selected],
        dtype=np.float32,
    )
    return terms, idf, {
        "training_documents": len(documents),
        "selected_features": len(terms),
        "families": family_report,
        "training_only_vocabulary": True,
        "feature_definition_unchanged_from_v3": True,
        "source_change_only_from_v3": True,
        "source_fields": list(CANONICAL_FIELD_NAMES),
        "gold_or_prediction_features": False,
    }


def transform_v4(
    fields: Mapping[str, str],
    *,
    ticker: str,
    aliases: Sequence[str],
    vocabulary: Mapping[str, int],
    idf: np.ndarray,
) -> np.ndarray:
    counts = tfidf_v3_feature_counts_from_fields(fields, ticker=ticker, aliases=aliases)
    vector = np.zeros(len(vocabulary), dtype=np.float32)
    for term, count in counts.items():
        index = vocabulary.get(term)
        if index is not None:
            vector[index] = (1.0 + math.log(count)) * float(idf[index])
    return l2_normalize(vector)


def prepare_tfidf_v4_dataset(
    *,
    source_data_root: Path,
    output_root: Path,
    client: ClickHouseHttpClient,
    source_database: str = "q_live",
    identity_database: str = "q_live",
    min_document_frequency: int = 3,
    source_batch_size: int = 500,
) -> dict[str, Any]:
    started = time.perf_counter()
    source_data_root = assert_runtime_path(source_data_root)
    output_root = assert_runtime_path(output_root)
    validate_prepared_dataset(source_data_root)
    if output_root.exists():
        raise RuntimeError(f"Refusing to overwrite TF-IDF V4 dataset: {output_root}")
    source_manifest = json.loads((source_data_root / "manifest.json").read_text(encoding="utf-8"))
    source_representation = str((source_manifest.get("representation") or {}).get("kind") or "qwen")
    if source_manifest.get("version") != DATASET_VERSION and source_representation != "qwen":
        raise RuntimeError("TF-IDF V4 requires the frozen Qwen supervision split/label authority")

    article_metadata = read_jsonl(source_data_root / "article_metadata.jsonl")
    issuer_metadata = read_jsonl(source_data_root / "issuer_metadata.jsonl")
    source_ids = sorted({str(row["source_id"]) for row in article_metadata})
    documents, source_authority_rows, source_report = _load_canonical_documents(
        client,
        source_ids,
        database=source_database,
        source_batch_size=source_batch_size,
    )
    article_document_keys = set(documents)
    frozen_issuer_views = 0
    for row in issuer_metadata:
        source_id = str(row["source_id"])
        ticker = str(row["ticker"]).rsplit(":", 1)[-1].upper()
        if match_issuer_embedding(source_id, ticker, documents)[0] is not None:
            continue
        source_keys = sorted(key for key in documents if key[0] == source_id)
        if not source_keys:
            raise RuntimeError(f"Missing canonical text for issuer unit: {row['unit_id']}")
        fields = dict(documents[source_keys[0]])
        fields["ticker"] = ticker
        documents[(source_id, ticker)] = fields
        frozen_issuer_views += 1
    identity_index = load_identity_index(client, identity_database)
    identity_rows: list[dict[str, Any]] = []
    aliases_by_key: dict[tuple[str, str], tuple[str, ...]] = {}
    for (source_id, ticker), fields in sorted(documents.items()):
        published = fields["published_at_utc"]
        aliases = point_in_time_aliases(identity_index, ticker=ticker, published_at_utc=published)
        aliases_by_key[(source_id, ticker)] = aliases
        identity_rows.append(
            {
                "source_id": source_id,
                "ticker": ticker,
                "published_at_utc": published,
                "aliases": list(aliases),
                "point_in_time_status": "resolved" if len(aliases) > 1 else "ticker_only",
            }
        )

    training_sources = {
        str(row["source_id"]) for row in article_metadata if row["split"] == "train"
    }
    training_documents = [
        (ticker, fields, aliases_by_key[(source_id, ticker)])
        for (source_id, ticker), fields in documents.items()
        if source_id in training_sources
    ]
    terms, idf, feature_report = fit_v4_vocabulary(
        training_documents, min_document_frequency=min_document_frequency
    )
    vocabulary = {term: index for index, term in enumerate(terms)}
    vectors = {
        key: transform_v4(
            fields,
            ticker=key[1],
            aliases=aliases_by_key[key],
            vocabulary=vocabulary,
            idf=idf,
        )
        for key, fields in documents.items()
    }
    vectors_by_source: dict[str, list[np.ndarray]] = defaultdict(list)
    for (source_id, ticker), vector in vectors.items():
        if (source_id, ticker) in article_document_keys:
            vectors_by_source[source_id].append(vector)
    article_vectors = np.stack(
        [
            l2_normalize(np.mean(vectors_by_source[row["source_id"]], axis=0))
            for row in article_metadata
        ]
    ).astype(np.float32)
    issuer_vectors: list[np.ndarray] = []
    issuer_match_counts: Counter[str] = Counter()
    for row in issuer_metadata:
        vector, status = match_issuer_embedding(row["source_id"], row["ticker"], vectors)
        if vector is None:
            raise RuntimeError(f"Missing TF-IDF V4 issuer vector: {row['unit_id']}")
        issuer_vectors.append(vector)
        issuer_match_counts[status] += 1

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
    write_jsonl(output_root / "identity_features.jsonl", identity_rows)
    write_jsonl(output_root / "source_text_authority.jsonl", source_authority_rows)
    write_json(
        output_root / "label_contract.json",
        json.loads((source_data_root / "label_contract.json").read_text(encoding="utf-8")),
    )
    write_json(
        output_root / "vocabulary.json",
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
        "source_text_authority.jsonl",
        "label_contract.json",
        "vocabulary.json",
    )
    manifest = {
        "version": TFIDF_V4_DATASET_VERSION,
        "status": "complete",
        "representation": {"kind": "tfidf_v4", **feature_report},
        "model_change_from_v3": False,
        "source_authority": source_report,
        "identity_authority": {
            "database": identity_database,
            "artifact": "identity_features.jsonl",
            "rows": len(identity_rows),
        },
        "issuer_vector_matching": dict(sorted(issuer_match_counts.items())),
        "frozen_issuer_views_added": frozen_issuer_views,
        "source_manifest_sha256": file_sha256(source_data_root / "manifest.json"),
        "split": source_manifest.get("split"),
        "files": dataset_file_manifest(output_root, files),
        "elapsed_seconds": time.perf_counter() - started,
    }
    manifest["contract_sha256"] = canonical_json_sha256(
        {key: value for key, value in manifest.items() if key != "elapsed_seconds"}
    )
    write_json(output_root / "manifest.json", manifest)
    validation = validate_prepared_dataset(output_root)
    write_json(output_root / "VALIDATION.json", validation)
    return {"manifest": manifest, "validation": validation}
