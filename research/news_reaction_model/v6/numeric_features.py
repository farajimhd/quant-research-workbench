from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from research.news_reaction_model.v5.config import LoaderConfig as V5LoaderConfig
from research.news_reaction_model.v5.text_features import load_feature_manifest as load_v5_feature_manifest
from research.news_reaction_model.v6.config import LoaderConfig, NumericFeatureConfig


NUMERIC_CONTRACT_VERSION = "financial_numeric_v1"
NUMERIC_DENSE_NAMES = (
    "has_numeric", "mention_count", "currency_count", "percent_count", "bps_count",
    "multiple_ratio_count", "positive_explicit_count", "negative_explicit_count",
    "positive_cue_count", "negative_cue_count", "range_count", "comparison_count",
    "year_count", "quarter_count", "estimate_context_count", "guidance_context_count",
    "log_abs_mean", "log_abs_max", "positive_percent_max", "negative_percent_min",
    "positive_relative_delta_max", "negative_relative_delta_min", "relative_delta_mean",
    "range_relative_width_mean",
)

_NUMBER_ATOM = r"(?:[+\-−]?\s*(?:[$€£¥]\s*)?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*(?:(?:trillion|billion|million|thousand)|[TtBbMmKk](?![A-Za-z]))?\s*(?:%|percent(?:age)?(?:\s+points?)?|bps|basis\s+points?|[xX](?![A-Za-z])|times)?)"
_NUMBER_RE = re.compile(
    r"(?<![\w.])(?P<sign>[+\-−])?\s*(?P<currency>[$€£¥])?\s*"
    r"(?P<number>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*"
    r"(?P<suffix>trillion|billion|million|thousand|[TtBbMmKk](?![A-Za-z]))?\s*"
    r"(?P<unit>%|percent(?:age)?(?:\s+points?)?|bps|basis\s+points?|[xX](?![A-Za-z])|times)?",
    re.IGNORECASE,
)
_FROM_TO_RE = re.compile(rf"\bfrom\s+(?P<a>{_NUMBER_ATOM})\s+to\s+(?P<b>{_NUMBER_ATOM})", re.IGNORECASE)
_COMPARISON_RE = re.compile(
    rf"(?P<a>{_NUMBER_ATOM})\s+(?P<relation>beats?|exceeds?|above|miss(?:es|ed)?|below|versus|vs\.?|compared\s+(?:with|to))\s+(?P<b>{_NUMBER_ATOM})",
    re.IGNORECASE,
)
_RANGE_RE = re.compile(rf"(?P<a>{_NUMBER_ATOM})\s*(?:-|–|—|\bto\b)\s*(?P<b>{_NUMBER_ATOM})", re.IGNORECASE)
_QUARTER_RE = re.compile(r"\b(?:q[1-4]|fy\s*\d{2,4}|h[12])\b", re.IGNORECASE)
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_/-]*")

_SUFFIX_MULTIPLIER = {
    "": 1.0, "k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6,
    "b": 1e9, "billion": 1e9, "t": 1e12, "trillion": 1e12,
}
_CURRENCY = {"$": "usd", "€": "eur", "£": "gbp", "¥": "jpy"}
_POSITIVE_CUES = {
    "raise", "raises", "raised", "increase", "increases", "increased", "higher", "up",
    "rise", "rises", "rose", "gain", "gains", "grew", "growth", "beat", "beats",
    "exceed", "exceeds", "above", "improve", "improves", "improved", "surge", "surges",
}
_NEGATIVE_CUES = {
    "lower", "lowers", "lowered", "cut", "cuts", "decrease", "decreases", "decreased",
    "down", "fall", "falls", "fell", "drop", "drops", "decline", "declines", "declined",
    "miss", "misses", "missed", "below", "worse", "loss", "losses", "sink", "sinks",
}
_ESTIMATE_WORDS = {"estimate", "estimates", "estimated", "consensus", "expected", "expectation", "forecast"}
_GUIDANCE_WORDS = {"guidance", "outlook", "forecast", "projects", "expects", "sees"}
_STOP_CONTEXT = {
    "the", "a", "an", "of", "to", "from", "for", "and", "or", "in", "on", "at", "by",
    "with", "as", "is", "was", "were", "be", "its", "it", "this", "that", "than",
}
_METRICS = (
    ("price_target", {"price target", "target price", "pt"}),
    ("eps", {"eps", "earnings per share", "per-share earnings"}),
    ("revenue", {"revenue", "sales", "turnover"}),
    ("guidance", _GUIDANCE_WORDS),
    ("margin", {"margin", "gross margin", "operating margin"}),
    ("earnings", {"earnings", "income", "profit", "profits", "loss", "losses"}),
    ("cash_flow", {"cash flow", "free cash flow", "fcf"}),
    ("dividend", {"dividend", "distribution"}),
    ("buyback", {"buyback", "repurchase", "authorization"}),
    ("debt", {"debt", "leverage", "liquidity", "borrowing", "loan"}),
    ("valuation", {"valuation", "market cap", "enterprise value"}),
    ("shares", {"shares", "share count", "offering", "float", "dilution"}),
    ("volume", {"volume", "units", "shipments", "deliveries"}),
    ("subscribers", {"subscriber", "subscribers", "users", "customers"}),
    ("contract", {"contract", "order", "award", "backlog", "deal"}),
    ("ownership", {"stake", "ownership", "holding", "position"}),
)


