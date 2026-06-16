from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from research.mlops.compact_events import DEFAULT_CANONICAL_ROOT, DEFAULT_EVENTS_PER_CHUNK, DEFAULT_REFERENCE_DIR
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
from research.mlops.event_sample_cache import DEFAULT_SAMPLE_CACHE_ROOT


DEFAULT_TRAIN_START = "2025-11-01"
DEFAULT_TRAIN_END = "2025-11-30"
DEFAULT_VALIDATION_START = "2025-12-01"
DEFAULT_VALIDATION_END = "2025-12-05"


@dataclass(slots=True)
class DataConfig:
    data_source: str = "clickhouse_events"
    canonical_root: Path = DEFAULT_CANONICAL_ROOT
    precomputed_chunk_root: Path | None = None
    sample_cache_root: Path | None = DEFAULT_SAMPLE_CACHE_ROOT
    reference_dir: Path = DEFAULT_REFERENCE_DIR
    clickhouse_url: str = ""
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
    sample_cache_prefetch_shards: int = 2
    sample_cache_train_start_shard: int = 0
    sample_cache_train_max_shards: int = 0
    sample_cache_validation_split: str = "validation"
    sample_cache_validation_start_shard: int = 0
    sample_cache_validation_max_shards: int = 0
    sample_cache_validation_max_samples: int = 0
    sample_cache_shuffle_records: bool = True
    sample_cache_drop_last: bool = True
    sample_cache_interleave_shards: int = 1
    max_index_files: int = 0
    strict_lossless: bool = True


@dataclass(slots=True)
class MaskConfig:
    event_mask_ratio: float = 0.70
    # v8 is the fixed-mask ablation of v6: every training batch masks the same
    # 70% of event tokens so loss scale and throughput are easier to compare.
    event_mask_schedule: str = "fixed"
    event_mask_high_probability: float = 0.70
    event_mask_zero_probability: float = 0.10
    event_mask_low_probability: float = 0.20
    event_mask_high_min: float = 0.50
    event_mask_high_max: float = 0.80
    event_mask_low_min: float = 0.01
    event_mask_low_max: float = 0.50
    min_masked_events: int = 1
    header_bit_corruption_prob: float = 0.20
    header_bit_corruption_ratio: float = 0.05
    event_bit_corruption_prob: float = 0.30
    event_bit_corruption_ratio: float = 0.20


@dataclass(slots=True)
class ModelConfig:
    input_representation: str = "bit"
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
    event_weight: float = 1.0
    objective: str = "weighted"


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
    progress_layout: str = "auto"
    profile_first_steps: int = 0
    profile_training_every_steps: int = 10
    profile_inference_every_steps: int = 10
    decoder_chunk_size: int = 0
    pretrain_validation_frequency: int = 50
    pretrain_validation_steps: int = 4
    checkpoint_latest_steps: int = 10
    checkpoint_archive_steps: int = 5000
    checkpoint_best_train: bool = True
    checkpoint_best_val: bool = True
    num_workers: int = 0
    prefetch_factor: int = 1
    seed: int = 17
    amp: bool = True
    amp_dtype: str = "auto"
    amp_initial_scale: float = 1024.0
    amp_growth_interval: int = 10_000
    amp_max_scale: float = 2048.0
    amp_overflow_fatal_threshold: int = 8
    compile_model: bool = False
    wandb_project: str = "June2026-event-token-mae-v8-fixed-mask"
    wandb_entity: str = "mehdifaraji"
    wandb_run_name: str = ""


@dataclass(slots=True)
class ExperimentConfig:
    data: DataConfig = field(default_factory=DataConfig)
    masks: MaskConfig = field(default_factory=MaskConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    losses: LossConfig = field(default_factory=LossConfig)
    train: TrainConfig = field(default_factory=TrainConfig)
