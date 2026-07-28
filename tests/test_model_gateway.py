from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from services.model_gateway.config import GatewayConfig, ProviderProfile, RouteProfile
from services.model_gateway.schemas import InferenceRequest
from services.model_gateway.service import ModelGateway


class _Store:
    def __init__(self) -> None:
        self.rows: dict[str, tuple[str, dict]] = {}

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

    async def test_schema_violation_fails_closed(self) -> None:
        with patch("services.model_gateway.service.AuditStore", return_value=_Store()):
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


if __name__ == "__main__":
    unittest.main()