@dataclass(slots=True)
class NumericMention:
    start: int
    end: int
    raw: str
    value: float
    kind: str
    unit: str
    metric: str
    explicit_sign: int
    cue_sign: int
    context_words: tuple[str, ...]


@dataclass(slots=True)
class NumericFeatureRows:
    numeric_ids: list[list[int]]
    numeric_weights: list[list[float]]
    numeric_dense: list[list[float]]


def publication_numeric_text(row: dict[str, Any], *, max_chars: int) -> str:
    parts = [
        str(row.get("title", "") or ""), str(row.get("teaser", "") or ""),
        str(row.get("body_text", "") or ""), str(row.get("external_text", "") or ""),
        str(row.get("pdf_text", "") or ""),
    ]
    return "\n".join(value for value in parts if value.strip())[: max(1, int(max_chars))]


def extract_numeric_batch(rows: list[dict[str, Any]], config: NumericFeatureConfig) -> NumericFeatureRows:
    ids: list[list[int]] = []
    weights: list[list[float]] = []
    dense: list[list[float]] = []
    for row in rows:
        row_ids, row_weights, row_dense = extract_numeric_features(row, config)
        ids.append(row_ids)
        weights.append(row_weights)
        dense.append(row_dense)
    return NumericFeatureRows(ids, weights, dense)


def extract_numeric_features(
    row: dict[str, Any], config: NumericFeatureConfig | None = None,
) -> tuple[list[int], list[float], list[float]]:
    config = config or NumericFeatureConfig()
    text = publication_numeric_text(row, max_chars=config.max_text_chars).replace("−", "-")
    mentions = _mentions(text, config)
    relations = _relations(text)
    tokens: list[tuple[str, float]] = []
    for mention in mentions:
        tokens.extend(_mention_tokens(mention))
    for relation in relations:
        kind, metric, delta, width = relation
        tokens.extend((("relation:" + kind, 1.0), ("relation_metric:" + kind + ":" + metric, 1.0)))
        if delta is not None:
            direction = "positive" if delta > 0 else "negative" if delta < 0 else "flat"
            tokens.append(("relation_direction:" + kind + ":" + direction, 1.0))
            tokens.append(("relation_delta_bin:" + _signed_bin(delta * 100.0), 1.0))
        if width is not None:
            tokens.append(("range_width_bin:" + _magnitude_bin(width * 100.0), 1.0))
    sparse_ids, sparse_weights = _hash_and_normalize(tokens, config.vocabulary_size)
    return sparse_ids, sparse_weights, _dense_features(text, mentions, relations, config.dense_dim)


