from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from research.mlops.paths import default_run_root
from research.news_reaction_model.v5 import HORIZONS, MODEL_FAMILY, MODEL_VERSION


def default_feature_artifact_root() -> Path:
    return Path(
        os.environ.get(
            "NEWS_REACTION_V5_FEATURE_ROOT",
            r"D:\market-data\prepared\news_reaction_model\v5\tfidf_word_char_lsa_v1",
        )
    )


@dataclass(slots=True)
class LoaderConfig:
    dataset_database: str = "market_sip_compact"
    dataset_table: str = "news_reaction_tfidf_dataset_v5"
    dataset_version: str = "news_reaction_tfidf_dataset_v5"
    source_dataset_table: str = "news_reaction_percentage_dataset_v4"
    source_dataset_version: str = "news_reaction_percentage_dataset_v4"
    news_database: str = "q_live"
    normalized_news_table: str = "benzinga_news_normalized_v1"
    reaction_table: str = "news_reaction_labels_v2"
    label_version: str = "news_reaction_event_labels_v3"
    representation_name: str = "word_char_tfidf_lsa_v1"
    feature_artifact_root: Path = field(default_factory=default_feature_artifact_root)
    train_start: str = "2019-01-01"
    train_end_exclusive: str = "2026-01-01"
    validation_start: str = "2026-01-01"
    validation_end_exclusive: str = "2027-01-01"
    batch_size: int = 2048
    query_batch_articles: int = 2048
    workers: int = 2
    prefetch_batches: int = 4
    max_threads_per_query: int = 4
    max_memory_usage: str = "16G"
    max_chunks: int = 2
    embedding_dim: int = 1024
    horizons: tuple[str, ...] = HORIZONS


@dataclass(slots=True)
class FeatureConfig:
    word_max_features: int = 65_536
    char_max_features: int = 65_536
    output_dim: int = 1024
    word_ngram_min: int = 1
    word_ngram_max: int = 2
    char_ngram_min: int = 3
    char_ngram_max: int = 5
    min_df: int = 3
    max_df: float = 0.995
    max_text_chars: int = 12_000
    char_text_chars: int = 3_000
    svd_iterations: int = 3
    random_seed: int = 17
    fit_query_batch_articles: int = 4096


@dataclass(slots=True)
class ModelConfig:
    # Kept identical to V4. Each lexical channel is reduced to one 1,024-value
    # vector, preserving the exact [B, 2, 1024] V4 input/head contract.
    embedding_dim: int = 1024
    max_chunks: int = 2
    d_model: int = 384
    hidden_dim: int = 384
    layers: int = 4
    dropout: float = 0.10
    horizon_dim: int = 32
    horizons: tuple[str, ...] = HORIZONS


@dataclass(slots=True)
class TrainConfig:
    output_root: Path = default_run_root(MODEL_FAMILY, MODEL_VERSION, "train", "tfidf-forecast")
    run_name: str = ""
    epochs: int = 15
    max_samples: int = 0
    learning_rate: float = 3e-4
    weight_decay: float = 0.01
    grad_clip_norm: float = 1.0
    ordinal_loss_weight: float = 0.25
    scheduler: str = "cosine"
    scheduler_restarts: int = 3
    scheduler_eta_min: float = 1e-6
    amp: bool = True
    amp_dtype: str = "bf16"
    compile_model: bool = True
    logging_samples: int = 50_000
    validation_samples: int = 250_000
    validation_max_batches: int = 0
    checkpoint_latest_samples: int = 500_000
    checkpoint_archive_samples: int = 5_000_000
    evaluate_at_end: bool = True
    # Keep the V3/V4 project so the representation ablation is directly comparable.
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
    return f"news-v5-tfidf-d{config.model.d_model}-l{config.model.layers}-b{config.loader.batch_size}"
