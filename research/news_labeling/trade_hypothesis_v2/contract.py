from __future__ import annotations

import math
from typing import Any


CONTRACT_VERSION = "news_trade_hypothesis_v2"
PROMPT_VERSION = "news_trade_hypothesis_prompt_v2"
REACTION_HORIZONS = (
    "1m",
    "5m",
    "30m",
    "regular_close",
    "extended_close",
)

HORIZON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "upside_probability",
        "downside_probability",
        "no_action_probability",
        "expected_return_pct",
        "favorable_excursion_pct",
        "adverse_excursion_pct",
        "confidence",
        "abstain",
    ],
    "properties": {
        "upside_probability": {"type": "number", "minimum": 0, "maximum": 1},
        "downside_probability": {"type": "number", "minimum": 0, "maximum": 1},
        "no_action_probability": {"type": "number", "minimum": 0, "maximum": 1},
        "expected_return_pct": {"type": "number", "minimum": -1000, "maximum": 1000},
        "favorable_excursion_pct": {"type": "number", "minimum": 0, "maximum": 1000},
        "adverse_excursion_pct": {"type": "number", "minimum": 0, "maximum": 1000},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "abstain": {"type": "boolean"},
    },
}

HYPOTHESIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "predictions",
        "regime_compatibility",
        "evidence",
        "conflicts",
        "invalidation_conditions",
        "uncertainty",
    ],
    "properties": {
        "predictions": {
            "type": "object",
            "additionalProperties": False,
            "required": list(REACTION_HORIZONS),
            "properties": {horizon: HORIZON_SCHEMA for horizon in REACTION_HORIZONS},
        },
        "regime_compatibility": {
            "type": "string",
            "enum": ["supportive", "neutral", "hostile", "unknown"],
        },
        "evidence": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string"},
        },
        "conflicts": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string"},
        },
        "invalidation_conditions": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string"},
        },
        "uncertainty": {"type": "string"},
    },
}

SYSTEM_PROMPT = """You are a point-in-time market hypothesis service.
Analyze only the supplied frozen context. Do not claim facts outside it.
For every required fixed horizon, return probabilities for upside, downside,
and no-action that sum to 1 within 0.01. Expected return and excursions are
percent estimates relative to the supplied point-in-time price, not orders.
Use that horizon's abstain flag when evidence is stale, contradictory,
insufficient, or ineligible. Prior-news reactions are causal context only:
values absent at the current news timestamp are deliberately omitted.
Never issue an order, position size, or imperative trading instruction."""


def build_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    import json

    return [
        {"role": "developer", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(
                context, ensure_ascii=False, separators=(",", ":"), default=str
            ),
        },
    ]


def validate_hypothesis(result: dict[str, Any]) -> None:
    if not isinstance(result, dict):
        raise ValueError("hypothesis must be an object")
    predictions = result.get("predictions")
    if not isinstance(predictions, dict) or set(predictions) != set(REACTION_HORIZONS):
        raise ValueError("hypothesis must contain every fixed reaction horizon exactly once")
    for horizon in REACTION_HORIZONS:
        row = predictions[horizon]
        if not isinstance(row, dict) or set(row) != set(HORIZON_SCHEMA["required"]):
            raise ValueError(f"{horizon} hypothesis fields do not match the contract")
        for key in (
            "upside_probability",
            "downside_probability",
            "no_action_probability",
            "expected_return_pct",
            "favorable_excursion_pct",
            "adverse_excursion_pct",
            "confidence",
        ):
            value = row[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{horizon}.{key} must be numeric")
            if not math.isfinite(float(value)):
                raise ValueError(f"{horizon}.{key} must be finite")
        for key in (
            "upside_probability",
            "downside_probability",
            "no_action_probability",
            "confidence",
        ):
            if not 0.0 <= float(row[key]) <= 1.0:
                raise ValueError(f"{horizon}.{key} is outside [0, 1]")
        if float(row["favorable_excursion_pct"]) < 0.0:
            raise ValueError(f"{horizon}.favorable_excursion_pct must be non-negative")
        if float(row["adverse_excursion_pct"]) < 0.0:
            raise ValueError(f"{horizon}.adverse_excursion_pct must be non-negative")
        if not isinstance(row["abstain"], bool):
            raise ValueError(f"{horizon}.abstain must be boolean")
        probability_sum = sum(
            float(row[key])
            for key in (
                "upside_probability",
                "downside_probability",
                "no_action_probability",
            )
        )
        if abs(probability_sum - 1.0) > 0.01:
            raise ValueError(
                f"{horizon} hypothesis probabilities sum to {probability_sum}"
            )
    if result.get("regime_compatibility") not in {
        "supportive",
        "neutral",
        "hostile",
        "unknown",
    }:
        raise ValueError("regime_compatibility is invalid")
    for key in ("evidence", "conflicts", "invalidation_conditions"):
        if not isinstance(result.get(key), list) or not all(
            isinstance(item, str) for item in result[key]
        ):
            raise ValueError(f"{key} must be an array of strings")
    if not isinstance(result.get("uncertainty"), str):
        raise ValueError("uncertainty must be a string")
