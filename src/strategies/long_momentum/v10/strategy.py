from __future__ import annotations

from datetime import datetime
from typing import Any

import polars as pl

from src.backtest.models import BarContext, Order, OrderRequest
from src.backtest.portfolio import Portfolio
from src.strategies.long_momentum.v10.config import LongMomentumV10Config
from src.strategies.long_momentum.v9.strategy import (
    ADAPTIVE_POCKET_V9_COLUMNS,
    REQUIRED_V9_COLUMNS,
    LongMomentumV9Strategy,
)


class LongMomentumV10Strategy(LongMomentumV9Strategy):
    """V9 watchlist and entries with longer High Break Hold exits."""

    def __init__(self, config: LongMomentumV10Config | None = None):
        super().__init__(config or LongMomentumV10Config())
        self.config: LongMomentumV10Config

    def _validate_provider_columns(self, frame: pl.DataFrame) -> None:
        required_columns = list(REQUIRED_V9_COLUMNS)
        if self.config.adaptive_pocket_enabled:
            required_columns.extend(ADAPTIVE_POCKET_V9_COLUMNS)
        missing = [column for column in required_columns if column not in frame.columns]
        if missing:
            date_text = self.session_date.isoformat() if self.session_date else "unknown session"
            feature_text = "core, momentum, volatility, and volume_liquidity" if self.config.adaptive_pocket_enabled else "core, momentum, and volume_liquidity"
            raise ValueError(
                f"Long Momentum v10 requires provider-built strategy-time {feature_text} features for {date_text}; "
                f"missing columns: {', '.join(missing)}. In the Build Data page, run the Long Momentum v9 feature build "
                f"for a range that includes {date_text}; v10 uses the same 1m core, momentum, session, volatility, and volume_liquidity features."
            )

    def _update_high_break_hold_watch(self, timestamp: datetime, row: dict[str, Any], watch) -> None:
        if not self.config.enable_high_break_hold_entry:
            return
        super()._update_high_break_hold_watch(timestamp, row, watch)

    def _evaluate_v9_row(self, row: dict[str, Any], ticker: str, portfolio: Portfolio, pending_symbols: set[str]) -> dict[str, Any]:
        result = super()._evaluate_v9_row(row, ticker, portfolio, pending_symbols)
        high_break_enabled = bool(self.config.enable_high_break_hold_entry)
        vwap_reclaim_enabled = bool(self.config.enable_vwap_reclaim_entry)
        high_break_open = high_break_enabled and bool(result.get("long_momentum_v9_high_break_hold_entry_open"))
        vwap_reclaim_base_open = bool(
            result.get("long_momentum_v9_price_eligible")
            and result.get("long_momentum_v9_watchlist_active")
            and result.get("long_momentum_v9_watchlist_entry_ready")
            and result.get("long_momentum_v9_no_symbol_position")
            and result.get("long_momentum_v9_entry_time_ok")
            and result.get("long_momentum_v9_vwap_reclaim_price_reclaim")
            and result.get("long_momentum_v9_vwap_reclaim_last_bar_not_red")
            and result.get("long_momentum_v9_vwap_reclaim_last_tema_open_ok")
            and result.get("long_momentum_v9_vwap_reclaim_bvd_ok")
            and result.get("long_momentum_v9_vwap_reclaim_body_break_ok")
            and result.get("long_momentum_v9_vwap_reclaim_open_not_below_last_body")
        )
        vwap_reclaim_open = vwap_reclaim_enabled and vwap_reclaim_base_open and not high_break_open
        result.update(
            {
                "long_momentum_v10_high_break_hold_enabled": high_break_enabled,
                "long_momentum_v10_vwap_reclaim_enabled": vwap_reclaim_enabled,
                "long_momentum_v9_high_break_hold_entry_open": high_break_open,
                "long_momentum_v9_vwap_reclaim_entry_open": vwap_reclaim_open,
                "long_momentum_v9_first_entry_open": high_break_open,
                "long_momentum_v9_reentry_open": vwap_reclaim_open,
                "long_momentum_v9_entry_priority": 2 if high_break_open else 1 if vwap_reclaim_open else 0,
                "long_momentum_v9_entry_open": high_break_open or vwap_reclaim_open,
                "entry_trigger": "HIGH_BREAK_HOLD" if high_break_open else "VWAP_RECLAIM" if vwap_reclaim_open else "",
            }
        )
        if not high_break_enabled and bool(result.get("long_momentum_v9_high_break_hold_ready")):
            result["long_momentum_v9_reject_reason"] = "high_break_hold_disabled"
        elif not vwap_reclaim_enabled and vwap_reclaim_base_open:
            result["long_momentum_v9_reject_reason"] = "vwap_reclaim_disabled"
        return result

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

        handled_high_break_symbols: set[str] = set()
        for symbol, position in list(portfolio.positions.items()):
            if symbol in pending_sell_symbols:
                continue
            bar = context.updates_by_symbol.get(symbol)
            if bar is None:
                continue
            meta = self._position_meta(symbol, position)
            if not self._is_high_break_entry_type(meta):
                continue
            request = self._v10_high_break_exit_request(context.timestamp, symbol, position, bar, meta)
            if request is not None:
                requests.append(request)
                handled_high_break_symbols.add(symbol)

        skip_symbols = set(current_bar_sell_symbols or set()) | handled_high_break_symbols
        requests.extend(super()._exit_requests(context, portfolio, pending_orders, current_bar_sell_symbols=skip_symbols))
        return requests

    def _v10_high_break_exit_request(
        self,
        timestamp: datetime,
        symbol: str,
        position,
        bar: dict,
        meta: dict,
    ) -> OrderRequest | None:
        self._update_first_entry_high_cycle(timestamp, symbol, position, bar, meta)
        self._update_first_entry_body_cycle(timestamp, symbol, bar, meta)
        current_open = self._bar_open(bar)
        entry_price = self._float(getattr(position, "entry_price", 0.0))
        take_profit_pct = max(0.0, self.config.high_break_take_profit_pct)
        take_profit_price = entry_price * (1.0 + take_profit_pct) if entry_price > 0 else 0.0
        if take_profit_price > 0 and current_open > take_profit_price:
            limit_price = self._liquid_limit_price("SELL", bar)
            reason = "HIGH_BREAK_TAKE_PROFIT"
            self._trace_exit(timestamp, symbol, reason, position, bar, meta)
            return OrderRequest(
                symbol=symbol,
                side="SELL",
                quantity=position.quantity,
                order_type="LIMIT",
                reason=reason,
                limit_price=limit_price,
                allow_same_bar_fill=True,
                tag=(
                    self._exit_tag(reason, position, bar, meta)
                    + f"|limit={limit_price:.4f}|takeProfitPct={take_profit_pct:.4f}"
                    + f"|takeProfitPrice={take_profit_price:.4f}"
                ),
            )

        stop_price = self._v10_high_break_vwap_touch_stop(symbol, position, bar, meta)
        if stop_price <= 0:
            return None
        position.stop_price = stop_price
        reason = "HIGH_BREAK_MAX_VWAP_TOUCH"
        return OrderRequest(
            symbol=symbol,
            side="SELL",
            quantity=position.quantity,
            order_type="STOP",
            reason=reason,
            stop_price=stop_price,
            tag=(
                self._exit_tag(reason, position, bar, meta)
                + f"|dayMaxVwap={self._v10_day_max_vwap(symbol, bar):.4f}"
                + f"|takeProfitPct={take_profit_pct:.4f}|takeProfitPrice={take_profit_price:.4f}"
            ),
            allow_same_bar_fill=True,
            expire_on_bar_close=True,
        )

    def _v10_high_break_vwap_touch_stop(self, symbol: str, position, bar: dict, meta: dict) -> float:
        day_max_vwap = self._v10_day_max_vwap(symbol, bar)
        current_stop = self._float(getattr(position, "stop_price", 0.0))
        initial_stop = self._float(meta.get("initial_stop"))
        if day_max_vwap > 0:
            stop_price = max(current_stop, day_max_vwap)
            meta["high_break_v10_day_max_vwap_stop"] = stop_price
            return stop_price
        return max(current_stop, initial_stop)

    def _v10_day_max_vwap(self, symbol: str, bar: dict) -> float:
        watch = self.momentum_watchlist.get(symbol)
        return max(
            self._float(self.day_max_vwap_by_ticker.get(symbol)),
            self._float(watch.max_vwap if watch else 0.0),
            self._float(bar.get("last_vwap")),
        )

    def _sync_recent_fills(self, context: BarContext) -> None:
        super()._sync_recent_fills(context)
        for fill in context.recent_fills:
            symbol = str(fill.get("symbol") or "").upper()
            if not symbol or str(fill.get("side") or "").upper() != "SELL":
                continue
            tag = str(fill.get("tag") or "")
            reason = str(fill.get("reason") or "")
            meta = self.position_meta.get(symbol, {})
            high_break_exit = (
                self._is_high_break_entry_type(meta)
                or reason.startswith("HIGH_BREAK_")
                or "entryType=HIGH_BREAK_HOLD" in tag
                or "entryType=FIRST_ENTRY" in tag
            )
            if not high_break_exit:
                continue
            watch = self.momentum_watchlist.get(symbol)
            if watch is not None:
                watch.entry_submitted = False
                watch.first_entry_submitted = False
                watch.first_entry_filled = False
                watch.last_state = "waiting_new_high_break"
            self.high_break_hold_watchlist.pop(symbol, None)


__all__ = ["LongMomentumV10Strategy"]
