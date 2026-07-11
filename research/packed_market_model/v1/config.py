from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from research.mlops.paths import default_run_root

from research.packed_market_model.v1 import MODEL_FAMILY, MODEL_VERSION


@dataclass(slots=True)
class LoaderConfig:
    data_source: str = "clickhouse"
    cache_root: Path = Path(r"D:\market-data\prepared\packed_market_block_cache\legacy_debug")
    months: tuple[str, ...] = ()
    tickers: tuple[str, ...] = ()
    shuffle_blocks: bool = False
    seed: int = 17
    max_blocks: int = 0
    database: str = "market_sip_compact"
    events_table_base: str = "events"
    events_ticker_day_index_table: str = "events_ticker_day_index"
    ticker_workers: int = 24
    ready_queue_blocks: int = 8
    target_origin_count_per_block: int = 65_536
    event_context_rows: int = 1_024
    future_event_guard_rows: int = 262_144
    max_threads_per_query: int = 4
    max_memory_usage: str = "32G"
    worker_memory_limit_mib: int = 12_288


@dataclass(slots=True)
class ModelConfig:
    event_feature_names: tuple[str, ...] = ()
    label_names: tuple[str, ...] = ()
    event_feature_dim: int = 0
    d_model: int = 384
    event_layers: int = 8
    event_kernel_size: int = 9
    event_dropout: float = 0.05
    head_hidden_dim: int = 512
    max_position_embeddings: int = 4_194_304
    use_position_embedding: bool = True


@dataclass(slots=True)
class TrainConfig:
    output_root: Path = default_run_root(MODEL_FAMILY, MODEL_VERSION, "train", "packed-v1-default")
    run_name: str = ""
    max_samples: int = 2_000_000
    max_blocks: int = 0
    epochs: int = 1
    learning_rate: float = 1e-3
    weight_decay: float = 0.01
    grad_clip_norm: float = 1.0
    scheduler: str = "cosine"
    scheduler_eta_min: float = 1e-6
    scheduler_cycle_samples: int = 1_024_000
    scheduler_decay_cycles: int = 100
    scheduler_decay_factor: float = 0.95
    amp: bool = True
    amp_dtype: str = "bf16"
    compile_model: bool = True
    optimizer_foreach: bool = True
    logging_samples: int = 65_536
    validation_samples: int = 262_144
    checkpoint_latest_samples: int = 1_048_576
    checkpoint_archive_samples: int = 16_777_216
    progress_layout: str = "auto"
    wandb_project: str = "packed-market-model-v1"
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
    if hasattr(config, "__dataclass_fields__"):
        return asdict(config)
    return dict(config)


def default_run_name(config: ExperimentConfig) -> str:
    if config.train.run_name:
        return config.train.run_name
    month_token = "-".join(config.loader.months) if config.loader.months else "allmonths"
    return f"packed-v1-{month_token}-d{config.model.d_model}-lr{config.train.learning_rate:g}"


def parse_csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in str(value or "").split(",") if item.strip())
