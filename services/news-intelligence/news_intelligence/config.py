from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class IntelligenceConfig:
    bind: str
    enable_llm: bool
    enable_models: bool
    llm_base_url: str
    llm_min_materiality: float
    llm_min_text_chars: int
    llm_model: str
    llm_timeout_ms: int
    manifest_path: Path
    max_text_chars: int
    model_root: Path
    prompt_version: str
    active_ner_model: str
    active_sentiment_model: str
    stack_version: str
    taxonomy_version: str

    @classmethod
    def from_env(cls) -> "IntelligenceConfig":
        base_dir = Path(__file__).resolve().parents[1]
        return cls(
            bind=env_string("NEWS_INTELLIGENCE_BIND", "127.0.0.1:8797"),
            enable_llm=env_bool("NEWS_INTELLIGENCE_ENABLE_LLM", False),
            enable_models=env_bool("NEWS_INTELLIGENCE_ENABLE_MODELS", True),
            llm_base_url=env_string("NEWS_INTELLIGENCE_LLM_BASE_URL", "http://127.0.0.1:8000/v1").rstrip("/"),
            llm_min_materiality=env_float("NEWS_INTELLIGENCE_LLM_MIN_MATERIALITY", 0.65),
            llm_min_text_chars=env_int("NEWS_INTELLIGENCE_LLM_MIN_TEXT_CHARS", 80),
            llm_model=env_string("NEWS_INTELLIGENCE_LLM_MODEL", "Qwen/Qwen3-1.7B"),
            llm_timeout_ms=env_int("NEWS_INTELLIGENCE_LLM_TIMEOUT_MS", 3500),
            manifest_path=Path(
                env_string(
                    "NEWS_INTELLIGENCE_MODEL_MANIFEST",
                    str(base_dir / "models" / "opensource_models.json"),
                )
            ),
            max_text_chars=env_int("NEWS_INTELLIGENCE_MAX_TEXT_CHARS", 6000),
            model_root=Path(env_string("NEWS_INTELLIGENCE_MODEL_ROOT", r"D:\models_artifacts\opensource")),
            prompt_version=env_string("NEWS_INTELLIGENCE_PROMPT_VERSION", "news-llm-prompt-v1"),
            active_ner_model=env_string("NEWS_INTELLIGENCE_ACTIVE_NER_MODEL", "quantbridge-energy-intelligence"),
            active_sentiment_model=env_string(
                "NEWS_INTELLIGENCE_ACTIVE_SENTIMENT_MODEL",
                "distilroberta-financial-news",
            ),
            stack_version=env_string("NEWS_INTELLIGENCE_STACK_VERSION", "news-intelligence-v1"),
            taxonomy_version=env_string("NEWS_INTELLIGENCE_TAXONOMY_VERSION", "news-taxonomy-v1"),
        )


def load_manifest(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def model_path(config: IntelligenceConfig, key: str) -> Path:
    return config.model_root / key


def env_string(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value if value else default


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default

