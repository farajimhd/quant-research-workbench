from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import polars as pl

from src.backtest.data.minute_bars import DayFrames
from src.backtest.models import BarContext, DataRequirements, Order, OrderRequest
from src.backtest.portfolio import Portfolio
from src.strategies.long_momentum.v3.strategy import LongMomentumV3SymbolState
from src.strategies.long_momentum.v11.config import LongMomentumV11Config
from src.strategies.long_momentum.v11.presentation import chart_presentation
from src.strategies.long_momentum.v9.strategy import REQUIRED_V9_COLUMNS, LongMomentumV9Strategy


REQUIRED_V11_COLUMNS = tuple(dict.fromkeys((*REQUIRED_V9_COLUMNS, "last_transactions_avg_prior_3")))


@dataclass(slots=True)
class PopWatch:
    ticker: str
    added_timestamp: datetime
    pop_high: float
    pop_close: float
    pop_vwap: float
    pop_transactions: float
    prior_pop_3_avg_transactions: float
    pop_transaction_ratio: float
    bars_since_pop: int = 0
    entry_submitted: bool = False
    entry_filled: bool = False
    last_entry_transaction_ratio: float = 0.0
    max_distance_above_vwap: float = 0.0
    previous_vwap: float = 0.0
    vwap_slope_down_count: int = 0
    last_state: str = "watching"


