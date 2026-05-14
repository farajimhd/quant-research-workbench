from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class LiquidityPullbackReversalConfig:
    min_price: float = 3.0
    max_price: float = 80.0
    trading_start_minute: int = 10 * 60 + 30
    trading_end_minute: int = 15 * 60 + 30
    excluded_symbols: tuple[str, ...] = (
        "SOXL",
        "SOXS",
        "NVDL",
        "NVDS",
        "TQQQ",
        "SQQQ",
        "SPXL",
        "SPXS",
        "UPRO",
        "SPXU",
        "TNA",
        "TZA",
        "TECL",
        "TECS",
        "LABU",
        "LABD",
        "TMF",
        "TMV",
        "IBIT",
        "USO",
        "BITO",
        "GLD",
        "SLV",
        "XLE",
        "XLF",
        "XLK",
        "XLU",
        "XLV",
        "IWM",
        "QQQ",
        "SPY",
        "UVIX",
        "SVIX",
    )

    max_active_positions: int = 2
    max_new_entries_per_bar: int = 1
    max_daily_entries: int = 3
    cooldown_minutes: int = 90
    min_hold_minutes: int = 15
    max_hold_minutes: int = 45

    min_dollar_volume_sma20: float = 500_000.0
    min_relative_dollar_volume20: float = 1.0
    min_volume_z20: float = -0.5
    min_close_location: float = 0.60
    min_reversal_return_bps: float = 0.0
    min_macd_hist_delta_bps: float = 0.15
    min_tema_spread_delta_bps: float = -1.0

    min_vwap_bps: float = -250.0
    max_vwap_bps: float = 25.0
    min_day_return_bps: float = -500.0
    max_day_return_bps: float = 300.0
    min_ret15_bps: float = -350.0
    max_ret15_bps: float = 100.0
    max_weak_tema_spread_bps: float = 5.0
    max_weak_macd_hist_bps: float = 5.0

    min_scanner_score: float = 85.0
    min_expected_edge_bps: float = 40.0
    estimated_round_trip_cost_bps: float = 25.0

    risk_per_trade_pct: float = 0.004
    max_capital_per_trade_pct: float = 0.30
    cash_reserve_pct: float = 0.05
    min_position_notional: float = 2_000.0
    initial_risk_pct: float = 0.012
    min_initial_risk_dollars: float = 0.03
    max_initial_risk_pct: float = 0.025
    trailing_activation_r: float = 1.5
    trailing_lock_r: float = 0.20
    trailing_giveback_r: float = 1.0
    failure_return_bps: float = 20.0

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "LiquidityPullbackReversalConfig":
        if raw is None:
            return cls()
        allowed = {field: value for field, value in raw.items() if field in cls.__dataclass_fields__}
        return cls(**allowed)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
