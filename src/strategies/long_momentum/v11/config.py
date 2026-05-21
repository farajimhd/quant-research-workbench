from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.strategies.long_momentum.v9.config import LongMomentumV9Config, V9_PARAMETER_FIELDS


V11_PARAMETER_FIELDS = tuple(
    dict.fromkeys(
        (
            *V9_PARAMETER_FIELDS,
            "min_last_5m_return",
            "min_pop_transaction_ratio",
            "min_entry_transaction_ratio",
            "pop_entry_stop_offset_dollars",
            "pop_entry_limit_offset_dollars",
            "entry_expire_bars",
            "max_pop_breakout_candidates_per_bar",
            "max_entry_extension_above_pop_high_pct",
            "vwap_trail_offset_pct",
            "vwap_slope_down_bars",
            "vwap_distance_giveback_pct",
            "min_vwap_distance_for_giveback_pct",
        )
    )
)


@dataclass(slots=True)
class LongMomentumV11Config(LongMomentumV9Config):
    min_last_5m_return: float = 0.08
    min_pop_transaction_ratio: float = 20.0
    min_entry_transaction_ratio: float = 10.0
    pop_entry_stop_offset_dollars: float = 0.01
    pop_entry_limit_offset_dollars: float = 0.01
    entry_expire_bars: int = 3
    max_pop_breakout_candidates_per_bar: int = 50
    vwap_stop_offset_pct: float = 1.0
    max_entry_extension_above_pop_high_pct: float = 0.03
    vwap_trail_offset_pct: float = 0.5
    vwap_slope_down_bars: int = 2
    vwap_distance_giveback_pct: float = 0.40
    min_vwap_distance_for_giveback_pct: float = 0.04

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "LongMomentumV11Config":
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
        return {field: getattr(self, field) for field in V11_PARAMETER_FIELDS}
