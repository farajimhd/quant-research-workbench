from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class OrbMomentumConfig:
    min_universe_price: float = 0.75
    min_price: float = 5.0
    max_price: float = 50.0
    min_daily_dollar_volume: float = 2_000_000.0
    max_float_or_shares: float = 500_000_000.0
    max_universe_size: int = 500
    min_avg_daily_volume: float = 1_000_000.0
    min_atr: float = 0.50
    daily_lookback_days: int = 30

    relative_volume_daily_share: float = 0.02
    min_opening_relative_volume: float = 1.0
    max_candidates: int = 5

    opening_box_start_minute: int = 9 * 60 + 31
    opening_box_end_minute: int = 9 * 60 + 35
    entry_cutoff_minute: int = 16 * 60 - 10
    exit_minutes_before_close: int = 5
    cancel_unfilled_minutes_before_close: int = 10

    entry_buffer_pct: float = 0.0005
    atr_stop_fraction: float = 0.20
    min_gap_up_pct: float = 0.005
    min_close_location: float = 0.75
    min_body_to_range: float = 0.35
    min_orb_range_atr_fraction: float = 0.05
    max_orb_range_atr_fraction: float = 0.50

    min_position_value: float = 500.0
    min_planned_risk_dollars: float = 12.0
    cash_reserve_pct: float = 0.05

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "OrbMomentumConfig":
        if raw is None:
            return cls()
        allowed = {field: value for field, value in raw.items() if field in cls.__dataclass_fields__}
        return cls(**allowed)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
