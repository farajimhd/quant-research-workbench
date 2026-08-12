from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from .embedding_supervision import (
    TFIDF_V8_DATASET_VERSION,
    TFIDF_V9_DATASET_VERSION,
    assert_runtime_path,
    canonical_json_sha256,
    dataset_file_manifest,
    read_jsonl,
    save_array,
    validate_prepared_dataset,
    write_json,
    write_jsonl,
)
from .tfidf_supervision_v2 import _char_features, _structural_features, _word_features
from .tfidf_supervision_v3 import (
    anonymize_issuer_mentions,
    economic_relation_features,
    issuer_local_clauses,
)
from .tfidf_supervision_v4 import _load_canonical_documents
from .tfidf_supervision_v5 import (
    DEFAULT_RAW_DRIVE_ROOT,
    _load_original_documents,
    original_body_text,
)
from .tfidf_supervision_v7 import (
    _provider_only_normalized,
    _rename_family,
    fit_v7_vocabulary_from_document_frequency,
    invariant_metadata_features,
)
from .tfidf_supervision_v8 import (
    V8_FIELD_BUDGETS,
    V8_VIEW_PREFIXES,
    _CURRENTNESS_PATTERNS,
    _EVENT_PATTERNS,
    _NEGATIVE,
    _ORIGIN_PATTERNS,
    _POSITIVE,
    _mask_clause,
    _normalized_aliases,
    tfidf_v8_feature_counts,
    v8_view_indexes,
)
from .sparse_features import CSRFeatureMatrix, csr_from_rows, save_csr_npz


DEFAULT_TFIDF_V9_ROOT = (
    Path(r"D:\TradingML\runtimes")
    / "text_intelligence"
    / "news_synthesis_v1"
    / "tfidf_supervision_v9"
)
V9_FIELD_BUDGETS = {
    **V8_FIELD_BUDGETS,
    "provider_title_word": 1216,
    "provider_body_word": 2048,
    "external_local_word": 448,
    "pdf_local_word": 448,
    "metadata_word": 320,
    "target_clause_word": 896,
    "target_clause_char": 384,
    "cross_view_agreement": 192,
    "predicate_role": 192,
    "state_transition": 192,
    "numeric_magnitude": 192,
}
if sum(V9_FIELD_BUDGETS.values()) != sum(V8_FIELD_BUDGETS.values()):
    raise AssertionError("V9 must retain the V8 total feature-budget ceiling")

V9_VIEW_PREFIXES = {
    **V8_VIEW_PREFIXES,
    "provider": (
        "provider_",
        "target_clause_",
        "predicate_role",
        "state_transition",
        "numeric_magnitude",
        "cross_view_agreement",
    ),
}

_PASSIVE = re.compile(
    r"<issuer>\s+(?:was|is|has been|had been)\s+(?:acquired|bought|sued|fined|penalized|approved|rejected|appointed)",
    re.I,
)
_RECEIVER = re.compile(
    r"<issuer>\s+(?:received|obtained|won|secured|was granted|was denied)", re.I
)
_ACTIVE = re.compile(
    r"<issuer>\s+(?:announced|reported|issued|completed|signed|acquired|bought|sold|launched|filed|appointed|approved|rejected|sued)",
    re.I,
)
_TARGET_AFTER_ACTION = re.compile(
    r"(?:acquired|bought|sued|fined|penalized|approved|rejected|appointed)\s+<issuer>",
    re.I,
)


@dataclass(frozen=True, slots=True)
class ClauseIR:
    clause_index: int
    raw: str
    masked: str
    role: str
    currentness: str
    events: tuple[str, ...]
    direction: str
    origin: str
    numeric_tokens: tuple[str, ...]
    state_tokens: tuple[str, ...]


def _negated(pattern: re.Pattern[str], text: str) -> bool:
    for match in pattern.finditer(text):
        prefix = text[max(0, match.start() - 28) : match.start()]
        if not re.search(r"\b(?:not|never|no longer|failed to|unable to|without)\b", prefix, re.I):
            return False
    return bool(pattern.search(text))


def _direction(text: str) -> str:
    positive = bool(_POSITIVE.search(text)) and not _negated(_POSITIVE, text)
    negative = bool(_NEGATIVE.search(text)) and not _negated(_NEGATIVE, text)
    return "mixed" if positive and negative else "positive" if positive else "negative" if negative else "none"


