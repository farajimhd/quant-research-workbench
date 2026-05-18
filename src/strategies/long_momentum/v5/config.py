from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class LongMomentumV5Config:
    min_price: float = 1.0
    max_price: float = 10.0
    trading_start_minute: int = 4 * 60
    trading_end_minute: int = 20 * 60

    min_volume: float = 10_000.0
    min_transactions: float = 100.0
    min_macd_hist_z_since_open: float = 0.5
    min_recent_dollar_volume_5: float = 100_000.0
    max_spread_bps_abs: float = 100.0
    max_spread_bps_max: float = 150.0
    min_quote_valid_ratio: float = 0.8
    max_locked_or_crossed_count: float = 0.0
    max_spread_below_5: float = 0.02
    max_spread_5_to_10: float = 0.05
    enable_early_uptrend_entry: bool = True
    entry_minute_start: int = 8 * 60
    entry_minute_end: int = 10 * 60
    entry_late_minute_start: int = 15 * 60
    entry_late_minute_end: int = 20 * 60
    setup_valid_bars: int = 3
    min_body_break_bps: float = 10.0
    min_tema_spread_pct: float = 0.005
    min_volume_vs_avg_so_far: float = 1.5
    min_volume_vs_recent_3: float = 0.75
    min_close_location: float = 0.55
    max_entry_below_day_high_pct: float = 0.06
    near_day_high_chase_pct: float = 0.005
    fresh_day_high_break_bps: float = 5.0
    max_distance_above_vwap_pct: float = 0.08
    max_distance_from_day_low_pct: float = 0.35
    max_open_above_last_close_pct: float = 0.03
    max_last_bar_range_pct: float = 0.12
    max_initial_risk_pct: float = 0.08
    max_bearish_divergence_entry_score: float = 50.0

    exit_watch_bearish_divergence_score: float = 50.0
    exit_definite_bearish_divergence_score: float = 90.0
    breakeven_activation_r: float = 1.0
    structural_trail_activation_r: float = 1.5
    vwap_stop_buffer_pct: float = 0.003
    trend_failure_requires_profit: bool = True
    cash_buffer_dollars: float = 5.0
    sizing_fee_per_share: float = 0.005
    sizing_min_fee: float = 1.0
    risk_per_trade_pct: float = 0.005
    stop_offset_dollars: float = 0.01

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "LongMomentumV5Config":
        if raw is None:
            return cls()
        allowed = {field: value for field, value in raw.items() if field in cls.__dataclass_fields__}
        return cls(**allowed)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

