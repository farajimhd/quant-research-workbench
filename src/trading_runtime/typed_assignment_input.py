"""Inactive closed input contract for typed long-momentum assignment state v1.

The grouped-resistance source is `vwap_resistance_ladder.levels`, which first
projects V7 evidence to ten named fields. These checks are deliberately not
installed on the legacy route and do not authorize typed persistence yet.
"""
from __future__ import annotations

from math import isfinite
from typing import Any, Mapping


STRUCTURAL_LEVEL_CONTRACT = "long-momentum-grouped-resistance-level-v1"
CALLER_STATE_CONTRACT = "long-momentum-caller-campaign-state-v1"
_LEVEL_KEYS = frozenset({
    "unified_level_id", "lower", "upper", "price", "side", "role",
    "confirmed_at_ms", "book_version", "input_policy", "seed_input_policy",
})
_REQUIRED_LEVEL = frozenset({
    "unified_level_id", "lower", "upper", "confirmed_at_ms", "book_version",
})
_CALLER_STATE_KEYS = frozenset({
    "campaign_id", "campaign_deployment_id", "campaign_profile_id",
    "campaign_book_id", "campaign_universe_id", "campaign_side",
})


def validate_grouped_resistance_level(row: Mapping[str, Any]) -> dict[str, Any]:
    """Validate exactly the projected source row before typed state admission."""
    if not isinstance(row, Mapping):
        raise ValueError("structural level must be a mapping")
    keys = set(row)
    if keys - _LEVEL_KEYS or _REQUIRED_LEVEL - keys:
        raise ValueError("structural level has missing or unmodeled fields")
    if not isinstance(row["unified_level_id"], str) or not row["unified_level_id"]:
        raise ValueError("structural level identity is invalid")
    if row["book_version"] != "causal-level-book-v7-mle-1":
        raise ValueError("structural level book version is invalid")
    for key in ("lower", "upper", "confirmed_at_ms", "price"):
        if key in row and (type(row[key]) not in (int, float) or not isfinite(row[key])):
            raise ValueError(f"structural level {key} must be finite numeric")
    if not 0 < row["lower"] <= row["upper"] or row["confirmed_at_ms"] <= 0:
        raise ValueError("structural level bounds or clock are invalid")
    if "price" in row and not row["lower"] <= row["price"] <= row["upper"]:
        raise ValueError("structural level price is outside its band")
    if "side" in row and (type(row["side"]) is not int or row["side"] not in (-1, 0, 1)):
        raise ValueError("structural level side is invalid")
    for key in ("role", "input_policy", "seed_input_policy"):
        if key in row and (not isinstance(row[key], str) or not row[key]):
            raise ValueError(f"structural level {key} is invalid")
    return dict(row)


def validate_typed_caller_assignment_state(state: Mapping[str, Any] | None) -> dict[str, str]:
    """Allow only caller-supplied campaign identity, not arbitrary state trees."""
    if state is None:
        return {}
    if not isinstance(state, Mapping):
        raise ValueError("typed caller assignment state must be a mapping")
    if set(state) - _CALLER_STATE_KEYS:
        raise ValueError("typed caller assignment state has unmodeled fields")
    for key, value in state.items():
        if not isinstance(value, str) or (key == "campaign_side" and value not in {"long", "short"}):
            raise ValueError(f"typed caller assignment state {key} is invalid")
    return dict(state)
