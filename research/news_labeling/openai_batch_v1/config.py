from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path


EXPERIMENT_VERSION = "news_semantic_openai_batch_v1"
HARD_MAX_COST_USD = Decimal("20.00")


@dataclass(frozen=True, slots=True)
class RemoteModel:
    profile: str
    model: str
    batch_input_usd_per_million: Decimal
    batch_output_usd_per_million: Decimal
    reasoning_effort: str | None


# Batch prices are 50% of the published synchronous token prices. Keep this
# registry explicit: an unknown or newly priced model must never be submitted
# under an inferred price.
MODEL_REGISTRY: dict[str, RemoteModel] = {
    "gpt-5.6-sol": RemoteModel(
        "gpt-5.6-sol", "gpt-5.6-sol", Decimal("2.50"), Decimal("15.00"), "none"
    ),
    "gpt-5.6-terra": RemoteModel(
        "gpt-5.6-terra", "gpt-5.6-terra", Decimal("1.25"), Decimal("7.50"), "none"
    ),
    "gpt-5.6-luna": RemoteModel(
        "gpt-5.6-luna", "gpt-5.6-luna", Decimal("0.50"), Decimal("3.00"), "none"
    ),
    "gpt-5.4-mini": RemoteModel(
        "gpt-5.4-mini", "gpt-5.4-mini", Decimal("0.375"), Decimal("2.25"), "none"
    ),
    "gpt-5.4-nano": RemoteModel(
        "gpt-5.4-nano", "gpt-5.4-nano", Decimal("0.10"), Decimal("0.625"), "none"
    ),
    "gpt-4.1-mini": RemoteModel(
        "gpt-4.1-mini", "gpt-4.1-mini", Decimal("0.20"), Decimal("0.80"), None
    ),
    "gpt-4.1-nano": RemoteModel(
        "gpt-4.1-nano", "gpt-4.1-nano", Decimal("0.05"), Decimal("0.20"), None
    ),
}
DEFAULT_PROFILES = tuple(MODEL_REGISTRY)


def default_runtime_root() -> Path:
    return Path(
        os.environ.get(
            "NEWS_OPENAI_BATCH_LABEL_ROOT",
            r"D:\TradingML\runtimes\news_labeling\openai_batch_v1",
        )
    )


def default_sample_path() -> Path:
    return Path(
        os.environ.get(
            "NEWS_OPENAI_BATCH_SAMPLE_JSONL",
            r"D:\TradingML\runtimes\news_labeling\gpt_oss_v1\shared\sample.jsonl",
        )
    )


@dataclass(frozen=True, slots=True)
class BatchConfig:
    runtime_root: Path = field(default_factory=default_runtime_root)
    sample_path: Path = field(default_factory=default_sample_path)
    profiles: tuple[str, ...] = DEFAULT_PROFILES
    max_output_tokens: int = 1_536
    poll_seconds: int = 60
    hard_max_cost_usd: Decimal = HARD_MAX_COST_USD
    base_url: str = "https://api.openai.com/v1"
    project_id: str = ""
    disagreement_limit: int = 48
    answer_key_path: Path | None = None

    @property
    def models_root(self) -> Path:
        return self.runtime_root / "models"

    @property
    def comparison_root(self) -> Path:
        return self.runtime_root / "comparison"

    @property
    def plan_path(self) -> Path:
        return self.runtime_root / "plan.json"

    def model_root(self, profile: str) -> Path:
        return self.models_root / profile
