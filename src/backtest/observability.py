from __future__ import annotations

import json
from datetime import date, datetime
from math import ceil
from typing import Any

from src.backtest.config import BacktestConfig


class ObservabilityRecorder:
    """Captures bounded strategy observability records for a backtest run."""

    def __init__(self, config: BacktestConfig) -> None:
        self.config = config
        self.session_date: date | None = None
        self.session_index = 0
        self.scanner_rows: list[dict[str, Any]] = []
        self.trace_rows: list[dict[str, Any]] = []
        self.state_rows: list[dict[str, Any]] = []

    def start_session(self, session_date: date, session_index: int) -> None:
        self.session_date = session_date
        self.session_index = session_index

    def scanner(
        self,
        *,
        timestamp: datetime | str,
        rows: list[dict[str, Any]],
        score_key: str,
        stage: str = "scanner",
    ) -> None:
        if not self._capture_profile_window() or not rows:
            return
        ranked = sorted(rows, key=lambda row: _number(row.get(score_key)), reverse=True)
        limit = self._scanner_capture_limit(len(ranked))
        for rank, row in enumerate(ranked[:limit], start=1):
            self.scanner_rows.append(
                self._base_row(timestamp, row.get("ticker"), stage=stage)
                | {
                    "rank": rank,
                    "scanner_status": row.get("scanner_status") or row.get("status") or "observed",
                    "reason_code": row.get("reason_code") or row.get("reject_reason") or row.get("reason") or "",
                    "score": _number(row.get(score_key)),
                    "score_key": score_key,
                    "total_candidates": len(ranked),
                    "captured_candidates": limit,
                    "values_json": _json(row),
                }
            )

    def trace(
        self,
        *,
        timestamp: datetime | str,
        stage: str,
        event_type: str,
        decision: str,
        reason_code: str = "",
        reason: str = "",
        ticker: str | None = None,
        values: dict[str, Any] | None = None,
        state: dict[str, Any] | None = None,
        linked_order_id: int | None = None,
        linked_trade_id: int | None = None,
        force: bool = False,
    ) -> None:
        if self.config.observability_mode == "off":
            return
        if not self._capture_profile_window() and not force:
            return
        self.trace_rows.append(
            self._base_row(timestamp, ticker, stage=stage)
            | {
                "event_type": event_type,
                "decision": decision,
                "reason_code": reason_code,
                "reason": reason,
                "linked_order_id": linked_order_id,
                "linked_trade_id": linked_trade_id,
                "values_json": _json(values or {}),
                "state_json": _json(state or {}),
            }
        )

    def state(
        self,
        *,
        timestamp: datetime | str,
        scope: str,
        state: dict[str, Any],
        ticker: str | None = None,
        force: bool = False,
    ) -> None:
        if self.config.observability_mode == "off":
            return
        if not self._capture_profile_window() and not force:
            return
        self.state_rows.append(
            self._base_row(timestamp, ticker, stage="state")
            | {
                "scope": scope,
                "state_json": _json(state),
            }
        )

    def artifacts(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "observability_scanner": self.scanner_rows,
            "observability_trace": self.trace_rows,
            "observability_state": self.state_rows,
        }

    def _base_row(self, timestamp: datetime | str, ticker: Any, *, stage: str) -> dict[str, Any]:
        return {
            "session_date": self.session_date.isoformat() if self.session_date else "",
            "session_index": self.session_index,
            "timestamp": timestamp,
            "ticker": str(ticker or ""),
            "strategy_name": self.config.strategy_name,
            "strategy_version": self.config.strategy_version,
            "stage": stage,
        }

    def _capture_profile_window(self) -> bool:
        if self.config.observability_mode == "off":
            return False
        if self.config.observability_sessions <= 0:
            return True
        return 0 < self.session_index <= self.config.observability_sessions

    def _scanner_capture_limit(self, row_count: int) -> int:
        if row_count <= 0:
            return 0
        raw_limit = ceil(row_count * max(0.0, self.config.observability_scanner_top_percent))
        return max(
            1,
            min(
                row_count,
                self.config.observability_scanner_max_rows,
                max(self.config.observability_scanner_min_rows, raw_limit),
            ),
        )


def _number(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _json(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True)
