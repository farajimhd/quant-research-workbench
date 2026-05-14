from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from math import log1p
from typing import Any

import polars as pl

from src.backtest.data.minute_bars import DayFrames
from src.backtest.models import BarContext, DataRequirements, Order, OrderRequest
from src.backtest.observability import ObservabilityRecorder
from src.backtest.portfolio import Portfolio
from src.strategies.break_of_vwap.v1.config import BreakOfVwapConfig
from src.strategies.break_of_vwap.v1.presentation import chart_presentation


@dataclass
class VwapBreakState:
    ticker: str
    first_close: float = 0.0
    last_close: float = 0.0
    last_timestamp: datetime | None = None
    last_vwap_bps: float | None = None
    last_macd_hist_bps: float | None = None
    last_tema_spread_bps: float | None = None
    recent_closes: deque[float] = field(default_factory=deque)
    row: dict[str, Any] = field(default_factory=dict)


class BreakOfVwapStrategy:
    name = "break_of_vwap"

    def __init__(self, config: BreakOfVwapConfig | None = None):
        self.config = config or BreakOfVwapConfig()
        self.session_date = None
        self.states: dict[str, VwapBreakState] = {}
        self.position_meta: dict[str, dict] = {}
        self.entry_order_metadata: dict[str, dict] = {}
        self.cooldown_until: dict[str, datetime] = {}
        self.daily_entry_count = 0
        self.scanner_snapshots: list[dict] = []
        self.live_rankings: list[dict] = []
        self.signal_events: list[dict] = []
        self.rejection_events: list[dict] = []
        self.observability: ObservabilityRecorder | None = None
        self._excluded_symbols = set(self.config.excluded_symbols)

    def set_observability(self, observability: ObservabilityRecorder) -> None:
        self.observability = observability

    def data_requirements(self) -> DataRequirements:
        return DataRequirements(
            event_timeframe="1m",
            feature_groups=("core", "momentum", "session", "volume_liquidity", "price_action", "shock", "market_structure"),
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
            ),
        )

    def chart_presentation(self) -> dict:
        return chart_presentation()

    def prepare_day(self, frames: DayFrames, portfolio: Portfolio) -> pl.DataFrame:
        self.session_date = frames.session_date
        self.states = {}
        self.position_meta = {}
        self.entry_order_metadata = {}
        self.cooldown_until = {}
        self.daily_entry_count = 0
        self._excluded_symbols = set(self.config.excluded_symbols)
        return frames.event_frame.filter(
            (pl.col("minute_of_day") >= self.config.trading_start_minute)
            & (pl.col("minute_of_day") < self.config.trading_end_minute)
        )

    def on_bar(self, context: BarContext, portfolio: Portfolio, pending_orders: list[Order]) -> list[OrderRequest]:
        self._update_states(context)
        requests = self._exit_requests(context, portfolio)

        pending_symbols = {order.symbol for order in pending_orders if order.status == "OPEN"}
        exiting_symbols = {request.symbol for request in requests if request.side == "SELL"}
        rows = self._scanner_rows(context, portfolio, pending_symbols, exiting_symbols)
        candidates = [row for row in rows if row["entry_open"]]
        self._record_scanner(context, rows, candidates, portfolio, pending_orders)

        open_slots = max(0, self.config.max_active_positions - len(portfolio.positions) - len(pending_symbols) + len(exiting_symbols))
        remaining_daily_entries = max(0, self.config.max_daily_entries - self.daily_entry_count)
        allowed_entries = min(open_slots, remaining_daily_entries, self.config.max_new_entries_per_bar)
        if allowed_entries <= 0:
            return requests

        for candidate in candidates[:allowed_entries]:
            request = self._entry_request(candidate, context, portfolio)
            if request is None:
                continue
            requests.append(request)
            self.daily_entry_count += 1
        return requests

    def on_day_end(self, timestamp: datetime, portfolio: Portfolio) -> list[OrderRequest]:
        requests = []
        for symbol, position in list(portfolio.positions.items()):
            requests.append(
                OrderRequest(
                    symbol=symbol,
                    side="SELL",
                    quantity=position.quantity,
                    order_type="MARKET",
                    reason="EOD",
                    tag=f"EXIT|reason=EOD|held_symbol={symbol}",
                )
            )
            self._set_cooldown(symbol, timestamp)
        return requests

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
        max_window = max(16, self.config.max_hold_minutes + 1)
        for raw in context.updates.iter_rows(named=True):
            ticker = str(raw["ticker"])
            close = self._float(raw.get("close"))
            if close <= 0:
                continue

            state = self.states.get(ticker)
            if state is None:
                state = VwapBreakState(ticker=ticker, recent_closes=deque(maxlen=max_window))
                self.states[ticker] = state
            if state.first_close <= 0:
                state.first_close = close

            row = dict(raw)
            vwap = self._float(row.get("vwap"))
            tema9 = self._float_or_none(row.get("tema9"))
            tema20 = self._float_or_none(row.get("tema20"))
            macd_hist = self._float_or_none(row.get("macd_hist"))
            if macd_hist is None and row.get("macd_line") is not None and row.get("macd_signal") is not None:
                macd_hist = self._float(row["macd_line"]) - self._float(row["macd_signal"])

            vwap_bps = ((close / vwap) - 1.0) * 10_000.0 if vwap > 0 else 0.0
            prior_vwap_bps = state.last_vwap_bps
            tema_spread_bps = ((tema9 - tema20) / close) * 10_000.0 if tema9 is not None and tema20 is not None else 0.0
            macd_hist_bps = (float(macd_hist or 0.0) / close) * 10_000.0
            ret5_bps = self._lookback_return_bps(state, close, 5)
            ret15_bps = self._lookback_return_bps(state, close, 15)
            return_1_bps = self._float(row.get("return_1")) * 10_000.0
            if return_1_bps == 0.0 and row.get("open") is not None and self._float(row.get("open")) > 0:
                return_1_bps = ((close / self._float(row["open"])) - 1.0) * 10_000.0

            row.update(
                {
                    "vwap_bps": vwap_bps,
                    "prior_vwap_bps": prior_vwap_bps,
                    "vwap_break_bps": vwap_bps - prior_vwap_bps if prior_vwap_bps is not None else 0.0,
                    "day_return_bps": ((close / self._float(row.get("day_open"))) - 1.0) * 10_000.0
                    if self._float(row.get("day_open")) > 0
                    else 0.0,
                    "tema_spread_bps": tema_spread_bps,
                    "tema_spread_delta_bps": 0.0
                    if state.last_tema_spread_bps is None
                    else tema_spread_bps - state.last_tema_spread_bps,
                    "macd_hist_bps": macd_hist_bps,
                    "macd_hist_delta_bps": 0.0 if state.last_macd_hist_bps is None else macd_hist_bps - state.last_macd_hist_bps,
                    "ret5_bps": ret5_bps,
                    "ret15_bps": ret15_bps,
                    "return_1_bps": return_1_bps,
                    "close_location_value": self._float(row.get("close_location")),
                    "dollar_volume_sma20_value": self._float(row.get("dollar_volume_sma20")),
                    "relative_dollar_volume20_value": self._float(row.get("relative_dollar_volume20")),
                    "reclaim_vwap_value": bool(row.get("reclaim_vwap")),
                }
            )
            row.update(self._score_row(row))

            state.last_close = close
            state.last_timestamp = context.timestamp
            state.last_vwap_bps = vwap_bps
            state.last_macd_hist_bps = macd_hist_bps
            state.last_tema_spread_bps = tema_spread_bps
            state.recent_closes.append(close)
            state.row = row

    def _scanner_rows(
        self,
        context: BarContext,
        portfolio: Portfolio,
        pending_symbols: set[str],
        exiting_symbols: set[str],
    ) -> list[dict]:
        rows = []
        for ticker, state in self.states.items():
            if state.last_timestamp != context.timestamp:
                continue
            row = dict(state.row)
            entry_open, reason = self._entry_open(row, context, portfolio, pending_symbols, exiting_symbols)
            position = portfolio.positions.get(ticker)
            row.update(
                {
                    "session_date": self.session_date.isoformat() if self.session_date else "",
                    "timestamp": context.timestamp,
                    "ticker": ticker,
                    "entry_open": entry_open,
                    "entry_state": reason,
                    "status": "held" if position else ("pending" if ticker in pending_symbols else ("eligible" if entry_open else "blocked")),
                    "price": self._float(row.get("close")),
                    "held_quantity": position.quantity if position else 0,
                    "open_positions": len(portfolio.positions),
                }
            )
            rows.append(row)

        rows.sort(key=lambda item: float(item.get("scanner_score") or 0.0), reverse=True)
        for rank, row in enumerate(rows, start=1):
            row["rank"] = rank
        entry_rank = 0
        for row in rows:
            if row["entry_open"]:
                entry_rank += 1
                row["entry_rank"] = entry_rank
            else:
                row["entry_rank"] = None
        return rows

    def _entry_open(
        self,
        row: dict,
        context: BarContext,
        portfolio: Portfolio,
        pending_symbols: set[str],
        exiting_symbols: set[str],
    ) -> tuple[bool, str]:
        ticker = str(row["ticker"])
        close = self._float(row.get("close"))
        prior_vwap_bps = row.get("prior_vwap_bps")
        if ticker in portfolio.positions and ticker not in exiting_symbols:
            return False, "already_held"
        if ticker in pending_symbols:
            return False, "pending_order"
        if self._in_cooldown(ticker, context.timestamp):
            return False, "cooldown"
        if ticker in self._excluded_symbols:
            return False, "excluded_symbol"
        if prior_vwap_bps is None:
            return False, "no_prior_vwap_state"
        if close < self.config.min_price:
            return False, "price_low"
        if close > self.config.max_price:
            return False, "price_high"
        if self._float(row.get("dollar_volume_sma20_value")) < self.config.min_dollar_volume_sma20:
            return False, "liquidity"
        if self._float(row.get("relative_dollar_volume20_value")) < self.config.min_relative_dollar_volume20:
            return False, "relative_liquidity"
        if self._float(prior_vwap_bps) < self.config.min_prior_vwap_bps:
            return False, "prior_too_far_below_vwap"
        if self._float(prior_vwap_bps) > self.config.max_prior_vwap_bps:
            return False, "prior_not_below_vwap"
        if self._float(row.get("vwap_bps")) < self.config.min_break_vwap_bps:
            return False, "not_above_vwap"
        if self._float(row.get("vwap_bps")) > self.config.max_break_vwap_bps:
            return False, "extended_above_vwap"
        if self._float(row.get("return_1_bps")) < self.config.min_break_return_bps:
            return False, "weak_break_bar"
        if self._float(row.get("close_location_value")) < self.config.min_close_location:
            return False, "weak_close_location"
        if self._float(row.get("day_return_bps")) < self.config.min_day_return_bps:
            return False, "day_damage"
        if self._float(row.get("day_return_bps")) > self.config.max_day_return_bps:
            return False, "day_extended"
        if self._float(row.get("ret5_bps")) < self.config.min_ret5_bps:
            return False, "recent_damage"
        if self._float(row.get("ret15_bps")) > self.config.max_ret15_bps:
            return False, "recent_extended"
        if self._float(row.get("macd_hist_delta_bps")) < self.config.min_macd_hist_delta_bps:
            return False, "macd_not_improving"
        if self._float(row.get("tema_spread_delta_bps")) < self.config.min_tema_spread_delta_bps:
            return False, "tema_not_improving"
        if self._float(row.get("scanner_score")) < self.config.min_scanner_score:
            return False, "scanner_score"
        if self._float(row.get("estimated_edge_bps")) < self.config.min_expected_edge_bps:
            return False, "estimated_edge"
        return True, "entry_open"

    def _entry_request(self, candidate: dict, context: BarContext, portfolio: Portfolio) -> OrderRequest | None:
        price = self._float(candidate.get("close"))
        risk_per_share, stop_price = self._initial_risk_and_stop(candidate)
        quantity, target_notional = self._entry_quantity(price, risk_per_share, candidate, portfolio, context)
        if quantity <= 0:
            self._reject(context.timestamp, str(candidate["ticker"]), "quantity", candidate)
            return None
        self.entry_order_metadata[str(candidate["ticker"])] = {
            "setup_rank": int(candidate.get("rank") or 0),
            "live_rank": int(candidate.get("entry_rank") or candidate.get("rank") or 0),
            "setup_score": float(candidate.get("scanner_score") or 0.0),
            "live_score": float(candidate.get("scanner_score") or 0.0),
            "stop_price": stop_price,
        }
        self.position_meta[str(candidate["ticker"])] = {
            "entry_score": float(candidate.get("scanner_score") or 0.0),
            "initial_r": risk_per_share,
            "trailing_stop": stop_price,
            "target_notional": target_notional,
            "entry_vwap_bps": candidate.get("vwap_bps"),
        }
        self._trace_entry(context.timestamp, candidate, quantity, stop_price, risk_per_share, target_notional)
        return OrderRequest(
            symbol=str(candidate["ticker"]),
            side="BUY",
            quantity=quantity,
            order_type="MARKET",
            reason="BREAK_OF_VWAP",
            tag=(
                f"ENTRY|rule=BREAK_OF_VWAP|rank={candidate.get('entry_rank') or candidate.get('rank')}"
                f"|qty={quantity}|entry={price:.2f}|stop={stop_price:.2f}|R={risk_per_share:.4f}"
                f"|score={self._float(candidate.get('scanner_score')):.1f}|edge={self._float(candidate.get('estimated_edge_bps')):.1f}"
                f"|prior_vwap={self._float(candidate.get('prior_vwap_bps')):.1f}|vwap={self._float(candidate.get('vwap_bps')):.1f}"
            ),
        )

    def _exit_requests(self, context: BarContext, portfolio: Portfolio) -> list[OrderRequest]:
        requests = []
        for symbol, position in list(portfolio.positions.items()):
            bar = context.updates_by_symbol.get(symbol)
            if bar is None:
                continue
            state = self.states.get(symbol)
            enriched_bar = state.row if state and state.row else bar
            meta = self._position_meta(symbol, position)
            trailing_stop = self._updated_trailing_stop(position, enriched_bar, meta)
            meta["trailing_stop"] = trailing_stop
            position.stop_price = max(position.stop_price, trailing_stop)

            held_minutes = (context.timestamp - position.entry_time).total_seconds() / 60.0
            reason = None
            order_type = "MARKET"
            stop_price = None
            allow_same_bar_fill = False
            if self._float(enriched_bar.get("low")) <= trailing_stop:
                reason = "TRAILING_STOP"
                order_type = "STOP"
                stop_price = trailing_stop
                allow_same_bar_fill = True
            elif held_minutes >= self.config.max_hold_minutes:
                reason = "TIME_EXIT"
            elif held_minutes >= self.config.min_hold_minutes and self._vwap_failed(enriched_bar):
                reason = "VWAP_FAILURE"
            elif held_minutes >= self.config.min_hold_minutes and self._break_failed(enriched_bar):
                reason = "BREAK_FAILED"
            if reason is None:
                continue
            self._trace_exit(context.timestamp, symbol, reason, position, enriched_bar, meta)
            self._set_cooldown(symbol, context.timestamp)
            requests.append(
                OrderRequest(
                    symbol=symbol,
                    side="SELL",
                    quantity=position.quantity,
                    order_type=order_type,
                    reason=reason,
                    stop_price=stop_price,
                    tag=self._exit_tag(reason, position, enriched_bar, meta),
                    allow_same_bar_fill=allow_same_bar_fill,
                )
            )
        return requests

    def _score_row(self, row: dict) -> dict[str, float]:
        dollar_volume = max(0.0, self._float(row.get("dollar_volume_sma20_value")))
        relative_dollar_volume = max(0.0, self._float(row.get("relative_dollar_volume20_value")))
        close_location = max(0.0, min(1.0, self._float(row.get("close_location_value"))))
        vwap_bps = self._float(row.get("vwap_bps"))
        prior_vwap_bps = self._float(row.get("prior_vwap_bps"))
        vwap_break_bps = self._float(row.get("vwap_break_bps"))
        day_return_bps = self._float(row.get("day_return_bps"))
        macd_delta = max(0.0, self._float(row.get("macd_hist_delta_bps")))
        tema_delta = max(0.0, self._float(row.get("tema_spread_delta_bps")))
        return_1_bps = max(0.0, self._float(row.get("return_1_bps")))

        liquidity_score = min(22.0, log1p(dollar_volume / max(self.config.min_dollar_volume_sma20, 1.0)) * 10.0)
        liquidity_score += min(8.0, relative_dollar_volume * 3.0)
        prior_score = max(0.0, 12.0 * (1.0 - abs(prior_vwap_bps) / max(abs(self.config.min_prior_vwap_bps), 1.0)))
        break_score = max(0.0, min(18.0, vwap_break_bps / 4.0)) + max(0.0, min(10.0, vwap_bps / 3.0))
        candle_score = close_location * 16.0 + min(10.0, return_1_bps / 1.8)
        confirmation_score = min(15.0, macd_delta * 5.0) + min(8.0, tema_delta * 2.0)
        day_score = 10.0 if self.config.min_day_return_bps <= day_return_bps <= self.config.max_day_return_bps else 0.0
        reclaim_score = 4.0 if row.get("reclaim_vwap_value") else 0.0
        scanner_score = liquidity_score + prior_score + break_score + candle_score + confirmation_score + day_score + reclaim_score
        estimated_edge_bps = scanner_score * 0.76 - self.config.estimated_round_trip_cost_bps
        return {
            "liquidity_score": liquidity_score,
            "prior_vwap_score": prior_score,
            "break_score": break_score,
            "candle_score": candle_score,
            "confirmation_score": confirmation_score,
            "day_score": day_score,
            "reclaim_score": reclaim_score,
            "scanner_score": scanner_score,
            "estimated_edge_bps": estimated_edge_bps,
        }

    def _entry_quantity(
        self,
        price: float,
        risk_per_share: float,
        candidate: dict,
        portfolio: Portfolio,
        context: BarContext,
    ) -> tuple[int, float]:
        if price <= 0 or risk_per_share <= 0:
            return 0, 0.0
        total_equity = portfolio.total_equity(context.latest_by_symbol)
        deployable_cash = max(0.0, portfolio.cash - (total_equity * self.config.cash_reserve_pct))
        score_quality = max(0.0, min(self._float(candidate.get("scanner_score")) / 100.0, 1.0))
        target_notional = total_equity * self.config.max_capital_per_trade_pct * (0.70 + 0.30 * score_quality)
        target_notional = min(target_notional, deployable_cash)
        if target_notional < self.config.min_position_notional:
            return 0, target_notional
        quantity_by_cash = int(target_notional / price)
        quantity_by_risk = int((total_equity * self.config.risk_per_trade_pct) / risk_per_share)
        quantity = max(0, min(quantity_by_cash, quantity_by_risk))
        if quantity * price < self.config.min_position_notional:
            return 0, target_notional
        return quantity, target_notional

    def _initial_risk_and_stop(self, candidate: dict) -> tuple[float, float]:
        price = self._float(candidate.get("close"))
        vwap = self._float(candidate.get("vwap"))
        risk = max(price * self.config.initial_risk_pct, self.config.min_initial_risk_dollars)
        risk = min(risk, price * self.config.max_initial_risk_pct)
        price_stop = price - risk
        vwap_stop = vwap * (1.0 - self.config.stop_vwap_buffer_bps / 10_000.0) if vwap > 0 else price_stop
        stop_price = max(0.01, min(price_stop, vwap_stop))
        return max(0.01, price - stop_price), stop_price

    def _updated_trailing_stop(self, position, bar: dict, meta: dict) -> float:
        initial_r = self._float(meta.get("initial_r")) or abs(position.entry_price - position.stop_price)
        if initial_r <= 0:
            return position.stop_price
        current_stop = max(self._float(meta.get("trailing_stop")), position.stop_price)
        max_price = max(position.max_price, self._float(bar.get("high")), self._float(bar.get("close")))
        max_profit = max_price - position.entry_price
        if max_profit < self.config.trailing_activation_r * initial_r:
            return current_stop
        lock_stop = position.entry_price + (self.config.trailing_lock_r * initial_r)
        giveback_stop = max_price - (self.config.trailing_giveback_r * initial_r)
        return max(current_stop, lock_stop, giveback_stop)

    def _vwap_failed(self, bar: dict) -> bool:
        return self._float(bar.get("vwap_bps")) <= self.config.vwap_failure_bps

    def _break_failed(self, bar: dict) -> bool:
        return self._float(bar.get("return_1_bps")) <= -self.config.failure_return_bps and self._float(bar.get("macd_hist_delta_bps")) < 0

    def _position_meta(self, symbol: str, position) -> dict:
        meta = self.position_meta.get(symbol)
        if meta is None:
            risk = max(0.01, abs(position.entry_price - position.stop_price))
            meta = {"entry_score": position.live_score, "initial_r": risk, "trailing_stop": position.stop_price}
            self.position_meta[symbol] = meta
        return meta

    def _record_scanner(
        self,
        context: BarContext,
        rows: list[dict],
        candidates: list[dict],
        portfolio: Portfolio,
        pending_orders: list[Order],
    ) -> None:
        captured = rows[: max(25, self.config.max_active_positions * 10)]
        self.live_rankings.extend(captured)
        self.scanner_snapshots.append(
            {
                "timestamp": context.timestamp,
                "session_date": self.session_date.isoformat() if self.session_date else "",
                "candidate_count": len(candidates),
                "scanned_count": len(rows),
                "selected_count": min(len(candidates), self.config.max_new_entries_per_bar),
            }
        )
        if not self.observability or not rows:
            return
        self.observability.scanner(timestamp=context.timestamp, rows=rows, score_key="scanner_score", stage="break_of_vwap_scanner")
        self.observability.state(
            timestamp=context.timestamp,
            scope="strategy",
            state={
                "scanned_count": len(rows),
                "entry_open_count": len(candidates),
                "open_positions": len(portfolio.positions),
                "pending_orders": len([order for order in pending_orders if order.status == "OPEN"]),
                "daily_entry_count": self.daily_entry_count,
                "max_daily_entries": self.config.max_daily_entries,
            },
        )

    def _trace_entry(
        self,
        timestamp: datetime,
        candidate: dict,
        quantity: int,
        stop_price: float,
        risk_per_share: float,
        target_notional: float,
    ) -> None:
        self.signal_events.append(
            {
                "timestamp": timestamp,
                "ticker": candidate["ticker"],
                "event": "ENTRY_INTENT",
                "rank": candidate.get("entry_rank") or candidate.get("rank"),
                "scanner_score": candidate.get("scanner_score"),
                "estimated_edge_bps": candidate.get("estimated_edge_bps"),
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
            reason_code="BREAK_OF_VWAP",
            reason="Fresh row reclaimed VWAP with liquidity and confirmation gates satisfied",
            values={
                "quantity": quantity,
                "price": self._float(candidate.get("close")),
                "stop": stop_price,
                "initial_r": risk_per_share,
                "target_notional": target_notional,
                "scanner_score": candidate.get("scanner_score"),
                "estimated_edge_bps": candidate.get("estimated_edge_bps"),
                "prior_vwap_bps": candidate.get("prior_vwap_bps"),
                "vwap_bps": candidate.get("vwap_bps"),
                "vwap_break_bps": candidate.get("vwap_break_bps"),
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
                "price": self._float(bar.get("close")),
                "entry_price": position.entry_price,
                "trailing_stop": meta.get("trailing_stop"),
                "initial_r": meta.get("initial_r"),
                "vwap_bps": bar.get("vwap_bps"),
                "return_1_bps": bar.get("return_1_bps"),
                "macd_hist_delta_bps": bar.get("macd_hist_delta_bps"),
            },
            force=self._force_trade_trace(),
        )

    def _reject(self, timestamp: datetime, symbol: str, reason: str, candidate: dict) -> None:
        self.rejection_events.append(
            {
                "timestamp": timestamp,
                "ticker": symbol,
                "reject_reason": reason,
                "scanner_score": candidate.get("scanner_score"),
                "estimated_edge_bps": candidate.get("estimated_edge_bps"),
                "rank": candidate.get("entry_rank") or candidate.get("rank"),
            }
        )
        self._set_cooldown(symbol, timestamp)

    def _exit_tag(self, reason: str, position, bar: dict, meta: dict) -> str:
        price = self._float(bar.get("close")) or position.entry_price
        return (
            f"EXIT|reason={reason}|price={price:.2f}|stop={self._float(meta.get('trailing_stop')):.2f}"
            f"|R={self._float(meta.get('initial_r')):.4f}|maxp={position.max_price:.2f}"
            f"|maxu={position.max_unrealized_profit:.2f}|vwap={self._float(bar.get('vwap_bps')):.1f}"
        )

    def _set_cooldown(self, symbol: str, timestamp: datetime) -> None:
        self.cooldown_until[symbol] = timestamp

    def _in_cooldown(self, symbol: str, timestamp: datetime) -> bool:
        last = self.cooldown_until.get(symbol)
        if last is None:
            return False
        return (timestamp - last).total_seconds() < self.config.cooldown_minutes * 60

    def _lookback_return_bps(self, state: VwapBreakState, close: float, lookback: int) -> float:
        closes = list(state.recent_closes)
        if len(closes) < lookback:
            return 0.0
        prior = closes[-lookback]
        return ((close / prior) - 1.0) * 10_000.0 if prior > 0 else 0.0

    def _float_or_none(self, value) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _float(self, value) -> float:
        parsed = self._float_or_none(value)
        return parsed if parsed is not None else 0.0

    def _force_trade_trace(self) -> bool:
        return bool(self.observability and self.observability.config.observability_always_trace_trades)
