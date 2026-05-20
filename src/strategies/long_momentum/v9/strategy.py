from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import polars as pl

from src.backtest.data.minute_bars import DayFrames
from src.backtest.models import BarContext, DataRequirements, Order, OrderRequest
from src.backtest.portfolio import Portfolio
from src.strategies.long_momentum.v3.strategy import LongMomentumV3Strategy
from src.strategies.long_momentum.v9.config import LongMomentumV9Config
from src.strategies.long_momentum.v9.presentation import chart_presentation


REQUIRED_V9_COLUMNS = (
    "last_close",
    "last_open",
    "last_return_5",
    "current_open",
    "last_volume",
    "last_transactions",
    "last_transactions_vs_prior_3",
    "last_tema9",
    "last_tema20",
    "current_open_tema9",
    "current_open_tema20",
    "last_vwap",
    "last_2_body_high",
    "current_open_above_last_2_body_high",
    "last_bearish_volume_divergence_score",
    "last_double_timeframe_bearish_volume_divergence_score",
)


@dataclass(slots=True)
class MomentumWatch:
    ticker: str
    added_timestamp: datetime
    added_last_close: float
    added_last_5m_return: float
    max_vwap: float = 0.0
    transaction_sum: float = 0.0
    transaction_count: int = 0
    entry_submitted: bool = False
    last_exit_timestamp: datetime | None = None
    last_entry_type: str = ""
    last_state: str = "watching"

    @property
    def avg_transactions_since_watchlist(self) -> float:
        if self.transaction_count <= 0:
            return 0.0
        return self.transaction_sum / self.transaction_count


