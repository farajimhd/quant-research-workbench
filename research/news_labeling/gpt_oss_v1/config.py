from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from pipelines.news.benzinga.news_benzinga_render_v2 import NEWS_RENDERER_VERSION


def default_runtime_root() -> Path:
    return Path(os.environ.get(
        "NEWS_GPT_OSS_LABEL_ROOT",
        r"D:\TradingML\runtimes\news_labeling\gpt_oss_v1",
    ))


@dataclass(frozen=True, slots=True)
class ModelProfile:
    name: str
    model: str
    tokenizer: str
    workers: int


MODEL_PROFILES = {
    "20b": ModelProfile(
        name="20b",
        model="openai/gpt-oss-20b",
        tokenizer="openai/gpt-oss-20b",
        workers=4,
    ),
    "120b": ModelProfile(
        name="120b",
        model="openai/gpt-oss-120b",
        tokenizer="openai/gpt-oss-120b",
        workers=4,
    ),
}


@dataclass(frozen=True, slots=True)
class LabelingConfig:
    endpoint: str = "http://127.0.0.1:8000/v1/chat/completions"
    model: str = "openai/gpt-oss-20b"
    tokenizer_source: str = "openai/gpt-oss-20b"
    profile: str = "20b"
    database: str = "q_live"
    event_table: str = "benzinga_news_event_v2"
    rendered_table: str = "benzinga_news_rendered_v2"
    authority_table: str = "benzinga_news_render_authority_v2"
    renderer_version: str = NEWS_RENDERER_VERSION
    sample_size: int = 192
    candidate_size: int = 4_000
    workers: int = 4
    max_input_chars: int = 45_000
    max_model_len: int = 16_384
    max_output_tokens: int = 1_536
    timeout_seconds: int = 240
    attempts: int = 3
    start_date: str = "2019-01-01"
    end_date_exclusive: str = "2027-01-01"
    runtime_root: Path = field(default_factory=default_runtime_root)

    @property
    def results_path(self) -> Path:
        return self.runtime_root / "labels.jsonl"

    @property
    def failures_path(self) -> Path:
        return self.runtime_root / "failures.jsonl"

    @property
    def sample_path(self) -> Path:
        return self.runtime_root / "sample.jsonl"
