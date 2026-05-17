from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class LongMomentumConfig:
    min_price: float = 1.0
    max_price: float = 10.0
    trading_start_minute: int = 4 * 60
    trading_end_minute: int = 20 * 60

    min_volume: float = 10_000.0
    min_transactions: float = 100.0
    min_macd_hist_z_since_open: float = 0.1
    max_spread_below_5: float = 0.02
    max_spread_5_to_10: float = 0.05

    cash_buffer_dollars: float = 5.0
    sizing_slippage_bps: float = 0.0
    sizing_fee_per_share: float = 0.005
    sizing_min_fee: float = 1.0
    min_initial_risk_dollars: float = 0.01

    tema_exit_offset_pct: float = 0.0
    take_profit_pct: float = 0.10

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "LongMomentumConfig":
        if raw is None:
            return cls()
        allowed = {field: value for field, value in raw.items() if field in cls.__dataclass_fields__}
        return cls(**allowed)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