def _role(masked: str) -> str:
    if _PASSIVE.search(masked) or _TARGET_AFTER_ACTION.search(masked):
        return "affected"
    if _RECEIVER.search(masked):
        return "affected_receiver"
    if _ACTIVE.search(masked):
        return "actor"
    return "mentioned_or_anaphoric"


def _bucket(value: float, boundaries: Sequence[tuple[float, str]], final: str) -> str:
    absolute = abs(value)
    for boundary, label in boundaries:
        if absolute < boundary:
            return label
    return final


def _numeric_tokens(text: str) -> tuple[str, ...]:
    tokens: set[str] = set()
    for match in re.finditer(
        r"([-+]?\d[\d,]*(?:\.\d+)?)\s*(%|percent\b|bps\b|basis points?\b)",
        text,
        re.I,
    ):
        value = float(match.group(1).replace(",", ""))
        unit = match.group(2).lower()
        family = "bps" if unit.startswith("bp") or unit.startswith("basis") else "percent"
        tokens.add(
            f"{family}:{_bucket(value, ((1, 'lt_1'), (5, '1_to_5'), (10, '5_to_10'), (25, '10_to_25')), 'ge_25')}"
        )
    for match in re.finditer(
        r"([$€£])\s*([-+]?\d[\d,]*(?:\.\d+)?)\s*(thousand|million|billion|k|m|bn|b)?",
        text,
        re.I,
    ):
        value = float(match.group(2).replace(",", ""))
        unit = (match.group(3) or "").lower()
        multiplier = 1e9 if unit in {"b", "bn", "billion"} else 1e6 if unit in {"m", "million"} else 1e3 if unit in {"k", "thousand"} else 1
        amount = abs(value * multiplier)
        tokens.add(
            "currency:lt_1m" if amount < 1e6 else "currency:1m_to_100m" if amount < 1e8 else "currency:100m_to_1b" if amount < 1e9 else "currency:ge_1b"
        )
    if re.search(r"\b(?:versus|vs\.?|compared with|above|below|beat|miss)\b", text, re.I):
        tokens.add("comparison:present")
    return tuple(sorted(tokens))


def _state_tokens(text: str, *, currentness: str, direction: str) -> tuple[str, ...]:
    tokens = {f"state:{currentness}:{direction}"}
    lowered = text.lower()
    if re.search(r"\b(?:previously|last (?:year|quarter|month)|formerly)\b", lowered) and re.search(r"\b(?:now|currently|today|has now)\b", lowered):
        tokens.add("transition:historical_to_current")
    if re.search(r"\b(?:failed|missed|declined|loss|noncompliance)\b", lowered) and re.search(r"\b(?:now|subsequently|later).{0,45}(?:approved|completed|regained|profit|increased)\b", lowered):
        tokens.add("transition:adverse_to_recovery")
    if re.search(r"\b(?:notice|deficiency|noncompliance|loss|decline)\b", lowered) and re.search(r"\b(?:may|could|plans? to|expects? to|deadline|extension)\b", lowered):
        tokens.add("transition:current_adverse_to_possible_cure")
    if re.search(r"\b(?:proposed|planned|intended)\b", lowered) and re.search(r"\b(?:completed|closed|approved|received)\b", lowered):
        tokens.add("transition:proposal_to_completion")
    if re.search(r"\b(?:not|never|failed to|unable to|without)\b", lowered):
        tokens.add("scope:negated_or_unachieved")
    return tuple(sorted(tokens))


def analyze_clause_ir(text: str, *, aliases: Sequence[str]) -> tuple[ClauseIR, ...]:
    aliases = _normalized_aliases(aliases)
    clauses = issuer_local_clauses(text, aliases=aliases)
    result: list[ClauseIR] = []
    for index, clause in enumerate(clauses):
        masked = _mask_clause(clause, aliases)
        currentness = next(
            (name for name, pattern in _CURRENTNESS_PATTERNS if pattern.search(clause)),
            "unspecified",
        )
        events = tuple(name for name, pattern in _EVENT_PATTERNS if pattern.search(clause)) or ("other",)
        direction = _direction(clause)
        result.append(
            ClauseIR(
                clause_index=index,
                raw=clause,
                masked=masked,
                role=_role(masked),
                currentness=currentness,
                events=events,
                direction=direction,
                origin=next((name for name, pattern in _ORIGIN_PATTERNS if pattern.search(clause)), "editorial_or_unknown"),
                numeric_tokens=_numeric_tokens(clause),
                state_tokens=_state_tokens(clause, currentness=currentness, direction=direction),
            )
        )
    return tuple(result)


