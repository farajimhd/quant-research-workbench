from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class OrbMomentumConfig:
    min_price: float = 5.0
    max_price: float = 100.0
    min_opening_volume: float = 50_000.0
    min_opening_dollar_volume: float = 250_000.0
    min_live_score: float = 55.0
    watchlist_size: int = 100
    max_active_positions: int = 5
    replacement_score_buffer: float = 10.0
    minimum_hold_minutes: int = 10
    opening_box_start_minute: int = 9 * 60 + 30
    opening_box_end_minute: int = 9 * 60 + 35
    entry_cutoff_minute: int = 15 * 60 + 30
    exit_minutes_before_close: int = 5
    entry_buffer_pct: float = 0.0005
    entry_stage_proximity_pct: float = 0.01
    stop_box_pullback_fraction: float = 0.50
    max_entry_extension_pct: float = 0.01
    min_risk_pct: float = 0.0025
    max_risk_pct: float = 0.0075
    max_capital_per_trade_pct: float = 0.15
    cash_reserve_pct: float = 0.05
    tema_entry_buffer_pct: float = 0.0005
    tema_exit_buffer_pct: float = 0.0005

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "OrbMomentumConfig":
        if raw is None:
            return cls()
        allowed = {field: value for field, value in raw.items() if field in cls.__dataclass_fields__}
        return cls(**allowed)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
