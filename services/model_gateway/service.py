from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from jsonschema import Draft202012Validator

from .config import GatewayConfig, ProviderProfile
from .schemas import InferenceRequest, InferenceResponse
from .store import AuditStore


class ModelGateway:
    def __init__(self, config: GatewayConfig):
        self.config = config
        self.store = AuditStore(config.runtime_root)
        self.semaphore = asyncio.Semaphore(max(1, config.max_concurrency))
        self.provider_failures: dict[str, int] = {}
        self.provider_open_until: dict[str, float] = {}
        self.key_locks = [asyncio.Lock() for _ in range(256)]
        self.budget_lock = asyncio.Lock()
        self.reserved_cost: dict[str, float] = {}

    async def infer(self, request: InferenceRequest) -> InferenceResponse:
        lock_index = int(hashlib.sha256(request.idempotency_key.encode()).hexdigest()[:8], 16) % len(self.key_locks)
        lock = self.key_locks[lock_index]
        async with lock:
            return await self._infer_locked(request)

    async def _infer_locked(self, request: InferenceRequest) -> InferenceResponse:
        route = self.config.routes.get(request.route)
        if route is None:
            raise ValueError(f"unknown route: {request.route}")
        canonical = json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        request_hash = hashlib.sha256(canonical.encode()).hexdigest()
        cached = await asyncio.to_thread(self.store.get, request.idempotency_key, request_hash)
        if cached:
            return InferenceResponse(idempotency_key=request.idempotency_key, cached=True, **cached)
        failures: list[str] = []
        deadline = time.monotonic() + route.timeout_seconds
        async with self.semaphore:
            for provider_name in route.providers:
                if time.monotonic() >= deadline:
                    failures.append(f"route deadline exhausted after {route.timeout_seconds:g}s")
                    break
                provider = self.config.providers.get(provider_name)
                if provider is None:
                    failures.append(f"{provider_name}: not configured")
                    continue
                if self.provider_open_until.get(provider_name, 0) > time.monotonic():
                    failures.append(f"{provider_name}: circuit open")
                    continue
                estimate = _estimate_cost(provider, canonical, route.max_output_tokens)
                async with self.budget_lock:
                    committed = await asyncio.to_thread(self.store.spent_today, route.name)
                    reserved = self.reserved_cost.get(route.name, 0.0)
                    if committed + reserved + estimate > route.daily_budget_usd:
                        failures.append(f"{provider_name}: daily route budget would be exceeded")
                        continue
                    self.reserved_cost[route.name] = reserved + estimate
                try:
                    for attempt in range(2):
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            failures.append(f"route deadline exhausted after {route.timeout_seconds:g}s")
                            break
                        attempt_started = time.perf_counter()
                        try:
                            row = await asyncio.to_thread(
                                _call_provider,
                                provider,
                                request,
                                route.max_output_tokens,
                                remaining,
                                route.reasoning_effort,
                            )
                            Draft202012Validator(request.response_schema).validate(row["result"])
                            row["route"] = route.name
                            self.provider_failures[provider_name] = 0
                            await asyncio.to_thread(
                                self.store.record_attempt,
                                idempotency_key=request.idempotency_key,
                                route=route.name,
                                provider=provider.name,
                                model=provider.model,
                                attempt=attempt + 1,
                                status="completed",
                                error="",
                                latency_ms=round((time.perf_counter() - attempt_started) * 1000),
                            )
                            await asyncio.to_thread(self.store.put, request.idempotency_key, request_hash, row)
                            return InferenceResponse(idempotency_key=request.idempotency_key, cached=False, **row)
                        except Exception as exc:  # provider retry/failover is the route contract
                            failure = f"{provider_name}[{attempt + 1}/2]: {type(exc).__name__}: {exc}"
                            failures.append(failure)
                            await asyncio.to_thread(
                                self.store.record_attempt,
                                idempotency_key=request.idempotency_key,
                                route=route.name,
                                provider=provider.name,
                                model=provider.model,
                                attempt=attempt + 1,
                                status="failed",
                                error=f"{type(exc).__name__}: {exc}",
                                latency_ms=round((time.perf_counter() - attempt_started) * 1000),
                            )
                            if attempt == 0 and time.monotonic() + 0.25 < deadline:
                                await asyncio.sleep(0.25)
                finally:
                    async with self.budget_lock:
                        self.reserved_cost[route.name] = max(
                            0.0, self.reserved_cost.get(route.name, 0.0) - estimate
                        )
                failure_count = self.provider_failures.get(provider_name, 0) + 1
                self.provider_failures[provider_name] = failure_count
                if failure_count >= 3:
                    self.provider_open_until[provider_name] = time.monotonic() + 30.0
        raise RuntimeError("; ".join(failures) or "no providers available")


def _call_provider(
    provider: ProviderProfile,
    request: InferenceRequest,
    max_output_tokens: int,
    timeout: float,
    reasoning_effort: str,
) -> dict[str, Any]:
    payload = {
        "model": provider.model,
        "messages": [item.model_dump() for item in request.messages],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "structured_result", "strict": True, "schema": request.response_schema},
        },
    }
    payload[provider.max_tokens_field] = max_output_tokens
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort
    headers = {"Content-Type": "application/json"}
    if provider.api_key_env:
        key = os.environ.get(provider.api_key_env, "")
        if not key:
            raise RuntimeError(f"missing {provider.api_key_env}")
        headers["Authorization"] = f"Bearer {key}"
    started = time.perf_counter()
    req = urllib.request.Request(
        f"{provider.base_url}/chat/completions",
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            decoded = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    content = decoded["choices"][0]["message"]["content"]
    result = json.loads(content) if isinstance(content, str) else content
    if not isinstance(result, dict):
        raise ValueError("provider returned a non-object result")
    usage = decoded.get("usage") or {}
    input_tokens = int(usage.get("prompt_tokens") or 0)
    output_tokens = int(usage.get("completion_tokens") or 0)
    cost = (
        input_tokens * provider.input_usd_per_million
        + output_tokens * provider.output_usd_per_million
    ) / 1_000_000
    return {
        "status": "completed", "provider": provider.name, "model": provider.model, "result": result,
        "input_tokens": input_tokens, "output_tokens": output_tokens, "cost_usd": cost,
        "latency_ms": round((time.perf_counter() - started) * 1000),
    }


def _estimate_cost(provider: ProviderProfile, text: str, max_output: int) -> float:
    # JSON is UTF-8 encoded for every configured provider. Reserving one input
    # token per byte is deliberately conservative and prevents a tokenizer or
    # language mix from spending past the configured route budget.
    estimated_input = max(1, len(text.encode("utf-8")))
    return (
        estimated_input * provider.input_usd_per_million
        + max_output * provider.output_usd_per_million
    ) / 1_000_000
