from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.backtest.data.minute_bars import DayFrames
from src.backtest.models import BarContext, DataRequirements, Order, OrderRequest
from src.backtest.observability import ObservabilityRecorder
from src.backtest.portfolio import Portfolio
from src.strategies.long_momentum.v6.config import LongMomentumV6Config
from src.strategies.long_momentum.v6.presentation import chart_presentation


ORACLE_COLUMNS = (
    "desired_method",
    "signal",
    "horizon_bars",
    "score",
    "expected_profit",
    "expected_drawdown",
    "best_exit_time_utc",
    "best_exit_price",
    "oracle_long_supervision",
    "oracle_short_supervision",
    "oracle_long_supervision_score",
    "oracle_short_supervision_score",
    "oracle_long_enter_signal",
    "oracle_long_exit_signal",
    "oracle_short_enter_signal",
    "oracle_short_exit_signal",
    "oracle_long_enter_score",
    "oracle_long_exit_score",
    "oracle_short_enter_score",
    "oracle_short_exit_score",
    "long_expected_profit",
    "short_expected_profit",
    "long_exit_realized_profit",
    "short_exit_realized_profit",
    "long_drawdown_before_best",
    "short_adverse_before_best",
)


@dataclass
class LongMomentumV6SymbolState:
    ticker: str
    last_timestamp: datetime | None = None
    row: dict[str, Any] = field(default_factory=dict)


