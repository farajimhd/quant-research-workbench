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
    TFIDF_V2_DATASET_VERSION,
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
from .tfidf_supervision import (
    DEFAULT_TOKENIZER_MODEL,
    decode_qwen_token_documents,
    iter_qwen_token_rows,
)


DEFAULT_TFIDF_V2_ROOT = (
    Path(r"D:\TradingML\runtimes")
    / "text_intelligence"
    / "news_synthesis_v1"
    / "tfidf_supervision_v2"
)
WORD_PATTERN = re.compile(r"[a-z][a-z0-9_'-]*|<[a-z_]+>", re.I)
SENTENCE_PATTERN = re.compile(r"(?<=[.!?])\s+|[\r\n]+")
FIELD_BUDGETS = {
    "title_word": 1536,
    "teaser_word": 768,
    "body_word": 3072,
    "supplemental_word": 512,
    "title_char": 1024,
    "teaser_char": 512,
    "local_word": 512,
    "structural": 256,
}


def parse_qwen_news_document(text: str) -> dict[str, str]:
    fields = {
        "provider": "",
        "ticker": "",
        "published_at_utc": "",
        "title": "",
        "teaser": "",
        "channels": "",
        "tags": "",
        "body": "",
        "external": "",
        "pdf": "",
    }
    section = ""
    section_lines: dict[str, list[str]] = defaultdict(list)
    for line in text.splitlines():
        marker = line.strip().upper()
        if marker in {"BODY", "EXTERNAL_TEXT", "PDF_TEXT"}:
            section = {
                "BODY": "body",
                "EXTERNAL_TEXT": "external",
                "PDF_TEXT": "pdf",
            }[marker]
            continue
        if section:
            section_lines[section].append(line)
            continue
        match = re.match(
            r"^(provider|ticker|published_at_utc|title|teaser|channels|tags):\s*(.*)$",
            line,
            re.I,
        )
        if match:
            fields[match.group(1).lower()] = match.group(2).strip()
    for name in ("body", "external", "pdf"):
        fields[name] = "\n".join(section_lines[name]).strip()
    return fields


def normalize_financial_text(text: str) -> str:
    value = text.lower()
    value = re.sub(r"(?:us\$|\$|€|£)\s*[-+]?\d[\d,]*(?:\.\d+)?", " <money> ", value)
    value = re.sub(r"[-+]?\d+(?:\.\d+)?\s*(?:%|percent|percentage points?|bps|basis points?)", " <percent> ", value)
    value = re.sub(r"\b(?:19|20)\d{2}\b", " <year> ", value)
    value = re.sub(r"\b\d+\.\d+\b", " <decimal> ", value)
    value = re.sub(r"\b\d[\d,]*\b", " <integer> ", value)
    return re.sub(r"\s+", " ", value).strip()


def _word_features(namespace: str, text: str) -> Counter[str]:
    tokens = [match.group(0) for match in WORD_PATTERN.finditer(normalize_financial_text(text))]
    result = Counter(f"{namespace}|u:{token}" for token in tokens)
    result.update(
        f"{namespace}|b:{left}::{right}" for left, right in zip(tokens, tokens[1:])
    )
    return result


def _char_features(namespace: str, text: str) -> Counter[str]:
    normalized = re.sub(r"[^a-z0-9<>]+", "_", normalize_financial_text(text))[:600]
    result: Counter[str] = Counter()
    for width in (3, 4, 5):
        result.update(
            f"{namespace}|c{width}:{normalized[index:index + width]}"
            for index in range(max(0, len(normalized) - width + 1))
        )
    return result


def _count_pattern(text: str, pattern: str) -> int:
    return len(re.findall(pattern, text, flags=re.I))


