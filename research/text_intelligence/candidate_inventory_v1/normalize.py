from __future__ import annotations

import html
import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable


TOKEN_RE = re.compile(r"<[a-z_]+>|[a-z][a-z0-9]*(?:[-'][a-z0-9]+)*", re.IGNORECASE)
SPACE_RE = re.compile(r"\s+")
SENTENCE_RE = re.compile(r"(?<=[.!?;:])\s+|\n+")
URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)

VALUE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "price_per_share",
        re.compile(
            r"(?P<raw>(?:US\$|\$)\s*\d[\d,]*(?:\.\d+)?"
            r"\s*(?:per|/)\s*(?:common\s+)?share)",
            re.IGNORECASE,
        ),
    ),
    (
        "share_count",
        re.compile(
            r"(?P<raw>\d[\d,]*(?:\.\d+)?\s*(?:thousand|million|billion|k|m|bn)?"
            r"\s+(?:common\s+|ordinary\s+)?shares?)",
            re.IGNORECASE,
        ),
    ),
    (
        "money",
        re.compile(
            r"(?P<raw>(?:US\$|\$|USD\s+)\s*\d[\d,]*(?:\.\d+)?"
            r"\s*(?:thousand|million|billion|trillion|k|m|bn|b)?\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "percentage",
        re.compile(r"(?P<raw>[+-]?\d[\d,]*(?:\.\d+)?\s*(?:%|percent\b))", re.IGNORECASE),
    ),
    (
        "basis_points",
        re.compile(
            r"(?P<raw>[+-]?\d[\d,]*(?:\.\d+)?\s*(?:basis\s+points?|bps)\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "multiple",
        re.compile(r"(?P<raw>\d[\d,]*(?:\.\d+)?\s*[xX]\b)"),
    ),
    (
        "ratio",
        re.compile(r"(?P<raw>\d+(?:\.\d+)?\s*(?::|to)\s*\d+(?:\.\d+)?)", re.IGNORECASE),
    ),
    (
        "number",
        re.compile(r"(?P<raw>[+-]?\d[\d,]*(?:\.\d+)?)"),
    ),
)

PLACEHOLDERS = {
    "basis_points": "<basis_points>",
    "money": "<money>",
    "multiple": "<multiple>",
    "number": "<number>",
    "percentage": "<percentage>",
    "price_per_share": "<price_per_share>",
    "ratio": "<ratio>",
    "share_count": "<share_count>",
}

STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "by", "for",
    "from", "has", "have", "having", "if", "in", "into", "is", "it", "its",
    "of", "on", "or", "that", "the", "their", "there", "these", "this", "to",
    "was", "were", "will", "with", "would",
}


@dataclass(frozen=True, slots=True)
class ExtractedValue:
    value_type: str
    raw: str
    normalized_number: str
    placeholder: str
    context: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class NormalizedText:
    text: str
    values: tuple[ExtractedValue, ...]


