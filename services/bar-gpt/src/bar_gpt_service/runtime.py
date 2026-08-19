from __future__ import annotations

import asyncio
import json
import time
import urllib.request
from collections import deque
from datetime import UTC, datetime
from threading import RLock
from typing import Any

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
            "backend_delivered": 0,
            "backend_failed": 0,
            "queue_dropped": 0,
        }
        self.failures: deque[dict[str, Any]] = deque(maxlen=100)
        self.qmd_state = {"status": "disabled", "error": "", "updated_at": ""}
        self.backend_state = {"status": "unknown", "error": "", "updated_at": ""}
        self.started_at = ""
        self._queue: asyncio.Queue[tuple[str, str, int]] = asyncio.Queue(maxsize=config.queue_capacity)
        self._queued: set[tuple[str, str, int]] = set()
        self._warm_tasks: dict[tuple[str, str], asyncio.Task[None]] = {}
        self._tasks: list[asyncio.Task[Any]] = []
        self._builder: LiveEventBarBuilder | None = None
        self._lock = RLock()
        self._listeners: set[asyncio.Queue[dict[str, Any]]] = set()
        self._warm_semaphore = asyncio.Semaphore(config.warm_concurrency)
        self._pending_historical: dict[tuple[str, str], list[RawBar]] = {}

    async def start(self) -> None:
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
            self.caches["live"] = self._new_cache()
            if self.config.connect_qmd:
                try:
                    references = await asyncio.to_thread(
                        ConditionReferences.load,
                        authority.data_config.database,
                        authority.data_config.condition_reference_table,
                    )
                    self._builder = LiveEventBarBuilder(
                        references, authority.data_config.max_quote_spread_bps
                    )
                    self._tasks.append(asyncio.create_task(self._qmd_loop(), name="bar-gpt-qmd"))
                    self._tasks.append(asyncio.create_task(self._clock_loop(), name="bar-gpt-clock"))
                except Exception as exc:
                    self._set_qmd_state("blocked", str(exc))
            else:
                self._set_qmd_state("disabled", "BAR_GPT_CONNECT_QMD is false")
            self._tasks.append(asyncio.create_task(self._batch_loop(), name="bar-gpt-batcher"))
            self._tasks.append(asyncio.create_task(self._scope_reaper(), name="bar-gpt-scope-reaper"))
        self.started_at = datetime.now(UTC).isoformat()

    async def stop(self) -> None:
        for task in (*self._warm_tasks.values(), *self._tasks):
            task.cancel()
        await asyncio.gather(*self._warm_tasks.values(), *self._tasks, return_exceptions=True)
        self._warm_tasks.clear()
        self._tasks.clear()

    def health(self) -> dict[str, Any]:
        active = self.active_tickers()
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
        elif live_auto and self.qmd_state["status"] != "streaming":
            status = "degraded"
            message = "Live automatic serving is scoped but the QMD stream is not healthy."
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
            "caches": {key: value.summary() for key, value in self.caches.items()},
            "qmd": dict(self.qmd_state),
            "backend": dict(self.backend_state),
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
        now = time.monotonic()
        row = {
            "scope_id": scope_id,
            "request": request.model_dump(),
            "created_at": datetime.now(UTC).isoformat(),
            "expires_monotonic": now + request.ttl_ms / 1000.0,
            "cache_id": "live" if request.mode in {"live", "paper"} else scope_id,
        }
        with self._lock:
            self.scopes[scope_id] = row
        cache_id = str(row["cache_id"])
        self.caches.setdefault(cache_id, self._new_cache())
        for ticker in request.tickers:
            self._promote_pending(cache_id, ticker, request.clock_us)
            self.request_warm(cache_id, ticker, request.clock_us)
            if request.clock_us and self._cache(cache_id).readiness(ticker, request.clock_us, self.config.minimum_warm_1s_bars)["ready"]:
                self._schedule_auto(cache_id, ticker, request.clock_us)
        self._reclaim_cache()
        return self.scope_snapshot(scope_id)

    def remove_scope(self, scope_id: str) -> bool:
        with self._lock:
            removed = self.scopes.pop(scope_id, None) is not None
        self._reclaim_cache()
        return removed

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
            cache.readiness(ticker, clock_us, self.config.minimum_warm_1s_bars)
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
            return
        self.metrics["warm_requested"] = int(self.metrics["warm_requested"]) + 1
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
        tickers = request.tickers or sorted(self.active_tickers(cache_id))
        model_ids = request.model_ids or list(self.releases)
        unknown = sorted(set(model_ids) - set(self.releases))
        if unknown:
            raise KeyError(f"unknown BarGPT model ids: {','.join(unknown)}")
        self.metrics["inference_requests"] = int(self.metrics["inference_requests"]) + 1
        results: list[dict[str, Any]] = []
        for start in range(0, len(tickers), self.config.maximum_batch_size):
            chunk = tickers[start:start + self.config.maximum_batch_size]
            for model_id in model_ids:
                release = self.releases[model_id]
                try:
                    predictions = await asyncio.to_thread(
                        self._infer_sync, release, self._cache(cache_id), chunk, request.origin_us
                    )
                except ValueError as exc:
                    self._record_failure("inference_not_ready", str(exc))
                    continue
                except Exception as exc:
                    self.metrics["failed_batches"] = int(self.metrics["failed_batches"]) + 1
                    self._record_failure("inference_failed", str(exc))
                    raise
                self.metrics["inference_batches"] = int(self.metrics["inference_batches"]) + 1
                for prediction in predictions:
                    prediction["scope_id"] = scope_id
                    prediction["mode"] = self.active_scopes().get(scope_id, {}).get("request", {}).get("mode", "live")
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
        try:
            async with self._warm_semaphore:
                release = next(iter(self.releases.values()))
                bootstrap = HistoricalBootstrap(release)
                bars = await asyncio.to_thread(
                    bootstrap.load,
                    ticker,
                    datetime.fromtimestamp(origin_us / 1_000_000, tz=UTC),
                )
                current = [bar for bar in bars if bar.available_at_us <= origin_us]
                future = sorted(
                    (bar for bar in bars if bar.available_at_us > origin_us),
                    key=lambda bar: (bar.available_at_us, bar.view, bar.bar_start_us),
                )
                self._cache(cache_id).upsert_many(current, derive=False)
                if cache_id != "live" and future:
                    self._pending_historical[(cache_id, ticker)] = future
                self.metrics["warm_completed"] = int(self.metrics["warm_completed"]) + 1
                for scope_id, row in self.active_scopes().items():
                    if row["cache_id"] == cache_id and ticker in row["request"]["tickers"] and row["request"].get("clock_us"):
                        self._schedule_auto(cache_id, ticker, int(row["request"]["clock_us"]))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.metrics["warm_failed"] = int(self.metrics["warm_failed"]) + 1
            self._record_failure("warm_failed", f"{ticker}: {exc}")

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

    async def _on_qmd_events(self, events: list[dict[str, Any]]) -> None:
        if self._builder is None:
            return
        corrections: list[RawBar] = []
        active = self.active_tickers("live")
        for event in events:
            corrections.extend(self._builder.apply(event, active))
        if corrections:
            self._cache("live").upsert_many(corrections)
            for bar in corrections:
                self._schedule_auto("live", bar.ticker, bar.available_at_us)

    async def _clock_loop(self) -> None:
        while True:
            await asyncio.sleep(0.05)
            if self._builder is None:
                continue
            bars = self._builder.flush(self._serving_clock_us())
            if not bars:
                continue
            self._cache("live").upsert_many(bars)
            for bar in bars:
                self._schedule_auto("live", bar.ticker, bar.available_at_us)

    def _schedule_auto(self, cache_id: str, ticker: str, origin_us: int) -> None:
        eligible = any(
            ticker in row["request"]["tickers"]
            and row["cache_id"] == cache_id
            and row["request"]["trigger_mode"] == "auto"
            and int(row["request"].get("clock_us") or origin_us) >= origin_us
            for row in self.active_scopes().values()
        )
        key = (cache_id, ticker, origin_us)
        if not eligible or key in self._queued:
            return
        try:
            self._queue.put_nowait(key)
            self._queued.add(key)
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
                        await self.infer(InferenceRequest(scope_id=scope_id, tickers=sorted(set(tickers)), origin_us=origin))
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
                    del self.scopes[key]
            if expired:
                self._reclaim_cache()

    def _reclaim_cache(self) -> None:
        active_cache_ids = {str(row["cache_id"]) for row in self.active_scopes().values()} | {"live"}
        for cache_id in list(self.caches):
            if cache_id not in active_cache_ids:
                del self.caches[cache_id]
                for key in [key for key in self._pending_historical if key[0] == cache_id]:
                    del self._pending_historical[key]
            else:
                self.caches[cache_id].evict_except(self.active_tickers(cache_id))

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
        self.failures.append({"code": code, "message": message, "at": datetime.now(UTC).isoformat()})

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
