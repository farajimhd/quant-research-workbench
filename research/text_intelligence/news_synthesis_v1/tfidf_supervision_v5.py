from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections import Counter, defaultdict
from html.parser import HTMLParser
from pathlib import Path, PureWindowsPath
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlparse

import numpy as np

from pipelines.news.benzinga.news_benzinga_normalize import normalize_text, provider_id
from research.mlops.clickhouse import ClickHouseHttpClient, quote_ident, sql_string

from .embedding_supervision import (
    DATASET_VERSION,
    TFIDF_V5_DATASET_VERSION,
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
from .tfidf_supervision_v2 import _word_features
from .tfidf_supervision_v4 import _chunks


DEFAULT_TFIDF_V5_ROOT = (
    Path(r"D:\TradingML\runtimes")
    / "text_intelligence"
    / "news_synthesis_v1"
    / "tfidf_supervision_v5"
)
DEFAULT_RAW_DRIVE_ROOT = Path(r"\\DESKTOP-SAAI85T\Workstation-D")
V5_FIELD_BUDGETS = {
    **{key: value for key, value in V3_FIELD_BUDGETS.items() if key != "supplemental_word"},
    "metadata_word": V3_FIELD_BUDGETS["supplemental_word"],
}
RAW_METADATA_FIELDS = (
    "published",
    "last_updated",
    "author",
    "url",
    "channels",
    "tags",
)


class _OriginalBodyParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        name = tag.lower()
        if name in {"script", "style", "noscript"}:
            self.skip_depth += 1
        elif name in {"p", "div", "li", "tr", "br", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        if name in {"script", "style", "noscript"} and self.skip_depth:
            self.skip_depth -= 1
        elif name in {"p", "div", "li", "tr", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            self.parts.append(data)


def original_body_text(value: Any) -> str:
    """Remove provider HTML markup without applying normalized-table text rules."""

    parser = _OriginalBodyParser()
    parser.feed(str(value or ""))
    parser.close()
    return re.sub(r"[ \t]+", " ", "".join(parser.parts)).strip()


def _list_values(value: Any) -> tuple[str, ...]:
    if isinstance(value, Mapping):
        values: list[str] = []
        for key in sorted(value):
            item = value[key]
            if isinstance(item, (str, int, float, bool)) and str(item).strip():
                values.append(str(item).strip())
        return tuple(values)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    text = str(value or "").strip()
    return (text,) if text else ()


def original_feature_document(
    payload: Mapping[str, Any],
    *,
    ticker: str,
) -> tuple[dict[str, str], str, Counter[str]]:
    raw_tickers = tuple(value.upper() for value in _list_values(payload.get("tickers")))
    tickers = tuple(dict.fromkeys(raw_tickers))
    channels = _list_values(payload.get("channels"))
    tags = _list_values(payload.get("tags"))
    url = str(payload.get("url") or "").strip()
    author = str(payload.get("author") or "").strip()
    fields = {
        "provider": "benzinga",
        "ticker": ticker,
        "published_at_utc": str(payload.get("published") or ""),
        "title": str(payload.get("title") or ""),
        "teaser": str(payload.get("teaser") or ""),
        "channels": ",".join(channels),
        "tags": ",".join(tags),
        "body": original_body_text(payload.get("body")),
        "external": "",
        "pdf": "",
    }
    metadata_text = "\n".join(
        value
        for value in (
            "provider benzinga",
            f"published {payload.get('published') or ''}",
            f"last updated {payload.get('last_updated') or ''}",
            f"author {author}",
            f"domain {urlparse(url).netloc.lower()}",
            f"channels {' '.join(channels)}",
            f"tags {' '.join(tags)}",
        )
        if value.strip()
    )
    structured: Counter[str] = Counter()
    ticker_bucket = "0" if not tickers else "1" if len(tickers) == 1 else "2_to_4" if len(tickers) <= 4 else "5_plus"
    structured[f"structural|metadata:ticker_count_{ticker_bucket}"] = 1
    if ticker.upper() in tickers:
        structured["structural|metadata:target_in_provider_tickers"] = 1
    for name, present in (
        ("author", author),
        ("url", url),
        ("channels", channels),
        ("tags", tags),
        ("teaser", fields["teaser"]),
        ("body", fields["body"]),
    ):
        if present:
            structured[f"structural|metadata:has_{name}"] = 1
    return fields, metadata_text, structured


def _raw_artifact_hash(raw_bytes: bytes) -> str:
    return hashlib.blake2b(raw_bytes, digest_size=16).hexdigest()


def _payload_hash_method(
    payload: Mapping[str, Any],
    *,
    retained_hash: str,
    raw_bytes: bytes,
) -> str | None:
    candidates = (
        ("exact_utf8_artifact_bytes", _raw_artifact_hash(raw_bytes)),
        (
            "canonical_json_ascii_escaped",
            _raw_artifact_hash(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")),
        ),
        (
            "canonical_json_utf8",
            _raw_artifact_hash(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
            ),
        ),
    )
    return next((name for name, value in candidates if value == retained_hash), None)


def resolve_raw_artifact_path(value: str, *, raw_drive_root: Path) -> Path:
    path = PureWindowsPath(value)
    if path.drive.upper() == "D:":
        return raw_drive_root.joinpath(*path.parts[1:])
    return Path(value)


def iter_original_source_authority(
    client: ClickHouseHttpClient,
    source_ids: Sequence[str],
    *,
    database: str = "q_live",
    source_batch_size: int = 500,
) -> Iterable[dict[str, Any]]:
    db = quote_ident(database)
    for batch in _chunks(tuple(source_ids), source_batch_size):
        values = ", ".join(sql_string(source_id) for source_id in batch)
        sql = f"""
SELECT
    canonical_news_id AS source_id,
    provider_article_id,
    toString(published_at_utc) AS published_at_utc,
    published_raw,
    title AS retained_title,
    raw_artifact_path,
    raw_payload_hash
FROM {db}.benzinga_news_normalized_v1 FINAL
WHERE canonical_news_id IN ({values})
ORDER BY source_id
FORMAT JSONEachRow
"""
        for line in client.execute(sql).splitlines():
            if line.strip():
                yield json.loads(line)


def _load_original_documents(
    client: ClickHouseHttpClient,
    source_ids: Sequence[str],
    *,
    database: str,
    source_batch_size: int,
    raw_drive_root: Path,
    allow_revised_original_artifacts: bool,
) -> tuple[
    dict[tuple[str, str], tuple[dict[str, str], str, Counter[str]]],
    dict[str, Mapping[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
]:
    documents: dict[tuple[str, str], tuple[dict[str, str], str, Counter[str]]] = {}
    payloads: dict[str, Mapping[str, Any]] = {}
    authority_rows: list[dict[str, Any]] = []
    hash_methods: Counter[str] = Counter()
    for row in iter_original_source_authority(
        client, source_ids, database=database, source_batch_size=source_batch_size
    ):
        source_id = str(row["source_id"])
        declared_path = str(row.get("raw_artifact_path") or "")
        retained_hash = str(row.get("raw_payload_hash") or "")
        if not declared_path or not retained_hash:
            raise RuntimeError(f"Missing original artifact authority: {source_id}")
        resolved_path = resolve_raw_artifact_path(declared_path, raw_drive_root=raw_drive_root)
        if not resolved_path.is_file():
            raise RuntimeError(f"Original provider artifact is unavailable: {source_id} {resolved_path}")
        raw_bytes = resolved_path.read_bytes()
        payload = json.loads(raw_bytes.decode("utf-8"))
        if not isinstance(payload, Mapping):
            raise RuntimeError(f"Original provider payload is not an object: {source_id}")
        if provider_id(dict(payload)) != str(row.get("provider_article_id") or ""):
            raise RuntimeError(f"Original provider article identity mismatch: {source_id}")
        title = str(payload.get("title") or "")
        if not title.strip():
            raise RuntimeError(f"Original provider title is empty: {source_id}")
        method = _payload_hash_method(payload, retained_hash=retained_hash, raw_bytes=raw_bytes)
        if method is None:
            same_published = str(payload.get("published") or "") == str(row.get("published_raw") or "")
            same_title = normalize_text(title) == str(row.get("retained_title") or "")
            if not allow_revised_original_artifacts or not same_published or not same_title:
                raise RuntimeError(f"Original provider payload hash mismatch: {source_id}")
            method = "current_original_artifact_identity_verified_hash_drift"
        raw_tickers = tuple(value.upper() for value in _list_values(payload.get("tickers")))
        tickers = tuple(dict.fromkeys(raw_tickers))
        if not tickers:
            raise RuntimeError(f"Original provider ticker metadata is empty: {source_id}")
        payloads[source_id] = payload
        for ticker in tickers:
            key = (source_id, ticker)
            if key in documents:
                raise RuntimeError(f"Duplicate original source/ticker row: {key}")
            documents[key] = original_feature_document(payload, ticker=ticker)
        byte_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        body_html = str(payload.get("body") or "")
        body_text = original_body_text(body_html)
        authority_rows.append(
            {
                "source_id": source_id,
                "provider_article_id": str(row.get("provider_article_id") or ""),
                "published_at_utc": str(row.get("published_at_utc") or ""),
                "declared_raw_artifact_path": declared_path,
                "resolved_raw_artifact_path": str(resolved_path),
                "retained_raw_payload_hash": retained_hash,
                "current_raw_payload_hash": _raw_artifact_hash(raw_bytes),
                "raw_artifact_sha256": byte_sha256,
                "hash_verification_method": method,
                "raw_bytes": len(raw_bytes),
                "raw_title_chars": len(title),
                "raw_teaser_chars": len(str(payload.get("teaser") or "")),
                "raw_body_html_chars": len(body_html),
                "parsed_body_text_chars": len(body_text),
                "ticker_count": len(tickers),
                "raw_ticker_count": len(raw_tickers),
                "duplicate_ticker_values_removed": len(raw_tickers) - len(tickers),
            }
        )
        hash_methods[method] += 1
    missing = sorted(set(source_ids) - set(payloads))
    if missing:
        raise RuntimeError(f"Original artifact coverage missing {len(missing)} rows; first={missing[:5]}")
    report = {
        "database": database,
        "authority_table": "benzinga_news_normalized_v1",
        "input_authority": "retained_original_provider_json",
        "raw_drive_root": str(raw_drive_root),
        "requested_sources": len(source_ids),
        "covered_sources": len(payloads),
        "source_ticker_documents": len(documents),
        "multi_ticker_sources": sum(row["ticker_count"] > 1 for row in authority_rows),
        "sources_with_duplicate_ticker_values": sum(
            row["duplicate_ticker_values_removed"] > 0 for row in authority_rows
        ),
        "hash_verification_methods": dict(sorted(hash_methods.items())),
        "revised_original_artifacts_allowed": allow_revised_original_artifacts,
        "revised_original_artifact_count": hash_methods[
            "current_original_artifact_identity_verified_hash_drift"
        ],
        "normalized_text_fields_read": False,
        "tokenizer_dependency": False,
        "token_ids_read": False,
        "external_or_pdf_enrichment_used": False,
        "html_handling": "deterministic_markup_removal_without_normalized_table_text",
    }
    return documents, payloads, authority_rows, report


def tfidf_v5_feature_counts(
    fields: Mapping[str, str],
    metadata_text: str,
    structured_metadata: Counter[str],
    *,
    ticker: str,
    aliases: Sequence[str],
) -> Counter[str]:
    result = tfidf_v3_feature_counts_from_fields(fields, ticker=ticker, aliases=aliases)
    result.update(_word_features("metadata_word", metadata_text))
    result.update(structured_metadata)
    return result


def fit_v5_vocabulary(
    documents: Sequence[tuple[str, Mapping[str, str], str, Counter[str], Sequence[str]]],
    *,
    min_document_frequency: int = 3,
    budgets: Mapping[str, int] = V5_FIELD_BUDGETS,
) -> tuple[tuple[str, ...], np.ndarray, dict[str, Any]]:
    if not documents:
        raise ValueError("Training documents are empty")
    document_frequency: dict[str, Counter[str]] = defaultdict(Counter)
    for ticker, fields, metadata_text, structured, aliases in documents:
        for term in tfidf_v5_feature_counts(
            fields, metadata_text, structured, ticker=ticker, aliases=aliases
        ):
            document_frequency[term.split("|", 1)[0]][term] += 1
    selected: list[tuple[str, int]] = []
    family_report: dict[str, Any] = {}
    for family, budget in budgets.items():
        minimum = 1 if family in {"structural", "economic_relation"} else min_document_frequency
        candidates = [item for item in document_frequency[family].items() if item[1] >= minimum]
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
        "original_provider_title_teaser_body": True,
        "original_provider_metadata": list(RAW_METADATA_FIELDS),
        "generic_metadata_features": True,
        "source_change_from_v4": "normalized_fields_to_original_provider_json",
        "model_change_from_v4": False,
        "gold_or_prediction_features": False,
    }


def transform_v5(
    document: tuple[Mapping[str, str], str, Counter[str]],
    *,
    ticker: str,
    aliases: Sequence[str],
    vocabulary: Mapping[str, int],
    idf: np.ndarray,
) -> np.ndarray:
    fields, metadata_text, structured = document
    counts = tfidf_v5_feature_counts(
        fields, metadata_text, structured, ticker=ticker, aliases=aliases
    )
    vector = np.zeros(len(vocabulary), dtype=np.float32)
    for term, count in counts.items():
        index = vocabulary.get(term)
        if index is not None:
            vector[index] = (1.0 + math.log(count)) * float(idf[index])
    return l2_normalize(vector)


def prepare_tfidf_v5_dataset(
    *,
    source_data_root: Path,
    output_root: Path,
    client: ClickHouseHttpClient,
    raw_drive_root: Path = DEFAULT_RAW_DRIVE_ROOT,
    source_database: str = "q_live",
    identity_database: str = "q_live",
    min_document_frequency: int = 3,
    source_batch_size: int = 500,
    allow_revised_original_artifacts: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    source_data_root = assert_runtime_path(source_data_root)
    output_root = assert_runtime_path(output_root)
    validate_prepared_dataset(source_data_root)
    if output_root.exists():
        raise RuntimeError(f"Refusing to overwrite TF-IDF V5 dataset: {output_root}")
    source_manifest = json.loads((source_data_root / "manifest.json").read_text(encoding="utf-8"))
    source_representation = str((source_manifest.get("representation") or {}).get("kind") or "qwen")
    if source_manifest.get("version") != DATASET_VERSION and source_representation != "qwen":
        raise RuntimeError("TF-IDF V5 requires the frozen supervision split/label authority")

    article_metadata = read_jsonl(source_data_root / "article_metadata.jsonl")
    issuer_metadata = read_jsonl(source_data_root / "issuer_metadata.jsonl")
    source_ids = sorted({str(row["source_id"]) for row in article_metadata})
    documents, payloads, authority_rows, source_report = _load_original_documents(
        client,
        source_ids,
        database=source_database,
        source_batch_size=source_batch_size,
        raw_drive_root=raw_drive_root,
        allow_revised_original_artifacts=allow_revised_original_artifacts,
    )
    article_document_keys = set(documents)
    frozen_issuer_views = 0
    for row in issuer_metadata:
        source_id = str(row["source_id"])
        ticker = str(row["ticker"]).rsplit(":", 1)[-1].upper()
        if match_issuer_embedding(source_id, ticker, documents)[0] is not None:
            continue
        documents[(source_id, ticker)] = original_feature_document(payloads[source_id], ticker=ticker)
        frozen_issuer_views += 1

    identity_index = load_identity_index(client, identity_database)
    identity_rows: list[dict[str, Any]] = []
    aliases_by_key: dict[tuple[str, str], tuple[str, ...]] = {}
    published_by_source = {row["source_id"]: row["published_at_utc"] for row in authority_rows}
    for source_id, ticker in sorted(documents):
        published = str(published_by_source[source_id])
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

    training_sources = {str(row["source_id"]) for row in article_metadata if row["split"] == "train"}
    training_documents = [
        (ticker, *document, aliases_by_key[(source_id, ticker)])
        for (source_id, ticker), document in documents.items()
        if source_id in training_sources
    ]
    terms, idf, feature_report = fit_v5_vocabulary(
        training_documents, min_document_frequency=min_document_frequency
    )
    vocabulary = {term: index for index, term in enumerate(terms)}
    vectors = {
        key: transform_v5(
            document,
            ticker=key[1],
            aliases=aliases_by_key[key],
            vocabulary=vocabulary,
            idf=idf,
        )
        for key, document in documents.items()
    }
    vectors_by_source: dict[str, list[np.ndarray]] = defaultdict(list)
    for key, vector in vectors.items():
        if key in article_document_keys:
            vectors_by_source[key[0]].append(vector)
    article_vectors = np.stack(
        [l2_normalize(np.mean(vectors_by_source[row["source_id"]], axis=0)) for row in article_metadata]
    ).astype(np.float32)
    issuer_vectors: list[np.ndarray] = []
    issuer_match_counts: Counter[str] = Counter()
    for row in issuer_metadata:
        vector, status = match_issuer_embedding(row["source_id"], row["ticker"], vectors)
        if vector is None:
            raise RuntimeError(f"Missing TF-IDF V5 issuer vector: {row['unit_id']}")
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
    write_jsonl(output_root / "source_text_authority.jsonl", authority_rows)
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
        "version": TFIDF_V5_DATASET_VERSION,
        "status": "complete",
        "representation": {"kind": "tfidf_v5", **feature_report},
        "model_change_from_v4": False,
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
