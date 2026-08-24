from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research.mlops.env import discover_env_files, load_env_files


@dataclass(frozen=True)
class IntelligenceConfig:
    bind: str
    enable_live_ai: bool
    enable_llm: bool
    enable_models: bool
    forecast_funnel_enabled: bool
    forecast_release_manifest: Path
    forecast_model_device: str
    forecast_eligibility_threshold: float
    review_trigger_mode: str
    review_prompt_path: Path
    model_gateway_url: str
    news_hypothesis_url: str
    llm_base_url: str
    llm_min_materiality: float
    llm_min_text_chars: int
    llm_model: str
    llm_max_tokens: int
    llm_merge_mode: str
    llm_reasoning_effort: str
    llm_response_format: str
    llm_timeout_ms: int
    manifest_path: Path
    max_text_chars: int
    model_device: str
    model_root: Path
    prompt_version: str
    active_ner_model: str
    active_sentiment_model: str
    stack_version: str
    taxonomy_version: str
    terminal_rich_enabled: bool
    terminal_screen_enabled: bool
    terminal_refresh_seconds: float

    @classmethod
    def from_env(cls) -> "IntelligenceConfig":
        load_repo_dotenv()
        base_dir = Path(__file__).resolve().parents[1]
        llm_model = env_string(
            "TEXT_INTELLIGENCE_LLM_MODEL",
            "Qwen/Qwen3-1.7B",
            "NEWS_INTELLIGENCE_LLM_MODEL",
        )
        gpt_oss_default = "gpt-oss" in llm_model.lower()
        return cls(
            bind=env_string(
                "TEXT_INTELLIGENCE_BIND", "127.0.0.1:8804", "NEWS_INTELLIGENCE_BIND"
            ),
            # News Synthesis V1 and the separate SEC classifier are the standard
            # responsibility. Model Gateway and News Hypothesis are a separate,
            # explicitly authorized live-trading dependency chain.
            enable_live_ai=env_bool(
                "TEXT_INTELLIGENCE_ENABLE_LIVE_AI",
                False,
                "NEWS_INTELLIGENCE_ENABLE_LIVE_AI",
            ),
            enable_llm=env_bool(
                "TEXT_INTELLIGENCE_ENABLE_LLM", False, "NEWS_INTELLIGENCE_ENABLE_LLM"
            ),
            # News Synthesis V1 plus SEC V5 labeling is the required service path.
            # Optional local model loading must be an explicit operator choice
            # so a bare service start never allocates model/GPU resources.
            enable_models=env_bool(
                "TEXT_INTELLIGENCE_ENABLE_MODELS",
                False,
                "NEWS_INTELLIGENCE_ENABLE_MODELS",
            ),
            forecast_funnel_enabled=env_bool(
                "TEXT_INTELLIGENCE_FORECAST_FUNNEL_ENABLED", True
            ),
            forecast_release_manifest=Path(env_string(
                "TEXT_INTELLIGENCE_FORECAST_RELEASE_MANIFEST",
                r"D:\TradingML\runtimes\text_intelligence\serving\news_forecast_funnel_v1\release.json",
            )),
            forecast_model_device=env_string(
                "TEXT_INTELLIGENCE_FORECAST_MODEL_DEVICE", "cpu"
            ).lower(),
            forecast_eligibility_threshold=env_float(
                "TEXT_INTELLIGENCE_FORECAST_ELIGIBILITY_THRESHOLD", 0.5
            ),
            review_trigger_mode=env_string(
                "TEXT_INTELLIGENCE_REVIEW_TRIGGER_MODE", "manual"
            ).lower(),
            review_prompt_path=Path(env_string(
                "TEXT_INTELLIGENCE_REVIEW_PROMPT_PATH",
                r"D:\TradingML\runtimes\text_intelligence\serving\news_forecast_funnel_v1\issuer_review_system_prompt_v1.txt",
            )),
            model_gateway_url=env_string(
                "TEXT_INTELLIGENCE_MODEL_GATEWAY_URL", "http://127.0.0.1:8802"
            ).rstrip("/"),
            news_hypothesis_url=env_string(
                "TEXT_INTELLIGENCE_NEWS_HYPOTHESIS_URL", "http://127.0.0.1:8803"
            ).rstrip("/"),
            llm_base_url=env_string(
                "TEXT_INTELLIGENCE_LLM_BASE_URL",
                "http://127.0.0.1:8000/v1",
                "NEWS_INTELLIGENCE_LLM_BASE_URL",
            ).rstrip("/"),
            llm_min_materiality=env_float(
                "TEXT_INTELLIGENCE_LLM_MIN_MATERIALITY",
                0.65,
                "NEWS_INTELLIGENCE_LLM_MIN_MATERIALITY",
            ),
            llm_min_text_chars=env_int(
                "TEXT_INTELLIGENCE_LLM_MIN_TEXT_CHARS",
                80,
                "NEWS_INTELLIGENCE_LLM_MIN_TEXT_CHARS",
            ),
            llm_model=llm_model,
            llm_max_tokens=env_int(
                "TEXT_INTELLIGENCE_LLM_MAX_TOKENS",
                512,
                "NEWS_INTELLIGENCE_LLM_MAX_TOKENS",
            ),
            llm_merge_mode=env_string(
                "TEXT_INTELLIGENCE_LLM_MERGE_MODE",
                "summary_only",
                "NEWS_INTELLIGENCE_LLM_MERGE_MODE",
            ).lower(),
            llm_reasoning_effort=env_string(
                "TEXT_INTELLIGENCE_LLM_REASONING_EFFORT",
                "low" if gpt_oss_default else "",
                "NEWS_INTELLIGENCE_LLM_REASONING_EFFORT",
            ),
            llm_response_format=env_string(
                "TEXT_INTELLIGENCE_LLM_RESPONSE_FORMAT",
                "json_object" if gpt_oss_default else "",
                "NEWS_INTELLIGENCE_LLM_RESPONSE_FORMAT",
            ),
            llm_timeout_ms=env_int(
                "TEXT_INTELLIGENCE_LLM_TIMEOUT_MS",
                3500,
                "NEWS_INTELLIGENCE_LLM_TIMEOUT_MS",
            ),
            manifest_path=Path(
                env_string(
                    "TEXT_INTELLIGENCE_MODEL_MANIFEST",
                    str(base_dir / "models" / "opensource_models.json"),
                    "NEWS_INTELLIGENCE_MODEL_MANIFEST",
                )
            ),
            max_text_chars=env_int(
                "TEXT_INTELLIGENCE_MAX_TEXT_CHARS",
                6000,
                "NEWS_INTELLIGENCE_MAX_TEXT_CHARS",
            ),
            model_device=env_string(
                "TEXT_INTELLIGENCE_MODEL_DEVICE",
                "auto",
                "NEWS_INTELLIGENCE_MODEL_DEVICE",
            ).lower(),
            model_root=Path(
                env_string(
                    "TEXT_INTELLIGENCE_MODEL_ROOT",
                    r"D:\models_artifacts\opensource",
                    "NEWS_INTELLIGENCE_MODEL_ROOT",
                )
            ),
            prompt_version=env_string(
                "TEXT_INTELLIGENCE_PROMPT_VERSION",
                "news-llm-prompt-v1",
                "NEWS_INTELLIGENCE_PROMPT_VERSION",
            ),
            active_ner_model=env_string(
                "TEXT_INTELLIGENCE_ACTIVE_NER_MODEL",
                "quantbridge-energy-intelligence",
                "NEWS_INTELLIGENCE_ACTIVE_NER_MODEL",
            ),
            active_sentiment_model=env_string(
                "TEXT_INTELLIGENCE_ACTIVE_SENTIMENT_MODEL",
                "distilroberta-financial-news",
                "NEWS_INTELLIGENCE_ACTIVE_SENTIMENT_MODEL",
            ),
            stack_version=env_string(
                "TEXT_INTELLIGENCE_STACK_VERSION",
                "text-intelligence-v1",
                "NEWS_INTELLIGENCE_STACK_VERSION",
            ),
            taxonomy_version=env_string(
                "TEXT_INTELLIGENCE_TAXONOMY_VERSION",
                "news-taxonomy-v1",
                "NEWS_INTELLIGENCE_TAXONOMY_VERSION",
            ),
            terminal_rich_enabled=env_bool_auto(
                "TEXT_INTELLIGENCE_TERMINAL_RICH_ENABLED",
                sys.stdout.isatty(),
                "NEWS_INTELLIGENCE_TERMINAL_RICH_ENABLED",
            ),
            terminal_screen_enabled=env_bool(
                "TEXT_INTELLIGENCE_TERMINAL_SCREEN_ENABLED",
                True,
                "NEWS_INTELLIGENCE_TERMINAL_SCREEN_ENABLED",
            ),
            terminal_refresh_seconds=max(
                0.25,
                env_float(
                    "TEXT_INTELLIGENCE_TERMINAL_REFRESH_SECONDS",
                    1.0,
                    "NEWS_INTELLIGENCE_TERMINAL_REFRESH_SECONDS",
                ),
            ),
        )


