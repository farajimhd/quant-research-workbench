from __future__ import annotations

import os
import threading
import time
from datetime import UTC, datetime
from typing import Any, Callable


class MarketDiscoveryRuntimeCoordinator:
    """Own the approved live discovery cycle independently of Canvas polling."""

    def __init__(
        self,
        *,
        health_loader: Callable[[], dict[str, Any]] | None = None,
        configuration_loader: Callable[[], dict[str, Any]] | None = None,
        refresh: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._health_loader = health_loader
        self._configuration_loader = configuration_loader
        self._refresh = refresh
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._status: dict[str, Any] = {
            "running": False,
            "state": "stopped",
            "cycles": 0,
            "failures": 0,
            "last_error": "",
        }

    def start(self) -> None:
        if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("MARKET_DISCOVERY_RUNTIME_ENABLED", "1") == "0":
            with self._lock:
                self._status.update({"running": False, "state": "disabled"})
            return
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="market-discovery-runtime", daemon=True)
            self._status.update({"running": True, "state": "starting"})
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=5)
        with self._lock:
            self._status.update({"running": False, "state": "stopped"})

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._status)

    def refresh_once(self) -> float:
        health = (self._health_loader or self._default_health_loader)()
        configuration = (self._configuration_loader or self._default_configuration_loader)()
        discovery = dict(configuration.get("market_discovery") or {})
        refresh_ms = _refresh_interval_ms(discovery)
        calendar = dict(health.get("market_calendar") or {})
        collecting = bool(calendar.get("active_collection_window")) and bool(health.get("running", True))
        if not collecting:
            with self._lock:
                self._status.update({
                    "state": "market_idle",
                    "market_status": health.get("status"),
                    "refresh_interval_ms": refresh_ms,
                })
            return max(1.0, min(refresh_ms / 1000, 60.0))
        started = time.perf_counter()
        payload = (self._refresh or self._default_refresh)()
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        signal_runtime = dict(payload.get("signal_stream_runtime") or {})
        watchlist_runtime = dict(payload.get("watchlist_runtime") or {})
        cycle_as_of = _aware_datetime(payload.get("as_of")) or datetime.now(UTC)
        from src.backend.news_signal_runtime_service import NEWS_SIGNAL_RUNTIME
        from src.backend.trading_runtime_service import trading_journal
        try:
            news_runtime = NEWS_SIGNAL_RUNTIME.refresh(
                configuration,
                list(payload.get("strategy_rows") or []),
                as_of=cycle_as_of,
                journal=trading_journal(),
            )
        except Exception as exc:
            news_runtime = {
                "status": "degraded",
                "error": str(exc),
                "occurrences": [],
                "new_occurrences": [],
            }
        new_occurrences = [
            *list(signal_runtime.get("new_occurrences") or []),
            *list(news_runtime.get("new_occurrences") or []),
        ]
        from src.trading_runtime.signal_dispatch import dispatchable_strategy_signals
        from src.backend.live_strategy_runtime_service import LIVE_STRATEGY_RUNTIME
        runtime_mode = LIVE_STRATEGY_RUNTIME.mode
        deliveries = dispatchable_strategy_signals(
            configuration,
            new_occurrences,
            watchlist_runtime=watchlist_runtime,
            mode=runtime_mode,
        ) if runtime_mode in {"paper", "live"} else []
        accepted_deliveries = LIVE_STRATEGY_RUNTIME.submit(deliveries)
        accepted_market_updates = LIVE_STRATEGY_RUNTIME.submit_market_rows(
            list(payload.get("strategy_rows") or []),
            as_of=payload.get("as_of") or datetime.now(UTC).isoformat(),
        )
        with self._lock:
            self._status.update({
                "state": "ready",
                "cycles": int(self._status.get("cycles") or 0) + 1,
                "last_cycle_at": datetime.now(UTC).isoformat(),
                "last_duration_ms": duration_ms,
                "last_error": "",
                "core_population_count": int(payload.get("core_population_count") or 0),
                "watchlist_count": len(watchlist_runtime.get("watchlists") or []),
                "signal_stream_count": len(signal_runtime.get("signal_streams") or []),
                "emitted_count": int(signal_runtime.get("occurrence_count") or 0),
                "news_signal_status": str(news_runtime.get("status") or "unknown"),
                "news_signal_error": str(news_runtime.get("error") or ""),
                "news_signal_emitted_count": len(news_runtime.get("occurrences") or []),
                "strategy_delivery_count": len(deliveries),
                "strategy_delivery_accepted_count": accepted_deliveries,
                "strategy_market_update_accepted_count": accepted_market_updates,
                "strategy_deliveries": deliveries[-25:],
                "refresh_interval_ms": refresh_ms,
                "stage_durations_ms": dict(payload.get("stage_durations_ms") or {}),
            })
        return max(0.01, min((refresh_ms - duration_ms) / 1000, 60.0))

    def _run(self) -> None:
        while not self._stop.is_set():
            wait_seconds = 5.0
            try:
                wait_seconds = self.refresh_once()
            except Exception as exc:
                with self._lock:
                    self._status.update({
                        "state": "degraded",
                        "failures": int(self._status.get("failures") or 0) + 1,
                        "last_error": str(exc),
                        "last_failure_at": datetime.now(UTC).isoformat(),
                    })
            self._stop.wait(wait_seconds)

    @staticmethod
    def _default_health_loader() -> dict[str, Any]:
        from src.backend.qmd_gateway_client import qmd_status
        return qmd_status()

    @staticmethod
    def _default_configuration_loader() -> dict[str, Any]:
        from src.backend.trading_configuration_service import market_discovery_runtime_configuration
        return market_discovery_runtime_configuration()

    @staticmethod
    def _default_refresh() -> dict[str, Any]:
        from src.backend.real_live_trading_service import refresh_live_market_discovery
        return refresh_live_market_discovery()


def _refresh_interval_ms(discovery: dict[str, Any]) -> int:
    rows = [dict(discovery.get("core_scan") or {})]
    rows.extend(row for row in discovery.get("watchlists") or [] if bool(row.get("enabled", True)))
    rows.extend(row for row in discovery.get("signal_streams") or [] if bool(row.get("enabled", True)))
    values = [int(row.get("refresh_interval_ms") or 0) for row in rows if int(row.get("refresh_interval_ms") or 0) > 0]
    return max(1_000, min(values or [5_000]))


MARKET_DISCOVERY_RUNTIME = MarketDiscoveryRuntimeCoordinator()


def _aware_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
