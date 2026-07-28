from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProviderProfile:
    name: str
    base_url: str
    model: str
    api_key_env: str
    input_usd_per_million: float
    output_usd_per_million: float
    max_tokens_field: str


@dataclass(frozen=True)
class RouteProfile:
    name: str
    providers: tuple[str, ...]
    timeout_seconds: float
    max_output_tokens: int
    daily_budget_usd: float
    reasoning_effort: str


@dataclass(frozen=True)
class GatewayConfig:
    bind: str
    runtime_root: Path
    max_concurrency: int
    providers: dict[str, ProviderProfile]
    routes: dict[str, RouteProfile]

    @classmethod
    def from_env(cls) -> "GatewayConfig":
        runtime_root = Path(
            os.environ.get("MODEL_GATEWAY_RUNTIME_ROOT", r"D:\TradingML\runtimes\model_gateway")
        )
        if "quant-research-workbench" in str(runtime_root).lower():
            raise ValueError("MODEL_GATEWAY_RUNTIME_ROOT must be outside the source repository")
        providers = {
            "local-vllm": ProviderProfile(
                "local-vllm",
                os.environ.get("MODEL_GATEWAY_VLLM_URL", "http://127.0.0.1:8000/v1").rstrip("/"),
                os.environ.get("MODEL_GATEWAY_VLLM_MODEL", "openai/gpt-oss-20b"),
                "",
                0.0,
                0.0,
                "max_tokens",
            ),
            "openai-fast": ProviderProfile(
                "openai-fast",
                os.environ.get("MODEL_GATEWAY_OPENAI_URL", "https://api.openai.com/v1").rstrip("/"),
                os.environ.get("MODEL_GATEWAY_FAST_MODEL", "gpt-5.4-mini"),
                "OPENAI_API_KEY",
                _float("MODEL_GATEWAY_FAST_INPUT_USD_PER_M", 0.75),
                _float("MODEL_GATEWAY_FAST_OUTPUT_USD_PER_M", 4.5),
                "max_completion_tokens",
            ),
            "openai-deep": ProviderProfile(
                "openai-deep",
                os.environ.get("MODEL_GATEWAY_OPENAI_URL", "https://api.openai.com/v1").rstrip("/"),
                os.environ.get("MODEL_GATEWAY_DEEP_MODEL", "gpt-5.6"),
                "OPENAI_API_KEY",
                _float("MODEL_GATEWAY_DEEP_INPUT_USD_PER_M", 2.5),
                _float("MODEL_GATEWAY_DEEP_OUTPUT_USD_PER_M", 15.0),
                "max_completion_tokens",
            ),
        }
        routes = {
            "news.semantic_fast.v1": RouteProfile(
                "news.semantic_fast.v1",
                _csv("MODEL_GATEWAY_NEWS_FAST_PROVIDERS", "local-vllm,openai-fast"),
                _float("MODEL_GATEWAY_NEWS_FAST_TIMEOUT_SECONDS", 8.0),
                _int("MODEL_GATEWAY_NEWS_FAST_MAX_OUTPUT_TOKENS", 1600),
                _float("MODEL_GATEWAY_NEWS_FAST_DAILY_BUDGET_USD", 10.0),
                os.environ.get("MODEL_GATEWAY_NEWS_FAST_REASONING_EFFORT", "low"),
            ),
            "news.trade_hypothesis.v1": RouteProfile(
                "news.trade_hypothesis.v1",
                _csv("MODEL_GATEWAY_NEWS_DEEP_PROVIDERS", "openai-deep"),
                _float("MODEL_GATEWAY_NEWS_DEEP_TIMEOUT_SECONDS", 25.0),
                _int("MODEL_GATEWAY_NEWS_DEEP_MAX_OUTPUT_TOKENS", 1800),
                _float("MODEL_GATEWAY_NEWS_DEEP_DAILY_BUDGET_USD", 30.0),
                os.environ.get("MODEL_GATEWAY_NEWS_DEEP_REASONING_EFFORT", "medium"),
            ),
            "sec.semantic_label.v1": RouteProfile(
                "sec.semantic_label.v1",
                _csv("MODEL_GATEWAY_SEC_PROVIDERS", "local-vllm,openai-fast"),
                _float("MODEL_GATEWAY_SEC_TIMEOUT_SECONDS", 15.0),
                _int("MODEL_GATEWAY_SEC_MAX_OUTPUT_TOKENS", 1800),
                _float("MODEL_GATEWAY_SEC_DAILY_BUDGET_USD", 10.0),
                os.environ.get("MODEL_GATEWAY_SEC_REASONING_EFFORT", "low"),
            ),
        }
        return cls(
            bind=os.environ.get("MODEL_GATEWAY_BIND", "127.0.0.1:8802"),
            runtime_root=runtime_root,
            max_concurrency=_int("MODEL_GATEWAY_MAX_CONCURRENCY", 8),
            providers=providers,
            routes=routes,
        )


def _csv(name: str, default: str) -> tuple[str, ...]:
    return tuple(value.strip() for value in os.environ.get(name, default).split(",") if value.strip())


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default
