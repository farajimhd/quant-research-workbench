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
    "last_day_high_so_far",
    "last_2_body_high",
    "current_open_above_last_2_body_high",
    "last_bearish_volume_divergence_score",
    "last_double_timeframe_bearish_volume_divergence_score",
)

ADAPTIVE_POCKET_V9_COLUMNS = (
    "last_true_range_ema5",
    "last_true_range_ema5_pct",
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
    first_entry_submitted: bool = False
    first_entry_filled: bool = False
    last_exit_timestamp: datetime | None = None
    last_entry_type: str = ""
    last_state: str = "watching"

    @property
    def avg_transactions_since_watchlist(self) -> float:
        if self.transaction_count <= 0:
            return 0.0
        return self.transaction_sum / self.transaction_count


@dataclass(slots=True)
class HighBreakHoldWatch:
    ticker: str
    detected_timestamp: datetime
    breakout_level: float
    detected_current_open: float
    hold_count: int = 0
    entry_submitted: bool = False
    entry_filled: bool = False
    last_hold_ok: bool = False
    last_state: str = "waiting_hold"

    @property
    def ready(self) -> bool:
        return self.hold_count > 0


class LongMomentumV9Strategy(LongMomentumV3Strategy):
    name = "long_momentum"

    def __init__(self, config: LongMomentumV9Config | None = None):
        super().__init__(config or LongMomentumV9Config())
        self.config: LongMomentumV9Config
        self.momentum_watchlist: dict[str, MomentumWatch] = {}
        self.high_break_hold_watchlist: dict[str, HighBreakHoldWatch] = {}
        self.watchlist_snapshots: list[dict] = []
        self.high_break_hold_snapshots: list[dict] = []
        self.last_scanner_rows: list[dict] = []

    def data_requirements(self) -> DataRequirements:
        feature_groups = ["core", "momentum", "session", "volume_liquidity"]
        if self.config.adaptive_pocket_enabled:
            feature_groups.append("volatility")
        return DataRequirements(
            event_timeframe="1m",
            feature_groups=tuple(feature_groups),
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
        self.high_break_hold_watchlist = {}
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
        artifacts["high_break_hold_snapshots"] = self.high_break_hold_snapshots
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
        high_break_candidates = [row for row in rows if row.get("long_momentum_v9_high_break_hold_entry_open")]
        vwap_reclaim_candidates = [row for row in rows if row.get("long_momentum_v9_vwap_reclaim_entry_open")]
        self._record_scanner(context, rows, high_break_candidates + vwap_reclaim_candidates, portfolio, active_pending_orders)

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

        high_break_candidates = [
            row for row in high_break_candidates
            if str(row.get("ticker") or "") not in blocked_symbols
        ][: max(0, int(self.config.max_high_break_hold_candidates_per_bar))]
        if high_break_candidates:
            rotation_requests = self._rotation_sell_requests_for_first_entries(
                candidates=high_break_candidates,
                context=context,
                portfolio=portfolio,
                existing_requests=requests,
                available_cash=available_cash,
            )
            if rotation_requests:
                requests.extend(rotation_requests)
                available_cash = self._available_cash_after_submitted_requests(
                    portfolio=portfolio,
                    requests=requests,
                    context=context,
                )
        if high_break_candidates and available_cash > 0:
            submitted = self._submit_entry_group(
                candidates=high_break_candidates,
                context=context,
                available_cash=available_cash,
                entry_type="HIGH_BREAK_HOLD",
            )
            requests.extend(submitted)
            blocked_symbols |= {request.symbol for request in submitted if request.side == "BUY"}
            available_cash = self._available_cash_after_submitted_requests(
                portfolio=portfolio,
                requests=requests,
                context=context,
            )

        vwap_reclaim_candidates = [
            row for row in vwap_reclaim_candidates
            if str(row.get("ticker") or "") not in blocked_symbols
        ][: max(0, int(self.config.max_reentry_candidates_per_bar))]
        if vwap_reclaim_candidates and available_cash > 0:
            requests.extend(
                self._submit_entry_group(
                    candidates=vwap_reclaim_candidates,
                    context=context,
                    available_cash=available_cash,
                    entry_type="VWAP_RECLAIM",
                )
            )
        self._record_watchlist_snapshot(context, portfolio)
        self._record_high_break_hold_snapshot(context, portfolio)
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
            return open_price
        return max(0.01, open_price - offset)

    def _validate_provider_columns(self, frame: pl.DataFrame) -> None:
        required_columns = list(REQUIRED_V9_COLUMNS)
        if self.config.adaptive_pocket_enabled:
            required_columns.extend(ADAPTIVE_POCKET_V9_COLUMNS)
        missing = [column for column in required_columns if column not in frame.columns]
        if missing:
            date_text = self.session_date.isoformat() if self.session_date else "unknown session"
            feature_text = "core, momentum, volatility, and volume_liquidity" if self.config.adaptive_pocket_enabled else "core, momentum, and volume_liquidity"
            raise ValueError(
                f"Long Momentum v9 requires provider-built strategy-time {feature_text} features for {date_text}; "
                f"missing columns: {', '.join(missing)}. Rebuild market data with current core, momentum, volatility, and volume_liquidity features."
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
                meta = self.position_meta.get(symbol)
                existing_entry_type = str((meta or {}).get("entry_type") or "")
                if "POCKET_REENTRY" in tag:
                    watch.last_entry_type = "POCKET_REENTRY"
                elif "HIGH_BREAK_HOLD" in tag or "FIRST_ENTRY" in tag or existing_entry_type in {"HIGH_BREAK_HOLD", "FIRST_ENTRY"}:
                    watch.first_entry_submitted = True
                    watch.first_entry_filled = True
                    watch.last_entry_type = "HIGH_BREAK_HOLD"
                    high_watch = self.high_break_hold_watchlist.get(symbol)
                    if high_watch is not None:
                        high_watch.entry_submitted = True
                        high_watch.entry_filled = True
                        high_watch.last_state = "filled"
                else:
                    watch.last_entry_type = "VWAP_RECLAIM"
                watch.last_state = "in_position"
                if meta is not None:
                    if "HIGH_BREAK_HOLD" in tag or "FIRST_ENTRY" in tag or existing_entry_type in {"HIGH_BREAK_HOLD", "FIRST_ENTRY"}:
                        meta["entry_type"] = "HIGH_BREAK_HOLD"
                        if "soft_exit_wait_bars_remaining" not in meta:
                            meta["soft_exit_wait_bars_remaining"] = max(0, int(self.config.first_entry_soft_exit_wait_bars))
                        meta.setdefault("first_entry_body_bars_observed", 0)
                        meta.setdefault("first_entry_high_bars_observed", 0)
                        if self._float(meta.get("first_entry_highest_high")) <= 0:
                            meta["first_entry_highest_high"] = self._float(fill.get("price"))
                        meta["entry_fill_timestamp"] = context.timestamp
                    elif "VWAP_RECLAIM" in tag or "WATCHLIST_REENTRY" in tag:
                        meta["entry_type"] = "VWAP_RECLAIM"
            elif side == "SELL":
                tag = str(fill.get("tag") or "")
                watch.last_exit_timestamp = context.timestamp
                watch.entry_submitted = False
                if "POCKET_PROFIT" in tag:
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
        self._update_high_break_hold_watch(timestamp, row, watch)

    def _update_high_break_hold_watch(self, timestamp: datetime, row: dict[str, Any], watch: MomentumWatch) -> None:
        ticker = watch.ticker
        high_watch = self.high_break_hold_watchlist.get(ticker)
        current_open = self._float(row.get("current_open"))
        last_day_high_so_far = self._float(row.get("last_day_high_so_far"))
        minute_of_day = int(self._float(row.get("minute_of_day")))
        entry_time_ok = self.config.trading_start_minute <= minute_of_day < self.config.trading_end_minute
        day_high_break_ok = current_open > 0 and last_day_high_so_far > 0 and current_open >= last_day_high_so_far
        if high_watch is None:
            if watch.first_entry_filled or watch.first_entry_submitted or not entry_time_ok or not day_high_break_ok:
                return
            high_watch = HighBreakHoldWatch(
                ticker=ticker,
                detected_timestamp=timestamp,
                breakout_level=last_day_high_so_far,
                detected_current_open=current_open,
                last_state="waiting_next_bar",
            )
            self.high_break_hold_watchlist[ticker] = high_watch
            self._trace_high_break_hold_add(timestamp, ticker, row, high_watch)
            return
        if high_watch.entry_submitted or high_watch.entry_filled or watch.first_entry_filled:
            return
        if timestamp <= high_watch.detected_timestamp:
            return
        threshold = high_watch.breakout_level * (1.0 - max(0.0, self.config.high_break_hold_tolerance_ratio))
        last_close = self._float(row.get("last_close"))
        last_open = self._float(row.get("last_open"))
        hold_ok = threshold > 0 and last_close >= threshold and (last_close >= last_open or last_close >= high_watch.breakout_level)
        high_watch.last_hold_ok = hold_ok
        high_watch.hold_count = high_watch.hold_count + 1 if hold_ok else 0
        required = max(1, int(self.config.high_break_hold_confirmation_bars))
        high_watch.last_state = "ready" if high_watch.hold_count >= required else "waiting_hold"

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
        reentry_tema_open_threshold = self._tema_open_threshold(last_tema20) if last_tema20 > 0 else 0.0
        reentry_last_tema_open_ok = last_tema9 > 0 and reentry_tema_open_threshold > 0 and last_tema9 >= reentry_tema_open_threshold
        reentry_bvd_score = self._float(row.get("last_bearish_volume_divergence_score"))
        reentry_bvd_ok = reentry_bvd_score <= self.config.max_reentry_bvd_score
        current_open = self._float(row.get("current_open"))
        last_day_high_so_far = self._float(row.get("last_day_high_so_far"))
        high_break_day_high_break_ok = current_open > 0 and last_day_high_so_far > 0 and current_open >= last_day_high_so_far
        high_break_watch = self.high_break_hold_watchlist.get(ticker)
        high_break_available = bool(
            high_break_watch
            and not high_break_watch.entry_submitted
            and not high_break_watch.entry_filled
            and watch
            and not watch.first_entry_submitted
            and not watch.first_entry_filled
        )
        high_break_hold_threshold = (
            high_break_watch.breakout_level * (1.0 - max(0.0, self.config.high_break_hold_tolerance_ratio))
            if high_break_watch
            else 0.0
        )
        high_break_hold_ready = bool(
            high_break_watch
            and high_break_watch.hold_count >= max(1, int(self.config.high_break_hold_confirmation_bars))
            and isinstance(current_timestamp, datetime)
            and high_break_watch.detected_timestamp < current_timestamp
        )
        reentry_body_break_threshold = self._float(row.get("last_2_body_high"))
        reentry_body_break_ok = current_open > 0 and reentry_body_break_threshold > 0 and current_open > reentry_body_break_threshold
        reentry_close_minus_vwap = last_close - last_vwap if last_vwap > 0 else None
        reentry_close_minus_vwap_threshold = last_close - reentry_vwap_threshold if reentry_vwap_threshold > 0 else None
        double_bvd_exit_score = self._float(row.get("last_double_timeframe_bearish_volume_divergence_score"))
        double_bvd_exit_red_ok = last_close <= last_open
        double_bvd_exit_open = double_bvd_exit_score > self.config.double_bvd_exit_score and double_bvd_exit_red_ok
        pocket_state = self._pocket_state(row, portfolio.positions.get(ticker))
        first_entry_meta = self.position_meta.get(ticker, {})
        first_entry_body_state = self._first_entry_body_cycle_state(first_entry_meta)
        first_entry_high_state = self._first_entry_high_cycle_state(first_entry_meta)
        high_break_hold_entry_open = (
            price_eligible
            and bool(watch)
            and high_break_available
            and high_break_hold_ready
            and no_symbol_position
            and entry_time_ok
        )
        vwap_reclaim_entry_open = (
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
            and not high_break_hold_entry_open
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
            "long_momentum_v9_watchlist_first_entry_submitted": bool(watch and watch.first_entry_submitted),
            "long_momentum_v9_watchlist_first_entry_filled": bool(watch and watch.first_entry_filled),
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
            "long_momentum_v9_high_break_hold_watch_active": high_break_watch is not None,
            "long_momentum_v9_high_break_hold_detected_timestamp": high_break_watch.detected_timestamp.isoformat() if high_break_watch else "",
            "long_momentum_v9_high_break_hold_breakout_level": high_break_watch.breakout_level if high_break_watch else None,
            "long_momentum_v9_high_break_hold_detected_current_open": high_break_watch.detected_current_open if high_break_watch else None,
            "long_momentum_v9_high_break_hold_threshold": high_break_hold_threshold if high_break_hold_threshold > 0 else None,
            "long_momentum_v9_high_break_hold_count": high_break_watch.hold_count if high_break_watch else 0,
            "long_momentum_v9_high_break_hold_confirmation_bars": max(1, int(self.config.high_break_hold_confirmation_bars)),
            "long_momentum_v9_high_break_hold_ok": bool(high_break_watch and high_break_watch.last_hold_ok),
            "long_momentum_v9_high_break_hold_ready": high_break_hold_ready,
            "long_momentum_v9_high_break_hold_available": high_break_available,
            "long_momentum_v9_high_break_hold_entry_submitted": bool(high_break_watch and high_break_watch.entry_submitted),
            "long_momentum_v9_high_break_hold_entry_filled": bool(high_break_watch and high_break_watch.entry_filled),
            "long_momentum_v9_high_break_hold_state": high_break_watch.last_state if high_break_watch else "",
            "long_momentum_v9_high_break_day_high_break_ok": high_break_day_high_break_ok,
            "long_momentum_v9_high_break_day_high_break_threshold": last_day_high_so_far if last_day_high_so_far > 0 else None,
            "long_momentum_v9_high_break_close_minus_day_high": last_close - last_day_high_so_far if last_day_high_so_far > 0 else None,
            "long_momentum_v9_first_entry_available": high_break_available,
            "long_momentum_v9_first_entry_day_high_break_ok": high_break_day_high_break_ok,
            "long_momentum_v9_first_entry_day_high_break_threshold": last_day_high_so_far if last_day_high_so_far > 0 else None,
            "long_momentum_v9_first_entry_close_minus_day_high": last_close - last_day_high_so_far if last_day_high_so_far > 0 else None,
            "long_momentum_v9_close_minus_vwap": reentry_close_minus_vwap,
            "long_momentum_v9_vwap_reclaim_vwap_threshold": reentry_vwap_threshold if reentry_vwap_threshold > 0 else None,
            "long_momentum_v9_close_minus_vwap_reclaim_threshold": reentry_close_minus_vwap_threshold,
            "long_momentum_v9_vwap_reclaim_price_reclaim": reentry_price_reclaim,
            "long_momentum_v9_vwap_reclaim_vwap_buffer_ok": reentry_price_reclaim,
            "long_momentum_v9_vwap_reclaim_last_bar_not_red": reentry_last_bar_not_red,
            "long_momentum_v9_vwap_reclaim_last_tema_open_ok": reentry_last_tema_open_ok,
            "long_momentum_v9_vwap_reclaim_tema_open_threshold": reentry_tema_open_threshold if reentry_tema_open_threshold > 0 else None,
            "long_momentum_v9_vwap_reclaim_bvd_ok": reentry_bvd_ok,
            "long_momentum_v9_vwap_reclaim_bvd_score": reentry_bvd_score,
            "long_momentum_v9_vwap_reclaim_body_break_ok": reentry_body_break_ok,
            "long_momentum_v9_vwap_reclaim_body_break_threshold": reentry_body_break_threshold,
            "long_momentum_v9_reentry_vwap_threshold": reentry_vwap_threshold if reentry_vwap_threshold > 0 else None,
            "long_momentum_v9_close_minus_reentry_vwap_threshold": reentry_close_minus_vwap_threshold,
            "long_momentum_v9_reentry_price_reclaim": reentry_price_reclaim,
            "long_momentum_v9_reentry_vwap_buffer_ok": reentry_price_reclaim,
            "long_momentum_v9_reentry_last_bar_not_red": reentry_last_bar_not_red,
            "long_momentum_v9_reentry_last_tema_open_ok": reentry_last_tema_open_ok,
            "long_momentum_v9_reentry_tema_open_threshold": reentry_tema_open_threshold if reentry_tema_open_threshold > 0 else None,
            "long_momentum_v9_reentry_bvd_ok": reentry_bvd_ok,
            "long_momentum_v9_reentry_bvd_score": reentry_bvd_score,
            "long_momentum_v9_reentry_body_break_ok": reentry_body_break_ok,
            "long_momentum_v9_reentry_body_break_threshold": reentry_body_break_threshold,
            "long_momentum_v9_double_bvd_exit_red_ok": double_bvd_exit_red_ok,
            "long_momentum_v9_double_bvd_exit_open": double_bvd_exit_open,
            **pocket_state,
            **first_entry_high_state,
            **first_entry_body_state,
            "long_momentum_v9_immediate_entry_open": False,
            "long_momentum_v9_high_break_hold_entry_open": high_break_hold_entry_open,
            "long_momentum_v9_vwap_reclaim_entry_open": vwap_reclaim_entry_open,
            "long_momentum_v9_first_entry_open": high_break_hold_entry_open,
            "long_momentum_v9_reentry_open": vwap_reclaim_entry_open,
            "long_momentum_v9_entry_priority": 2 if high_break_hold_entry_open else 1 if vwap_reclaim_entry_open else 0,
            "long_momentum_v9_entry_open": high_break_hold_entry_open or vwap_reclaim_entry_open,
            "entry_trigger": "HIGH_BREAK_HOLD" if high_break_hold_entry_open else "VWAP_RECLAIM" if vwap_reclaim_entry_open else "",
            "long_momentum_v9_reject_reason": self._v9_reject_reason(
                price_eligible=price_eligible,
                watch_active=watch is not None,
                high_break_watch_active=high_break_watch is not None,
                high_break_available=high_break_available,
                high_break_hold_ready=high_break_hold_ready,
                high_break_day_high_break_ok=high_break_day_high_break_ok,
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
            if self._uses_vwap_trail(meta):
                self._trail_reentry_stop(position, bar, meta)
            pocket_state = self._pocket_state(bar, position)
            self._trace_pocket_evaluation(context.timestamp, symbol, position, bar, meta, pocket_state)
            body_state = self._update_first_entry_body_cycle(context.timestamp, symbol, bar, meta)
            high_state = self._update_first_entry_high_cycle(context.timestamp, symbol, position, bar, meta)
            soft_exits_blocked = self._first_entry_soft_exits_blocked(context.timestamp, symbol, meta)
            if soft_exits_blocked:
                self._trace_first_entry_soft_exit_wait(context.timestamp, symbol, position, bar, meta)
                stop_reason = "VWAP_TRAIL_STOP" if self._uses_vwap_trail(meta) else "INITIAL_STOP"
                requests.append(
                    OrderRequest(
                        symbol=symbol,
                        side="SELL",
                        quantity=position.quantity,
                        order_type="STOP",
                        reason=stop_reason,
                        stop_price=position.stop_price,
                        tag=self._exit_tag(stop_reason, position, bar, meta) + f"|softExitWaitBarsRemaining={int(meta.get('soft_exit_wait_bars_remaining') or 0)}",
                        allow_same_bar_fill=True,
                        expire_on_bar_close=True,
                    )
                )
                continue
            lifecycle_exits_blocked = self._first_entry_lifecycle_exits_blocked(meta)
            if lifecycle_exits_blocked:
                self._trace_first_entry_lifecycle_wait(context.timestamp, symbol, position, bar, meta, body_state, high_state)
                stop_reason = "VWAP_TRAIL_STOP" if self._uses_vwap_trail(meta) else "INITIAL_STOP"
                requests.append(
                    OrderRequest(
                        symbol=symbol,
                        side="SELL",
                        quantity=position.quantity,
                        order_type="STOP",
                        reason=stop_reason,
                        stop_price=position.stop_price,
                        tag=(
                            self._exit_tag(stop_reason, position, bar, meta)
                            + f"|lifecycleWait=True|highStallConfirmed={bool(high_state.get('long_momentum_v9_first_entry_high_stall_confirmed'))}"
                            + f"|noNewHighBars={int(self._float(high_state.get('long_momentum_v9_first_entry_no_new_high_count')))}"
                            + f"|bodyStrengthRatio={self._float(body_state.get('long_momentum_v9_first_entry_body_strength_ratio')):.4f}"
                            + f"|contractionBars={int(self._float(body_state.get('long_momentum_v9_first_entry_body_contraction_count')))}"
                        ),
                        allow_same_bar_fill=True,
                        expire_on_bar_close=True,
                    )
                )
                continue
            double_bvd_score = self._float(bar.get("last_double_timeframe_bearish_volume_divergence_score"))
            double_bvd_red_ok = self._float(bar.get("last_close")) <= self._float(bar.get("last_open"))
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
            pocket_requests = self._pocket_requests(context.timestamp, symbol, position, bar, meta, portfolio, pocket_state)
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
            stop_reason = "VWAP_TRAIL_STOP" if self._uses_vwap_trail(meta) else "INITIAL_STOP"
            requests.append(
                OrderRequest(
                    symbol=symbol,
                    side="SELL",
                    quantity=position.quantity,
                    order_type="STOP",
                    reason=stop_reason,
                    stop_price=position.stop_price,
                    tag=self._exit_tag(stop_reason, position, bar, meta),
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

    def _rotation_sell_requests_for_first_entries(
        self,
        *,
        candidates: list[dict],
        context: BarContext,
        portfolio: Portfolio,
        existing_requests: list[OrderRequest],
        available_cash: float,
    ) -> list[OrderRequest]:
        if not candidates:
            return []
        target_cash = 0.0
        for candidate in candidates:
            entry_price = self._liquid_limit_price("BUY", candidate)
            if entry_price > 0:
                target_cash += entry_price * max(0, int(self.config.max_entry_order_quantity))
        shortfall = max(0.0, target_cash - available_cash)
        if shortfall <= 0:
            return []

        high_break_symbols = {str(candidate.get("ticker") or "").upper() for candidate in candidates}
        existing_sell_symbols = {request.symbol for request in existing_requests if request.side == "SELL"}
        requests: list[OrderRequest] = []
        for symbol, position in list(portfolio.positions.items()):
            if shortfall <= 0:
                break
            if symbol in high_break_symbols or symbol in existing_sell_symbols:
                continue
            meta = self._position_meta(symbol, position)
            if self._is_high_break_entry_type(meta):
                continue
            row = context.updates_by_symbol.get(symbol) or context.latest_by_symbol.get(symbol)
            if row is None:
                continue
            limit_price = self._liquid_limit_price("SELL", row)
            if limit_price <= 0:
                continue
            quantity = min(int(position.quantity), int((shortfall / limit_price) + 0.999999))
            if quantity <= 0:
                continue
            requests.append(
                OrderRequest(
                    symbol=symbol,
                    side="SELL",
                    quantity=quantity,
                    order_type="LIMIT",
                    reason="HIGH_BREAK_HOLD_CASH_ROTATION",
                    limit_price=limit_price,
                    allow_same_bar_fill=True,
                    tag=(
                        self._exit_tag("HIGH_BREAK_HOLD_CASH_ROTATION", position, row, meta)
                        + f"|limit={limit_price:.4f}|qtyReleased={quantity}|cashShortfall={shortfall:.2f}"
                    ),
                )
            )
            existing_sell_symbols.add(symbol)
            shortfall = max(0.0, shortfall - (quantity * limit_price))
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
            "soft_exit_wait_bars_remaining": max(0, int(self.config.first_entry_soft_exit_wait_bars)) if entry_type == "HIGH_BREAK_HOLD" else 0,
            "first_entry_body_bars_observed": 0,
            "first_entry_high_bars_observed": 0,
            "first_entry_highest_high": entry_price if entry_type == "HIGH_BREAK_HOLD" else 0.0,
        }
        watch = self.momentum_watchlist.get(symbol)
        if watch is not None:
            watch.entry_submitted = True
            if entry_type == "HIGH_BREAK_HOLD":
                watch.first_entry_submitted = True
            watch.last_entry_type = entry_type
            watch.last_state = "entry_submitted"
        high_watch = self.high_break_hold_watchlist.get(symbol)
        if high_watch is not None and entry_type == "HIGH_BREAK_HOLD":
            high_watch.entry_submitted = True
            high_watch.last_state = "entry_submitted"
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
        if entry_type in {"HIGH_BREAK_HOLD", "FIRST_ENTRY"}:
            vwap = self._float(candidate.get("last_vwap"))
            vwap_stop = self._vwap_offset_stop(vwap)
            high_break_level = self._float(candidate.get("long_momentum_v9_high_break_hold_breakout_level"))
            high_break_stop = high_break_level * (1.0 - max(0.0, self.config.high_break_stop_offset_ratio)) if high_break_level > 0 else 0.0
            stop = max(vwap_stop, high_break_stop)
            return stop if 0 < stop < entry_price else 0.0
        if entry_type in {"VWAP_RECLAIM", "WATCHLIST_REENTRY"}:
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
        pocket_state: dict[str, Any],
    ) -> list[OrderRequest]:
        pocket_pct = self._float(pocket_state.get("long_momentum_v9_pocket_pct"))
        if pocket_pct <= 0:
            return []
        sell_limit = self._float(pocket_state.get("long_momentum_v9_pocket_estimated_bid"))
        trigger_price = self._float(pocket_state.get("long_momentum_v9_pocket_trigger_price"))
        if not pocket_state.get("long_momentum_v9_pocket_open"):
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
            + f"|limit={sell_limit:.4f}|pocketMode={pocket_state.get('long_momentum_v9_pocket_mode')}"
            + f"|pocketPct={pocket_pct:.4f}|trigger={trigger_price:.4f}"
            + f"|volPct={self._float(pocket_state.get('long_momentum_v9_pocket_vol_pct')):.4f}"
            + f"|fixedPct={self.config.pocket_profit_pct:.4f}"
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
        return requests

    def _pocket_state(self, row: dict, position) -> dict[str, Any]:
        fixed_pct = max(0.0, self.config.pocket_profit_pct)
        min_pct = max(0.0, self.config.adaptive_pocket_min_profit_pct)
        max_pct = max(min_pct, self.config.adaptive_pocket_max_profit_pct)
        vol_pct = self._float(row.get("last_true_range_ema5_pct"))
        if vol_pct <= 0:
            true_range_ema5 = self._float(row.get("last_true_range_ema5"))
            last_close = self._float(row.get("last_close"))
            vol_pct = true_range_ema5 / last_close if true_range_ema5 > 0 and last_close > 0 else 0.0

        mode = "fixed"
        unclamped_pct = fixed_pct
        pocket_pct = fixed_pct
        if self.config.adaptive_pocket_enabled:
            if vol_pct > 0:
                mode = "adaptive"
                unclamped_pct = vol_pct * max(0.0, self.config.adaptive_pocket_vol_multiplier)
                pocket_pct = min(max(unclamped_pct, min_pct), max_pct)
            else:
                mode = "adaptive_missing_vol_fixed"

        current_open = self._bar_open(row)
        estimated_bid = current_open
        entry_price = self._float(getattr(position, "entry_price", 0.0)) if position is not None else 0.0
        quantity = int(getattr(position, "quantity", 0) or 0) if position is not None else 0
        trigger_price = entry_price * (1.0 + pocket_pct) if entry_price > 0 and pocket_pct > 0 else 0.0
        remaining_to_trigger = trigger_price - estimated_bid if trigger_price > 0 and estimated_bid > 0 else None
        pocket_open = bool(quantity > 0 and estimated_bid > 0 and trigger_price > 0 and estimated_bid >= trigger_price)
        return {
            "long_momentum_v9_pocket_active": quantity > 0,
            "long_momentum_v9_pocket_open": pocket_open,
            "long_momentum_v9_pocket_mode": mode,
            "long_momentum_v9_pocket_pct": pocket_pct,
            "long_momentum_v9_pocket_fixed_pct": fixed_pct,
            "long_momentum_v9_pocket_unclamped_pct": unclamped_pct,
            "long_momentum_v9_pocket_vol_pct": vol_pct,
            "long_momentum_v9_pocket_vol_multiplier": self.config.adaptive_pocket_vol_multiplier,
            "long_momentum_v9_pocket_min_profit_pct": min_pct,
            "long_momentum_v9_pocket_max_profit_pct": max_pct,
            "long_momentum_v9_pocket_current_open": current_open,
            "long_momentum_v9_pocket_sell_offset": self.config.limit_order_offset_dollars,
            "long_momentum_v9_pocket_estimated_bid": estimated_bid,
            "long_momentum_v9_pocket_entry_price": entry_price,
            "long_momentum_v9_pocket_trigger_price": trigger_price,
            "long_momentum_v9_pocket_remaining_to_trigger": remaining_to_trigger,
            "long_momentum_v9_pocket_true_range_ema5": self._float(row.get("last_true_range_ema5")),
        }

    def _trace_pocket_evaluation(
        self,
        timestamp: datetime,
        symbol: str,
        position,
        bar: dict,
        meta: dict,
        pocket_state: dict[str, Any],
    ) -> None:
        if not self.observability:
            return
        self.observability.trace(
            timestamp=timestamp,
            ticker=symbol,
            stage="exit",
            event_type="pocket_evaluation",
            decision="submit_order" if pocket_state.get("long_momentum_v9_pocket_open") else "hold_position",
            reason_code="POCKET_PROFIT_READY" if pocket_state.get("long_momentum_v9_pocket_open") else "POCKET_WAIT",
            reason="Evaluated Long Momentum v9 pocket-profit threshold for the open position.",
            values={
                "entry_price": getattr(position, "entry_price", None),
                "quantity": getattr(position, "quantity", None),
                "current_open": self._bar_open(bar),
                "estimated_bid": pocket_state.get("long_momentum_v9_pocket_estimated_bid"),
                "sell_offset_not_applied_to_pocket_bid": pocket_state.get("long_momentum_v9_pocket_sell_offset"),
                "pocket_mode": pocket_state.get("long_momentum_v9_pocket_mode"),
                "pocket_pct": pocket_state.get("long_momentum_v9_pocket_pct"),
                "fixed_pct": pocket_state.get("long_momentum_v9_pocket_fixed_pct"),
                "vol_pct": pocket_state.get("long_momentum_v9_pocket_vol_pct"),
                "vol_multiplier": pocket_state.get("long_momentum_v9_pocket_vol_multiplier"),
                "min_profit_pct": pocket_state.get("long_momentum_v9_pocket_min_profit_pct"),
                "max_profit_pct": pocket_state.get("long_momentum_v9_pocket_max_profit_pct"),
                "unclamped_pct": pocket_state.get("long_momentum_v9_pocket_unclamped_pct"),
                "trigger_price": pocket_state.get("long_momentum_v9_pocket_trigger_price"),
                "remaining_to_trigger": pocket_state.get("long_momentum_v9_pocket_remaining_to_trigger"),
                "last_true_range_ema5": pocket_state.get("long_momentum_v9_pocket_true_range_ema5"),
                "last_true_range_ema5_pct": bar.get("last_true_range_ema5_pct"),
                "entry_type": meta.get("entry_type"),
            },
            force=True,
        )

    def _exit_tag(self, reason: str, position, bar: dict | None, meta: dict) -> str:
        current_open = self._bar_open(bar or {})
        last_close = self._float((bar or {}).get("last_close"))
        stop = self._float(meta.get("initial_stop")) or position.stop_price
        body_suffix = ""
        if self._is_high_break_entry_type(meta):
            body_suffix = (
                f"|highestHigh={self._float(meta.get('first_entry_highest_high')):.4f}"
                f"|lastHigh={self._float(meta.get('first_entry_last_high')):.4f}"
                f"|noNewHighBars={int(self._float(meta.get('first_entry_no_new_high_count')))}"
                f"|highStallConfirmed={bool(meta.get('first_entry_high_stall_confirmed'))}"
                f"|bodyStrengthRatio={self._float(meta.get('first_entry_body_strength_ratio')):.4f}"
                f"|bodyContractionCount={int(self._float(meta.get('first_entry_body_contraction_count')))}"
                f"|bodyContractionConfirmed={bool(meta.get('first_entry_body_contraction_confirmed'))}"
                f"|softExitWaitBarsRemaining={int(self._float(meta.get('soft_exit_wait_bars_remaining')))}"
            )
        return (
            f"EXIT|reason={reason}|price={current_open:.4f}|entry={position.entry_price:.4f}"
            f"|qty={position.quantity}|stop={stop:.4f}|R={self._float(meta.get('initial_r')):.4f}"
            f"|entryType={str(meta.get('entry_type') or '')}"
            f"|currentOpen={current_open:.4f}|lastClose={last_close:.4f}"
            f"{body_suffix}"
        )

    def _tema_closed(self, bar: dict) -> bool:
        tema9 = self._float(bar.get("current_open_tema9"))
        tema20 = self._float(bar.get("current_open_tema20"))
        if tema9 <= 0 or tema20 <= 0:
            return False
        return tema20 >= self._tema_exit_threshold(tema9)

    def _tema_exit_threshold(self, tema9: float) -> float:
        return tema9 * (1.0 + self.config.tema9_exit_buffer_pct)

    def _tema_open_threshold(self, tema20: float) -> float:
        return tema20 * (1.0 + self.config.tema9_open_buffer_pct)

    def _tema_exit_tag(self, bar: dict) -> str:
        tema9 = self._float(bar.get("current_open_tema9"))
        tema20 = self._float(bar.get("current_open_tema20"))
        threshold = self._tema_exit_threshold(tema9) if tema9 > 0 else 0.0
        return (
            f"|currentOpenTema9={tema9:.4f}|currentOpenTema20={tema20:.4f}"
            f"|temaThreshold={threshold:.4f}|tema9BufferRatio={self.config.tema9_exit_buffer_pct:.4f}"
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

    def _uses_vwap_trail(self, meta: dict) -> bool:
        return str(meta.get("entry_type") or "") in {"HIGH_BREAK_HOLD", "VWAP_RECLAIM", "FIRST_ENTRY", "WATCHLIST_REENTRY"}

    def _is_high_break_entry_type(self, meta: dict) -> bool:
        return str(meta.get("entry_type") or "") in {"HIGH_BREAK_HOLD", "FIRST_ENTRY"}

    def _update_first_entry_high_cycle(
        self,
        timestamp: datetime,
        symbol: str,
        position,
        bar: dict,
        meta: dict,
    ) -> dict[str, Any]:
        if not self._is_high_break_entry_type(meta):
            return self._first_entry_high_cycle_state(meta)
        if meta.get("_first_entry_high_last_timestamp") == timestamp:
            return self._first_entry_high_cycle_state(meta)

        last_high = self._float(bar.get("last_high"))
        entry_price = self._float(getattr(position, "entry_price", 0.0))
        previous_highest = max(self._float(meta.get("first_entry_highest_high")), entry_price)
        tolerance = max(0.0, self.config.first_entry_high_near_tolerance_ratio)
        near_high_threshold = previous_highest * (1.0 - tolerance) if previous_highest > 0 else 0.0
        new_high = last_high > previous_highest if previous_highest > 0 else last_high > 0
        near_high = last_high >= near_high_threshold if near_high_threshold > 0 else last_high > 0
        highest_high = max(previous_highest, last_high)
        observed_bars = int(self._float(meta.get("first_entry_high_bars_observed")))
        no_new_high_count = int(self._float(meta.get("first_entry_no_new_high_count")))
        no_new_high_count = 0 if new_high or near_high else no_new_high_count + 1
        required_stall_bars = max(1, int(self.config.first_entry_high_stall_bars))
        stall_confirmed = no_new_high_count >= required_stall_bars

        meta["first_entry_high_bars_observed"] = observed_bars + 1
        meta["first_entry_last_high"] = last_high
        meta["first_entry_highest_high"] = highest_high
        meta["first_entry_near_high_threshold"] = near_high_threshold
        meta["first_entry_new_high"] = new_high
        meta["first_entry_near_high"] = near_high
        meta["first_entry_no_new_high_count"] = no_new_high_count
        meta["first_entry_high_stall_confirmed"] = stall_confirmed
        meta["_first_entry_high_last_timestamp"] = timestamp
        return self._first_entry_high_cycle_state(meta)

    def _first_entry_high_cycle_state(self, meta: dict) -> dict[str, Any]:
        enabled = bool(self.config.first_entry_high_lifecycle_exit_enabled)
        return {
            "long_momentum_v9_first_entry_high_lifecycle_enabled": enabled,
            "long_momentum_v9_first_entry_high_bars_observed": int(self._float(meta.get("first_entry_high_bars_observed"))),
            "long_momentum_v9_first_entry_last_high": self._float(meta.get("first_entry_last_high")),
            "long_momentum_v9_first_entry_highest_high": self._float(meta.get("first_entry_highest_high")),
            "long_momentum_v9_first_entry_near_high_threshold": self._float(meta.get("first_entry_near_high_threshold")),
            "long_momentum_v9_first_entry_new_high": bool(meta.get("first_entry_new_high")),
            "long_momentum_v9_first_entry_near_high": bool(meta.get("first_entry_near_high")),
            "long_momentum_v9_first_entry_no_new_high_count": int(self._float(meta.get("first_entry_no_new_high_count"))),
            "long_momentum_v9_first_entry_high_stall_required": max(1, int(self.config.first_entry_high_stall_bars)),
            "long_momentum_v9_first_entry_high_near_tolerance_ratio": max(0.0, self.config.first_entry_high_near_tolerance_ratio),
            "long_momentum_v9_first_entry_high_stall_confirmed": bool(meta.get("first_entry_high_stall_confirmed")),
        }

    def _update_first_entry_body_cycle(self, timestamp: datetime, symbol: str, bar: dict, meta: dict) -> dict[str, Any]:
        if not self._is_high_break_entry_type(meta):
            return self._first_entry_body_cycle_state(meta)
        if meta.get("_first_entry_body_last_timestamp") == timestamp:
            return self._first_entry_body_cycle_state(meta)

        last_open = self._float(bar.get("last_open"))
        last_close = self._float(bar.get("last_close"))
        green_body = max(0.0, last_close - last_open)
        green_body_pct = green_body / last_open if last_open > 0 else 0.0

        fast_bars = max(1.0, self._float(self.config.first_entry_body_fast_ema_bars))
        slow_bars = max(fast_bars, self._float(self.config.first_entry_body_slow_ema_bars))
        fast_alpha = 2.0 / (fast_bars + 1.0)
        slow_alpha = 2.0 / (slow_bars + 1.0)
        previous_fast = meta.get("first_entry_green_body_ema_fast")
        previous_slow = meta.get("first_entry_green_body_ema_slow")
        previous_fast_float = self._float(previous_fast)
        previous_slow_float = self._float(previous_slow)
        fast_ema = green_body_pct if previous_fast is None else (fast_alpha * green_body_pct) + ((1.0 - fast_alpha) * previous_fast_float)
        slow_ema = green_body_pct if previous_slow is None else (slow_alpha * green_body_pct) + ((1.0 - slow_alpha) * previous_slow_float)
        peak_fast = max(self._float(meta.get("first_entry_green_body_peak_ema_fast")), fast_ema)
        strength_ratio = fast_ema / peak_fast if peak_fast > 0 else 1.0
        threshold = max(0.0, self.config.first_entry_body_contraction_ratio)
        observed_bars = int(self._float(meta.get("first_entry_body_bars_observed")))
        no_green_weakness = peak_fast <= 0 and green_body_pct <= 0 and observed_bars > 0
        contracting_now = bool(
            no_green_weakness
            or (
                peak_fast > 0
                and strength_ratio <= threshold
                and (previous_fast is None or fast_ema < previous_fast_float or green_body_pct <= 0)
            )
        )
        contraction_count = int(self._float(meta.get("first_entry_body_contraction_count")))
        contraction_count = contraction_count + 1 if contracting_now else 0
        required_contraction_bars = max(1, int(self.config.first_entry_body_contraction_bars))
        confirmed = contraction_count >= required_contraction_bars

        meta["first_entry_body_bars_observed"] = observed_bars + 1
        meta["first_entry_green_body"] = green_body
        meta["first_entry_green_body_pct"] = green_body_pct
        meta["first_entry_green_body_ema_fast"] = fast_ema
        meta["first_entry_green_body_ema_slow"] = slow_ema
        meta["first_entry_green_body_peak_ema_fast"] = peak_fast
        meta["first_entry_body_strength_ratio"] = strength_ratio
        meta["first_entry_body_contracting_now"] = contracting_now
        meta["first_entry_body_contraction_count"] = contraction_count
        meta["first_entry_body_contraction_confirmed"] = confirmed
        meta["_first_entry_body_last_timestamp"] = timestamp
        return self._first_entry_body_cycle_state(meta)

    def _first_entry_body_cycle_state(self, meta: dict) -> dict[str, Any]:
        enabled = bool(self.config.first_entry_body_lifecycle_exit_enabled)
        strength_ratio = self._float(meta.get("first_entry_body_strength_ratio")) if "first_entry_body_strength_ratio" in meta else 1.0
        return {
            "long_momentum_v9_first_entry_body_lifecycle_enabled": enabled,
            "long_momentum_v9_first_entry_body_bars_observed": int(self._float(meta.get("first_entry_body_bars_observed"))),
            "long_momentum_v9_first_entry_green_body": self._float(meta.get("first_entry_green_body")),
            "long_momentum_v9_first_entry_green_body_pct": self._float(meta.get("first_entry_green_body_pct")),
            "long_momentum_v9_first_entry_green_body_ema_fast": self._float(meta.get("first_entry_green_body_ema_fast")),
            "long_momentum_v9_first_entry_green_body_ema_slow": self._float(meta.get("first_entry_green_body_ema_slow")),
            "long_momentum_v9_first_entry_green_body_peak_ema_fast": self._float(meta.get("first_entry_green_body_peak_ema_fast")),
            "long_momentum_v9_first_entry_body_strength_ratio": strength_ratio,
            "long_momentum_v9_first_entry_body_contracting_now": bool(meta.get("first_entry_body_contracting_now")),
            "long_momentum_v9_first_entry_body_contraction_count": int(self._float(meta.get("first_entry_body_contraction_count"))),
            "long_momentum_v9_first_entry_body_contraction_required": max(1, int(self.config.first_entry_body_contraction_bars)),
            "long_momentum_v9_first_entry_body_contraction_ratio_threshold": max(0.0, self.config.first_entry_body_contraction_ratio),
            "long_momentum_v9_first_entry_body_contraction_confirmed": bool(meta.get("first_entry_body_contraction_confirmed")),
            "long_momentum_v9_first_entry_soft_exit_wait_bars_remaining": int(self._float(meta.get("soft_exit_wait_bars_remaining"))),
            "long_momentum_v9_first_entry_soft_exits_allowed": self._first_entry_soft_exits_allowed(meta),
        }

    def _first_entry_soft_exits_blocked(self, timestamp: datetime, symbol: str, meta: dict) -> bool:
        if not self._is_high_break_entry_type(meta):
            return False
        remaining = int(self._float(meta.get("soft_exit_wait_bars_remaining")))
        if remaining <= 0:
            return False
        if meta.get("_soft_exit_wait_last_timestamp") != timestamp:
            meta["soft_exit_wait_bars_remaining"] = max(0, remaining - 1)
            meta["_soft_exit_wait_last_timestamp"] = timestamp
        return True

    def _first_entry_lifecycle_exits_blocked(self, meta: dict) -> bool:
        if not self._is_high_break_entry_type(meta):
            return False
        if self.config.first_entry_high_lifecycle_exit_enabled and not bool(meta.get("first_entry_high_stall_confirmed")):
            return True
        if self.config.first_entry_body_lifecycle_exit_enabled and not bool(meta.get("first_entry_body_contraction_confirmed")):
            return True
        return False

    def _first_entry_soft_exits_allowed(self, meta: dict) -> bool:
        if not self._is_high_break_entry_type(meta):
            return True
        if int(self._float(meta.get("soft_exit_wait_bars_remaining"))) > 0:
            return False
        if self.config.first_entry_high_lifecycle_exit_enabled and not bool(meta.get("first_entry_high_stall_confirmed")):
            return False
        if self.config.first_entry_body_lifecycle_exit_enabled and not bool(meta.get("first_entry_body_contraction_confirmed")):
            return False
        return True

    def _trace_first_entry_soft_exit_wait(
        self,
        timestamp: datetime,
        symbol: str,
        position,
        bar: dict,
        meta: dict,
    ) -> None:
        if not self.observability:
            return
        self.observability.trace(
            timestamp=timestamp,
            ticker=symbol,
            stage="exit",
            event_type="high_break_hold_soft_exit_wait",
            decision="hold_position",
            reason_code="HIGH_BREAK_HOLD_SOFT_EXIT_WAIT",
            reason="High Break Hold TEMA, 2xBVD, and pocket exits are disabled during the configured wait; the protective stop remains active.",
            values={
                "entry_price": getattr(position, "entry_price", None),
                "quantity": getattr(position, "quantity", None),
                "current_open": self._bar_open(bar),
                "stop_price": getattr(position, "stop_price", None),
                "soft_exit_wait_bars_remaining": meta.get("soft_exit_wait_bars_remaining"),
                "configured_wait_bars": self.config.first_entry_soft_exit_wait_bars,
            },
            force=True,
        )

    def _trace_first_entry_lifecycle_wait(
        self,
        timestamp: datetime,
        symbol: str,
        position,
        bar: dict,
        meta: dict,
        body_state: dict[str, Any],
        high_state: dict[str, Any],
    ) -> None:
        if not self.observability:
            return
        self.observability.trace(
            timestamp=timestamp,
            ticker=symbol,
            stage="exit",
            event_type="high_break_hold_lifecycle_wait",
            decision="hold_position",
            reason_code="HIGH_BREAK_HOLD_LIFECYCLE_WAIT",
            reason="High Break Hold soft exits are waiting for the high structure to stall and any enabled body lifecycle gate to pass.",
            values={
                "entry_price": getattr(position, "entry_price", None),
                "quantity": getattr(position, "quantity", None),
                "current_open": self._bar_open(bar),
                "stop_price": getattr(position, "stop_price", None),
                "highest_high": high_state.get("long_momentum_v9_first_entry_highest_high"),
                "last_high": high_state.get("long_momentum_v9_first_entry_last_high"),
                "near_high_threshold": high_state.get("long_momentum_v9_first_entry_near_high_threshold"),
                "new_high": high_state.get("long_momentum_v9_first_entry_new_high"),
                "near_high": high_state.get("long_momentum_v9_first_entry_near_high"),
                "no_new_high_count": high_state.get("long_momentum_v9_first_entry_no_new_high_count"),
                "required_stall_bars": high_state.get("long_momentum_v9_first_entry_high_stall_required"),
                "high_stall_confirmed": high_state.get("long_momentum_v9_first_entry_high_stall_confirmed"),
                "high_near_tolerance_ratio": high_state.get("long_momentum_v9_first_entry_high_near_tolerance_ratio"),
                "green_body": body_state.get("long_momentum_v9_first_entry_green_body"),
                "green_body_pct": body_state.get("long_momentum_v9_first_entry_green_body_pct"),
                "green_body_ema_fast": body_state.get("long_momentum_v9_first_entry_green_body_ema_fast"),
                "green_body_ema_slow": body_state.get("long_momentum_v9_first_entry_green_body_ema_slow"),
                "peak_green_body_ema_fast": body_state.get("long_momentum_v9_first_entry_green_body_peak_ema_fast"),
                "body_strength_ratio": body_state.get("long_momentum_v9_first_entry_body_strength_ratio"),
                "contraction_count": body_state.get("long_momentum_v9_first_entry_body_contraction_count"),
                "required_contraction_bars": body_state.get("long_momentum_v9_first_entry_body_contraction_required"),
                "contraction_ratio_threshold": body_state.get("long_momentum_v9_first_entry_body_contraction_ratio_threshold"),
            },
            force=True,
        )

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
            current_open = self._float(row.get("current_open"))
            last_day_high_so_far = self._float(row.get("last_day_high_so_far"))
            high_break_day_high_break_ok = current_open > 0 and last_day_high_so_far > 0 and current_open >= last_day_high_so_far
            high_watch = self.high_break_hold_watchlist.get(watch.ticker)
            position = portfolio.positions.get(watch.ticker)
            meta = self.position_meta.get(watch.ticker, {})
            pocket_state = self._pocket_state(row, position)
            high_state = self._first_entry_high_cycle_state(meta)
            body_state = self._first_entry_body_cycle_state(meta)
            rows.append(
                {
                    "timestamp": context.timestamp,
                    "session_date": self.session_date.isoformat() if self.session_date else "",
                    "ticker": watch.ticker,
                    "watchlist_added_timestamp": watch.added_timestamp,
                    "watchlist_state": "held" if watch.ticker in portfolio.positions else watch.last_state,
                    "watchlist_entry_submitted": watch.entry_submitted,
                    "watchlist_first_entry_submitted": watch.first_entry_submitted,
                    "watchlist_first_entry_filled": watch.first_entry_filled,
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
                    "last_day_high_so_far": row.get("last_day_high_so_far"),
                    "first_entry_day_high_break_threshold": last_day_high_so_far if last_day_high_so_far > 0 else None,
                    "first_entry_day_high_break_ok": high_break_day_high_break_ok,
                    "high_break_hold_watch_active": high_watch is not None,
                    "high_break_hold_detected_timestamp": high_watch.detected_timestamp if high_watch else None,
                    "high_break_hold_breakout_level": high_watch.breakout_level if high_watch else None,
                    "high_break_hold_threshold": high_watch.breakout_level * (1.0 - max(0.0, self.config.high_break_hold_tolerance_ratio)) if high_watch else None,
                    "high_break_hold_count": high_watch.hold_count if high_watch else 0,
                    "high_break_hold_confirmation_bars": max(1, int(self.config.high_break_hold_confirmation_bars)),
                    "high_break_hold_ok": bool(high_watch and high_watch.last_hold_ok),
                    "high_break_hold_ready": bool(high_watch and high_watch.hold_count >= max(1, int(self.config.high_break_hold_confirmation_bars))),
                    "last_close_minus_vwap": last_close - last_vwap if last_vwap > 0 else None,
                    "last_tema_open": row.get("last_tema_open"),
                    "last_double_timeframe_bearish_volume_divergence_score": row.get("last_double_timeframe_bearish_volume_divergence_score"),
                    "current_open": row.get("current_open"),
                    "long_momentum_v9_pocket_mode": pocket_state.get("long_momentum_v9_pocket_mode"),
                    "long_momentum_v9_pocket_pct": pocket_state.get("long_momentum_v9_pocket_pct"),
                    "long_momentum_v9_pocket_vol_pct": pocket_state.get("long_momentum_v9_pocket_vol_pct"),
                    "long_momentum_v9_pocket_current_open": pocket_state.get("long_momentum_v9_pocket_current_open"),
                    "long_momentum_v9_pocket_sell_offset": pocket_state.get("long_momentum_v9_pocket_sell_offset"),
                    "long_momentum_v9_pocket_estimated_bid": pocket_state.get("long_momentum_v9_pocket_estimated_bid"),
                    "long_momentum_v9_pocket_trigger_price": pocket_state.get("long_momentum_v9_pocket_trigger_price"),
                    "long_momentum_v9_pocket_remaining_to_trigger": pocket_state.get("long_momentum_v9_pocket_remaining_to_trigger"),
                    "long_momentum_v9_pocket_open": pocket_state.get("long_momentum_v9_pocket_open"),
                    "last_true_range_ema5_pct": row.get("last_true_range_ema5_pct"),
                    **high_state,
                    **body_state,
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

    def _record_high_break_hold_snapshot(self, context: BarContext, portfolio: Portfolio) -> None:
        if not self.high_break_hold_watchlist:
            return
        rows = []
        required = max(1, int(self.config.high_break_hold_confirmation_bars))
        tolerance = max(0.0, self.config.high_break_hold_tolerance_ratio)
        for watch in self.high_break_hold_watchlist.values():
            row = self.states.get(watch.ticker).row if watch.ticker in self.states else {}
            position = portfolio.positions.get(watch.ticker)
            threshold = watch.breakout_level * (1.0 - tolerance) if watch.breakout_level > 0 else None
            rows.append(
                {
                    "timestamp": context.timestamp,
                    "session_date": self.session_date.isoformat() if self.session_date else "",
                    "ticker": watch.ticker,
                    "detected_timestamp": watch.detected_timestamp,
                    "breakout_level": watch.breakout_level,
                    "detected_current_open": watch.detected_current_open,
                    "hold_threshold": threshold,
                    "hold_count": watch.hold_count,
                    "confirmation_bars": required,
                    "hold_ok": watch.last_hold_ok,
                    "ready": watch.hold_count >= required,
                    "entry_submitted": watch.entry_submitted,
                    "entry_filled": watch.entry_filled,
                    "state": "held" if position is not None else watch.last_state,
                    "current_open": row.get("current_open"),
                    "last_open": row.get("last_open"),
                    "last_close": row.get("last_close"),
                    "last_high": row.get("last_high"),
                    "last_day_high_so_far": row.get("last_day_high_so_far"),
                    "last_vwap": row.get("last_vwap"),
                    "last_5m_return": row.get("last_5m_return"),
                    "last_transactions": row.get("last_transactions"),
                }
            )
        rows.sort(key=lambda item: (bool(item.get("ready")), self._float(item.get("last_5m_return"))), reverse=True)
        self.high_break_hold_snapshots.extend(rows[: max(1, int(self.config.watchlist_snapshot_limit))])
        if self.observability:
            self.observability.state(
                timestamp=context.timestamp,
                scope="long_momentum_v9_high_break_hold_watchlist",
                state={
                    "watchlist_count": len(self.high_break_hold_watchlist),
                    "ready_count": len([watch for watch in self.high_break_hold_watchlist.values() if watch.hold_count >= required]),
                    "entry_submitted_count": len([watch for watch in self.high_break_hold_watchlist.values() if watch.entry_submitted]),
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
                "high_break_hold_count": len([row for row in candidates if row.get("long_momentum_v9_high_break_hold_entry_open")]),
                "vwap_reclaim_count": len([row for row in candidates if row.get("long_momentum_v9_vwap_reclaim_entry_open")]),
                "first_entry_count": len([row for row in candidates if row.get("long_momentum_v9_high_break_hold_entry_open")]),
                "watchlist_entry_count": len([row for row in candidates if row.get("long_momentum_v9_vwap_reclaim_entry_open")]),
                "scanned_count": len(rows),
                "watchlist_count": len(self.momentum_watchlist),
                "high_break_watchlist_count": len(self.high_break_hold_watchlist),
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
                "high_break_hold_count": len([row for row in candidates if row.get("long_momentum_v9_high_break_hold_entry_open")]),
                "vwap_reclaim_count": len([row for row in candidates if row.get("long_momentum_v9_vwap_reclaim_entry_open")]),
                "first_entry_count": len([row for row in candidates if row.get("long_momentum_v9_high_break_hold_entry_open")]),
                "watchlist_entry_count": len([row for row in candidates if row.get("long_momentum_v9_vwap_reclaim_entry_open")]),
                "watchlist_count": len(self.momentum_watchlist),
                "high_break_watchlist_count": len(self.high_break_hold_watchlist),
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

    def _trace_high_break_hold_add(self, timestamp: datetime, ticker: str, row: dict[str, Any], watch: HighBreakHoldWatch) -> None:
        if not self.observability:
            return
        self.observability.trace(
            timestamp=timestamp,
            ticker=ticker,
            stage="watchlist",
            event_type="high_break_hold_watch_add",
            decision="add_to_high_break_hold_watchlist",
            reason_code="DAY_HIGH_BREAK_WAIT_FOR_HOLD",
            reason="Ticker hit the day-high break trigger; high-break hold entry now waits for configured hold confirmation on later bars.",
            values={
                "current_open": row.get("current_open"),
                "last_day_high_so_far": row.get("last_day_high_so_far"),
                "breakout_level": watch.breakout_level,
                "hold_tolerance_ratio": self.config.high_break_hold_tolerance_ratio,
                "confirmation_bars": self.config.high_break_hold_confirmation_bars,
            },
            force=True,
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
            reason="Ticker passed either the high-break hold entry or the VWAP reclaim entry; eligible candidates share available cash by priority group.",
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
                "last_day_high_so_far": candidate.get("last_day_high_so_far"),
                "high_break_hold_breakout_level": candidate.get("long_momentum_v9_high_break_hold_breakout_level"),
                "high_break_hold_count": candidate.get("long_momentum_v9_high_break_hold_count"),
                "high_break_hold_confirmation_bars": candidate.get("long_momentum_v9_high_break_hold_confirmation_bars"),
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
        if row.get("long_momentum_v9_high_break_hold_entry_open"):
            return "HIGH_BREAK_HOLD"
        if row.get("long_momentum_v9_vwap_reclaim_entry_open"):
            return "VWAP_RECLAIM"
        return str(row.get("long_momentum_v9_reject_reason") or "filtered")

    def _v9_reject_reason(
        self,
        *,
        price_eligible: bool,
        watch_active: bool,
        high_break_watch_active: bool,
        high_break_available: bool,
        high_break_hold_ready: bool,
        high_break_day_high_break_ok: bool,
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
        if watch_active and not high_break_watch_active and not high_break_day_high_break_ok:
            return "high_break_wait_day_high_break"
        if high_break_available and not high_break_hold_ready:
            return "high_break_wait_hold_confirmation"
        if not watch_entry_ready:
            return "watchlist_entry_wait_next_bar"
        if not reentry_price_reclaim:
            return "vwap_reclaim_below_vwap_buffer"
        if not reentry_last_bar_not_red:
            return "vwap_reclaim_red_reclaim_bar"
        if not reentry_last_tema_open_ok:
            return "vwap_reclaim_last_tema_not_open"
        if not reentry_bvd_ok:
            return "vwap_reclaim_bearish_volume_divergence"
        if not reentry_body_break_ok:
            return "vwap_reclaim_two_bar_body_break"
        return "filtered"

__all__ = ["LongMomentumV9Strategy"]
