from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from .embedding_supervision import TFIDF_V10_DATASET_VERSION
from .tfidf_supervision_v7 import fit_v7_vocabulary_from_document_frequency
from .tfidf_supervision_v3 import anonymize_issuer_mentions
from .tfidf_supervision_v9 import (
    ClauseIR,
    V9_FIELD_BUDGETS,
    _EVENT_PATTERNS,
    _normalized_aliases,
    analyze_clause_ir,
    prepare_sparse_feature_dataset,
    tfidf_v9_feature_counts,
)


DEFAULT_TFIDF_V10_ROOT = (
    Path(r"D:\TradingML\runtimes")
    / "text_intelligence"
    / "news_synthesis_v1"
    / "tfidf_supervision_v10"
)
V10_STABILITY_FOLDS = 4
V10_STABILITY_MIN_FOLDS = 3
V10_FIELD_BUDGETS = {
    **V9_FIELD_BUDGETS,
    "provider_body_word": 1856,
    "target_clause_word": 736,
    "target_clause_char": 320,
    "cross_view_agreement": 96,
    "metric_relation": 192,
    "evidence_composition": 128,
    "cross_view_alignment": 96,
    "evidence_position": 96,
}
if sum(V10_FIELD_BUDGETS.values()) != sum(V9_FIELD_BUDGETS.values()):
    raise AssertionError("V10 must retain the V9 total feature-budget ceiling")

V10_VIEW_PREFIXES = {
    "provider": ("provider_", "target_clause_"),
    "normalized": ("normalized_",),
    "enrichment": ("external_local_", "pdf_local_", "enrichment_"),
    "metadata": ("metadata_",),
    "role": ("predicate_role",),
    "state": ("state_transition",),
    "numeric": ("numeric_magnitude", "metric_relation"),
    "composition": ("evidence_composition",),
    "agreement": ("cross_view_agreement", "cross_view_alignment"),
    "position": ("evidence_position",),
}

_METRICS = (
    ("revenue", re.compile(r"\b(?:revenue|sales)\b", re.I)),
    ("eps", re.compile(r"\b(?:eps|earnings per share)\b", re.I)),
    ("profit", re.compile(r"\b(?:profit|net income|earnings)\b", re.I)),
    ("loss", re.compile(r"\b(?:loss|net loss)\b", re.I)),
    ("margin", re.compile(r"\bmargin\b", re.I)),
    ("guidance", re.compile(r"\b(?:guidance|outlook|forecast)\b", re.I)),
    ("cash", re.compile(r"\b(?:cash|liquidity|runway)\b", re.I)),
    ("debt", re.compile(r"\b(?:debt|loan|notes?|credit facility)\b", re.I)),
    ("price", re.compile(r"\b(?:share price|stock price|price)\b", re.I)),
    ("volume", re.compile(r"\b(?:volume|shipments?|orders?)\b", re.I)),
)
_COMPARISONS = (
    ("beat", re.compile(r"\b(?:beat|exceed(?:ed|s)?|above)\b", re.I)),
    ("miss", re.compile(r"\b(?:miss(?:ed|es)?|below)\b", re.I)),
    ("increase", re.compile(r"\b(?:increase[sd]?|grew|rose|higher)\b", re.I)),
    ("decrease", re.compile(r"\b(?:decrease[sd]?|declined?|fell|lower)\b", re.I)),
    ("versus", re.compile(r"\b(?:versus|vs\.?|compared with|year[- ]over[- ]year|yoy)\b", re.I)),
)


def _metric_relation_features(ir: Sequence[ClauseIR]) -> Counter[str]:
    result: Counter[str] = Counter()
    for row in ir:
        metrics = [name for name, pattern in _METRICS if pattern.search(row.raw)]
        comparisons = [name for name, pattern in _COMPARISONS if pattern.search(row.raw)]
        if not metrics:
            continue
        for metric in metrics:
            result[f"metric_relation|metric:{metric}|direction:{row.direction}"] += 1
            result[f"metric_relation|metric:{metric}|currentness:{row.currentness}"] += 1
            for comparison in comparisons or ("stated",):
                result[f"metric_relation|metric:{metric}|comparison:{comparison}"] += 1
            for magnitude in row.numeric_tokens or ("no_magnitude",):
                result[f"metric_relation|metric:{metric}|{magnitude}"] += 1
    return result


