from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class BreakOfVwapConfig:
    min_price: float = 3.0
    max_price: float = 80.0
    trading_start_minute: int = 9 * 60 + 45
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
        "FXI",
        "EEM",
        "EWZ",
        "IWM",
        "QQQ",
        "SPY",
        "UVIX",
        "SVIX",
    )

    max_active_positions: int = 2
    max_new_entries_per_bar: int = 1
    max_daily_entries: int = 4
    cooldown_minutes: int = 75
    min_hold_minutes: int = 10
    max_hold_minutes: int = 45

    min_dollar_volume_sma20: float = 500_000.0
    min_relative_dollar_volume20: float = 1.1
    min_close_location: float = 0.65
    min_break_return_bps: float = 3.0
    min_prior_vwap_bps: float = -250.0
    max_prior_vwap_bps: float = 0.0
    min_break_vwap_bps: float = 3.0
    max_break_vwap_bps: float = 120.0
    min_day_return_bps: float = -400.0
    max_day_return_bps: float = 500.0
    min_ret5_bps: float = -120.0
    max_ret15_bps: float = 350.0
    min_macd_hist_delta_bps: float = 0.10
    min_tema_spread_delta_bps: float = -0.50
    min_scanner_score: float = 78.0
    min_expected_edge_bps: float = 34.0
    estimated_round_trip_cost_bps: float = 25.0

    risk_per_trade_pct: float = 0.004
    max_capital_per_trade_pct: float = 0.30
    cash_reserve_pct: float = 0.05
    min_position_notional: float = 2_000.0
    initial_risk_pct: float = 0.010
    min_initial_risk_dollars: float = 0.03
    max_initial_risk_pct: float = 0.025
    stop_vwap_buffer_bps: float = 18.0
    trailing_activation_r: float = 1.4
    trailing_lock_r: float = 0.20
    trailing_giveback_r: float = 0.95
    vwap_failure_bps: float = -25.0
    failure_return_bps: float = 18.0

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "BreakOfVwapConfig":
        if raw is None:
            return cls()
        allowed = {field: value for field, value in raw.items() if field in cls.__dataclass_fields__}
        return cls(**allowed)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
