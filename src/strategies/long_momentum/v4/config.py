from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class LongMomentumV4Config:
    min_price: float = 1.0
    max_price: float = 10.0
    trading_start_minute: int = 4 * 60
    trading_end_minute: int = 20 * 60

    min_volume: float = 10_000.0
    min_transactions: float = 100.0
    min_macd_hist_z_since_open: float = 0.1
    min_recent_dollar_volume_5: float = 100_000.0
    max_spread_bps_abs: float = 100.0
    max_spread_bps_max: float = 150.0
    min_quote_valid_ratio: float = 0.8
    max_locked_or_crossed_count: float = 0.0
    max_spread_below_5: float = 0.02
    max_spread_5_to_10: float = 0.05
    enable_entry_trigger_1_earlier_body_break: bool = True
    enable_entry_trigger_2_pullback_reclaim: bool = True
    pullback_reclaim_valid_bars: int = 6
    max_bearish_divergence_entry_score: float = 75.0

    exit_watch_bearish_divergence_score: float = 50.0
    exit_definite_bearish_divergence_score: float = 90.0
    cash_buffer_dollars: float = 5.0
    sizing_fee_per_share: float = 0.005
    sizing_min_fee: float = 1.0
    stop_offset_dollars: float = 0.01

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "LongMomentumV4Config":
        if raw is None:
            return cls()
        allowed = {field: value for field, value in raw.items() if field in cls.__dataclass_fields__}
        return cls(**allowed)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
