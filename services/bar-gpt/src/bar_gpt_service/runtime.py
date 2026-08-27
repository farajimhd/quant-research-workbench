from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import urllib.request
from collections import deque
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from threading import Event, RLock
from typing import Any
from zoneinfo import ZoneInfo

from .cache import CALENDAR_VIEWS, INTRADAY_VIEW_US, CausalCache, RawBar
from .config import ServiceConfig
from .contracts import InferenceRequest, RawBarInput, ScopeRequest
from .decoding import decode_batch
from .models import LoadedRelease, load_releases, prepare_batch, release_summary
from .sources import (
    ConditionReferences,
    HistoricalBootstrap,
    LiveEventBarBuilder,
    consume_qmd_events,
)
from .warm_snapshots import WarmSnapshotStore


class ContextWarmingError(RuntimeError):
    """Retryable inference admission state; not an operational failure."""


class PrioritySemaphore:
    """Bounded gate with priority ordering and a cap for background work."""

    def __init__(self, value: int, *, background_limit: int | None = None) -> None:
        self._capacity = max(1, value)
        self._available = self._capacity
        self._background_limit = min(
            self._capacity,
            max(1, background_limit if background_limit is not None else self._capacity),
        )
        self._active_background = 0
        self._condition = asyncio.Condition()
        self._waiting: dict[int, int] = {}

    @asynccontextmanager
    async def slot(self, priority: int):
        async with self._condition:
            self._waiting[priority] = self._waiting.get(priority, 0) + 1
            try:
                await self._condition.wait_for(
                    lambda: self._available > 0
                    and not any(count > 0 and candidate < priority for candidate, count in self._waiting.items())
                    and (priority == 0 or self._active_background < self._background_limit)
                )
                self._available -= 1
                if priority > 0:
                    self._active_background += 1
            finally:
                self._waiting[priority] -= 1
        try:
            yield
        finally:
            async with self._condition:
                self._available += 1
                if priority > 0:
                    self._active_background -= 1
                self._condition.notify_all()


