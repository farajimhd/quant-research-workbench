from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from research.mlops.paths import default_run_root
from research.news_reaction_model.v14 import HORIZONS, MODEL_FAMILY, MODEL_VERSION
from research.news_reaction_model.v14.stock_state import STOCK_STATE_DIM
from research.news_reaction_model.v14.time_features import TIME_FEATURE_DIM


def default_representation_artifact_root() -> Path:
    return Path(
        os.environ.get(
            "NEWS_REACTION_V7_FEATURE_ROOT",
            r"D:\market-data\prepared\news_reaction_model\v7\stock_state_v1",
        )
    )


def default_v5_feature_artifact_root() -> Path:
    return Path(
        os.environ.get(
            "NEWS_REACTION_V5_FEATURE_ROOT",
            r"D:\market-data\prepared\news_reaction_model\v5\sparse_tfidf_v2",
        )
    )


def default_v6_feature_artifact_root() -> Path:
    return Path(
        os.environ.get(
            "NEWS_REACTION_V6_FEATURE_ROOT",
            r"D:\market-data\prepared\news_reaction_model\v6\numeric_tfidf_v1",
        )
    )


@dataclass(slots=True)
class LoaderConfig:
    dataset_database: str = "market_sip_compact"
    # V14 deliberately reuses the completed V7 sparse lexical, numeric, and
    # point-in-time stock-state rows. Time is derived causally from publication.
    dataset_table: str = "news_reaction_stock_state_dataset_v7"
    dataset_version: str = "news_reaction_stock_state_dataset_v7"
    news_database: str = "q_live"
    reaction_table: str = "news_reaction_labels_v2"
    label_version: str = "news_reaction_event_labels_v3"
    representation_name: str = "v6_tfidf_numeric_plus_point_in_time_stock_state_v1"
    representation_artifact_root: Path = field(default_factory=default_representation_artifact_root)
    v5_feature_artifact_root: Path = field(default_factory=default_v5_feature_artifact_root)
    v6_feature_artifact_root: Path = field(default_factory=default_v6_feature_artifact_root)
    train_start: str = "2019-01-01"
    train_end_exclusive: str = "2026-01-01"
    validation_start: str = "2026-01-01"
    validation_end_exclusive: str = "2027-01-01"
    batch_size: int = 2048
    query_batch_articles: int = 2048
    workers: int = 2
    prefetch_batches: int = 4
    shuffle_buffer_articles: int = 32_768
    max_threads_per_query: int = 4
    max_memory_usage: str = "16G"
    word_vocab_size: int = 65_536
    char_vocab_size: int = 65_536
    numeric_vocab_size: int = 32_768
    numeric_dense_dim: int = 24
    stock_state_dim: int = STOCK_STATE_DIM
    time_feature_dim: int = TIME_FEATURE_DIM
    max_word_tokens: int = 256
    max_char_tokens: int = 512
    max_numeric_tokens: int = 64
    numeric_max_text_chars: int = 24_000
    numeric_context_words: int = 6
    numeric_max_mentions: int = 128
    horizons: tuple[str, ...] = HORIZONS


@dataclass(slots=True)
class ModelConfig:
    word_vocab_size: int = 65_536
    char_vocab_size: int = 65_536
    numeric_vocab_size: int = 32_768
    numeric_dense_dim: int = 24
    stock_state_dim: int = STOCK_STATE_DIM
    time_feature_dim: int = TIME_FEATURE_DIM
    d_model: int = 384
    hidden_dim: int = 384
    layers: int = 4
    attention_heads: int = 6
    dropout: float = 0.10
    horizon_dim: int = 32
    horizons: tuple[str, ...] = HORIZONS


@dataclass(slots=True)
class TrainConfig:
    output_root: Path = default_run_root(
        MODEL_FAMILY, MODEL_VERSION, "train", "tfidf-token-transformer-forecast"
    )
    run_name: str = ""
    epochs: int = 50
    max_samples: int = 0
    learning_rate: float = 3e-4
    weight_decay: float = 0.01
    grad_clip_norm: float = 1.0
    scheduler: str = "cosine"
    scheduler_restarts: int = 49
    scheduler_eta_min: float = 1e-6
    scheduler_cycle_decay: float = 0.98
    amp: bool = True
    amp_dtype: str = "bf16"
    compile_model: bool = True
    logging_samples: int = 50_000
    validation_samples: int = 250_000
    validation_max_batches: int = 0
    checkpoint_latest_samples: int = 500_000
    checkpoint_archive_samples: int = 5_000_000
    evaluate_at_end: bool = True
    # Keep the V10/V13 project so V14 is directly comparable.
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
        f"news-v14-opportunity-tfidf-token-transformer-d{config.model.d_model}"
        f"-a{config.model.attention_heads}-l{config.model.layers}"
        f"-w{config.loader.max_word_tokens}-c{config.loader.max_char_tokens}"
        f"-n{config.loader.max_numeric_tokens}-b{config.loader.batch_size}"
    )