def _mentions(text: str, config: NumericFeatureConfig) -> list[NumericMention]:
    output: list[NumericMention] = []
    for match in _NUMBER_RE.finditer(text):
        if len(output) >= config.max_mentions:
            break
        raw = match.group(0).strip()
        number = float(match.group("number").replace(",", ""))
        sign_text = (match.group("sign") or "").replace("−", "-")
        explicit_sign = -1 if sign_text == "-" else 1 if sign_text == "+" else 0
        currency = match.group("currency") or ""
        suffix = (match.group("suffix") or "").lower()
        unit_text = (match.group("unit") or "").lower()
        value = number * _SUFFIX_MULTIPLIER.get(suffix, 1.0)
        if explicit_sign < 0:
            value = -value
        if currency:
            kind, unit = "currency", _CURRENCY[currency]
        elif unit_text.startswith("%") or unit_text.startswith("percent"):
            kind, unit = "percent", "percent"
        elif unit_text == "bps" or unit_text.startswith("basis"):
            kind, unit = "bps", "bps"
        elif unit_text in {"x", "times"}:
            kind, unit = "multiple", "x"
        elif not suffix and 1900 <= number <= 2100 and number.is_integer():
            kind, unit = "year", "year"
        else:
            kind, unit = "amount", suffix or "scalar"
        context = text[max(0, match.start() - 120) : min(len(text), match.end() + 120)].lower()
        words = tuple(word.lower() for word in _WORD_RE.findall(context))
        metric = _metric(context)
        cue_sign = _cue_sign(words)
        output.append(NumericMention(
            match.start(), match.end(), raw, value, kind, unit, metric, explicit_sign, cue_sign,
            tuple(word for word in words if word not in _STOP_CONTEXT)[: config.context_words * 2],
        ))
    return output


def _mention_tokens(mention: NumericMention) -> list[tuple[str, float]]:
    sign = mention.explicit_sign or mention.cue_sign
    direction = "positive" if sign > 0 else "negative" if sign < 0 else "unsigned"
    tokens: list[tuple[str, float]] = [
        ("has_numeric", 1.0), ("kind:" + mention.kind, 1.0), ("unit:" + mention.unit, 1.0),
        ("metric:" + mention.metric, 1.0),
        ("kind_metric:" + mention.kind + ":" + mention.metric, 1.0),
        ("direction:" + direction, 1.0),
        ("metric_direction:" + mention.metric + ":" + direction, 1.0),
        ("magnitude:" + mention.kind + ":" + _magnitude_bin(mention.value), 1.0),
    ]
    for word in mention.context_words:
        if not any(character.isdigit() for character in word):
            tokens.append(("context:" + word, 0.5))
            tokens.append(("metric_context:" + mention.metric + ":" + word, 0.5))
    return tokens


def _relations(text: str) -> list[tuple[str, str, float | None, float | None]]:
    output: list[tuple[str, str, float | None, float | None]] = []
    occupied: set[tuple[int, int]] = set()
    for pattern, default_kind in ((_FROM_TO_RE, "from_to"), (_COMPARISON_RE, "comparison")):
        for match in pattern.finditer(text):
            first = _fragment_value(match.group("a"))
            second = _fragment_value(match.group("b"))
            if first is None or second is None or not _comparable(first, second):
                continue
            relation = default_kind
            if "relation" in match.groupdict():
                word = (match.group("relation") or "").lower()
                relation = "beat" if re.search(r"beat|exceed|above", word) else "miss" if re.search(r"miss|below", word) else "versus"
            delta = _relative_delta(first[0], second[0]) if relation != "from_to" else _relative_delta(second[0], first[0])
            context = text[max(0, match.start() - 100) : min(len(text), match.end() + 100)].lower()
            output.append((relation, _metric(context), delta, None))
            occupied.add((match.start(), match.end()))
    for match in _RANGE_RE.finditer(text):
        if any(not (match.end() <= start or match.start() >= end) for start, end in occupied):
            continue
        first = _fragment_value(match.group("a"))
        second = _fragment_value(match.group("b"))
        if first is None or second is None or not _comparable(first, second):
            continue
        midpoint = (abs(first[0]) + abs(second[0])) / 2.0
        width = abs(second[0] - first[0]) / midpoint if midpoint > 1e-12 else 0.0
        context = text[max(0, match.start() - 100) : min(len(text), match.end() + 100)].lower()
        output.append(("range", _metric(context), None, width))
    return output


def _fragment_value(fragment: str) -> tuple[float, str, str] | None:
    match = _NUMBER_RE.search(fragment.replace("−", "-"))
    if match is None:
        return None
    number = float(match.group("number").replace(",", ""))
    sign = -1.0 if (match.group("sign") or "") in {"-", "−"} else 1.0
    suffix = (match.group("suffix") or "").lower()
    value = sign * number * _SUFFIX_MULTIPLIER.get(suffix, 1.0)
    currency = match.group("currency") or ""
    unit_text = (match.group("unit") or "").lower()
    kind = "currency" if currency else "percent" if unit_text.startswith(("%", "percent")) else "bps" if unit_text == "bps" or unit_text.startswith("basis") else "multiple" if unit_text in {"x", "times"} else "amount"
    unit = _CURRENCY.get(currency, unit_text or suffix or "scalar")
    return value, kind, unit


