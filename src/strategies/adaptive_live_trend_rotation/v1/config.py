from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class AdaptiveLiveTrendRotationConfig:
    min_price: float = 2.0
    max_price: float = 150.0
    trading_start_minute: int = 4 * 60
    trading_end_minute: int = 20 * 60

    top_n: int = 5
    max_active_positions: int = 5
    max_gross_exposure_pct: float = 0.95
    cash_reserve_pct: float = 0.03
    rank_decay: float = 0.45

    min_session_dollar_volume: float = 500_000.0
    min_recent_dollar_volume: float = 100_000.0
    min_recent_volume: float = 10_000.0
    min_recent_transactions: int = 25
    liquidity_window_minutes: int = 5

    recent_return_lookback_minutes: int = 15
    min_recent_return_bps: float = 5.0
    min_momentum_score: float = 25.0
    require_price_above_vwap: bool = True
    max_vwap_extension_bps: float = 500.0

    session_return_weight: float = 0.25
    recent_return_weight: float = 0.30
    macd_pressure_weight: float = 0.20
    tema_spread_weight: float = 0.15
    volume_weight: float = 0.10
    overextension_penalty_weight: float = 0.50

    tema_entry_buffer_pct: float = 0.0005
    tema_exit_buffer_pct: float = 0.0005
    replacement_score_buffer: float = 30.0
    rotation_min_hold_minutes: int = 5
    non_progress_score_decay: float = 20.0
    min_progress_r_after_hold: float = 0.10

    risk_per_trade_pct: float = 0.0075
    initial_risk_pct: float = 0.0060
    min_initial_risk_dollars: float = 0.03
    max_initial_risk_pct: float = 0.03
    trailing_activation_r: float = 1.0
    trailing_lock_r: float = 0.20
    trailing_giveback_r: float = 0.75

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "AdaptiveLiveTrendRotationConfig":
        if raw is None:
            return cls()
        allowed = {field: value for field, value in raw.items() if field in cls.__dataclass_fields__}
        return cls(**allowed)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
