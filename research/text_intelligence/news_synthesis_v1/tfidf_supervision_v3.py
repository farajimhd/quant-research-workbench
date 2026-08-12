from __future__ import annotations

import json
import math
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from research.mlops.clickhouse import ClickHouseHttpClient

from .embedding_supervision import (
    DATASET_VERSION,
    DEFAULT_EMBEDDING_MODEL,
    TFIDF_V3_DATASET_VERSION,
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
from .engine import IssuerIdentityIndex
from .storage import load_identity_index
from .tfidf_supervision import (
    DEFAULT_TOKENIZER_MODEL,
    decode_qwen_token_documents,
    iter_qwen_token_rows,
)
from .tfidf_supervision_v2 import (
    FIELD_BUDGETS,
    _char_features,
    _structural_features,
    _word_features,
    parse_qwen_news_document,
)


DEFAULT_TFIDF_V3_ROOT = (
    Path(r"D:\TradingML\runtimes")
    / "text_intelligence"
    / "news_synthesis_v1"
    / "tfidf_supervision_v3"
)
V3_FIELD_BUDGETS = {
    **FIELD_BUDGETS,
    "issuer_clause_word": 1024,
    "issuer_clause_char": 512,
    "economic_relation": 256,
}
CLAUSE_PATTERN = re.compile(r"(?<=[.!?;])\s+|[\r\n]+")
ANAPHORA_PATTERN = re.compile(
    r"^(?:it|its|they|their|the company|the issuer|the group|the business)\b",
    re.I,
)
NUMBER_PATTERN = r"[-+]?\d[\d,]*(?:\.\d+)?"


def point_in_time_aliases(
    identity_index: IssuerIdentityIndex,
    *,
    ticker: str,
    published_at_utc: str,
) -> tuple[str, ...]:
    entities = identity_index.supported_candidates(
        candidates=(ticker,), timestamp=published_at_utc
    )
    if len(entities) != 1:
        return (ticker,)
    aliases = identity_index.mention_terms(entities[0])
    return tuple(dict.fromkeys((ticker, *aliases)))


def issuer_local_clauses(text: str, *, aliases: Sequence[str]) -> tuple[str, ...]:
    normalized_aliases = tuple(
        sorted(
            {
                re.sub(r"[^a-z0-9]+", " ", alias.strip().lower()).strip()
                for alias in aliases
                if len(alias.strip()) >= 2
            },
            key=lambda value: (-len(value), value),
        )
    )
    clauses = [value.strip() for value in CLAUSE_PATTERN.split(text) if value.strip()]
    selected: list[str] = []
    previous_explicit = False
    for clause in clauses:
        normalized = f" {re.sub(r'[^a-z0-9]+', ' ', clause.lower()).strip()} "
        explicit = any(f" {alias} " in normalized for alias in normalized_aliases)
        anaphoric = previous_explicit and bool(ANAPHORA_PATTERN.match(clause))
        if explicit or anaphoric:
            selected.append(clause)
        previous_explicit = explicit
    return tuple(selected)


def anonymize_issuer_mentions(text: str, *, aliases: Sequence[str]) -> str:
    result = text
    for alias in sorted(
        {value.strip() for value in aliases if len(value.strip()) >= 2},
        key=lambda value: (-len(value), value.lower()),
    ):
        result = re.sub(
            rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])",
            " <issuer> ",
            result,
            flags=re.I,
        )
    return re.sub(r"\s+", " ", result).strip()


def _as_float(value: str) -> float | None:
    try:
        return float(value.replace(",", ""))
    except ValueError:
        return None


def _magnitude_bucket(value: float) -> str:
    absolute = abs(value)
    if absolute < 1:
        return "lt_1"
    if absolute < 5:
        return "1_to_5"
    if absolute < 10:
        return "5_to_10"
    if absolute < 25:
        return "10_to_25"
    return "ge_25"