def _structural_features(fields: Mapping[str, str], ticker: str) -> Counter[str]:
    title = fields["title"]
    content = "\n".join(
        fields[name] for name in ("title", "teaser", "body", "external", "pdf")
    )
    patterns = {
        "time:completed": r"\b(?:completed|closed|received|approved|granted|regained|achieved|launched|reported|announced)\b",
        "time:forward": r"\b(?:expects?|forecasts?|guidance|outlook|will|plans?|targets?|projects?)\b",
        "time:historical": r"\b(?:previously|formerly|last year|prior year|years? ago|historically)\b",
        "state:conditional": r"\b(?:may|might|could|if|subject to|seeks?|intends?|proposes?)\b",
        "state:negated": r"\b(?:not|never|no longer|failed to|unable to|cannot|can't|hasn't|haven't)\b",
        "origin:analyst": r"\b(?:analysts?|brokerage|price target|rating|upgrade[sd]?|downgrade[sd]?)\b",
        "origin:regulator": r"\b(?:FDA|SEC|FTC|DOJ|Nasdaq|NYSE|regulator|commission)\b",
        "purpose:move": r"\b(?:shares?|stock)\b.{0,60}\b(?:rose|fell|rallied|dropped|higher|lower|up|down)\b",
        "purpose:recap": r"\b(?:roundup|recap|stocks? to watch|market overview|movers|gainers|losers)\b",
        "direction:positive": r"\b(?:beat|beats|exceeded|grew|growth|improved|approval|approved|won|gain(?:ed)?|record|raised|profitable)\b",
        "direction:negative": r"\b(?:missed|fell|declined|loss|losses|failed|rejected|denied|suspended|delisted|investigation|lawsuit|bankruptcy)\b",
        "concept:earnings": r"\b(?:earnings|revenue|sales|EPS|net income|profit|margin)\b",
        "concept:guidance": r"\b(?:guidance|outlook|forecast|expects?|projects?|targets?)\b",
        "concept:financing": r"\b(?:offering|financing|credit facility|debt|loan|notes|liquidity|cash runway)\b",
        "concept:capital_return": r"\b(?:dividend|distribution|buyback|repurchase)\b",
        "concept:clinical": r"\b(?:clinical|trial|endpoint|patient|phase [123]|study results?)\b",
        "concept:regulatory": r"\b(?:FDA|regulatory|clearance|approval|submission|authorization)\b",
        "concept:transaction": r"\b(?:acquisition|merger|acquire[sd]?|buyout|takeover|definitive agreement)\b",
        "concept:contract": r"\b(?:contract|agreement|order|partnership|collaboration)\b",
        "concept:legal": r"\b(?:lawsuit|litigation|settlement|court|investigation|subpoena)\b",
        "concept:listing": r"\b(?:Nasdaq|NYSE|listing|delisting|reverse split|minimum bid|compliance)\b",
        "concept:management": r"\b(?:CEO|CFO|president|director|appointed|resigned|retired)\b",
        "concept:ownership": r"\b(?:stake|ownership|shareholder|beneficial owner|position)\b",
    }
    result: Counter[str] = Counter()
    counts = {name: _count_pattern(content, pattern) for name, pattern in patterns.items()}
    for name, count in counts.items():
        if count:
            result[f"structural|{name}"] = min(count, 8)
    if counts["direction:positive"] and counts["direction:negative"]:
        result["structural|direction:tradeoff"] = 1
    ticker_pattern = rf"(?<![A-Z0-9]){re.escape(ticker)}(?![A-Z0-9])"
    title_mentions = len(re.findall(ticker_pattern, title, flags=re.I))
    content_mentions = len(re.findall(ticker_pattern, content, flags=re.I))
    if title_mentions:
        result["structural|focality:ticker_in_title"] = min(title_mentions, 4)
    if content_mentions:
        result["structural|focality:ticker_in_content"] = min(content_mentions, 8)
    for name in ("body", "external", "pdf"):
        if fields[name]:
            result[f"structural|source:has_{name}"] = 1
    length = len(fields["body"])
    bucket = "short" if length < 1000 else "medium" if length < 5000 else "long"
    result[f"structural|source:body_{bucket}"] = 1
    return result


def tfidf_v2_feature_counts(text: str, *, ticker: str) -> Counter[str]:
    fields = parse_qwen_news_document(text)
    supplemental = "\n".join(value for value in (fields["external"], fields["pdf"]) if value)
    local_sentences = [
        sentence
        for sentence in SENTENCE_PATTERN.split(
            "\n".join((fields["title"], fields["teaser"], fields["body"]))
        )
        if re.search(rf"(?<![A-Z0-9]){re.escape(ticker)}(?![A-Z0-9])", sentence, re.I)
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
    return result


def fit_v2_vocabulary(
    documents: Sequence[tuple[str, str]],
    *,
    min_document_frequency: int = 3,
    budgets: Mapping[str, int] = FIELD_BUDGETS,
) -> tuple[tuple[str, ...], np.ndarray, dict[str, Any]]:
    if not documents:
        raise ValueError("Training documents are empty")
    document_frequency: dict[str, Counter[str]] = defaultdict(Counter)
    for ticker, text in documents:
        for term in tfidf_v2_feature_counts(text, ticker=ticker):
            family = term.split("|", 1)[0]
            document_frequency[family][term] += 1
    selected: list[tuple[str, int]] = []
    family_report: dict[str, Any] = {}
    for family, budget in budgets.items():
        minimum = 1 if family == "structural" else min_document_frequency
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
        "field_aware": True,
        "financial_quantity_normalization": True,
        "generic_structural_features": True,
        "gold_or_prediction_features": False,
    }


