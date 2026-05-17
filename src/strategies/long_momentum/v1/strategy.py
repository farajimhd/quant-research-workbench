from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import polars as pl

from src.backtest.data.minute_bars import DayFrames
from src.backtest.models import BarContext, DataRequirements, Order, OrderRequest
from src.backtest.observability import ObservabilityRecorder
from src.backtest.portfolio import Portfolio
from src.strategies.long_momentum.v1.config import LongMomentumConfig
from src.strategies.long_momentum.v1.presentation import chart_presentation


@dataclass
class LongMomentumSymbolState:
    ticker: str
    last_timestamp: datetime | None = None
    row: dict[str, Any] = field(default_factory=dict)


class LongMomentumStrategy:
    name = "long_momentum"

    def __init__(self, config: LongMomentumConfig | None = None):
        self.config = config or LongMomentumConfig()
        self.session_date = None
        self.states: dict[str, LongMomentumSymbolState] = {}
        self.entry_order_metadata: dict[str, dict] = {}
        self.position_meta: dict[str, dict] = {}
        self.live_rankings: list[dict] = []
        self.signal_events: list[dict] = []
        self.rejection_events: list[dict] = []
        self.scanner_snapshots: list[dict] = []
        self.observability: ObservabilityRecorder | None = None

    def set_observability(self, observability: ObservabilityRecorder) -> None:
        self.observability = observability

    def data_requirements(self) -> DataRequirements:
        return DataRequirements(
            event_timeframe="1m",
            feature_groups=("core", "momentum", "session", "volume_liquidity"),
            required_columns=(
                "ticker",
                "bar_time_market",
                "minute_of_day",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "transactions",
                "spread",
            ),
        )

    def chart_presentation(self) -> dict:
        return chart_presentation()

    def prepare_day(self, frames: DayFrames, portfolio: Portfolio) -> pl.DataFrame:
        self.session_date = frames.session_date
        self.states = {}
        self.entry_order_metadata = {}
        self.position_meta = {}
        return frames.event_frame.filter(
            (pl.col("minute_of_day") >= self.config.trading_start_minute)
            & (pl.col("minute_of_day") < self.config.trading_end_minute)
        )

    def on_bar(self, context: BarContext, portfolio: Portfolio, pending_orders: list[Order]) -> list[OrderRequest]:
        self._update_states(context)
        rows = self._scanner_rows(context, portfolio, pending_orders)
        candidates = [row for row in rows if row["entry_open"]]
        self._record_scanner(context, rows, candidates, portfolio, pending_orders)

        stale_entry_cancels = self._cancel_stale_entry_requests(pending_orders)
        active_pending_orders = [
            order
            for order in pending_orders
            if not self._is_pending_entry_order(order)
        ]
        requests = stale_entry_cancels + self._exit_requests(context, portfolio, active_pending_orders)
        pending_symbols = {order.symbol for order in active_pending_orders if order.status == "OPEN"}
        exiting_symbols = {request.symbol for request in requests if request.side == "SELL"}
        entry_request = self._entry_or_rotation_request(
            context=context,
            portfolio=portfolio,
            candidates=candidates,
            pending_symbols=pending_symbols,
            exiting_symbols=exiting_symbols,
            requests=requests,
        )
        if entry_request is not None:
            requests.append(entry_request)
        return requests

    def _cancel_stale_entry_requests(self, pending_orders: list[Order]) -> list[OrderRequest]:
        return [
            OrderRequest(
                symbol=order.symbol,
                side=order.side,
                quantity=order.quantity,
                order_type="CANCEL",
                reason="ENTRY_EXPIRED",
                tag=f"CANCEL|reason=ENTRY_EXPIRED|order_id={order.order_id}",
            )
            for order in pending_orders
            if self._is_pending_entry_order(order)
        ]

    def _is_pending_entry_order(self, order: Order) -> bool:
        return (
            order.status == "OPEN"
            and order.side == "BUY"
            and order.order_type == "STOP"
            and order.reason == "LONG_MOMENTUM"
        )

    def on_day_end(self, timestamp: datetime, portfolio: Portfolio) -> list[OrderRequest]:
        return [
            OrderRequest(
                symbol=symbol,
                side="SELL",
                quantity=position.quantity,
                order_type="MARKET",
                reason="EOD",
                tag=f"EXIT|reason=EOD|held_symbol={symbol}",
            )
            for symbol, position in list(portfolio.positions.items())
        ]

    def entry_metadata(self, order: Order) -> dict:
        return self.entry_order_metadata.get(order.symbol, {})

    def artifacts(self) -> dict[str, list[dict]]:
        return {
            "scanner_snapshots": self.scanner_snapshots,
            "live_rankings": self.live_rankings,
            "signal_events": self.signal_events,
            "rejection_events": self.rejection_events,
        }

    def _update_states(self, context: BarContext) -> None:
        for raw in context.updates.iter_rows(named=True):
            ticker = str(raw["ticker"])
            row = dict(raw)
            state = self.states.get(ticker)
            if state is None:
                state = LongMomentumSymbolState(ticker=ticker)
                self.states[ticker] = state
            state.last_timestamp = context.timestamp
            state.row = row

    def _scanner_rows(self, context: BarContext, portfolio: Portfolio, pending_orders: list[Order]) -> list[dict]:
        if context.updates.is_empty():
            return []

        pending_symbols = {order.symbol for order in pending_orders if order.status == "OPEN"}
        frame = context.updates.with_columns(
            pl.col("last_close").cast(pl.Float64).alias("_lm_last_close"),
            pl.col("last_open").cast(pl.Float64).alias("_lm_last_open"),
            pl.col("current_open").cast(pl.Float64).alias("_lm_current_open"),
            pl.col("last_volume").cast(pl.Float64).alias("_lm_last_volume"),
            pl.col("last_transactions").cast(pl.Float64).alias("_lm_last_transactions"),
            pl.col("last_spread").cast(pl.Float64).alias("_lm_last_spread"),
            pl.col("last_return_1").cast(pl.Float64).fill_null(0.0).alias("_lm_last_return_1"),
            pl.col("last_macd_line").cast(pl.Float64).alias("_lm_last_macd_line"),
            pl.col("last_macd_hist_z_since_open").cast(pl.Float64).alias("_lm_last_macd_hist_z"),
            pl.col("last_tema9").cast(pl.Float64).alias("_lm_last_tema9"),
            pl.col("last_tema20").cast(pl.Float64).alias("_lm_last_tema20"),
        ).with_columns(
            (pl.col("_lm_last_close") < pl.col("_lm_last_open")).alias("_lm_last_is_red"),
            (pl.col("_lm_last_return_1") > 0).alias("_lm_last_close_above_previous"),
            (
                pl.when(pl.col("_lm_last_close") < 5.0)
                .then(pl.col("_lm_last_spread") <= self.config.max_spread_below_5)
                .otherwise(pl.col("_lm_last_spread") <= self.config.max_spread_5_to_10)
            ).alias("_lm_spread_ok"),
        ).with_columns(
            (
                (pl.col("_lm_last_close") >= self.config.min_price)
                & (pl.col("_lm_last_close") <= self.config.max_price)
                & (pl.col("_lm_last_volume") >= self.config.min_volume)
                & (pl.col("_lm_last_transactions") >= self.config.min_transactions)
                & (~pl.col("_lm_last_is_red"))
                & pl.col("_lm_last_close_above_previous")
                & (pl.col("_lm_last_tema9") > pl.col("_lm_last_tema20"))
                & (pl.col("_lm_last_macd_line") > 0)
                & (pl.col("_lm_last_macd_hist_z") >= self.config.min_macd_hist_z_since_open)
                & pl.col("_lm_spread_ok")
            ).fill_null(False).alias("entry_open"),
            pl.col("_lm_spread_ok").fill_null(False).alias("long_momentum_spread_ok"),
            (pl.col("_lm_last_return_1") * 10_000.0).alias("return_1_bps"),
            (pl.col("_lm_last_return_1") * 10_000.0).alias("scanner_score"),
        ).with_columns(
            pl.col("entry_open").alias("long_momentum_entry_open"),
        )

        rows = frame.sort(["entry_open", "return_1_bps", "last_macd_hist_z_since_open"], descending=[True, True, True]).to_dicts()
        for rank, row in enumerate(rows, start=1):
            ticker = str(row["ticker"])
            row["timestamp"] = context.timestamp
            row["session_date"] = self.session_date.isoformat() if self.session_date else ""
            row["ticker"] = ticker
            row["price"] = self._float(row.get("last_close"))
            row["held_quantity"] = portfolio.positions[ticker].quantity if ticker in portfolio.positions else 0
            row["open_positions"] = len(portfolio.positions)
            row["rank"] = rank
            row["status"] = self._scanner_status(row, ticker, portfolio, pending_symbols)
            row["entry_state"] = "entry_open" if row["entry_open"] else self._entry_block_reason(row)

        entry_rank = 0
        for row in rows:
            if row["entry_open"]:
                entry_rank += 1
                row["entry_rank"] = entry_rank
            else:
                row["entry_rank"] = None
        return rows

    def _scanner_status(self, row: dict, ticker: str, portfolio: Portfolio, pending_symbols: set[str]) -> str:
        if ticker in portfolio.positions:
            return "held"
        if ticker in pending_symbols:
            return "pending"
        return "eligible" if row.get("entry_open") else "blocked"

    def _entry_block_reason(self, row: dict) -> str:
        close = self._float(row.get("last_close"))
        if close < self.config.min_price:
            return "price_low"
        if close > self.config.max_price:
            return "price_high"
        if self._float(row.get("last_volume")) < self.config.min_volume:
            return "volume"
        if self._float(row.get("last_transactions")) < self.config.min_transactions:
            return "transactions"
        if bool(row.get("_lm_last_is_red") if "_lm_last_is_red" in row else row.get("last_is_red")):
            return "red_candle"
        if not bool(row.get("_lm_last_close_above_previous")):
            return "close_not_above_previous"
        if self._float(row.get("last_tema9")) <= self._float(row.get("last_tema20")):
            return "tema_closed"
        if self._float(row.get("last_macd_line")) <= 0:
            return "macd_line"
        if self._float(row.get("last_macd_hist_z_since_open")) < self.config.min_macd_hist_z_since_open:
            return "macd_hist_z"
        if not bool(row.get("_lm_spread_ok")):
            return "spread"
        return "filtered"

    def _entry_or_rotation_request(
        self,
        *,
        context: BarContext,
        portfolio: Portfolio,
        candidates: list[dict],
        pending_symbols: set[str],
        exiting_symbols: set[str],
        requests: list[OrderRequest],
    ) -> OrderRequest | None:
        if not candidates:
            return None
        candidate = candidates[0]
        symbol = str(candidate["ticker"])
        if symbol in pending_symbols or symbol in exiting_symbols:
            return None

        active_positions = [item for item in portfolio.positions.items() if item[0] not in exiting_symbols]
        if active_positions:
            held_symbol, position = active_positions[0]
            if position.entry_time == context.timestamp:
                return None
            if symbol == held_symbol:
                return None
            held_bar = context.updates_by_symbol.get(held_symbol)
            if held_bar is None:
                return None
            held_profit_bps = ((self._float(held_bar.get("last_close")) / position.entry_price) - 1.0) * 10_000.0 if position.entry_price > 0 else 0.0
            if self._float(candidate.get("return_1_bps")) <= held_profit_bps:
                return None
            requests.append(
                OrderRequest(
                    symbol=held_symbol,
                    side="SELL",
                    quantity=position.quantity,
                    order_type="MARKET",
                    reason="ROTATE_OUT",
                    tag=self._exit_tag("ROTATE_OUT", position, held_bar, self._position_meta(held_symbol, position)),
                )
            )
            exiting_symbols.add(held_symbol)
        expected_cash = portfolio.cash + self._expected_sale_value(exiting_symbols, portfolio, context)
        return self._entry_request(candidate, context, portfolio, expected_cash)

    def _entry_request(self, candidate: dict, context: BarContext, portfolio: Portfolio, expected_cash: float) -> OrderRequest | None:
        symbol = str(candidate["ticker"])
        trigger_price, stop_price, initial_r = self._entry_levels(candidate)
        quantity = self._entry_quantity(trigger_price, expected_cash)
        if quantity <= 0:
            self._reject(context.timestamp, symbol, "quantity", candidate)
            return None
        raw_max_fill_qty = candidate.get("last_max_fill_qty")
        max_fill_qty = int(self._float(raw_max_fill_qty))
        if raw_max_fill_qty is not None:
            if max_fill_qty <= 0:
                self._reject(context.timestamp, symbol, "no_quote_liquidity", candidate | {"quantity": quantity, "last_max_fill_qty": max_fill_qty})
                return None
            if quantity > max_fill_qty:
                self._reject(context.timestamp, symbol, "liquidity_capacity", candidate | {"quantity": quantity, "last_max_fill_qty": max_fill_qty})
                return None
        self.entry_order_metadata[symbol] = {
            "setup_rank": int(candidate.get("rank") or 0),
            "live_rank": int(candidate.get("entry_rank") or candidate.get("rank") or 0),
            "setup_score": self._float(candidate.get("scanner_score")),
            "live_score": self._float(candidate.get("scanner_score")),
            "stop_price": stop_price,
            "trigger_price": trigger_price,
            "stop_offset_dollars": self.config.min_initial_risk_dollars,
        }
        self.position_meta[symbol] = {
            "initial_r": initial_r,
            "initial_stop": stop_price,
            "entry_score": self._float(candidate.get("scanner_score")),
        }
        self._trace_entry(context.timestamp, candidate, quantity, trigger_price, stop_price, initial_r)
        return OrderRequest(
            symbol=symbol,
            side="BUY",
            quantity=quantity,
            order_type="STOP",
            reason="LONG_MOMENTUM",
            stop_price=trigger_price,
            allow_same_bar_fill=True,
            tag=(
                f"ENTRY|rule=LONG_MOMENTUM|rank={candidate.get('entry_rank') or candidate.get('rank')}"
                f"|qty={quantity}|trigger={trigger_price:.2f}|stop={stop_price:.2f}|R={initial_r:.4f}"
                f"|current_open={self._float(candidate.get('current_open')):.2f}|last_close={self._float(candidate.get('last_close')):.2f}"
                f"|ret1={self._float(candidate.get('return_1_bps')):.1f}|macdz={self._float(candidate.get('last_macd_hist_z_since_open')):.2f}"
                f"|spread={self._float(candidate.get('last_spread')):.4f}|maxfill={max_fill_qty}"
            ),
        )

    def _entry_quantity(self, price: float, expected_cash: float) -> int:
        if price <= 0 or expected_cash <= self.config.cash_buffer_dollars:
            return 0
        fill_price_estimate = price * (1.0 + max(0.0, self.config.sizing_slippage_bps) / 10_000.0)
        per_share_cost = fill_price_estimate + max(0.0, self.config.sizing_fee_per_share)
        budget = max(0.0, expected_cash - self.config.cash_buffer_dollars)
        quantity = int((budget - self.config.sizing_min_fee) / per_share_cost) if per_share_cost > 0 else 0
        while quantity > 0:
            estimated_fee = max(self.config.sizing_min_fee, quantity * self.config.sizing_fee_per_share)
            if quantity * fill_price_estimate + estimated_fee <= budget:
                return quantity
            quantity -= 1
        return 0

    def _exit_requests(self, context: BarContext, portfolio: Portfolio, pending_orders: list[Order]) -> list[OrderRequest]:
        requests = []
        pending_sell_symbols = {order.symbol for order in pending_orders if order.status == "OPEN" and order.side == "SELL"}
        for symbol, position in list(portfolio.positions.items()):
            if symbol in pending_sell_symbols:
                continue
            bar = context.updates_by_symbol.get(symbol)
            if bar is None:
                continue
            meta = self._position_meta(symbol, position)
            if position.entry_time == context.timestamp:
                continue
            active_stop = position.stop_price

            if self._tema_closed(bar):
                self._trace_exit(context.timestamp, symbol, "TEMA_CLOSE", position, bar, meta)
                requests.append(
                    OrderRequest(
                        symbol=symbol,
                        side="SELL",
                        quantity=position.quantity,
                        order_type="MARKET",
                        reason="TEMA_CLOSE",
                        tag=self._exit_tag("TEMA_CLOSE", position, bar, meta),
                    )
                )
                continue

            requests.extend(
                [
                    OrderRequest(
                        symbol=symbol,
                        side="SELL",
                        quantity=position.quantity,
                        order_type="STOP",
                        reason="INITIAL_STOP",
                        stop_price=active_stop,
                        tag=self._exit_tag("INITIAL_STOP", position, bar, meta),
                        allow_same_bar_fill=True,
                        expire_on_bar_close=True,
                    ),
                    OrderRequest(
                        symbol=symbol,
                        side="SELL",
                        quantity=position.quantity,
                        order_type="LIMIT",
                        reason="TAKE_PROFIT",
                        limit_price=self._take_profit_price(position),
                        tag=self._exit_tag("TAKE_PROFIT", position, bar, meta),
                        allow_same_bar_fill=True,
                        expire_on_bar_close=True,
                    ),
                ]
            )
        return requests

    def _entry_levels(self, candidate: dict) -> tuple[float, float, float]:
        open_price = self._float(candidate.get("current_open"))
        close = self._float(candidate.get("last_close"))
        trigger = max(open_price, close)
        stop = trigger - self.config.min_initial_risk_dollars
        if trigger <= 0:
            return 0.0, 0.0, 0.0
        stop = max(0.01, stop)
        return trigger, stop, max(self.config.min_initial_risk_dollars, trigger - stop)

    def _tema_closed(self, bar: dict) -> bool:
        close = self._float(bar.get("last_close"))
        return (
            bar.get("last_tema9") is not None
            and bar.get("last_tema20") is not None
            and self._float(bar.get("last_tema9")) < self._float(bar.get("last_tema20")) + (close * self.config.tema_exit_offset_pct)
        )

    def _take_profit_price(self, position) -> float:
        return position.entry_price * (1.0 + self.config.take_profit_pct)

    def _position_meta(self, symbol: str, position) -> dict:
        meta = self.position_meta.get(symbol)
        if meta is None:
            risk = max(self.config.min_initial_risk_dollars, abs(position.entry_price - position.stop_price))
            meta = {
                "initial_r": risk,
                "initial_stop": position.stop_price,
                "entry_score": position.live_score,
            }
            self.position_meta[symbol] = meta
        elif self._float(meta.get("initial_stop")) != position.stop_price:
            meta["initial_stop"] = position.stop_price
            meta["initial_r"] = max(self.config.min_initial_risk_dollars, abs(position.entry_price - position.stop_price))
        return meta

    def _open_r_multiple(self, position, close: float, meta: dict) -> float:
        initial_r = self._float(meta.get("initial_r")) or abs(position.entry_price - position.stop_price)
        return (close - position.entry_price) / initial_r if initial_r > 0 else 0.0

    def _expected_sale_value(self, exiting_symbols: set[str], portfolio: Portfolio, context: BarContext) -> float:
        total = 0.0
        for symbol in exiting_symbols:
            position = portfolio.positions.get(symbol)
            bar = context.latest_by_symbol.get(symbol)
            if position is not None and bar is not None:
                total += position.quantity * self._float(bar.get("last_close")) * 0.98
        return total

    def _record_scanner(
        self,
        context: BarContext,
        rows: list[dict],
        candidates: list[dict],
        portfolio: Portfolio,
        pending_orders: list[Order],
    ) -> None:
        captured = rows[:25]
        self.live_rankings.extend(captured)
        self.scanner_snapshots.append(
            {
                "timestamp": context.timestamp,
                "session_date": self.session_date.isoformat() if self.session_date else "",
                "candidate_count": len(candidates),
                "scanned_count": len(rows),
                "selected_count": 1 if candidates else 0,
            }
        )
        if not self.observability or not rows:
            return
        self.observability.scanner(timestamp=context.timestamp, rows=rows, score_key="scanner_score", stage="long_momentum_scanner")
        self.observability.state(
            timestamp=context.timestamp,
            scope="strategy",
            state={
                "scanned_count": len(rows),
                "entry_open_count": len(candidates),
                "open_positions": len(portfolio.positions),
                "pending_orders": len([order for order in pending_orders if order.status == "OPEN"]),
            },
        )

    def _trace_entry(self, timestamp: datetime, candidate: dict, quantity: int, trigger_price: float, stop_price: float, initial_r: float) -> None:
        self.signal_events.append(
            {
                "timestamp": timestamp,
                "ticker": candidate["ticker"],
                "event": "ENTRY_INTENT",
                "rank": candidate.get("entry_rank") or candidate.get("rank"),
                "return_1_bps": candidate.get("return_1_bps"),
                "quantity": quantity,
                "trigger": trigger_price,
                "stop": stop_price,
            }
        )
        if not self.observability:
            return
        self.observability.trace(
            timestamp=timestamp,
            ticker=str(candidate["ticker"]),
            stage="order_request",
            event_type="entry_intent",
            decision="submit_order",
            reason_code="LONG_MOMENTUM",
            reason="Top eligible long momentum scanner candidate",
            values={
                "quantity": quantity,
                "current_open": candidate.get("current_open"),
                "last_close": candidate.get("last_close"),
                "trigger": trigger_price,
                "stop": stop_price,
                "initial_r": initial_r,
                "return_1_bps": candidate.get("return_1_bps"),
                "last_macd_hist_z_since_open": candidate.get("last_macd_hist_z_since_open"),
                "last_spread": candidate.get("last_spread"),
                "rank": candidate.get("entry_rank") or candidate.get("rank"),
            },
            force=self._force_trade_trace(),
        )

    def _trace_exit(self, timestamp: datetime, symbol: str, reason: str, position, bar: dict, meta: dict) -> None:
        if not self.observability:
            return
        close = self._float(bar.get("last_close"))
        self.observability.trace(
            timestamp=timestamp,
            ticker=symbol,
            stage="exit_evaluation",
            event_type="exit_intent",
            decision="exit",
            reason_code=reason,
            reason=f"Exit condition {reason} triggered",
            values={
                "quantity": position.quantity,
                "price": close,
                "entry_price": position.entry_price,
                "initial_stop": meta.get("initial_stop"),
                "initial_r": meta.get("initial_r"),
                "open_r": self._open_r_multiple(position, close, meta),
            },
            force=self._force_trade_trace(),
        )

    def _reject(self, timestamp: datetime, symbol: str, reason: str, candidate: dict) -> None:
        self.rejection_events.append(
            {
                "timestamp": timestamp,
                "ticker": symbol,
                "reject_reason": reason,
                "return_1_bps": candidate.get("return_1_bps"),
                "rank": candidate.get("entry_rank") or candidate.get("rank"),
            }
        )

    def _exit_tag(self, reason: str, position, bar: dict | None, meta: dict) -> str:
        price = self._float(bar.get("last_close")) if bar is not None else position.entry_price
        stop = self._float(meta.get("initial_stop")) or position.stop_price
        return (
            f"EXIT|reason={reason}|price={price:.2f}|stop={stop:.2f}"
            f"|R={self._float(meta.get('initial_r')):.4f}|maxp={position.max_price:.2f}"
            f"|maxR={position.max_r_multiple:.2f}"
        )

    def _float(self, value) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _force_trade_trace(self) -> bool:
        return bool(self.observability and self.observability.config.observability_always_trace_trades)