class LongMomentumV11Strategy(LongMomentumV9Strategy):
    """Price-pop continuation with transaction-shock watchlist and VWAP-distance exits."""

    def __init__(self, config: LongMomentumV11Config | None = None):
        super().__init__(config or LongMomentumV11Config())
        self.config: LongMomentumV11Config
        self.pop_watchlist: dict[str, PopWatch] = {}
        self.pop_watchlist_snapshots: list[dict] = []

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
        self.high_break_hold_watchlist = {}
        self.day_max_vwap_by_ticker = {}
        self.pop_watchlist = {}
        self.last_scanner_rows = []
        frame = frames.event_frame.filter(
            (pl.col("minute_of_day") >= self.config.trading_start_minute)
            & (pl.col("minute_of_day") < self.config.trading_end_minute)
        )
        self._validate_provider_columns(frame)
        return self._with_last_5m_return(frame)

    def artifacts(self) -> dict[str, list[dict]]:
        artifacts = super().artifacts()
        artifacts["watchlist_snapshots"] = self.pop_watchlist_snapshots
        artifacts["pop_watchlist_snapshots"] = self.pop_watchlist_snapshots
        return artifacts

    def _validate_provider_columns(self, frame: pl.DataFrame) -> None:
        missing = [column for column in REQUIRED_V11_COLUMNS if column not in frame.columns]
        if missing:
            date_text = self.session_date.isoformat() if self.session_date else "unknown session"
            raise ValueError(
                f"Long Momentum v11 requires provider-built strategy-time core, momentum, session, and volume_liquidity features "
                f"for {date_text}; missing columns: {', '.join(missing)}. Rebuild market data with current Long Momentum features."
            )

    def on_bar(self, context: BarContext, portfolio: Portfolio, pending_orders: list[Order]) -> list[OrderRequest]:
        self._update_states(context)
        self._sync_recent_fills(context)

        requests: list[OrderRequest] = []
        active_pending_orders = [order for order in pending_orders if order.status == "OPEN"]
        requests.extend(self._partial_residual_requests(context, portfolio))
        requests.extend(self._cancel_expired_entry_requests(context, active_pending_orders))
        current_bar_residual_symbols = {request.symbol for request in requests}
        requests.extend(self._v11_exit_requests(context, portfolio, active_pending_orders, current_bar_residual_symbols))

        rows = self._scanner_rows(context, portfolio, active_pending_orders)
        candidates = [row for row in rows if row.get("long_momentum_v11_entry_open")]
        self._record_scanner(context, rows, candidates, portfolio, active_pending_orders)

        blocked_symbols = {
            order.symbol for order in active_pending_orders if order.side == "BUY"
        } | {
            request.symbol for request in requests if request.side == "BUY"
        } | set(portfolio.positions)
        candidates = [
            row for row in candidates
            if str(row.get("ticker") or "") not in blocked_symbols
        ][: max(0, int(self.config.max_pop_breakout_candidates_per_bar))]

        available_cash = self._available_cash_after_submitted_requests(
            portfolio=portfolio,
            requests=requests,
            context=context,
        )
        if candidates and available_cash > 0:
            submitted = self._submit_entry_group(
                candidates=candidates,
                context=context,
                available_cash=available_cash,
                entry_type="POP_BREAKOUT",
            )
            requests.extend(submitted)

        self._record_pop_watchlist_snapshot(context, portfolio)
        return requests

    def _cancel_expired_entry_requests(self, context: BarContext, pending_orders: list[Order]) -> list[OrderRequest]:
        requests: list[OrderRequest] = []
        expiry = max(1, int(self.config.entry_expire_bars))
        for order in pending_orders:
            if order.side != "BUY" or order.order_type != "STOP" or order.reason != "LONG_MOMENTUM_V11_POP_BREAKOUT":
                continue
            if getattr(order, "deferred_fill_at_next_open", False):
                continue
            watch = self.pop_watchlist.get(order.symbol)
            if watch is None or watch.bars_since_pop <= expiry:
                continue
            watch.entry_submitted = False
            watch.last_state = "expired"
            requests.append(
                OrderRequest(
                    symbol=order.symbol,
                    side="BUY",
                    quantity=order.quantity,
                    order_type="CANCEL",
                    reason="V11_POP_ENTRY_EXPIRED",
                    tag=f"CANCEL|reason=V11_POP_ENTRY_EXPIRED|barsSincePop={watch.bars_since_pop}|expiry={expiry}",
                )
            )
        return requests

    def _update_states(self, context: BarContext) -> None:
        for raw in context.updates.iter_rows(named=True):
            row = dict(raw)
            ticker = str(row.get("ticker") or "")
            state = self.states.get(ticker)
            if state is None:
                state = LongMomentumV3SymbolState(ticker=ticker)
                self.states[ticker] = state
            state.last_timestamp = context.timestamp
            state.row = row
            self._update_pop_watch(context.timestamp, row)

    def _sync_recent_fills(self, context: BarContext) -> None:
        for fill in context.recent_fills:
            symbol = str(fill.get("symbol") or "").upper()
            if not symbol:
                continue
            watch = self.pop_watchlist.get(symbol)
            if watch is None:
                continue
            side = str(fill.get("side") or "").upper()
            if side == "BUY":
                watch.entry_submitted = True
                watch.entry_filled = True
                watch.last_state = "in_position"
                meta = self.position_meta.get(symbol)
                if meta is not None:
                    meta["entry_type"] = "POP_BREAKOUT"
                    meta["entry_fill_timestamp"] = context.timestamp
                    meta["previous_vwap"] = watch.pop_vwap
            elif side == "SELL":
                watch.entry_submitted = False
                watch.last_state = "closed"

    def _update_pop_watch(self, timestamp: datetime, row: dict[str, Any]) -> None:
        ticker = str(row.get("ticker") or "").upper()
        if not ticker:
            return
        watch = self.pop_watchlist.get(ticker)
        if watch is not None:
            watch.bars_since_pop += 1
            return
        last_close = self._float(row.get("last_close"))
        last_5m_return = self._float(row.get("last_5m_return"))
        last_volume = self._float(row.get("last_volume"))
        last_transactions = self._float(row.get("last_transactions"))
        prior_avg = self._float(row.get("last_transactions_avg_prior_3"))
        pop_vwap = self._float(row.get("last_vwap"))
        pop_high = self._float(row.get("last_high"))
        price_ok = self.config.min_price <= last_close <= self.config.max_price
        return_ok = last_5m_return >= self.config.min_last_5m_return
        volume_ok = last_volume >= self.config.min_watchlist_add_volume
        pop_ratio = last_transactions / prior_avg if prior_avg > 0 else 0.0
        pop_liquidity_ok = pop_ratio >= self.config.min_pop_transaction_ratio
        if not (price_ok and return_ok and volume_ok and pop_liquidity_ok and pop_vwap > 0 and pop_high > 0):
            return
        watch = PopWatch(
            ticker=ticker,
            added_timestamp=timestamp,
            pop_high=pop_high,
            pop_close=last_close,
            pop_vwap=pop_vwap,
            pop_transactions=last_transactions,
            prior_pop_3_avg_transactions=prior_avg,
            pop_transaction_ratio=pop_ratio,
        )
        self.pop_watchlist[ticker] = watch
        self._trace_pop_watch_add(timestamp, row, watch)

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
            row.update(self._evaluate_v11_row(row, ticker, portfolio, pending_symbols))
            row["entry_open"] = bool(row["long_momentum_v11_entry_open"])
            row["long_momentum_entry_open"] = row["entry_open"]
            row["scanner_score"] = self._float(row.get("long_momentum_v11_entry_transaction_ratio"))
            row["status"] = self._scanner_status(row, ticker, portfolio, pending_symbols)
            row["entry_state"] = "POP_BREAKOUT" if row["entry_open"] else str(row.get("long_momentum_v11_reject_reason") or "filtered")
            rows.append(row)
        rows.sort(
            key=lambda item: (
                int(bool(item.get("long_momentum_v11_entry_open"))),
                self._float(item.get("long_momentum_v11_pop_transaction_ratio")),
                self._float(item.get("long_momentum_v11_entry_transaction_ratio")),
                self._float(item.get("last_transactions")),
            ),
            reverse=True,
        )
        for rank, row in enumerate(rows, start=1):
            row["rank"] = rank
            row["entry_rank"] = rank if row.get("entry_open") else None
        self.last_scanner_rows = [dict(row) for row in rows[: max(500, int(self.config.watchlist_snapshot_limit))]]
        return rows

    def _evaluate_v11_row(self, row: dict[str, Any], ticker: str, portfolio: Portfolio, pending_symbols: set[str]) -> dict[str, Any]:
        watch = self.pop_watchlist.get(ticker)
        last_close = self._float(row.get("last_close"))
        last_5m_return = self._float(row.get("last_5m_return"))
        last_volume = self._float(row.get("last_volume"))
        last_transactions = self._float(row.get("last_transactions"))
        prior_avg = self._float(row.get("last_transactions_avg_prior_3"))
        current_open = self._float(row.get("current_open"))
        last_vwap = self._float(row.get("last_vwap"))
        price_ok = self.config.min_price <= last_close <= self.config.max_price
        return_ok = last_5m_return >= self.config.min_last_5m_return
        volume_ok = last_volume >= self.config.min_watchlist_add_volume
        pop_ratio_raw = last_transactions / prior_avg if prior_avg > 0 else 0.0
        pop_liquidity_ok_raw = pop_ratio_raw >= self.config.min_pop_transaction_ratio
        entry_time_ok = self.config.trading_start_minute <= int(self._float(row.get("minute_of_day"))) < self.config.trading_end_minute
        pending_symbol_order = ticker in pending_symbols
        no_symbol_position = ticker not in portfolio.positions and not pending_symbol_order
        watch_active = watch is not None
        entry_ratio = last_transactions / watch.prior_pop_3_avg_transactions if watch and watch.prior_pop_3_avg_transactions > 0 else 0.0
        entry_liquidity_ok = entry_ratio >= self.config.min_entry_transaction_ratio
        not_expired = bool(watch and watch.bars_since_pop <= max(1, int(self.config.entry_expire_bars)))
        buy_stop = watch.pop_high + max(0.0, self.config.pop_entry_stop_offset_dollars) if watch else 0.0
        buy_limit = buy_stop + max(0.0, self.config.pop_entry_limit_offset_dollars) if buy_stop > 0 else 0.0
        stop_price = self._vwap_offset_stop(watch.pop_vwap) if watch else 0.0
        meta = self.position_meta.get(ticker, {})
        above_pop_vwap = bool(watch and current_open >= watch.pop_vwap and last_close >= watch.pop_vwap)
        max_entry_open = watch.pop_high * (1.0 + max(0.0, self.config.max_entry_extension_above_pop_high_pct)) if watch else 0.0
        not_too_extended = bool(watch and max_entry_open > 0 and current_open <= max_entry_open)
        risk_ok = bool(stop_price > 0 and buy_stop > stop_price)
        entry_open = bool(
            price_ok
            and watch_active
            and not watch.entry_submitted
            and not watch.entry_filled
            and not_expired
            and no_symbol_position
            and entry_time_ok
            and entry_liquidity_ok
            and above_pop_vwap
            and not_too_extended
            and risk_ok
        )
        return {
            "long_momentum_v11_price_eligible": price_ok,
            "long_momentum_v11_return_ok": return_ok,
            "long_momentum_v11_watchlist_add_volume_ok": volume_ok,
            "long_momentum_v11_prior_pop_3_avg_transactions": prior_avg,
            "long_momentum_v11_raw_pop_transaction_ratio": pop_ratio_raw,
            "long_momentum_v11_pop_liquidity_ok": pop_liquidity_ok_raw,
            "long_momentum_v11_watchlist_add_open": price_ok and return_ok and volume_ok and pop_liquidity_ok_raw and last_vwap > 0,
            "long_momentum_v11_watchlist_active": watch_active,
            "long_momentum_v11_pop_added_timestamp": watch.added_timestamp.isoformat() if watch else "",
            "long_momentum_v11_pop_high": watch.pop_high if watch else None,
            "long_momentum_v11_pop_close": watch.pop_close if watch else None,
            "long_momentum_v11_pop_vwap": watch.pop_vwap if watch else None,
            "long_momentum_v11_pop_transactions": watch.pop_transactions if watch else None,
            "long_momentum_v11_pop_prior_3_avg_transactions": watch.prior_pop_3_avg_transactions if watch else None,
            "long_momentum_v11_pop_transaction_ratio": watch.pop_transaction_ratio if watch else None,
            "long_momentum_v11_bars_since_pop": watch.bars_since_pop if watch else None,
            "long_momentum_v11_entry_transaction_ratio": entry_ratio,
            "long_momentum_v11_entry_liquidity_ok": entry_liquidity_ok,
            "long_momentum_v11_entry_not_expired": not_expired,
            "long_momentum_v11_entry_above_pop_vwap": above_pop_vwap,
            "long_momentum_v11_entry_not_too_extended": not_too_extended,
            "long_momentum_v11_buy_stop": buy_stop if buy_stop > 0 else None,
            "long_momentum_v11_buy_limit": buy_limit if buy_limit > 0 else None,
            "long_momentum_v11_initial_stop": stop_price if stop_price > 0 else None,
            "long_momentum_v11_risk_ok": risk_ok,
            "long_momentum_v11_pending_symbol_order": pending_symbol_order,
            "long_momentum_v11_no_symbol_position": no_symbol_position,
            "long_momentum_v11_entry_time_ok": entry_time_ok,
            "long_momentum_v11_current_distance_above_vwap": meta.get("current_distance_above_vwap"),
            "long_momentum_v11_max_distance_above_vwap": meta.get("max_distance_above_vwap"),
            "long_momentum_v11_vwap_slope": meta.get("vwap_slope"),
            "long_momentum_v11_vwap_slope_down_count": meta.get("vwap_slope_down_count"),
            "long_momentum_v11_entry_open": entry_open,
            "long_momentum_v11_reject_reason": self._v11_reject_reason(
                price_ok=price_ok,
                watch_active=watch_active,
                not_expired=not_expired,
                no_symbol_position=no_symbol_position,
                entry_time_ok=entry_time_ok,
                entry_liquidity_ok=entry_liquidity_ok,
                above_pop_vwap=above_pop_vwap,
                not_too_extended=not_too_extended,
                risk_ok=risk_ok,
            ),
        }

    def _entry_request_for_type(
        self,
        candidate: dict,
        context: BarContext,
        available_cash: float,
        entry_type: str,
    ) -> OrderRequest | None:
        symbol = str(candidate["ticker"])
        entry_price = self._float(candidate.get("long_momentum_v11_buy_stop"))
        limit_price = self._float(candidate.get("long_momentum_v11_buy_limit"))
        stop_price = self._float(candidate.get("long_momentum_v11_initial_stop"))
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
        score = self._float(candidate.get("long_momentum_v11_entry_transaction_ratio"))
        self._set_entry_metadata(symbol, candidate, rank=rank, score=score, stop_price=stop_price)
        self.entry_order_metadata[symbol]["entry_type"] = entry_type
        self.position_meta[symbol] = {
            "initial_stop": stop_price,
            "initial_r": risk_per_share,
            "entry_score": score,
            "entry_type": entry_type,
            "pop_high": candidate.get("long_momentum_v11_pop_high"),
            "pop_vwap": candidate.get("long_momentum_v11_pop_vwap"),
            "pop_transaction_ratio": candidate.get("long_momentum_v11_pop_transaction_ratio"),
            "entry_transaction_ratio": score,
            "max_distance_above_vwap": 0.0,
            "previous_vwap": candidate.get("long_momentum_v11_pop_vwap"),
            "vwap_slope_down_count": 0,
        }
        watch = self.pop_watchlist.get(symbol)
        if watch is not None:
            watch.entry_submitted = True
            watch.last_entry_transaction_ratio = score
            watch.last_state = "buy_stop_submitted"
        self._trace_entry(context.timestamp, candidate, quantity, entry_price, stop_price)
        return OrderRequest(
            symbol=symbol,
            side="BUY",
            quantity=quantity,
            order_type="STOP",
            reason="LONG_MOMENTUM_V11_POP_BREAKOUT",
            stop_price=entry_price,
            limit_price=limit_price,
            allow_same_bar_fill=True,
            protective_stop_price=stop_price,
            tag=(
                f"ENTRY|rule=LONG_MOMENTUM_V11|trigger=POP_BREAKOUT|rank={rank}|qty={quantity}"
                f"|buyStop={entry_price:.4f}|limit={limit_price:.4f}|stop={stop_price:.4f}|risk={risk_per_share:.4f}"
                f"|popRatio={self._float(candidate.get('long_momentum_v11_pop_transaction_ratio')):.4f}"
                f"|entryRatio={score:.4f}"
            ),
        )

    def _v11_exit_requests(
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
            if str(meta.get("entry_type") or "") != "POP_BREAKOUT":
                continue
            request = self._v11_exit_request(context.timestamp, symbol, position, bar, meta)
            if request is not None:
                requests.append(request)
        return requests

    def _v11_exit_request(self, timestamp: datetime, symbol: str, position, bar: dict, meta: dict) -> OrderRequest | None:
        current_open = self._bar_open(bar)
        last_vwap = self._float(bar.get("last_vwap"))
        if last_vwap <= 0 or current_open <= 0:
            return None
        current_distance = (current_open / last_vwap) - 1.0
        max_distance = max(self._float(meta.get("max_distance_above_vwap")), current_distance)
        previous_vwap = self._float(meta.get("previous_vwap"))
        vwap_slope = (last_vwap / previous_vwap) - 1.0 if previous_vwap > 0 else 0.0
        down_count = int(self._float(meta.get("vwap_slope_down_count")))
        down_count = down_count + 1 if previous_vwap > 0 and vwap_slope <= 0 else 0
        trail_stop = last_vwap * (1.0 - max(0.0, self.config.vwap_trail_offset_pct) / 100.0)
        if 0 < trail_stop < current_open:
            position.stop_price = max(position.stop_price, trail_stop)
            meta["initial_stop"] = position.stop_price
        meta["current_distance_above_vwap"] = current_distance
        meta["max_distance_above_vwap"] = max_distance
        meta["previous_vwap"] = last_vwap
        meta["vwap_slope"] = vwap_slope
        meta["vwap_slope_down_count"] = down_count

        if position.stop_price > 0:
            hard_stop = OrderRequest(
                symbol=symbol,
                side="SELL",
                quantity=position.quantity,
                order_type="STOP",
                reason="VWAP_TRAIL_STOP",
                stop_price=position.stop_price,
                tag=self._v11_exit_tag("VWAP_TRAIL_STOP", position, bar, meta),
                allow_same_bar_fill=True,
                expire_on_bar_close=True,
            )
            low = self._float(bar.get("low"))
            if low > 0 and low <= position.stop_price:
                return hard_stop

        if down_count >= max(1, int(self.config.vwap_slope_down_bars)):
            return self._v11_limit_exit(symbol, position, bar, meta, "VWAP_SLOPE_DOWN")

        min_distance = max(0.0, self.config.min_vwap_distance_for_giveback_pct)
        giveback_pct = min(max(0.0, self.config.vwap_distance_giveback_pct), 1.0)
        giveback_threshold = max_distance * (1.0 - giveback_pct)
        if max_distance >= min_distance and current_distance <= giveback_threshold:
            return self._v11_limit_exit(symbol, position, bar, meta, "VWAP_DISTANCE_GIVEBACK")
        return None

    def _v11_limit_exit(self, symbol: str, position, bar: dict, meta: dict, reason: str) -> OrderRequest:
        limit_price = self._liquid_limit_price("SELL", bar)
        return OrderRequest(
            symbol=symbol,
            side="SELL",
            quantity=position.quantity,
            order_type="LIMIT",
            reason=reason,
            limit_price=limit_price,
            allow_same_bar_fill=True,
            tag=self._v11_exit_tag(reason, position, bar, meta) + f"|limit={limit_price:.4f}",
        )

    def _v11_exit_tag(self, reason: str, position, bar: dict, meta: dict) -> str:
        return (
            self._exit_tag(reason, position, bar, meta)
            + f"|currentDistanceAboveVwap={self._float(meta.get('current_distance_above_vwap')):.4f}"
            + f"|maxDistanceAboveVwap={self._float(meta.get('max_distance_above_vwap')):.4f}"
            + f"|vwapSlope={self._float(meta.get('vwap_slope')):.6f}"
            + f"|vwapSlopeDownCount={int(self._float(meta.get('vwap_slope_down_count')))}"
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
        self.live_rankings.extend(captured)
        self.scanner_snapshots.append(
            {
                "timestamp": context.timestamp,
                "session_date": self.session_date.isoformat() if self.session_date else "",
                "candidate_count": len(candidates),
                "pop_breakout_count": len(candidates),
                "scanned_count": len(rows),
                "watchlist_count": len(self.pop_watchlist),
            }
        )
        if self.observability:
            self.observability.scanner(timestamp=context.timestamp, rows=rows[: self.config.watchlist_snapshot_limit], score_key="scanner_score", stage="long_momentum_v11_scanner")
            self.observability.state(
                timestamp=context.timestamp,
                scope="strategy",
                state={
                    "scanned_count": len(rows),
                    "entry_open_count": len(candidates),
                    "watchlist_count": len(self.pop_watchlist),
                    "open_positions": len(portfolio.positions),
                    "pending_orders": len([order for order in pending_orders if order.status == "OPEN"]),
                },
            )

    def _record_pop_watchlist_snapshot(self, context: BarContext, portfolio: Portfolio) -> None:
        rows: list[dict] = []
        for watch in self.pop_watchlist.values():
            row = self.states.get(watch.ticker).row if watch.ticker in self.states else {}
            position = portfolio.positions.get(watch.ticker)
            meta = self.position_meta.get(watch.ticker, {})
            rows.append(
                {
                    "timestamp": context.timestamp,
                    "session_date": self.session_date.isoformat() if self.session_date else "",
                    "ticker": watch.ticker,
                    "watchlist_added_timestamp": watch.added_timestamp,
                    "watchlist_state": "held" if position is not None else watch.last_state,
                    "pop_high": watch.pop_high,
                    "pop_close": watch.pop_close,
                    "pop_vwap": watch.pop_vwap,
                    "pop_transactions": watch.pop_transactions,
                    "prior_pop_3_avg_transactions": watch.prior_pop_3_avg_transactions,
                    "pop_transaction_ratio": watch.pop_transaction_ratio,
                    "last_entry_transaction_ratio": watch.last_entry_transaction_ratio,
                    "bars_since_pop": watch.bars_since_pop,
                    "entry_submitted": watch.entry_submitted,
                    "entry_filled": watch.entry_filled,
                    "last_close": row.get("last_close"),
                    "last_5m_return": row.get("last_5m_return"),
                    "last_volume": row.get("last_volume"),
                    "last_transactions": row.get("last_transactions"),
                    "last_transactions_avg_prior_3": row.get("last_transactions_avg_prior_3"),
                    "last_vwap": row.get("last_vwap"),
                    "current_open": row.get("current_open"),
                    "current_distance_above_vwap": meta.get("current_distance_above_vwap"),
                    "max_distance_above_vwap": meta.get("max_distance_above_vwap"),
                    "vwap_slope": meta.get("vwap_slope"),
                    "vwap_slope_down_count": meta.get("vwap_slope_down_count"),
                }
            )
        rows.sort(key=lambda item: self._float(item.get("pop_transaction_ratio")), reverse=True)
        self.pop_watchlist_snapshots.extend(rows[: max(1, int(self.config.watchlist_snapshot_limit))])

    def _trace_pop_watch_add(self, timestamp: datetime, row: dict[str, Any], watch: PopWatch) -> None:
        if not self.observability:
            return
        self.observability.trace(
            timestamp=timestamp,
            ticker=watch.ticker,
            stage="watchlist",
            event_type="v11_pop_watch_add",
            decision="add_to_pop_watchlist",
            reason_code="PRICE_POP_TRANSACTION_SHOCK",
            reason="Ticker passed v11 price-pop and transaction-shock gates and is waiting for a pop-high buy stop.",
            values={
                "last_close": row.get("last_close"),
                "last_5m_return": row.get("last_5m_return"),
                "last_volume": row.get("last_volume"),
                "pop_high": watch.pop_high,
                "pop_vwap": watch.pop_vwap,
                "pop_transactions": watch.pop_transactions,
                "prior_pop_3_avg_transactions": watch.prior_pop_3_avg_transactions,
                "pop_transaction_ratio": watch.pop_transaction_ratio,
                "min_pop_transaction_ratio": self.config.min_pop_transaction_ratio,
            },
        )

    def _trace_entry(self, timestamp: datetime, candidate: dict, quantity: int, entry_price: float, stop_price: float) -> None:
        self.signal_events.append(
            {
                "timestamp": timestamp,
                "ticker": candidate["ticker"],
                "event": "ENTRY_INTENT",
                "strategy_version": "v11",
                "entry_trigger": "POP_BREAKOUT",
                "rank": candidate.get("entry_rank") or candidate.get("rank"),
                "quantity": quantity,
                "entry": entry_price,
                "stop": stop_price,
                "pop_transaction_ratio": candidate.get("long_momentum_v11_pop_transaction_ratio"),
                "entry_transaction_ratio": candidate.get("long_momentum_v11_entry_transaction_ratio"),
                "pop_high": candidate.get("long_momentum_v11_pop_high"),
                "pop_vwap": candidate.get("long_momentum_v11_pop_vwap"),
            }
        )
        if self.observability:
            self.observability.trace(
                timestamp=timestamp,
                ticker=str(candidate["ticker"]),
                stage="entry",
                event_type="entry_intent",
                decision="submit_buy_stop",
                reason_code="POP_BREAKOUT_BUY_STOP",
                reason="V11 entry submits a buy stop above the stored pop high after pop and entry transaction-ratio gates pass.",
                values={
                    "entry_type": "POP_BREAKOUT",
                    "quantity": quantity,
                    "buy_stop": entry_price,
                    "stop_price": stop_price,
                    "pop_transaction_ratio": candidate.get("long_momentum_v11_pop_transaction_ratio"),
                    "entry_transaction_ratio": candidate.get("long_momentum_v11_entry_transaction_ratio"),
                    "pop_high": candidate.get("long_momentum_v11_pop_high"),
                    "pop_vwap": candidate.get("long_momentum_v11_pop_vwap"),
                },
                force=self._force_trade_trace(),
            )

    def _v11_reject_reason(
        self,
        *,
        price_ok: bool,
        watch_active: bool,
        not_expired: bool,
        no_symbol_position: bool,
        entry_time_ok: bool,
        entry_liquidity_ok: bool,
        above_pop_vwap: bool,
        not_too_extended: bool,
        risk_ok: bool,
    ) -> str:
        if not price_ok:
            return "price_eligibility"
        if not watch_active:
            return "not_in_pop_watchlist"
        if not not_expired:
            return "pop_entry_expired"
        if not no_symbol_position:
            return "already_held_or_pending"
        if not entry_time_ok:
            return "entry_time"
        if not entry_liquidity_ok:
            return "entry_transaction_ratio"
        if not above_pop_vwap:
            return "below_pop_vwap"
        if not not_too_extended:
            return "entry_too_extended"
        if not risk_ok:
            return "invalid_vwap_stop"
        return "filtered"


__all__ = ["LongMomentumV11Strategy"]