def _comparable(first: tuple[float, str, str], second: tuple[float, str, str]) -> bool:
    return first[1] == second[1] and (first[2] == second[2] or "scalar" in {first[2], second[2]})


def _relative_delta(new: float, reference: float) -> float:
    return (new - reference) / max(abs(reference), 1e-9)


def _metric(context: str) -> str:
    lowered = context.lower()
    for name, terms in _METRICS:
        if any(term in lowered for term in terms):
            return name
    return "other"


def _cue_sign(words: tuple[str, ...]) -> int:
    positive = sum(word in _POSITIVE_CUES for word in words)
    negative = sum(word in _NEGATIVE_CUES for word in words)
    return 1 if positive > negative else -1 if negative > positive else 0


def _magnitude_bin(value: float) -> str:
    absolute = abs(float(value))
    if absolute == 0:
        return "zero"
    boundaries = (0.01, 0.1, 1, 5, 10, 25, 50, 100, 1_000, 1e6, 1e9, 1e12)
    labels = ("lt_0p01", "lt_0p1", "lt_1", "lt_5", "lt_10", "lt_25", "lt_50", "lt_100", "lt_1k", "lt_1m", "lt_1b", "lt_1t")
    for boundary, label in zip(boundaries, labels):
        if absolute < boundary:
            return label
    return "ge_1t"


def _signed_bin(value: float) -> str:
    prefix = "pos" if value > 0 else "neg" if value < 0 else "flat"
    return prefix + ":" + _magnitude_bin(value)


def _hash_and_normalize(tokens: list[tuple[str, float]], vocabulary_size: int) -> tuple[list[int], list[float]]:
    values: defaultdict[int, float] = defaultdict(float)
    for token, weight in tokens:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8, person=b"nr-num-v1").digest()
        values[int.from_bytes(digest, "little") % vocabulary_size] += float(weight)
    if not values:
        return [], []
    ids = sorted(values)
    weights = np.asarray([values[index] for index in ids], dtype=np.float32)
    norm = float(np.linalg.norm(weights))
    if norm > 0:
        weights /= norm
    return ids, weights.tolist()


def _dense_features(
    text: str,
    mentions: list[NumericMention],
    relations: list[tuple[str, str, float | None, float | None]],
    dense_dim: int,
) -> list[float]:
    if dense_dim != len(NUMERIC_DENSE_NAMES):
        raise ValueError(f"numeric_dense_dim must be {len(NUMERIC_DENSE_NAMES)} for {NUMERIC_CONTRACT_VERSION}.")
    count = lambda predicate: sum(1 for mention in mentions if predicate(mention))
    words = tuple(word.lower() for word in _WORD_RE.findall(text))
    magnitudes = [abs(mention.value) for mention in mentions if mention.kind not in {"year"}]
    # Store percentage extrema in percentage-point units. Basis points are
    # converted explicitly (100 bps = 1%) so the dense channel never treats a
    # 75 bps margin move as a 75% move.
    percents = [
        mention.value / 100.0 if mention.kind == "bps" else mention.value
        for mention in mentions if mention.kind in {"percent", "bps"}
    ]
    deltas = [delta for _, _, delta, _ in relations if delta is not None and math.isfinite(delta)]
    widths = [width for _, _, _, width in relations if width is not None and math.isfinite(width)]
    values = [
        float(bool(mentions)), _count_scale(len(mentions)), _count_scale(count(lambda m: m.kind == "currency")),
        _count_scale(count(lambda m: m.kind == "percent")), _count_scale(count(lambda m: m.kind == "bps")),
        _count_scale(count(lambda m: m.kind == "multiple") + text.count(":")),
        _count_scale(count(lambda m: m.explicit_sign > 0)), _count_scale(count(lambda m: m.explicit_sign < 0)),
        _count_scale(count(lambda m: m.cue_sign > 0)), _count_scale(count(lambda m: m.cue_sign < 0)),
        _count_scale(sum(kind == "range" for kind, _, _, _ in relations)),
        _count_scale(sum(kind != "range" for kind, _, _, _ in relations)),
        _count_scale(count(lambda m: m.kind == "year")), _count_scale(len(_QUARTER_RE.findall(text))),
        _count_scale(sum(word in _ESTIMATE_WORDS for word in words)),
        _count_scale(sum(word in _GUIDANCE_WORDS for word in words)),
        _log_scale(float(np.mean(magnitudes)) if magnitudes else 0.0),
        _log_scale(max(magnitudes) if magnitudes else 0.0),
        math.tanh(max([value for value in percents if value > 0], default=0.0) / 25.0),
        math.tanh(min([value for value in percents if value < 0], default=0.0) / 25.0),
        math.tanh(max([value for value in deltas if value > 0], default=0.0) * 2.0),
        math.tanh(min([value for value in deltas if value < 0], default=0.0) * 2.0),
        math.tanh((float(np.mean(deltas)) if deltas else 0.0) * 2.0),
        math.tanh((float(np.mean(widths)) if widths else 0.0) * 2.0),
    ]
    return [float(value) for value in values]


