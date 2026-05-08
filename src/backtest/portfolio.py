from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from src.backtest.models import Order, Position, Trade


class Portfolio:
    def __init__(self, initial_cash: float):
        self.initial_cash = float(initial_cash)
        self.cash = float(initial_cash)
        self.positions: dict[str, Position] = {}
        self.trades: list[Trade] = []

    def total_equity(self, bars_by_symbol: dict[str, dict] | None = None) -> float:
        equity = self.cash
        if not bars_by_symbol:
            return equity + sum(pos.quantity * pos.entry_price for pos in self.positions.values())

        for symbol, position in self.positions.items():
            bar = bars_by_symbol.get(symbol)
            mark = float(bar["close"]) if bar is not None else position.entry_price
            equity += position.quantity * mark
        return equity

    def position_value(self, symbol: str, bars_by_symbol: dict[str, dict]) -> float:
        position = self.positions.get(symbol)
        if position is None:
            return 0.0
        bar = bars_by_symbol.get(symbol)
        mark = float(bar["close"]) if bar is not None else position.entry_price
        return position.quantity * mark

    def can_afford(self, price: float, quantity: int) -> bool:
        return self.cash >= price * quantity

    def open_position(
        self,
        order: Order,
        setup_rank: int,
        live_rank: int,
        setup_score: float,
        live_score: float,
        stop_price: float,
    ) -> None:
        if order.fill_price is None or order.filled_at is None:
            raise ValueError("Cannot open position from an unfilled order")

        cost = order.fill_price * order.quantity
        self.cash -= cost
        self.positions[order.symbol] = Position(
            symbol=order.symbol,
            quantity=order.quantity,
            entry_time=order.filled_at,
            entry_price=order.fill_price,
            stop_price=stop_price,
            entry_order_id=order.order_id,
            setup_rank=setup_rank,
            live_rank=live_rank,
            setup_score=setup_score,
            live_score=live_score,
            max_price=order.fill_price,
            min_price=order.fill_price,
        )

    def close_position(self, order: Order) -> Trade | None:
        position = self.positions.get(order.symbol)
        if position is None or order.fill_price is None or order.filled_at is None:
            return None

        quantity = min(position.quantity, abs(order.quantity))
        proceeds = order.fill_price * quantity
        self.cash += proceeds
        pnl = (order.fill_price - position.entry_price) * quantity
        return_pct = (order.fill_price / position.entry_price) - 1.0 if position.entry_price > 0 else 0.0

        trade = Trade(
            symbol=order.symbol,
            entry_time=position.entry_time,
            exit_time=order.filled_at,
            quantity=quantity,
            entry_price=position.entry_price,
            exit_price=order.fill_price,
            pnl=pnl,
            return_pct=return_pct,
            exit_reason=order.reason,
            max_unrealized_profit=position.max_unrealized_profit,
            max_r_multiple=position.max_r_multiple,
            mae=position.max_adverse_excursion,
            mfe=position.max_unrealized_profit,
            end_trade_drawdown=position.max_unrealized_profit - max(pnl, 0.0),
        )
        self.trades.append(trade)

        remaining = position.quantity - quantity
        if remaining <= 0:
            del self.positions[order.symbol]
        else:
            position.quantity = remaining

        return trade

    def update_peaks(self, bars_by_symbol: dict[str, dict]) -> None:
        for symbol, position in self.positions.items():
            bar = bars_by_symbol.get(symbol)
            if bar is None:
                continue
            position.max_price = max(position.max_price, float(bar["high"]))
            position.min_price = min(position.min_price, float(bar["low"]))
            max_profit_per_share = max(0.0, position.max_price - position.entry_price)
            position.max_unrealized_profit = max_profit_per_share * position.quantity
            max_loss_per_share = min(0.0, position.min_price - position.entry_price)
            position.max_adverse_excursion = max_loss_per_share * position.quantity
            risk_per_share = abs(position.entry_price - position.stop_price)
            if risk_per_share > 0:
                position.max_r_multiple = max_profit_per_share / risk_per_share

    def snapshot_rows(self, timestamp: datetime, bars_by_symbol: dict[str, dict]) -> list[dict]:
        rows = []
        for position in self.positions.values():
            bar = bars_by_symbol.get(position.symbol)
            mark = float(bar["close"]) if bar is not None else position.entry_price
            row = asdict(position)
            row.update(
                {
                    "timestamp": timestamp,
                    "mark_price": mark,
                    "unrealized_pnl": (mark - position.entry_price) * position.quantity,
                    "market_value": mark * position.quantity,
                }
            )
            rows.append(row)
        return rows