def economic_relation_features(text: str) -> Counter[str]:
    """Extract generic economic relationships without retaining raw values."""

    value = re.sub(r"\s+", " ", text.lower())
    result: Counter[str] = Counter()

    patterns = {
        "comparison:beat": r"\b(?:beat|beats|beating|above|exceed(?:ed|s|ing)?)\b.{0,55}\b(?:estimate|expectation|consensus|forecast)\b|\b(?:estimate|expectation|consensus|forecast)\b.{0,55}\b(?:beat|above|exceed(?:ed|s|ing)?)\b",
        "comparison:miss": r"\b(?:miss|missed|misses|below|fell short of)\b.{0,55}\b(?:estimate|expectation|consensus|forecast)\b|\b(?:estimate|expectation|consensus|forecast)\b.{0,55}\b(?:miss|below|fell short)\b",
        "comparison:in_line": r"\b(?:in line with|matched?|roughly equal to)\b.{0,45}\b(?:estimate|expectation|consensus|forecast)\b",
        "transition:loss_to_profit": r"\b(?:profit|net income|earnings)\b.{0,70}\b(?:prior|previous|year-ago)\b.{0,35}\bloss\b|\bturned?\b.{0,25}\b(?:profit|profitable)\b.{0,45}\b(?:from|versus|vs\.?)\b.{0,25}\bloss\b",
        "transition:profit_to_loss": r"\b(?:loss|net loss)\b.{0,70}\b(?:prior|previous|year-ago)\b.{0,35}\bprofit\b|\bturned?\b.{0,25}\bloss\b.{0,45}\b(?:from|versus|vs\.?)\b.{0,25}\bprofit\b",
        "guidance:raised": r"\b(?:raise[sd]?|increase[sd]?|boost(?:ed|s)?)\b.{0,35}\b(?:guidance|outlook|forecast|target)\b|\b(?:guidance|outlook|forecast|target)\b.{0,35}\b(?:raise[sd]?|increase[sd]?|boost(?:ed|s)?)\b",
        "guidance:lowered": r"\b(?:lower(?:ed|s)?|cut|cuts|reduce[sd]?)\b.{0,35}\b(?:guidance|outlook|forecast|target)\b|\b(?:guidance|outlook|forecast|target)\b.{0,35}\b(?:lower(?:ed|s)?|cut|cuts|reduce[sd]?)\b",
        "guidance:reaffirmed": r"\b(?:reaffirm(?:ed|s)?|maintain(?:ed|s)?|reiterate[sd]?)\b.{0,35}\b(?:guidance|outlook|forecast|target)\b",
        "financing:new_capital": r"\b(?:raised?|secured?|closed|entered into)\b.{0,55}\b(?:financing|offering|credit facility|loan|notes?|capital)\b",
        "financing:debt_increase": rf"\b(?:issued?|borrowed?)\b.{{0,45}}\b(?:debt|notes?|loan)\b|\braised?\b.{{0,20}}(?:[$â‚¬Â£]\s*)?{NUMBER_PATTERN}(?:\s*(?:million|billion|m|b))?.{{0,20}}\b(?:debt|notes?|loan)\b|\b(?:debt|borrowings?)\b.{{0,35}}\b(?:increased?|rose|grew)\b",
        "financing:debt_reduction": r"\b(?:repaid?|redeemed?|retired?|reduced?)\b.{0,45}\b(?:debt|notes?|loan|borrowings?)\b|\b(?:debt|borrowings?)\b.{0,35}\b(?:decreased?|fell|declined)\b",
        "liquidity:runway": r"\b(?:cash|liquidity|capital)\b.{0,55}\b(?:runway|fund operations|into 20\d{2}|through 20\d{2})\b",
        "capital_return:declared": r"\b(?:declared?|announced?|approved?)\b.{0,45}\b(?:dividend|distribution|buyback|repurchase)\b",
        "regulatory:achieved": r"\b(?:approved?|clear(?:ed|ance)|authorized?|accepted?|regained compliance)\b",
        "regulatory:failed": r"\b(?:rejected?|denied|failed|missed)\b.{0,45}\b(?:approval|clearance|authorization|endpoint|compliance)\b",
    }
    for name, pattern in patterns.items():
        count = len(re.findall(pattern, value, flags=re.I))
        if count:
            result[f"economic_relation|{name}"] = min(count, 8)

    for direction, pattern in {
        "increase": rf"\b(?:rose|grew|increased?|gained|improved)\b[^.;]{{0,45}}?({NUMBER_PATTERN})\s*(?:%|percent|bps|basis points?)",
        "decrease": rf"\b(?:fell|declined|decreased?|dropped|contracted)\b[^.;]{{0,45}}?({NUMBER_PATTERN})\s*(?:%|percent|bps|basis points?)",
    }.items():
        for match in re.finditer(pattern, value, flags=re.I):
            amount = _as_float(match.group(1))
            if amount is not None:
                result[f"economic_relation|change:{direction}"] += 1
                result[f"economic_relation|change:{direction}:{_magnitude_bucket(amount)}"] += 1

    actual_estimate = re.compile(
        rf"\b(?:eps|earnings per share|revenue|sales)\b[^.;]{{0,50}}?({NUMBER_PATTERN})"
        rf"[^.;]{{0,35}}?\b(?:versus|vs\.?|compared with)\b[^.;]{{0,20}}?"
        rf"(?:an?\s+)?(?:estimate|expectation|consensus|forecast)[^.;]{{0,15}}?({NUMBER_PATTERN})",
        re.I,
    )
    for match in actual_estimate.finditer(value):
        actual = _as_float(match.group(1))
        estimate = _as_float(match.group(2))
        if actual is None or estimate is None:
            continue
        relation = "above" if actual > estimate else "below" if actual < estimate else "equal"
        result[f"economic_relation|numeric_actual_vs_estimate:{relation}"] += 1
    actual_outcome = re.compile(
        rf"\b(?:eps|earnings per share|revenue|sales)\b[^.;]{{0,50}}?({NUMBER_PATTERN})"
        rf"[^.;]{{0,30}}?\b(beat|above|exceeded|missed|below)\b[^.;]{{0,35}}?"
        rf"\b(?:estimate|expectation|consensus|forecast)\b[^.;]{{0,15}}?({NUMBER_PATTERN})",
        re.I,
    )
    for match in actual_outcome.finditer(value):
        actual = _as_float(match.group(1))
        estimate = _as_float(match.group(3))
        if actual is None or estimate is None:
            continue
        relation = "above" if actual > estimate else "below" if actual < estimate else "equal"
        result[f"economic_relation|numeric_actual_vs_estimate:{relation}"] += 1

    if any(key.endswith(":beat") for key in result) and any(
        key.endswith(":miss") for key in result
    ):
        result["economic_relation|comparison:tradeoff"] = 1
    return result