class LongMomentumV6Strategy:
    name = "long_momentum"

    def __init__(self, config: LongMomentumV6Config | None = None):
        self.config = config or LongMomentumV6Config()
        self.session_date = None
        self.states: dict[str, LongMomentumV6SymbolState] = {}
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
            supervision_groups=("oracle",),
            required_columns=(
                "ticker",
                "bar_time_market",
                "minute_of_day",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "spread",
            ),
            decision_current_columns=ORACLE_COLUMNS,
        )

    def chart_presentation(self) -> dict:
        return chart_presentation()

    def prepare_day(self, frames: DayFrames, portfolio: Portfolio) -> Any:
        self.session_date = frames.session_date
        self.states = {}
        self.entry_order_metadata = {}
        self.position_meta = {}
        missing = [column for column in ORACLE_COLUMNS if column not in frames.event_frame.columns]
        if missing:
            shown = ", ".join(missing[:8])
            suffix = " ..." if len(missing) > 8 else ""
            raise ValueError(
                f"Long Momentum v6 requires provider-built oracle supervision for {frames.session_date.isoformat()}; "
                f"missing columns: {shown}{suffix}. Build oracle supervision for this session first."
            )
        return frames.event_frame.filter(
            (frames.event_frame["minute_of_day"] >= self.config.trading_start_minute)
            & (frames.event_frame["minute_of_day"] < self.config.trading_end_minute)
        )

    def on_bar(self, context: BarContext, portfolio: Portfolio, pending_orders: list[Order]) -> list[OrderRequest]:
        self._update_states(context)
        active_pending_orders = [order for order in pending_orders if order.status == "OPEN"]
        requests = self._exit_requests(context, portfolio, active_pending_orders)
        rows = self._scanner_rows(context, portfolio, active_pending_orders)
        candidates = [row for row in rows if row["entry_open"]]
        self._record_scanner(context, rows, candidates, portfolio, active_pending_orders)

        pending_buy_symbols = {order.symbol for order in active_pending_orders if order.side == "BUY"}
        blocked_symbols = set(portfolio.positions) | pending_buy_symbols | {request.symbol for request in requests if request.side == "BUY"}
        available_slots = max(0, int(self.config.max_active_positions) - len(portfolio.positions) - len(pending_buy_symbols))
        available_cash = max(0.0, portfolio.cash - self.config.cash_buffer_dollars)
        for candidate in candidates:
            if available_slots <= 0 or available_cash <= 0:
                break
            symbol = str(candidate["ticker"])
            if symbol in blocked_symbols:
                continue
            request = self._entry_request(candidate, context, portfolio, available_cash)
            if request is None:
                continue
            requests.append(request)
            blocked_symbols.add(symbol)
            available_slots -= 1
            available_cash -= self._estimated_buy_cost(request.quantity, self._float(request.limit_price))
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
                state = LongMomentumV6SymbolState(ticker=ticker)
                self.states[ticker] = state
            state.last_timestamp = context.timestamp
            state.row = dict(raw)

    def _scanner_rows(self, context: BarContext, portfolio: Portfolio, pending_orders: list[Order]) -> list[dict]:
        pending_symbols = {order.symbol for order in pending_orders if order.status == "OPEN"}
        rows: list[dict] = []
        for raw in context.updates.iter_rows(named=True):
            row = dict(raw)
            symbol = str(row.get("ticker") or "").upper()
            row["ticker"] = symbol
            row["timestamp"] = context.timestamp
            row["session_date"] = self.session_date.isoformat() if self.session_date else ""
            row["oracle_entry_score"] = self._float(row.get("oracle_long_supervision_score") or row.get("oracle_long_enter_score"))
            row["oracle_exit_score"] = self._float(row.get("oracle_long_exit_score"))
            row["oracle_expected_profit"] = self._float(row.get("long_expected_profit") or row.get("expected_profit"))
            row["oracle_drawdown_before_best"] = self._float(row.get("long_drawdown_before_best") or row.get("expected_drawdown"))
            row["oracle_exit_realized_profit"] = self._float(row.get("long_exit_realized_profit"))
            row["entry_open"] = self._entry_open(row)
            row["scanner_score"] = row["oracle_entry_score"] * 1_000_000.0 + max(0.0, row["oracle_expected_profit"]) * 100_000.0
            row["rank"] = 0
            row["held_quantity"] = portfolio.positions[symbol].quantity if symbol in portfolio.positions else 0
            row["open_positions"] = len(portfolio.positions)
            row["status"] = self._scanner_status(row, symbol, portfolio, pending_symbols)
            row["entry_state"] = "entry_open" if row["entry_open"] else self._entry_block_reason(row)
            rows.append(row)
        rows.sort(
            key=lambda row: (
                bool(row.get("entry_open")),
                self._float(row.get("oracle_entry_score")),
                self._float(row.get("oracle_expected_profit")),
                self._float(row.get("last_recent_dollar_volume_5")),
            ),
            reverse=True,
        )
        entry_rank = 0
        for rank, row in enumerate(rows, start=1):
            row["rank"] = rank
            if row["entry_open"]:
                entry_rank += 1
                row["entry_rank"] = entry_rank
            else:
                row["entry_rank"] = None
        return rows

    def _entry_open(self, row: dict) -> bool:
        price = self._entry_price(row)
        if price < self.config.min_price or price > self.config.max_price:
            return False
        if not self._bool(row.get("oracle_long_enter_signal")):
            return False
        if self._float(row.get("oracle_entry_score")) < self.config.min_oracle_entry_score:
            return False
        if self._float(row.get("oracle_expected_profit")) < self.config.min_oracle_expected_profit:
            return False
        if abs(min(0.0, self._float(row.get("oracle_drawdown_before_best")))) > self.config.max_oracle_drawdown_before_best:
            return False
        if self.config.require_spread_ok and not self._spread_ok(row, price):
            return False
        if self.config.require_positive_expected_profit_after_fees:
            fee_drag = self._round_trip_fee_drag(price)
            if self._float(row.get("oracle_expected_profit")) <= fee_drag:
                return False
        return True

    def _entry_request(self, candidate: dict, context: BarContext, portfolio: Portfolio, available_cash: float) -> OrderRequest | None:
        symbol = str(candidate["ticker"])
        entry_price = self._entry_price(candidate)
        if entry_price <= 0:
            self._reject(context.timestamp, symbol, "entry_price", candidate)
            return None
        stop_price = self._initial_stop_price(candidate, entry_price)
        risk_pct = (entry_price - stop_price) / entry_price if entry_price > 0 else 0.0
        if risk_pct <= 0 or risk_pct > self.config.max_initial_risk_pct:
            self._reject(context.timestamp, symbol, "initial_risk", candidate)
            return None
        quantity = self._entry_quantity(candidate, entry_price, available_cash, portfolio.total_equity())
        if quantity <= 0:
            self._reject(context.timestamp, symbol, "risk_cash_or_capacity", candidate)
            return None
        rank = int(candidate.get("entry_rank") or candidate.get("rank") or 0)
        score = self._float(candidate.get("oracle_entry_score"))
        self.entry_order_metadata[symbol] = {
            "setup_rank": rank,
            "live_rank": rank,
            "setup_score": score,
            "live_score": score,
            "stop_price": stop_price,
        }
        self.position_meta[symbol] = {
            "initial_stop": stop_price,
            "entry_score": score,
            "best_exit_price": self._float(candidate.get("best_exit_price")),
            "best_exit_time_utc": candidate.get("best_exit_time_utc"),
        }
        self._trace_entry(context.timestamp, candidate, quantity, entry_price, stop_price)
        return OrderRequest(
            symbol=symbol,
            side="BUY",
            quantity=quantity,
            order_type="LIMIT",
            reason="LONG_MOMENTUM_V6_ORACLE_ENTRY",
            limit_price=entry_price,
            allow_same_bar_fill=True,
            protective_stop_price=stop_price,
            tag=(
                f"ENTRY|rule=LONG_MOMENTUM_V6|rank={rank}|qty={quantity}|entry={entry_price:.4f}"
                f"|stop={stop_price:.4f}|oracle_score={score:.2f}"
                f"|expected_profit={self._float(candidate.get('oracle_expected_profit')):.4f}"
                f"|horizon_bars={int(self._float(candidate.get('horizon_bars')))}"
            ),
        )

    def _exit_requests(self, context: BarContext, portfolio: Portfolio, pending_orders: list[Order]) -> list[OrderRequest]:
        requests: list[OrderRequest] = []
        pending_sell_symbols = {order.symbol for order in pending_orders if order.side == "SELL" and order.status == "OPEN"}
        for symbol, position in list(portfolio.positions.items()):
            if symbol in pending_sell_symbols:
                continue
            bar = context.updates_by_symbol.get(symbol)
            if bar is None:
                continue
            oracle_exit = self._oracle_exit_open(bar)
            if oracle_exit:
                exit_price = self._float(bar.get("best_exit_price")) or self._float(bar.get("open")) or self._float(bar.get("current_open"))
                self._trace_exit(context.timestamp, symbol, "LONG_MOMENTUM_V6_ORACLE_EXIT", position, bar)
                requests.append(
                    OrderRequest(
                        symbol=symbol,
                        side="SELL",
                        quantity=position.quantity,
                        order_type="LIMIT",
                        reason="LONG_MOMENTUM_V6_ORACLE_EXIT",
                        limit_price=exit_price,
                        allow_same_bar_fill=True,
                        tag=self._exit_tag("LONG_MOMENTUM_V6_ORACLE_EXIT", position, bar, exit_price),
                    )
                )
                continue
            stop_price = self._managed_stop_price(position, bar)
            requests.append(
                OrderRequest(
                    symbol=symbol,
                    side="SELL",
                    quantity=position.quantity,
                    order_type="STOP",
                    reason="LONG_MOMENTUM_V6_PROTECTIVE_STOP",
                    stop_price=stop_price,
                    allow_same_bar_fill=True,
                    expire_on_bar_close=True,
                    tag=self._exit_tag("LONG_MOMENTUM_V6_PROTECTIVE_STOP", position, bar, stop_price),
                )
            )
        return requests

    def _oracle_exit_open(self, bar: dict) -> bool:
        if self._bool(bar.get("oracle_long_exit_signal")):
            if self._float(bar.get("oracle_long_supervision_score") or bar.get("oracle_long_exit_score")) >= self.config.min_oracle_exit_score:
                if self._float(bar.get("long_exit_realized_profit")) >= self.config.min_oracle_exit_realized_profit:
                    return True
        if self._bool(bar.get("oracle_short_enter_signal")) or self._bool(bar.get("oracle_short_supervision")):
            return self._float(bar.get("oracle_short_supervision_score")) >= self.config.short_supervision_exit_score
        return False

    def _entry_quantity(self, row: dict, price: float, available_cash: float, equity: float) -> int:
        if price <= 0 or available_cash <= 0:
            return 0
        budget = min(available_cash, max(0.0, equity) * max(0.0, self.config.capital_fraction_per_trade))
        per_share_cost = price + max(0.0, self.config.sizing_fee_per_share)
        cash_quantity = int((budget - self.config.sizing_min_fee) / per_share_cost) if per_share_cost > 0 else 0
        capacity = int(self._float(row.get("max_entry_qty") or row.get("last_quote_ask_size") or row.get("max_fill_qty") or cash_quantity))
        if capacity < self.config.min_entry_capacity:
            return 0
        return max(0, min(cash_quantity, capacity))

    def _initial_stop_price(self, row: dict, entry_price: float) -> float:
        floor = entry_price * (1.0 - max(0.0, self.config.max_initial_risk_pct))
        candidates = [
            self._float(row.get("last_3_candle_low_price")),
            self._float(row.get("last_low")),
            self._float(row.get("last_vwap")),
            floor,
        ]
        valid = [value for value in candidates if value > 0 and value < entry_price]
        if valid:
            return max(0.01, max(valid))
        return max(0.01, entry_price - self.config.stop_offset_dollars)

    def _managed_stop_price(self, position, bar: dict) -> float:
        stop = position.stop_price
        last_close = self._float(bar.get("last_close") or bar.get("close"))
        if last_close <= 0:
            return stop
        unrealized_return = (last_close / position.entry_price) - 1.0 if position.entry_price > 0 else 0.0
        if unrealized_return >= self.config.breakeven_activation_return:
            stop = max(stop, position.entry_price)
        if unrealized_return >= self.config.trail_activation_return:
            trail = last_close * (1.0 - max(0.0, self.config.trail_buffer_pct))
            stop = max(stop, trail)
        return max(0.01, min(stop, last_close - 0.0001))

    def _entry_price(self, row: dict) -> float:
        return self._float(row.get("current_open") if row.get("current_open") is not None else row.get("open"))

    def _spread_ok(self, row: dict, price: float) -> bool:
        spread = self._float(row.get("last_spread") if row.get("last_spread") is not None else row.get("spread"))
        if spread <= 0:
            return True
        if price < 5.0:
            return spread <= self.config.max_spread_below_5 + 1e-9
        return spread <= self.config.max_spread_5_to_10 + 1e-9

    def _round_trip_fee_drag(self, price: float) -> float:
        if price <= 0:
            return 0.0
        return (2.0 * max(0.0, self.config.sizing_fee_per_share)) / price

    def _scanner_status(self, row: dict, ticker: str, portfolio: Portfolio, pending_symbols: set[str]) -> str:
        if ticker in portfolio.positions:
            return "held"
        if ticker in pending_symbols:
            return "pending"
        return "eligible" if row.get("entry_open") else "blocked"

    def _entry_block_reason(self, row: dict) -> str:
        price = self._entry_price(row)
        if price < self.config.min_price:
            return "price_low"
        if price > self.config.max_price:
            return "price_high"
        if not self._bool(row.get("oracle_long_enter_signal")):
            return "oracle_not_enter"
        if self._float(row.get("oracle_entry_score")) < self.config.min_oracle_entry_score:
            return "oracle_entry_score"
        if self._float(row.get("oracle_expected_profit")) < self.config.min_oracle_expected_profit:
            return "oracle_expected_profit"
        if abs(min(0.0, self._float(row.get("oracle_drawdown_before_best")))) > self.config.max_oracle_drawdown_before_best:
            return "oracle_drawdown"
        if self.config.require_spread_ok and not self._spread_ok(row, price):
            return "spread"
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
        self.observability.scanner(timestamp=context.timestamp, rows=rows, score_key="oracle_entry_score", stage="long_momentum_v6_scanner")

    def _trace_entry(self, timestamp: datetime, candidate: dict, quantity: int, entry_price: float, stop_price: float) -> None:
        self.signal_events.append(
            {
                "timestamp": timestamp,
                "ticker": candidate["ticker"],
                "event": "ENTRY_INTENT",
                "rank": candidate.get("entry_rank") or candidate.get("rank"),
                "quantity": quantity,
                "entry": entry_price,
                "stop": stop_price,
                "oracle_score": candidate.get("oracle_entry_score"),
                "oracle_expected_profit": candidate.get("oracle_expected_profit"),
            }
        )

    def _trace_exit(self, timestamp: datetime, symbol: str, reason: str, position, bar: dict) -> None:
        self.signal_events.append(
            {
                "timestamp": timestamp,
                "ticker": symbol,
                "event": "EXIT_INTENT",
                "reason": reason,
                "quantity": position.quantity,
                "entry_price": position.entry_price,
                "best_exit_price": bar.get("best_exit_price"),
                "oracle_exit_score": bar.get("oracle_long_supervision_score") or bar.get("oracle_long_exit_score"),
                "oracle_exit_profit": bar.get("long_exit_realized_profit"),
            }
        )

    def _reject(self, timestamp: datetime, symbol: str, reason: str, candidate: dict) -> None:
        self.rejection_events.append(
            {
                "timestamp": timestamp,
                "ticker": symbol,
                "reject_reason": reason,
                "rank": candidate.get("entry_rank") or candidate.get("rank"),
                "oracle_score": candidate.get("oracle_entry_score"),
                "oracle_expected_profit": candidate.get("oracle_expected_profit"),
            }
        )

    def _exit_tag(self, reason: str, position, bar: dict, price: float) -> str:
        return (
            f"EXIT|reason={reason}|price={price:.4f}|entry={position.entry_price:.4f}"
            f"|oracle_score={self._float(bar.get('oracle_long_supervision_score') or bar.get('oracle_long_exit_score')):.2f}"
            f"|oracle_profit={self._float(bar.get('long_exit_realized_profit')):.4f}"
        )

    def _estimated_buy_cost(self, quantity: int, price: float) -> float:
        if quantity <= 0 or price <= 0:
            return 0.0
        fee = max(self.config.sizing_min_fee, quantity * self.config.sizing_fee_per_share)
        return quantity * price + fee

    def _bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y"}
        return bool(value)

    def _float(self, value: Any) -> float:
        try:
            if value is None:
                return 0.0
            return float(value)
        except (TypeError, ValueError):
            return 0.0