def _composition_features(ir: Sequence[ClauseIR]) -> Counter[str]:
    result: Counter[str] = Counter()
    counts = Counter()
    for row in ir:
        time = "current" if row.currentness in {"current_completed", "current_state"} else row.currentness
        counts[(time, row.direction)] += 1
        for event in row.events:
            counts[("event", event, row.direction)] += 1
    for (time, direction), count in sorted(
        (item for item in counts.items() if len(item[0]) == 2), key=lambda item: item[0]
    ):
        bucket = "one" if count == 1 else "two" if count == 2 else "three_plus"
        result[f"evidence_composition|{time}:{direction}:{bucket}"] = 1
    current_positive = counts[("current", "positive")]
    current_negative = counts[("current", "negative")]
    if current_positive and current_negative:
        result["evidence_composition|current_tradeoff:positive+negative"] = 1
    if counts[("current", "none")] and not (current_positive or current_negative):
        result["evidence_composition|current_only_neutral"] = 1
    if counts[("historical", "negative")] and current_positive:
        result["evidence_composition|historical_negative+current_positive"] = 1
    if counts[("historical", "positive")] and current_negative:
        result["evidence_composition|historical_positive+current_negative"] = 1
    for key, count in counts.items():
        if len(key) == 3 and key[0] == "event" and count:
            _, event, direction = key
            result[f"evidence_composition|event:{event}|direction:{direction}"] += count
    return result


def _aligned_cross_view_features(
    provider: Sequence[ClauseIR], normalized: Sequence[ClauseIR], enrichment: Sequence[ClauseIR]
) -> Counter[str]:
    result: Counter[str] = Counter()
    views = {"provider": provider, "normalized": normalized, "enrichment": enrichment}
    for left, right in (("provider", "normalized"), ("provider", "enrichment")):
        for a in views[left]:
            for b in views[right]:
                events = sorted(set(a.events) & set(b.events))
                if not events:
                    continue
                role = "same" if a.role == b.role else "different"
                time = "same" if a.currentness == b.currentness else "different"
                direction = "same" if a.direction == b.direction else "different"
                left_tokens = set(re.findall(r"[a-z0-9]+", a.masked.lower())) - {"issuer"}
                right_tokens = set(re.findall(r"[a-z0-9]+", b.masked.lower())) - {"issuer"}
                union = left_tokens | right_tokens
                overlap = len(left_tokens & right_tokens) / len(union) if union else 0.0
                similarity = "high" if overlap >= 0.5 else "medium" if overlap >= 0.2 else "low"
                for event in events:
                    result[
                        f"cross_view_alignment|{left}+{right}|event:{event}|role:{role}|time:{time}|direction:{direction}|similarity:{similarity}"
                    ] = 1
    return result


def _position_features(fields: Mapping[str, str], ir: Sequence[ClauseIR]) -> Counter[str]:
    result: Counter[str] = Counter()
    for name in ("title", "teaser", "body"):
        text = str(fields.get(name) or "")
        if "<issuer>" in text.lower():
            result[f"evidence_position|issuer_in:{name}"] = 1
    if ir:
        result[f"evidence_position|local_clause_count:{'one' if len(ir) == 1 else 'two' if len(ir) == 2 else 'three_plus'}"] = 1
        first = ir[0]
        result[f"evidence_position|first_event:{first.events[0]}|role:{first.role}"] = 1
        directional = [row.clause_index for row in ir if row.direction != "none"]
        result[f"evidence_position|first_directional:{'none' if not directional else 'lead' if directional[0] == 0 else 'later'}"] = 1
        for row in ir:
            issuer_at = row.masked.lower().find("<issuer>")
            predicate_positions = [
                match.start()
                for _, pattern in _EVENT_PATTERNS
                for match in [pattern.search(row.masked)]
                if match is not None
            ]
            if issuer_at >= 0 and predicate_positions:
                distance = min(abs(position - issuer_at) for position in predicate_positions)
                bucket = "near" if distance <= 24 else "medium" if distance <= 80 else "far"
                result[f"evidence_position|issuer_predicate_distance:{bucket}"] += 1
    return result


def tfidf_v10_feature_counts(
    *,
    original_fields: Mapping[str, str],
    normalized_fields: Mapping[str, str],
    metadata_text: str,
    metadata_structural: Counter[str],
    ticker: str,
    aliases: Sequence[str],
) -> Counter[str]:
    aliases = _normalized_aliases(aliases)
    provider_text = "\n".join(str(original_fields.get(name) or "") for name in ("title", "teaser", "body"))
    normalized_text = "\n".join(str(normalized_fields.get(name) or "") for name in ("title", "teaser", "body"))
    external_text = str(normalized_fields.get("external") or "")
    pdf_text = str(normalized_fields.get("pdf") or "")
    ir_cache = {
        "provider": analyze_clause_ir(provider_text, aliases=aliases),
        "normalized": analyze_clause_ir(normalized_text, aliases=aliases),
        "external": analyze_clause_ir(external_text, aliases=aliases),
        "pdf": analyze_clause_ir(pdf_text, aliases=aliases),
    }
    result = tfidf_v9_feature_counts(
        original_fields=original_fields,
        normalized_fields=normalized_fields,
        metadata_text=metadata_text,
        metadata_structural=metadata_structural,
        ticker=ticker,
        aliases=aliases,
        clause_ir=ir_cache,
    )
    provider_ir = ir_cache["provider"]
    normalized_ir = ir_cache["normalized"]
    enrichment_ir = (*ir_cache["external"], *ir_cache["pdf"])
    result.update(_metric_relation_features(provider_ir))
    result.update(_composition_features(provider_ir))
    result.update(_aligned_cross_view_features(provider_ir, normalized_ir, enrichment_ir))
    masked_fields = {
        name: anonymize_issuer_mentions(
            str(original_fields.get(name) or ""), aliases=aliases
        )
        for name in ("title", "teaser", "body")
    }
    result.update(_position_features(masked_fields, provider_ir))
    return result


