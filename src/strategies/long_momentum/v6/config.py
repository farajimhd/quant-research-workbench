from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class LongMomentumV6Config:
    min_price: float = 1.0
    max_price: float = 10.0
    trading_start_minute: int = 4 * 60
    trading_end_minute: int = 20 * 60

    max_active_positions: int = 1
    cash_buffer_dollars: float = 5.0
    capital_fraction_per_trade: float = 0.34
    sizing_fee_per_share: float = 0.005
    sizing_min_fee: float = 1.0
    min_entry_capacity: int = 1

    min_oracle_entry_score: float = 65.0
    min_oracle_expected_profit: float = 0.015
    max_oracle_drawdown_before_best: float = 0.08
    min_oracle_exit_score: float = 45.0
    min_oracle_exit_realized_profit: float = 0.008
    short_supervision_exit_score: float = 75.0

    max_spread_below_5: float = 0.02
    max_spread_5_to_10: float = 0.05
    require_spread_ok: bool = True
    require_positive_expected_profit_after_fees: bool = True

    max_initial_risk_pct: float = 0.08
    stop_offset_dollars: float = 0.01
    breakeven_activation_return: float = 0.04
    trail_activation_return: float = 0.08
    trail_buffer_pct: float = 0.01

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "LongMomentumV6Config":
        if raw is None:
            return cls()
        allowed = {field: value for field, value in raw.items() if field in cls.__dataclass_fields__}
        return cls(**allowed)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
