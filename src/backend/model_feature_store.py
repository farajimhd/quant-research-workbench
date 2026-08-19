from __future__ import annotations

import threading
import os
import time
from collections import deque
from datetime import UTC, datetime
from typing import Any


class ModelFeatureStore:
    """Latest causal model fields plus immutable issued-prediction history."""

    def __init__(self, history_limit: int = 100_000) -> None:
        self._lock = threading.RLock()
        self._latest: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self._history: deque[dict[str, Any]] = deque(maxlen=history_limit)
        self._accepted = 0
        self._duplicates = 0
        self._rejected = 0

    def publish(self, update: dict[str, Any]) -> dict[str, Any]:
        ticker = str(update.get("ticker") or "").strip().upper()
        model_id = str(update.get("model_id") or "").strip()
        prediction_id = str(update.get("prediction_id") or "").strip()
        fields = update.get("fields")
        mode = str(update.get("mode") or "").strip()
        scope_id = str(update.get("scope_id") or "").strip()
        event_at_us = int(update.get("event_at_us") or 0)
        available_at_us = int(update.get("available_at_us") or 0)
        if not ticker or not model_id or not prediction_id or not mode or not scope_id or not isinstance(fields, dict):
            with self._lock:
                self._rejected += 1
            raise ValueError("model update requires mode, scope_id, ticker, model_id, prediction_id, and fields")
        if event_at_us <= 0 or available_at_us < event_at_us:
            with self._lock:
                self._rejected += 1
            raise ValueError("model update timestamps violate event_at <= available_at")
        key = (mode, scope_id, ticker, model_id)
        with self._lock:
            prior = self._latest.get(key)
            if prior and prior.get("prediction_id") == prediction_id:
                self._duplicates += 1
                return {"status": "duplicate", "prediction_id": prediction_id}
            if prior and int(prior.get("event_at_us") or 0) > event_at_us:
                self._rejected += 1
                raise ValueError("model update would move the latest causal origin backward")
            normalized = {
                **update,
                "ticker": ticker,
                "model_id": model_id,
                "prediction_id": prediction_id,
                "received_at": datetime.now(UTC).isoformat(),
            }
            self._latest[key] = normalized
            self._history.append(normalized)
            self._accepted += 1
        return {"status": "accepted", "prediction_id": prediction_id}

    def project_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        now_us = time.time_ns() // 1_000
        ttl_us = max(1_000_000, int(os.environ.get("BAR_GPT_FEATURE_TTL_US", "10000000")))
        with self._lock:
            by_ticker: dict[str, dict[str, Any]] = {}
            for (mode, _scope_id, ticker, _model_id), update in self._latest.items():
                if mode not in {"live", "paper"}:
                    continue
                available_at_us = int(update.get("available_at_us") or 0)
                if available_at_us > now_us or now_us - available_at_us > ttl_us:
                    continue
                target = by_ticker.setdefault(ticker, {})
                target.update(update.get("fields") or {})
                target["model.bargpt.latest_prediction_id"] = update["prediction_id"]
                target["model.bargpt.latest_origin_us"] = int(update["event_at_us"])
                target["model.bargpt.latest_available_at_us"] = int(update["available_at_us"])
            return [
                {**row, **by_ticker.get(str(row.get("ticker") or row.get("symbol") or "").upper(), {})}
                for row in rows
            ]

    def scoped_fields(self, *, mode: str, scope_id: str, ticker: str, as_of_us: int) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        ticker = ticker.strip().upper()
        with self._lock:
            for (row_mode, row_scope, row_ticker, _model_id), update in self._latest.items():
                if (row_mode, row_scope, row_ticker) != (mode, scope_id, ticker):
                    continue
                if int(update.get("event_at_us") or 0) > as_of_us:
                    continue
                if int(update.get("available_at_us") or 0) > as_of_us:
                    continue
                fields.update(update.get("fields") or {})
                fields["model.bargpt.latest_prediction_id"] = update["prediction_id"]
                fields["model.bargpt.latest_origin_us"] = int(update["event_at_us"])
                fields["model.bargpt.latest_available_at_us"] = int(update["available_at_us"])
        return fields

    def snapshot(self, ticker: str = "", limit: int = 100) -> dict[str, Any]:
        ticker = ticker.strip().upper()
        with self._lock:
            rows = [
                dict(row) for row in reversed(self._history)
                if not ticker or row["ticker"] == ticker
            ][:max(1, min(limit, 10_000))]
            latest = [
                dict(row) for (_mode, _scope, symbol, _model), row in self._latest.items()
                if not ticker or symbol == ticker
            ]
            return {
                "schema_version": 1,
                "rows": rows,
                "latest": latest,
                "metrics": {
                    "accepted": self._accepted,
                    "duplicates": self._duplicates,
                    "rejected": self._rejected,
                    "latest_count": len(self._latest),
                    "history_count": len(self._history),
                },
            }

    def chart_forecasts(self, ticker: str, model_version: str = "v2", scope_id: str = "") -> dict[str, Any]:
        prefix = f"model.bargpt.{model_version}.physical."
        rows = []
        with self._lock:
            history = [
                row for row in self._history
                if row["ticker"] == ticker.upper()
                and (not scope_id or row.get("scope_id") == scope_id)
            ]
        for update in history:
            fields = dict(update.get("fields") or {})
            grouped: dict[str, dict[str, float]] = {}
            for field_id, value in fields.items():
                if not field_id.startswith(prefix) or not field_id.endswith(".q50.value"):
                    continue
                if value is None:
                    continue
                rest = field_id[len(prefix):].removesuffix(".q50.value")
                try:
                    horizon, target = rest.split(".", 1)
                    family, component, suffix = target.split("_", 2)
                except ValueError:
                    continue
                if family != "trade" or suffix != "return" or component not in {"open", "high", "low", "close"}:
                    continue
                grouped.setdefault(horizon, {})[component] = float(value)
            for horizon, values in grouped.items():
                if set(values) != {"open", "high", "low", "close"}:
                    continue
                rows.append({
                    "prediction_id": update["prediction_id"],
                    "model_id": update["model_id"],
                    "model_version": update["model_version"],
                    "origin_us": int(update["event_at_us"]),
                    "available_at_us": int(update["available_at_us"]),
                    "horizon": horizon,
                    **values,
                    "geometry_valid": values["high"] >= max(values["open"], values["close"])
                    and values["low"] <= min(values["open"], values["close"]),
                })
        return {"schema_version": 1, "ticker": ticker.upper(), "scope_id": scope_id, "rows": rows, "row_count": len(rows)}


MODEL_FEATURE_STORE = ModelFeatureStore()
