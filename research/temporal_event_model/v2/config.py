from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from research.mlops.clickhouse_events import DEFAULT_CLICKHOUSE_URL, DEFAULT_DATABASE, DEFAULT_EVENTS_TABLE


MODEL_FAMILY = "temporal_event_model"
MODEL_VERSION = "v2"
JOB_TYPE = "return_horizon_pretrain"


@dataclass(slots=True)
class DataConfig:
    clickhouse_url: str = ""
    clickhouse_database: str = DEFAULT_DATABASE
    events_table: str = DEFAULT_EVENTS_TABLE
    train_index_table: str = "train_2019_to_2025"
    validation_index_table: str = "validation_2026"
    tickers: tuple[str, ...] = ("ALL",)
    events_per_chunk: int = 128
    context_chunks: int = 64
    window_days: int = 15
    context_lag_schedule: str = "dense_geometric"
    context_dense_fraction: float = 0.50
    context_max_lag_steps: int = 512
    train_stride_choices: tuple[int, ...] = (16, 32, 64, 128)
    validation_stride_choices: tuple[int, ...] = (16, 32, 64, 128)
    future_event_horizons: tuple[int, ...] = (8, 16, 32, 64, 128, 256, 512, 1024)
    origin_stride_events: int = 1
    block_max_events: int = 2_000_000
    min_samples_per_block: int = 512
    validation_blocks: int = 8
    validation_batches_per_block: int = 2
    clickhouse_max_threads: int = 8
    clickhouse_max_memory_usage: str = "120G"
    return_bps_scale: float = 100.0


@dataclass(slots=True)
class EncoderConfig:
    version: str = "v20"
    checkpoint: Path = Path("")
    checkpoint_search_root: Path = Path("//DESKTOP-SAAI85T/Workstation-D/TradingML/runtimes/masked_event_model/v20/pretrain")
    freeze: bool = True
    d_byte: int = 24
    d_model: int = 128
    embedding_dim: int = 32
    n_heads: int = 4
    encoder_layers: int = 6
    decoder_layers: int = 2
    ffn_mult: int = 4
    dropout: float = 0.08
    encoder_batch_size: int = 4096


@dataclass(slots=True)
class ModelConfig:
    embedding_dim: int = 32
    temporal_d_model: int = 256
    temporal_layers: int = 4
    temporal_heads: int = 8
    temporal_ffn_mult: int = 4
    dropout: float = 0.08
    global_context_dim: int = 0
    ticker_context_dim: int = 0

    @property
    def temporal_ff_dim(self) -> int:
        return int(self.temporal_d_model * self.temporal_ffn_mult)


@dataclass(slots=True)
class LossConfig:
    loss_name: str = "mse"
    huber_beta: float = 1.0


@dataclass(slots=True)
class TrainConfig:
    output_root: Path = Path("")
    batch_size: int = 512
    epochs: int = 5
    blocks_per_epoch: int = 128
    learning_rate: float = 2e-4
    weight_decay: float = 1e-4
    scheduler: str = "cosine_warm_restarts"
    scheduler_t0_steps: int = 1000
    scheduler_t_mult: int = 1
    scheduler_eta_min: float = 1e-6
    grad_clip_norm: float = 1.0
    logging_steps: int = 10
    detailed_metrics_steps: int = 100
    validation_frequency_steps: int = 500
    checkpoint_latest_steps: int = 100
    checkpoint_archive_steps: int = 5000
    seed: int = 17
    amp_dtype: str = "bf16"
    compile_model: bool = False
    wandb_project: str = "June2026-market-ai-temporal-v2"
    wandb_entity: str = "mehdifaraji"
    wandb_run_name: str = ""
    wandb_mode: str = "auto"
    wandb_timeout_seconds: int = 120


@dataclass(slots=True)
class ExperimentConfig:
    data: DataConfig = field(default_factory=DataConfig)
    encoder: EncoderConfig = field(default_factory=EncoderConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    losses: LossConfig = field(default_factory=LossConfig)
    train: TrainConfig = field(default_factory=TrainConfig)

