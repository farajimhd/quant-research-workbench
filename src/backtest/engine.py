from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime
from math import ceil
from typing import Callable, Protocol

import polars as pl

from src.backtest.artifact_writer import ArtifactWriter
from src.backtest.cancel import BacktestCancelled
from src.backtest.config import BacktestConfig
from src.backtest.data.minute_bars import DayFrames, available_session_dates, load_day_frames, timeframe_minutes
from src.backtest.equity_candles import default_portfolio_candle_timeframe
from src.backtest.exclusions import load_symbol_exclusions, normalize_symbol
from src.backtest.fees import FeeBreakdown, fee_model_for_name
from src.backtest.fills import BarFillModel
from src.backtest.metrics import compute_summary
from src.backtest.models import BarContext, DataRequirements, Fill, Order, OrderRequest
from src.backtest.observability import ObservabilityRecorder
from src.backtest.portfolio import Portfolio
from src.backtest.results import base_metadata, create_run_dir


class Strategy(Protocol):
    name: str

    def data_requirements(self) -> DataRequirements:
        ...

    def prepare_day(self, frames: DayFrames, portfolio: Portfolio) -> pl.DataFrame:
        ...

    def on_bar(self, context: BarContext, portfolio: Portfolio, pending_orders: list[Order]) -> list[OrderRequest]:
        ...

    def on_day_end(self, timestamp: datetime, portfolio: Portfolio) -> list[OrderRequest]:
        ...

    def artifacts(self) -> dict[str, list[dict]]:
        ...

    def entry_metadata(self, order: Order) -> dict:
        ...

    def chart_presentation(self) -> dict:
        ...


