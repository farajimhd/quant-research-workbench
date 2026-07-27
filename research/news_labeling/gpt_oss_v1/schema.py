from __future__ import annotations

import copy
import math
from typing import Any

from .taxonomy import (
    CONTENT_ROLES,
    DIRECTIONS,
    EVENT_FAMILY_CODES,
    EVENT_SUBTYPES,
    IMPACT_HORIZONS,
    ISSUER_RELATIONSHIPS,
    MODALITIES,
    NOVELTY,
    ORIGINS,
    OVERALL_SENTIMENT,
    QUALITY_FLAGS,
    SENTIMENT_DIMENSIONS,
    SENTIMENT_LABELS,
    TIME_ORIENTATIONS,
)


TRANSPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["source", "events", "sentiment", "novelty", "quality", "evidence"],
    "properties": {
        "source": {
            "type": "object",
            "additionalProperties": False,
            "required": ["origin", "role", "issuer_relationship", "company_announcement", "confidence"],
            "properties": {
                "origin": {"type": "string", "enum": list(ORIGINS)},
                "role": {"type": "string", "enum": list(CONTENT_ROLES)},
                "issuer_relationship": {"type": "string", "enum": list(ISSUER_RELATIONSHIPS)},
                "company_announcement": {"type": "boolean"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
        },
        "events": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["family", "subtype", "direction", "intensity", "time", "modality", "confidence"],
                "properties": {
                    "family": {"type": "string", "enum": list(EVENT_FAMILY_CODES)},
                    "subtype": {"type": "string"},
                    "direction": {"type": "string", "enum": list(DIRECTIONS)},
                    "intensity": {"type": "integer", "minimum": 0, "maximum": 3},
                    "time": {"type": "string", "enum": list(TIME_ORIENTATIONS)},
                    "modality": {"type": "string", "enum": list(MODALITIES)},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
            },
        },
        "sentiment": {
            "type": "object",
            "additionalProperties": False,
            "required": ["overall", "score", "confidence", "dimensions"],
            "properties": {
                "overall": {"type": "string", "enum": list(OVERALL_SENTIMENT)},
                "score": {"type": "integer", "minimum": -100, "maximum": 100},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "dimensions": {
                    "type": "array",
                    "minItems": len(SENTIMENT_DIMENSIONS),
                    "maxItems": len(SENTIMENT_DIMENSIONS),
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["name", "label", "intensity"],
                        "properties": {
                            "name": {"type": "string", "enum": list(SENTIMENT_DIMENSIONS)},
                            "label": {"type": "string", "enum": list(SENTIMENT_LABELS)},
                            "intensity": {"type": "integer", "minimum": 0, "maximum": 3},
                        },
                    },
                },
            },
        },
        "novelty": {
            "type": "object",
            "additionalProperties": False,
            "required": ["class", "impact_horizon"],
            "properties": {
                "class": {"type": "string", "enum": list(NOVELTY)},
                "impact_horizon": {"type": "string", "enum": list(IMPACT_HORIZONS)},
            },
        },
        "quality": {
            "type": "array",
            "uniqueItems": True,
            "items": {"type": "string", "enum": list(QUALITY_FLAGS)},
        },
        "evidence": {
            "type": "array",
            "minItems": 1,
            "maxItems": 6,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["supports", "quote"],
                "properties": {
                    "supports": {"type": "string"},
                    "quote": {"type": "string", "minLength": 1, "maxLength": 180},
                },
            },
        },
    },
}

# vLLM compiles ``response_format.json_schema`` into a guided-decoding
# grammar. Its grammar backend does not implement ``uniqueItems``. Preserve
# that invariant in the canonical contract above and in ``validate_label``
# below, while sending only grammar-supported constraints over the wire.
_VLLM_UNSUPPORTED_SCHEMA_KEYS = frozenset({"uniqueItems"})


def vllm_transport_schema() -> dict[str, Any]:
    schema = copy.deepcopy(TRANSPORT_SCHEMA)
    _remove_schema_keys(schema, _VLLM_UNSUPPORTED_SCHEMA_KEYS)
    return schema


def _remove_schema_keys(value: Any, keys: frozenset[str]) -> None:
    if isinstance(value, dict):
        for key in tuple(value):
            if key in keys:
                del value[key]
            else:
                _remove_schema_keys(value[key], keys)
    elif isinstance(value, list):
        for item in value:
            _remove_schema_keys(item, keys)


VLLM_TRANSPORT_SCHEMA = vllm_transport_schema()


