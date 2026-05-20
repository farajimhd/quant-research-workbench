from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.strategies.long_momentum.v9.config import LongMomentumV9Config, V9_PARAMETER_FIELDS


V10_PARAMETER_FIELDS = tuple(
    dict.fromkeys(
        (
            *V9_PARAMETER_FIELDS,
            "enable_high_break_hold_entry",
            "enable_vwap_reclaim_entry",
            "high_break_take_profit_pct",
        )
    )
)


@dataclass(slots=True)
class LongMomentumV10Config(LongMomentumV9Config):
    enable_high_break_hold_entry: bool = True
    enable_vwap_reclaim_entry: bool = False
    high_break_take_profit_pct: float = 0.15

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "LongMomentumV10Config":
        if raw is None:
            return cls()
        raw = dict(raw)
        if "max_high_break_hold_candidates_per_bar" not in raw and "max_first_entry_candidates_per_bar" in raw:
            raw["max_high_break_hold_candidates_per_bar"] = raw["max_first_entry_candidates_per_bar"]
        if "max_high_break_hold_candidates_per_bar" not in raw and "max_immediate_entry_candidates_per_bar" in raw:
            raw["max_high_break_hold_candidates_per_bar"] = raw["max_immediate_entry_candidates_per_bar"]
        allowed = {field: value for field, value in raw.items() if field in cls.__dataclass_fields__}
        return cls(**allowed)

    def to_dict(self) -> dict[str, Any]:
        return {field: getattr(self, field) for field in V10_PARAMETER_FIELDS}