def _ir_features(ir: Sequence[ClauseIR]) -> Counter[str]:
    result: Counter[str] = Counter()
    masked = " ".join(row.masked for row in ir)
    result.update(_word_features("target_clause_word", masked))
    result.update(_char_features("target_clause_char", masked))
    for row in ir:
        result[f"target_clause_structure|currentness:{row.currentness}"] += 1
        result[f"target_clause_structure|direction:{row.direction}"] += 1
        result[f"target_clause_structure|origin:{row.origin}"] += 1
        result[f"target_clause_structure|role:{row.role}"] += 1
        result[f"predicate_role|{row.role}"] += 1
        for event in row.events:
            result[f"target_clause_structure|event:{event}"] += 1
            result[f"target_clause_interaction|event:{event}|currentness:{row.currentness}|direction:{row.direction}"] += 1
            result[f"target_clause_interaction|event:{event}|origin:{row.origin}"] += 1
            result[f"target_clause_interaction|event:{event}|role:{row.role}"] += 1
            result[f"predicate_role|event:{event}|role:{row.role}"] += 1
        for token in row.numeric_tokens:
            result[f"numeric_magnitude|{token}"] += 1
            result[f"numeric_magnitude|{token}|direction:{row.direction}"] += 1
        for token in row.state_tokens:
            result[f"state_transition|{token}"] += 1
    for left, right in zip(ir, ir[1:]):
        result[f"state_transition|sequence:{left.currentness}:{left.direction}->{right.currentness}:{right.direction}"] += 1
    return result


def _cross_view_features(
    provider: Sequence[ClauseIR], normalized: Sequence[ClauseIR], enrichment: Sequence[ClauseIR]
) -> Counter[str]:
    result: Counter[str] = Counter()
    views = {"provider": provider, "normalized": normalized, "enrichment": enrichment}
    event_sets = {name: {event for row in rows for event in row.events} for name, rows in views.items()}
    direction_sets = {name: {row.direction for row in rows if row.direction != "none"} for name, rows in views.items()}
    for left, right in (("provider", "normalized"), ("provider", "enrichment"), ("normalized", "enrichment")):
        for event in sorted(event_sets[left] & event_sets[right]):
            result[f"cross_view_agreement|{left}+{right}|event:{event}"] = 1
        if direction_sets[left] and direction_sets[right]:
            relation = "agree" if direction_sets[left] == direction_sets[right] else "conflict"
            result[f"cross_view_agreement|{left}+{right}|direction:{relation}"] = 1
    for event in sorted(event_sets["provider"] - event_sets["normalized"] - event_sets["enrichment"]):
        result[f"cross_view_agreement|provider_only|event:{event}"] = 1
    for event in sorted(event_sets["enrichment"] - event_sets["provider"]):
        result[f"cross_view_agreement|enrichment_only|event:{event}"] = 1
    return result