def transform_v2(
    text: str,
    *,
    ticker: str,
    vocabulary: Mapping[str, int],
    idf: np.ndarray,
) -> np.ndarray:
    counts = tfidf_v2_feature_counts(text, ticker=ticker)
    vector = np.zeros(len(vocabulary), dtype=np.float32)
    for term, count in counts.items():
        index = vocabulary.get(term)
        if index is not None:
            vector[index] = (1.0 + math.log(count)) * float(idf[index])
    return l2_normalize(vector)


def prepare_tfidf_v2_dataset(
    *,
    source_data_root: Path,
    output_root: Path,
    client: ClickHouseHttpClient,
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
        raise RuntimeError(f"Refusing to overwrite TF-IDF V2 dataset: {output_root}")
    source_manifest = json.loads((source_data_root / "manifest.json").read_text(encoding="utf-8"))
    source_representation = str((source_manifest.get("representation") or {}).get("kind") or "qwen")
    if source_manifest.get("version") != DATASET_VERSION and source_representation != "qwen":
        raise RuntimeError("TF-IDF V2 requires a Qwen supervision authority")
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
    training_sources = {
        str(row["source_id"]) for row in article_metadata if row["split"] == "train"
    }
    training_documents = [
        (ticker, text)
        for (source_id, ticker), text in documents.items()
        if source_id in training_sources
    ]
    terms, idf, feature_report = fit_v2_vocabulary(
        training_documents, min_document_frequency=min_document_frequency
    )
    vocabulary = {term: index for index, term in enumerate(terms)}
    vectors = {
        key: transform_v2(text, ticker=key[1], vocabulary=vocabulary, idf=idf)
        for key, text in documents.items()
    }
    vectors_by_source: dict[str, list[np.ndarray]] = defaultdict(list)
    for (source_id, _), vector in vectors.items():
        vectors_by_source[source_id].append(vector)
    article_vectors = np.stack(
        [l2_normalize(np.mean(vectors_by_source[row["source_id"]], axis=0)) for row in article_metadata]
    ).astype(np.float32)
    issuer_vectors: list[np.ndarray] = []
    for row in issuer_metadata:
        vector, _ = match_issuer_embedding(row["source_id"], row["ticker"], vectors)
        if vector is None:
            raise RuntimeError(f"Missing TF-IDF V2 issuer vector: {row['unit_id']}")
        issuer_vectors.append(vector)
    output_root.mkdir(parents=True)
    save_array(output_root / "article_embeddings.npy", article_vectors)
    save_array(output_root / "issuer_embeddings.npy", np.stack(issuer_vectors).astype(np.float32))
    for name in ("article_eligibility.npy", "issuer_eligibility.npy", "issuer_sentiment.npy", "issuer_concepts.npy"):
        save_array(output_root / name, np.load(source_data_root / name))
    write_jsonl(output_root / "article_metadata.jsonl", article_metadata)
    write_jsonl(output_root / "issuer_metadata.jsonl", issuer_metadata)
    write_json(
        output_root / "label_contract.json",
        json.loads((source_data_root / "label_contract.json").read_text(encoding="utf-8")),
    )
    write_json(output_root / "vocabulary.json", {"terms": list(terms), "idf": [float(value) for value in idf]})
    files = (
        "article_embeddings.npy", "article_eligibility.npy", "issuer_embeddings.npy",
        "issuer_eligibility.npy", "issuer_sentiment.npy", "issuer_concepts.npy",
        "article_metadata.jsonl", "issuer_metadata.jsonl", "label_contract.json", "vocabulary.json",
    )
    manifest = {
        "version": TFIDF_V2_DATASET_VERSION,
        "status": "complete",
        "representation": {"kind": "tfidf_v2", **feature_report},
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
