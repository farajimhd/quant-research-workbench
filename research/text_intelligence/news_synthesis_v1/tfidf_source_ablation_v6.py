from __future__ import annotations

import hashlib
import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from pipelines.news.benzinga.news_benzinga_render_v2 import render_news_source
from research.mlops.clickhouse import ClickHouseHttpClient

from .embedding_supervision import (
    DATASET_VERSION,
    TFIDF_V6_DATASET_VERSION,
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
from .tfidf_supervision_v3 import point_in_time_aliases
from .tfidf_supervision_v4 import (
    _load_canonical_documents,
    fit_v4_vocabulary,
    transform_v4,
)
from .tfidf_supervision_v5 import (
    DEFAULT_RAW_DRIVE_ROOT,
    _load_original_documents,
    original_body_text,
)


DEFAULT_TFIDF_V6_ROOT = (
    Path(r"D:\TradingML\runtimes")
    / "text_intelligence"
    / "news_synthesis_v1"
    / "tfidf_source_ablation_v6"
)
LANES = ("original_provider", "normalized_provider", "rendered_provider")


def controlled_feature_fields(
    *,
    ticker: str,
    published_at_utc: str,
    title: str,
    teaser: str,
    body: str,
) -> dict[str, str]:
    """Return the identical provider-only field contract used by every lane."""

    return {
        "provider": "benzinga",
        "ticker": ticker,
        "published_at_utc": published_at_utc,
        "title": title,
        "teaser": teaser,
        "channels": "",
        "tags": "",
        "body": body,
        "external": "",
        "pdf": "",
    }


def _provider_rendered_body(payload: Mapping[str, Any]) -> str:
    body = str(payload.get("body") or "")
    if not body:
        return ""
    return render_news_source(
        body,
        source_kind="provider_body",
        source_ordinal=0,
        source_url=str(payload.get("url") or ""),
        artifact_path="",
        content_format="html",
    ).rendered_text


def _subset_rows_and_arrays(
    source_data_root: Path,
    exact_sources: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, np.ndarray]]:
    article_metadata_all = read_jsonl(source_data_root / "article_metadata.jsonl")
    issuer_metadata_all = read_jsonl(source_data_root / "issuer_metadata.jsonl")
    article_indexes = [
        index
        for index, row in enumerate(article_metadata_all)
        if str(row["source_id"]) in exact_sources
    ]
    issuer_indexes = [
        index
        for index, row in enumerate(issuer_metadata_all)
        if str(row["source_id"]) in exact_sources
    ]
    arrays = {
        "article_eligibility.npy": np.load(source_data_root / "article_eligibility.npy")[
            article_indexes
        ],
        "issuer_eligibility.npy": np.load(source_data_root / "issuer_eligibility.npy")[
            issuer_indexes
        ],
        "issuer_sentiment.npy": np.load(source_data_root / "issuer_sentiment.npy")[issuer_indexes],
        "issuer_concepts.npy": np.load(source_data_root / "issuer_concepts.npy")[issuer_indexes],
    }
    return (
        [article_metadata_all[index] for index in article_indexes],
        [issuer_metadata_all[index] for index in issuer_indexes],
        arrays,
    )