def validate_label(value: Any, supplied_text: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return ["label must be an object"]
    required = set(TRANSPORT_SCHEMA["required"])
    if set(value) != required:
        errors.append(f"top-level keys must be exactly {sorted(required)}")

    source = value.get("source")
    if not isinstance(source, dict):
        errors.append("source must be an object")
    else:
        _enum(errors, "source.origin", source.get("origin"), ORIGINS)
        _enum(errors, "source.role", source.get("role"), CONTENT_ROLES)
        _enum(errors, "source.issuer_relationship", source.get("issuer_relationship"), ISSUER_RELATIONSHIPS)
        if not isinstance(source.get("company_announcement"), bool):
            errors.append("source.company_announcement must be boolean")
        _probability(errors, "source.confidence", source.get("confidence"))
        if source.get("company_announcement") and source.get("issuer_relationship") not in {
            "direct_announcement", "reported_issuer_event",
        }:
            errors.append("company_announcement requires direct or reported issuer-event relationship")

    events = value.get("events")
    if not isinstance(events, list) or len(events) > 8:
        errors.append("events must be an array with at most eight items")
    else:
        for index, event in enumerate(events):
            prefix = f"events[{index}]"
            if not isinstance(event, dict):
                errors.append(f"{prefix} must be an object")
                continue
            family = event.get("family")
            _enum(errors, f"{prefix}.family", family, EVENT_FAMILY_CODES)
            if family in EVENT_SUBTYPES and event.get("subtype") not in EVENT_SUBTYPES[family]:
                errors.append(f"{prefix}.subtype is invalid for family {family}")
            _enum(errors, f"{prefix}.direction", event.get("direction"), DIRECTIONS)
            _integer_range(errors, f"{prefix}.intensity", event.get("intensity"), 0, 3)
            _enum(errors, f"{prefix}.time", event.get("time"), TIME_ORIENTATIONS)
            _enum(errors, f"{prefix}.modality", event.get("modality"), MODALITIES)
            _probability(errors, f"{prefix}.confidence", event.get("confidence"))

    sentiment = value.get("sentiment")
    if not isinstance(sentiment, dict):
        errors.append("sentiment must be an object")
    else:
        _enum(errors, "sentiment.overall", sentiment.get("overall"), OVERALL_SENTIMENT)
        _integer_range(errors, "sentiment.score", sentiment.get("score"), -100, 100)
        _probability(errors, "sentiment.confidence", sentiment.get("confidence"))
        dimensions = sentiment.get("dimensions")
        if not isinstance(dimensions, list):
            errors.append("sentiment.dimensions must be an array")
        else:
            names: list[str] = []
            for index, item in enumerate(dimensions):
                if not isinstance(item, dict):
                    errors.append(f"sentiment.dimensions[{index}] must be an object")
                    continue
                names.append(str(item.get("name")))
                _enum(errors, f"sentiment.dimensions[{index}].name", item.get("name"), SENTIMENT_DIMENSIONS)
                _enum(errors, f"sentiment.dimensions[{index}].label", item.get("label"), SENTIMENT_LABELS)
                _integer_range(errors, f"sentiment.dimensions[{index}].intensity", item.get("intensity"), 0, 3)
            if len(names) != len(set(names)):
                errors.append("sentiment dimensions must be unique")
            if set(names) != set(SENTIMENT_DIMENSIONS):
                errors.append("sentiment dimensions must contain every defined dimension exactly once")

    novelty = value.get("novelty")
    if not isinstance(novelty, dict):
        errors.append("novelty must be an object")
    else:
        _enum(errors, "novelty.class", novelty.get("class"), NOVELTY)
        _enum(errors, "novelty.impact_horizon", novelty.get("impact_horizon"), IMPACT_HORIZONS)

    quality = value.get("quality")
    if not isinstance(quality, list):
        errors.append("quality must be an array")
    else:
        for item in quality:
            _enum(errors, "quality[]", item, QUALITY_FLAGS)
        if len(quality) != len(set(quality)):
            errors.append("quality flags must be unique")

    evidence = value.get("evidence")
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 6:
        errors.append("evidence must be an array with one to six items")
    else:
        haystack = " ".join(supplied_text.casefold().split())
        for index, item in enumerate(evidence):
            if not isinstance(item, dict):
                errors.append(f"evidence[{index}] must be an object")
                continue
            quote = str(item.get("quote") or "").strip()
            if not quote or len(quote) > 180:
                errors.append(f"evidence[{index}].quote must contain 1-180 characters")
            elif " ".join(quote.casefold().split()) not in haystack:
                errors.append(f"evidence[{index}].quote is not verbatim source text")
            if not str(item.get("supports") or "").strip():
                errors.append(f"evidence[{index}].supports is empty")
    return errors


def _enum(errors: list[str], name: str, value: Any, allowed: tuple[str, ...]) -> None:
    if value not in allowed:
        errors.append(f"{name} must be one of {allowed}")


def _probability(errors: list[str], name: str, value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
        errors.append(f"{name} must be a finite number in [0,1]")


def _integer_range(errors: list[str], name: str, value: Any, minimum: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        errors.append(f"{name} must be an integer in [{minimum},{maximum}]")