def normalize_financial_text(
    text: str,
    *,
    entity_terms: Iterable[str] = (),
    evidence_chars: int = 240,
) -> NormalizedText:
    clean = html.unescape(str(text or "")).replace("\x00", " ")
    clean = URL_RE.sub(" <url> ", clean)
    values: list[ExtractedValue] = []
    occupied: list[tuple[int, int]] = []
    replacements: list[tuple[int, int, str]] = []
    for value_type, pattern in VALUE_PATTERNS:
        for match in pattern.finditer(clean):
            start, end = match.span("raw")
            if any(start < existing_end and end > existing_start for existing_start, existing_end in occupied):
                continue
            raw = match.group("raw")
            placeholder = PLACEHOLDERS[value_type]
            values.append(
                ExtractedValue(
                    value_type=value_type,
                    raw=raw,
                    normalized_number=normalize_number(raw),
                    placeholder=placeholder,
                    context=evidence_context(clean, start, end, evidence_chars),
                    start=start,
                    end=end,
                )
            )
            occupied.append((start, end))
            replacements.append((start, end, f" {placeholder} "))
    for start, end, replacement in sorted(replacements, reverse=True):
        clean = clean[:start] + replacement + clean[end:]
    lowered = clean.lower()
    for term in sorted(
        {str(item).strip().lower() for item in entity_terms if len(str(item).strip()) >= 2},
        key=len,
        reverse=True,
    ):
        lowered = re.sub(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", " <entity> ", lowered)
    lowered = re.sub(r"[^a-z0-9<_>'\-\n.!?;:]+", " ", lowered)
    return NormalizedText(text=SPACE_RE.sub(" ", lowered).strip(), values=tuple(values))


def iter_sentences(text: str) -> Iterable[str]:
    for value in SENTENCE_RE.split(str(text or "")):
        sentence = SPACE_RE.sub(" ", value).strip()
        if sentence:
            yield sentence


def tokens(text: str) -> tuple[str, ...]:
    return tuple(match.group(0).lower() for match in TOKEN_RE.finditer(text))


def candidate_ngrams(
    normalized_text: str,
    *,
    min_ngram: int,
    max_ngram: int,
) -> Iterable[tuple[str, int]]:
    for sentence in iter_sentences(normalized_text):
        values = tokens(sentence)
        if len(values) < min_ngram:
            continue
        for size in range(min_ngram, min(max_ngram, len(values)) + 1):
            for index in range(0, len(values) - size + 1):
                phrase_tokens = values[index : index + size]
                if not valid_phrase(phrase_tokens):
                    continue
                yield " ".join(phrase_tokens), size


def valid_phrase(phrase_tokens: tuple[str, ...]) -> bool:
    if not phrase_tokens:
        return False
    if phrase_tokens[0] in STOP_WORDS or phrase_tokens[-1] in STOP_WORDS:
        return False
    content = [
        value
        for value in phrase_tokens
        if value not in STOP_WORDS and not value.startswith("<")
    ]
    if not content:
        return False
    if len(set(phrase_tokens)) == 1:
        return False
    return True


def normalize_number(raw: str) -> str:
    match = re.search(r"[+-]?\d[\d,]*(?:\.\d+)?", raw)
    if not match:
        return ""
    try:
        number = Decimal(match.group(0).replace(",", ""))
    except InvalidOperation:
        return ""
    lowered = raw.lower()
    suffix = lowered[match.end() :]
    multiplier = Decimal(1)
    if re.search(r"^\s*(?:thousand|k)\b", suffix):
        multiplier = Decimal(1_000)
    elif re.search(r"^\s*(?:million|m)\b", suffix):
        multiplier = Decimal(1_000_000)
    elif re.search(r"^\s*(?:billion|bn|b)\b", suffix):
        multiplier = Decimal(1_000_000_000)
    elif re.search(r"^\s*trillion\b", suffix):
        multiplier = Decimal(1_000_000_000_000)
    value = number * multiplier
    if not value.is_finite():
        return ""
    if value == value.to_integral():
        return str(int(value))
    return format(value.normalize(), "f")


def evidence_context(text: str, start: int, end: int, max_chars: int) -> str:
    half = max(20, int(max_chars) // 2)
    left = max(0, start - half)
    right = min(len(text), end + half)
    value = SPACE_RE.sub(" ", text[left:right]).strip()
    if left:
        value = "…" + value
    if right < len(text):
        value += "…"
    return value[: max_chars + 2]


def normalized_pmi(
    phrase_documents: int,
    left_documents: int,
    right_documents: int,
    total_documents: int,
) -> float | None:
    if min(phrase_documents, left_documents, right_documents, total_documents) <= 0:
        return None
    p_xy = phrase_documents / total_documents
    p_x = left_documents / total_documents
    p_y = right_documents / total_documents
    pmi = math.log(p_xy / (p_x * p_y))
    denominator = -math.log(p_xy)
    if denominator <= 0:
        return None
    return pmi / denominator
