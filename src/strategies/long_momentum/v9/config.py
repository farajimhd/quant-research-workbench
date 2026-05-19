from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from src.strategies.long_momentum.v3.config import LongMomentumV3Config


@dataclass(slots=True)
class LongMomentumV9Config(LongMomentumV3Config):
    min_price: float = 1.0
    max_price: float = 10.0
    trading_start_minute: int = 4 * 60
    trading_end_minute: int = 20 * 60

    min_last_5m_return: float = 0.05
    min_first_entry_transactions: float = 100.0
    min_first_entry_transactions_vs_prior_3: float = 20.0
    max_risk_fraction_of_cash: float = 0.25
    double_bvd_exit_score: float = 50.0
    profit_giveback_exit_pct: float = 0.10
    tema9_exit_buffer_pct: float = -0.01
    vwap_stop_offset_pct: float = 3.0
    partial_fill_reprice_offset: float = 0.01

    max_immediate_entry_candidates_per_bar: int = 50
    max_reentry_candidates_per_bar: int = 50
    watchlist_snapshot_limit: int = 250

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "LongMomentumV9Config":
        if raw is None:
            return cls()
        allowed = {field: value for field, value in raw.items() if field in cls.__dataclass_fields__}
        return cls(**allowed)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
