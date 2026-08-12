from __future__ import annotations

import hashlib
import json
import math
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from research.mlops.clickhouse import ClickHouseHttpClient

from .embedding_supervision import (
    DATASET_VERSION,
    TFIDF_V7_DATASET_VERSION,
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
from .tfidf_source_ablation_v6 import _subset_rows_and_arrays
from .tfidf_supervision_v2 import _char_features, _structural_features, _word_features
from .tfidf_supervision_v3 import (
    anonymize_issuer_mentions,
    economic_relation_features,
    issuer_local_clauses,
    point_in_time_aliases,
)
from .tfidf_supervision_v4 import _load_canonical_documents
from .tfidf_supervision_v5 import (
    DEFAULT_RAW_DRIVE_ROOT,
    _list_values,
    _load_original_documents,
    original_body_text,
)


DEFAULT_TFIDF_V7_ROOT = (
    Path(r"D:\TradingML\runtimes")
    / "text_intelligence"
    / "news_synthesis_v1"
    / "tfidf_supervision_v7"
)
V7_FIELD_BUDGETS = {
    "provider_title_word": 1536,
    "provider_teaser_word": 768,
    "provider_body_word": 3072,
    "provider_title_char": 1024,
    "provider_teaser_char": 512,
    "provider_local_word": 1024,
    "provider_local_char": 512,
    "provider_economic": 256,
    "normalized_structural": 256,
    "normalized_economic": 256,
    "external_local_word": 768,
    "pdf_local_word": 768,
    "enrichment_economic": 256,
    "metadata_word": 512,
    "metadata_structural": 256,
}
V7_VIEW_PREFIXES = {
    "provider": ("provider_",),
    "normalized": ("normalized_",),
    "enrichment": ("external_local_", "pdf_local_", "enrichment_"),
    "metadata": ("metadata_",),
}


def _rename_family(features: Mapping[str, int], family: str) -> Counter[str]:
    return Counter(
        {
            f"{family}|{term.split('|', 1)[1]}": int(count)
            for term, count in features.items()
        }
    )


def _count_bucket(count: int) -> str:
    if count <= 0:
        return "0"
    if count == 1:
        return "1"
    if count <= 4:
        return "2_to_4"
    return "5_plus"


def invariant_metadata_features(
    payload: Mapping[str, Any],
    *,
    target_ticker: str,
    has_external: bool,
    has_pdf: bool,
) -> tuple[str, Counter[str]]:
    """Use semantic metadata and shape, excluding author/domain identity values."""

    channels = _list_values(payload.get("channels"))
    tags = _list_values(payload.get("tags"))
    raw_tickers = tuple(value.upper() for value in _list_values(payload.get("tickers")))
    tickers = tuple(dict.fromkeys(raw_tickers))
    metadata_text = "\n".join(
        value
        for value in (
            f"channels {' '.join(channels)}" if channels else "",
            f"tags {' '.join(tags)}" if tags else "",
        )
        if value
    )
    result: Counter[str] = Counter()
    result[f"metadata_structural|ticker_count:{_count_bucket(len(tickers))}"] = 1
    result[f"metadata_structural|channel_count:{_count_bucket(len(channels))}"] = 1
    result[f"metadata_structural|tag_count:{_count_bucket(len(tags))}"] = 1
    if target_ticker.upper() in tickers:
        result["metadata_structural|target_in_provider_tickers"] = 1
    for name, present in (
        ("author", payload.get("author")),
        ("url", payload.get("url")),
        ("updated", payload.get("last_updated")),
        ("external", has_external),
        ("pdf", has_pdf),
    ):
        if present:
            result[f"metadata_structural|has:{name}"] = 1
    published = str(payload.get("published") or "")
    try:
        hour = datetime.fromisoformat(published.replace("Z", "+00:00")).hour
    except ValueError:
        hour = -1
    if hour >= 0:
        session = "pre_market" if hour < 13 else "market_or_after" if hour < 21 else "late"
        result[f"metadata_structural|publication_session:{session}"] = 1
    return metadata_text, result


def _provider_only_normalized(fields: Mapping[str, str]) -> dict[str, str]:
    return {
        **{key: str(value or "") for key, value in fields.items()},
        "external": "",
        "pdf": "",
        "channels": "",
        "tags": "",
    }


def tfidf_v7_feature_counts(
    *,
    original_fields: Mapping[str, str],
    normalized_fields: Mapping[str, str],
    metadata_text: str,
    metadata_structural: Counter[str],
    ticker: str,
    aliases: Sequence[str],
) -> Counter[str]:
    provider = {
        name: anonymize_issuer_mentions(str(original_fields.get(name) or ""), aliases=aliases)
        for name in ("title", "teaser", "body")
    }
    provider_text = "\n".join(value for value in provider.values() if value)
    provider_local = anonymize_issuer_mentions(
        " ".join(issuer_local_clauses(provider_text, aliases=("<issuer>",))),
        aliases=("<issuer>",),
    )
    normalized_provider = _provider_only_normalized(normalized_fields)
    normalized_text = "\n".join(
        str(normalized_provider.get(name) or "") for name in ("title", "teaser", "body")
    )
    external_local = anonymize_issuer_mentions(
        " ".join(
            issuer_local_clauses(str(normalized_fields.get("external") or ""), aliases=aliases)
        ),
        aliases=aliases,
    )
    pdf_local = anonymize_issuer_mentions(
        " ".join(issuer_local_clauses(str(normalized_fields.get("pdf") or ""), aliases=aliases)),
        aliases=aliases,
    )

    result: Counter[str] = Counter()
    result.update(_word_features("provider_title_word", provider["title"]))
    result.update(_word_features("provider_teaser_word", provider["teaser"]))
    result.update(_word_features("provider_body_word", provider["body"]))
    result.update(_char_features("provider_title_char", provider["title"]))
    result.update(_char_features("provider_teaser_char", provider["teaser"]))
    result.update(_word_features("provider_local_word", provider_local))
    result.update(_char_features("provider_local_char", provider_local))
    result.update(_rename_family(economic_relation_features(provider_local), "provider_economic"))
    result.update(
        _rename_family(
            _structural_features(normalized_provider, ticker), "normalized_structural"
        )
    )
    result.update(
        _rename_family(
            economic_relation_features(normalized_text), "normalized_economic"
        )
    )
    result.update(_word_features("external_local_word", external_local))
    result.update(_word_features("pdf_local_word", pdf_local))
    result.update(
        _rename_family(
            economic_relation_features("\n".join((external_local, pdf_local))),
            "enrichment_economic",
        )
    )
    result.update(_word_features("metadata_word", metadata_text))
    result.update(metadata_structural)
    return result


def fit_v7_vocabulary(
    documents: Sequence[tuple[Mapping[str, Any], str, Sequence[str]]],
    *,
    min_document_frequency: int = 3,
    budgets: Mapping[str, int] = V7_FIELD_BUDGETS,
) -> tuple[tuple[str, ...], np.ndarray, dict[str, Any]]:
    if not documents:
        raise ValueError("Training documents are empty")
    document_frequency: dict[str, Counter[str]] = defaultdict(Counter)
    for document, ticker, aliases in documents:
        for term in tfidf_v7_feature_counts(**document, ticker=ticker, aliases=aliases):
            document_frequency[term.split("|", 1)[0]][term] += 1
    return fit_v7_vocabulary_from_document_frequency(
        document_frequency,
        training_document_count=len(documents),
        min_document_frequency=min_document_frequency,
        budgets=budgets,
    )


def fit_v7_vocabulary_from_document_frequency(
    document_frequency: Mapping[str, Counter[str]],
    *,
    training_document_count: int,
    min_document_frequency: int = 3,
    budgets: Mapping[str, int] = V7_FIELD_BUDGETS,
) -> tuple[tuple[str, ...], np.ndarray, dict[str, Any]]:
    if training_document_count <= 0:
        raise ValueError("Training document count must be positive")
    selected: list[tuple[str, int]] = []
    families: dict[str, Any] = {}
    for family, budget in budgets.items():
        minimum = (
            1
            if family
            in {
                "normalized_structural",
                "provider_economic",
                "normalized_economic",
                "enrichment_economic",
                "metadata_structural",
                "target_clause_structure",
                "target_clause_interaction",
                "cross_view_agreement",
                "predicate_role",
                "state_transition",
                "numeric_magnitude",
            }
            else min_document_frequency
        )
        observed = document_frequency.get(family, Counter())
        candidates = [item for item in observed.items() if item[1] >= minimum]
        candidates.sort(key=lambda item: (-item[1], item[0]))
        chosen = candidates[:budget]
        selected.extend(chosen)
        families[family] = {
            "observed": len(observed),
            "selected": len(chosen),
            "budget": budget,
            "min_document_frequency": minimum,
        }
    terms = tuple(term for term, _ in selected)
    idf = np.asarray(
        [
            math.log((1.0 + training_document_count) / (1.0 + count)) + 1.0
            for _, count in selected
        ],
        dtype=np.float32,
    )
    return terms, idf, {
        "training_documents": training_document_count,
        "selected_features": len(terms),
        "families": families,
        "training_only_vocabulary": True,
        "feature_only_change_from_v6": True,
        "provider_original_text": True,
        "provider_metadata": "semantic_values_and_invariant_shape_only",
        "normalized_text_role": "provider_semantic_and_structural_view",
        "enrichment_role": "issuer_local_provenance_separated_external_and_pdf_views",
        "view_normalization": "independent_l2_then_equal_weight_concatenation",
        "raw_author_domain_values_excluded": True,
        "gold_or_prediction_features": False,
    }


def transform_v7(
    document: Mapping[str, Any],
    *,
    ticker: str,
    aliases: Sequence[str],
    vocabulary: Mapping[str, int],
    idf: np.ndarray,
    view_indexes: Mapping[str, np.ndarray] | None = None,
) -> np.ndarray:
    counts = tfidf_v7_feature_counts(**document, ticker=ticker, aliases=aliases)
    return transform_v7_counts(
        counts,
        vocabulary=vocabulary,
        idf=idf,
        view_indexes=view_indexes,
    )


def transform_v7_counts(
    counts: Mapping[str, int],
    *,
    vocabulary: Mapping[str, int],
    idf: np.ndarray,
    view_indexes: Mapping[str, np.ndarray] | None = None,
) -> np.ndarray:
    vector = np.zeros(len(vocabulary), dtype=np.float32)
    for term, count in counts.items():
        index = vocabulary.get(term)
        if index is not None:
            vector[index] = (1.0 + math.log(count)) * float(idf[index])
    indexes_by_view = view_indexes or v7_view_indexes(vocabulary)
    for indexes in indexes_by_view.values():
        if not len(indexes):
            continue
        norm = float(np.linalg.norm(vector[indexes]))
        if norm:
            vector[indexes] /= norm
    return l2_normalize(vector)


def v7_view_indexes(vocabulary: Mapping[str, int]) -> dict[str, np.ndarray]:
    terms_by_index = sorted(vocabulary, key=vocabulary.get)
    return {
        view: np.asarray(
            [
                index
                for index, term in enumerate(terms_by_index)
                if term.split("|", 1)[0].startswith(prefixes)
            ],
            dtype=np.int64,
        )
        for view, prefixes in V7_VIEW_PREFIXES.items()
    }


def prepare_tfidf_v7_dataset(
    *,
    source_data_root: Path,
    output_root: Path,
    client: ClickHouseHttpClient,
    raw_drive_root: Path = DEFAULT_RAW_DRIVE_ROOT,
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
        raise RuntimeError(f"Refusing to overwrite TF-IDF V7 dataset: {output_root}")
    source_manifest = json.loads((source_data_root / "manifest.json").read_text(encoding="utf-8"))
    if source_manifest.get("version") != DATASET_VERSION:
        raise RuntimeError("V7 requires the frozen Qwen supervision split/label authority")

    all_article_metadata = read_jsonl(source_data_root / "article_metadata.jsonl")
    all_source_ids = sorted({str(row["source_id"]) for row in all_article_metadata})
    _, payloads, raw_authority_rows, raw_report = _load_original_documents(
        client,
        all_source_ids,
        database=source_database,
        source_batch_size=source_batch_size,
        raw_drive_root=raw_drive_root,
        allow_revised_original_artifacts=True,
    )
    exact_sources = {
        str(row["source_id"])
        for row in raw_authority_rows
        if row["hash_verification_method"]
        != "current_original_artifact_identity_verified_hash_drift"
    }
    excluded_sources = sorted(set(all_source_ids) - exact_sources)
    article_metadata, issuer_metadata, label_arrays = _subset_rows_and_arrays(
        source_data_root, exact_sources
    )
    normalized_documents, normalized_authority_rows, normalized_report = _load_canonical_documents(
        client,
        sorted(exact_sources),
        database=source_database,
        source_batch_size=source_batch_size,
    )
    article_document_keys = set(normalized_documents)
    fixed_keys = set(normalized_documents)
    for row in issuer_metadata:
        source_id = str(row["source_id"])
        ticker = str(row["ticker"]).rsplit(":", 1)[-1].upper()
        fixed_keys.add((source_id, ticker))

    documents: dict[tuple[str, str], dict[str, Any]] = {}
    for source_id, ticker in sorted(fixed_keys):
        source_keys = sorted(key for key in normalized_documents if key[0] == source_id)
        normalized = normalized_documents.get(
            (source_id, ticker), normalized_documents[source_keys[0]]
        )
        payload = payloads[source_id]
        metadata_text, metadata_structural = invariant_metadata_features(
            payload,
            target_ticker=ticker,
            has_external=bool(normalized.get("external")),
            has_pdf=bool(normalized.get("pdf")),
        )
        documents[(source_id, ticker)] = {
            "original_fields": {
                "title": str(payload.get("title") or ""),
                "teaser": str(payload.get("teaser") or ""),
                "body": original_body_text(payload.get("body")),
            },
            "normalized_fields": dict(normalized),
            "metadata_text": metadata_text,
            "metadata_structural": metadata_structural,
        }

    identity_index = load_identity_index(client, identity_database)
    aliases_by_key: dict[tuple[str, str], tuple[str, ...]] = {}
    identity_rows: list[dict[str, Any]] = []
    for source_id, ticker in sorted(fixed_keys):
        published = str(documents[(source_id, ticker)]["normalized_fields"]["published_at_utc"])
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
    terms, idf, feature_report = fit_v7_vocabulary(
        [
            (document, ticker, aliases_by_key[(source_id, ticker)])
            for (source_id, ticker), document in documents.items()
            if source_id in training_sources
        ],
        min_document_frequency=min_document_frequency,
    )
    vocabulary = {term: index for index, term in enumerate(terms)}
    view_indexes = v7_view_indexes(vocabulary)
    vectors = {
        key: transform_v7(
            document,
            ticker=key[1],
            aliases=aliases_by_key[key],
            vocabulary=vocabulary,
            idf=idf,
            view_indexes=view_indexes,
        )
        for key, document in documents.items()
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
            raise RuntimeError(f"Missing TF-IDF V7 issuer vector: {row['unit_id']}")
        issuer_vectors.append(vector)
        issuer_match_counts[status] += 1

    output_root.mkdir(parents=True)
    save_array(output_root / "article_embeddings.npy", article_vectors)
    save_array(output_root / "issuer_embeddings.npy", np.stack(issuer_vectors).astype(np.float32))
    for name, values in label_arrays.items():
        save_array(output_root / name, values)
    write_jsonl(output_root / "article_metadata.jsonl", article_metadata)
    write_jsonl(output_root / "issuer_metadata.jsonl", issuer_metadata)
    write_jsonl(output_root / "identity_features.jsonl", identity_rows)
    write_jsonl(
        output_root / "source_text_authority.jsonl",
        [row for row in raw_authority_rows if str(row["source_id"]) in exact_sources],
    )
    write_jsonl(output_root / "normalized_text_authority.jsonl", normalized_authority_rows)
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
        "normalized_text_authority.jsonl",
        "label_contract.json",
        "vocabulary.json",
    )
    manifest = {
        "version": TFIDF_V7_DATASET_VERSION,
        "status": "complete",
        "representation": {"kind": "tfidf_v7", **feature_report},
        "population": {
            "frozen_articles": len(all_source_ids),
            "exact_authority_articles": len(exact_sources),
            "excluded_revised_artifacts": len(excluded_sources),
            "excluded_source_ids_sha256": hashlib.sha256(
                "\n".join(excluded_sources).encode("utf-8")
            ).hexdigest(),
        },
        "source_authority": raw_report,
        "normalized_authority": normalized_report,
        "identity_authority": {"database": identity_database, "rows": len(identity_rows)},
        "issuer_vector_matching": dict(sorted(issuer_match_counts.items())),
        "source_manifest_sha256": file_sha256(source_data_root / "manifest.json"),
        "split": {
            "authority": "frozen_source_split_membership_after_exact-authority_filter",
            "train_articles": len(training_sources),
            "validation_articles": len(article_metadata) - len(training_sources),
        },
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
