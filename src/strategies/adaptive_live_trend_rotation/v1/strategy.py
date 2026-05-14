from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from math import exp, log1p
from typing import Any

import polars as pl

from src.backtest.data.minute_bars import DayFrames
from src.backtest.models import BarContext, DataRequirements, Order, OrderRequest
from src.backtest.observability import ObservabilityRecorder
from src.backtest.portfolio import Portfolio
from src.strategies.adaptive_live_trend_rotation.v1.config import AdaptiveLiveTrendRotationConfig
from src.strategies.adaptive_live_trend_rotation.v1.presentation import chart_presentation


@dataclass
class SymbolMomentumState:
    ticker: str
    first_close: float = 0.0
    last_close: float = 0.0
    last_timestamp: datetime | None = None
    session_volume: float = 0.0
    session_dollar_volume: float = 0.0
    session_transactions: float = 0.0
    cumulative_macd_pressure_bps: float = 0.0
    recent_closes: deque[float] = field(default_factory=deque)
    recent_volume: deque[float] = field(default_factory=deque)
    recent_dollar_volume: deque[float] = field(default_factory=deque)
    recent_transactions: deque[float] = field(default_factory=deque)
    recent_macd_pressure: deque[float] = field(default_factory=deque)
    row: dict[str, Any] = field(default_factory=dict)


