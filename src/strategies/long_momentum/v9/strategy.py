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
    "last_transactions",
    "last_transactions_vs_prior_3",
    "last_vwap",
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
    first_entry_submitted: bool = False
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
        requests.extend(self._exit_requests(context, portfolio, active_pending_orders))

        rows = self._scanner_rows(context, portfolio, active_pending_orders)
        first_entry_candidates = [row for row in rows if row.get("long_momentum_v9_first_entry_open")]
        reentry_candidates = [row for row in rows if row.get("long_momentum_v9_reentry_open")]
        self._record_scanner(context, rows, first_entry_candidates + reentry_candidates, portfolio, active_pending_orders)

        blocked_symbols = {
            order.symbol for order in active_pending_orders if order.side == "BUY"
        } | {
            request.symbol for request in requests if request.side == "BUY"
        } | set(portfolio.positions)
        current_bar_sell_symbols = {request.symbol for request in requests if request.side == "SELL" and request.order_type != "STOP"}

        available_cash = self._available_cash_after_submitted_requests(
            portfolio=portfolio,
            requests=requests,
            context=context,
        )

        first_entry_candidates = [
            row for row in first_entry_candidates
            if str(row.get("ticker") or "") not in blocked_symbols
        ][: max(0, int(self.config.max_first_entry_candidates_per_bar))]

        if first_entry_candidates and self._cannot_buy_group(first_entry_candidates, available_cash):
            rotation_requests = self._rotation_exit_requests(context, portfolio, current_bar_sell_symbols)
            if rotation_requests:
                rotation_symbols = {request.symbol for request in rotation_requests}
                requests = [
                    request
                    for request in requests
                    if not (request.symbol in rotation_symbols and request.side == "SELL" and request.order_type == "STOP")
                ]
                requests.extend(rotation_requests)
                current_bar_sell_symbols |= {request.symbol for request in rotation_requests}
                available_cash = self._available_cash_after_submitted_requests(
                    portfolio=portfolio,
                    requests=requests,
                    context=context,
                )

        if first_entry_candidates:
            submitted = self._submit_entry_group(
                candidates=first_entry_candidates,
                context=context,
                available_cash=available_cash,
                entry_type="FIRST_ENTRY",
            )
            requests.extend(submitted)
            self._record_watchlist_snapshot(context, portfolio)
            return requests

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

    def _validate_provider_columns(self, frame: pl.DataFrame) -> None:
        missing = [column for column in REQUIRED_V9_COLUMNS if column not in frame.columns]
        if missing:
            date_text = self.session_date.isoformat() if self.session_date else "unknown session"
            raise ValueError(
                f"Long Momentum v9 requires provider-built strategy-time core and volume/liquidity features for {date_text}; "
                f"missing columns: {', '.join(missing)}. Rebuild market data with current core and volume_liquidity features."
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
                tag = str(fill.get("tag") or "")
                watch.first_entry_submitted = True
                watch.last_entry_type = "WATCHLIST_REENTRY" if "WATCHLIST_REENTRY" in tag else "FIRST_ENTRY"
                watch.last_state = "in_position"
            elif side == "SELL":
                watch.last_exit_timestamp = context.timestamp
                watch.last_state = "watching_after_exit"

    def _update_momentum_watch(self, timestamp: datetime, row: dict[str, Any]) -> None:
        ticker = str(row.get("ticker") or "").upper()
        if not ticker:
            return
        last_close = self._float(row.get("last_close"))
        last_5m_return = self._float(row.get("last_5m_return"))
        transactions = self._float(row.get("last_transactions"))
        price_eligible = self.config.min_price <= last_close <= self.config.max_price
        return_ok = last_5m_return >= self.config.min_last_5m_return
        transactions_ok = transactions >= self.config.min_first_entry_transactions
        watch = self.momentum_watchlist.get(ticker)
        if watch is None and price_eligible and return_ok and transactions_ok:
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
                self._float(item.get("last_transactions_vs_prior_3")),
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
        transactions = self._float(row.get("last_transactions"))
        transactions_vs_prior_3 = self._float(row.get("last_transactions_vs_prior_3"))
        price_eligible = self.config.min_price <= last_close <= self.config.max_price
        return_ok = last_5m_return >= self.config.min_last_5m_return
        first_transactions_ok = transactions >= self.config.min_first_entry_transactions
        first_transactions_vs_prior_3_ok = transactions_vs_prior_3 >= self.config.min_first_entry_transactions_vs_prior_3
        entry_time_ok = self.config.trading_start_minute <= int(self._float(row.get("minute_of_day"))) < self.config.trading_end_minute
        pending_symbol_order = ticker in pending_symbols
        no_symbol_position = ticker not in portfolio.positions and not pending_symbol_order
        first_entry_available = bool(watch and not watch.first_entry_submitted)
        first_entry_open = (
            price_eligible
            and bool(watch)
            and first_entry_available
            and no_symbol_position
            and entry_time_ok
            and return_ok
            and first_transactions_ok
            and first_transactions_vs_prior_3_ok
        )
        max_vwap = watch.max_vwap if watch else 0.0
        reentry_price_reclaim = max_vwap > 0 and last_close > max_vwap
        reentry_close_minus_max_vwap = last_close - max_vwap if max_vwap > 0 else None
        tema_open = self._tema_open(row)
        reentry_open = (
            price_eligible
            and bool(watch)
            and bool(watch.first_entry_submitted if watch else False)
            and no_symbol_position
            and entry_time_ok
            and reentry_price_reclaim
            and tema_open
        )
        return {
            "long_momentum_v9_price_eligible": price_eligible,
            "long_momentum_v9_watchlist_add_open": price_eligible and return_ok and first_transactions_ok,
            "long_momentum_v9_watchlist_active": watch is not None,
            "long_momentum_v9_watchlist_added_timestamp": watch.added_timestamp.isoformat() if watch else "",
            "long_momentum_v9_watchlist_added_last_close": watch.added_last_close if watch else None,
            "long_momentum_v9_watchlist_added_last_5m_return": watch.added_last_5m_return if watch else None,
            "long_momentum_v9_watchlist_first_entry_submitted": bool(watch and watch.first_entry_submitted),
            "long_momentum_v9_watchlist_last_entry_type": watch.last_entry_type if watch else "",
            "long_momentum_v9_watchlist_last_state": watch.last_state if watch else "",
            "long_momentum_v9_watchlist_max_vwap": max_vwap,
            "long_momentum_v9_watchlist_avg_transactions": watch.avg_transactions_since_watchlist if watch else 0.0,
            "long_momentum_v9_last_5m_return": last_5m_return,
            "long_momentum_v9_return_ok": return_ok,
            "long_momentum_v9_first_entry_transactions_ok": first_transactions_ok,
            "long_momentum_v9_first_entry_transactions_vs_prior_3_ok": first_transactions_vs_prior_3_ok,
            "long_momentum_v9_entry_time_ok": entry_time_ok,
            "long_momentum_v9_pending_symbol_order": pending_symbol_order,
            "long_momentum_v9_no_symbol_position": no_symbol_position,
            "long_momentum_v9_first_entry_open": first_entry_open,
            "long_momentum_v9_close_minus_watchlist_max_vwap": reentry_close_minus_max_vwap,
            "long_momentum_v9_reentry_price_reclaim": reentry_price_reclaim,
            "long_momentum_v9_reentry_tema_open": tema_open,
            "long_momentum_v9_reentry_open": reentry_open,
            "long_momentum_v9_entry_priority": 2 if first_entry_open else 1 if reentry_open else 0,
            "long_momentum_v9_entry_open": first_entry_open or reentry_open,
            "entry_trigger": "FIRST_ENTRY" if first_entry_open else "WATCHLIST_REENTRY" if reentry_open else "",
            "long_momentum_v9_reject_reason": self._v9_reject_reason(
                price_eligible=price_eligible,
                watch_active=watch is not None,
                first_entry_available=first_entry_available,
                entry_time_ok=entry_time_ok,
                return_ok=return_ok,
                first_transactions_ok=first_transactions_ok,
                first_transactions_vs_prior_3_ok=first_transactions_vs_prior_3_ok,
                reentry_price_reclaim=reentry_price_reclaim,
                tema_open=tema_open,
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
            if double_bvd_score > self.config.double_bvd_exit_score:
                self._trace_exit(context.timestamp, symbol, "DOUBLE_BVD", position, bar, meta)
                requests.append(
                    OrderRequest(
                        symbol=symbol,
                        side="SELL",
                        quantity=position.quantity,
                        order_type="MARKET",
                        reason="DOUBLE_BVD",
                        tag=self._exit_tag("DOUBLE_BVD", position, bar, meta) + f"|2xBVD={double_bvd_score:.2f}",
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
        entry_price = self._float(candidate.get("current_open"))
        stop_price = self._entry_stop_for_type(candidate, entry_price, entry_type)
        risk_per_share = entry_price - stop_price
        if entry_price <= 0 or stop_price <= 0 or risk_per_share <= 0:
            self._reject(context.timestamp, symbol, "invalid_entry_risk", candidate)
            return None
        max_risk_cash = available_cash * max(0.0, self.config.max_risk_fraction_of_cash)
        risk_quantity = int(max_risk_cash / risk_per_share) if risk_per_share > 0 else 0
        cash_quantity = self._cash_quantity(entry_price, available_cash)
        quantity = min(risk_quantity, cash_quantity)
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
        }
        watch = self.momentum_watchlist.get(symbol)
        if watch is not None:
            watch.first_entry_submitted = True
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
                f"|entry={entry_price:.2f}|stop={stop_price:.2f}|risk={risk_per_share:.4f}"
                f"|last_5m_return={self._float(candidate.get('long_momentum_v9_last_5m_return')):.4f}"
                f"|transactions={self._float(candidate.get('last_transactions')):.0f}"
                f"|tx_vs_prior_3={self._float(candidate.get('last_transactions_vs_prior_3')):.2f}"
            ),
        )

    def _entry_stop_for_type(self, candidate: dict, entry_price: float, entry_type: str) -> float:
        if entry_type == "WATCHLIST_REENTRY":
            vwap = self._float(candidate.get("last_vwap"))
            stop = vwap * (1.0 - max(0.0, self.config.vwap_stop_buffer_pct)) if vwap > 0 else 0.0
            return stop if 0 < stop < entry_price else 0.0
        stop = self._float(candidate.get("last_open"))
        return stop if 0 < stop < entry_price else 0.0

    def _rotation_exit_requests(
        self,
        context: BarContext,
        portfolio: Portfolio,
        current_bar_sell_symbols: set[str],
    ) -> list[OrderRequest]:
        requests: list[OrderRequest] = []
        for symbol, position in list(portfolio.positions.items()):
            if symbol in current_bar_sell_symbols:
                continue
            bar = context.updates_by_symbol.get(symbol)
            if bar is None:
                continue
            meta = self._position_meta(symbol, position)
            self._trace_exit(context.timestamp, symbol, "ROTATE_TO_FIRST_ENTRY", position, bar, meta)
            requests.append(
                OrderRequest(
                    symbol=symbol,
                    side="SELL",
                    quantity=position.quantity,
                    order_type="MARKET",
                    reason="ROTATE_TO_FIRST_ENTRY",
                    tag=self._exit_tag("ROTATE_TO_FIRST_ENTRY", position, bar, meta),
                )
            )
        return requests

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

    def _cannot_buy_group(self, candidates: list[dict], available_cash: float) -> bool:
        if not candidates:
            return False
        if available_cash <= 0:
            return True
        cash_slice = available_cash / len(candidates)
        return any(self._float(candidate.get("current_open")) > cash_slice for candidate in candidates)

    def _trail_reentry_stop(self, position, bar: dict, meta: dict) -> None:
        vwap = self._float(bar.get("last_vwap"))
        if vwap <= 0:
            return
        next_stop = vwap * (1.0 - max(0.0, self.config.vwap_stop_buffer_pct))
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
            rows.append(
                {
                    "timestamp": context.timestamp,
                    "session_date": self.session_date.isoformat() if self.session_date else "",
                    "ticker": watch.ticker,
                    "watchlist_added_timestamp": watch.added_timestamp,
                    "watchlist_state": "held" if watch.ticker in portfolio.positions else watch.last_state,
                    "watchlist_first_entry_submitted": watch.first_entry_submitted,
                    "watchlist_last_entry_type": watch.last_entry_type,
                    "watchlist_added_last_close": watch.added_last_close,
                    "watchlist_added_last_5m_return": watch.added_last_5m_return,
                    "watchlist_max_vwap": watch.max_vwap,
                    "watchlist_avg_transactions": watch.avg_transactions_since_watchlist,
                    "last_close": row.get("last_close"),
                    "last_5m_return": row.get("last_5m_return"),
                    "last_transactions": row.get("last_transactions"),
                    "last_transactions_vs_prior_3": row.get("last_transactions_vs_prior_3"),
                    "last_vwap": row.get("last_vwap"),
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
                    "first_entry_submitted_count": len([watch for watch in self.momentum_watchlist.values() if watch.first_entry_submitted]),
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
                "first_entry_count": len([row for row in candidates if row.get("long_momentum_v9_first_entry_open")]),
                "reentry_count": len([row for row in candidates if row.get("long_momentum_v9_reentry_open")]),
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
                "first_entry_count": len([row for row in candidates if row.get("long_momentum_v9_first_entry_open")]),
                "reentry_count": len([row for row in candidates if row.get("long_momentum_v9_reentry_open")]),
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
            reason_code="PRICE_RETURN_TRANSACTIONS",
            reason="Ticker entered the day momentum watchlist from price, completed-bar 5m return, and transactions.",
            values={
                "last_close": row.get("last_close"),
                "last_5m_return": watch.added_last_5m_return,
                "last_transactions": row.get("last_transactions"),
                "min_last_5m_return": self.config.min_last_5m_return,
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
            reason="First Entry has priority over watchlist reentry; eligible candidates share available cash.",
            values={
                "entry_type": entry_type,
                "quantity": quantity,
                "current_open": entry_price,
                "stop": stop_price,
                "risk_per_share": entry_price - stop_price,
                "last_5m_return": candidate.get("long_momentum_v9_last_5m_return"),
                "last_transactions": candidate.get("last_transactions"),
                "last_transactions_vs_prior_3": candidate.get("last_transactions_vs_prior_3"),
                "watchlist_max_vwap": candidate.get("long_momentum_v9_watchlist_max_vwap"),
            },
            state={
                "watchlist_count": len(self.momentum_watchlist),
                "first_entry_submitted": candidate.get("long_momentum_v9_watchlist_first_entry_submitted"),
            },
            force=self._force_trade_trace(),
        )

    def _v9_entry_state(self, row: dict[str, Any]) -> str:
        if row.get("long_momentum_v9_first_entry_open"):
            return "FIRST_ENTRY"
        if row.get("long_momentum_v9_reentry_open"):
            return "WATCHLIST_REENTRY"
        return str(row.get("long_momentum_v9_reject_reason") or "filtered")

    def _v9_reject_reason(
        self,
        *,
        price_eligible: bool,
        watch_active: bool,
        first_entry_available: bool,
        entry_time_ok: bool,
        return_ok: bool,
        first_transactions_ok: bool,
        first_transactions_vs_prior_3_ok: bool,
        reentry_price_reclaim: bool,
        tema_open: bool,
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
        if first_entry_available:
            if not return_ok:
                return "first_entry_5m_return"
            if not first_transactions_ok:
                return "first_entry_transactions"
            if not first_transactions_vs_prior_3_ok:
                return "first_entry_transactions_vs_prior_3"
            return "first_entry_filtered"
        if not reentry_price_reclaim:
            return "reentry_below_max_vwap"
        if not tema_open:
            return "reentry_tema_closed"
        return "filtered"

    def _tema_open(self, row: dict[str, Any]) -> bool:
        if row.get("last_tema_open") is not None:
            return self._bool(row.get("last_tema_open"))
        return self._float(row.get("last_tema9")) > self._float(row.get("last_tema20"))

    def _bool(self, value: Any) -> bool:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y"}
        return bool(value)


__all__ = ["LongMomentumV9Strategy"]