class BarGptRuntime:
    def __init__(self, config: ServiceConfig) -> None:
        self.config = config
        self.releases: dict[str, LoadedRelease] = {}
        self.caches: dict[str, CausalCache] = {}
        self._cache_config: tuple[dict[str, int], int, int] | None = None
        self.scopes: dict[str, dict[str, Any]] = {}
        self.predictions: deque[dict[str, Any]] = deque(maxlen=config.prediction_history)
        self.latest: dict[tuple[str, str], dict[str, Any]] = {}
        self.metrics: dict[str, int | float] = {
            "inference_requests": 0,
            "inference_batches": 0,
            "predictions": 0,
            "failed_batches": 0,
            "warm_requested": 0,
            "warm_completed": 0,
            "warm_failed": 0,
            "warm_source_revision_checks": 0,
            "backend_delivered": 0,
            "backend_failed": 0,
            "queue_dropped": 0,
            "event_log_write_failures": 0,
        }
        self.failures: deque[dict[str, Any]] = deque(maxlen=100)
        self.qmd_state = {"status": "disabled", "error": "", "updated_at": ""}
        self.backend_state = {"status": "unknown", "error": "", "updated_at": ""}
        self.started_at = ""
        self._queue: asyncio.Queue[tuple[str, str, int]] = asyncio.Queue(maxsize=config.queue_capacity)
        self._queued: set[tuple[str, str, int]] = set()
        self._deferred_auto: dict[tuple[str, str], int] = {}
        self._warm_tasks: dict[tuple[str, str], asyncio.Task[None]] = {}
        self._warm_state: dict[tuple[str, str], dict[str, Any]] = {}
        self._warm_durations: deque[float] = deque(maxlen=100)
        self._retained_tickers: dict[tuple[str, str], float] = {}
        self._tasks: list[asyncio.Task[Any]] = []
        self._builder: LiveEventBarBuilder | None = None
        self._live_builder_lock = asyncio.Lock()
        self._lock = RLock()
        self._listeners: set[asyncio.Queue[dict[str, Any]]] = set()
        # Direct-event historical warm-up is ClickHouse and memory intensive.
        # Keep broad Live discovery to one raw scan while leaving the remaining
        # configured capacity available to bounded interactive scopes.
        self._warm_gate = PrioritySemaphore(config.warm_concurrency, background_limit=1)
        self._stop_requested = Event()
        self._pending_historical: dict[tuple[str, str], list[RawBar]] = {}
        self._snapshot_store: WarmSnapshotStore | None = None
        self._event_lock = RLock()
        self.event_log_state = {
            "status": "unknown", "error": "", "last_error": "",
            "failure_count": 0, "updated_at": "",
        }
        self._last_qmd_event_at_us = 0
        self._shadow_sample_rate = min(1.0, max(0.0, float(os.environ.get("BAR_GPT_SHADOW_SAMPLE_RATE", "0.05"))))
        self._shadow_max_tickers = max(0, int(os.environ.get("BAR_GPT_SHADOW_MAX_TICKERS", "4")))
        self._scope_retention_seconds = max(0.0, float(os.environ.get("BAR_GPT_SCOPE_RETENTION_SECONDS", "300")))

    async def start(self) -> None:
        self._stop_requested.clear()
        runtime_parent = self.config.runtime_root.parent
        if not runtime_parent.exists():
            raise RuntimeError(f"required runtime root is unavailable: {runtime_parent}")
        self.config.runtime_root.mkdir(parents=True, exist_ok=True)
        self.releases = await asyncio.to_thread(load_releases, self.config)
        if self.releases:
            authority = next(iter(self.releases.values()))
            self._validate_release_contexts(authority)
            capacities = {
                **authority.data_config.intraday_context_by_name,
                **authority.data_config.calendar_context_by_name,
            }
            capacities["1s"] = int(capacities["1s"]) + 1
            self._cache_config = (
                capacities, int(authority.data_config.intraday_warmup_bars_1s),
                int(authority.data_config.calendar_warmup_daily_bars),
            )
            self._snapshot_store = WarmSnapshotStore(
                self.config.runtime_root / "warm_snapshots", authority.contract_hash
            )
            self.caches["live"] = self._new_cache()
            if self.config.connect_qmd:
                self._tasks.append(
                    asyncio.create_task(
                        self._live_dependency_loop(authority),
                        name="bar-gpt-live-dependencies",
                    )
                )
            else:
                self._set_qmd_state("disabled", "BAR_GPT_CONNECT_QMD is false")
            self._tasks.append(asyncio.create_task(self._batch_loop(), name="bar-gpt-batcher"))
            self._tasks.append(asyncio.create_task(self._scope_reaper(), name="bar-gpt-scope-reaper"))
        self.started_at = datetime.now(UTC).isoformat()
        self._record_event("service_started", {"models": list(self.releases)})

    async def stop(self) -> None:
        self._record_event("service_stopping", {})
        # Cancelling an asyncio task does not interrupt a synchronous
        # ClickHouse request already running in a worker thread. Signal the
        # history loader too so it stops between bounded requests instead of
        # continuing the complete warm plan during shutdown.
        self._stop_requested.set()
        for task in (*self._warm_tasks.values(), *self._tasks):
            task.cancel()
        await asyncio.gather(*self._warm_tasks.values(), *self._tasks, return_exceptions=True)
        self._warm_tasks.clear()
        self._tasks.clear()

    def health(self) -> dict[str, Any]:
        active = self.active_tickers()
        now_us = time.time_ns() // 1_000
        warm = self._warm_summary(active, now_us)
        live_auto = any(
            row["request"]["mode"] in {"live", "paper"}
            and row["request"]["trigger_mode"] == "auto"
            for row in self.active_scopes().values()
        )
        status = "ready"
        message = "BarGPT releases are loaded and available for causal inference."
        if not self.releases:
            status = "blocked"
            message = "No enabled BarGPT checkpoint releases are configured."
        elif warm["failed"]:
            status = "degraded"
            message = f"BarGPT warm-up failed for {warm['failed']} admitted ticker(s)."
        elif self.event_log_state["status"] == "failed":
            status = "degraded"
            message = "BarGPT durable lifecycle-event logging is unavailable."
        elif live_auto and self.qmd_state["status"] not in {"streaming", "idle"}:
            status = "degraded"
            message = "Live automatic serving is scoped but the QMD stream is not healthy."
        elif warm["ready"] < warm["admitted"]:
            status = "warming"
            message = (
                f"BarGPT context is ready for {warm['ready']} of {warm['admitted']} admitted ticker(s)."
            )
        qmd = dict(self.qmd_state)
        qmd["last_event_at_us"] = self._last_qmd_event_at_us
        qmd["freshness_ms"] = (
            max(0, (now_us - self._last_qmd_event_at_us) // 1_000)
            if self._last_qmd_event_at_us else None
        )
        return {
            "service": "bar_gpt",
            "status": status,
            "message": message,
            "responsibility": "causal BarGPT predictions only; no rule, strategy, risk, or order authority",
            "started_at": self.started_at,
            "models": [release_summary(row) for row in self.releases.values()],
            "scope_count": len(self.active_scopes()),
            "active_ticker_count": len(active),
            "maximum_tickers": self.config.maximum_tickers,
            "queue": {"active": self._queue.qsize(), "capacity": self._queue.maxsize},
            "warm": warm,
            "caches": {
                key: (
                    value.health_summary()
                    if hasattr(value, "health_summary") else value.summary()
                )
                for key, value in self.caches.items()
            },
            "qmd": qmd,
            "backend": dict(self.backend_state),
            "event_log": dict(self.event_log_state),
            "metrics": dict(self.metrics),
            "active_failures": list(self.failures)[-10:],
        }

    async def replace_scope(self, scope_id: str, request: ScopeRequest) -> dict[str, Any]:
        scope_id = scope_id.strip()
        if not scope_id:
            raise ValueError("scope_id cannot be empty")
        if len(request.tickers) > self.config.maximum_tickers:
            raise ValueError(
                f"scope requests {len(request.tickers)} tickers; BAR_GPT_MAX_TICKERS is {self.config.maximum_tickers}"
            )
        resolved_models = self._resolve_model_ids(request.model_ids)
        now = time.monotonic()
        prior = self.active_scopes().get(scope_id)
        prior_tickers = list((prior or {}).get("request", {}).get("tickers") or [])
        cache_id = "live" if request.mode in {"live", "paper"} else scope_id
        request_payload = request.model_dump()
        request_payload["model_ids"] = resolved_models
        row = {
            "scope_id": scope_id,
            "request": request_payload,
            "created_at": str((prior or {}).get("created_at") or datetime.now(UTC).isoformat()),
            "updated_at": datetime.now(UTC).isoformat(),
            "expires_monotonic": now + request.ttl_ms / 1000.0,
            "cache_id": cache_id,
        }
        with self._lock:
            self.scopes[scope_id] = row
        self.caches.setdefault(cache_id, self._new_cache())
        added = [ticker for ticker in request.tickers if ticker not in prior_tickers]
        removed = [ticker for ticker in prior_tickers if ticker not in request.tickers]
        for ticker in added:
            self._retained_tickers.pop((cache_id, ticker), None)
        for ticker in removed:
            self._retained_tickers[(cache_id, ticker)] = now + self._scope_retention_seconds
        self._trim_retained()
        for ticker in request.tickers:
            self._promote_pending(cache_id, ticker, request.clock_us)
            self.request_warm(cache_id, ticker, request.clock_us)
            if request.clock_us and self._cache(cache_id).readiness(ticker, request.clock_us, self.config.minimum_warm_1s_bars)["ready"]:
                self._schedule_auto(cache_id, ticker, request.clock_us)
        self._reclaim_cache()
        if prior is None or added or removed or list((prior or {}).get("request", {}).get("model_ids") or []) != resolved_models:
            self._record_event("scope_updated", {
                "scope_id": scope_id, "added": added, "removed": removed, "model_ids": resolved_models,
            })
        return self.scope_snapshot(scope_id)

    async def advance_scope(self, scope_id: str, request: ScopeRequest) -> dict[str, Any]:
        """Advance a historical clock and return its prediction before the caller proceeds."""
        if request.clock_us is None:
            raise ValueError("a synchronous scope advance requires clock_us")
        manual_request = request.model_copy(update={"trigger_mode": "manual"})
        await self.replace_scope(scope_id, manual_request)
        cache_id = self._scope_cache_id(scope_id)
        warm_tasks = [
            task for (task_cache_id, ticker), task in list(self._warm_tasks.items())
            if task_cache_id == cache_id and ticker in request.tickers
        ]
        if warm_tasks:
            await asyncio.gather(*warm_tasks)
        for ticker in request.tickers:
            self._promote_pending(cache_id, ticker, request.clock_us)
        snapshot = self.scope_snapshot(scope_id)
        if int(snapshot["ready_count"]) != int(snapshot["ticker_count"]):
            missing = [row["ticker"] for row in snapshot["readiness"] if not row["ready"]]
            raise RuntimeError("BarGPT synchronous advance is not warm for: " + ",".join(missing))
        predictions = await self.infer(
            InferenceRequest(
                scope_id=scope_id, tickers=request.tickers,
                model_ids=list(snapshot["request"].get("model_ids") or []),
                origin_us=request.clock_us,
            )
        )
        with self._lock:
            active = self.scopes.get(scope_id)
            if active is not None:
                active["request"]["trigger_mode"] = request.trigger_mode
        return {**self.scope_snapshot(scope_id), "predictions": predictions, "prediction_count": len(predictions)}

    def remove_scope(self, scope_id: str) -> bool:
        with self._lock:
            row = self.scopes.pop(scope_id, None)
        if row is not None:
            deadline = time.monotonic() + self._scope_retention_seconds
            for ticker in row["request"]["tickers"]:
                self._retained_tickers[(str(row["cache_id"]), ticker)] = deadline
            self._trim_retained()
        self._reclaim_cache()
        return row is not None

    def active_scopes(self) -> dict[str, dict[str, Any]]:
        now = time.monotonic()
        with self._lock:
            return {
                key: dict(value)
                for key, value in self.scopes.items()
                if float(value["expires_monotonic"]) > now
            }

    def active_tickers(self, cache_id: str | None = None) -> set[str]:
        return {
            ticker
            for row in self.active_scopes().values()
            if cache_id is None or row["cache_id"] == cache_id
            for ticker in row["request"]["tickers"]
        }

    def scope_snapshot(self, scope_id: str) -> dict[str, Any]:
        row = self.active_scopes().get(scope_id)
        if row is None:
            raise KeyError(scope_id)
        clock_us = int(row["request"].get("clock_us") or time.time_ns() // 1_000)
        cache = self._cache(str(row["cache_id"]))
        readiness = [
            {
                **cache.readiness(ticker, clock_us, self.config.minimum_warm_1s_bars),
                "warm": dict(self._warm_state.get((str(row["cache_id"]), ticker), {})),
            }
            for ticker in row["request"]["tickers"]
        ]
        return {
            **row,
            "expires_in_ms": max(0, int((float(row["expires_monotonic"]) - time.monotonic()) * 1000)),
            "readiness": readiness,
            "ready_count": sum(bool(item["ready"]) for item in readiness),
            "ticker_count": len(readiness),
        }

    def request_warm(self, cache_id: str, ticker: str, clock_us: int | None) -> None:
        ticker = ticker.upper()
        key = (cache_id, ticker)
        if not self.releases or key in self._warm_tasks:
            return
        origin = int(clock_us or time.time_ns() // 1_000)
        if self._cache(cache_id).readiness(ticker, origin, self.config.minimum_warm_1s_bars)["ready"]:
            self._warm_state[key] = {"status": "ready", "origin_us": origin, "updated_at": datetime.now(UTC).isoformat()}
            return
        self.metrics["warm_requested"] = int(self.metrics["warm_requested"]) + 1
        self._warm_state[key] = {
            "status": "queued", "origin_us": origin, "queued_at": datetime.now(UTC).isoformat(),
        }
        task = asyncio.create_task(self._warm(cache_id, ticker, origin), name=f"bar-gpt-warm-{cache_id}-{ticker}")
        self._warm_tasks[key] = task
        task.add_done_callback(lambda _task, task_key=key: self._warm_tasks.pop(task_key, None))

    async def ingest_bars(self, scope_id: str, inputs: list[RawBarInput]) -> dict[str, Any]:
        cache_id = self._scope_cache_id(scope_id)
        cache = self._cache(cache_id)
        bars = [RawBar(**row.model_dump()) for row in inputs]
        counts = cache.upsert_many(bars)
        for bar in bars:
            if bar.view == "1s" and bar.ticker in self.active_tickers(cache_id):
                self._schedule_auto(cache_id, bar.ticker, bar.available_at_us)
        return {"status": "accepted", "scope_id": scope_id, "counts": counts, "cache": cache.summary()}

    async def infer(self, request: InferenceRequest) -> list[dict[str, Any]]:
        scope_id = request.scope_id or self._default_scope_id()
        cache_id = self._scope_cache_id(scope_id)
        scope = self.active_scopes().get(scope_id)
        if scope is None:
            raise KeyError(f"unknown or expired BarGPT scope {scope_id!r}")
        tickers = request.tickers or list(scope["request"]["tickers"])
        if not tickers:
            raise RuntimeError(f"BarGPT scope {scope_id!r} has no active tickers")
        outside_scope = sorted(set(tickers) - set(scope["request"]["tickers"]))
        if outside_scope:
            raise RuntimeError(
                f"BarGPT inference tickers are outside scope {scope_id!r}: {','.join(outside_scope)}"
            )
        readiness_origin = int(request.origin_us or scope["request"].get("clock_us") or time.time_ns() // 1_000)
        not_ready = [
            ticker for ticker in tickers
            if not self._cache(cache_id).readiness(
                ticker, readiness_origin, self.config.minimum_warm_1s_bars
            )["ready"]
        ]
        if not_ready:
            raise ContextWarmingError(
                "BarGPT context is still warming for: " + ",".join(not_ready)
            )
        explicit_models = self._resolve_model_ids(request.model_ids) if request.model_ids else []
        selected_models = explicit_models or list(scope["request"].get("model_ids") or [])
        plan = self._inference_plan(
            selected_models, tickers, readiness_origin,
            automatic=not explicit_models and scope["request"].get("trigger_mode") == "auto",
        )
        self.metrics["inference_requests"] = int(self.metrics["inference_requests"]) + 1
        results: list[dict[str, Any]] = []
        for model_id, model_tickers in plan:
            for start in range(0, len(model_tickers), self.config.maximum_batch_size):
                chunk = model_tickers[start:start + self.config.maximum_batch_size]
                release = self.releases[model_id]
                try:
                    predictions = await asyncio.to_thread(
                        self._infer_sync, release, self._cache(cache_id), chunk, request.origin_us
                    )
                except ValueError as exc:
                    self._record_failure("inference_not_ready", str(exc))
                    raise RuntimeError(
                        f"BarGPT inference batch is not causally ready for {model_id}: {exc}"
                    ) from exc
                except Exception as exc:
                    self.metrics["failed_batches"] = int(self.metrics["failed_batches"]) + 1
                    self._record_failure("inference_failed", str(exc))
                    raise
                self.metrics["inference_batches"] = int(self.metrics["inference_batches"]) + 1
                for prediction in predictions:
                    prediction["scope_id"] = scope_id
                    mode = self.active_scopes().get(scope_id, {}).get("request", {}).get("mode", "live")
                    prediction["mode"] = mode
                    if mode in {"live", "paper"}:
                        prediction["available_at_us"] = max(
                            int(prediction["event_at_us"]), time.time_ns() // 1_000
                        )
                    await self._record_prediction(prediction)
                results.extend(predictions)
        return results

    def prediction_snapshot(self, ticker: str = "", limit: int = 100) -> dict[str, Any]:
        selected = [
            row for row in reversed(self.predictions)
            if not ticker or row["ticker"] == ticker.upper()
        ][:max(1, min(limit, 10_000))]
        return {"schema_version": 1, "rows": selected, "row_count": len(selected)}

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1024)
        self._listeners.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._listeners.discard(queue)

    async def _warm(self, cache_id: str, ticker: str, origin_us: int) -> None:
        key = (cache_id, ticker)
        started = time.monotonic()
        try:
            # Historical/Replays are interactive bounded scopes. They must not
            # wait behind hundreds of opportunistic Live watchlist warm-ups.
            async with self._warm_gate.slot(priority=0 if cache_id != "live" else 1):
                self._warm_state[key] = {
                    **self._warm_state.get(key, {}), "status": "warming",
                    "started_at": datetime.now(UTC).isoformat(),
                }
                release = self._champion_release()
                bootstrap = HistoricalBootstrap(release)
                as_of = datetime.fromtimestamp(origin_us / 1_000_000, tz=UTC)
                source_revision = await asyncio.to_thread(bootstrap.source_revision, ticker, as_of)
                self.metrics["warm_source_revision_checks"] = int(
                    self.metrics["warm_source_revision_checks"]
                ) + 1
                snapshot_rows = (
                    await asyncio.to_thread(
                        self._snapshot_store.load, ticker, origin_us, source_revision
                    )
                    if self._snapshot_store is not None else []
                )
                bars = await asyncio.to_thread(
                    bootstrap.load,
                    ticker,
                    as_of,
                    include_calendar=not bool(snapshot_rows),
                    stop_requested=self._stop_requested.is_set,
                )
                if cache_id != "live":
                    one_second = [
                        bar for bar in bars
                        if bar.view == "1s" and bar.available_at_us <= origin_us
                    ]
                    requested_session = as_of.astimezone(
                        ZoneInfo("America/New_York")
                    ).date()
                    observed_session = (
                        datetime.fromtimestamp(
                            one_second[-1].available_at_us / 1_000_000, tz=UTC
                        ).astimezone(ZoneInfo("America/New_York")).date()
                        if one_second else None
                    )
                    if observed_session != requested_session:
                        supplemental = await asyncio.to_thread(
                            bootstrap.load_current_session, ticker, as_of
                        )
                        if supplemental:
                            bars.extend(supplemental)
                            one_second = [
                                bar for bar in bars
                                if bar.view == "1s" and bar.available_at_us <= origin_us
                            ]
                            one_second.sort(
                                key=lambda bar: (bar.available_at_us, bar.bar_start_us)
                            )
                            observed_session = (
                                datetime.fromtimestamp(
                                    one_second[-1].available_at_us / 1_000_000,
                                    tz=UTC,
                                ).astimezone(ZoneInfo("America/New_York")).date()
                                if one_second else None
                            )
                    if observed_session != requested_session:
                        observed = observed_session.isoformat() if observed_session else "none"
                        raise RuntimeError(
                            "QMD history has no BarGPT intraday context for requested "
                            f"session {requested_session.isoformat()}; latest available session is {observed}"
                        )
                confirmed_revision = await asyncio.to_thread(bootstrap.source_revision, ticker, as_of)
                self.metrics["warm_source_revision_checks"] = int(
                    self.metrics["warm_source_revision_checks"]
                ) + 1
                if confirmed_revision != source_revision:
                    raise RuntimeError("QMD history source revision changed during BarGPT warm-up")
                current = [bar for bar in bars if bar.available_at_us <= origin_us]
                future = sorted(
                    (bar for bar in bars if bar.available_at_us > origin_us),
                    key=lambda bar: (bar.available_at_us, bar.view, bar.bar_start_us),
                )
                await self._admit_warm_rows(cache_id, [*snapshot_rows, *current])
                if cache_id != "live" and future:
                    self._pending_historical[(cache_id, ticker)] = future
                self.metrics["warm_completed"] = int(self.metrics["warm_completed"]) + 1
                duration = time.monotonic() - started
                self._warm_durations.append(duration)
                self._warm_state[key] = {
                    "status": "ready", "origin_us": origin_us, "duration_seconds": round(duration, 3),
                    "snapshot_hit": bool(snapshot_rows), "updated_at": datetime.now(UTC).isoformat(),
                }
                if self._snapshot_store is not None:
                    calendar = self._cache(cache_id).snapshot_rows(ticker, views=CALENDAR_VIEWS)
                    await asyncio.to_thread(
                        self._snapshot_store.save, ticker, origin_us, calendar, confirmed_revision
                    )
                self._record_event("warm_completed", {
                    "cache_id": cache_id, "ticker": ticker, "duration_seconds": round(duration, 3),
                    "snapshot_hit": bool(snapshot_rows),
                })
                deferred_origin = self._deferred_auto.get((cache_id, ticker))
                if deferred_origin is not None:
                    self._schedule_auto(cache_id, ticker, deferred_origin)
                for scope_id, row in self.active_scopes().items():
                    if row["cache_id"] == cache_id and ticker in row["request"]["tickers"] and row["request"].get("clock_us"):
                        self._schedule_auto(cache_id, ticker, int(row["request"]["clock_us"]))
        except asyncio.CancelledError:
            self._warm_state[key] = {
                **self._warm_state.get(key, {}), "status": "cancelled",
                "updated_at": datetime.now(UTC).isoformat(),
            }
            raise
        except Exception as exc:
            self.metrics["warm_failed"] = int(self.metrics["warm_failed"]) + 1
            self._warm_state[key] = {
                "status": "failed", "origin_us": origin_us, "error": str(exc),
                "updated_at": datetime.now(UTC).isoformat(),
            }
            self._record_failure("warm_failed", f"{ticker}: {exc}")

    async def _admit_warm_rows(self, cache_id: str, rows: list[RawBar]) -> None:
        """Admit a complete causal context without monopolizing the event loop/cache lock."""
        cache = self._cache(cache_id)
        chunk_size = 2_048
        for start in range(0, len(rows), chunk_size):
            await asyncio.to_thread(
                cache.upsert_many, rows[start:start + chunk_size], derive=False
            )
            await asyncio.sleep(0)

    def _infer_sync(self, release: LoadedRelease, cache: CausalCache, tickers: list[str], origin_us: int | None) -> list[dict[str, Any]]:
        batch = prepare_batch(release, cache, tickers, origin_us)
        output = release.forward(batch)
        return decode_batch(release, batch, output)

    async def _record_prediction(self, prediction: dict[str, Any]) -> None:
        self.predictions.append(prediction)
        self.latest[(prediction["ticker"], prediction["model_id"])] = prediction
        self.metrics["predictions"] = int(self.metrics["predictions"]) + 1
        await asyncio.to_thread(self._append_prediction, prediction)
        for queue in list(self._listeners):
            try:
                queue.put_nowait(prediction)
            except asyncio.QueueFull:
                self._listeners.discard(queue)
        asyncio.create_task(self._publish_backend(prediction))

    def _append_prediction(self, prediction: dict[str, Any]) -> None:
        day = datetime.now(UTC).date().isoformat()
        path = self.config.runtime_root / "predictions" / f"{day}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(prediction, separators=(",", ":"), allow_nan=False) + "\n")

    async def _publish_backend(self, prediction: dict[str, Any]) -> None:
        payload = {
            key: prediction[key]
            for key in (
                "schema_version", "prediction_id", "ticker", "event_at_us", "available_at_us",
                "model_id", "model_version", "checkpoint_hash", "scope_id", "mode", "fields", "raw",
            )
        }
        try:
            await asyncio.to_thread(
                _post_json,
                f"{self.config.backend_url}/api/model-features/updates",
                payload,
                2.0,
            )
            self.metrics["backend_delivered"] = int(self.metrics["backend_delivered"]) + 1
            self.backend_state = {"status": "ready", "error": "", "updated_at": datetime.now(UTC).isoformat()}
        except Exception as exc:
            self.metrics["backend_failed"] = int(self.metrics["backend_failed"]) + 1
            self.backend_state = {"status": "degraded", "error": str(exc), "updated_at": datetime.now(UTC).isoformat()}

    async def _qmd_loop(self) -> None:
        await consume_qmd_events(
            self.config.qmd_ws_url,
            lambda: self.active_tickers("live"),
            self._on_qmd_events,
            self._set_qmd_state,
        )

    async def _live_dependency_loop(self, authority: LoadedRelease) -> None:
        """Recover the reference authority before starting the live event stream."""
        retry_seconds = 5.0
        while True:
            try:
                self._set_qmd_state("initializing", "loading condition references")
                references = await asyncio.to_thread(
                    ConditionReferences.load,
                    authority.data_config.database,
                    authority.data_config.condition_reference_table,
                )
                self._builder = LiveEventBarBuilder(
                    references, authority.data_config.max_quote_spread_bps
                )
                await asyncio.gather(self._qmd_loop(), self._clock_loop())
                raise RuntimeError("BarGPT live dependency tasks stopped unexpectedly")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._builder = None
                self._set_qmd_state("retrying", str(exc))
                self._record_failure("live_dependency_retry", str(exc))
                await asyncio.sleep(retry_seconds)

    async def _on_qmd_events(self, events: list[dict[str, Any]]) -> None:
        if self._builder is None:
            return
        if events:
            self._last_qmd_event_at_us = max(
                self._last_qmd_event_at_us,
                max(int(row.get("received_at_us") or row.get("sip_timestamp_us") or 0) for row in events),
            )
            self._set_qmd_state("streaming", "")
        active = self.active_tickers("live")
        async with self._live_builder_lock:
            corrections = await asyncio.to_thread(
                self._apply_live_events_sync, events, active
            )
        for bar in corrections:
            self._schedule_auto("live", bar.ticker, bar.available_at_us)

    async def _clock_loop(self) -> None:
        while True:
            await asyncio.sleep(0.05)
            if self._builder is None:
                continue
            async with self._live_builder_lock:
                bars = await asyncio.to_thread(
                    self._flush_live_bars_sync, self._serving_clock_us()
                )
            if not bars:
                continue
            for bar in bars:
                self._schedule_auto("live", bar.ticker, bar.available_at_us)

    def _apply_live_events_sync(
        self, events: list[dict[str, Any]], active: set[str]
    ) -> list[RawBar]:
        builder = self._builder
        if builder is None:
            return []
        corrections: list[RawBar] = []
        for event in events:
            corrections.extend(builder.apply(event, active))
        if corrections:
            self._cache("live").upsert_many(corrections)
        return corrections

    def _flush_live_bars_sync(self, clock_us: int) -> list[RawBar]:
        builder = self._builder
        if builder is None:
            return []
        bars = builder.flush(clock_us)
        if bars:
            self._cache("live").upsert_many(bars)
        return bars

    def _schedule_auto(self, cache_id: str, ticker: str, origin_us: int) -> None:
        eligible = any(
            ticker in row["request"]["tickers"]
            and row["cache_id"] == cache_id
            and row["request"]["trigger_mode"] == "auto"
            and int(row["request"].get("clock_us") or origin_us) >= origin_us
            for row in self.active_scopes().values()
        )
        if not eligible:
            return
        if not self._cache(cache_id).readiness(
            ticker, origin_us, self.config.minimum_warm_1s_bars
        )["ready"]:
            deferred_key = (cache_id, ticker)
            self._deferred_auto[deferred_key] = max(
                origin_us, self._deferred_auto.get(deferred_key, 0)
            )
            return
        deferred_key = (cache_id, ticker)
        scheduled_origin = max(origin_us, self._deferred_auto.get(deferred_key, 0))
        key = (cache_id, ticker, scheduled_origin)
        if key in self._queued:
            self._deferred_auto.pop(deferred_key, None)
            return
        try:
            self._queue.put_nowait(key)
            self._queued.add(key)
            self._deferred_auto.pop(deferred_key, None)
        except asyncio.QueueFull:
            self.metrics["queue_dropped"] = int(self.metrics["queue_dropped"]) + 1
            self._record_failure("inference_queue_full", f"{ticker}@{origin_us}")

    async def _batch_loop(self) -> None:
        while True:
            first = await self._queue.get()
            items = [first]
            deadline = time.monotonic() + self.config.maximum_batch_delay_ms / 1000.0
            try:
                while len(items) < self.config.maximum_batch_size:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    try:
                        items.append(await asyncio.wait_for(self._queue.get(), timeout=remaining))
                    except TimeoutError:
                        break
                by_origin: dict[tuple[str, int], list[str]] = {}
                for cache_id, ticker, origin in items:
                    by_origin.setdefault((cache_id, origin), []).append(ticker)
                for (cache_id, origin), tickers in by_origin.items():
                    try:
                        scope_id = next(
                            key for key, row in self.active_scopes().items()
                            if row["cache_id"] == cache_id and row["request"]["trigger_mode"] == "auto"
                        )
                        await self.infer(InferenceRequest(
                            scope_id=scope_id, tickers=list(dict.fromkeys(tickers)), origin_us=origin
                        ))
                    except ContextWarmingError:
                        # Admission normally gates this path. A concurrent scope/cache
                        # transition remains retryable and must not pollute failure logs.
                        pass
                    except Exception as exc:
                        self._record_failure("automatic_inference_failed", str(exc))
            finally:
                for item in items:
                    self._queued.discard(item)
                    self._queue.task_done()

    async def _scope_reaper(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            now = time.monotonic()
            with self._lock:
                expired = [key for key, value in self.scopes.items() if float(value["expires_monotonic"]) <= now]
                for key in expired:
                    row = self.scopes.pop(key)
                    for ticker in row["request"]["tickers"]:
                        self._retained_tickers[(str(row["cache_id"]), ticker)] = now + self._scope_retention_seconds
            if expired:
                self._trim_retained()
                self._reclaim_cache()

    def _reclaim_cache(self) -> None:
        active_cache_ids = {str(row["cache_id"]) for row in self.active_scopes().values()} | {"live"}
        active_by_cache = {
            cache_id: self.active_tickers(cache_id) | {
                ticker for (retained_cache_id, ticker), deadline in self._retained_tickers.items()
                if retained_cache_id == cache_id and deadline > time.monotonic()
            }
            for cache_id in active_cache_ids
        }
        for (cache_id, ticker), task in list(self._warm_tasks.items()):
            if ticker not in active_by_cache.get(cache_id, set()):
                task.cancel()
        for cache_id, ticker in list(self._deferred_auto):
            if ticker not in active_by_cache.get(cache_id, set()):
                del self._deferred_auto[(cache_id, ticker)]
        for cache_id in list(self.caches):
            if cache_id not in active_cache_ids:
                del self.caches[cache_id]
                for key in [key for key in self._pending_historical if key[0] == cache_id]:
                    del self._pending_historical[key]
            else:
                self.caches[cache_id].evict_except(active_by_cache[cache_id])

    def _new_cache(self) -> CausalCache:
        if self._cache_config is None:
            raise RuntimeError("BarGPT cache authority is unavailable")
        return CausalCache(*self._cache_config)

    def _promote_pending(self, cache_id: str, ticker: str, clock_us: int | None) -> None:
        if clock_us is None:
            return
        key = (cache_id, ticker.upper())
        pending = self._pending_historical.get(key)
        if not pending:
            return
        split = 0
        while split < len(pending) and pending[split].available_at_us <= clock_us:
            split += 1
        if split:
            self._cache(cache_id).upsert_many(pending[:split], derive=False)
            remaining = pending[split:]
            if remaining:
                self._pending_historical[key] = remaining
            else:
                del self._pending_historical[key]

    def _cache(self, cache_id: str) -> CausalCache:
        cache = self.caches.get(cache_id)
        if cache is None:
            raise RuntimeError(f"BarGPT cache scope {cache_id!r} is unavailable")
        return cache

    def _scope_cache_id(self, scope_id: str) -> str:
        if scope_id == "live":
            return "live"
        row = self.active_scopes().get(scope_id)
        if row is None:
            raise KeyError(f"unknown or expired BarGPT scope {scope_id!r}")
        return str(row["cache_id"])

    def _default_scope_id(self) -> str:
        scopes = self.active_scopes()
        if len(scopes) == 1:
            return next(iter(scopes))
        if any(row["cache_id"] == "live" for row in scopes.values()):
            return next(key for key, row in scopes.items() if row["cache_id"] == "live")
        raise ValueError("scope_id is required when multiple BarGPT cache authorities are active")

    def _serving_clock_us(self) -> int:
        return time.time_ns() // 1_000

    def _set_qmd_state(self, status: str, error: str) -> None:
        self.qmd_state = {"status": status, "error": error, "updated_at": datetime.now(UTC).isoformat()}

    def _record_failure(self, code: str, message: str) -> None:
        row = {"code": code, "message": message, "at": datetime.now(UTC).isoformat()}
        self.failures.append(row)
        self._record_event("failure", row)

    def _record_event(self, event: str, detail: dict[str, Any]) -> None:
        row = {"schema_version": 1, "event": event, "at": datetime.now(UTC).isoformat(), **detail}
        path = self.config.runtime_root / "events" / f"{datetime.now(UTC).date().isoformat()}.jsonl"
        try:
            with self._event_lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n")
            self.event_log_state = {
                **self.event_log_state,
                "status": "ready", "error": "", "updated_at": datetime.now(UTC).isoformat()
            }
        except OSError as exc:
            self.metrics["event_log_write_failures"] = int(self.metrics["event_log_write_failures"]) + 1
            self.event_log_state = {
                "status": "failed", "error": str(exc), "last_error": str(exc),
                "failure_count": int(self.metrics["event_log_write_failures"]),
                "updated_at": datetime.now(UTC).isoformat(),
            }

    def _champion_release(self) -> LoadedRelease:
        return next(
            (release for release in self.releases.values() if release.config.role == "champion"),
            next(iter(self.releases.values())),
        )

    def _resolve_model_ids(self, selectors: list[str]) -> list[str]:
        if not selectors:
            return list(self.releases)
        resolved: list[str] = []
        for selector in selectors:
            matches = [
                model_id for model_id, release in self.releases.items()
                if selector == model_id or selector.lower() in {release.config.version, f"bar_gpt_{release.config.version}"}
            ]
            if len(matches) != 1:
                raise ValueError(f"unknown or ambiguous immutable BarGPT release selector: {selector}")
            if matches[0] not in resolved:
                resolved.append(matches[0])
        return resolved

    def _inference_plan(
        self, model_ids: list[str], tickers: list[str], origin_us: int, *, automatic: bool
    ) -> list[tuple[str, list[str]]]:
        selected = model_ids or list(self.releases)
        unknown = sorted(set(selected) - set(self.releases))
        if unknown:
            raise KeyError(f"unknown BarGPT model ids: {','.join(unknown)}")
        if not automatic:
            return [(model_id, tickers) for model_id in selected]
        champion = self._champion_release().config.model_id
        plan: list[tuple[str, list[str]]] = [(champion, tickers)]
        explicit_shadow = (
            len(selected) == 1 and self.releases[selected[0]].config.role == "shadow"
        )
        for model_id in selected:
            release = self.releases[model_id]
            if model_id == champion or release.config.role != "shadow" or self._shadow_max_tickers <= 0:
                continue
            sampled = tickers if explicit_shadow else [
                ticker for ticker in tickers
                if int(hashlib.sha256(f"{model_id}:{ticker}:{origin_us // 1_000_000}".encode()).hexdigest(), 16)
                / (2**256 - 1) < self._shadow_sample_rate
            ]
            sampled = sampled[:self._shadow_max_tickers]
            if sampled:
                plan.append((model_id, sampled))
        return plan

    def _warm_summary(self, active: set[str], origin_us: int) -> dict[str, Any]:
        admitted = {
            (str(row["cache_id"]), ticker)
            for row in self.active_scopes().values()
            for ticker in row["request"]["tickers"]
        }
        states = [self._warm_state.get(key, {}) for key in admitted]
        # Warm state becomes ready only after the complete causal context has
        # been admitted. Re-reading every ticker cache here makes health depend
        # on data-plane locks and can falsely report the service as down.
        ready = sum(self._warm_state.get(key, {}).get("status") == "ready" for key in admitted)
        queued = sum(row.get("status") == "queued" for row in states)
        warming = sum(row.get("status") == "warming" for row in states)
        failed = sum(row.get("status") == "failed" for row in states)
        mean = sum(self._warm_durations) / len(self._warm_durations) if self._warm_durations else 0.0
        remaining = max(0, len(admitted) - ready - failed)
        now = datetime.now(UTC)

        def oldest_age(field: str, selected_status: str) -> float | None:
            ages = [
                max(0.0, (now - datetime.fromisoformat(str(row[field]))).total_seconds())
                for row in states
                if row.get("status") == selected_status and row.get(field)
            ]
            return round(max(ages), 1) if ages else None

        return {
            "admitted": len(admitted), "unique_tickers": len(active),
            "queued": queued, "warming": warming, "ready": ready,
            "failed": failed, "retained": len(self._retained_tickers),
            "deferred_auto": len(self._deferred_auto),
            "oldest_queued_seconds": oldest_age("queued_at", "queued"),
            "oldest_warming_seconds": oldest_age("started_at", "warming"),
            "mean_duration_seconds": round(mean, 3) if mean else None,
            "eta_seconds": round(remaining * mean / self.config.warm_concurrency, 1) if mean else None,
        }

    def _trim_retained(self) -> None:
        now = time.monotonic()
        self._retained_tickers = {
            key: deadline for key, deadline in self._retained_tickers.items() if deadline > now
        }
        overflow = max(0, len(self._retained_tickers) - self.config.maximum_tickers)
        for key, _deadline in sorted(self._retained_tickers.items(), key=lambda item: item[1])[:overflow]:
            del self._retained_tickers[key]

    def _validate_release_contexts(self, authority: LoadedRelease) -> None:
        expected = (
            tuple(authority.data_config.intraday_context_bars),
            tuple(authority.data_config.calendar_context_bars),
            tuple(authority.data_config.horizons_us),
            authority.data_config.loader_stream_contract_version,
        )
        for release in self.releases.values():
            actual = (
                tuple(release.data_config.intraday_context_bars),
                tuple(release.data_config.calendar_context_bars),
                tuple(release.data_config.horizons_us),
                release.data_config.loader_stream_contract_version,
            )
            if actual != expected:
                raise RuntimeError(
                    f"release {release.config.model_id} cannot share the serving cache because its data contract differs"
                )


def _post_json(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))
