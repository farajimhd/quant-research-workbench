from __future__ import annotations

import re
from collections import Counter
from typing import Mapping, Sequence

import numpy as np

from .tfidf_supervision_v2 import _char_features, _word_features
from .tfidf_supervision_v3 import CLAUSE_PATTERN, anonymize_issuer_mentions, issuer_local_clauses
from .tfidf_supervision_v7 import (
    V7_FIELD_BUDGETS,
    tfidf_v7_feature_counts,
)


V8_FIELD_BUDGETS = {
    "provider_title_word": 1280,
    "provider_teaser_word": 640,
    "provider_body_word": 2304,
    "provider_title_char": 768,
    "provider_teaser_char": 384,
    "provider_local_word": 768,
    "provider_local_char": 384,
    "provider_economic": 256,
    "normalized_structural": 256,
    "normalized_economic": 256,
    "external_local_word": 512,
    "pdf_local_word": 512,
    "enrichment_economic": 256,
    "metadata_word": 384,
    "metadata_structural": 256,
    "target_clause_word": 1024,
    "target_clause_char": 512,
    "target_clause_structure": 384,
    "target_clause_interaction": 384,
    "enrichment_target_clause_word": 256,
}
if sum(V8_FIELD_BUDGETS.values()) != sum(V7_FIELD_BUDGETS.values()):
    raise AssertionError("V8 must retain the V7 total feature-budget ceiling")

V8_VIEW_PREFIXES = {
    "provider": ("provider_", "target_clause_"),
    "normalized": ("normalized_",),
    "enrichment": ("external_local_", "pdf_local_", "enrichment_"),
    "metadata": ("metadata_",),
}

_NUMBER = r"[-+]?\d[\d,]*(?:\.\d+)?"
_CURRENTNESS_PATTERNS = (
    ("conditional", re.compile(r"\b(?:if|unless|subject to|conditional(?:ly)?|could|may|might)\b", re.I)),
    ("forward", re.compile(r"\b(?:will|expects? to|plans? to|intends? to|aims? to|scheduled to|seeks? to)\b", re.I)),
    ("historical", re.compile(r"\b(?:previously|formerly|last (?:year|quarter|month)|year-ago|had been|used to)\b", re.I)),
    ("current_completed", re.compile(r"\b(?:announced|reported|completed|closed|received|approved|launched|signed|filed|issued|declared|appointed|acquired|sold)\b", re.I)),
    ("current_state", re.compile(r"\b(?:is|are|has|have|remains?|continues?)\b", re.I)),
)
_EVENT_PATTERNS = (
    ("earnings", re.compile(r"\b(?:earnings|revenue|sales|eps|income|profit|loss|margin)\b", re.I)),
    ("guidance", re.compile(r"\b(?:guidance|outlook|forecast|target|runway)\b", re.I)),
    ("financing", re.compile(r"\b(?:financing|offering|loan|debt|notes?|credit facility|capital raise)\b", re.I)),
    ("capital_return", re.compile(r"\b(?:dividend|distribution|buyback|repurchase)\b", re.I)),
    ("transaction", re.compile(r"\b(?:acquisition|merger|takeover|sale agreement|divestiture)\b", re.I)),
    ("commercial", re.compile(r"\b(?:contract|order|partnership|license|supply agreement)\b", re.I)),
    ("product", re.compile(r"\b(?:product|launch|release|milestone)\b", re.I)),
    ("clinical", re.compile(r"\b(?:clinical|trial|endpoint|patient|study)\b", re.I)),
    ("regulatory", re.compile(r"\b(?:regulator|regulatory|fda|approval|clearance|compliance)\b", re.I)),
    ("legal", re.compile(r"\b(?:lawsuit|litigation|settlement|court|investigation|fine|penalty)\b", re.I)),
    ("listing", re.compile(r"\b(?:listing|delisting|exchange|reverse split|share consolidation)\b", re.I)),
    ("governance", re.compile(r"\b(?:chief executive|ceo|cfo|director|board|management)\b", re.I)),
    ("ownership", re.compile(r"\b(?:stake|ownership|shareholder|beneficial owner)\b", re.I)),
    ("market_observation", re.compile(r"\b(?:shares?|stock|price)\b.{0,24}\b(?:rose|fell|gained|dropped|traded)\b", re.I)),
)
_POSITIVE = re.compile(r"\b(?:beat|exceed(?:ed|s)?|increase[sd]?|grew|rose|improved|approved|cleared|won|profit(?:able)?|regained|raised guidance)\b", re.I)
_NEGATIVE = re.compile(r"\b(?:miss(?:ed|es)?|declined?|decrease[sd]?|fell|dropped|failed|rejected|denied|loss|delist(?:ed|ing)?|cut guidance|terminated)\b", re.I)
_ORIGIN_PATTERNS = (
    ("analyst", re.compile(r"\b(?:analyst|brokerage|price target|rating|research note)\b", re.I)),
    ("regulator", re.compile(r"\b(?:fda|sec|ftc|department of justice|regulator|court|nasdaq|nyse)\b", re.I)),
    ("issuer", re.compile(r"\b(?:announced|reported|said|stated|expects|guidance)\b", re.I)),
)


def _normalized_aliases(aliases: Sequence[str]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {value.strip() for value in aliases if len(value.strip()) >= 2},
            key=lambda value: (-len(value), value.lower()),
        )
    )


