from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from uuid import uuid4

import polars as pl

from src.backtest.config import BacktestConfig
from src.backtest.data.minute_bars import DayFrames, available_session_dates, load_day_frames
from src.backtest.engine import BacktestEngine, Strategy
from src.backtest.models import BarContext


class StepBacktestDebugger(BacktestEngine):
    """Interactive one-bar-at-a-time backtest debugger."""

    def __init__(self, config: BacktestConfig, strategy: Strategy):
        super().__init__(config, strategy)
        self.debug_session_id = uuid4().hex
        self.requirements = self.strategy.data_requirements()
        self.sessions = available_session_dates(self.config, self.requirements)
        if not self.sessions:
            raise ValueError(
                f"No market sessions found for requested range "
                f"{self.config.start_date.isoformat()}..{self.config.end_date.isoformat()}"
            )
        self.requires_latest_frame = self._strategy_requires_latest_frame()
        self.session_index = 0
        self.session_date = None
        self.day_start_equity = self.portfolio.total_equity()
        self.event_slices: list[tuple[datetime, pl.DataFrame]] = []
        self.execution_by_timestamp: dict[datetime, pl.DataFrame] = {}
        self.slice_index = 0
        self.latest_bars: dict[str, dict] = {}
        self.latest_execution_bars: dict[str, dict] = {}
        self.processed_event_bars = 0
        self.history: list[dict] = []
        self.history_cursor = -1
        self.complete = False
        self._load_session(0)

    def payload(self) -> dict:
        return {
            "session_id": self.debug_session_id,
            "status": "complete" if self.complete else "ready",
            "config": self.config.to_dict(),
            "strategy": {
                "name": self.config.strategy_name,
                "version": self.config.strategy_version,
                "data_requirements": asdict(self.requirements),
            },
            "sessions": [session.isoformat() for session in self.sessions],
            "current_session": self.session_date.isoformat() if self.session_date else None,
            "current_step_index": self.history_cursor,
            "processed_event_bars": self.processed_event_bars,
            "total_history_steps": len(self.history),
            "total_event_bars": self._expected_event_bar_count(self.sessions, self.requirements),
            "summary": self._summary("debug"),
            "trades": list(self.trades),
            "step": self.history[self.history_cursor] if 0 <= self.history_cursor < len(self.history) else None,
        }

    def previous_step(self) -> dict:
        if self.history_cursor > 0:
            self.history_cursor -= 1
        return self.payload()

    def next_step(self) -> dict:
        if self.history_cursor < len(self.history) - 1:
            self.history_cursor += 1
            return self.payload()
        if self.complete:
            return self.payload()
        step = self._advance_one_bar()
        self.history.append(step)
        self.history_cursor = len(self.history) - 1
        return self.payload()

    def _load_session(self, session_index: int) -> None:
        self.session_index = session_index
        self.session_date = self.sessions[session_index]
        self.observability.start_session(self.session_date, session_index + 1)
        self.day_start_equity = self.portfolio.total_equity()
        frames = load_day_frames(self.config, self.session_date, self.requirements)
        frames = self._apply_symbol_exclusions(frames)
        execution_frame = frames.event_frame
        decision_frames = DayFrames(
            session_date=frames.session_date,
            event_frame=self._strategy_decision_frame(execution_frame, self.requirements),
            daily_context=frames.daily_context,
            context_frames=frames.context_frames,
        )
        event_frame = self.strategy.prepare_day(decision_frames, self.portfolio)
        event_frame = self._filter_excluded_frame(event_frame).sort(["bar_time_market", "ticker"])
        execution_frame = self._execution_frame_for_decisions(execution_frame, event_frame).sort(["bar_time_market", "ticker"])
        self.event_slices = list(self._timestamp_slices(event_frame, self.requirements.event_timeframe) or [])
        self.execution_by_timestamp = {
            timestamp: frame
            for timestamp, frame in self._timestamp_slices(execution_frame, self.requirements.event_timeframe)
        }
        self.slice_index = 0
        self.latest_bars = {}
        self.latest_execution_bars = {}

    def _advance_one_bar(self) -> dict:
        while self.slice_index >= len(self.event_slices):
            if self.session_index < len(self.sessions) - 1:
                self._record_day_end(self.event_slices[-1][0] if self.event_slices else datetime.combine(self.session_date, datetime.min.time()))
                self._load_session(self.session_index + 1)
                continue
            self.complete = True
            return self._completion_step()

        timestamp, updates = self.event_slices[self.slice_index]
        self.slice_index += 1
        before = self._counts()
        pending_order_ids_at_open = {order.order_id for order in self.pending_orders}
        update_rows = updates.to_dicts()
        fresh_bars = {row["ticker"]: row for row in update_rows}
        execution_updates = self.execution_by_timestamp.get(timestamp, pl.DataFrame())
        execution_rows = execution_updates.to_dicts() if not execution_updates.is_empty() else []
        execution_fresh_bars = {row["ticker"]: row for row in execution_rows}
        self.latest_bars.update(fresh_bars)
        self.latest_execution_bars.update(execution_fresh_bars)
        latest = (
            pl.DataFrame(list(self.latest_bars.values()), infer_schema_length=None)
            if self.requires_latest_frame and self.latest_bars
            else pl.DataFrame()
        )

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
                latest_by_symbol=self.latest_bars,
                observability=self.observability,
                recent_orders=recent_orders,
                recent_fills=recent_fills,
            ),
            self.portfolio,
            list(self.pending_orders),
        )
        requests_payload = [asdict(request) for request in requests]
        self._handle_requests(timestamp, requests, execution_fresh_bars)
        self._fill_pending_orders(timestamp, execution_fresh_bars, eligible_order_ids=pending_order_ids_at_open)
        self.portfolio.update_peaks(execution_fresh_bars)
        self._record_portfolio(
            timestamp,
            self._portfolio_mark_bars_at_current_open(self.latest_execution_bars, execution_fresh_bars),
        )
        self.processed_event_bars += 1
        after = self._counts()
        return self._step_payload(
            timestamp=timestamp,
            updates=update_rows,
            execution_rows=execution_rows,
            requests=requests_payload,
            before=before,
            after=after,
        )

    def _record_day_end(self, timestamp: datetime) -> None:
        if self.session_date is None:
            return
        self._handle_requests(timestamp, self.strategy.on_day_end(timestamp, self.portfolio), self.latest_execution_bars)
        self._fill_market_exits(timestamp, self.latest_execution_bars)
        self._record_portfolio(timestamp, self.latest_execution_bars)
        day_end_equity = self.portfolio.total_equity(self.latest_execution_bars)
        self.daily_rows.append(
            {
                "session_date": self.session_date.isoformat(),
                "start_equity": self.day_start_equity,
                "end_equity": day_end_equity,
                "pnl": day_end_equity - self.day_start_equity,
                "return_pct": (day_end_equity / self.day_start_equity) - 1.0 if self.day_start_equity else 0.0,
                "trade_count": len([t for t in self.trades if str(t["exit_time"]).startswith(self.session_date.isoformat())]),
                "candidate_count": self._candidate_count_for_day(self.session_date),
                "signal_count": self._signal_count_for_day(self.session_date),
                "rejection_count": self._rejection_count_for_day(self.session_date),
            }
        )

    def _completion_step(self) -> dict:
        return {
            "type": "complete",
            "timestamp": None,
            "session_date": self.session_date.isoformat() if self.session_date else None,
            "message": "Debug backtest is complete.",
            "summary": self._summary("debug"),
            "cumulative_trades": list(self.trades),
            "portfolio": self.portfolio_rows[-1:] if self.portfolio_rows else [],
            "positions": self.position_rows[-20:],
        }

    def _step_payload(
        self,
        *,
        timestamp: datetime,
        updates: list[dict],
        execution_rows: list[dict],
        requests: list[dict],
        before: dict[str, int],
        after: dict[str, int],
    ) -> dict:
        artifacts = self.strategy.artifacts()
        observability = self.observability.artifacts()
        scanner_rows = self._last_strategy_scanner_rows()
        if not scanner_rows:
            scanner_rows = self._delta_rows(artifacts.get("live_rankings", []), before["live_rankings"], after["live_rankings"])
        if not scanner_rows:
            scanner_rows = self._delta_rows(artifacts.get("candidate_rankings", []), before["candidate_rankings"], after["candidate_rankings"])
        watchlist_rows = self._delta_rows(artifacts.get("watchlist_snapshots", []), before["watchlist_snapshots"], after["watchlist_snapshots"])
        return {
            "type": "bar",
            "timestamp": timestamp.isoformat(),
            "session_date": self.session_date.isoformat() if self.session_date else None,
            "bar_index": self.processed_event_bars,
            "raw_scanner_rows": updates,
            "execution_rows": execution_rows,
            "strategy_scanner_rows": scanner_rows,
            "strategy_watchlist_rows": watchlist_rows,
            "filter_groups": self._filter_group_rows(scanner_rows or updates),
            "strategy_requests": requests,
            "orders": self.orders[before["orders"] : after["orders"]],
            "fills": self.fills[before["fills"] : after["fills"]],
            "trades": self.trades[before["trades"] : after["trades"]],
            "cumulative_trades": list(self.trades),
            "portfolio": self.portfolio_rows[before["portfolio"] : after["portfolio"]],
            "positions": self.position_rows[before["positions"] : after["positions"]],
            "signal_events": self._delta_rows(artifacts.get("signal_events", []), before["signal_events"], after["signal_events"]),
            "rejection_events": self._delta_rows(artifacts.get("rejection_events", []), before["rejection_events"], after["rejection_events"])
            + self.engine_rejection_events[before["engine_rejections"] : after["engine_rejections"]],
            "observability_scanner": self._delta_rows(observability.get("observability_scanner", []), before["observability_scanner"], after["observability_scanner"]),
            "observability_trace": self._delta_rows(observability.get("observability_trace", []), before["observability_trace"], after["observability_trace"]),
            "observability_state": self._delta_rows(observability.get("observability_state", []), before["observability_state"], after["observability_state"]),
            "pending_orders": [asdict(order) for order in self.pending_orders],
            "recent_orders_for_next_bar": list(self._strategy_recent_orders),
            "recent_fills_for_next_bar": list(self._strategy_recent_fills),
            "summary": self._summary("debug"),
        }

    def _counts(self) -> dict[str, int]:
        artifacts = self.strategy.artifacts()
        observability = self.observability.artifacts()
        return {
            "candidate_rankings": len(artifacts.get("candidate_rankings", [])),
            "engine_rejections": len(self.engine_rejection_events),
            "fills": len(self.fills),
            "live_rankings": len(artifacts.get("live_rankings", [])),
            "observability_scanner": len(observability.get("observability_scanner", [])),
            "observability_state": len(observability.get("observability_state", [])),
            "observability_trace": len(observability.get("observability_trace", [])),
            "orders": len(self.orders),
            "portfolio": len(self.portfolio_rows),
            "positions": len(self.position_rows),
            "rejection_events": len(artifacts.get("rejection_events", [])),
            "signal_events": len(artifacts.get("signal_events", [])),
            "trades": len(self.trades),
            "watchlist_snapshots": len(artifacts.get("watchlist_snapshots", [])),
        }

    def _delta_rows(self, rows: list[dict], before: int, after: int) -> list[dict]:
        return rows[before:after]

    def _last_strategy_scanner_rows(self) -> list[dict]:
        rows = getattr(self.strategy, "last_scanner_rows", None)
        if not isinstance(rows, list):
            return []
        return [dict(row) for row in rows if isinstance(row, dict)]

    def _filter_group_rows(self, rows: list[dict]) -> list[dict]:
        if self.config.strategy_name == "long_momentum" and str(self.config.strategy_version).lower() == "v9":
            return self._long_momentum_v9_filter_group_rows(rows)
        if self.config.strategy_name == "long_momentum" and str(self.config.strategy_version).lower() in {"v2", "v3", "v7"}:
            return self._long_momentum_v2_family_filter_group_rows(rows)
        groups: dict[tuple[str, str], dict] = {}
        for row in rows:
            ticker = str(row.get("ticker") or row.get("symbol") or "")
            for key, value in row.items():
                if not self._looks_like_filter_column(key):
                    continue
                group = self._filter_group_name(key)
                item = groups.setdefault(
                    (ticker, group),
                    {"ticker": ticker, "filter_group": group, "passed": 0, "failed": 0, "filters": []},
                )
                passed = bool(value)
                item["passed"] += 1 if passed else 0
                item["failed"] += 0 if passed else 1
                item["filters"].append(f"{key}={value}")
        return [
            {**item, "all_passed": item["failed"] == 0, "filters": " | ".join(item["filters"])}
            for item in groups.values()
        ]

    def _looks_like_filter_column(self, key: str) -> bool:
        lowered = key.lower()
        return (
            lowered.endswith("_ok")
            or lowered.endswith("_open")
            or lowered.endswith("_positive")
            or lowered.endswith("_seen")
            or lowered.startswith("long_momentum_")
            or lowered in {"entry_open", "current_open_above_last_body_high"}
        )

    def _filter_group_name(self, key: str) -> str:
        lowered = key.lower()
        if "spread" in lowered or "quote" in lowered or "liquidity" in lowered:
            return "Liquidity / Spread"
        if "volume" in lowered or "transaction" in lowered:
            return "Volume / Activity"
        if "tema" in lowered or "macd" in lowered or "trend" in lowered or "momentum" in lowered:
            return "Trend / Momentum"
        if "entry" in lowered or "trigger" in lowered or "open" in lowered:
            return "Entry Trigger"
        if "divergence" in lowered or "exhaustion" in lowered:
            return "Exhaustion Guard"
        if "price" in lowered or "vwap" in lowered or "day_high" in lowered or "distance" in lowered:
            return "Price Location"
        return "Other Filters"

    def _long_momentum_v2_family_filter_group_rows(self, rows: list[dict]) -> list[dict]:
        version = str(self.config.strategy_version).lower()
        filter_rows: list[dict] = []
        for row in rows:
            ticker = str(row.get("ticker") or row.get("symbol") or "")
            checks_by_group = {
                "Price": [
                    self._range_check(row, "last_close", self._strategy_param("min_price", 1.0), self._strategy_param("max_price", 10.0)),
                ],
                "Entry Trigger": [
                    self._bool_check(row, "current_open_above_last_body_high", True, fallback_key="_lm_current_open_above_last_body_high"),
                ],
                "Volume / Activity": [
                    self._gte_check(row, "last_volume", self._strategy_param("min_volume", 10_000.0)),
                    self._gte_check(row, "last_transactions", self._strategy_param("min_transactions", 100.0)),
                    self._gte_check(row, "last_recent_dollar_volume_5", self._strategy_param("min_recent_dollar_volume_5", 100_000.0)),
                ],
                "Liquidity / Spread": [
                    self._bool_check(row, "long_momentum_spread_ok", True, fallback_key="_lm_spread_ok"),
                    self._lte_check(row, "last_spread_bps_abs", self._strategy_param("max_spread_bps_abs", 100.0)),
                    self._lte_check(row, "last_spread_bps_max", self._strategy_param("max_spread_bps_max", 150.0)),
                    self._gte_check(row, "last_quote_valid_ratio", self._strategy_param("min_quote_valid_ratio", 0.8)),
                    self._lte_check(row, "last_locked_or_crossed_count", self._strategy_param("max_locked_or_crossed_count", 0.0)),
                ],
                "Trend / Momentum": [
                    self._bool_check(row, "last_tema_open", True, fallback_key="_lm_tema_open"),
                    self._macd_line_or_vwap_check(row) if version == "v2" else self._gt_check(row, "last_macd_line", 0.0),
                    self._gte_check(row, "last_macd_hist_z_since_open", self._strategy_param("min_macd_hist_z_since_open", 0.1)),
                ],
                "Final Strategy Decision": [
                    self._bool_check(row, "long_momentum_entry_open", True, fallback_key="entry_open"),
                ],
            }
            if version in {"v3", "v7"}:
                price_quality_checks = checks_by_group.setdefault("Price Quality", [])
                price_quality_checks.append(self._gte_check(row, "last_close_location", self._strategy_param("min_close_location", 0.85)))
            for group, checks in checks_by_group.items():
                passed_count = sum(1 for check in checks if check["passed"])
                failed_checks = [check for check in checks if not check["passed"]]
                filter_rows.append(
                    {
                        "ticker": ticker,
                        "filter_group": group,
                        "passed": passed_count,
                        "failed": len(failed_checks),
                        "all_passed": not failed_checks,
                        "failed_filters": " | ".join(check["name"] for check in failed_checks),
                        "filters": " | ".join(check["label"] for check in checks),
                    }
                )
        return filter_rows

    def _long_momentum_v9_filter_group_rows(self, rows: list[dict]) -> list[dict]:
        filter_rows: list[dict] = []
        for row in rows:
            ticker = str(row.get("ticker") or row.get("symbol") or "")
            checks_by_group = {
                "Price Eligibility": [
                    self._range_check(row, "last_close", self._strategy_param("min_price", 1.0), self._strategy_param("max_price", 10.0)),
                ],
                "Watchlist Add": [
                    self._range_check(row, "last_close", self._strategy_param("min_price", 1.0), self._strategy_param("max_price", 10.0)),
                    self._gte_check(row, "long_momentum_v9_last_5m_return", self._strategy_param("min_last_5m_return", 0.05), fallback_key="last_5m_return"),
                    self._gte_check(row, "last_volume", self._strategy_param("min_watchlist_add_volume", 8_000.0)),
                    self._gte_check(row, "last_transactions", self._strategy_param("min_first_entry_transactions", 100.0)),
                ],
                "First Entry Raw Inputs": [
                    self._range_check(row, "last_close", self._strategy_param("min_price", 1.0), self._strategy_param("max_price", 10.0)),
                    self._range_check(row, "minute_of_day", self._strategy_param("trading_start_minute", 240.0), self._strategy_param("trading_end_minute", 1200.0) - 1),
                    self._gt_check(row, "last_day_high_so_far", 0.0),
                    self._gt_check(row, "current_open", self._number(row, "last_day_high_so_far") or 0.0),
                ],
                "First Entry Strategy State": [
                    self._present_check(row, "long_momentum_v9_watchlist_added_timestamp"),
                    self._lte_check(row, "held_quantity", 0.0),
                    self._bool_check(row, "long_momentum_v9_pending_symbol_order", False),
                    self._bool_check(row, "long_momentum_v9_first_entry_available", True),
                    self._bool_check(row, "long_momentum_v9_first_entry_day_high_break_ok", True),
                ],
                "Watchlist VWAP Entry Raw Inputs": [
                    self._range_check(row, "last_close", self._strategy_param("min_price", 1.0), self._strategy_param("max_price", 10.0)),
                    self._range_check(row, "minute_of_day", self._strategy_param("trading_start_minute", 240.0), self._strategy_param("trading_end_minute", 1200.0) - 1),
                    self._gt_check(row, "last_vwap", 0.0),
                    self._gte_check(
                        row,
                        "last_close",
                        (self._number(row, "last_vwap") or 0.0)
                        * (1.0 + max(0.0, self._strategy_param("reentry_vwap_buffer_pct", 2.0)) / 100.0),
                    ),
                    self._gte_check(row, "last_close", self._number(row, "last_open") or 0.0),
                    self._v9_last_tema_open_check(row),
                    self._lte_check(row, "last_bearish_volume_divergence_score", self._strategy_param("max_reentry_bvd_score", 80.0)),
                    self._v9_two_bar_body_reentry_check(row),
                ],
                "Watchlist VWAP Entry Strategy State": [
                    self._present_check(row, "long_momentum_v9_watchlist_added_timestamp"),
                    self._bool_check(row, "long_momentum_v9_watchlist_first_entry_filled", True),
                    self._bool_check(row, "long_momentum_v9_watchlist_entry_ready", True),
                    self._lte_check(row, "held_quantity", 0.0),
                    self._bool_check(row, "long_momentum_v9_pending_symbol_order", False),
                    self._bool_check(row, "long_momentum_v9_reentry_vwap_buffer_ok", True),
                    self._bool_check(row, "long_momentum_v9_reentry_last_bar_not_red", True),
                    self._bool_check(row, "long_momentum_v9_reentry_last_tema_open_ok", True),
                    self._bool_check(row, "long_momentum_v9_reentry_bvd_ok", True),
                    self._bool_check(row, "long_momentum_v9_reentry_body_break_ok", True),
                ],
                "Exit": [
                    self._gt_check(row, "last_double_timeframe_bearish_volume_divergence_score", self._strategy_param("double_bvd_exit_score", 50.0)),
                    self._bool_check(row, "long_momentum_v9_double_bvd_exit_red_ok", True),
                    self._v9_tema_exit_check(row),
                ],
                "Pocket Exit": [
                    self._gt_check(row, "held_quantity", 0.0),
                    self._v9_pocket_threshold_check(row),
                ],
                "Final Strategy Decision": [
                    self._check(
                        "long_momentum_v9_first_or_watchlist_entry_open",
                        f"first={self._bool_value(row, 'long_momentum_v9_first_entry_open')}, reentry={self._bool_value(row, 'long_momentum_v9_reentry_open')}",
                        bool(
                            self._bool_value(row, "long_momentum_v9_first_entry_open")
                            or self._bool_value(row, "long_momentum_v9_reentry_open")
                        ),
                        "is True",
                    ),
                    self._bool_check(row, "long_momentum_v9_entry_open", True, fallback_key="entry_open"),
                ],
            }
            for group, checks in checks_by_group.items():
                passed_count = sum(1 for check in checks if check["passed"])
                failed_checks = [check for check in checks if not check["passed"]]
                filter_rows.append(
                    {
                        "ticker": ticker,
                        "filter_group": group,
                        "passed": passed_count,
                        "failed": len(failed_checks),
                        "all_passed": not failed_checks,
                        "failed_filters": " | ".join(check["name"] for check in failed_checks),
                        "filters": " | ".join(check["label"] for check in checks),
                    }
                )
        return filter_rows

    def _strategy_param(self, name: str, default: float) -> float:
        config = getattr(self.strategy, "config", None)
        value = getattr(config, name, None)
        if value is None:
            value = self.config.strategy_params.get(name, default)
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed == parsed else default

    def _number(self, row: dict, key: str, fallback_key: str | None = None) -> float | None:
        value = row.get(key)
        if value is None and fallback_key:
            value = row.get(fallback_key)
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed == parsed else None

    def _bool_value(self, row: dict, key: str, fallback_key: str | None = None) -> bool | None:
        value = row.get(key)
        if value is None and fallback_key:
            value = row.get(fallback_key)
        if value is None:
            return None
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y"}
        return bool(value)

    def _check(self, name: str, value: object, passed: bool, expectation: str) -> dict:
        return {"label": f"{name}={value} {expectation} => {passed}", "name": name, "passed": passed}

    def _present_check(self, row: dict, key: str) -> dict:
        value = row.get(key)
        passed = value is not None and str(value).strip() != ""
        return self._check(key, value, passed, "has value")

    def _range_check(self, row: dict, key: str, minimum: float, maximum: float, fallback_key: str | None = None) -> dict:
        value = self._number(row, key, fallback_key)
        return self._check(key, value, value is not None and minimum <= value <= maximum, f"between {minimum:g} and {maximum:g}")

    def _gte_check(self, row: dict, key: str, threshold: float, fallback_key: str | None = None) -> dict:
        value = self._number(row, key, fallback_key)
        return self._check(key, value, value is not None and value >= threshold, f">= {threshold:g}")

    def _gt_check(self, row: dict, key: str, threshold: float, fallback_key: str | None = None) -> dict:
        value = self._number(row, key, fallback_key)
        return self._check(key, value, value is not None and value > threshold, f"> {threshold:g}")

    def _v9_tema_exit_check(self, row: dict) -> dict:
        tema9 = self._number(row, "current_open_tema9")
        tema20 = self._number(row, "current_open_tema20")
        buffer_pct = self._strategy_param("tema9_exit_buffer_pct", -0.002)
        threshold = tema9 * (1.0 + buffer_pct) if tema9 is not None else None
        passed = tema20 is not None and threshold is not None and tema20 >= threshold
        return self._check(
            "current_open_tema20_vs_tema9_exit_buffer",
            f"current_open_tema20={tema20}, threshold={threshold}",
            passed,
            f">= current_open_tema9 * (1 + {buffer_pct:g})",
        )

    def _v9_last_tema_open_check(self, row: dict) -> dict:
        tema9 = self._number(row, "last_tema9")
        tema20 = self._number(row, "last_tema20")
        buffer_pct = self._strategy_param("tema9_open_buffer_pct", 0.002)
        threshold = tema20 * (1.0 + buffer_pct) if tema20 is not None else None
        passed = tema9 is not None and threshold is not None and tema9 >= threshold
        return self._check(
            "last_tema9_vs_tema20_open_buffer",
            f"last_tema9={tema9}, threshold={threshold}",
            passed,
            f">= last_tema20 * (1 + {buffer_pct:g})",
        )

    def _v9_two_bar_body_reentry_check(self, row: dict) -> dict:
        current_open = self._number(row, "current_open")
        threshold = self._number(row, "last_2_body_high")
        return self._check(
            "current_open_breaks_last_2_body_high",
            f"current_open={current_open}, last_2_body_high={threshold}",
            current_open is not None and threshold is not None and current_open > threshold,
            "current_open > last_2_body_high",
        )

    def _v9_pocket_threshold_check(self, row: dict) -> dict:
        estimated_bid = self._number(row, "long_momentum_v9_pocket_estimated_bid")
        trigger_price = self._number(row, "long_momentum_v9_pocket_trigger_price")
        pocket_pct = self._number(row, "long_momentum_v9_pocket_pct")
        vol_pct = self._number(row, "long_momentum_v9_pocket_vol_pct")
        mode = row.get("long_momentum_v9_pocket_mode")
        passed = estimated_bid is not None and trigger_price is not None and estimated_bid >= trigger_price
        return self._check(
            "long_momentum_v9_pocket_estimated_bid_vs_trigger",
            f"mode={mode}, estimated_bid={estimated_bid}, trigger={trigger_price}, pocket_pct={pocket_pct}, vol_pct={vol_pct}",
            passed,
            "estimated bid >= entry * (1 + pocket pct)",
        )

    def _lte_check(self, row: dict, key: str, threshold: float, fallback_key: str | None = None) -> dict:
        value = self._number(row, key, fallback_key)
        return self._check(key, value, value is not None and value <= threshold, f"<= {threshold:g}")

    def _bool_check(self, row: dict, key: str, expected: bool, fallback_key: str | None = None) -> dict:
        value = self._bool_value(row, key, fallback_key)
        return self._check(key, value, value == expected, f"is {expected}")

    def _macd_line_or_vwap_check(self, row: dict) -> dict:
        value = self._bool_value(row, "long_momentum_v2_macd_line_or_vwap_ok")
        if value is None:
            macd_line = self._number(row, "last_macd_line", "_lm_macd_line")
            current_open = self._number(row, "current_open", "_lm_current_open")
            last_vwap = self._number(row, "last_vwap", "_lm_last_vwap")
            value = bool((macd_line is not None and macd_line > 0) or (current_open is not None and last_vwap is not None and last_vwap > 0 and current_open > last_vwap))
        macd_line = self._number(row, "last_macd_line", "_lm_macd_line")
        current_open = self._number(row, "current_open", "_lm_current_open")
        last_vwap = self._number(row, "last_vwap", "_lm_last_vwap")
        label = (
            "last_macd_line_or_current_open_above_vwap="
            f"{value} (last_macd_line={macd_line}, current_open={current_open}, last_vwap={last_vwap}) is True => {value}"
        )
        return {"label": label, "name": "last_macd_line_or_current_open_above_vwap", "passed": value}
