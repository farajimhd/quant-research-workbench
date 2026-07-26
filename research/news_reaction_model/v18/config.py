from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from research.mlops.paths import default_run_root
from research.news_reaction_model.v15.config import (
    LoaderConfig as V15LoaderConfig,
)
from research.news_reaction_model.v15.config import OPENAI_EMBEDDING_DIM
from research.news_reaction_model.v15.stock_state import STOCK_STATE_DIM
from research.news_reaction_model.v15.time_features import TIME_FEATURE_DIM
from research.news_reaction_model.v18 import MODEL_FAMILY, MODEL_VERSION
from research.news_reaction_model.v18.episode_contract import (
    CONTEXT_FEATURE_DIM,
    CONTEXT_SIZE,
    CURRENT_EPISODE_FEATURE_DIM,
)


def default_v15_root() -> Path:
    return Path(
        os.environ.get(
            "NEWS_REACTION_V15_DATA_ROOT",
            r"D:\market-data\prepared\news_reaction_model\v15\causal_context_v1",
        )
    )


def default_prepared_root() -> Path:
    return Path(
        os.environ.get(
            "NEWS_REACTION_V18_DATA_ROOT",
            r"D:\market-data\prepared\news_reaction_model\v18\single_ticker_episodes_v1",
        )
    )


@dataclass(slots=True)
class LoaderConfig:
    v15_prepared_root: Path = field(default_factory=default_v15_root)
    v15_dataset_version: str = "news_reaction_openai_causal_context_dataset_v15"
    prepared_dataset_root: Path = field(default_factory=default_prepared_root)
    prepared_dataset_version: str = "news_reaction_single_ticker_episode_dataset_v18"
    news_database: str = "q_live"
    normalized_news_table: str = "benzinga_news_normalized_v1"
    ticker_link_table: str = "benzinga_news_ticker_v1"
    relevance_table: str = "news_ticker_relevance_v2"
    relevance_version: str = "news_ticker_relevance_rules_v2_1"
    semantic_table: str = "news_semantic_event_features_v2"
    semantic_version: str = "news_semantic_event_dictionary_v2_1"
    reaction_calendar_table: str = "news_reaction_calendar_v1"
    reaction_calendar_version: str = "xnys_pandas_market_calendars_v1"
    market_database: str = "market_sip_compact"
    events_table_base: str = "events"
    condition_reference_table: str = "event_condition_token_reference"
    split_table: str = "market_stock_split_v1"
    train_start: str = "2019-01-01"
    train_end_exclusive: str = "2026-01-01"
    validation_start: str = "2026-01-01"
    validation_end_exclusive: str = "2027-01-01"
    root_max_price: float = 20.0
    root_planning_slack_fraction: float = 0.01
    episode_inactivity_sessions: int = 2
    context_size: int = CONTEXT_SIZE
    batch_size: int = 2048
    workers: int = 16
    tickers_per_query: int = 64
    prefetch_batches: int = 4
    max_threads_per_query: int = 2
    max_memory_usage: str = "4G"
    anchor_audit_relative_tolerance: float = 0.005
    openai_embedding_dim: int = OPENAI_EMBEDDING_DIM
    stock_state_dim: int = STOCK_STATE_DIM
    time_feature_dim: int = TIME_FEATURE_DIM
    current_episode_feature_dim: int = CURRENT_EPISODE_FEATURE_DIM
    context_feature_dim: int = CONTEXT_FEATURE_DIM

    def __post_init__(self) -> None:
        self.v15_prepared_root = Path(self.v15_prepared_root)
        self.prepared_dataset_root = Path(self.prepared_dataset_root)

    def v15_config(self) -> V15LoaderConfig:
        return V15LoaderConfig(
            prepared_dataset_root=self.v15_prepared_root,
            prepared_dataset_version=self.v15_dataset_version,
            batch_size=self.batch_size,
        )


@dataclass(slots=True)
class ModelConfig:
    openai_embedding_dim: int = OPENAI_EMBEDDING_DIM
    stock_state_dim: int = STOCK_STATE_DIM
    time_feature_dim: int = TIME_FEATURE_DIM
    current_episode_feature_dim: int = CURRENT_EPISODE_FEATURE_DIM
    context_size: int = CONTEXT_SIZE
    context_feature_dim: int = CONTEXT_FEATURE_DIM
    d_model: int = 384
    hidden_dim: int = 384
    layers: int = 4
    attention_heads: int = 6
    dropout: float = 0.10


@dataclass(slots=True)
class TrainConfig:
    output_root: Path = default_run_root(
        MODEL_FAMILY, MODEL_VERSION, "train", "single-ticker-episodes"
    )
    run_name: str = ""
    epochs: int = 50
    learning_rate: float = 3e-4
    weight_decay: float = 0.01
    grad_clip_norm: float = 1.0
    scheduler_restarts: int = 49
    scheduler_eta_min: float = 1e-6
    scheduler_cycle_decay: float = 0.98
    regression_weight: float = 1.0
    amp: bool = True
    amp_dtype: str = "bf16"
    seed: int = 17
    wandb_project: str = "news-reaction-model-v3"
    wandb_entity: str = "mehdifaraji"
    wandb_mode: str = "auto"
    evaluate_at_end: bool = True

    def __post_init__(self) -> None:
        self.output_root = Path(self.output_root)


@dataclass(slots=True)
class ExperimentConfig:
    loader: LoaderConfig = field(default_factory=LoaderConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    train: TrainConfig = field(default_factory=TrainConfig)


def to_dict(value: Any) -> dict[str, Any]:
    from dataclasses import asdict

    return asdict(value)


def default_run_name(config: ExperimentConfig) -> str:
    if config.train.run_name:
        return config.train.run_name
    return (
        f"news-v18-episodes-openai-n{config.loader.context_size}"
        f"-d{config.model.d_model}-a{config.model.attention_heads}"
        f"-l{config.model.layers}-b{config.loader.batch_size}"
    )