def _mask_clause(clause: str, aliases: Sequence[str]) -> str:
    value = anonymize_issuer_mentions(clause, aliases=aliases)
    value = re.sub(rf"(?:[$€£]\s*){_NUMBER}(?:\s*(?:million|billion|m|bn|b))?", " <currency_amount> ", value, flags=re.I)
    value = re.sub(
        rf"{_NUMBER}\s*(?:%|percent\b|basis points?\b|bps\b)",
        " <rate_value> ",
        value,
        flags=re.I,
    )
    value = re.sub(r"\b(?:19|20)\d{2}\b", " <year_value> ", value)
    value = re.sub(_NUMBER, " <number_value> ", value)
    return re.sub(r"\s+", " ", value).strip()


def _clause_roles(clause: str, aliases: Sequence[str]) -> tuple[str, ...]:
    lowered = clause.lower()
    positions = [lowered.find(alias.lower()) for alias in aliases if alias.lower() in lowered]
    if not positions:
        return ("anaphoric",)
    target_position = min(positions)
    action = re.search(r"\b(?:announced|reported|issued|completed|received|signed|acquired|sold|launched|filed|appointed|approved|rejected|sued|fined)\b", lowered)
    if action is None:
        return ("mentioned",)
    return ("actor" if target_position < action.start() else "affected",)


def invariant_target_clause_features(
    text: str,
    *,
    aliases: Sequence[str],
) -> tuple[str, Counter[str], Counter[str]]:
    aliases = _normalized_aliases(aliases)
    local_clauses = issuer_local_clauses(text, aliases=aliases)
    masked: list[str] = []
    structural: Counter[str] = Counter()
    interactions: Counter[str] = Counter()
    for clause in local_clauses:
        masked.append(_mask_clause(clause, aliases))
        roles = _clause_roles(clause, aliases)
        currentness = next(
            (name for name, pattern in _CURRENTNESS_PATTERNS if pattern.search(clause)),
            "unspecified",
        )
        events = tuple(name for name, pattern in _EVENT_PATTERNS if pattern.search(clause)) or ("other",)
        positive = bool(_POSITIVE.search(clause))
        negative = bool(_NEGATIVE.search(clause))
        direction = "mixed" if positive and negative else "positive" if positive else "negative" if negative else "none"
        origin = next((name for name, pattern in _ORIGIN_PATTERNS if pattern.search(clause)), "editorial_or_unknown")
        number_types = []
        if re.search(rf"{_NUMBER}\s*(?:%|percent)\b", clause, re.I):
            number_types.append("percent")
        if re.search(rf"{_NUMBER}\s*(?:bps|basis points?)\b", clause, re.I):
            number_types.append("basis_points")
        if re.search(rf"(?:[$€£]\s*){_NUMBER}", clause, re.I):
            number_types.append("currency")
        if re.search(r"\b(?:versus|vs\.?|compared with|above|below|beat|miss)\b", clause, re.I):
            number_types.append("comparison")
        structural[f"target_clause_structure|currentness:{currentness}"] += 1
        structural[f"target_clause_structure|direction:{direction}"] += 1
        structural[f"target_clause_structure|origin:{origin}"] += 1
        for role in roles:
            structural[f"target_clause_structure|role:{role}"] += 1
        for event in events:
            structural[f"target_clause_structure|event:{event}"] += 1
            interactions[f"target_clause_interaction|event:{event}|currentness:{currentness}|direction:{direction}"] += 1
            interactions[f"target_clause_interaction|event:{event}|origin:{origin}"] += 1
            for role in roles:
                interactions[f"target_clause_interaction|event:{event}|role:{role}"] += 1
        for number_type in number_types:
            structural[f"target_clause_structure|number:{number_type}"] += 1
            interactions[f"target_clause_interaction|number:{number_type}|direction:{direction}"] += 1
    return " ".join(masked), structural, interactions


def tfidf_v8_feature_counts(
    *,
    original_fields: Mapping[str, str],
    normalized_fields: Mapping[str, str],
    metadata_text: str,
    metadata_structural: Counter[str],
    ticker: str,
    aliases: Sequence[str],
) -> Counter[str]:
    result = tfidf_v7_feature_counts(
        original_fields=original_fields,
        normalized_fields=normalized_fields,
        metadata_text=metadata_text,
        metadata_structural=metadata_structural,
        ticker=ticker,
        aliases=aliases,
    )
    provider_text = "\n".join(
        str(original_fields.get(name) or "") for name in ("title", "teaser", "body")
    )
    masked, structural, interactions = invariant_target_clause_features(
        provider_text, aliases=aliases
    )
    enrichment_text = "\n".join(
        str(normalized_fields.get(name) or "") for name in ("external", "pdf")
    )
    enrichment_masked, _, _ = invariant_target_clause_features(
        enrichment_text, aliases=aliases
    )
    result.update(_word_features("target_clause_word", masked))
    result.update(_char_features("target_clause_char", masked))
    result.update(structural)
    result.update(interactions)
    result.update(_word_features("enrichment_target_clause_word", enrichment_masked))
    return result


def v8_view_indexes(vocabulary: Mapping[str, int]) -> dict[str, np.ndarray]:
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
        for view, prefixes in V8_VIEW_PREFIXES.items()
    }