def v10_view_indexes(vocabulary: Mapping[str, int]) -> dict[str, np.ndarray]:
    terms = sorted(vocabulary, key=vocabulary.get)
    memberships: dict[int, list[str]] = defaultdict(list)
    result: dict[str, np.ndarray] = {}
    for view, prefixes in V10_VIEW_PREFIXES.items():
        indexes = [
            index
            for index, term in enumerate(terms)
            if term.split("|", 1)[0].startswith(prefixes)
        ]
        for index in indexes:
            memberships[index].append(view)
        result[view] = np.asarray(indexes, dtype=np.int64)
    invalid = {index: views for index, views in memberships.items() if len(views) != 1}
    missing = set(range(len(terms))) - set(memberships)
    if invalid or missing:
        raise RuntimeError(f"V10 view partition invalid: overlaps={invalid}, missing={sorted(missing)}")
    return result


def fit_v10_stable_vocabulary(
    *,
    document_frequency: Mapping[str, Counter[str]],
    training_document_count: int,
    min_document_frequency: int,
    budgets: Mapping[str, int],
    feature_counts: Mapping[tuple[str, str], Counter[str]],
    training_sources: set[str],
) -> tuple[tuple[str, ...], np.ndarray, dict[str, Any]]:
    fold_sources = {fold: set() for fold in range(V10_STABILITY_FOLDS)}
    for source_id in sorted(training_sources):
        digest = hashlib.sha256(f"tfidf-v10-stability\0{source_id}".encode()).digest()
        fold_sources[int.from_bytes(digest[:4], "big") % V10_STABILITY_FOLDS].add(source_id)
    fold_df: dict[int, dict[str, Counter[str]]] = {
        fold: defaultdict(Counter) for fold in range(V10_STABILITY_FOLDS)
    }
    fold_documents = Counter()
    for (source_id, _), counts in feature_counts.items():
        if source_id not in training_sources:
            continue
        fold = next(index for index, sources in fold_sources.items() if source_id in sources)
        fold_documents[fold] += 1
        for term in counts:
            fold_df[fold][term.split("|", 1)[0]][term] += 1
    stable_df: dict[str, Counter[str]] = defaultdict(Counter)
    stable_terms: set[str] = set()
    for family, observed in document_frequency.items():
        family_minimum = 1 if family in {
            "normalized_structural", "provider_economic", "normalized_economic",
            "enrichment_economic", "metadata_structural", "target_clause_structure",
            "target_clause_interaction", "cross_view_agreement", "predicate_role",
            "state_transition", "numeric_magnitude", "metric_relation",
            "evidence_composition", "cross_view_alignment", "evidence_position",
        } else min_document_frequency
        for term, count in observed.items():
            present = sum(
                fold_df[fold].get(family, Counter()).get(term, 0) >= family_minimum
                for fold in range(V10_STABILITY_FOLDS)
            )
            if present >= V10_STABILITY_MIN_FOLDS:
                stable_df[family][term] = count
                stable_terms.add(term)
    terms, idf, report = fit_v7_vocabulary_from_document_frequency(
        stable_df,
        training_document_count=training_document_count,
        min_document_frequency=min_document_frequency,
        budgets=budgets,
    )
    report["stability_selection"] = {
        "folds": V10_STABILITY_FOLDS,
        "minimum_folds": V10_STABILITY_MIN_FOLDS,
        "candidate_terms": sum(len(values) for values in document_frequency.values()),
        "stable_terms": len(stable_terms),
        "training_only": True,
    }
    return terms, idf, report


def prepare_v10_sparse_dataset(**kwargs) -> dict[str, Any]:
    return prepare_sparse_feature_dataset(
        feature_counter=tfidf_v10_feature_counts,
        budgets=V10_FIELD_BUDGETS,
        view_indexes=v10_view_indexes,
        vocabulary_fitter=fit_v10_stable_vocabulary,
        dataset_version=TFIDF_V10_DATASET_VERSION,
        representation_kind="tfidf_v10_relational_stable",
        **kwargs,
    )