def _count_scale(value: int) -> float:
    return min(1.0, math.log1p(max(0, value)) / math.log(9.0))


def _log_scale(value: float) -> float:
    return math.tanh(math.log1p(max(0.0, value)) / 10.0)


def numeric_contract_payload(config: NumericFeatureConfig) -> dict[str, Any]:
    return {
        "version": NUMERIC_CONTRACT_VERSION,
        "config": asdict(config),
        "dense_names": list(NUMERIC_DENSE_NAMES),
        "metrics": [name for name, _ in _METRICS],
        "relation_types": ["from_to", "beat", "miss", "versus", "range"],
        "hash": "blake2b-64-person-nr-num-v1-mod-vocabulary",
    }


def numeric_contract_sha256(config: NumericFeatureConfig) -> str:
    payload = json.dumps(numeric_contract_payload(config), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_representation_manifest(loader: LoaderConfig, numeric: NumericFeatureConfig) -> dict[str, Any]:
    v5_loader = V5LoaderConfig(feature_artifact_root=loader.v5_feature_artifact_root)
    v5_manifest = load_v5_feature_manifest(v5_loader)
    contract_sha = numeric_contract_sha256(numeric)
    identity = {
        "representation_name": loader.representation_name,
        "dataset_version": loader.dataset_version,
        "source_dataset_version": loader.source_dataset_version,
        "v5_bundle_sha256": v5_manifest["bundle_sha256"],
        "numeric_contract_sha256": contract_sha,
        "numeric_vocabulary_size": numeric.vocabulary_size,
        "numeric_dense_dim": numeric.dense_dim,
    }
    representation_sha = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        **identity,
        "representation_sha256": representation_sha,
        "v5_feature_artifact_root": str(loader.v5_feature_artifact_root),
        "numeric_contract": numeric_contract_payload(numeric),
        "causality": "all lexical and numeric features are derived only from text available at publication time",
    }


def save_representation_manifest(loader: LoaderConfig, manifest: dict[str, Any]) -> Path:
    root = Path(loader.representation_artifact_root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "manifest.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    temporary.replace(path)
    return path


def load_representation_manifest(loader: LoaderConfig) -> dict[str, Any]:
    path = Path(loader.representation_artifact_root) / "manifest.json"
    if not path.exists():
        raise RuntimeError(f"V6 representation manifest is missing at {path}. Run preparation first.")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    expected = build_representation_manifest(loader, NumericFeatureConfig(
        vocabulary_size=loader.numeric_vocab_size,
        dense_dim=loader.numeric_dense_dim,
        max_text_chars=loader.numeric_max_text_chars,
        context_words=loader.numeric_context_words,
        max_mentions=loader.numeric_max_mentions,
    ))
    for key in (
        "representation_name", "dataset_version", "source_dataset_version", "v5_bundle_sha256",
        "numeric_contract_sha256", "numeric_vocabulary_size", "numeric_dense_dim", "representation_sha256",
    ):
        if manifest.get(key) != expected.get(key):
            raise RuntimeError(f"V6 representation manifest mismatch for {key}: {manifest.get(key)!r} != {expected.get(key)!r}.")
    return manifest