class AdaptiveLiveTrendRotationStrategy:
    name = "adaptive_live_trend_rotation"

    def __init__(self, config: AdaptiveLiveTrendRotationConfig | None = None):
        self.config = config or AdaptiveLiveTrendRotationConfig()
        self.session_date = None
        self.states: dict[str, SymbolMomentumState] = {}
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
            context_feature_groups={"5m": ("momentum",)},
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
        self.entry_order_metadata = {}
        self.position_meta = {}
        return self._tradable_frame(frames.event_frame)

    def on_bar(self, context: BarContext, portfolio: Portfolio, pending_orders: list[Order]) -> list[OrderRequest]:
        self._update_states(context)
        rows = self._scanner_rows(context, portfolio, pending_orders)
        candidates = [row for row in rows if row["entry_open"]]
        self._record_scanner(context, rows, candidates, portfolio, pending_orders)

        requests: list[OrderRequest] = []
        requests.extend(self._exit_requests(context, portfolio))

        pending_symbols = {order.symbol for order in pending_orders if order.status == "OPEN"}
        exiting_symbols = {request.symbol for request in requests if request.side == "SELL"}
        effective_positions = {symbol for symbol in portfolio.positions if symbol not in exiting_symbols}
        buy_requests = self._rotation_and_entry_requests(
            context=context,
            portfolio=portfolio,
            candidates=candidates,
            pending_symbols=pending_symbols,
            effective_positions=effective_positions,
            exiting_symbols=exiting_symbols,
        )
        requests.extend(buy_requests)
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

    def _tradable_frame(self, frame: pl.DataFrame) -> pl.DataFrame:
        return frame.filter(
            (pl.col("minute_of_day") >= self.config.trading_start_minute)
            & (pl.col("minute_of_day") < self.config.trading_end_minute)
        )

    def _update_states(self, context: BarContext) -> None:
        max_window = max(self.config.liquidity_window_minutes, self.config.recent_return_lookback_minutes) + 1
        for bar in context.updates.iter_rows(named=True):
            ticker = str(bar["ticker"])
            close = float(bar.get("close") or 0.0)
            if close <= 0:
                continue
            state = self.states.get(ticker)
            if state is None:
                state = SymbolMomentumState(
                    ticker=ticker,
                    recent_closes=deque(maxlen=max_window),
                    recent_volume=deque(maxlen=max_window),
                    recent_dollar_volume=deque(maxlen=max_window),
                    recent_transactions=deque(maxlen=max_window),
                    recent_macd_pressure=deque(maxlen=max_window),
                )
                self.states[ticker] = state
            if state.first_close <= 0:
                state.first_close = close

            volume = float(bar.get("volume") or 0.0)
            transactions = float(bar.get("transactions") or 0.0)
            dollar_volume = close * volume
            macd_hist = self._float_or_none(bar.get("macd_hist"))
            if macd_hist is None and bar.get("macd_line") is not None and bar.get("macd_signal") is not None:
                macd_hist = float(bar["macd_line"]) - float(bar["macd_signal"])
            macd_pressure_bps = (float(macd_hist or 0.0) / close) * 10_000.0

            state.last_close = close
            state.last_timestamp = context.timestamp
            state.session_volume += volume
            state.session_dollar_volume += dollar_volume
            state.session_transactions += transactions
            state.cumulative_macd_pressure_bps += macd_pressure_bps
            state.recent_closes.append(close)
            state.recent_volume.append(volume)
            state.recent_dollar_volume.append(dollar_volume)
            state.recent_transactions.append(transactions)
            state.recent_macd_pressure.append(macd_pressure_bps)
            state.row = dict(bar)

    def _scanner_rows(self, context: BarContext, portfolio: Portfolio, pending_orders: list[Order]) -> list[dict]:
        rows = []
        pending_symbols = {order.symbol for order in pending_orders if order.status == "OPEN"}
        for ticker, state in self.states.items():
            if state.last_timestamp != context.timestamp:
                continue
            bar = state.row
            score_fields = self._score_state(state, bar)
            entry_open, entry_state = self._entry_open(state, bar, score_fields, portfolio, pending_symbols)
            position = portfolio.positions.get(ticker)
            row = {
                "session_date": self.session_date.isoformat() if self.session_date else "",
                "timestamp": context.timestamp,
                "ticker": ticker,
                "entry_open": entry_open,
                "entry_state": entry_state,
                "status": "held" if position else ("pending" if ticker in pending_symbols else ("eligible" if entry_open else "blocked")),
                "price": float(bar["close"]),
                "volume": float(bar.get("volume") or 0.0),
                "transactions": float(bar.get("transactions") or 0.0),
                "held_quantity": position.quantity if position else 0,
                "open_positions": len(portfolio.positions),
                "macd_line_5m": bar.get("macd_line_5m"),
                "macd_signal_5m": bar.get("macd_signal_5m"),
                "macd_hist_5m": bar.get("macd_hist_5m"),
                "tema9_5m": bar.get("tema9_5m"),
                "tema20_5m": bar.get("tema20_5m"),
                **score_fields,
            }
            rows.append(row)

        rows.sort(key=lambda item: float(item["momentum_score"]), reverse=True)
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

    def _score_state(self, state: SymbolMomentumState, bar: dict) -> dict:
        close = float(bar["close"])
        prior_close = self._prior_close(state)
        session_return_bps = ((close / state.first_close) - 1.0) * 10_000.0 if state.first_close > 0 else 0.0
        recent_return_bps = ((close / prior_close) - 1.0) * 10_000.0 if prior_close > 0 else 0.0
        recent_dollar_volume = sum(list(state.recent_dollar_volume)[-self.config.liquidity_window_minutes :])
        recent_volume = sum(list(state.recent_volume)[-self.config.liquidity_window_minutes :])
        recent_transactions = sum(list(state.recent_transactions)[-self.config.liquidity_window_minutes :])
        recent_macd_pressure_bps = sum(list(state.recent_macd_pressure)[-self.config.recent_return_lookback_minutes :])
        vwap = self._float_or_none(bar.get("vwap"))
        vwap_distance_bps = ((close / vwap) - 1.0) * 10_000.0 if vwap and vwap > 0 else 0.0
        tema_spread_bps = self._tema_spread_bps(bar, close)
        volume_score = log1p(max(0.0, recent_dollar_volume) / max(self.config.min_recent_dollar_volume, 1.0)) * 10.0
        overextension_penalty = max(0.0, vwap_distance_bps - self.config.max_vwap_extension_bps)
        momentum_score = (
            self.config.session_return_weight * session_return_bps
            + self.config.recent_return_weight * recent_return_bps
            + self.config.macd_pressure_weight * state.cumulative_macd_pressure_bps
            + self.config.tema_spread_weight * tema_spread_bps
            + self.config.volume_weight * volume_score
            - self.config.overextension_penalty_weight * overextension_penalty
        )
        return {
            "momentum_score": momentum_score,
            "live_score": momentum_score,
            "setup_score": momentum_score,
            "session_return_bps": session_return_bps,
            "recent_return_bps": recent_return_bps,
            "cumulative_macd_pressure_bps": state.cumulative_macd_pressure_bps,
            "recent_macd_pressure_bps": recent_macd_pressure_bps,
            "tema_spread_bps": tema_spread_bps,
            "vwap_distance_bps": vwap_distance_bps,
            "volume_score": volume_score,
            "overextension_penalty": overextension_penalty,
            "session_volume": state.session_volume,
            "session_dollar_volume": state.session_dollar_volume,
            "recent_volume": recent_volume,
            "recent_dollar_volume": recent_dollar_volume,
            "recent_transactions": recent_transactions,
        }

    def _entry_open(
        self,
        state: SymbolMomentumState,
        bar: dict,
        score_fields: dict,
        portfolio: Portfolio,
        pending_symbols: set[str],
    ) -> tuple[bool, str]:
        close = float(bar["close"])
        if close < self.config.min_price:
            return False, "price_low"
        if close > self.config.max_price:
            return False, "price_high"
        if state.session_dollar_volume < self.config.min_session_dollar_volume:
            return False, "session_liquidity"
        if score_fields["recent_dollar_volume"] < self.config.min_recent_dollar_volume:
            return False, "recent_dollar_liquidity"
        if score_fields["recent_volume"] < self.config.min_recent_volume:
            return False, "recent_volume"
        if score_fields["recent_transactions"] < self.config.min_recent_transactions:
            return False, "recent_transactions"
        if score_fields["recent_return_bps"] < self.config.min_recent_return_bps:
            return False, "recent_return"
        if score_fields["momentum_score"] < self.config.min_momentum_score:
            return False, "momentum_score"
        if self.config.require_price_above_vwap and score_fields["vwap_distance_bps"] <= 0:
            return False, "below_vwap"
        if not self._macd_open(bar):
            return False, "macd_closed"
        if not self._tema_open(bar):
            return False, "tema_closed"
        if state.ticker in pending_symbols:
            return False, "pending_order"
        return True, "entry_open"

    def _rotation_and_entry_requests(
        self,
        *,
        context: BarContext,
        portfolio: Portfolio,
        candidates: list[dict],
        pending_symbols: set[str],
        effective_positions: set[str],
        exiting_symbols: set[str],
    ) -> list[OrderRequest]:
        requests: list[OrderRequest] = []
        top_candidates = candidates[: max(1, self.config.top_n)]
        if not top_candidates:
            return requests

        rotation_sell_symbol = None
        open_slots = max(0, self.config.max_active_positions - len(effective_positions))
        if open_slots <= 0:
            rotation = self._rotation_pair(context, portfolio, top_candidates, effective_positions, exiting_symbols)
            if rotation:
                new_candidate, weak_symbol = rotation
                weak_position = portfolio.positions[weak_symbol]
                rotation_sell_symbol = weak_symbol
                open_slots = 1
                requests.append(
                    OrderRequest(
                        symbol=weak_symbol,
                        side="SELL",
                        quantity=weak_position.quantity,
                        order_type="MARKET",
                        reason="ROTATE_OUT",
                        tag=self._exit_tag("ROTATE_OUT", weak_position, context.latest_by_symbol.get(weak_symbol), {"rotate_to": new_candidate["ticker"]}),
                    )
                )

        if open_slots <= 0:
            return requests

        rank_weights = self._rank_weights(top_candidates)
        expected_cash = portfolio.cash + self._expected_sale_value(rotation_sell_symbol, portfolio, context)
        reserved_cash = portfolio.total_equity(context.latest_by_symbol) * self.config.cash_reserve_pct
        available_cash = max(0.0, expected_cash - reserved_cash)
        buys_used = 0
        for candidate in top_candidates:
            symbol = candidate["ticker"]
            if buys_used >= open_slots:
                break
            if symbol in effective_positions or symbol in pending_symbols:
                continue
            if symbol == rotation_sell_symbol:
                continue
            bar = context.updates_by_symbol.get(symbol)
            if bar is None:
                continue
            quantity, stop_price, risk_per_share, target_capital = self._entry_size(
                candidate=candidate,
                rank_weight=rank_weights.get(symbol, 0.0),
                available_cash=available_cash,
                portfolio=portfolio,
                context=context,
            )
            if quantity <= 0:
                self._reject(context.timestamp, symbol, "quantity", candidate)
                continue
            available_cash -= quantity * float(bar["close"])
            buys_used += 1
            self.entry_order_metadata[symbol] = {
                "setup_rank": candidate.get("rank") or 0,
                "live_rank": candidate.get("entry_rank") or candidate.get("rank") or 0,
                "setup_score": candidate["momentum_score"],
                "live_score": candidate["momentum_score"],
                "stop_price": stop_price,
                "momentum_score": candidate["momentum_score"],
            }
            self.position_meta[symbol] = {
                "entry_score": candidate["momentum_score"],
                "initial_r": risk_per_share,
                "trailing_stop": stop_price,
                "target_capital": target_capital,
            }
            self._trace_entry(context.timestamp, candidate, quantity, stop_price, risk_per_share, target_capital)
            requests.append(
                OrderRequest(
                    symbol=symbol,
                    side="BUY",
                    quantity=quantity,
                    order_type="MARKET",
                    reason="LIVE_TREND_ROTATION",
                    stop_price=stop_price,
                    tag=(
                        f"ENTRY|rule=ADAPTIVE_LIVE_TREND_ROTATION|rank={candidate.get('entry_rank') or candidate.get('rank')}"
                        f"|qty={quantity}|entry={float(bar['close']):.2f}|stop={stop_price:.2f}|R={risk_per_share:.4f}"
                        f"|score={candidate['momentum_score']:.1f}|recent={candidate['recent_return_bps']:.1f}"
                        f"|macd={candidate['cumulative_macd_pressure_bps']:.1f}"
                    ),
                )
            )
        return requests

    def _exit_requests(self, context: BarContext, portfolio: Portfolio) -> list[OrderRequest]:
        requests = []
        for symbol, position in list(portfolio.positions.items()):
            bar = context.updates_by_symbol.get(symbol)
            if bar is None:
                continue
            meta = self._position_meta(symbol, position)
            trailing_stop = self._updated_trailing_stop(position, bar, meta)
            meta["trailing_stop"] = trailing_stop
            position.stop_price = max(position.stop_price, trailing_stop)
            reason = None
            close = float(bar["close"])
            if close <= trailing_stop:
                reason = "TRAILING_STOP"
            elif self._macd_closed(bar):
                reason = "MACD_CLOSE"
            elif self._tema_closed(bar):
                reason = "TEMA_CLOSE"
            if reason is None:
                continue
            self._trace_exit(context.timestamp, symbol, reason, position, bar, meta)
            requests.append(
                OrderRequest(
                    symbol=symbol,
                    side="SELL",
                    quantity=position.quantity,
                    order_type="MARKET",
                    reason=reason,
                    tag=self._exit_tag(reason, position, bar, meta),
                )
            )
        return requests

    def _rotation_pair(
        self,
        context: BarContext,
        portfolio: Portfolio,
        top_candidates: list[dict],
        effective_positions: set[str],
        exiting_symbols: set[str],
    ) -> tuple[dict, str] | None:
        weak_symbol = self._weakest_rotatable_position(context, portfolio, exiting_symbols)
        if weak_symbol is None:
            return None
        weak_score = self._current_position_score(weak_symbol)
        for candidate in top_candidates:
            symbol = candidate["ticker"]
            if symbol in effective_positions:
                continue
            if candidate["momentum_score"] > weak_score + self.config.replacement_score_buffer:
                self._trace_rotation(context.timestamp, candidate, weak_symbol, weak_score)
                return candidate, weak_symbol
        return None

    def _weakest_rotatable_position(self, context: BarContext, portfolio: Portfolio, exiting_symbols: set[str]) -> str | None:
        weakest = None
        for symbol, position in portfolio.positions.items():
            if symbol in exiting_symbols:
                continue
            bar = context.latest_by_symbol.get(symbol)
            if bar is None:
                continue
            held_minutes = (context.timestamp - position.entry_time).total_seconds() / 60.0
            if held_minutes < self.config.rotation_min_hold_minutes:
                continue
            score = self._current_position_score(symbol)
            meta = self._position_meta(symbol, position)
            r_multiple = self._open_r_multiple(position, bar, meta)
            entry_score = float(meta.get("entry_score") or position.live_score)
            not_progressing = (
                score < entry_score - self.config.non_progress_score_decay
                or r_multiple < self.config.min_progress_r_after_hold
                or not self._entry_state_open(bar)
            )
            if not not_progressing:
                continue
            candidate = (score, symbol)
            if weakest is None or candidate[0] < weakest[0]:
                weakest = candidate
        return weakest[1] if weakest else None

    def _entry_size(
        self,
        *,
        candidate: dict,
        rank_weight: float,
        available_cash: float,
        portfolio: Portfolio,
        context: BarContext,
    ) -> tuple[int, float, float, float]:
        price = float(candidate["price"])
        risk_per_share = self._initial_risk_per_share(price)
        stop_price = max(0.01, price - risk_per_share)
        total_equity = portfolio.total_equity(context.latest_by_symbol)
        target_capital = total_equity * self.config.max_gross_exposure_pct * rank_weight
        cash_budget = min(max(0.0, available_cash), target_capital)
        quantity_by_cash = int(cash_budget / price) if price > 0 else 0
        quantity_by_risk = int((total_equity * self.config.risk_per_trade_pct) / risk_per_share) if risk_per_share > 0 else 0
        return max(0, min(quantity_by_cash, quantity_by_risk)), stop_price, risk_per_share, target_capital

    def _initial_risk_per_share(self, price: float) -> float:
        raw = max(price * self.config.initial_risk_pct, self.config.min_initial_risk_dollars)
        return min(raw, price * self.config.max_initial_risk_pct)

    def _updated_trailing_stop(self, position, bar: dict, meta: dict) -> float:
        initial_r = float(meta.get("initial_r") or abs(position.entry_price - position.stop_price))
        if initial_r <= 0:
            return position.stop_price
        current_stop = max(float(meta.get("trailing_stop") or position.stop_price), position.stop_price)
        max_price = max(position.max_price, float(bar.get("high") or bar["close"]))
        max_profit = max_price - position.entry_price
        if max_profit < self.config.trailing_activation_r * initial_r:
            return current_stop
        lock_stop = position.entry_price + (self.config.trailing_lock_r * initial_r)
        giveback_stop = max_price - (self.config.trailing_giveback_r * initial_r)
        return max(current_stop, lock_stop, giveback_stop)

    def _rank_weights(self, candidates: list[dict]) -> dict[str, float]:
        selected = candidates[: max(1, self.config.top_n)]
        raw = {row["ticker"]: exp(-self.config.rank_decay * index) for index, row in enumerate(selected)}
        total = sum(raw.values())
        return {symbol: weight / total for symbol, weight in raw.items()} if total > 0 else {}

    def _expected_sale_value(self, symbol: str | None, portfolio: Portfolio, context: BarContext) -> float:
        if not symbol:
            return 0.0
        position = portfolio.positions.get(symbol)
        bar = context.latest_by_symbol.get(symbol)
        if position is None or bar is None:
            return 0.0
        return position.quantity * float(bar["close"]) * 0.98

    def _position_meta(self, symbol: str, position) -> dict:
        meta = self.position_meta.get(symbol)
        if meta is None:
            risk = max(0.01, abs(position.entry_price - position.stop_price))
            meta = {
                "entry_score": position.live_score,
                "initial_r": risk,
                "trailing_stop": position.stop_price,
            }
            self.position_meta[symbol] = meta
        return meta

    def _current_position_score(self, symbol: str) -> float:
        state = self.states.get(symbol)
        if state is None or not state.row:
            meta = self.position_meta.get(symbol, {})
            return float(meta.get("entry_score") or 0.0)
        return float(self._score_state(state, state.row)["momentum_score"])

    def _open_r_multiple(self, position, bar: dict, meta: dict) -> float:
        initial_r = float(meta.get("initial_r") or abs(position.entry_price - position.stop_price))
        if initial_r <= 0:
            return 0.0
        return (float(bar["close"]) - position.entry_price) / initial_r

    def _prior_close(self, state: SymbolMomentumState) -> float:
        closes = list(state.recent_closes)
        lookback = self.config.recent_return_lookback_minutes
        if len(closes) <= lookback:
            return closes[0] if closes else 0.0
        return closes[-lookback - 1]

    def _macd_open(self, bar: dict) -> bool:
        return (
            bool(bar.get("macd_ready_5m"))
            and bar.get("macd_line_5m") is not None
            and bar.get("macd_signal_5m") is not None
            and bar.get("macd_hist_5m") is not None
            and float(bar["macd_line_5m"]) > float(bar["macd_signal_5m"])
            and float(bar["macd_line_5m"]) > 0
            and float(bar["macd_hist_5m"]) > 0
        )

    def _macd_closed(self, bar: dict) -> bool:
        return not self._macd_open(bar)

    def _tema_open(self, bar: dict) -> bool:
        close = float(bar.get("close") or 0.0)
        return (
            bool(bar.get("tema_ready_5m"))
            and bar.get("tema9_5m") is not None
            and bar.get("tema20_5m") is not None
            and float(bar["tema9_5m"]) > float(bar["tema20_5m"]) + (close * self.config.tema_entry_buffer_pct)
        )

    def _tema_closed(self, bar: dict) -> bool:
        close = float(bar.get("close") or 0.0)
        return (
            bool(bar.get("tema_ready_5m"))
            and bar.get("tema9_5m") is not None
            and bar.get("tema20_5m") is not None
            and float(bar["tema20_5m"]) + (close * self.config.tema_exit_buffer_pct) > float(bar["tema9_5m"])
        )

    def _entry_state_open(self, bar: dict) -> bool:
        return self._macd_open(bar) and self._tema_open(bar)

    def _tema_spread_bps(self, bar: dict, close: float) -> float:
        if close <= 0 or bar.get("tema9") is None or bar.get("tema20") is None:
            return 0.0
        return ((float(bar["tema9"]) - float(bar["tema20"])) / close) * 10_000.0

    def _float_or_none(self, value) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _record_scanner(
        self,
        context: BarContext,
        rows: list[dict],
        candidates: list[dict],
        portfolio: Portfolio,
        pending_orders: list[Order],
    ) -> None:
        captured = rows[: max(25, self.config.top_n * 4)]
        self.live_rankings.extend(captured)
        self.scanner_snapshots.append(
            {
                "timestamp": context.timestamp,
                "session_date": self.session_date.isoformat() if self.session_date else "",
                "candidate_count": len(candidates),
                "scanned_count": len(rows),
                "selected_count": min(len(candidates), self.config.top_n),
            }
        )
        if not self.observability or not rows:
            return
        self.observability.scanner(
            timestamp=context.timestamp,
            rows=rows,
            score_key="momentum_score",
            stage="live_trend_scanner",
        )
        self.observability.state(
            timestamp=context.timestamp,
            scope="strategy",
            state={
                "scanned_count": len(rows),
                "entry_open_count": len(candidates),
                "open_positions": len(portfolio.positions),
                "pending_orders": len([order for order in pending_orders if order.status == "OPEN"]),
                "top_n": self.config.top_n,
            },
        )

    def _trace_entry(self, timestamp: datetime, candidate: dict, quantity: int, stop_price: float, risk_per_share: float, target_capital: float) -> None:
        self.signal_events.append(
            {
                "timestamp": timestamp,
                "ticker": candidate["ticker"],
                "event": "ENTRY_INTENT",
                "rank": candidate.get("entry_rank") or candidate.get("rank"),
                "momentum_score": candidate["momentum_score"],
                "quantity": quantity,
                "stop": stop_price,
            }
        )
        if not self.observability:
            return
        self.observability.trace(
            timestamp=timestamp,
            ticker=candidate["ticker"],
            stage="order_request",
            event_type="entry_intent",
            decision="submit_order",
            reason_code="LIVE_TREND_ROTATION",
            reason="Entry-open candidate ranked inside the live rotation set",
            values={
                "quantity": quantity,
                "price": candidate["price"],
                "stop": stop_price,
                "initial_r": risk_per_share,
                "target_capital": target_capital,
                "momentum_score": candidate["momentum_score"],
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
                "price": float(bar["close"]),
                "entry_price": position.entry_price,
                "trailing_stop": meta.get("trailing_stop"),
                "initial_r": meta.get("initial_r"),
                "momentum_score": self._current_position_score(symbol),
            },
            force=self._force_trade_trace(),
        )

    def _trace_rotation(self, timestamp: datetime, candidate: dict, weak_symbol: str, weak_score: float) -> None:
        if not self.observability:
            return
        self.observability.trace(
            timestamp=timestamp,
            ticker=candidate["ticker"],
            stage="portfolio_rotation",
            event_type="rotation_intent",
            decision="rotate",
            reason_code="STRONGER_CANDIDATE",
            reason="A higher-ranked entry-open candidate is stronger than a weak held position",
            values={
                "rotate_from": weak_symbol,
                "rotate_to": candidate["ticker"],
                "new_score": candidate["momentum_score"],
                "weak_score": weak_score,
                "score_advantage": candidate["momentum_score"] - weak_score,
            },
            force=self._force_trade_trace(),
        )

    def _reject(self, timestamp: datetime, symbol: str, reason: str, candidate: dict) -> None:
        self.rejection_events.append(
            {
                "timestamp": timestamp,
                "ticker": symbol,
                "reject_reason": reason,
                "momentum_score": candidate.get("momentum_score"),
                "rank": candidate.get("entry_rank") or candidate.get("rank"),
            }
        )

    def _exit_tag(self, reason: str, position, bar: dict | None, meta: dict) -> str:
        price = float(bar["close"]) if bar is not None else position.entry_price
        return (
            f"EXIT|reason={reason}|price={price:.2f}|stop={float(meta.get('trailing_stop') or position.stop_price):.2f}"
            f"|R={float(meta.get('initial_r') or 0.0):.4f}|maxp={position.max_price:.2f}"
            f"|score={self._current_position_score(position.symbol):.1f}"
        )

    def _force_trade_trace(self) -> bool:
        return bool(self.observability and self.observability.config.observability_always_trace_trades)