def tfidf_v3_feature_counts(
    text: str,
    *,
    ticker: str,
    aliases: Sequence[str],
) -> Counter[str]:
    fields = parse_qwen_news_document(text)
    return tfidf_v3_feature_counts_from_fields(fields, ticker=ticker, aliases=aliases)


def tfidf_v3_feature_counts_from_fields(
    fields: Mapping[str, str],
    *,
    ticker: str,
    aliases: Sequence[str],
) -> Counter[str]:
    """Build V3 features from authoritative fields without requiring token text."""

    authoritative_text = "\n".join(
        fields[name] for name in ("title", "teaser", "body") if fields[name]
    )
    local = anonymize_issuer_mentions(
        " ".join(issuer_local_clauses(authoritative_text, aliases=aliases)),
        aliases=aliases,
    )
    supplemental = "\n".join(
        value for value in (fields["external"], fields["pdf"]) if value
    )
    local_sentences = [
        sentence
        for sentence in re.split(
            r"(?<=[.!?])\s+|[\r\n]+",
            "\n".join((fields["title"], fields["teaser"], fields["body"])),
        )
        if re.search(
            rf"(?<![A-Z0-9]){re.escape(ticker)}(?![A-Z0-9])", sentence, re.I
        )
    ]
    result = Counter()
    result.update(_word_features("title_word", fields["title"]))
    result.update(_word_features("teaser_word", fields["teaser"]))
    result.update(_word_features("body_word", fields["body"]))
    result.update(_word_features("supplemental_word", supplemental))
    result.update(_char_features("title_char", fields["title"]))
    result.update(_char_features("teaser_char", fields["teaser"]))
    result.update(_word_features("local_word", " ".join(local_sentences)))
    result.update(_structural_features(fields, ticker))
    result.update(_word_features("issuer_clause_word", local))
    result.update(_char_features("issuer_clause_char", local))
    result.update(economic_relation_features(local))
    if local:
        result["economic_relation|locality:explicit_or_bounded_anaphora"] = 1
    return result


def fit_v3_vocabulary(
    documents: Sequence[tuple[str, str, Sequence[str]]],
    *,
    min_document_frequency: int = 3,
    budgets: Mapping[str, int] = V3_FIELD_BUDGETS,
) -> tuple[tuple[str, ...], np.ndarray, dict[str, Any]]:
    if not documents:
        raise ValueError("Training documents are empty")
    document_frequency: dict[str, Counter[str]] = defaultdict(Counter)
    for ticker, text, aliases in documents:
        for term in tfidf_v3_feature_counts(text, ticker=ticker, aliases=aliases):
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
        "feature_only_change_from_v2": True,
        "point_in_time_alias_locality": True,
        "structured_numeric_economic_relations": True,
        "supervised_feature_selection": False,
        "gold_or_prediction_features": False,
    }


