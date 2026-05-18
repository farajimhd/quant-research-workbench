from __future__ import annotations

from src.backtest.models import BarContext, OrderRequest
from src.strategies.long_momentum.v3.strategy import LongMomentumV3Strategy
from src.strategies.long_momentum.v7.config import LongMomentumV7Config
from src.strategies.long_momentum.v7.presentation import chart_presentation


class LongMomentumV7Strategy(LongMomentumV3Strategy):
    name = "long_momentum"

    def __init__(self, config: LongMomentumV7Config | None = None):
        super().__init__(config or LongMomentumV7Config())
        self.config: LongMomentumV7Config

    def chart_presentation(self) -> dict:
        return chart_presentation()

    def _entry_request(self, candidate: dict, context: BarContext, available_cash: float) -> OrderRequest | None:
        request = super()._entry_request(candidate, context, available_cash)
        if request is None:
            return None
        request.reason = "LONG_MOMENTUM_V7"
        request.tag = str(request.tag).replace("LONG_MOMENTUM_V3", "LONG_MOMENTUM_V7")
        return request

    def _trace_entry(self, timestamp, candidate: dict, quantity: int, entry_price: float, stop_price: float) -> None:
        super()._trace_entry(timestamp, candidate, quantity, entry_price, stop_price)
        if self.signal_events:
            self.signal_events[-1]["strategy_version"] = "v7"


__all__ = ["LongMomentumV7Strategy"]
