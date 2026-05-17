from __future__ import annotations

from collections import deque
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
    last_red_low: float | None = None
    recent_green_bodies: deque[float] = field(default_factory=lambda: deque(maxlen=6))
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

        requests = self._exit_requests(context, portfolio)
        pending_symbols = {order.symbol for order in pending_orders if order.status == "OPEN"}
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
            open_price = self._float(row.get("open"))
            close = self._float(row.get("close"))
            low = self._float(row.get("low"))
            body = abs(close - open_price)
            if close > open_price:
                state.recent_green_bodies.append(body)
            elif close < open_price:
                state.last_red_low = low
            state.last_timestamp = context.timestamp
            state.row = row

    def _scanner_rows(self, context: BarContext, portfolio: Portfolio, pending_orders: list[Order]) -> list[dict]:
        if context.updates.is_empty():
            return []

        pending_symbols = {order.symbol for order in pending_orders if order.status == "OPEN"}
        frame = context.updates.with_columns(
            pl.col("close").cast(pl.Float64).alias("_lm_close"),
            pl.col("open").cast(pl.Float64).alias("_lm_open"),
            pl.col("volume").cast(pl.Float64).alias("_lm_volume"),
            pl.col("transactions").cast(pl.Float64).alias("_lm_transactions"),
            pl.col("spread").cast(pl.Float64).alias("_lm_spread"),
            pl.col("return_1").cast(pl.Float64).fill_null(0.0).alias("_lm_return_1"),
            pl.col("macd_line").cast(pl.Float64).alias("_lm_macd_line"),
            pl.col("macd_signal").cast(pl.Float64).alias("_lm_macd_signal"),
            pl.col("macd_hist_z_since_open").cast(pl.Float64).alias("_lm_macd_hist_z"),
            pl.col("tema9").cast(pl.Float64).alias("_lm_tema9"),
            pl.col("tema20").cast(pl.Float64).alias("_lm_tema20"),
        ).with_columns(
            (pl.col("_lm_close") > pl.col("_lm_open")).alias("_lm_is_green"),
            (
                pl.when(pl.col("_lm_close") < 5.0)
                .then(pl.col("_lm_spread") <= self.config.max_spread_below_5)
                .otherwise(pl.col("_lm_spread") <= self.config.max_spread_5_to_10)
            ).alias("_lm_spread_ok"),
        ).with_columns(
            (
                (pl.col("_lm_close") >= self.config.min_price)
                & (pl.col("_lm_close") <= self.config.max_price)
                & (pl.col("_lm_volume") >= self.config.min_volume)
                & (pl.col("_lm_transactions") >= self.config.min_transactions)
                & pl.col("_lm_is_green")
                & (pl.col("_lm_tema9") > pl.col("_lm_tema20"))
                & (pl.col("_lm_macd_line") > 0)
                & (pl.col("_lm_macd_signal") > 0)
                & (pl.col("_lm_macd_hist_z") >= self.config.min_macd_hist_z_since_open)
                & pl.col("_lm_spread_ok")
            ).fill_null(False).alias("entry_open"),
            (pl.col("_lm_return_1") * 10_000.0).alias("return_1_bps"),
            (pl.col("_lm_return_1") * 10_000.0).alias("scanner_score"),
        )

        rows = frame.sort(["entry_open", "return_1_bps", "macd_hist_z_since_open"], descending=[True, True, True]).to_dicts()
        for rank, row in enumerate(rows, start=1):
            ticker = str(row["ticker"])
            row["timestamp"] = context.timestamp
            row["session_date"] = self.session_date.isoformat() if self.session_date else ""
            row["ticker"] = ticker
            row["price"] = self._float(row.get("close"))
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
        close = self._float(row.get("close"))
        if close < self.config.min_price:
            return "price_low"
        if close > self.config.max_price:
            return "price_high"
        if self._float(row.get("volume")) < self.config.min_volume:
            return "volume"
        if self._float(row.get("transactions")) < self.config.min_transactions:
            return "transactions"
        if not bool(row.get("_lm_is_green") if "_lm_is_green" in row else row.get("is_green")):
            return "red_candle"
        if self._float(row.get("tema9")) <= self._float(row.get("tema20")):
            return "tema_closed"
        if self._float(row.get("macd_line")) <= 0:
            return "macd_line"
        if self._float(row.get("macd_signal")) <= 0:
            return "macd_signal"
        if self._float(row.get("macd_hist_z_since_open")) < self.config.min_macd_hist_z_since_open:
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
            if symbol == held_symbol:
                return None
            held_bar = context.updates_by_symbol.get(held_symbol)
            if held_bar is None:
                return None
            held_profit_bps = ((self._float(held_bar.get("close")) / position.entry_price) - 1.0) * 10_000.0 if position.entry_price > 0 else 0.0
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
        price = self._float(candidate.get("close"))
        quantity = self._entry_quantity(price, expected_cash)
        if quantity <= 0:
            self._reject(context.timestamp, symbol, "quantity", candidate)
            return None
        stop_price, initial_r = self._initial_stop(candidate, symbol)
        self.entry_order_metadata[symbol] = {
            "setup_rank": int(candidate.get("rank") or 0),
            "live_rank": int(candidate.get("entry_rank") or candidate.get("rank") or 0),
            "setup_score": self._float(candidate.get("scanner_score")),
            "live_score": self._float(candidate.get("scanner_score")),
            "stop_price": stop_price,
        }
        self.position_meta[symbol] = {
            "initial_r": initial_r,
            "trailing_stop": stop_price,
            "entry_score": self._float(candidate.get("scanner_score")),
        }
        self._trace_entry(context.timestamp, candidate, quantity, stop_price, initial_r)
        return OrderRequest(
            symbol=symbol,
            side="BUY",
            quantity=quantity,
            order_type="MARKET",
            reason="LONG_MOMENTUM",
            stop_price=stop_price,
            tag=(
                f"ENTRY|rule=LONG_MOMENTUM|rank={candidate.get('entry_rank') or candidate.get('rank')}"
                f"|qty={quantity}|entry={price:.2f}|stop={stop_price:.2f}|R={initial_r:.4f}"
                f"|ret1={self._float(candidate.get('return_1_bps')):.1f}|macdz={self._float(candidate.get('macd_hist_z_since_open')):.2f}"
                f"|spread={self._float(candidate.get('spread')):.4f}"
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

    def _exit_requests(self, context: BarContext, portfolio: Portfolio) -> list[OrderRequest]:
        requests = []
        for symbol, position in list(portfolio.positions.items()):
            bar = context.updates_by_symbol.get(symbol)
            if bar is None:
                continue
            meta = self._position_meta(symbol, position)
            active_stop = self._active_stop(symbol, position, bar, meta)
            meta["trailing_stop"] = active_stop
            position.stop_price = max(position.stop_price, active_stop)

            reason = self._exit_reason(symbol, position, bar, meta, active_stop)
            if reason is None:
                continue
            self._trace_exit(context.timestamp, symbol, reason, position, bar, meta)
            order_type = "STOP" if reason in {"TRAILING_STOP", "INITIAL_STOP"} and self._float(bar.get("low")) <= active_stop else "MARKET"
            requests.append(
                OrderRequest(
                    symbol=symbol,
                    side="SELL",
                    quantity=position.quantity,
                    order_type=order_type,
                    reason=reason,
                    stop_price=active_stop if order_type == "STOP" else None,
                    tag=self._exit_tag(reason, position, bar, meta),
                    allow_same_bar_fill=order_type == "STOP",
                )
            )
        return requests

    def _exit_reason(self, symbol: str, position, bar: dict, meta: dict, active_stop: float) -> str | None:
        close = self._float(bar.get("close"))
        low = self._float(bar.get("low"))
        if low <= active_stop:
            return "INITIAL_STOP" if active_stop <= position.entry_price else "TRAILING_STOP"
        if self._tema_closed(bar):
            return "TEMA_CLOSE"
        r_multiple = self._open_r_multiple(position, close, meta)
        if self._velocity_take_profit(bar, position, meta, r_multiple):
            return "VELOCITY_TAKE_PROFIT"
        if self._green_body_contraction(symbol, r_multiple):
            return "GREEN_BODY_CONTRACTION"
        if self._small_red_top(symbol, position, bar, meta, r_multiple):
            return "SMALL_RED_TOP"
        if self._red_profit_giveback(position, bar):
            return "RED_PROFIT_GIVEBACK"
        return None

    def _initial_stop(self, candidate: dict, symbol: str) -> tuple[float, float]:
        entry = self._float(candidate.get("close"))
        open_price = self._float(candidate.get("open"))
        low = self._float(candidate.get("low"))
        max_risk_stop = entry * (1.0 - self.config.max_initial_stop_pct)
        candidates = [max_risk_stop]
        if entry > open_price:
            candidates.append(open_price + ((entry - open_price) * 0.5))
        else:
            candidates.append(low)
        last_red_low = self.states.get(symbol).last_red_low if self.states.get(symbol) else None
        if last_red_low is not None:
            candidates.append(float(last_red_low))
        stop = max(value for value in candidates if value < entry) if any(value < entry for value in candidates) else entry - self.config.min_initial_risk_dollars
        stop = max(0.01, min(stop, entry - self.config.min_initial_risk_dollars))
        return stop, max(self.config.min_initial_risk_dollars, entry - stop)

    def _active_stop(self, symbol: str, position, bar: dict, meta: dict) -> float:
        initial_r = self._float(meta.get("initial_r")) or abs(position.entry_price - position.stop_price)
        current_stop = max(self._float(meta.get("trailing_stop")), position.stop_price)
        max_price = max(position.max_price, self._float(bar.get("high")), self._float(bar.get("close")))
        max_r_seen = (max_price - position.entry_price) / initial_r if initial_r > 0 else 0.0
        floor = current_stop
        if max_r_seen >= 1.0:
            floor = max(floor, position.entry_price + 0.25 * initial_r)
        if max_r_seen >= 1.5:
            floor = max(floor, position.entry_price + 0.75 * initial_r)
        if max_r_seen >= 2.0:
            floor = max(floor, position.entry_price + 1.25 * initial_r)
        if max_r_seen >= 3.0:
            floor = max(floor, max_price - 1.0 * initial_r)
        state = self.states.get(symbol)
        if state and state.last_red_low is not None and max_r_seen >= 1.0:
            floor = max(floor, min(float(state.last_red_low), self._float(bar.get("close")) - self.config.min_initial_risk_dollars))
        return max(0.01, floor)

    def _tema_closed(self, bar: dict) -> bool:
        close = self._float(bar.get("close"))
        return (
            bar.get("tema9") is not None
            and bar.get("tema20") is not None
            and self._float(bar.get("tema9")) < self._float(bar.get("tema20")) + (close * self.config.tema_exit_offset_pct)
        )

    def _velocity_take_profit(self, bar: dict, position, meta: dict, r_multiple: float) -> bool:
        if r_multiple < self.config.velocity_min_r:
            return False
        close = self._float(bar.get("close"))
        open_price = self._float(bar.get("open"))
        if close <= open_price:
            return False
        body = close - open_price
        green_avg = self._float(bar.get("green_body_avg"))
        close_location = self._float(bar.get("close_location"))
        return_1_bps = self._float(bar.get("return_1")) * 10_000.0
        return (
            green_avg > 0
            and body >= green_avg * self.config.velocity_body_multiple
            and return_1_bps >= self.config.velocity_return_1_bps
            and close_location >= self.config.velocity_min_close_location
            and close > position.entry_price
        )

    def _green_body_contraction(self, symbol: str, r_multiple: float) -> bool:
        if r_multiple < self.config.contraction_min_r:
            return False
        bodies = list(self.states.get(symbol, LongMomentumSymbolState(symbol)).recent_green_bodies)
        required = max(3, min(6, int(self.config.contraction_bars)))
        if len(bodies) < required:
            return False
        recent = bodies[-required:]
        return all(recent[index] < recent[index - 1] for index in range(1, len(recent)))

    def _small_red_top(self, symbol: str, position, bar: dict, meta: dict, r_multiple: float) -> bool:
        if r_multiple < self.config.small_red_min_r:
            return False
        open_price = self._float(bar.get("open"))
        close = self._float(bar.get("close"))
        if close >= open_price:
            return False
        body = open_price - close
        state = self.states.get(symbol)
        avg_green = sum(state.recent_green_bodies) / len(state.recent_green_bodies) if state and state.recent_green_bodies else 0.0
        initial_r = self._float(meta.get("initial_r")) or abs(position.entry_price - position.stop_price)
        near_high = close >= max(position.max_price, self._float(bar.get("high"))) - self.config.small_red_near_high_r * initial_r
        return avg_green > 0 and body <= avg_green * self.config.small_red_body_multiple and near_high

    def _red_profit_giveback(self, position, bar: dict) -> bool:
        open_price = self._float(bar.get("open"))
        close = self._float(bar.get("close"))
        if close >= open_price:
            return False
        pre_candle_profit = max(0.0, (open_price - position.entry_price) * position.quantity)
        if pre_candle_profit <= 0:
            return False
        red_body_dollars = (open_price - close) * position.quantity
        return red_body_dollars >= pre_candle_profit * self.config.red_profit_giveback_pct

    def _position_meta(self, symbol: str, position) -> dict:
        meta = self.position_meta.get(symbol)
        if meta is None:
            risk = max(self.config.min_initial_risk_dollars, abs(position.entry_price - position.stop_price))
            meta = {"initial_r": risk, "trailing_stop": position.stop_price, "entry_score": position.live_score}
            self.position_meta[symbol] = meta
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
                total += position.quantity * self._float(bar.get("close")) * 0.98
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

    def _trace_entry(self, timestamp: datetime, candidate: dict, quantity: int, stop_price: float, initial_r: float) -> None:
        self.signal_events.append(
            {
                "timestamp": timestamp,
                "ticker": candidate["ticker"],
                "event": "ENTRY_INTENT",
                "rank": candidate.get("entry_rank") or candidate.get("rank"),
                "return_1_bps": candidate.get("return_1_bps"),
                "quantity": quantity,
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
                "price": candidate.get("close"),
                "stop": stop_price,
                "initial_r": initial_r,
                "return_1_bps": candidate.get("return_1_bps"),
                "macd_hist_z_since_open": candidate.get("macd_hist_z_since_open"),
                "spread": candidate.get("spread"),
                "rank": candidate.get("entry_rank") or candidate.get("rank"),
            },
            force=self._force_trade_trace(),
        )

    def _trace_exit(self, timestamp: datetime, symbol: str, reason: str, position, bar: dict, meta: dict) -> None:
        if not self.observability:
            return
        close = self._float(bar.get("close"))
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
                "trailing_stop": meta.get("trailing_stop"),
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
        price = self._float(bar.get("close")) if bar is not None else position.entry_price
        return (
            f"EXIT|reason={reason}|price={price:.2f}|stop={self._float(meta.get('trailing_stop')):.2f}"
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
