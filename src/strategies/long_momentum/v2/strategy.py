from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import polars as pl

from src.backtest.data.minute_bars import DayFrames
from src.backtest.models import BarContext, DataRequirements, Order, OrderRequest
from src.backtest.observability import ObservabilityRecorder
from src.backtest.portfolio import Portfolio
from src.strategies.long_momentum.v2.config import LongMomentumV2Config
from src.strategies.long_momentum.v2.presentation import chart_presentation


@dataclass
class LongMomentumV2SymbolState:
    ticker: str
    last_timestamp: datetime | None = None
    row: dict[str, Any] = field(default_factory=dict)


class LongMomentumV2Strategy:
    name = "long_momentum"

    def __init__(self, config: LongMomentumV2Config | None = None):
        self.config = config or LongMomentumV2Config()
        self.session_date = None
        self.states: dict[str, LongMomentumV2SymbolState] = {}
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
        requests: list[OrderRequest] = []
        requests.extend(self._partial_residual_requests(context, portfolio))
        current_bar_sell_symbols = {request.symbol for request in requests if request.side == "SELL"}

        active_pending_orders = [order for order in pending_orders if order.status == "OPEN"]
        requests.extend(self._exit_requests(context, portfolio, active_pending_orders, current_bar_sell_symbols))

        rows = self._scanner_rows(context, portfolio, active_pending_orders)
        candidates = [row for row in rows if row["entry_open"]]
        self._record_scanner(context, rows, candidates, portfolio, active_pending_orders)

        blocked_symbols = {
            order.symbol for order in active_pending_orders if order.side == "BUY"
        } | {
            request.symbol for request in requests if request.side == "BUY"
        } | set(portfolio.positions)
        available_cash = max(0.0, portfolio.cash - self.config.cash_buffer_dollars)
        for request in requests:
            if request.side == "BUY":
                available_cash -= self._estimated_buy_cost(request.quantity, self._float(request.limit_price or request.stop_price))

        entries_submitted = 0
        for candidate in candidates:
            if entries_submitted >= self.config.max_entries_per_bar:
                break
            symbol = str(candidate["ticker"])
            if symbol in blocked_symbols:
                continue
            request = self._entry_request(candidate, context, available_cash)
            if request is None:
                continue
            requests.append(request)
            entries_submitted += 1
            blocked_symbols.add(symbol)
            available_cash -= self._estimated_buy_cost(request.quantity, self._float(request.limit_price or request.stop_price))
            if available_cash <= 0:
                break
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
            state = self.states.get(ticker)
            if state is None:
                state = LongMomentumV2SymbolState(ticker=ticker)
                self.states[ticker] = state
            state.last_timestamp = context.timestamp
            state.row = dict(raw)

    def _partial_residual_requests(self, context: BarContext, portfolio: Portfolio) -> list[OrderRequest]:
        requests: list[OrderRequest] = []
        for fill in context.recent_fills:
            remaining = self._partial_remaining(fill.get("tag"))
            if remaining <= 0:
                continue
            symbol = str(fill.get("symbol") or "").upper()
            if not symbol:
                continue
            row = context.updates_by_symbol.get(symbol) or context.latest_by_symbol.get(symbol)
            if row is None:
                continue
            open_price = self._bar_open(row)
            if open_price <= 0:
                continue
            side = str(fill.get("side") or "").upper()
            if side == "BUY":
                self._reject(context.timestamp, symbol, "partial_entry_rest_disabled", row)
                continue
            elif side == "SELL":
                position = portfolio.positions.get(symbol)
                if position is None:
                    continue
                quantity = min(remaining, position.quantity)
                if quantity <= 0:
                    continue
                requests.append(
                    OrderRequest(
                        symbol=symbol,
                        side="SELL",
                        quantity=quantity,
                        order_type="LIMIT",
                        reason="PARTIAL_EXIT_REST",
                        limit_price=open_price,
                        allow_same_bar_fill=True,
                        tag=f"EXIT|reason=PARTIAL_EXIT_REST|qty={quantity}|open={open_price:.2f}|source_fill={fill.get('fill_id')}",
                    )
                )
        return requests

    def _scanner_rows(self, context: BarContext, portfolio: Portfolio, pending_orders: list[Order]) -> list[dict]:
        if context.updates.is_empty():
            return []

        names = set(context.updates.columns)

        def column(name: str, default: Any = None) -> pl.Expr:
            if name in names:
                return pl.col(name)
            return pl.lit(default)

        last_spread = column("last_spread", None).cast(pl.Float64)
        long_spread_ok = (
            column("long_momentum_spread_ok", None)
            if "long_momentum_spread_ok" in names
            else pl.when(column("last_close", None).cast(pl.Float64) < 5.0)
            .then(last_spread <= self.config.max_spread_below_5)
            .otherwise(last_spread <= self.config.max_spread_5_to_10)
        )
        tema_open = (
            column("last_tema_open", None)
            if "last_tema_open" in names
            else column("last_tema9", None).cast(pl.Float64) > column("last_tema20", None).cast(pl.Float64)
        )

        frame = context.updates.with_columns(
            column("last_close", None).cast(pl.Float64).alias("_lm_last_close"),
            column("last_volume", 0.0).cast(pl.Float64).fill_null(0.0).alias("_lm_last_volume"),
            column("last_transactions", 0.0).cast(pl.Float64).fill_null(0.0).alias("_lm_last_transactions"),
            column("last_recent_volume_5", 0.0).cast(pl.Float64).fill_null(0.0).alias("_lm_recent_volume_5"),
            column("last_recent_dollar_volume_5", 0.0).cast(pl.Float64).fill_null(0.0).alias("_lm_recent_dollar_volume_5"),
            column("last_spread_bps_abs", None).cast(pl.Float64).alias("_lm_spread_bps_abs"),
            column("last_spread_bps_max", None).cast(pl.Float64).alias("_lm_spread_bps_max"),
            column("last_quote_valid_ratio", None).cast(pl.Float64).alias("_lm_quote_valid_ratio"),
            column("last_locked_or_crossed_count", None).cast(pl.Float64).alias("_lm_locked_or_crossed_count"),
            column("last_macd_line", None).cast(pl.Float64).alias("_lm_macd_line"),
            column("last_macd_hist_z_since_open", None).cast(pl.Float64).alias("_lm_macd_hist_z"),
            column("last_close_location", None).cast(pl.Float64).alias("_lm_close_location"),
            column("last_quote_ask_size", 0.0).cast(pl.Float64).fill_null(0.0).alias("_lm_quote_ask_size"),
            column("current_open", None).cast(pl.Float64).alias("_lm_current_open"),
            column("current_open_above_last_body_high", False).fill_null(False).alias("_lm_current_open_above_last_body_high"),
            long_spread_ok.fill_null(False).alias("_lm_spread_ok"),
            tema_open.fill_null(False).alias("_lm_tema_open"),
        ).with_columns(
            (
                (pl.col("_lm_last_close") >= self.config.min_price)
                & (pl.col("_lm_last_close") <= self.config.max_price)
                & pl.col("_lm_current_open_above_last_body_high")
                & (pl.col("_lm_last_volume") >= self.config.min_volume)
                & (pl.col("_lm_last_transactions") >= self.config.min_transactions)
                & pl.col("_lm_spread_ok")
                & pl.col("_lm_tema_open")
                & (pl.col("_lm_macd_line") > 0)
                & (pl.col("_lm_macd_hist_z") >= self.config.min_macd_hist_z_since_open)
                & (pl.col("_lm_close_location") >= self.config.min_close_location)
                & (pl.col("_lm_recent_dollar_volume_5") >= self.config.min_recent_dollar_volume_5)
                & (pl.col("_lm_spread_bps_abs") <= self.config.max_spread_bps_abs)
                & (pl.col("_lm_spread_bps_max") <= self.config.max_spread_bps_max)
                & (pl.col("_lm_quote_valid_ratio") >= self.config.min_quote_valid_ratio)
                & (pl.col("_lm_locked_or_crossed_count") <= self.config.max_locked_or_crossed_count)
            ).fill_null(False).alias("entry_open"),
            pl.col("_lm_spread_ok").alias("long_momentum_spread_ok"),
            pl.col("_lm_recent_volume_5").alias("scanner_score"),
        ).with_columns(
            pl.col("entry_open").alias("long_momentum_entry_open"),
        )

        rows = frame.sort(
            ["entry_open", "scanner_score", "_lm_recent_dollar_volume_5"],
            descending=[True, True, True],
        ).to_dicts()
        pending_symbols = {order.symbol for order in pending_orders if order.status == "OPEN"}
        entry_rank = 0
        for rank, row in enumerate(rows, start=1):
            ticker = str(row["ticker"])
            row["timestamp"] = context.timestamp
            row["session_date"] = self.session_date.isoformat() if self.session_date else ""
            row["ticker"] = ticker
            row["price"] = self._float(row.get("last_close"))
            row["rank"] = rank
            row["held_quantity"] = portfolio.positions[ticker].quantity if ticker in portfolio.positions else 0
            row["open_positions"] = len(portfolio.positions)
            row["status"] = self._scanner_status(row, ticker, portfolio, pending_symbols)
            row["entry_state"] = "entry_open" if row["entry_open"] else self._entry_block_reason(row)
            if row["entry_open"]:
                entry_rank += 1
                row["entry_rank"] = entry_rank
            else:
                row["entry_rank"] = None
        return rows

    def _entry_request(self, candidate: dict, context: BarContext, available_cash: float) -> OrderRequest | None:
        symbol = str(candidate["ticker"])
        entry_price = self._float(candidate.get("current_open"))
        ask_size = int(self._float(candidate.get("last_quote_ask_size")))
        if entry_price <= 0 or ask_size <= 0:
            self._reject(context.timestamp, symbol, "quote_ask_size", candidate)
            return None
        cash_quantity = self._cash_quantity(entry_price, available_cash)
        quantity = min(ask_size, cash_quantity)
        if quantity <= 0:
            self._reject(context.timestamp, symbol, "cash", candidate)
            return None
        stop_price = self._initial_stop_price(candidate, entry_price)
        rank = int(candidate.get("entry_rank") or candidate.get("rank") or 0)
        score = self._float(candidate.get("scanner_score"))
        self._set_entry_metadata(symbol, candidate, rank=rank, score=score, stop_price=stop_price)
        self.position_meta[symbol] = {
            "initial_stop": stop_price,
            "initial_r": max(self.config.stop_offset_dollars, abs(entry_price - stop_price)),
            "entry_score": score,
        }
        self._trace_entry(context.timestamp, candidate, quantity, entry_price, stop_price)
        return OrderRequest(
            symbol=symbol,
            side="BUY",
            quantity=quantity,
            order_type="LIMIT",
            reason="LONG_MOMENTUM_V2",
            limit_price=entry_price,
            allow_same_bar_fill=True,
            protective_stop_price=stop_price,
            tag=(
                f"ENTRY|rule=LONG_MOMENTUM_V2|rank={rank}|qty={quantity}|entry={entry_price:.2f}"
                f"|stop={stop_price:.2f}|last_recent_volume_5={score:.0f}|ask_size={ask_size}"
                f"|macdz={self._float(candidate.get('last_macd_hist_z_since_open')):.2f}"
            ),
        )

    def _exit_requests(
        self,
        context: BarContext,
        portfolio: Portfolio,
        pending_orders: list[Order],
        current_bar_sell_symbols: set[str] | None = None,
    ) -> list[OrderRequest]:
        requests: list[OrderRequest] = []
        pending_sell_symbols = {order.symbol for order in pending_orders if order.side == "SELL" and order.status == "OPEN"}
        if current_bar_sell_symbols:
            pending_sell_symbols |= current_bar_sell_symbols
        for symbol, position in list(portfolio.positions.items()):
            if symbol in pending_sell_symbols:
                continue
            bar = context.updates_by_symbol.get(symbol)
            if bar is None:
                continue
            meta = self._position_meta(symbol, position)
            if self._profit_lock_triggered(position, bar, meta):
                self._trace_exit(context.timestamp, symbol, "PROFIT_LOCK", position, bar, meta)
                requests.append(
                    OrderRequest(
                        symbol=symbol,
                        side="SELL",
                        quantity=position.quantity,
                        order_type="MARKET",
                        reason="PROFIT_LOCK",
                        tag=self._exit_tag("PROFIT_LOCK", position, bar, meta),
                    )
                )
                continue
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
            requests.append(
                OrderRequest(
                    symbol=symbol,
                    side="SELL",
                    quantity=position.quantity,
                    order_type="STOP",
                    reason="INITIAL_STOP",
                    stop_price=position.stop_price,
                    tag=self._exit_tag("INITIAL_STOP", position, bar, meta),
                    allow_same_bar_fill=True,
                    expire_on_bar_close=True,
                )
            )
        return requests

    def _set_entry_metadata(self, symbol: str, row: dict, *, rank: int, score: float, stop_price: float) -> None:
        self.entry_order_metadata[symbol] = {
            "setup_rank": rank,
            "live_rank": rank,
            "setup_score": score,
            "live_score": score,
            "stop_price": stop_price,
        }

    def _initial_stop_price(self, row: dict, entry_price: float) -> float:
        last_3_candle_low = self._float(row.get("last_3_candle_low_price"))
        if last_3_candle_low > 0 and last_3_candle_low < entry_price:
            return max(0.01, last_3_candle_low)
        body_floor = min(self._float(row.get("last_open")), self._float(row.get("last_close")))
        if body_floor > 0 and body_floor < entry_price:
            return max(0.01, body_floor)
        return max(0.01, entry_price - self.config.stop_offset_dollars)

    def _cash_quantity(self, price: float, available_cash: float) -> int:
        if price <= 0 or available_cash <= 0:
            return 0
        per_share_cost = price + max(0.0, self.config.sizing_fee_per_share)
        quantity = int((available_cash - self.config.sizing_min_fee) / per_share_cost) if per_share_cost > 0 else 0
        while quantity > 0 and self._estimated_buy_cost(quantity, price) > available_cash:
            quantity -= 1
        return max(0, quantity)

    def _estimated_buy_cost(self, quantity: int, price: float) -> float:
        if quantity <= 0 or price <= 0:
            return 0.0
        fee = max(self.config.sizing_min_fee, quantity * self.config.sizing_fee_per_share)
        return quantity * price + fee

    def _tema_closed(self, bar: dict) -> bool:
        close = self._float(bar.get("last_close"))
        return (
            bar.get("last_tema9") is not None
            and bar.get("last_tema20") is not None
            and self._float(bar.get("last_tema9")) < self._float(bar.get("last_tema20")) + (close * self.config.tema_exit_offset_pct)
        )

    def _position_meta(self, symbol: str, position) -> dict:
        meta = self.position_meta.get(symbol)
        if meta is None:
            risk = max(self.config.stop_offset_dollars, abs(position.entry_price - position.stop_price))
            meta = {
                "initial_stop": position.stop_price,
                "initial_r": risk,
                "entry_score": position.live_score,
            }
            self.position_meta[symbol] = meta
        return meta

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
        if not bool(row.get("_lm_current_open_above_last_body_high")):
            return "current_open_not_above_last_high"
        if self._float(row.get("last_volume")) < self.config.min_volume:
            return "volume"
        if self._float(row.get("last_transactions")) < self.config.min_transactions:
            return "transactions"
        if not bool(row.get("_lm_spread_ok")):
            return "spread"
        if not bool(row.get("_lm_tema_open")):
            return "tema_closed"
        if self._float(row.get("last_macd_line")) <= 0:
            return "macd_line"
        if self._float(row.get("last_macd_hist_z_since_open")) < self.config.min_macd_hist_z_since_open:
            return "macd_hist_z"
        if self._float(row.get("last_close_location")) < self.config.min_close_location:
            return "close_location"
        if self._float(row.get("last_recent_dollar_volume_5")) < self.config.min_recent_dollar_volume_5:
            return "recent_dollar_volume_5"
        if self._float(row.get("last_spread_bps_abs")) > self.config.max_spread_bps_abs:
            return "spread_bps_abs"
        if self._float(row.get("last_spread_bps_max")) > self.config.max_spread_bps_max:
            return "spread_bps_max"
        if self._float(row.get("last_quote_valid_ratio")) < self.config.min_quote_valid_ratio:
            return "quote_valid_ratio"
        if self._float(row.get("last_locked_or_crossed_count")) > self.config.max_locked_or_crossed_count:
            return "locked_or_crossed"
        return "filtered"

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
                "selected_count": len(candidates),
            }
        )
        if not self.observability or not rows:
            return
        self.observability.scanner(timestamp=context.timestamp, rows=rows, score_key="scanner_score", stage="long_momentum_v2_scanner")
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

    def _trace_entry(self, timestamp: datetime, candidate: dict, quantity: int, entry_price: float, stop_price: float) -> None:
        self.signal_events.append(
            {
                "timestamp": timestamp,
                "ticker": candidate["ticker"],
                "event": "ENTRY_INTENT",
                "rank": candidate.get("entry_rank") or candidate.get("rank"),
                "recent_volume_5": candidate.get("last_recent_volume_5"),
                "quantity": quantity,
                "entry": entry_price,
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
            reason_code="LONG_MOMENTUM_V2",
            reason="Eligible long momentum v2 scanner candidate",
            values={
                "quantity": quantity,
                "current_open": candidate.get("current_open"),
                "stop": stop_price,
                "last_recent_volume_5": candidate.get("last_recent_volume_5"),
                "last_recent_dollar_volume_5": candidate.get("last_recent_dollar_volume_5"),
                "last_quote_ask_size": candidate.get("last_quote_ask_size"),
                "last_macd_line": candidate.get("last_macd_line"),
                "last_macd_hist_z_since_open": candidate.get("last_macd_hist_z_since_open"),
                "rank": candidate.get("entry_rank") or candidate.get("rank"),
            },
            force=self._force_trade_trace(),
        )

    def _trace_exit(self, timestamp: datetime, symbol: str, reason: str, position, bar: dict, meta: dict) -> None:
        if not self.observability:
            return
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
                "last_close": bar.get("last_close"),
                "entry_price": position.entry_price,
                "initial_stop": meta.get("initial_stop"),
                "initial_r": meta.get("initial_r"),
                "profit_lock_price": self._profit_lock_price(position, meta),
            },
            force=self._force_trade_trace(),
        )

    def _reject(self, timestamp: datetime, symbol: str, reason: str, candidate: dict) -> None:
        self.rejection_events.append(
            {
                "timestamp": timestamp,
                "ticker": symbol,
                "reject_reason": reason,
                "rank": candidate.get("entry_rank") or candidate.get("rank"),
                "last_recent_volume_5": candidate.get("last_recent_volume_5"),
            }
        )

    def _exit_tag(self, reason: str, position, bar: dict | None, meta: dict) -> str:
        price = self._float(bar.get("last_close")) if bar is not None else position.entry_price
        stop = self._float(meta.get("initial_stop")) or position.stop_price
        return (
            f"EXIT|reason={reason}|price={price:.2f}|stop={stop:.2f}"
            f"|R={self._float(meta.get('initial_r')):.4f}|maxp={position.max_price:.2f}"
            f"|maxR={position.max_r_multiple:.2f}|lock={self._profit_lock_price(position, meta):.2f}"
        )

    def _profit_lock_triggered(self, position, bar: dict, meta: dict) -> bool:
        activation_r = max(0.0, self.config.profit_lock_activation_r)
        activation_pct = max(0.0, self.config.profit_lock_activation_pct)
        if position.max_r_multiple < activation_r:
            return False
        if position.max_price < position.entry_price * (1.0 + activation_pct):
            return False
        current_open = self._bar_open(bar)
        lock_price = self._profit_lock_price(position, meta)
        return current_open > 0 and lock_price > 0 and current_open <= lock_price

    def _profit_lock_price(self, position, meta: dict) -> float:
        peak_gain = max(0.0, position.max_price - position.entry_price)
        if peak_gain <= 0:
            return 0.0
        retained_gain = peak_gain * (1.0 - max(0.0, min(1.0, self.config.profit_lock_giveback_pct)))
        min_locked_gain = position.entry_price * max(0.0, self.config.profit_lock_min_locked_pct)
        return position.entry_price + max(retained_gain, min_locked_gain)

    def _partial_remaining(self, tag: Any) -> int:
        parsed = self._parse_pipe_tag(str(tag or ""))
        try:
            return max(0, int(float(parsed.get("remaining", 0))))
        except (TypeError, ValueError):
            return 0

    def _parse_pipe_tag(self, tag: str) -> dict[str, str]:
        values: dict[str, str] = {}
        for part in tag.split("|"):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            values[key.strip().lower()] = value.strip()
        return values

    def _bar_open(self, row: dict) -> float:
        return self._float(row.get("current_open") if row.get("current_open") is not None else row.get("open"))

    def _float(self, value) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _force_trade_trace(self) -> bool:
        return bool(self.observability and self.observability.config.observability_always_trace_trades)
