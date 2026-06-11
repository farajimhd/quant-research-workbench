from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from research.mlops.compact_events import DEFAULT_CANONICAL_ROOT, DEFAULT_EVENTS_PER_CHUNK, DEFAULT_PRECOMPUTED_V4_CHUNK_ROOT, DEFAULT_REFERENCE_DIR
from research.mlops.clickhouse_events import (
    DEFAULT_CLICKHOUSE_URL,
    DEFAULT_DATABASE,
    DEFAULT_EVENTS_TABLE,
    DEFAULT_MAX_ORIGIN_STRIDE,
    DEFAULT_MIN_ORIGIN_STRIDE,
    DEFAULT_ORIGINS_PER_SPAN,
    DEFAULT_QUERY_BUNDLE_SPANS,
    DEFAULT_TRAIN_INDEX_TABLE,
    DEFAULT_VALIDATION_INDEX_TABLE,
)


DEFAULT_TRAIN_START = "2025-11-01"
DEFAULT_TRAIN_END = "2025-11-30"
DEFAULT_VALIDATION_START = "2025-12-01"
DEFAULT_VALIDATION_END = "2025-12-05"


@dataclass(slots=True)
class DataConfig:
    data_source: str = "clickhouse_events"
    canonical_root: Path = DEFAULT_CANONICAL_ROOT
    precomputed_chunk_root: Path | None = DEFAULT_PRECOMPUTED_V4_CHUNK_ROOT
    reference_dir: Path = DEFAULT_REFERENCE_DIR
    clickhouse_url: str = DEFAULT_CLICKHOUSE_URL
    clickhouse_database: str = DEFAULT_DATABASE
    events_table: str = DEFAULT_EVENTS_TABLE
    train_index_table: str = DEFAULT_TRAIN_INDEX_TABLE
    validation_index_table: str = DEFAULT_VALIDATION_INDEX_TABLE
    index_table: str = ""
    train_start_date: str = DEFAULT_TRAIN_START
    train_end_date: str = DEFAULT_TRAIN_END
    validation_start_date: str = DEFAULT_VALIDATION_START
    validation_end_date: str = DEFAULT_VALIDATION_END
    tickers: tuple[str, ...] = ("ALL",)
    events_per_chunk: int = DEFAULT_EVENTS_PER_CHUNK
    num_spans: int = 128
    origins_per_span: int = DEFAULT_ORIGINS_PER_SPAN
    min_origin_stride: int = DEFAULT_MIN_ORIGIN_STRIDE
    max_origin_stride: int = DEFAULT_MAX_ORIGIN_STRIDE
    query_bundle_spans: int = DEFAULT_QUERY_BUNDLE_SPANS
    clickhouse_max_threads: int = 8
    clickhouse_max_memory_usage: str = "80G"
    month_cache_size: int = 8
    max_index_files: int = 0
    strict_lossless: bool = True


@dataclass(slots=True)
class MaskConfig:
    mask_ratio: float = 0.70
    header_mask_ratio: float = 0.50
    min_masked_bytes: int = 1


@dataclass(slots=True)
class ModelConfig:
    d_byte: int = 24
    d_model: int = 128
    embedding_dim: int = 32
    n_heads: int = 4
    encoder_layers: int = 6
    decoder_layers: int = 2
    ffn_mult: int = 4
    dropout: float = 0.08

    @property
    def ff_dim(self) -> int:
        return int(self.d_model * self.ffn_mult)


@dataclass(slots=True)
class LossConfig:
    header_weight: float = 1.0
    event_weight: float = 1.0


@dataclass(slots=True)
class TrainConfig:
    output_root: Path = Path("")
    batch_size: int = 4096
    max_steps: int = 10000
    epochs: int = 1
    learning_rate: float = 2e-4
    weight_decay: float = 1e-4
    scheduler: str = "cosine_warm_restarts"
    scheduler_t0_steps: int = 1000
    scheduler_t_mult: int = 2
    scheduler_eta_min: float = 1e-6
    grad_clip_norm: float = 1.0
    logging_steps: int = 10
    detailed_metrics_steps: int = 50
    profile_training_every_steps: int = 10
    profile_inference_every_steps: int = 10
    pretrain_validation_frequency: int = 50
    pretrain_validation_steps: int = 4
    checkpoint_latest_steps: int = 10
    checkpoint_archive_steps: int = 5000
    num_workers: int = 0
    prefetch_factor: int = 1
    seed: int = 17
    amp: bool = True
    compile_model: bool = False
    wandb_project: str = "May2026-compact-byte-event-modeling"
    wandb_entity: str = "mehdifaraji"
    wandb_run_name: str = ""


@dataclass(slots=True)
class ExperimentConfig:
    data: DataConfig = field(default_factory=DataConfig)
    masks: MaskConfig = field(default_factory=MaskConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    losses: LossConfig = field(default_factory=LossConfig)
    train: TrainConfig = field(default_factory=TrainConfig)
