from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timedelta
from math import ceil
from typing import Callable, Protocol

import polars as pl

from src.backtest.artifact_writer import ArtifactWriter
from src.backtest.cancel import BacktestCancelled
from src.backtest.config import BacktestConfig
from src.backtest.data.minute_bars import DayFrames, available_session_dates, load_day_frames, timeframe_minutes
from src.backtest.equity_candles import default_portfolio_candle_timeframe
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
        self.daily_rows: list[dict] = []
        self.logs: list[str] = []
        self.symbol_bar_rows: list[dict] = []
        self.symbol_bar_5m_rows: list[dict] = []
        self.fill_model = BarFillModel()
        self.fee_model = fee_model_for_name(config.fee_model, tax_rate=config.fee_tax_rate)
        self.observability = ObservabilityRecorder(config)
        self._attach_observability()

    def run(self, progress_callback=None, cancel_check: Callable[[], None] | None = None) -> dict:
        run_dir = create_run_dir(self.config)
        metadata = base_metadata(self.config, run_dir, "running")
        metadata["strategy_chart_presentation"] = self._strategy_chart_presentation()

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
                for index, session_date in enumerate(sessions, start=1):
                    self._check_cancelled(cancel_check)
                    self.observability.start_session(session_date, index)
                    day_start_equity = self.portfolio.total_equity()
                    frames = load_day_frames(self.config, session_date, requirements)
                    event_frame = self.strategy.prepare_day(frames, self.portfolio)
                    self._check_cancelled(cancel_check)
                    event_frame = event_frame.sort(["bar_time_market", "ticker"])
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

                    for timestamp, updates in self._timestamp_slices(event_frame, requirements.event_timeframe):
                        self._check_cancelled(cancel_check)
                        last_timestamp = timestamp
                        update_rows = updates.to_dicts()
                        fresh_bars = {row["ticker"]: row for row in update_rows}
                        latest_bars.update(fresh_bars)
                        latest = pl.DataFrame(list(latest_bars.values()), infer_schema_length=None) if latest_bars else pl.DataFrame()

                        self._fill_pending_orders(timestamp, fresh_bars)
                        self.portfolio.update_peaks(fresh_bars)

                        requests = self.strategy.on_bar(
                            BarContext(
                                timestamp=timestamp,
                                updates=updates,
                                latest=latest,
                                updates_by_symbol=fresh_bars,
                                latest_by_symbol=latest_bars,
                                observability=self.observability,
                            ),
                            self.portfolio,
                            list(self.pending_orders),
                        )
                        self._handle_requests(timestamp, requests, fresh_bars)
                        self._record_portfolio(timestamp, latest_bars)
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
                            latest_bars,
                        )
                        self._fill_market_exits(last_timestamp, latest_bars)
                        self._record_portfolio(last_timestamp, latest_bars)

                    day_end_equity = self.portfolio.total_equity(latest_bars)
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
        bar_duration = timedelta(minutes=timeframe_minutes(event_timeframe))
        for key, frame in event_frame.partition_by("bar_time_market", as_dict=True, maintain_order=True).items():
            bar_start = key[0] if isinstance(key, tuple) else key
            yield bar_start + bar_duration, frame

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
                if request.allow_same_bar_fill and bar is not None and self.fill_model.crossed(order, bar):
                    self._fill_order(order, timestamp, bar, order.reason)
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

    def _fill_pending_orders(self, timestamp: datetime, bars_by_symbol: dict[str, dict]) -> None:
        still_open = []
        for order in self.pending_orders:
            bar = bars_by_symbol.get(order.symbol)
            if bar is None:
                still_open.append(order)
                continue

            should_fill = False
            should_fill = self.fill_model.crossed(order, bar)

            if should_fill:
                self._fill_order(order, timestamp, bar, order.reason)
            else:
                still_open.append(order)
        self.pending_orders = still_open

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
        fee = self.fee_model.estimate(side=order.side, quantity=order.quantity, fill_price=fill_price)

        if order.side == "BUY":
            metadata = self.strategy.entry_metadata(order)
            if not self.portfolio.can_afford(fill_price, order.quantity, fee.total):
                order.status = "REJECTED_CASH"
                order.filled_at = timestamp
                order.fill_price = fill_price
                order.reason = reason
            else:
                order.status = "FILLED"
                order.filled_at = timestamp
                order.fill_price = fill_price
                order.reason = reason
                self._apply_fee_to_order(order, fee)
                self._record_fill(order, timestamp, bar, fill_price, fee)
                self.portfolio.open_position(
                    order,
                    setup_rank=int(metadata.get("setup_rank", 0)),
                    live_rank=int(metadata.get("live_rank", 0)),
                    setup_score=float(metadata.get("setup_score", 0.0)),
                    live_score=float(metadata.get("live_score", 0.0)),
                    stop_price=float(metadata.get("stop_price", order.stop_price or fill_price)),
                    fee=fee.total,
                )
        else:
            if order.symbol not in self.portfolio.positions:
                order.status = "REJECTED_NO_POSITION"
                order.filled_at = timestamp
                order.fill_price = fill_price
                order.reason = reason
                self.orders.append(asdict(order))
                return
            order.status = "FILLED"
            order.filled_at = timestamp
            order.fill_price = fill_price
            order.reason = reason
            self._apply_fee_to_order(order, fee)
            self._record_fill(order, timestamp, bar, fill_price, fee)
            trade = self.portfolio.close_position(order, fee=fee.total)
            if trade is not None:
                self.trades.append(asdict(trade))

        self.orders.append(asdict(order))

    def _apply_fee_to_order(self, order: Order, fee: FeeBreakdown) -> None:
        order.fill_fee = fee.total
        order.commission = fee.commission
        order.regulatory_fee = fee.regulatory_fee
        order.fee_tax = fee.tax
        order.fee_model = fee.model

    def _record_fill(self, order: Order, timestamp: datetime, bar: dict, fill_price: float, fee: FeeBreakdown) -> None:
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
            slippage_bps=self.config.slippage_bps,
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
        self.fills.append(asdict(fill))

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
        return len([row for row in self.strategy.artifacts().get("rejection_events", []) if str(row.get("timestamp", "")).startswith(day)])

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
        artifact_writer.write_table(run_dir / "rejection_events.parquet", artifacts.get("rejection_events", []))
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
                "portfolio_candle_timeframes": ["1h", "2h", "4h", "1d"],
                "default_portfolio_candle_timeframe": default_portfolio_candle_timeframe(
                    self.config.start_date,
                    self.config.end_date,
                ),
            },
        )

    def _write_metadata(self, run_dir, metadata: dict, artifact_writer: ArtifactWriter) -> None:
        artifact_writer.write_json(run_dir / "metadata.json", metadata)