def tfidf_v9_feature_counts(
    *,
    original_fields: Mapping[str, str],
    normalized_fields: Mapping[str, str],
    metadata_text: str,
    metadata_structural: Counter[str],
    ticker: str,
    aliases: Sequence[str],
) -> Counter[str]:
    provider = {
        name: anonymize_issuer_mentions(
            str(original_fields.get(name) or ""), aliases=aliases
        )
        for name in ("title", "teaser", "body")
    }
    provider_text = "\n".join(str(original_fields.get(name) or "") for name in ("title", "teaser", "body"))
    normalized_provider = _provider_only_normalized(normalized_fields)
    normalized_text = "\n".join(str(normalized_provider.get(name) or "") for name in ("title", "teaser", "body"))
    external_text = str(normalized_fields.get("external") or "")
    pdf_text = str(normalized_fields.get("pdf") or "")
    provider_ir = analyze_clause_ir(provider_text, aliases=aliases)
    normalized_ir = analyze_clause_ir(normalized_text, aliases=aliases)
    external_ir = analyze_clause_ir(external_text, aliases=aliases)
    pdf_ir = analyze_clause_ir(pdf_text, aliases=aliases)
    enrichment_ir = (*external_ir, *pdf_ir)
    provider_local = " ".join(row.masked for row in provider_ir)
    external_local = " ".join(row.masked for row in external_ir)
    pdf_local = " ".join(row.masked for row in pdf_ir)
    result: Counter[str] = Counter()
    result.update(_word_features("provider_title_word", provider["title"]))
    result.update(_word_features("provider_teaser_word", provider["teaser"]))
    result.update(_word_features("provider_body_word", provider["body"]))
    result.update(_char_features("provider_title_char", provider["title"]))
    result.update(_char_features("provider_teaser_char", provider["teaser"]))
    result.update(_word_features("provider_local_word", provider_local))
    result.update(_char_features("provider_local_char", provider_local))
    result.update(_rename_family(economic_relation_features(provider_local), "provider_economic"))
    result.update(_rename_family(_structural_features(normalized_provider, ticker), "normalized_structural"))
    result.update(_rename_family(economic_relation_features(normalized_text), "normalized_economic"))
    result.update(_word_features("external_local_word", external_local))
    result.update(_word_features("pdf_local_word", pdf_local))
    result.update(_rename_family(economic_relation_features("\n".join((external_local, pdf_local))), "enrichment_economic"))
    result.update(_word_features("metadata_word", metadata_text))
    result.update(metadata_structural)
    result.update(_ir_features(provider_ir))
    result.update(_word_features("enrichment_target_clause_word", " ".join(row.masked for row in enrichment_ir)))
    result.update(_cross_view_features(provider_ir, normalized_ir, enrichment_ir))
    return result


def v9_view_indexes(vocabulary: Mapping[str, int]) -> dict[str, np.ndarray]:
    terms = sorted(vocabulary, key=vocabulary.get)
    return {
        view: np.asarray(
            [index for index, term in enumerate(terms) if term.split("|", 1)[0].startswith(prefixes)],
            dtype=np.int64,
        )
        for view, prefixes in V9_VIEW_PREFIXES.items()
    }


def _sparse_row(
    counts: Mapping[str, int], *, vocabulary: Mapping[str, int], idf: np.ndarray, index_view: Mapping[int, str]
) -> tuple[np.ndarray, np.ndarray]:
    indexes: list[int] = []
    values: list[float] = []
    by_view: dict[str, float] = defaultdict(float)
    for term, count in counts.items():
        index = vocabulary.get(term)
        if index is None:
            continue
        value = (1.0 + math.log(count)) * float(idf[index])
        indexes.append(index)
        values.append(value)
        by_view[index_view[index]] += value * value
    for position, index in enumerate(indexes):
        norm = math.sqrt(by_view[index_view[index]])
        if norm:
            values[position] /= norm
    global_norm = math.sqrt(sum(value * value for value in values))
    if global_norm:
        values = [value / global_norm for value in values]
    order = np.argsort(indexes)
    return (
        np.asarray(indexes, dtype=np.int32)[order],
        np.asarray(values, dtype=np.float32)[order],
    )


