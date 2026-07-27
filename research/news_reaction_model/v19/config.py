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
from research.news_reaction_model.v18.episode_contract import (
    CONTEXT_FEATURE_DIM,
    CONTEXT_SIZE,
    CURRENT_EPISODE_FEATURE_DIM,
)
from research.news_reaction_model.v19 import (
    MODEL_CONTRACT_VERSION,
    MODEL_FAMILY,
    MODEL_VERSION,
    SOURCE_DATASET_VERSION,
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
    prepared_dataset_version: str = SOURCE_DATASET_VERSION
    train_start: str = "2019-01-01"
    train_end_exclusive: str = "2026-01-01"
    validation_start: str = "2026-01-01"
    validation_end_exclusive: str = "2027-01-01"
    context_size: int = CONTEXT_SIZE
    batch_size: int = 2048
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
    price_regimes: int = 5
    publication_sessions: int = 4
    d_model: int = 384
    feedforward_dim: int = 1152
    transformer_layers: int = 2
    tower_hidden_dim: int = 384
    attention_heads: int = 6
    dropout: float = 0.10
    model_contract_version: str = MODEL_CONTRACT_VERSION


@dataclass(slots=True)
class TrainConfig:
    output_root: Path = default_run_root(
        MODEL_FAMILY, MODEL_VERSION, "train", "single-ticker-episodes"
    )
    run_name: str = ""
    joint_epochs: int = 12
    specialization_epochs: int = 8
    path_epochs: int = 15
    learning_rate: float = 3e-4
    specialization_learning_rate: float = 1e-4
    weight_decay: float = 0.01
    grad_clip_norm: float = 1.0
    scheduler_eta_min: float = 1e-6
    scheduler_cycle_decay: float = 0.98
    regression_weight: float = 1.0
    effective_number_beta: float = 0.9999
    minimum_class_weight: float = 0.25
    maximum_class_weight: float = 4.0
    regression_scale_quantile: float = 0.75
    regression_scale_floor_pct: float = 0.25
    regression_scale_minimum_rows: int = 128
    gradient_audit_epochs: int = 2
    class_recall_gate: float = 0.01
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
        f"news-v19-token-multitask-n{config.loader.context_size}"
        f"-d{config.model.d_model}-a{config.model.attention_heads}"
        f"-l{config.model.transformer_layers}-b{config.loader.batch_size}"
    )
