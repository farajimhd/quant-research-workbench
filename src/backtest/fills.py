from __future__ import annotations

from src.backtest.models import Order


class BarFillModel:
    """Phase 1 fill approximation from OHLC minute bars."""

    def crossed(self, order: Order, bar: dict) -> bool:
        if order.order_type == "STOP" and order.side == "BUY" and order.stop_price is not None:
            return float(bar["high"]) >= order.stop_price
        if order.order_type == "STOP" and order.side == "SELL" and order.stop_price is not None:
            return float(bar["low"]) <= order.stop_price
        if order.order_type == "LIMIT" and order.side == "BUY" and order.limit_price is not None:
            return float(bar["low"]) <= order.limit_price
        if order.order_type == "LIMIT" and order.side == "SELL" and order.limit_price is not None:
            return float(bar["high"]) >= order.limit_price
        return False

    def fill_price(self, order: Order, bar: dict, slippage_bps: float) -> float:
        if order.order_type == "MARKET":
            base_price = float(bar["close"])
        elif order.order_type == "STOP" and order.stop_price is not None:
            base_price = float(order.stop_price)
        elif order.order_type == "LIMIT" and order.limit_price is not None:
            base_price = float(order.limit_price)
        else:
            base_price = float(bar["close"])

        slip = slippage_bps / 10_000.0
        return base_price * (1.0 + slip) if order.side == "BUY" else base_price * (1.0 - slip)
