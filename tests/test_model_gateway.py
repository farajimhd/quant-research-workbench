from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from services.model_gateway.config import GatewayConfig, ProviderProfile, RouteProfile
from services.model_gateway.schemas import InferenceRequest
from services.model_gateway.service import ModelGateway
from services.model_gateway.store import AuditStore


class _Store:
    def __init__(self) -> None:
        self.rows: dict[str, tuple[str, dict]] = {}
        self.attempts: list[dict] = []

    def get(self, key: str, request_hash: str):
        value = self.rows.get(key)
        if not value:
            return None
        if value[0] != request_hash:
            raise ValueError("idempotency mismatch")
        return value[1]

    def put(self, key: str, request_hash: str, row: dict) -> None:
        self.rows[key] = (request_hash, row)

    def spent_today(self, _route: str) -> float:
        return 0.0

    def record_attempt(self, **row) -> None:
        self.attempts.append(row)

    def attempt_metrics(self) -> dict:
        return {"attempts_today": len(self.attempts), "failed_attempts_today": 0}


def _config() -> GatewayConfig:
    return GatewayConfig(
        bind="127.0.0.1:8802",
        runtime_root=Path(r"D:\TradingML\runtimes\tests\model_gateway"),
        max_concurrency=2,
        providers={
            "local": ProviderProfile("local", "http://unused", "test", "", 0, 0, "max_tokens")
        },
        routes={
            "test.route": RouteProfile("test.route", ("local",), 1.0, 10, 1.0, "low")
        },
    )


class ModelGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_attempt_audit_persists_success_and_failure_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = AuditStore(Path(directory))
            store.record_attempt(
                idempotency_key="one",
                route="test.route",
                provider="local",
                model="test-model",
                attempt=1,
                status="completed",
                error="",
                latency_ms=10,
            )
            store.record_attempt(
                idempotency_key="two",
                route="test.route",
                provider="local",
                model="test-model",
                attempt=1,
                status="failed",
                error="TimeoutError: provider timed out",
                latency_ms=20,
            )

            metrics = store.attempt_metrics()

        self.assertEqual(metrics["attempts_today"], 2)
        self.assertEqual(metrics["failed_attempts_today"], 1)
        self.assertEqual(metrics["latest_attempt"]["status"], "failed")
        self.assertEqual(metrics["latest_attempt"]["latency_ms"], 20)

    async def test_structured_result_is_cached_by_idempotency_key(self) -> None:
        store = _Store()
        with patch("services.model_gateway.service.AuditStore", return_value=store):
            gateway = ModelGateway(_config())
        request = InferenceRequest(
            route="test.route",
            idempotency_key="abcdefgh",
            messages=[{"role": "user", "content": "hello"}],
            response_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["label"],
                "properties": {"label": {"type": "string"}},
            },
        )
        provider_result = {
            "status": "completed",
            "provider": "local",
            "model": "test",
            "result": {"label": "ok"},
            "input_tokens": 1,
            "output_tokens": 1,
            "cost_usd": 0,
            "latency_ms": 1,
        }
        with patch("services.model_gateway.service._call_provider", return_value=provider_result) as call:
            first = await gateway.infer(request)
            second = await gateway.infer(request)
        self.assertFalse(first.cached)
        self.assertTrue(second.cached)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(store.attempts[0]["status"], "completed")

    async def test_schema_violation_fails_closed(self) -> None:
        store = _Store()
        with patch("services.model_gateway.service.AuditStore", return_value=store):
            gateway = ModelGateway(_config())
        request = InferenceRequest(
            route="test.route",
            idempotency_key="abcdefgh",
            messages=[{"role": "user", "content": "hello"}],
            response_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["label"],
                "properties": {"label": {"type": "string"}},
            },
        )
        invalid = {
            "status": "completed",
            "provider": "local",
            "model": "test",
            "result": {"wrong": True},
            "input_tokens": 1,
            "output_tokens": 1,
            "cost_usd": 0,
            "latency_ms": 1,
        }
        with patch("services.model_gateway.service._call_provider", return_value=invalid):
            with self.assertRaises(RuntimeError):
                await gateway.infer(request)
        self.assertEqual(len(store.attempts), 2)
        self.assertTrue(all(row["status"] == "failed" for row in store.attempts))

    async def test_route_timeout_is_one_total_budget_not_one_budget_per_retry(self) -> None:
        config = _config()
        config.routes["test.route"] = RouteProfile("test.route", ("local",), 0.02, 10, 1.0, "low")
        store = _Store()
        with patch("services.model_gateway.service.AuditStore", return_value=store):
            gateway = ModelGateway(config)
        request = InferenceRequest(
            route="test.route",
            idempotency_key="deadline-test",
            messages=[{"role": "user", "content": "hello"}],
            response_schema={"type": "object"},
        )

        def exceed_budget(*_args, **_kwargs):
            time.sleep(0.03)
            raise TimeoutError("provider timed out")

        with patch("services.model_gateway.service._call_provider", side_effect=exceed_budget) as call:
            with self.assertRaisesRegex(RuntimeError, "route deadline exhausted"):
                await gateway.infer(request)

        self.assertEqual(call.call_count, 1)
        self.assertEqual(len(store.attempts), 1)
        self.assertEqual(store.attempts[0]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
