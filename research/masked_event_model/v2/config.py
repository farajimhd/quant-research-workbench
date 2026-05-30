from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_TRAIN_START = "2025-11-01"
DEFAULT_TRAIN_END = "2025-11-30"
DEFAULT_VALIDATION_START = "2025-12-01"
DEFAULT_VALIDATION_END = "2025-12-05"
DEFAULT_TEST_START = "2025-12-08"
DEFAULT_TEST_END = "2025-12-12"


@dataclass(slots=True)
class DataConfig:
    cache_root: Path = Path("D:/market-data/flatfiles/us_stocks_sip/derived/event_chunks_v2")
    canonical_root: Path = Path("D:/market-data/flatfiles/us_stocks_sip/derived/canonical_events_v2")
    train_start_date: str = DEFAULT_TRAIN_START
    train_end_date: str = DEFAULT_TRAIN_END
    validation_start_date: str = DEFAULT_VALIDATION_START
    validation_end_date: str = DEFAULT_VALIDATION_END
    test_start_date: str = DEFAULT_TEST_START
    test_end_date: str = DEFAULT_TEST_END
    tickers: tuple[str, ...] = ("ALL",)
    chunk_ms: int = 500
    context_seconds: int = 30
    origin_stride_chunks: int = 1
    max_quote_events: int = 128
    max_trade_events: int = 192
    max_total_events: int = 256
    binary_magnitude_bits: int = 12
    row_block_size: int = 8192
    loader_progress_windows: int = 256
    shuffle_files: bool = True
    shuffle_windows: bool = True
    max_files: int = 0
    max_windows_per_file: int = 0

    @property
    def context_chunks(self) -> int:
        return int(self.context_seconds * 1000 // self.chunk_ms)

    @property
    def target_bit_count(self) -> int:
        return 1 + int(self.binary_magnitude_bits)


@dataclass(slots=True)
class MaskConfig:
    mask_ratio: float = 0.70
    chunk_mask_ratio: float = 0.30
    span_mask_ratio: float = 0.20
    tail_mask_ratio: float = 0.15
    modality_mask_ratio: float = 0.15
    event_mask_ratio: float = 0.25
    field_mask_ratio: float = 0.10
    min_mask_ratio: float = 0.55
    max_span_chunks: int = 8
    max_tail_chunks: int = 12


@dataclass(slots=True)
class ModelConfig:
    d_model: int = 256
    n_heads: int = 4
    quote_event_layers: int = 2
    trade_event_layers: int = 2
    temporal_layers: int = 8
    decoder_layers: int = 4
    ffn_mult: int = 4
    dropout: float = 0.08
    encoder_visible_ratio: float = 0.30

    @property
    def ff_dim(self) -> int:
        return int(self.d_model * self.ffn_mult)


@dataclass(slots=True)
class LossConfig:
    quote_weight: float = 1.0
    trade_weight: float = 1.0
    summary_weight: float = 0.5
    event_kind_weight: float = 0.2
    forecast_probe_weight: float = 0.0


@dataclass(slots=True)
class ProbeConfig:
    enabled: bool = True
    every_steps: int = 5000
    train_steps: int = 200
    train_windows: int = 20000
    val_windows: int = 20000
    batch_size: int = 0
    hidden_dim: int = 0
    learning_rate: float = 1e-3


@dataclass(slots=True)
class TrainConfig:
    output_root: Path = Path("D:/TradingData/quant-research-workbench/market_data/models/masked_event_model/v2")
    batch_size: int = 256
    epochs: int = 3
    max_steps: int = 0
    learning_rate: float = 2e-4
    weight_decay: float = 1e-4
    scheduler: str = "cosine_warm_restarts"
    scheduler_t0_steps: int = 1000
    scheduler_t_mult: int = 2
    scheduler_eta_min: float = 1e-6
    grad_clip_norm: float = 1.0
    logging_steps: int = 1
    detailed_metrics_steps: int = 10
    profile_training_every_steps: int = 10
    profile_inference_every_steps: int = 10
    pretrain_validation_frequency: int = 50
    pretrain_validation_steps: int = 4
    checkpoint_steps: int = 1000
    num_workers: int = 0
    prefetch_factor: int = 1
    loader_prefetch_batches: int = 1
    seed: int = 17
    amp: bool = True
    compile_model: bool = False
    resume_latest: bool = True
    fresh_start: bool = False
    checkpoint_policy: str = "last_only"
    wandb_project: str = "May2026-masked-event-modeling"
    wandb_entity: str = "mehdifaraji"
    wandb_run_name: str = "mem-v2-d256-e2-t8-d4-mask70-chunk500-nov2025"


@dataclass(slots=True)
class ExperimentConfig:
    data: DataConfig = field(default_factory=DataConfig)
    masks: MaskConfig = field(default_factory=MaskConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    losses: LossConfig = field(default_factory=LossConfig)
    probe: ProbeConfig = field(default_factory=ProbeConfig)
    train: TrainConfig = field(default_factory=TrainConfig)