class BacktestEngine:
    def __init__(self, config: BacktestConfig, strategy: Strategy):
        self.config = config
        self.strategy = strategy
        self.portfolio = Portfolio(config.initial_cash)
        self.next_order_id = 1
        self.next_fill_id = 1
        self.pending_orders: list[Order] = []
        self.orders: list[dict] = []
        self.fills: list[dict] = []
        self.trades: list[dict] = []
        self.portfolio_rows: list[dict] = []
        self.position_rows: list[dict] = []
        self.engine_rejection_events: list[dict] = []
        self.daily_rows: list[dict] = []
        self.logs: list[str] = []
        self.symbol_bar_rows: list[dict] = []
        self.symbol_bar_5m_rows: list[dict] = []
        self._strategy_recent_orders: list[dict] = []
        self._strategy_recent_fills: list[dict] = []
        self.fill_model = BarFillModel()
        self.fee_model = fee_model_for_name(config.fee_model, tax_rate=config.fee_tax_rate)
        self.symbol_exclusions = load_symbol_exclusions(config.excluded_symbols_file)
        self.observability = ObservabilityRecorder(config)
        self._attach_observability()

    def run(self, progress_callback=None, cancel_check: Callable[[], None] | None = None) -> dict:
        run_dir = create_run_dir(self.config)
        metadata = base_metadata(self.config, run_dir, "running")
        metadata["strategy_chart_presentation"] = self._strategy_chart_presentation()
        metadata["symbol_exclusions"] = self.symbol_exclusions.metadata()

        with ArtifactWriter() as artifact_writer:
            self._write_metadata(run_dir, metadata, artifact_writer)
            artifact_writer.write_json(run_dir / "config.json", self.config.to_dict())

            try:
                self._check_cancelled(cancel_check)
                requirements = self.strategy.data_requirements()
                sessions = available_session_dates(self.config, requirements)
                if not sessions:
                    raise ValueError(
                        f"No market sessions found for requested range "
                        f"{self.config.start_date.isoformat()}..{self.config.end_date.isoformat()}"
                    )
                metadata.update(
                    {
                        "requested_start_date": self.config.start_date.isoformat(),
                        "requested_end_date": self.config.end_date.isoformat(),
                        "scheduled_sessions": [session.isoformat() for session in sessions],
                        "total_sessions": len(sessions),
                        "progress_kind": "bars",
                        "progress_unit": requirements.event_timeframe,
                        "processed_event_bars": 0,
                        "total_event_bars": self._expected_event_bar_count(sessions, requirements),
                    }
                )
                metadata["summary"] = self._summary(run_dir)
                self._write_metadata(run_dir, metadata, artifact_writer)
                self._emit_progress(
                    progress_callback,
                    {
                        "event": "run_progress_initialized",
                        "phase": "backtest",
                        "status": "running",
                        "run_dir": str(run_dir),
                        "progress_kind": "bars",
                        "progress_unit": requirements.event_timeframe,
                        "processed_event_bars": 0,
                        "total_event_bars": metadata["total_event_bars"],
                        "completed_sessions": 0,
                        "total_sessions": len(sessions),
                        "summary": metadata["summary"],
                    },
                )
                logging.info("Running %s sessions", len(sessions))

                processed_event_bars = 0
                total_event_bars = int(metadata["total_event_bars"])
                live_chart_bar_interval = self._live_chart_bar_interval(requirements)
                live_trade_count = 0
                for index, session_date in enumerate(sessions, start=1):
                    self._check_cancelled(cancel_check)
                    self.observability.start_session(session_date, index)
                    day_start_equity = self.portfolio.total_equity()
                    frames = load_day_frames(self.config, session_date, requirements)
                    frames = self._apply_symbol_exclusions(frames)
                    execution_frame = frames.event_frame
                    decision_frames = DayFrames(
                        session_date=frames.session_date,
                        event_frame=self._strategy_decision_frame(execution_frame),
                        daily_context=frames.daily_context,
                        context_frames=frames.context_frames,
                    )
                    event_frame = self.strategy.prepare_day(decision_frames, self.portfolio)
                    event_frame = self._filter_excluded_frame(event_frame)
                    execution_frame = self._execution_frame_for_decisions(execution_frame, event_frame)
                    self._check_cancelled(cancel_check)
                    event_frame = event_frame.sort(["bar_time_market", "ticker"])
                    execution_frame = execution_frame.sort(["bar_time_market", "ticker"])
                    session_total_bars = self._event_bar_count(event_frame)
                    remaining_sessions = len(sessions) - index
                    total_event_bars = max(
                        total_event_bars,
                        processed_event_bars + session_total_bars + self._expected_bars_per_session(requirements) * remaining_sessions,
                    )

                    if self.config.save_symbol_bars:
                        self.symbol_bar_rows.extend(self._symbol_bar_rows(event_frame, session_date))
                        self._record_context_symbol_bars(frames.context_frames, session_date)

                    last_timestamp = None
                    latest_bars: dict[str, dict] = {}
                    latest_execution_bars: dict[str, dict] = {}
                    session_processed_bars = 0
                    self._emit_progress(
                        progress_callback,
                        {
                            "event": "session_started",
                            "phase": "backtest",
                            "status": "running",
                            "run_dir": str(run_dir),
                            "progress_kind": "bars",
                            "progress_unit": requirements.event_timeframe,
                            "processed_event_bars": processed_event_bars,
                            "total_event_bars": total_event_bars,
                            "completed_sessions": index - 1,
                            "total_sessions": len(sessions),
                            "session_date": session_date.isoformat(),
                            "current_session": session_date.isoformat(),
                            "current_session_processed_bars": 0,
                            "current_session_total_bars": session_total_bars,
                        },
                    )

                    execution_by_timestamp = {
                        timestamp: frame
                        for timestamp, frame in self._timestamp_slices(execution_frame, requirements.event_timeframe)
                    }
                    for timestamp, updates in self._timestamp_slices(event_frame, requirements.event_timeframe):
                        self._check_cancelled(cancel_check)
                        last_timestamp = timestamp
                        update_rows = updates.to_dicts()
                        fresh_bars = {row["ticker"]: row for row in update_rows}
                        execution_updates = execution_by_timestamp.get(timestamp, pl.DataFrame())
                        execution_rows = execution_updates.to_dicts() if not execution_updates.is_empty() else []
                        execution_fresh_bars = {row["ticker"]: row for row in execution_rows}
                        latest_bars.update(fresh_bars)
                        latest_execution_bars.update(execution_fresh_bars)
                        latest = pl.DataFrame(list(latest_bars.values()), infer_schema_length=None) if latest_bars else pl.DataFrame()

                        pending_order_ids_at_open = {order.order_id for order in self.pending_orders}
                        recent_orders = self._strategy_recent_orders
                        recent_fills = self._strategy_recent_fills
                        self._strategy_recent_orders = []
                        self._strategy_recent_fills = []
                        requests = self.strategy.on_bar(
                            BarContext(
                                timestamp=timestamp,
                                updates=updates,
                                latest=latest,
                                updates_by_symbol=fresh_bars,
                                latest_by_symbol=latest_bars,
                                observability=self.observability,
                                recent_orders=recent_orders,
                                recent_fills=recent_fills,
                            ),
                            self.portfolio,
                            list(self.pending_orders),
                        )
                        self._handle_requests(timestamp, requests, execution_fresh_bars)
                        self._fill_pending_orders(timestamp, execution_fresh_bars, eligible_order_ids=pending_order_ids_at_open)
                        live_trade_count = self._write_live_trades_if_needed(run_dir, artifact_writer, live_trade_count)
                        self.portfolio.update_peaks(execution_fresh_bars)
                        self._record_portfolio(timestamp, latest_execution_bars)
                        processed_event_bars += 1
                        session_processed_bars += 1
                        total_event_bars = max(total_event_bars, processed_event_bars)
                        if self._should_write_live_chart(session_processed_bars, session_total_bars, live_chart_bar_interval):
                            self._write_chart_artifacts(run_dir, artifact_writer)
                        if self._should_emit_bar_progress(session_processed_bars, session_total_bars):
                            summary = self._summary(run_dir)
                            self._emit_progress(
                                progress_callback,
                                {
                                    "event": "bar_progress",
                                    "phase": "backtest",
                                    "status": "running",
                                    "run_dir": str(run_dir),
                                    "progress_kind": "bars",
                                    "progress_unit": requirements.event_timeframe,
                                    "processed_event_bars": processed_event_bars,
                                    "total_event_bars": total_event_bars,
                                    "completed_sessions": index - 1,
                                    "total_sessions": len(sessions),
                                    "current_session": session_date.isoformat(),
                                    "current_bar_time": timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp),
                                    "current_session_processed_bars": session_processed_bars,
                                    "current_session_total_bars": session_total_bars,
                                    "summary": summary,
                                },
                            )

                    if last_timestamp is not None:
                        self._handle_requests(
                            last_timestamp,
                            self.strategy.on_day_end(last_timestamp, self.portfolio),
                            latest_execution_bars,
                        )
                        self._fill_market_exits(last_timestamp, latest_execution_bars)
                        live_trade_count = self._write_live_trades_if_needed(run_dir, artifact_writer, live_trade_count)
                        self._record_portfolio(last_timestamp, latest_execution_bars)

                    day_end_equity = self.portfolio.total_equity(latest_execution_bars)
                    self.daily_rows.append(
                        {
                            "session_date": session_date.isoformat(),
                            "start_equity": day_start_equity,
                            "end_equity": day_end_equity,
                            "pnl": day_end_equity - day_start_equity,
                            "return_pct": (day_end_equity / day_start_equity) - 1.0 if day_start_equity else 0.0,
                            "trade_count": len([t for t in self.trades if str(t["exit_time"]).startswith(session_date.isoformat())]),
                            "candidate_count": self._candidate_count_for_day(session_date),
                            "signal_count": self._signal_count_for_day(session_date),
                            "rejection_count": self._rejection_count_for_day(session_date),
                        }
                    )

                    metadata.update(
                        {
                            "status": "running",
                            "completed_sessions": index,
                            "total_sessions": len(sessions),
                            "processed_event_bars": processed_event_bars,
                            "total_event_bars": total_event_bars,
                            "progress_kind": "bars",
                            "progress_unit": requirements.event_timeframe,
                            "latest_session": session_date.isoformat(),
                            "latest_daily_summary": dict(self.daily_rows[-1]),
                        }
                    )
                    metadata["summary"] = self._summary(run_dir)
                    self._write_metadata(run_dir, metadata, artifact_writer)

                    if progress_callback:
                        self._emit_progress(
                            progress_callback,
                            {
                                "event": "session_complete",
                                "phase": "backtest",
                                "status": "running",
                                "run_dir": str(run_dir),
                                "progress_kind": "bars",
                                "progress_unit": requirements.event_timeframe,
                                "processed_event_bars": processed_event_bars,
                                "total_event_bars": total_event_bars,
                                "completed_sessions": index,
                                "total_sessions": len(sessions),
                                "current_session": session_date.isoformat(),
                                "current_session_processed_bars": session_processed_bars,
                                "current_session_total_bars": session_total_bars,
                                "session_date": session_date.isoformat(),
                                "daily_summary": dict(self.daily_rows[-1]),
                                "summary": metadata["summary"],
                            },
                        )

                summary = self._summary(run_dir)
                metadata.update(
                    {
                        "status": "complete",
                        "completed_at": datetime.now().isoformat(timespec="seconds"),
                        "processed_event_bars": processed_event_bars,
                        "total_event_bars": max(total_event_bars, processed_event_bars),
                        "summary": summary,
                    }
                )
                self._write_artifacts(run_dir, summary, artifact_writer)
                self._write_metadata(run_dir, metadata, artifact_writer)
                artifact_writer.wait()
                return {"run_dir": str(run_dir), "summary": summary}
            except BacktestCancelled as exc:
                summary = self._summary(run_dir)
                metadata.update(
                    {
                        "status": "cancelled",
                        "cancelled_at": datetime.now().isoformat(timespec="seconds"),
                        "error": str(exc),
                        "summary": summary,
                    }
                )
                self._write_artifacts(run_dir, summary, artifact_writer)
                self._write_metadata(run_dir, metadata, artifact_writer)
                artifact_writer.wait()
                raise
            except Exception as exc:
                metadata.update({"status": "error", "error": str(exc), "failed_at": datetime.now().isoformat(timespec="seconds")})
                self._write_metadata(run_dir, metadata, artifact_writer)
                try:
                    artifact_writer.wait()
                except Exception:
                    logging.exception("Failed to flush backtest artifacts after run error")
                raise

    def _timestamp_slices(self, event_frame: pl.DataFrame, event_timeframe: str):
        if event_frame.is_empty():
            return
        for key, frame in event_frame.partition_by("bar_time_market", as_dict=True, maintain_order=True).items():
            bar_start = key[0] if isinstance(key, tuple) else key
            yield bar_start, frame

    def _strategy_decision_frame(self, execution_frame: pl.DataFrame) -> pl.DataFrame:
        if execution_frame.is_empty() or "ticker" not in execution_frame.columns:
            return execution_frame
        current_columns = {
            "ticker",
            "bar_id",
            "session_date",
            "timeframe",
            "bar_time_market",
            "bar_time_utc",
            "minute_of_day",
            "open",
        }
        order_columns = [column for column in ["ticker", "bar_time_market"] if column in execution_frame.columns]
        frame = execution_frame.sort(order_columns) if order_columns else execution_frame
        shifted_exprs = [
            pl.col(column).shift(1).over("ticker").alias(column)
            for column in frame.columns
            if column not in current_columns
        ]
        decision = frame.with_columns(shifted_exprs) if shifted_exprs else frame
        alias_exprs = [pl.col("open").alias("current_open"), pl.col("open").shift(1).over("ticker").alias("last_open")]
        alias_exprs.extend(
            pl.col(column).alias(f"last_{column}")
            for column in frame.columns
            if column not in current_columns and f"last_{column}" not in decision.columns
        )
        decision = decision.with_columns(alias_exprs)
        if {"current_open", "last_open", "last_close"}.issubset(decision.columns):
            decision = decision.with_columns(
                (
                    pl.when(pl.col("last_close") < pl.col("last_open"))
                    .then(pl.col("current_open") > pl.max_horizontal("last_open", "last_close"))
                    .otherwise(True)
                ).alias("current_open_above_last_body_high")
            )
        return decision

    def _execution_frame_for_decisions(self, execution_frame: pl.DataFrame, decision_frame: pl.DataFrame) -> pl.DataFrame:
        if execution_frame.is_empty() or decision_frame.is_empty():
            return execution_frame.head(0)
        key_columns = [column for column in ["ticker", "bar_time_market"] if column in execution_frame.columns and column in decision_frame.columns]
        if not key_columns:
            return execution_frame
        keys = decision_frame.select(key_columns).unique()
        return execution_frame.join(keys, on=key_columns, how="semi")

    def _expected_event_bar_count(self, sessions: list, requirements: DataRequirements) -> int:
        return max(1, len(sessions) * self._expected_bars_per_session(requirements))

    def _expected_bars_per_session(self, requirements: DataRequirements) -> int:
        minutes = max(1, timeframe_minutes(requirements.event_timeframe))
        session_minutes = max(1, self.config.session_end_minute - self.config.session_start_minute)
        return max(1, ceil(session_minutes / minutes))

    def _event_bar_count(self, event_frame: pl.DataFrame) -> int:
        if event_frame.is_empty() or "bar_time_market" not in event_frame.columns:
            return 0
        return int(event_frame.select(pl.col("bar_time_market").n_unique()).item())

    def _should_emit_bar_progress(self, session_processed_bars: int, session_total_bars: int) -> bool:
        if session_processed_bars <= 0:
            return False
        return session_processed_bars == session_total_bars or session_processed_bars % 10 == 0

    def _live_chart_bar_interval(self, requirements: DataRequirements) -> int:
        default_timeframe = default_portfolio_candle_timeframe(self.config.start_date, self.config.end_date)
        chart_minutes = timeframe_minutes(default_timeframe)
        event_minutes = max(1, timeframe_minutes(requirements.event_timeframe))
        return max(1, ceil(chart_minutes / event_minutes))

    def _should_write_live_chart(self, session_processed_bars: int, session_total_bars: int, interval: int) -> bool:
        if session_processed_bars <= 0:
            return False
        return session_processed_bars == session_total_bars or session_processed_bars % interval == 0

    def _write_live_trades_if_needed(self, run_dir, artifact_writer: ArtifactWriter, previous_count: int) -> int:
        current_count = len(self.trades)
        if current_count > previous_count:
            artifact_writer.write_table(run_dir / "trades.parquet", self.trades)
        return current_count

    def _emit_progress(self, progress_callback, payload: dict) -> None:
        if progress_callback:
            progress_callback(payload)

    def _check_cancelled(self, cancel_check: Callable[[], None] | None) -> None:
        if cancel_check:
            cancel_check()

    def _handle_requests(self, timestamp: datetime, requests: list[OrderRequest], bars_by_symbol: dict[str, dict]) -> None:
        for request in requests:
            if request.order_type == "CANCEL":
                self._cancel_pending_order(timestamp, request)
                continue
            if request.quantity <= 0:
                continue
            if self.symbol_exclusions.contains(request.symbol):
                self._reject_excluded_order(timestamp, request)
                continue
            order = Order(
                order_id=self.next_order_id,
                symbol=request.symbol,
                side=request.side,
                quantity=request.quantity,
                order_type=request.order_type,
                reason=request.reason,
                created_at=timestamp,
                stop_price=request.stop_price,
                limit_price=request.limit_price,
                tag=request.tag,
                fill_requires_green_bar=request.fill_requires_green_bar,
                fill_requires_close_through_stop=request.fill_requires_close_through_stop,
                expire_on_bar_close=request.expire_on_bar_close,
            )
            self.next_order_id += 1

            if order.order_type == "MARKET":
                bar = bars_by_symbol.get(order.symbol)
                if bar is None:
                    order.status = "REJECTED_NO_MARKET_DATA"
                    self.orders.append(asdict(order))
                else:
                    self._fill_order(order, timestamp, bar, order.reason)
            else:
                bar = bars_by_symbol.get(order.symbol)
                if request.allow_same_bar_fill and bar is not None and self.fill_model.crossed(order, bar) and self._order_bar_filter_passes(order, bar):
                    self._fill_order(order, self._bar_fill_timestamp(timestamp, bar), bar, order.reason)
                elif request.allow_same_bar_fill and request.expire_on_bar_close:
                    order.status = "EXPIRED"
                    self.orders.append(asdict(order))
                else:
                    self.pending_orders.append(order)
                    self.orders.append(asdict(order))

    def _cancel_pending_order(self, timestamp: datetime, request: OrderRequest) -> None:
        still_open = []
        for order in self.pending_orders:
            if order.symbol == request.symbol and order.side == request.side and order.status == "OPEN":
                order.status = "CANCELED"
                order.filled_at = timestamp
                order.reason = request.reason
                order.tag = request.tag
                self.orders.append(asdict(order))
            else:
                still_open.append(order)
        self.pending_orders = still_open

    def _fill_pending_orders(self, timestamp: datetime, bars_by_symbol: dict[str, dict], eligible_order_ids: set[int] | None = None) -> None:
        starting_pending_ids = {order.order_id for order in self.pending_orders}
        still_open = []
        for order in list(self.pending_orders):
            if eligible_order_ids is not None and order.order_id not in eligible_order_ids:
                still_open.append(order)
                continue
            bar = bars_by_symbol.get(order.symbol)
            if bar is None:
                still_open.append(order)
                continue

            should_fill = self.fill_model.crossed(order, bar) and self._order_bar_filter_passes(order, bar)

            if should_fill:
                self._fill_order(order, self._bar_fill_timestamp(timestamp, bar), bar, order.reason)
            else:
                still_open.append(order)
        new_pending_orders = [order for order in self.pending_orders if order.order_id not in starting_pending_ids]
        self.pending_orders = still_open + new_pending_orders

    def _bar_fill_timestamp(self, timestamp: datetime, _bar: dict) -> datetime:
        return timestamp

    def _order_bar_filter_passes(self, order: Order, bar: dict) -> bool:
        if order.fill_requires_green_bar:
            try:
                if float(bar.get("close")) < float(bar.get("open")):
                    return False
            except (TypeError, ValueError):
                return False
        if order.fill_requires_close_through_stop and order.stop_price is not None:
            try:
                close = float(bar.get("close"))
                stop_price = float(order.stop_price)
            except (TypeError, ValueError):
                return False
            if order.side == "BUY":
                return close >= stop_price
            if order.side == "SELL":
                return close <= stop_price
        return True

    def _reject_excluded_order(self, timestamp: datetime, request: OrderRequest) -> None:
        order = Order(
            order_id=self.next_order_id,
            symbol=normalize_symbol(request.symbol),
            side=request.side,
            quantity=request.quantity,
            order_type=request.order_type,
            reason="EXCLUDED_SYMBOL",
            created_at=timestamp,
            stop_price=request.stop_price,
            limit_price=request.limit_price,
            status="REJECTED_EXCLUDED_SYMBOL",
            tag=request.tag,
            fill_requires_green_bar=request.fill_requires_green_bar,
            fill_requires_close_through_stop=request.fill_requires_close_through_stop,
            expire_on_bar_close=request.expire_on_bar_close,
        )
        self.next_order_id += 1
        self.orders.append(asdict(order))
        self.engine_rejection_events.append(
            {
                "timestamp": timestamp,
                "ticker": order.symbol,
                "event_type": "order_rejected",
                "reject_reason": "excluded_symbol",
                "reason_code": "excluded_symbol",
                "stage": "engine_order_guard",
                "side": order.side,
                "quantity": order.quantity,
                "order_type": order.order_type,
                "tag": order.tag,
            }
        )

    def _record_engine_rejection(self, timestamp: datetime, order: Order, reason: str) -> None:
        self.engine_rejection_events.append(
            {
                "timestamp": timestamp,
                "ticker": order.symbol,
                "event_type": "order_rejected",
                "reject_reason": reason,
                "reason_code": reason,
                "stage": "engine_order_guard",
                "side": order.side,
                "quantity": order.quantity,
                "order_type": order.order_type,
                "tag": order.tag,
            }
        )

    def _fill_market_exits(self, timestamp: datetime, bars_by_symbol: dict[str, dict]) -> None:
        for order in list(self.pending_orders):
            if order.side != "SELL":
                continue
            bar = bars_by_symbol.get(order.symbol)
            if bar is not None:
                self._fill_order(order, timestamp, bar, order.reason)
                self.pending_orders.remove(order)

    def _fill_order(self, order: Order, timestamp: datetime, bar: dict, reason: str) -> None:
        fill_price = self.fill_model.fill_price(order, bar, self.config.slippage_bps)
        liquidity_slippage_bps = self._exit_liquidity_slippage_bps(order, bar)
        if liquidity_slippage_bps > 0:
            fill_price *= 1.0 - (liquidity_slippage_bps / 10_000.0)
        effective_slippage_bps = self.config.slippage_bps + liquidity_slippage_bps

        if order.side == "BUY":
            metadata = self.strategy.entry_metadata(order)
            max_fill_quantity = self._max_fill_quantity(order, bar)
            requested_quantity = order.quantity
            if max_fill_quantity is not None and order.quantity > max_fill_quantity:
                fill_quantity = max(0, max_fill_quantity)
                if fill_quantity <= 0:
                    order.status = "REJECTED_LIQUIDITY"
                    order.filled_at = timestamp
                    order.fill_price = fill_price
                    order.reason = reason
                    self.orders.append(asdict(order))
                    self._record_strategy_order_event(order)
                    self._record_engine_rejection(timestamp, order, "entry_liquidity")
                    return
                order.quantity = fill_quantity
                order.status = "PARTIALLY_FILLED"
                order.tag = self._tag_with_partial_fill(order.tag, requested_quantity, fill_quantity)
            else:
                order.status = "FILLED"
            fee = self.fee_model.estimate(side=order.side, quantity=order.quantity, fill_price=fill_price)
            if not self.portfolio.can_afford(fill_price, order.quantity, fee.total):
                order.status = "REJECTED_CASH"
                order.filled_at = timestamp
                order.fill_price = fill_price
                order.reason = reason
            else:
                order.filled_at = timestamp
                order.fill_price = fill_price
                order.reason = reason
                self._apply_fee_to_order(order, fee)
                self._record_fill(order, timestamp, bar, fill_price, fee, effective_slippage_bps)
                self.portfolio.open_position(
                    order,
                    setup_rank=int(metadata.get("setup_rank", 0)),
                    live_rank=int(metadata.get("live_rank", 0)),
                    setup_score=float(metadata.get("setup_score", 0.0)),
                    live_score=float(metadata.get("live_score", 0.0)),
                    stop_price=self._entry_stop_price(metadata, order, fill_price),
                    fee=fee.total,
                )
        else:
            max_fill_quantity = self._max_fill_quantity(order, bar)
            requested_quantity = order.quantity
            if max_fill_quantity is not None and requested_quantity > max_fill_quantity:
                fill_quantity = max(0, max_fill_quantity)
                if fill_quantity <= 0:
                    order.status = "REJECTED_LIQUIDITY"
                    order.filled_at = timestamp
                    order.fill_price = fill_price
                    order.reason = reason
                    self.orders.append(asdict(order))
                    self._record_strategy_order_event(order)
                    self._record_engine_rejection(timestamp, order, "exit_liquidity")
                    return
                order.quantity = fill_quantity
                order.status = "PARTIALLY_FILLED"
                order.tag = self._tag_with_partial_fill(order.tag, requested_quantity, fill_quantity)
            else:
                order.status = "FILLED"
            fee = self.fee_model.estimate(side=order.side, quantity=order.quantity, fill_price=fill_price)
            if order.symbol not in self.portfolio.positions:
                order.status = "REJECTED_NO_POSITION"
                order.filled_at = timestamp
                order.fill_price = fill_price
                order.reason = reason
                self.orders.append(asdict(order))
                return
            order.filled_at = timestamp
            order.fill_price = fill_price
            order.reason = reason
            self._apply_fee_to_order(order, fee)
            self._record_fill(order, timestamp, bar, fill_price, fee, effective_slippage_bps)
            trade = self.portfolio.close_position(order, fee=fee.total)
            if trade is not None:
                self.trades.append(asdict(trade))

        self.orders.append(asdict(order))
        self._record_strategy_order_event(order)

    def _entry_stop_price(self, metadata: dict, order: Order, fill_price: float) -> float:
        if "stop_offset_dollars" in metadata:
            return max(0.01, fill_price - float(metadata["stop_offset_dollars"]))
        return float(metadata.get("stop_price", order.stop_price or fill_price))

    def _max_fill_quantity(self, order: Order, bar: dict) -> int | None:
        quote_column = "quote_ask_size" if order.side == "BUY" else "quote_bid_size"
        quote_quantity = self._nonnegative_int(bar.get(quote_column))
        if quote_quantity is not None:
            return quote_quantity
        if order.side == "BUY":
            provider_column = "max_entry_qty"
        else:
            provider_column = "max_exit_qty"
        provider_quantity = self._nonnegative_int(bar.get(provider_column))
        if provider_quantity is not None:
            return provider_quantity
        try:
            volume = float(bar.get("volume"))
        except (TypeError, ValueError):
            return None
        if volume <= 0:
            return 0
        try:
            transactions = float(bar.get("transactions"))
        except (TypeError, ValueError):
            transactions = 0.0
        participation_capacity = volume * max(0.0, self.config.max_entry_participation_rate)
        if transactions > 0:
            average_trade_size = volume / transactions
            print_capacity = average_trade_size * max(0.0, self.config.max_entry_trade_multiple)
            capacity = min(participation_capacity, print_capacity)
        else:
            capacity = participation_capacity
        return max(0, int(capacity))

    def _exit_liquidity_slippage_bps(self, order: Order, bar: dict) -> float:
        if order.side != "SELL":
            return 0.0
        if bar.get("quote_bid_size") is not None:
            return 0.0
        max_fill_quantity = self._max_fill_quantity(order, bar)
        if max_fill_quantity is None or max_fill_quantity <= 0 or order.quantity <= max_fill_quantity:
            return 0.0
        excess_multiple = (order.quantity / max_fill_quantity) - 1.0
        return max(0.0, excess_multiple * self.config.exit_liquidity_slippage_bps_per_excess_multiple)

    def _nonnegative_int(self, value) -> int | None:
        try:
            if value is None:
                return None
            number = float(value)
        except (TypeError, ValueError):
            return None
        if number < 0:
            return 0
        return int(number)

    def _tag_with_partial_fill(self, tag: str, requested_quantity: int, fill_quantity: int) -> str:
        suffix = f"PARTIAL|requested={requested_quantity}|filled={fill_quantity}|remaining={requested_quantity - fill_quantity}"
        return f"{tag}|{suffix}" if tag else suffix

    def _apply_fee_to_order(self, order: Order, fee: FeeBreakdown) -> None:
        order.fill_fee = fee.total
        order.commission = fee.commission
        order.regulatory_fee = fee.regulatory_fee
        order.fee_tax = fee.tax
        order.fee_model = fee.model

    def _record_fill(self, order: Order, timestamp: datetime, bar: dict, fill_price: float, fee: FeeBreakdown, slippage_bps: float | None = None) -> None:
        fill = Fill(
            fill_id=self.next_fill_id,
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            fill_price=fill_price,
            filled_at=timestamp,
            order_type=order.order_type,
            reason=order.reason,
            slippage_bps=self.config.slippage_bps if slippage_bps is None else slippage_bps,
            commission=fee.commission,
            regulatory_fee=fee.regulatory_fee,
            fee_tax=fee.tax,
            total_fee=fee.total,
            sec_fee=fee.sec_fee,
            finra_taf=fee.finra_taf,
            finra_cat=fee.finra_cat,
            fee_model=fee.model,
            bar_time_market=bar.get("bar_time_market"),
            bar_open=float(bar["open"]) if bar.get("open") is not None else None,
            bar_high=float(bar["high"]) if bar.get("high") is not None else None,
            bar_low=float(bar["low"]) if bar.get("low") is not None else None,
            bar_close=float(bar["close"]) if bar.get("close") is not None else None,
            tag=order.tag,
        )
        self.next_fill_id += 1
        row = asdict(fill)
        self.fills.append(row)
        self._strategy_recent_fills.append(dict(row))

    def _record_strategy_order_event(self, order: Order) -> None:
        self._strategy_recent_orders.append(asdict(order))

    def _record_portfolio(self, timestamp: datetime, bars_by_symbol: dict[str, dict]) -> None:
        equity = self.portfolio.total_equity(bars_by_symbol)
        peak_equity = max(
            self.config.initial_cash,
            equity,
            float(self.portfolio_rows[-1]["peak_equity"]) if self.portfolio_rows else self.config.initial_cash,
        )
        drawdown = equity - peak_equity
        self.portfolio_rows.append(
            {
                "timestamp": timestamp,
                "cash": self.portfolio.cash,
                "equity": equity,
                "pnl": equity - self.config.initial_cash,
                "realized_pnl": self.portfolio.realized_pnl(),
                "open_unrealized_pnl": self.portfolio.open_unrealized_pnl(bars_by_symbol),
                "gross_exposure": self.portfolio.gross_exposure(bars_by_symbol),
                "peak_equity": peak_equity,
                "drawdown": drawdown,
                "drawdown_pct": drawdown / peak_equity if peak_equity else 0.0,
                "open_positions": len(self.portfolio.positions),
            }
        )
        self.position_rows.extend(self.portfolio.snapshot_rows(timestamp, bars_by_symbol))

    def _symbol_bar_rows(self, minute_bars: pl.DataFrame, session_date) -> list[dict]:
        selected_cols = [
            "ticker",
            "bar_time_market",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "vwap",
            "or_5m_high",
            "or_5m_low",
            "or_5m_range",
            "macd_line",
            "macd_signal",
            "macd_hist",
            "tema9",
            "tema20",
            "macd_line_5m",
            "macd_signal_5m",
            "macd_hist_5m",
            "tema9_5m",
            "tema20_5m",
        ]
        return minute_bars.select([col for col in selected_cols if col in minute_bars.columns]).with_columns(
            pl.lit(session_date.isoformat()).alias("session_date")
        ).to_dicts()

    def _record_context_symbol_bars(self, context_frames: dict[str, pl.DataFrame], session_date) -> None:
        for timeframe, frame in context_frames.items():
            rows = self._context_symbol_bar_rows(frame, session_date)
            if timeframe == "5m":
                self.symbol_bar_5m_rows.extend(rows)

    def _context_symbol_bar_rows(self, frame: pl.DataFrame, session_date) -> list[dict]:
        if frame.is_empty():
            return []
        return frame.with_columns(pl.lit(session_date.isoformat()).alias("session_date")).to_dicts()

    def _apply_symbol_exclusions(self, frames: DayFrames) -> DayFrames:
        if not self.symbol_exclusions.symbols:
            return frames
        return DayFrames(
            session_date=frames.session_date,
            event_frame=self._filter_excluded_frame(frames.event_frame),
            daily_context=self._filter_excluded_frame(frames.daily_context),
            context_frames={timeframe: self._filter_excluded_frame(frame) for timeframe, frame in frames.context_frames.items()},
        )

    def _filter_excluded_frame(self, frame: pl.DataFrame) -> pl.DataFrame:
        if frame.is_empty() or "ticker" not in frame.columns or not self.symbol_exclusions.symbols:
            return frame
        return frame.filter(~pl.col("ticker").cast(pl.Utf8).str.to_uppercase().is_in(list(self.symbol_exclusions.symbols)))

    def _strategy_chart_presentation(self) -> dict:
        presenter = getattr(self.strategy, "chart_presentation", None)
        if callable(presenter):
            presentation = presenter()
            return presentation if isinstance(presentation, dict) else {}
        return {}

    def _attach_observability(self) -> None:
        setter = getattr(self.strategy, "set_observability", None)
        if callable(setter):
            setter(self.observability)

    def _summary(self, run_dir) -> dict:
        return compute_summary(
            run_dir=str(run_dir),
            strategy_name=self.config.strategy_name,
            run_name=self.config.run_name,
            initial_cash=self.config.initial_cash,
            trades=self.trades,
            orders=self.orders,
            portfolio_rows=self.portfolio_rows,
            daily_rows=self.daily_rows,
            fills=self.fills,
        )

    def _candidate_count_for_day(self, session_date) -> int:
        day = session_date.isoformat()
        return len([row for row in self.strategy.artifacts().get("candidate_rankings", []) if row.get("session_date") == day])

    def _signal_count_for_day(self, session_date) -> int:
        day = session_date.isoformat()
        return len([row for row in self.strategy.artifacts().get("signal_events", []) if str(row.get("timestamp", "")).startswith(day)])

    def _rejection_count_for_day(self, session_date) -> int:
        day = session_date.isoformat()
        strategy_rejections = self.strategy.artifacts().get("rejection_events", [])
        all_rejections = strategy_rejections + self.engine_rejection_events
        return len([row for row in all_rejections if str(row.get("timestamp", "")).startswith(day)])

    def _write_artifacts(self, run_dir, summary: dict, artifact_writer: ArtifactWriter) -> None:
        artifacts = self.strategy.artifacts()
        observability_artifacts = self.observability.artifacts()
        artifact_writer.write_json(run_dir / "summary.json", summary)
        artifact_writer.write_table(run_dir / "daily_summary.parquet", self.daily_rows)
        artifact_writer.write_table(run_dir / "orders.parquet", self.orders)
        artifact_writer.write_table(run_dir / "fills.parquet", self.fills)
        artifact_writer.write_table(run_dir / "trades.parquet", self.trades)
        artifact_writer.write_table(run_dir / "positions.parquet", self.position_rows)
        artifact_writer.write_table(run_dir / "portfolio.parquet", self.portfolio_rows)
        self._write_chart_artifacts(run_dir, artifact_writer)
        artifact_writer.write_table(run_dir / "scanner_snapshots.parquet", artifacts.get("scanner_snapshots", []))
        artifact_writer.write_table(run_dir / "candidate_rankings.parquet", artifacts.get("candidate_rankings", []))
        artifact_writer.write_table(run_dir / "live_rankings.parquet", artifacts.get("live_rankings", []))
        artifact_writer.write_table(run_dir / "signal_events.parquet", artifacts.get("signal_events", []))
        artifact_writer.write_table(run_dir / "rejection_events.parquet", artifacts.get("rejection_events", []) + self.engine_rejection_events)
        artifact_writer.write_table(run_dir / "observability_scanner.parquet", observability_artifacts.get("observability_scanner", []))
        artifact_writer.write_table(run_dir / "observability_trace.parquet", observability_artifacts.get("observability_trace", []))
        artifact_writer.write_table(run_dir / "observability_state.parquet", observability_artifacts.get("observability_state", []))
        if self.symbol_bar_rows:
            artifact_writer.write_table(run_dir / "symbol_bars.parquet", self.symbol_bar_rows)
        if self.symbol_bar_5m_rows:
            artifact_writer.write_table(run_dir / "symbol_bars_5m.parquet", self.symbol_bar_5m_rows)
        artifact_writer.write_text(run_dir / "logs.txt", "\n".join(self.logs))

    def _write_chart_artifacts(self, run_dir, artifact_writer: ArtifactWriter) -> None:
        artifact_writer.write_portfolio_candles(
            run_dir / "portfolio_candles.parquet",
            self.portfolio_rows,
            initial_cash=self.config.initial_cash,
        )
        artifact_writer.write_json(
            run_dir / "chart_metadata.json",
            {
                "portfolio_candle_timeframes": ["30m", "1h", "2h", "4h", "1d"],
                "default_portfolio_candle_timeframe": default_portfolio_candle_timeframe(
                    self.config.start_date,
                    self.config.end_date,
                ),
            },
        )

    def _write_metadata(self, run_dir, metadata: dict, artifact_writer: ArtifactWriter) -> None:
        artifact_writer.write_json(run_dir / "metadata.json", metadata)
