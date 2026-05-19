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
        self._record_portfolio(timestamp, self.latest_execution_bars)
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
        scanner_rows = self._delta_rows(artifacts.get("live_rankings", []), before["live_rankings"], after["live_rankings"])
        if not scanner_rows:
            scanner_rows = self._delta_rows(artifacts.get("candidate_rankings", []), before["candidate_rankings"], after["candidate_rankings"])
        return {
            "type": "bar",
            "timestamp": timestamp.isoformat(),
            "session_date": self.session_date.isoformat() if self.session_date else None,
            "bar_index": self.processed_event_bars,
            "raw_scanner_rows": updates,
            "execution_rows": execution_rows,
            "strategy_scanner_rows": scanner_rows,
            "filter_groups": self._filter_group_rows(scanner_rows or updates),
            "strategy_requests": requests,
            "orders": self.orders[before["orders"] : after["orders"]],
            "fills": self.fills[before["fills"] : after["fills"]],
            "trades": self.trades[before["trades"] : after["trades"]],
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
        }

    def _delta_rows(self, rows: list[dict], before: int, after: int) -> list[dict]:
        return rows[before:after]

    def _filter_group_rows(self, rows: list[dict]) -> list[dict]:
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
                    self._gt_check(row, "last_macd_line", 0.0),
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

    def _range_check(self, row: dict, key: str, minimum: float, maximum: float, fallback_key: str | None = None) -> dict:
        value = self._number(row, key, fallback_key)
        return self._check(key, value, value is not None and minimum <= value <= maximum, f"between {minimum:g} and {maximum:g}")

    def _gte_check(self, row: dict, key: str, threshold: float, fallback_key: str | None = None) -> dict:
        value = self._number(row, key, fallback_key)
        return self._check(key, value, value is not None and value >= threshold, f">= {threshold:g}")

    def _gt_check(self, row: dict, key: str, threshold: float, fallback_key: str | None = None) -> dict:
        value = self._number(row, key, fallback_key)
        return self._check(key, value, value is not None and value > threshold, f"> {threshold:g}")

    def _lte_check(self, row: dict, key: str, threshold: float, fallback_key: str | None = None) -> dict:
        value = self._number(row, key, fallback_key)
        return self._check(key, value, value is not None and value <= threshold, f"<= {threshold:g}")

    def _bool_check(self, row: dict, key: str, expected: bool, fallback_key: str | None = None) -> dict:
        value = self._bool_value(row, key, fallback_key)
        return self._check(key, value, value == expected, f"is {expected}")
