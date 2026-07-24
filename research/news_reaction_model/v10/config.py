from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from research.mlops.paths import default_run_root
from research.news_reaction_model.v10 import HORIZONS, MODEL_FAMILY, MODEL_VERSION
from research.news_reaction_model.v10.stock_state import STOCK_STATE_DIM
from research.news_reaction_model.v10.time_features import TIME_FEATURE_DIM


OPENAI_EMBEDDING_DIM = 3_072
OPENAI_EMBEDDING_VERSION = "news_openai_text_embedding_3_large_3072_v1"
OPENAI_EMBEDDING_MODEL = "text-embedding-3-large"
OPENAI_TEXT_CONTRACT = "news_reaction_v7_publication_text_12000chars_8000tokens_v1"


def default_representation_artifact_root() -> Path:
    return Path(
        os.environ.get(
            "NEWS_REACTION_V8_FEATURE_ROOT",
            r"D:\market-data\prepared\news_reaction_model\v8\openai_embedding_stock_state_v1",
        )
    )


@dataclass(slots=True)
class LoaderConfig:
    dataset_database: str = "market_sip_compact"
    # Corrected V10 still reuses the exact prepared V8 rows; its causal time
    # channel is derived losslessly from their timestamp and session columns.
    dataset_table: str = "news_reaction_openai_stock_state_dataset_v8"
    dataset_version: str = "news_reaction_openai_stock_state_dataset_v8"
    embedding_version: str = OPENAI_EMBEDDING_VERSION
    embedding_model: str = OPENAI_EMBEDDING_MODEL
    embedding_text_contract: str = OPENAI_TEXT_CONTRACT
    news_database: str = "q_live"
    reaction_table: str = "news_reaction_labels_v2"
    label_version: str = "news_reaction_event_labels_v3"
    representation_name: str = "openai_3072_plus_point_in_time_stock_state_v1"
    representation_artifact_root: Path = field(default_factory=default_representation_artifact_root)
    train_start: str = "2019-01-01"
    train_end_exclusive: str = "2026-01-01"
    validation_start: str = "2026-01-01"
    validation_end_exclusive: str = "2027-01-01"
    batch_size: int = 2048
    query_batch_articles: int = 2048
    workers: int = 16
    prefetch_batches: int = 4
    shuffle_buffer_articles: int = 32_768
    max_threads_per_query: int = 4
    max_memory_usage: str = "16G"
    openai_embedding_dim: int = OPENAI_EMBEDDING_DIM
    stock_state_dim: int = STOCK_STATE_DIM
    time_feature_dim: int = TIME_FEATURE_DIM
    horizons: tuple[str, ...] = HORIZONS


@dataclass(slots=True)
class ModelConfig:
    openai_embedding_dim: int = OPENAI_EMBEDDING_DIM
    stock_state_dim: int = STOCK_STATE_DIM
    time_feature_dim: int = TIME_FEATURE_DIM
    d_model: int = 384
    hidden_dim: int = 384
    layers: int = 4
    dropout: float = 0.10
    horizon_dim: int = 32
    horizons: tuple[str, ...] = HORIZONS


@dataclass(slots=True)
class TrainConfig:
    output_root: Path = default_run_root(MODEL_FAMILY, MODEL_VERSION, "train", "openai-stock-state-forecast")
    run_name: str = ""
    epochs: int = 15
    max_samples: int = 0
    learning_rate: float = 3e-4
    weight_decay: float = 0.01
    grad_clip_norm: float = 1.0
    scheduler: str = "cosine"
    scheduler_restarts: int = 3
    scheduler_eta_min: float = 1e-6
    scheduler_cycle_decay: float = 1.0
    amp: bool = True
    amp_dtype: str = "bf16"
    compile_model: bool = True
    logging_samples: int = 50_000
    validation_samples: int = 250_000
    validation_max_batches: int = 0
    checkpoint_latest_samples: int = 500_000
    checkpoint_archive_samples: int = 5_000_000
    evaluate_at_end: bool = True
    # Keep the V3-V9 project so V10 remains directly comparable.
    wandb_project: str = "news-reaction-model-v3"
    wandb_entity: str = "mehdifaraji"
    wandb_mode: str = "auto"
    wandb_init_timeout: int = 120
    seed: int = 17


@dataclass(slots=True)
class ExperimentConfig:
    loader: LoaderConfig = field(default_factory=LoaderConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    train: TrainConfig = field(default_factory=TrainConfig)


def to_dict(config: Any) -> dict[str, Any]:
    return asdict(config) if hasattr(config, "__dataclass_fields__") else dict(config)


def default_run_name(config: ExperimentConfig) -> str:
    if config.train.run_name:
        return config.train.run_name
    return (
        f"news-v10-opportunity-openai-stock-state-time-balanced-d{config.model.d_model}"
        f"-l{config.model.layers}-b{config.loader.batch_size}"
    )
