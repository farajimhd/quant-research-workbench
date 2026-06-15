from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from research.mlops.clickhouse_events import DEFAULT_CLICKHOUSE_URL, DEFAULT_DATABASE, DEFAULT_EVENTS_TABLE


MODEL_FAMILY = "temporal_event_model"
MODEL_VERSION = "v1"
JOB_TYPE = "temporal_pretrain"


@dataclass(slots=True)
class DataConfig:
    clickhouse_url: str = ""
    clickhouse_database: str = DEFAULT_DATABASE
    events_table: str = DEFAULT_EVENTS_TABLE
    train_index_table: str = "train_2019_to_2025"
    validation_index_table: str = "validation_2026"
    tickers: tuple[str, ...] = ("ALL",)
    events_per_chunk: int = 128
    context_chunks: int = 16
    target_chunks: int = 1
    window_days: int = 15
    train_stride_choices: tuple[int, ...] = (16, 32, 64, 128)
    validation_stride_choices: tuple[int, ...] = (16, 32, 64, 128)
    origin_stride_events: int = 1
    block_max_events: int = 2_000_000
    min_samples_per_block: int = 512
    validation_blocks: int = 8
    validation_batches_per_block: int = 2
    clickhouse_max_threads: int = 8
    clickhouse_max_memory_usage: str = "120G"


@dataclass(slots=True)
class EncoderConfig:
    version: str = "v7"
    checkpoint: Path = Path("")
    freeze: bool = True
    d_byte: int = 24
    d_model: int = 128
    embedding_dim: int = 32
    n_heads: int = 4
    encoder_layers: int = 6
    decoder_layers: int = 2
    ffn_mult: int = 4
    dropout: float = 0.08


@dataclass(slots=True)
class ModelConfig:
    embedding_dim: int = 32
    temporal_d_model: int = 256
    temporal_layers: int = 6
    temporal_heads: int = 8
    temporal_ffn_mult: int = 4
    decoder_layers: int = 2
    dropout: float = 0.08

    @property
    def temporal_ff_dim(self) -> int:
        return int(self.temporal_d_model * self.temporal_ffn_mult)


@dataclass(slots=True)
class LossConfig:
    event_weight: float = 1.0
    header_weight: float = 0.25


@dataclass(slots=True)
class TrainConfig:
    output_root: Path = Path("")
    batch_size: int = 256
    max_steps: int = 10_000
    epochs: int = 1
    learning_rate: float = 2e-4
    weight_decay: float = 1e-4
    scheduler: str = "cosine_warm_restarts"
    scheduler_t0_steps: int = 1000
    scheduler_t_mult: int = 2
    scheduler_eta_min: float = 1e-6
    grad_clip_norm: float = 1.0
    logging_steps: int = 10
    detailed_metrics_steps: int = 100
    validation_frequency: int = 250
    checkpoint_latest_steps: int = 25
    checkpoint_archive_steps: int = 5000
    seed: int = 17
    amp: bool = True
    compile_model: bool = False
    progress_layout: str = "auto"
    wandb_project: str = "June2026-single-ticker-temporal-event-model"
    wandb_entity: str = "mehdifaraji"
    wandb_run_name: str = ""


@dataclass(slots=True)
class ExperimentConfig:
    data: DataConfig = field(default_factory=DataConfig)
    encoder: EncoderConfig = field(default_factory=EncoderConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    losses: LossConfig = field(default_factory=LossConfig)
    train: TrainConfig = field(default_factory=TrainConfig)