def _write_lane(
    *,
    output_root: Path,
    lane: str,
    documents: Mapping[tuple[str, str], Mapping[str, str]],
    article_document_keys: set[tuple[str, str]],
    aliases_by_key: Mapping[tuple[str, str], Sequence[str]],
    identity_rows: Sequence[Mapping[str, Any]],
    article_metadata: Sequence[Mapping[str, Any]],
    issuer_metadata: Sequence[Mapping[str, Any]],
    label_arrays: Mapping[str, np.ndarray],
    label_contract: Mapping[str, Any],
    source_data_root: Path,
    min_document_frequency: int,
) -> dict[str, Any]:
    lane_root = output_root / "data" / lane
    if lane_root.exists():
        raise RuntimeError(f"Refusing to overwrite V6 lane: {lane_root}")
    training_sources = {
        str(row["source_id"]) for row in article_metadata if row["split"] == "train"
    }
    training_documents = [
        (ticker, fields, aliases_by_key[(source_id, ticker)])
        for (source_id, ticker), fields in documents.items()
        if source_id in training_sources
    ]
    terms, idf, feature_report = fit_v4_vocabulary(
        training_documents,
        min_document_frequency=min_document_frequency,
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
            raise RuntimeError(f"Missing V6 {lane} issuer vector: {row['unit_id']}")
        issuer_vectors.append(vector)
        issuer_match_counts[status] += 1

    lane_root.mkdir(parents=True)
    save_array(lane_root / "article_embeddings.npy", article_vectors)
    save_array(lane_root / "issuer_embeddings.npy", np.stack(issuer_vectors).astype(np.float32))
    for name, values in label_arrays.items():
        save_array(lane_root / name, values)
    write_jsonl(lane_root / "article_metadata.jsonl", article_metadata)
    write_jsonl(lane_root / "issuer_metadata.jsonl", issuer_metadata)
    write_jsonl(lane_root / "identity_features.jsonl", identity_rows)
    write_json(lane_root / "label_contract.json", label_contract)
    write_json(
        lane_root / "vocabulary.json",
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
    manifest = {
        "version": TFIDF_V6_DATASET_VERSION,
        "status": "complete",
        "representation": {
            "kind": "tfidf_v6_ablation",
            "lane": lane,
            **feature_report,
            "controlled_fields": ["title", "teaser", "provider_body"],
            "external_pdf_metadata_excluded": True,
        },
        "source_manifest_sha256": file_sha256(source_data_root / "manifest.json"),
        "split": {
            "authority": "frozen_source_split_membership_after_exact-authority_filter",
            "train_articles": len(training_sources),
            "validation_articles": len(article_metadata) - len(training_sources),
        },
        "issuer_vector_matching": dict(sorted(issuer_match_counts.items())),
        "files": dataset_file_manifest(lane_root, files),
    }
    manifest["contract_sha256"] = canonical_json_sha256(manifest)
    write_json(lane_root / "manifest.json", manifest)
    validation = validate_prepared_dataset(lane_root)
    write_json(lane_root / "VALIDATION.json", validation)
    return {
        "data_root": str(lane_root),
        "selected_features": len(terms),
        "validation": validation,
    }


def prepare_source_ablation_v6(
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
        raise RuntimeError(f"Refusing to overwrite TF-IDF V6 experiment: {output_root}")
    source_manifest = json.loads((source_data_root / "manifest.json").read_text(encoding="utf-8"))
    if source_manifest.get("version") != DATASET_VERSION:
        raise RuntimeError("V6 requires the frozen Qwen supervision split/label authority")

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
        if row["hash_verification_method"] != "current_original_artifact_identity_verified_hash_drift"
    }
    excluded_sources = sorted(set(all_source_ids) - exact_sources)
    article_metadata, issuer_metadata, label_arrays = _subset_rows_and_arrays(
        source_data_root, exact_sources
    )
    normalized_documents, normalized_authority_rows, normalized_report = (
        _load_canonical_documents(
            client,
            sorted(exact_sources),
            database=source_database,
            source_batch_size=source_batch_size,
        )
    )
    article_document_keys = set(normalized_documents)
    fixed_keys = set(normalized_documents)
    for row in issuer_metadata:
        source_id = str(row["source_id"])
        ticker = str(row["ticker"]).rsplit(":", 1)[-1].upper()
        fixed_keys.add((source_id, ticker))

    rendered_body_by_source = {
        source_id: _provider_rendered_body(payloads[source_id]) for source_id in exact_sources
    }
    lane_documents: dict[str, dict[tuple[str, str], dict[str, str]]] = {
        lane: {} for lane in LANES
    }
    for source_id, ticker in sorted(fixed_keys):
        source_keys = sorted(key for key in normalized_documents if key[0] == source_id)
        if not source_keys:
            raise RuntimeError(f"Missing fixed normalized source: {source_id}")
        normalized = normalized_documents.get((source_id, ticker), normalized_documents[source_keys[0]])
        published = str(normalized["published_at_utc"])
        payload = payloads[source_id]
        lane_documents["original_provider"][(source_id, ticker)] = controlled_feature_fields(
            ticker=ticker,
            published_at_utc=published,
            title=str(payload.get("title") or ""),
            teaser=str(payload.get("teaser") or ""),
            body=original_body_text(payload.get("body")),
        )
        lane_documents["normalized_provider"][(source_id, ticker)] = controlled_feature_fields(
            ticker=ticker,
            published_at_utc=published,
            title=str(normalized["title"]),
            teaser=str(normalized["teaser"]),
            body=str(normalized["body"]),
        )
        lane_documents["rendered_provider"][(source_id, ticker)] = controlled_feature_fields(
            ticker=ticker,
            published_at_utc=published,
            title=str(payload.get("title") or ""),
            teaser=str(payload.get("teaser") or ""),
            body=rendered_body_by_source[source_id],
        )

    identity_index = load_identity_index(client, identity_database)
    aliases_by_key: dict[tuple[str, str], tuple[str, ...]] = {}
    identity_rows: list[dict[str, Any]] = []
    for source_id, ticker in sorted(fixed_keys):
        published = lane_documents["normalized_provider"][(source_id, ticker)]["published_at_utc"]
        aliases = point_in_time_aliases(
            identity_index, ticker=ticker, published_at_utc=published
        )
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

    label_contract = json.loads(
        (source_data_root / "label_contract.json").read_text(encoding="utf-8")
    )
    lane_reports = {
        lane: _write_lane(
            output_root=output_root,
            lane=lane,
            documents=lane_documents[lane],
            article_document_keys=article_document_keys,
            aliases_by_key=aliases_by_key,
            identity_rows=identity_rows,
            article_metadata=article_metadata,
            issuer_metadata=issuer_metadata,
            label_arrays=label_arrays,
            label_contract=label_contract,
            source_data_root=source_data_root,
            min_document_frequency=min_document_frequency,
        )
        for lane in LANES
    }
    report = {
        "status": "complete",
        "experiment": "tfidf_source_representation_ablation_v6",
        "population": {
            "frozen_articles": len(all_source_ids),
            "exact_authority_articles": len(exact_sources),
            "excluded_revised_artifacts": len(excluded_sources),
            "excluded_source_ids_sha256": hashlib.sha256(
                "\n".join(excluded_sources).encode("utf-8")
            ).hexdigest(),
            "articles": len(article_metadata),
            "issuer_units": len(issuer_metadata),
        },
        "controls": {
            "same_source_ids": True,
            "same_frozen_split_membership": True,
            "same_labels": True,
            "same_source_ticker_views": True,
            "same_point_in_time_aliases": True,
            "same_feature_extractor_and_budgets": True,
            "same_model_and_training": True,
            "external_pdf_metadata_excluded": True,
            "vocabulary_fit_on_training_only_per_lane": True,
        },
        "lane_definitions": {
            "original_provider": "raw provider title/teaser plus deterministic HTML-to-text body",
            "normalized_provider": "normalized table title/teaser/body_text",
            "rendered_provider": "raw provider title/teaser plus structured renderer provider-body text",
        },
        "raw_authority": raw_report,
        "normalized_authority": normalized_report,
        "lanes": lane_reports,
        "elapsed_seconds": time.perf_counter() - started,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    write_json(output_root / "prepare_report.json", report)
    return report
