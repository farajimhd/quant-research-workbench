from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from src.strategies.long_momentum.v7.config import LongMomentumV7Config


@dataclass(slots=True)
class LongMomentumV8Config(LongMomentumV7Config):
    min_price: float = 1.0
    max_price: float = 10.0
    min_volume: float = 8_000.0
    min_transactions: float = 100.0
    min_recent_dollar_volume_5: float = 75_000.0
    min_macd_hist_z_since_open: float = 0.0
    min_close_location: float = 0.45
    max_spread_bps_abs: float = 150.0
    max_spread_bps_max: float = 225.0
    min_quote_valid_ratio: float = 0.75

    require_news_time_window: bool = True
    news_time_window_minutes: int = 5
    include_half_hour_news_window: bool = True
    entry_start_minute: int = 4 * 60
    entry_end_minute: int = 9 * 60 + 30
    max_entries_per_day: int = 20
    max_entries_per_symbol_per_day: int = 1

    min_seed_price_shock_score: float = 0.55
    min_seed_combined_shock_score: float = 0.45
    min_seed_close_location: float = 0.55
    min_shock_entry_delay_minutes: int = 2
    max_shock_watch_minutes: int = 12

    min_liquidity_volume_shock_score: float = 0.45
    min_liquidity_combined_shock_score: float = 0.55
    min_volume_vs_avg_so_far: float = 1.20
    min_volume_vs_recent_3: float = 0.55
    min_price_acceptance_above_midpoint_pct: float = -0.01
    max_distance_above_vwap_pct: float = 0.12
    max_distance_from_shock_midpoint_pct: float = 0.28
    max_bearish_divergence_entry_score: float = 75.0

    min_reclaim_bps: float = 5.0
    min_entry_score: float = 78.0
    max_initial_risk_pct: float = 0.08
    vwap_stop_buffer_pct: float = 0.003

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "LongMomentumV8Config":
        if raw is None:
            return cls()
        allowed = {field: value for field, value in raw.items() if field in cls.__dataclass_fields__}
        return cls(**allowed)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