def load_manifest(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_repo_dotenv() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    load_env_files(discover_env_files(repo_root), verbose=False)


def dotenv_candidates() -> list[Path]:
    here = Path(__file__).resolve()
    return [
        here.parents[2] / ".env",
        here.parents[3] / ".env",
        Path.cwd() / ".env",
    ]


def strip_env_value(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"'}:
        return stripped[1:-1]
    return stripped


def model_path(config: IntelligenceConfig, key: str) -> Path:
    return config.model_root / key


def env_string(name: str, default: str, *legacy_names: str) -> str:
    for candidate in (name, *legacy_names):
        value = os.environ.get(candidate, "").strip()
        if value:
            return value
    return default


def env_bool(name: str, default: bool, *legacy_names: str) -> bool:
    value = env_string(name, "", *legacy_names).lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def env_bool_auto(name: str, default: bool, *legacy_names: str) -> bool:
    value = env_string(name, "", *legacy_names).lower()
    if value in {"auto", ""}:
        return default
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def env_int(name: str, default: int, *legacy_names: str) -> int:
    try:
        return int(env_string(name, str(default), *legacy_names))
    except ValueError:
        return default


def env_float(name: str, default: float, *legacy_names: str) -> float:
    try:
        return float(env_string(name, str(default), *legacy_names))
    except ValueError:
        return default