def transform_v3(
    text: str,
    *,
    ticker: str,
    aliases: Sequence[str],
    vocabulary: Mapping[str, int],
    idf: np.ndarray,
) -> np.ndarray:
    counts = tfidf_v3_feature_counts(text, ticker=ticker, aliases=aliases)
    vector = np.zeros(len(vocabulary), dtype=np.float32)
    for term, count in counts.items():
        index = vocabulary.get(term)
        if index is not None:
            vector[index] = (1.0 + math.log(count)) * float(idf[index])
    return l2_normalize(vector)


def prepare_tfidf_v3_dataset(
    *,
    source_data_root: Path,
    output_root: Path,
    client: ClickHouseHttpClient,
    identity_database: str = "q_live",
    tokenizer_model: str = DEFAULT_TOKENIZER_MODEL,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    min_document_frequency: int = 3,
    source_batch_size: int = 500,
) -> dict[str, Any]:
    started = time.perf_counter()
    source_data_root = assert_runtime_path(source_data_root)
    output_root = assert_runtime_path(output_root)
    validate_prepared_dataset(source_data_root)
    if output_root.exists():
        raise RuntimeError(f"Refusing to overwrite TF-IDF V3 dataset: {output_root}")
    source_manifest = json.loads((source_data_root / "manifest.json").read_text(encoding="utf-8"))
    source_representation = str((source_manifest.get("representation") or {}).get("kind") or "qwen")
    if source_manifest.get("version") != DATASET_VERSION and source_representation != "qwen":
        raise RuntimeError("TF-IDF V3 requires a Qwen supervision authority")

    article_metadata = read_jsonl(source_data_root / "article_metadata.jsonl")
    issuer_metadata = read_jsonl(source_data_root / "issuer_metadata.jsonl")
    source_ids = sorted({str(row["source_id"]) for row in article_metadata})
    documents, token_report = decode_qwen_token_documents(
        iter_qwen_token_rows(
            client,
            source_ids,
            tokenizer_model=tokenizer_model,
            embedding_model=embedding_model,
            source_batch_size=source_batch_size,
        ),
        tokenizer_model=tokenizer_model,
    )
    identity_index = load_identity_index(client, identity_database)
    identity_rows: list[dict[str, Any]] = []
    aliases_by_key: dict[tuple[str, str], tuple[str, ...]] = {}
    for (source_id, ticker), text in sorted(documents.items()):
        fields = parse_qwen_news_document(text)
        published = fields["published_at_utc"]
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

    training_sources = {
        str(row["source_id"]) for row in article_metadata if row["split"] == "train"
    }
    training_documents = [
        (ticker, text, aliases_by_key[(source_id, ticker)])
        for (source_id, ticker), text in documents.items()
        if source_id in training_sources
    ]
    terms, idf, feature_report = fit_v3_vocabulary(
        training_documents, min_document_frequency=min_document_frequency
    )
    vocabulary = {term: index for index, term in enumerate(terms)}
    vectors = {
        key: transform_v3(
            text,
            ticker=key[1],
            aliases=aliases_by_key[key],
            vocabulary=vocabulary,
            idf=idf,
        )
        for key, text in documents.items()
    }
    vectors_by_source: dict[str, list[np.ndarray]] = defaultdict(list)
    for (source_id, _), vector in vectors.items():
        vectors_by_source[source_id].append(vector)
    article_vectors = np.stack(
        [
            l2_normalize(np.mean(vectors_by_source[row["source_id"]], axis=0))
            for row in article_metadata
        ]
    ).astype(np.float32)
    issuer_vectors: list[np.ndarray] = []
    for row in issuer_metadata:
        vector, _ = match_issuer_embedding(row["source_id"], row["ticker"], vectors)
        if vector is None:
            raise RuntimeError(f"Missing TF-IDF V3 issuer vector: {row['unit_id']}")
        issuer_vectors.append(vector)

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
        "label_contract.json",
        "vocabulary.json",
    )
    manifest = {
        "version": TFIDF_V3_DATASET_VERSION,
        "status": "complete",
        "representation": {"kind": "tfidf_v3", **feature_report},
        "model_change_from_v2": False,
        "identity_authority": {
            "database": identity_database,
            "artifact": "identity_features.jsonl",
            "rows": len(identity_rows),
        },
        "source_manifest_sha256": file_sha256(source_data_root / "manifest.json"),
        "token_authority": token_report,
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