class LongMomentumV9Strategy(LongMomentumV3Strategy):
    name = "long_momentum"

    def __init__(self, config: LongMomentumV9Config | None = None):
        super().__init__(config or LongMomentumV9Config())
        self.config: LongMomentumV9Config
        self.momentum_watchlist: dict[str, MomentumWatch] = {}
        self.watchlist_snapshots: list[dict] = []
        self.last_scanner_rows: list[dict] = []

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
            decision_current_columns=("current_open_tema9", "current_open_tema20"),
        )

    def chart_presentation(self) -> dict:
        return chart_presentation()

    def prepare_day(self, frames: DayFrames, portfolio: Portfolio) -> pl.DataFrame:
        self.session_date = frames.session_date
        self.states = {}
        self.entry_order_metadata = {}
        self.position_meta = {}
        self.momentum_watchlist = {}
        self.last_scanner_rows = []
        frame = frames.event_frame.filter(
            (pl.col("minute_of_day") >= self.config.trading_start_minute)
            & (pl.col("minute_of_day") < self.config.trading_end_minute)
        )
        self._validate_provider_columns(frame)
        return self._with_last_5m_return(frame)

    def artifacts(self) -> dict[str, list[dict]]:
        artifacts = super().artifacts()
        artifacts["watchlist_snapshots"] = self.watchlist_snapshots
        return artifacts

    def on_bar(self, context: BarContext, portfolio: Portfolio, pending_orders: list[Order]) -> list[OrderRequest]:
        self._update_states(context)
        self._sync_recent_fills(context)

        requests: list[OrderRequest] = []
        active_pending_orders = [order for order in pending_orders if order.status == "OPEN"]
        requests.extend(self._partial_residual_requests(context, portfolio))
        current_bar_residual_symbols = {request.symbol for request in requests}
        requests.extend(self._exit_requests(context, portfolio, active_pending_orders, current_bar_residual_symbols))

        rows = self._scanner_rows(context, portfolio, active_pending_orders)
        immediate_candidates = [row for row in rows if row.get("long_momentum_v9_immediate_entry_open")]
        reentry_candidates = [row for row in rows if row.get("long_momentum_v9_reentry_open")]
        self._record_scanner(context, rows, immediate_candidates + reentry_candidates, portfolio, active_pending_orders)

        blocked_symbols = {
            order.symbol for order in active_pending_orders if order.side == "BUY"
        } | {
            request.symbol for request in requests if request.side == "BUY"
        } | set(portfolio.positions)

        available_cash = self._available_cash_after_submitted_requests(
            portfolio=portfolio,
            requests=requests,
            context=context,
        )

        immediate_candidates = [
            row for row in immediate_candidates
            if str(row.get("ticker") or "") not in blocked_symbols
        ][: max(0, int(self.config.max_immediate_entry_candidates_per_bar))]
        if immediate_candidates and available_cash > 0:
            submitted = self._submit_entry_group(
                candidates=immediate_candidates,
                context=context,
                available_cash=available_cash,
                entry_type="IMMEDIATE_ENTRY",
            )
            requests.extend(submitted)
            blocked_symbols |= {request.symbol for request in submitted if request.side == "BUY"}
            available_cash = self._available_cash_after_submitted_requests(
                portfolio=portfolio,
                requests=requests,
                context=context,
            )

        reentry_candidates = [
            row for row in reentry_candidates
            if str(row.get("ticker") or "") not in blocked_symbols
        ][: max(0, int(self.config.max_reentry_candidates_per_bar))]
        if reentry_candidates and available_cash > 0:
            requests.extend(
                self._submit_entry_group(
                    candidates=reentry_candidates,
                    context=context,
                    available_cash=available_cash,
                    entry_type="WATCHLIST_REENTRY",
                )
            )
        self._record_watchlist_snapshot(context, portfolio)
        return requests

    def _partial_residual_requests(self, context: BarContext, portfolio: Portfolio) -> list[OrderRequest]:
        requests: list[OrderRequest] = []
        for fill in context.recent_fills:
            remaining = self._partial_remaining(fill.get("tag"))
            if remaining <= 0:
                continue
            symbol = str(fill.get("symbol") or "").upper()
            side = str(fill.get("side") or "").upper()
            if not symbol or side not in {"BUY", "SELL"}:
                continue
            row = context.updates_by_symbol.get(symbol) or context.latest_by_symbol.get(symbol)
            if row is None:
                continue
            quantity = remaining
            if side == "SELL":
                position = portfolio.positions.get(symbol)
                if position is None:
                    continue
                quantity = min(remaining, position.quantity)
            elif side == "BUY":
                if portfolio.cash <= self.config.cash_buffer_dollars:
                    continue
            if quantity <= 0:
                continue
            limit_price = self._partial_residual_limit_price(side, row)
            if limit_price <= 0:
                continue
            if side == "BUY":
                quantity = min(quantity, self._cash_quantity(limit_price, max(0.0, portfolio.cash - self.config.cash_buffer_dollars)))
                quantity = self._capped_entry_quantity(quantity)
                if quantity <= 0:
                    continue
            protective_stop_price = None
            if side == "BUY" and symbol in portfolio.positions:
                protective_stop_price = portfolio.positions[symbol].stop_price
            requests.append(
                OrderRequest(
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    order_type="LIMIT",
                    reason=f"PARTIAL_{'ENTRY' if side == 'BUY' else 'EXIT'}_REST",
                    limit_price=limit_price,
                    allow_same_bar_fill=True,
                    protective_stop_price=protective_stop_price,
                    tag=(
                        f"{'ENTRY' if side == 'BUY' else 'EXIT'}|reason=PARTIAL_{'ENTRY' if side == 'BUY' else 'EXIT'}_REST"
                        f"|qty={quantity}|limit={limit_price:.4f}|offset={self.config.limit_order_offset_dollars:.4f}"
                        f"|source_fill={fill.get('fill_id')}"
                    ),
                )
            )
        return requests

    def _partial_residual_limit_price(self, side: str, row: dict[str, Any]) -> float:
        return self._liquid_limit_price(side, row)

    def _liquid_limit_price(self, side: str, row: dict[str, Any]) -> float:
        open_price = self._bar_open(row)
        if open_price <= 0:
            return 0.0
        offset = max(0.0, self.config.limit_order_offset_dollars)
        if side == "BUY":
            return open_price + offset
        return max(0.01, open_price - offset)

    def _validate_provider_columns(self, frame: pl.DataFrame) -> None:
        missing = [column for column in REQUIRED_V9_COLUMNS if column not in frame.columns]
        if missing:
            date_text = self.session_date.isoformat() if self.session_date else "unknown session"
            raise ValueError(
                f"Long Momentum v9 requires provider-built strategy-time core, momentum, and volume/liquidity features for {date_text}; "
                f"missing columns: {', '.join(missing)}. Rebuild market data with current core, momentum, and volume_liquidity features."
            )

    def _with_last_5m_return(self, frame: pl.DataFrame) -> pl.DataFrame:
        if frame.is_empty():
            return frame
        if "last_return_5" in frame.columns:
            return frame.with_columns(
                pl.col("last_return_5").alias("last_5m_return"),
                pl.col("last_return_5").alias("long_momentum_v9_last_5m_return"),
            )
        group_columns = [column for column in ["ticker", "session_date"] if column in frame.columns]
        if not group_columns:
            group_columns = ["ticker"]
        return frame.with_columns(
            pl.when(pl.col("last_close").shift(5).over(group_columns) > 0)
            .then((pl.col("last_close") / pl.col("last_close").shift(5).over(group_columns)) - 1.0)
            .otherwise(None)
            .alias("last_5m_return")
        ).with_columns(
            pl.col("last_5m_return").alias("long_momentum_v9_last_5m_return")
        )

    def _update_states(self, context: BarContext) -> None:
        super()._update_states(context)
        for raw in context.updates.iter_rows(named=True):
            self._update_momentum_watch(context.timestamp, dict(raw))

    def _sync_recent_fills(self, context: BarContext) -> None:
        for fill in context.recent_fills:
            symbol = str(fill.get("symbol") or "").upper()
            if not symbol:
                continue
            watch = self.momentum_watchlist.get(symbol)
            if watch is None:
                continue
            side = str(fill.get("side") or "").upper()
            if side == "BUY":
                watch.entry_submitted = True
                tag = str(fill.get("tag") or "")
                if "POCKET_REENTRY" in tag:
                    watch.last_entry_type = "POCKET_REENTRY"
                elif "IMMEDIATE_ENTRY" in tag:
                    watch.last_entry_type = "IMMEDIATE_ENTRY"
                else:
                    watch.last_entry_type = "WATCHLIST_REENTRY"
                watch.last_state = "in_position"
            elif side == "SELL":
                tag = str(fill.get("tag") or "")
                watch.last_exit_timestamp = context.timestamp
                if "POCKET_PROFIT" in tag:
                    watch.entry_submitted = False
                    watch.last_entry_type = "POCKET_PROFIT"
                    watch.last_state = "pocketed_waiting_reentry"
                else:
                    watch.last_state = "watching_after_exit"

    def _update_momentum_watch(self, timestamp: datetime, row: dict[str, Any]) -> None:
        ticker = str(row.get("ticker") or "").upper()
        if not ticker:
            return
        last_close = self._float(row.get("last_close"))
        last_5m_return = self._float(row.get("last_5m_return"))
        volume = self._float(row.get("last_volume"))
        transactions = self._float(row.get("last_transactions"))
        price_eligible = self.config.min_price <= last_close <= self.config.max_price
        return_ok = last_5m_return >= self.config.min_last_5m_return
        volume_ok = volume >= self.config.min_watchlist_add_volume
        transactions_ok = transactions >= self.config.min_first_entry_transactions
        watch = self.momentum_watchlist.get(ticker)
        if watch is None and price_eligible and return_ok and volume_ok and transactions_ok:
            watch = MomentumWatch(
                ticker=ticker,
                added_timestamp=timestamp,
                added_last_close=last_close,
                added_last_5m_return=last_5m_return,
            )
            self.momentum_watchlist[ticker] = watch
            self._trace_watchlist_add(timestamp, ticker, row, watch)
        if watch is None:
            return
        last_vwap = self._float(row.get("last_vwap"))
        if last_vwap > 0:
            watch.max_vwap = max(watch.max_vwap, last_vwap)
        if transactions > 0:
            watch.transaction_sum += transactions
            watch.transaction_count += 1

    def _scanner_rows(self, context: BarContext, portfolio: Portfolio, pending_orders: list[Order]) -> list[dict]:
        if context.updates.is_empty():
            return []
        pending_symbols = {order.symbol for order in pending_orders if order.status == "OPEN"}
        rows: list[dict] = []
        for raw in context.updates.iter_rows(named=True):
            row = dict(raw)
            ticker = str(row.get("ticker") or "").upper()
            row["ticker"] = ticker
            row["timestamp"] = context.timestamp
            row["session_date"] = self.session_date.isoformat() if self.session_date else ""
            row["price"] = self._float(row.get("last_close"))
            row["held_quantity"] = portfolio.positions[ticker].quantity if ticker in portfolio.positions else 0
            row["open_positions"] = len(portfolio.positions)
            row.update(self._evaluate_v9_row(row, ticker, portfolio, pending_symbols))
            row["entry_open"] = bool(row["long_momentum_v9_entry_open"])
            row["long_momentum_entry_open"] = row["entry_open"]
            row["scanner_score"] = self._float(row.get("long_momentum_v9_last_5m_return"))
            row["status"] = self._scanner_status(row, ticker, portfolio, pending_symbols)
            row["entry_state"] = self._v9_entry_state(row)
            rows.append(row)
        rows.sort(
            key=lambda item: (
                int(item.get("long_momentum_v9_entry_priority") or 0),
                self._float(item.get("long_momentum_v9_last_5m_return")),
                self._float(item.get("last_transactions")),
            ),
            reverse=True,
        )
        for rank, row in enumerate(rows, start=1):
            row["rank"] = rank
            row["entry_rank"] = rank if row.get("entry_open") else None
        return rows

    def _evaluate_v9_row(self, row: dict[str, Any], ticker: str, portfolio: Portfolio, pending_symbols: set[str]) -> dict[str, Any]:
        watch = self.momentum_watchlist.get(ticker)
        last_close = self._float(row.get("last_close"))
        last_5m_return = self._float(row.get("last_5m_return"))
        volume = self._float(row.get("last_volume"))
        transactions = self._float(row.get("last_transactions"))
        transactions_vs_prior_3 = self._float(row.get("last_transactions_vs_prior_3"))
        price_eligible = self.config.min_price <= last_close <= self.config.max_price
        return_ok = last_5m_return >= self.config.min_last_5m_return
        watchlist_add_volume_ok = volume >= self.config.min_watchlist_add_volume
        watchlist_add_transactions_ok = transactions >= self.config.min_first_entry_transactions
        immediate_transactions_vs_prior_3_ok = transactions_vs_prior_3 >= self.config.min_first_entry_transactions_vs_prior_3
        entry_time_ok = self.config.trading_start_minute <= int(self._float(row.get("minute_of_day"))) < self.config.trading_end_minute
        pending_symbol_order = ticker in pending_symbols
        no_symbol_position = ticker not in portfolio.positions and not pending_symbol_order
        current_timestamp = row.get("timestamp")
        watch_added_this_bar = bool(watch and current_timestamp == watch.added_timestamp)
        watch_entry_ready = bool(watch and isinstance(current_timestamp, datetime) and watch.added_timestamp < current_timestamp)
        max_vwap = watch.max_vwap if watch else 0.0
        last_vwap = self._float(row.get("last_vwap"))
        last_open = self._float(row.get("last_open"))
        reentry_vwap_threshold = last_vwap * (1.0 + max(0.0, self.config.reentry_vwap_buffer_pct) / 100.0) if last_vwap > 0 else 0.0
        reentry_price_reclaim = reentry_vwap_threshold > 0 and last_close >= reentry_vwap_threshold
        reentry_last_bar_not_red = last_close >= last_open
        last_tema9 = self._float(row.get("last_tema9"))
        last_tema20 = self._float(row.get("last_tema20"))
        reentry_last_tema_open_ok = last_tema9 > 0 and last_tema20 > 0 and last_tema9 > last_tema20
        reentry_bvd_score = self._float(row.get("last_bearish_volume_divergence_score"))
        reentry_bvd_ok = reentry_bvd_score <= self.config.max_reentry_bvd_score
        current_open = self._float(row.get("current_open"))
        reentry_body_break_threshold = self._float(row.get("last_2_body_high"))
        reentry_body_break_ok = current_open > 0 and reentry_body_break_threshold > 0 and current_open > reentry_body_break_threshold
        reentry_close_minus_vwap = last_close - last_vwap if last_vwap > 0 else None
        reentry_close_minus_vwap_threshold = last_close - reentry_vwap_threshold if reentry_vwap_threshold > 0 else None
        double_bvd_exit_score = self._float(row.get("last_double_timeframe_bearish_volume_divergence_score"))
        double_bvd_exit_red_ok = last_close < last_open
        double_bvd_exit_open = double_bvd_exit_score > self.config.double_bvd_exit_score and double_bvd_exit_red_ok
        immediate_entry_open = (
            price_eligible
            and bool(watch)
            and no_symbol_position
            and entry_time_ok
            and return_ok
            and watchlist_add_volume_ok
            and watchlist_add_transactions_ok
            and immediate_transactions_vs_prior_3_ok
        )
        reentry_open = (
            price_eligible
            and bool(watch)
            and watch_entry_ready
            and no_symbol_position
            and entry_time_ok
            and reentry_price_reclaim
            and reentry_last_bar_not_red
            and reentry_last_tema_open_ok
            and reentry_bvd_ok
            and reentry_body_break_ok
            and not immediate_entry_open
        )
        return {
            "long_momentum_v9_price_eligible": price_eligible,
            "long_momentum_v9_watchlist_add_open": price_eligible and return_ok and watchlist_add_volume_ok and watchlist_add_transactions_ok,
            "long_momentum_v9_watchlist_active": watch is not None,
            "long_momentum_v9_watchlist_added_timestamp": watch.added_timestamp.isoformat() if watch else "",
            "long_momentum_v9_watchlist_added_last_close": watch.added_last_close if watch else None,
            "long_momentum_v9_watchlist_added_last_5m_return": watch.added_last_5m_return if watch else None,
            "long_momentum_v9_watchlist_added_this_bar": watch_added_this_bar,
            "long_momentum_v9_watchlist_entry_ready": watch_entry_ready,
            "long_momentum_v9_watchlist_entry_submitted": bool(watch and watch.entry_submitted),
            "long_momentum_v9_watchlist_last_entry_type": watch.last_entry_type if watch else "",
            "long_momentum_v9_watchlist_last_state": watch.last_state if watch else "",
            "long_momentum_v9_watchlist_max_vwap": max_vwap,
            "long_momentum_v9_watchlist_avg_transactions": watch.avg_transactions_since_watchlist if watch else 0.0,
            "long_momentum_v9_last_5m_return": last_5m_return,
            "long_momentum_v9_return_ok": return_ok,
            "long_momentum_v9_watchlist_add_volume_ok": watchlist_add_volume_ok,
            "long_momentum_v9_watchlist_add_transactions_ok": watchlist_add_transactions_ok,
            "long_momentum_v9_immediate_transactions_vs_prior_3_ok": immediate_transactions_vs_prior_3_ok,
            "long_momentum_v9_entry_time_ok": entry_time_ok,
            "long_momentum_v9_pending_symbol_order": pending_symbol_order,
            "long_momentum_v9_no_symbol_position": no_symbol_position,
            "long_momentum_v9_close_minus_vwap": reentry_close_minus_vwap,
            "long_momentum_v9_reentry_vwap_threshold": reentry_vwap_threshold if reentry_vwap_threshold > 0 else None,
            "long_momentum_v9_close_minus_reentry_vwap_threshold": reentry_close_minus_vwap_threshold,
            "long_momentum_v9_reentry_price_reclaim": reentry_price_reclaim,
            "long_momentum_v9_reentry_vwap_buffer_ok": reentry_price_reclaim,
            "long_momentum_v9_reentry_last_bar_not_red": reentry_last_bar_not_red,
            "long_momentum_v9_reentry_last_tema_open_ok": reentry_last_tema_open_ok,
            "long_momentum_v9_reentry_bvd_ok": reentry_bvd_ok,
            "long_momentum_v9_reentry_bvd_score": reentry_bvd_score,
            "long_momentum_v9_reentry_body_break_ok": reentry_body_break_ok,
            "long_momentum_v9_reentry_body_break_threshold": reentry_body_break_threshold,
            "long_momentum_v9_double_bvd_exit_red_ok": double_bvd_exit_red_ok,
            "long_momentum_v9_double_bvd_exit_open": double_bvd_exit_open,
            "long_momentum_v9_immediate_entry_open": immediate_entry_open,
            "long_momentum_v9_reentry_open": reentry_open,
            "long_momentum_v9_entry_priority": 2 if immediate_entry_open else 1 if reentry_open else 0,
            "long_momentum_v9_entry_open": immediate_entry_open or reentry_open,
            "entry_trigger": "IMMEDIATE_ENTRY" if immediate_entry_open else "WATCHLIST_REENTRY" if reentry_open else "",
            "long_momentum_v9_reject_reason": self._v9_reject_reason(
                price_eligible=price_eligible,
                watch_active=watch is not None,
                watch_entry_ready=watch_entry_ready,
                entry_time_ok=entry_time_ok,
                return_ok=return_ok,
                watchlist_add_transactions_ok=watchlist_add_transactions_ok,
                immediate_transactions_vs_prior_3_ok=immediate_transactions_vs_prior_3_ok,
                reentry_price_reclaim=reentry_price_reclaim,
                reentry_last_bar_not_red=reentry_last_bar_not_red,
                reentry_last_tema_open_ok=reentry_last_tema_open_ok,
                reentry_bvd_ok=reentry_bvd_ok,
                reentry_body_break_ok=reentry_body_break_ok,
                no_symbol_position=no_symbol_position,
            ),
        }

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
            if str(meta.get("entry_type") or "") == "WATCHLIST_REENTRY":
                self._trail_reentry_stop(position, bar, meta)
            double_bvd_score = self._float(bar.get("last_double_timeframe_bearish_volume_divergence_score"))
            double_bvd_red_ok = self._float(bar.get("last_close")) < self._float(bar.get("last_open"))
            if double_bvd_score > self.config.double_bvd_exit_score and double_bvd_red_ok:
                limit_price = self._liquid_limit_price("SELL", bar)
                self._trace_exit(context.timestamp, symbol, "DOUBLE_BVD", position, bar, meta)
                requests.append(
                    OrderRequest(
                        symbol=symbol,
                        side="SELL",
                        quantity=position.quantity,
                        order_type="LIMIT",
                        reason="DOUBLE_BVD",
                        limit_price=limit_price,
                        allow_same_bar_fill=True,
                        tag=self._exit_tag("DOUBLE_BVD", position, bar, meta) + f"|limit={limit_price:.4f}|2xBVD={double_bvd_score:.2f}|lastRed={double_bvd_red_ok}",
                    )
                )
                continue
            pocket_requests = self._pocket_requests(context.timestamp, symbol, position, bar, meta, portfolio)
            if pocket_requests:
                requests.extend(pocket_requests)
                continue
            if self._tema_closed(bar):
                limit_price = self._liquid_limit_price("SELL", bar)
                self._trace_exit(context.timestamp, symbol, "TEMA_CLOSE", position, bar, meta)
                requests.append(
                    OrderRequest(
                        symbol=symbol,
                        side="SELL",
                        quantity=position.quantity,
                        order_type="LIMIT",
                        reason="TEMA_CLOSE",
                        limit_price=limit_price,
                        allow_same_bar_fill=True,
                        tag=self._exit_tag("TEMA_CLOSE", position, bar, meta) + f"|limit={limit_price:.4f}" + self._tema_exit_tag(bar),
                    )
                )
                continue
            requests.append(
                OrderRequest(
                    symbol=symbol,
                    side="SELL",
                    quantity=position.quantity,
                    order_type="STOP",
                    reason="VWAP_TRAIL_STOP" if str(meta.get("entry_type") or "") == "WATCHLIST_REENTRY" else "INITIAL_STOP",
                    stop_price=position.stop_price,
                    tag=self._exit_tag("VWAP_TRAIL_STOP" if str(meta.get("entry_type") or "") == "WATCHLIST_REENTRY" else "INITIAL_STOP", position, bar, meta),
                    allow_same_bar_fill=True,
                    expire_on_bar_close=True,
                )
            )
        return requests

    def _submit_entry_group(
        self,
        *,
        candidates: list[dict],
        context: BarContext,
        available_cash: float,
        entry_type: str,
    ) -> list[OrderRequest]:
        if not candidates or available_cash <= 0:
            return []
        cash_slice = available_cash / len(candidates)
        requests: list[OrderRequest] = []
        for candidate in candidates:
            request = self._entry_request_for_type(candidate, context, cash_slice, entry_type)
            if request is not None:
                requests.append(request)
        return requests

    def _entry_request_for_type(
        self,
        candidate: dict,
        context: BarContext,
        available_cash: float,
        entry_type: str,
    ) -> OrderRequest | None:
        symbol = str(candidate["ticker"])
        signal_open = self._float(candidate.get("current_open"))
        entry_price = self._liquid_limit_price("BUY", candidate)
        stop_price = self._entry_stop_for_type(candidate, entry_price, entry_type)
        risk_per_share = entry_price - stop_price
        if entry_price <= 0 or stop_price <= 0 or risk_per_share <= 0:
            self._reject(context.timestamp, symbol, "invalid_entry_risk", candidate)
            return None
        max_risk_cash = available_cash * max(0.0, self.config.max_risk_fraction_of_cash)
        risk_quantity = int(max_risk_cash / risk_per_share) if risk_per_share > 0 else 0
        cash_quantity = self._cash_quantity(entry_price, available_cash)
        quantity = self._capped_entry_quantity(min(risk_quantity, cash_quantity))
        if quantity <= 0:
            self._reject(context.timestamp, symbol, "cash", candidate)
            return None
        rank = int(candidate.get("entry_rank") or candidate.get("rank") or 0)
        score = self._float(candidate.get("long_momentum_v9_last_5m_return"))
        self._set_entry_metadata(symbol, candidate, rank=rank, score=score, stop_price=stop_price)
        self.entry_order_metadata[symbol]["entry_type"] = entry_type
        self.position_meta[symbol] = {
            "initial_stop": stop_price,
            "initial_r": risk_per_share,
            "entry_score": score,
            "entry_type": entry_type,
            "peak_completed_close_profit_per_share": 0.0,
        }
        watch = self.momentum_watchlist.get(symbol)
        if watch is not None:
            watch.entry_submitted = True
            watch.last_entry_type = entry_type
            watch.last_state = "entry_submitted"
        self._trace_entry(context.timestamp, candidate, quantity, entry_price, stop_price)
        return OrderRequest(
            symbol=symbol,
            side="BUY",
            quantity=quantity,
            order_type="LIMIT",
            reason=f"LONG_MOMENTUM_V9_{entry_type}",
            limit_price=entry_price,
            allow_same_bar_fill=True,
            protective_stop_price=stop_price,
            tag=(
                f"ENTRY|rule=LONG_MOMENTUM_V9|trigger={entry_type}|rank={rank}|qty={quantity}"
                f"|signal_open={signal_open:.4f}|limit={entry_price:.4f}|entry={entry_price:.4f}|stop={stop_price:.4f}|risk={risk_per_share:.4f}"
                f"|last_5m_return={self._float(candidate.get('long_momentum_v9_last_5m_return')):.4f}"
                f"|transactions={self._float(candidate.get('last_transactions')):.0f}"
            ),
        )

    def _entry_stop_for_type(self, candidate: dict, entry_price: float, entry_type: str) -> float:
        if entry_type == "WATCHLIST_REENTRY":
            vwap = self._float(candidate.get("last_vwap"))
            stop = self._vwap_offset_stop(vwap)
            return stop if 0 < stop < entry_price else 0.0
        stop = self._float(candidate.get("last_open"))
        return stop if 0 < stop < entry_price else 0.0

    def _capped_entry_quantity(self, quantity: int) -> int:
        max_quantity = int(max(0, self.config.max_entry_order_quantity))
        if max_quantity <= 0:
            return int(quantity)
        return min(int(quantity), max_quantity)

    def _vwap_offset_stop(self, vwap: float) -> float:
        if vwap <= 0:
            return 0.0
        offset_fraction = max(0.0, self.config.vwap_stop_offset_pct) / 100.0
        return vwap * (1.0 - offset_fraction)

    def _pocket_requests(
        self,
        timestamp: datetime,
        symbol: str,
        position,
        bar: dict,
        meta: dict,
        portfolio: Portfolio,
    ) -> list[OrderRequest]:
        pocket_pct = max(0.0, self.config.pocket_profit_pct)
        if pocket_pct <= 0:
            return []
        sell_limit = self._liquid_limit_price("SELL", bar)
        buy_limit = self._liquid_limit_price("BUY", bar)
        trigger_price = position.entry_price * (1.0 + pocket_pct)
        if sell_limit <= 0 or buy_limit <= 0 or sell_limit < trigger_price:
            return []
        quantity = int(position.quantity)
        if quantity <= 0:
            return []
        watch = self.momentum_watchlist.get(symbol)
        if watch is not None:
            watch.last_entry_type = "POCKET_PROFIT"
            watch.last_state = "pocket_exit_submitted"

        sell_tag = (
            self._exit_tag("POCKET_PROFIT", position, bar, meta)
            + f"|limit={sell_limit:.4f}|pocketPct={pocket_pct:.4f}|trigger={trigger_price:.4f}"
        )
        self._trace_exit(timestamp, symbol, "POCKET_PROFIT", position, bar, meta)
        requests = [
            OrderRequest(
                symbol=symbol,
                side="SELL",
                quantity=quantity,
                order_type="LIMIT",
                reason="POCKET_PROFIT",
                limit_price=sell_limit,
                allow_same_bar_fill=True,
                tag=sell_tag,
            ),
        ]
        if not self.config.pocket_immediate_reentry_enabled:
            return requests

        reentry_quantity = min(
            quantity,
            self._cash_quantity(
                buy_limit,
                max(0.0, portfolio.cash + (sell_limit * quantity) - self.config.cash_buffer_dollars),
            ),
        )
        reentry_quantity = self._capped_entry_quantity(reentry_quantity)
        if reentry_quantity <= 0:
            return requests

        signal_open = self._bar_open(bar)
        stop_price = self._pocket_reentry_stop(position, bar, signal_open)
        if stop_price <= 0 or stop_price >= buy_limit:
            return requests

        risk_per_share = buy_limit - stop_price
        self.position_meta[symbol] = {
            "initial_stop": stop_price,
            "initial_r": risk_per_share,
            "entry_score": self._float(meta.get("entry_score")),
            "entry_type": "POCKET_REENTRY",
            "pocketed_from_entry": position.entry_price,
            "pocket_trigger_price": trigger_price,
        }
        self.entry_order_metadata[symbol] = {
            **self.entry_order_metadata.get(symbol, {}),
            "setup_rank": position.setup_rank,
            "live_rank": position.live_rank,
            "setup_score": position.setup_score,
            "live_score": position.live_score,
            "stop_price": stop_price,
            "entry_type": "POCKET_REENTRY",
        }
        if watch is not None:
            watch.entry_submitted = True
            watch.last_entry_type = "POCKET_REENTRY"
            watch.last_state = "pocket_reentry_submitted"
        buy_tag = (
            f"ENTRY|rule=LONG_MOMENTUM_V9|trigger=POCKET_REENTRY|rank={position.live_rank}"
            f"|qty={reentry_quantity}|signal_open={signal_open:.4f}|limit={buy_limit:.4f}"
            f"|entry={buy_limit:.4f}|stop={stop_price:.4f}|risk={risk_per_share:.4f}"
            f"|pocket_exit_limit={sell_limit:.4f}|pocket_from_entry={position.entry_price:.4f}"
            f"|pocketReentryStopPct={self.config.pocket_reentry_stop_loss_pct:.4f}"
            f"|pocketReentryStopBase={signal_open:.4f}"
        )
        requests.append(
            OrderRequest(
                symbol=symbol,
                side="BUY",
                quantity=reentry_quantity,
                order_type="LIMIT",
                reason="LONG_MOMENTUM_V9_POCKET_REENTRY",
                limit_price=buy_limit,
                allow_same_bar_fill=True,
                protective_stop_price=stop_price,
                tag=buy_tag,
            )
        )
        return requests

    def _pocket_reentry_stop(self, position, bar: dict, signal_open: float) -> float:
        stop_loss_fraction = max(0.0, self.config.pocket_reentry_stop_loss_pct) / 100.0
        if signal_open <= 0 or stop_loss_fraction <= 0:
            return 0.0
        return signal_open * (1.0 - stop_loss_fraction)

    def _exit_tag(self, reason: str, position, bar: dict | None, meta: dict) -> str:
        current_open = self._bar_open(bar or {})
        last_close = self._float((bar or {}).get("last_close"))
        stop = self._float(meta.get("initial_stop")) or position.stop_price
        return (
            f"EXIT|reason={reason}|price={current_open:.4f}|entry={position.entry_price:.4f}"
            f"|qty={position.quantity}|stop={stop:.4f}|R={self._float(meta.get('initial_r')):.4f}"
            f"|entryType={str(meta.get('entry_type') or '')}"
            f"|currentOpen={current_open:.4f}|lastClose={last_close:.4f}"
        )

    def _tema_closed(self, bar: dict) -> bool:
        tema9 = self._float(bar.get("current_open_tema9"))
        tema20 = self._float(bar.get("current_open_tema20"))
        if tema9 <= 0 or tema20 <= 0:
            return False
        return tema20 >= self._tema_exit_threshold(tema9)

    def _tema_exit_threshold(self, tema9: float) -> float:
        return tema9 * (1.0 + self.config.tema9_exit_buffer_pct)

    def _tema_exit_tag(self, bar: dict) -> str:
        tema9 = self._float(bar.get("current_open_tema9"))
        tema20 = self._float(bar.get("current_open_tema20"))
        threshold = self._tema_exit_threshold(tema9) if tema9 > 0 else 0.0
        return (
            f"|currentOpenTema9={tema9:.4f}|currentOpenTema20={tema20:.4f}"
            f"|temaThreshold={threshold:.4f}|tema9BufferPct={self.config.tema9_exit_buffer_pct:.4f}"
        )

    def _available_cash_after_submitted_requests(
        self,
        *,
        portfolio: Portfolio,
        requests: list[OrderRequest],
        context: BarContext,
    ) -> float:
        cash = max(0.0, portfolio.cash - self.config.cash_buffer_dollars)
        for request in requests:
            price = self._float(request.limit_price or request.stop_price)
            if request.side == "BUY":
                cash -= self._estimated_buy_cost(request.quantity, price)
            elif request.side == "SELL" and request.order_type != "STOP":
                bar = context.updates_by_symbol.get(request.symbol) or context.latest_by_symbol.get(request.symbol)
                open_price = self._bar_open(bar) if bar else 0.0
                cash += max(0.0, open_price * request.quantity)
        return max(0.0, cash)

    def _trail_reentry_stop(self, position, bar: dict, meta: dict) -> None:
        vwap = self._float(bar.get("last_vwap"))
        if vwap <= 0:
            return
        next_stop = self._vwap_offset_stop(vwap)
        if next_stop <= 0 or next_stop >= self._bar_open(bar):
            return
        position.stop_price = max(position.stop_price, next_stop)
        meta["initial_stop"] = position.stop_price

    def _record_watchlist_snapshot(self, context: BarContext, portfolio: Portfolio) -> None:
        if not self.momentum_watchlist:
            return
        rows = []
        for watch in self.momentum_watchlist.values():
            row = self.states.get(watch.ticker).row if watch.ticker in self.states else {}
            last_close = self._float(row.get("last_close"))
            last_vwap = self._float(row.get("last_vwap"))
            rows.append(
                {
                    "timestamp": context.timestamp,
                    "session_date": self.session_date.isoformat() if self.session_date else "",
                    "ticker": watch.ticker,
                    "watchlist_added_timestamp": watch.added_timestamp,
                    "watchlist_state": "held" if watch.ticker in portfolio.positions else watch.last_state,
                    "watchlist_entry_submitted": watch.entry_submitted,
                    "watchlist_last_entry_type": watch.last_entry_type,
                    "watchlist_added_last_close": watch.added_last_close,
                    "watchlist_added_last_5m_return": watch.added_last_5m_return,
                    "watchlist_max_vwap": watch.max_vwap,
                    "watchlist_avg_transactions": watch.avg_transactions_since_watchlist,
                    "last_close": row.get("last_close"),
                    "last_5m_return": row.get("last_5m_return"),
                    "last_volume": row.get("last_volume"),
                    "last_transactions": row.get("last_transactions"),
                    "last_transactions_vs_prior_3": row.get("last_transactions_vs_prior_3"),
                    "last_vwap": row.get("last_vwap"),
                    "last_close_minus_vwap": last_close - last_vwap if last_vwap > 0 else None,
                    "last_tema_open": row.get("last_tema_open"),
                    "last_double_timeframe_bearish_volume_divergence_score": row.get("last_double_timeframe_bearish_volume_divergence_score"),
                }
            )
        rows.sort(key=lambda item: (str(item["watchlist_state"]) == "held", self._float(item.get("last_5m_return"))), reverse=True)
        limit = max(1, int(self.config.watchlist_snapshot_limit))
        self.watchlist_snapshots.extend(rows[:limit])
        if self.observability:
            self.observability.state(
                timestamp=context.timestamp,
                scope="long_momentum_v9_watchlist",
                state={
                    "watchlist_count": len(self.momentum_watchlist),
                    "held_watchlist_symbols": [symbol for symbol in self.momentum_watchlist if symbol in portfolio.positions],
                    "entry_submitted_count": len([watch for watch in self.momentum_watchlist.values() if watch.entry_submitted]),
                },
            )

    def _record_scanner(
        self,
        context: BarContext,
        rows: list[dict],
        candidates: list[dict],
        portfolio: Portfolio,
        pending_orders: list[Order],
    ) -> None:
        captured = rows[: max(25, len(candidates))]
        self.last_scanner_rows = [dict(row) for row in rows]
        self.live_rankings.extend(captured)
        self.scanner_snapshots.append(
            {
                "timestamp": context.timestamp,
                "session_date": self.session_date.isoformat() if self.session_date else "",
                "candidate_count": len(candidates),
                "immediate_entry_count": len([row for row in candidates if row.get("long_momentum_v9_immediate_entry_open")]),
                "watchlist_entry_count": len([row for row in candidates if row.get("long_momentum_v9_reentry_open")]),
                "scanned_count": len(rows),
                "watchlist_count": len(self.momentum_watchlist),
            }
        )
        if not self.observability or not rows:
            return
        self.observability.scanner(timestamp=context.timestamp, rows=rows, score_key="scanner_score", stage="long_momentum_v9_scanner")
        self.observability.state(
            timestamp=context.timestamp,
            scope="strategy",
            state={
                "scanned_count": len(rows),
                "entry_open_count": len(candidates),
                "immediate_entry_count": len([row for row in candidates if row.get("long_momentum_v9_immediate_entry_open")]),
                "watchlist_entry_count": len([row for row in candidates if row.get("long_momentum_v9_reentry_open")]),
                "watchlist_count": len(self.momentum_watchlist),
                "open_positions": len(portfolio.positions),
                "pending_orders": len([order for order in pending_orders if order.status == "OPEN"]),
            },
        )

    def _trace_watchlist_add(self, timestamp: datetime, ticker: str, row: dict[str, Any], watch: MomentumWatch) -> None:
        if not self.observability:
            return
        self.observability.trace(
            timestamp=timestamp,
            ticker=ticker,
            stage="watchlist",
            event_type="watchlist_add",
            decision="add_to_day_momentum_watchlist",
            reason_code="PRICE_RETURN_VOLUME_TRANSACTIONS",
            reason="Ticker entered the day momentum watchlist from price, completed-bar 5m return, volume, and transactions.",
            values={
                "last_close": row.get("last_close"),
                "last_5m_return": watch.added_last_5m_return,
                "last_volume": row.get("last_volume"),
                "last_transactions": row.get("last_transactions"),
                "min_last_5m_return": self.config.min_last_5m_return,
                "min_watchlist_add_volume": self.config.min_watchlist_add_volume,
                "min_first_entry_transactions": self.config.min_first_entry_transactions,
                "min_price": self.config.min_price,
                "max_price": self.config.max_price,
            },
        )

    def _trace_entry(self, timestamp: datetime, candidate: dict, quantity: int, entry_price: float, stop_price: float) -> None:
        entry_type = str(candidate.get("entry_trigger") or "")
        self.signal_events.append(
            {
                "timestamp": timestamp,
                "ticker": candidate["ticker"],
                "event": "ENTRY_INTENT",
                "strategy_version": "v9",
                "entry_trigger": entry_type,
                "rank": candidate.get("entry_rank") or candidate.get("rank"),
                "quantity": quantity,
                "entry": entry_price,
                "stop": stop_price,
                "last_5m_return": candidate.get("long_momentum_v9_last_5m_return"),
                "transactions": candidate.get("last_transactions"),
                "transactions_vs_prior_3": candidate.get("last_transactions_vs_prior_3"),
                "watchlist_max_vwap": candidate.get("long_momentum_v9_watchlist_max_vwap"),
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
            reason_code=f"LONG_MOMENTUM_V9_{entry_type}",
            reason="Ticker passed the immediate transaction impulse or the watchlist VWAP entry; eligible candidates share available cash.",
            values={
                "entry_type": entry_type,
                "quantity": quantity,
                "current_open": entry_price,
                "stop": stop_price,
                "risk_per_share": entry_price - stop_price,
                "last_5m_return": candidate.get("long_momentum_v9_last_5m_return"),
                "last_transactions": candidate.get("last_transactions"),
                "last_transactions_vs_prior_3": candidate.get("last_transactions_vs_prior_3"),
                "last_vwap": candidate.get("last_vwap"),
                "close_minus_vwap": candidate.get("long_momentum_v9_close_minus_vwap"),
                "watchlist_max_vwap": candidate.get("long_momentum_v9_watchlist_max_vwap"),
            },
            state={
                "watchlist_count": len(self.momentum_watchlist),
                "entry_submitted": candidate.get("long_momentum_v9_watchlist_entry_submitted"),
            },
            force=self._force_trade_trace(),
        )

    def _v9_entry_state(self, row: dict[str, Any]) -> str:
        if row.get("long_momentum_v9_immediate_entry_open"):
            return "IMMEDIATE_ENTRY"
        if row.get("long_momentum_v9_reentry_open"):
            return "WATCHLIST_VWAP_ENTRY"
        return str(row.get("long_momentum_v9_reject_reason") or "filtered")

    def _v9_reject_reason(
        self,
        *,
        price_eligible: bool,
        watch_active: bool,
        watch_entry_ready: bool,
        entry_time_ok: bool,
        return_ok: bool,
        watchlist_add_transactions_ok: bool,
        immediate_transactions_vs_prior_3_ok: bool,
        reentry_price_reclaim: bool,
        reentry_last_bar_not_red: bool,
        reentry_last_tema_open_ok: bool,
        reentry_bvd_ok: bool,
        reentry_body_break_ok: bool,
        no_symbol_position: bool,
    ) -> str:
        if not price_eligible:
            return "price_eligibility"
        if not watch_active:
            return "not_in_day_momentum_watchlist"
        if not entry_time_ok:
            return "entry_time"
        if not no_symbol_position:
            return "already_held_or_pending"
        if return_ok and watchlist_add_transactions_ok and not immediate_transactions_vs_prior_3_ok:
            return "immediate_entry_transactions_vs_prior_3"
        if not watch_entry_ready:
            return "watchlist_entry_wait_next_bar"
        if not reentry_price_reclaim:
            return "watchlist_entry_below_vwap_buffer"
        if not reentry_last_bar_not_red:
            return "watchlist_entry_red_vwap_reclaim_bar"
        if not reentry_last_tema_open_ok:
            return "watchlist_entry_last_tema_not_open"
        if not reentry_bvd_ok:
            return "watchlist_entry_bearish_volume_divergence"
        if not reentry_body_break_ok:
            return "watchlist_entry_two_bar_body_break"
        return "filtered"

__all__ = ["LongMomentumV9Strategy"]