def prepare_sparse_feature_dataset(
    *,
    source_data_root: Path,
    output_root: Path,
    client,
    feature_counter: Callable[..., Counter[str]],
    budgets: Mapping[str, int],
    view_indexes: Callable[[Mapping[str, int]], Mapping[str, np.ndarray]],
    dataset_version: str,
    representation_kind: str,
    raw_drive_root: Path = DEFAULT_RAW_DRIVE_ROOT,
    source_database: str = "q_live",
    min_document_frequency: int = 3,
    source_batch_size: int = 500,
) -> dict[str, Any]:
    started = time.perf_counter()
    source_data_root = assert_runtime_path(source_data_root)
    output_root = assert_runtime_path(output_root)
    validate_prepared_dataset(source_data_root)
    if output_root.exists():
        raise RuntimeError(f"Refusing to overwrite feature dataset: {output_root}")
    article_metadata = read_jsonl(source_data_root / "article_metadata.jsonl")
    issuer_metadata = read_jsonl(source_data_root / "issuer_metadata.jsonl")
    source_ids = sorted({str(row["source_id"]) for row in article_metadata})
    identity_rows = read_jsonl(source_data_root / "identity_features.jsonl")
    aliases_by_key = {
        (str(row["source_id"]), str(row["ticker"]).rsplit(":", 1)[-1].upper()): tuple(row["aliases"])
        for row in identity_rows
    }
    _, payloads, raw_authority_rows, raw_report = _load_original_documents(
        client,
        source_ids,
        database=source_database,
        source_batch_size=source_batch_size,
        raw_drive_root=raw_drive_root,
        allow_revised_original_artifacts=False,
    )
    normalized, normalized_authority_rows, normalized_report = _load_canonical_documents(
        client, source_ids, database=source_database, source_batch_size=source_batch_size
    )
    article_document_keys = set(normalized)
    fixed_keys = set(normalized) | set(aliases_by_key)
    extraction_started = time.perf_counter()
    feature_counts: dict[tuple[str, str], Counter[str]] = {}
    for source_id, ticker in sorted(fixed_keys):
        source_keys = sorted(key for key in normalized if key[0] == source_id)
        document = normalized.get((source_id, ticker), normalized[source_keys[0]])
        payload = payloads[source_id]
        metadata_text, metadata_structural = invariant_metadata_features(
            payload,
            target_ticker=ticker,
            has_external=bool(document.get("external")),
            has_pdf=bool(document.get("pdf")),
        )
        feature_counts[(source_id, ticker)] = feature_counter(
            original_fields={
                "title": str(payload.get("title") or ""),
                "teaser": str(payload.get("teaser") or ""),
                "body": original_body_text(payload.get("body")),
            },
            normalized_fields=document,
            metadata_text=metadata_text,
            metadata_structural=metadata_structural,
            ticker=ticker,
            aliases=aliases_by_key.get((source_id, ticker), (ticker,)),
        )
    extraction_seconds = time.perf_counter() - extraction_started
    training_sources = {str(row["source_id"]) for row in article_metadata if row["split"] == "train"}
    document_frequency: dict[str, Counter[str]] = defaultdict(Counter)
    training_documents = 0
    for (source_id, _), counts in feature_counts.items():
        if source_id not in training_sources:
            continue
        training_documents += 1
        for term in counts:
            document_frequency[term.split("|", 1)[0]][term] += 1
    terms, idf, feature_report = fit_v7_vocabulary_from_document_frequency(
        document_frequency,
        training_document_count=training_documents,
        min_document_frequency=min_document_frequency,
        budgets=budgets,
    )
    vocabulary = {term: index for index, term in enumerate(terms)}
    views = view_indexes(vocabulary)
    index_view = {int(index): view for view, indexes in views.items() for index in indexes}
    if len(index_view) != len(vocabulary):
        raise RuntimeError("Every V9 vocabulary feature must belong to exactly one view")
    keys = sorted(feature_counts)
    key_index = {key: index for index, key in enumerate(keys)}
    sparse_rows = [
        _sparse_row(
            feature_counts[key],
            vocabulary=vocabulary,
            idf=idf,
            index_view=index_view,
        )
        for key in keys
    ]
    matrix = csr_from_rows(sparse_rows, columns=len(vocabulary))
    indexes_by_source: dict[str, list[int]] = defaultdict(list)
    for key, index in key_index.items():
        if key in article_document_keys:
            indexes_by_source[key[0]].append(index)
    article_rows: list[tuple[np.ndarray, np.ndarray]] = []
    for row in article_metadata:
        indexes = indexes_by_source[str(row["source_id"])]
        accumulated: dict[int, float] = defaultdict(float)
        for index in indexes:
            sparse = matrix.row(index)
            for column, value in zip(sparse.indices, sparse.values, strict=True):
                accumulated[int(column)] += float(value) / len(indexes)
        columns = np.asarray(sorted(accumulated), dtype=np.int32)
        values = np.asarray([accumulated[int(column)] for column in columns], dtype=np.float32)
        norm = float(np.linalg.norm(values))
        if norm:
            values /= norm
        article_rows.append((columns, values))
    issuer_rows: list[tuple[np.ndarray, np.ndarray]] = []
    issuer_matches: Counter[str] = Counter()
    for row in issuer_metadata:
        source_id = str(row["source_id"])
        candidates = (
            str(row["ticker"]).upper(),
            str(row["ticker"]).rsplit(":", 1)[-1].upper(),
            str(row["ticker"]).replace(".", "-").upper(),
        )
        found = next(((source_id, ticker) for ticker in candidates if (source_id, ticker) in key_index), None)
        if found is None:
            raise RuntimeError(f"Missing sparse issuer vector: {row['unit_id']}")
        sparse = matrix.row(key_index[found])
        issuer_rows.append((sparse.indices, sparse.values))
        issuer_matches["exact_or_normalized_ticker"] += 1
    article_matrix = csr_from_rows(article_rows, columns=len(vocabulary))
    issuer_matrix = csr_from_rows(issuer_rows, columns=len(vocabulary))
    output_root.mkdir(parents=True)
    save_csr_npz(output_root / "article_embeddings.npz", article_matrix)
    save_csr_npz(output_root / "issuer_embeddings.npz", issuer_matrix)
    for name in ("article_eligibility.npy", "issuer_eligibility.npy", "issuer_sentiment.npy", "issuer_concepts.npy"):
        save_array(output_root / name, np.load(source_data_root / name))
    write_jsonl(output_root / "article_metadata.jsonl", article_metadata)
    write_jsonl(output_root / "issuer_metadata.jsonl", issuer_metadata)
    write_jsonl(output_root / "identity_features.jsonl", identity_rows)
    write_jsonl(output_root / "source_text_authority.jsonl", raw_authority_rows)
    write_jsonl(output_root / "normalized_text_authority.jsonl", normalized_authority_rows)
    write_json(output_root / "label_contract.json", json.loads((source_data_root / "label_contract.json").read_text(encoding="utf-8")))
    write_json(output_root / "vocabulary.json", {"terms": list(terms), "idf": [float(value) for value in idf]})
    files = (
        "article_embeddings.npz", "article_eligibility.npy", "issuer_embeddings.npz",
        "issuer_eligibility.npy", "issuer_sentiment.npy", "issuer_concepts.npy",
        "article_metadata.jsonl", "issuer_metadata.jsonl", "identity_features.jsonl",
        "source_text_authority.jsonl", "normalized_text_authority.jsonl",
        "label_contract.json", "vocabulary.json",
    )
    manifest = {
        "version": dataset_version,
        "status": "complete",
        "representation": {
            "kind": representation_kind,
            "vector_storage": "csr_npz",
            "sparse_to_dense_boundary": "minibatch_only",
            **feature_report,
        },
        "population": {"articles": len(article_metadata), "issuer_units": len(issuer_metadata)},
        "split": {
            "authority": "frozen_v7_exact_authority_split",
            "train_articles": len(training_sources),
            "validation_articles": len(article_metadata) - len(training_sources),
        },
        "source_authority": raw_report,
        "normalized_authority": normalized_report,
        "issuer_vector_matching": dict(issuer_matches),
        "performance": {
            "feature_documents": len(feature_counts),
            "feature_extraction_seconds": extraction_seconds,
            "feature_documents_per_second": len(feature_counts) / extraction_seconds,
            "article_sparse_bytes": (output_root / "article_embeddings.npz").stat().st_size,
            "issuer_sparse_bytes": (output_root / "issuer_embeddings.npz").stat().st_size,
            "dense_float32_equivalent_bytes": int((article_matrix.shape[0] + issuer_matrix.shape[0]) * article_matrix.shape[1] * 4),
        },
        "files": dataset_file_manifest(output_root, files),
        "elapsed_seconds": time.perf_counter() - started,
    }
    manifest["contract_sha256"] = canonical_json_sha256({key: value for key, value in manifest.items() if key != "elapsed_seconds"})
    write_json(output_root / "manifest.json", manifest)
    validation = validate_prepared_dataset(output_root)
    write_json(output_root / "VALIDATION.json", validation)
    return {"manifest": manifest, "validation": validation}


def prepare_v8_sparse_dataset(**kwargs) -> dict[str, Any]:
    return prepare_sparse_feature_dataset(
        feature_counter=tfidf_v8_feature_counts,
        budgets=V8_FIELD_BUDGETS,
        view_indexes=v8_view_indexes,
        dataset_version=TFIDF_V8_DATASET_VERSION,
        representation_kind="tfidf_v8_entity_clause_invariant",
        **kwargs,
    )


def prepare_v9_sparse_dataset(**kwargs) -> dict[str, Any]:
    return prepare_sparse_feature_dataset(
        feature_counter=tfidf_v9_feature_counts,
        budgets=V9_FIELD_BUDGETS,
        view_indexes=v9_view_indexes,
        dataset_version=TFIDF_V9_DATASET_VERSION,
        representation_kind="tfidf_v9_clause_ir_sparse",
        **kwargs,
    )
