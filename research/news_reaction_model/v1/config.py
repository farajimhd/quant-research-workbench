from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from research.mlops.paths import default_run_root
from research.news_reaction_model.v1 import HORIZONS, MODEL_FAMILY, MODEL_VERSION


@dataclass(slots=True)
class LoaderConfig:
    dataset_database: str = "market_sip_compact"
    dataset_table: str = "news_reaction_embedding_dataset_v1"
    dataset_version: str = "news_reaction_embedding_dataset_v1"
    embedding_database: str = "market_sip_compact"
    embedding_table: str = "news_text_embeddings"
    news_database: str = "q_live"
    ticker_table: str = "benzinga_news_ticker_v1"
    reaction_table: str = "news_reaction_labels_v2"
    quality_table: str = "news_reaction_quality_overlay_v1"
    scale_table: str = "news_reaction_scale_v2"
    embedding_model: str = "Qwen/Qwen3-Embedding-0.6B"
    label_version: str = "news_reaction_event_labels_v3"
    quality_version: str = "news_reaction_quality_overlay_v1"
    scale_version: str = "news_reaction_robust_scale_v2_1"
    train_start: str = "2019-01-01"
    train_end_exclusive: str = "2026-01-01"
    validation_start: str = "2026-01-01"
    validation_end_exclusive: str = "2027-01-01"
    batch_size: int = 512
    query_batch_articles: int = 2048
    workers: int = 2
    prefetch_batches: int = 4
    max_threads_per_query: int = 4
    max_memory_usage: str = "16G"
    max_chunks: int = 2
    embedding_dim: int = 1024
    horizons: tuple[str, ...] = HORIZONS
    reaction_z_threshold: float = 0.5


@dataclass(slots=True)
class ModelConfig:
    embedding_dim: int = 1024
    max_chunks: int = 2
    d_model: int = 256
    hidden_dim: int = 256
    layers: int = 2
    dropout: float = 0.10
    horizon_dim: int = 32
    session_dim: int = 16
    horizons: tuple[str, ...] = HORIZONS
    session_count: int = 4


@dataclass(slots=True)
class TrainConfig:
    output_root: Path = default_run_root(MODEL_FAMILY, MODEL_VERSION, "train", "embedding-forecast")
    run_name: str = ""
    epochs: int = 3
    max_samples: int = 0
    learning_rate: float = 3e-4
    weight_decay: float = 0.01
    grad_clip_norm: float = 1.0
    return_loss_weight: float = 0.25
    scheduler: str = "cosine"
    scheduler_eta_min: float = 1e-6
    scheduler_cycle_samples: int = 1_000_000
    amp: bool = True
    amp_dtype: str = "bf16"
    compile_model: bool = True
    logging_samples: int = 50_000
    validation_samples: int = 250_000
    validation_max_batches: int = 0
    checkpoint_latest_samples: int = 500_000
    checkpoint_archive_samples: int = 5_000_000
    wandb_project: str = "news-reaction-model-v1"
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
    return f"news-v1-d{config.model.d_model}-l{config.model.layers}-b{config.loader.batch_size}"
