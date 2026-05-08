from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime
from typing import Protocol

import polars as pl

from src.backtest.config import BacktestConfig
from src.backtest.data.minute_bars import DayFrames, available_session_dates, load_day_frames
from src.backtest.fills import BarFillModel
from src.backtest.metrics import compute_summary
from src.backtest.models import MinuteContext, Order, OrderRequest
from src.backtest.portfolio import Portfolio
from src.backtest.results import base_metadata, create_run_dir, write_json, write_run_metadata, write_table


class Strategy(Protocol):
    name: str

    def prepare_day(self, frames: DayFrames, portfolio: Portfolio) -> None:
        ...

    def on_minute(self, context: MinuteContext, portfolio: Portfolio, pending_orders: list[Order]) -> list[OrderRequest]:
        ...

    def on_day_end(self, timestamp: datetime, portfolio: Portfolio) -> list[OrderRequest]:
        ...

    def artifacts(self) -> dict[str, list[dict]]:
        ...

    def entry_metadata(self, order: Order) -> dict:
        ...


class BacktestEngine:
    def __init__(self, config: BacktestConfig, strategy: Strategy):
        self.config = config
        self.strategy = strategy
        self.portfolio = Portfolio(config.initial_cash)
        self.next_order_id = 1
        self.pending_orders: list[Order] = []
        self.orders: list[dict] = []
        self.trades: list[dict] = []
        self.portfolio_rows: list[dict] = []
        self.position_rows: list[dict] = []
        self.daily_rows: list[dict] = []
        self.logs: list[str] = []
        self.symbol_bar_rows: list[dict] = []
        self.fill_model = BarFillModel()

    def run(self, progress_callback=None) -> dict:
        run_dir = create_run_dir(self.config)
        metadata = base_metadata(self.config, run_dir, "running")
        write_run_metadata(run_dir, metadata)
        write_json(run_dir / "config.json", self.config.to_dict())

        try:
            sessions = available_session_dates(self.config)
            logging.info("Running %s sessions", len(sessions))

            for index, session_date in enumerate(sessions, start=1):
                day_start_equity = self.portfolio.total_equity()
                frames = load_day_frames(self.config, session_date)
                self.strategy.prepare_day(frames, self.portfolio)

                if self.config.save_symbol_bars:
                    self.symbol_bar_rows.extend(self._symbol_bar_rows(frames.minute_bars, session_date))

                rows_by_time = self._rows_by_time(frames.minute_bars)
                last_timestamp = None
                last_bars = {}

                for timestamp in sorted(rows_by_time):
                    last_timestamp = timestamp
                    bars_by_symbol = rows_by_time[timestamp]
                    last_bars = bars_by_symbol
                    self._fill_pending_orders(timestamp, bars_by_symbol)
                    self.portfolio.update_peaks(bars_by_symbol)

                    requests = self.strategy.on_minute(
                        MinuteContext(timestamp=timestamp, bars_by_symbol=bars_by_symbol),
                        self.portfolio,
                        self.pending_orders,
                    )
                    self._handle_requests(timestamp, requests, bars_by_symbol)
                    self._record_portfolio(timestamp, bars_by_symbol)

                if last_timestamp is not None:
                    self._handle_requests(
                        last_timestamp,
                        self.strategy.on_day_end(last_timestamp, self.portfolio),
                        last_bars,
                    )
                    self._fill_market_exits(last_timestamp, last_bars)
                    self._record_portfolio(last_timestamp, last_bars)

                day_end_equity = self.portfolio.total_equity(last_bars)
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
                        "latest_session": session_date.isoformat(),
                        "latest_daily_summary": dict(self.daily_rows[-1]),
                    }
                )
                write_run_metadata(run_dir, metadata)

                if progress_callback:
                    progress_callback(session_date, dict(self.daily_rows[-1]), run_dir)

            summary = self._summary(run_dir)
            metadata.update({"status": "complete", "completed_at": datetime.now().isoformat(timespec="seconds"), "summary": summary})
            write_run_metadata(run_dir, metadata)
            self._write_artifacts(run_dir, summary)
            return {"run_dir": str(run_dir), "summary": summary}
        except Exception as exc:
            metadata.update({"status": "error", "error": str(exc), "failed_at": datetime.now().isoformat(timespec="seconds")})
            write_run_metadata(run_dir, metadata)
            raise

    def _rows_by_time(self, minute_bars: pl.DataFrame) -> dict[datetime, dict[str, dict]]:
        grouped: dict[datetime, dict[str, dict]] = {}
        for row in minute_bars.iter_rows(named=True):
            timestamp = row["bar_time_market"]
            grouped.setdefault(timestamp, {})[row["ticker"]] = row
        return grouped

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
                self._fill_order(order, timestamp, bars_by_symbol[order.symbol], order.reason)
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
        order.status = "FILLED"
        order.filled_at = timestamp
        order.fill_price = fill_price
        order.reason = reason

        if order.side == "BUY":
            metadata = self.strategy.entry_metadata(order)
            if not self.portfolio.can_afford(fill_price, order.quantity):
                order.status = "REJECTED_CASH"
            else:
                self.portfolio.open_position(
                    order,
                    setup_rank=int(metadata.get("setup_rank", 0)),
                    live_rank=int(metadata.get("live_rank", 0)),
                    setup_score=float(metadata.get("setup_score", 0.0)),
                    live_score=float(metadata.get("live_score", 0.0)),
                    stop_price=float(metadata.get("stop_price", order.stop_price or fill_price)),
                )
        else:
            trade = self.portfolio.close_position(order)
            if trade is not None:
                self.trades.append(asdict(trade))

        self.orders.append(asdict(order))

    def _record_portfolio(self, timestamp: datetime, bars_by_symbol: dict[str, dict]) -> None:
        equity = self.portfolio.total_equity(bars_by_symbol)
        self.portfolio_rows.append(
            {
                "timestamp": timestamp,
                "cash": self.portfolio.cash,
                "equity": equity,
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
            "macd_line_5m",
            "macd_signal_5m",
            "macd_hist_5m",
            "tema9_5m",
            "tema20_5m",
        ]
        return minute_bars.select([col for col in selected_cols if col in minute_bars.columns]).with_columns(
            pl.lit(session_date.isoformat()).alias("session_date")
        ).to_dicts()

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

    def _write_artifacts(self, run_dir, summary: dict) -> None:
        artifacts = self.strategy.artifacts()
        write_json(run_dir / "summary.json", summary)
        write_table(run_dir / "daily_summary.parquet", self.daily_rows)
        write_table(run_dir / "orders.parquet", self.orders)
        write_table(run_dir / "trades.parquet", self.trades)
        write_table(run_dir / "positions.parquet", self.position_rows)
        write_table(run_dir / "portfolio.parquet", self.portfolio_rows)
        write_table(run_dir / "scanner_snapshots.parquet", artifacts.get("scanner_snapshots", []))
        write_table(run_dir / "candidate_rankings.parquet", artifacts.get("candidate_rankings", []))
        write_table(run_dir / "signal_events.parquet", artifacts.get("signal_events", []))
        write_table(run_dir / "rejection_events.parquet", artifacts.get("rejection_events", []))
        if self.symbol_bar_rows:
            write_table(run_dir / "symbol_bars.parquet", self.symbol_bar_rows)
        (run_dir / "logs.txt").write_text("\n".join(self.logs), encoding="utf-8")
