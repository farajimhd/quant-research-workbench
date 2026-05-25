from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from src.data_provider.config import DEFAULT_PROCESSED_ROOT


DEFAULT_TRAIN_START = "2024-01-22"
DEFAULT_TRAIN_END = "2025-12-31"
DEFAULT_VALIDATION_START = "2026-01-01"
DEFAULT_VALIDATION_END = "2026-02-28"
DEFAULT_TEST_START = "2026-03-01"
DEFAULT_TEST_END = ""


@dataclass(slots=True)
class DataConfig:
    processed_root: Path = DEFAULT_PROCESSED_ROOT
    train_start_date: str = DEFAULT_TRAIN_START
    train_end_date: str = DEFAULT_TRAIN_END
    validation_start_date: str = DEFAULT_VALIDATION_START
    validation_end_date: str = DEFAULT_VALIDATION_END
    test_start_date: str = DEFAULT_TEST_START
    test_end_date: str = DEFAULT_TEST_END
    timeframe: str = "1m"
    session_scope: str = "all"
    context_length: int = 64
    horizon: int = 3
    target_mode: str = "return_bps"
    target_columns: tuple[str, ...] = ("open", "high", "low", "close")
    input_feature_columns: tuple[str, ...] = (
        "close_return_bps",
        "open_to_prev_close_bps",
        "high_to_close_bps",
        "low_to_close_bps",
        "close_to_open_bps",
        "volume_log",
        "volume_log_change",
        "transactions_log",
        "transactions_log_change",
        "spread_bps",
        "quoted_depth_log",
        "quote_imbalance",
        "quote_valid_ratio",
    )
    time_feature_columns: tuple[str, ...] = (
        "minute_sin",
        "minute_cos",
        "regular_position_sin",
        "regular_position_cos",
        "is_premarket",
        "is_regular",
        "is_afterhours",
        "is_new_session",
        "gap_minutes_clipped",
    )
    tickers: tuple[str, ...] = ()
    max_tickers: int = 2000
    allow_target_across_session: bool = False
    carry_context_across_session: bool = True


@dataclass(slots=True)
class ModelConfig:
    d_model: int = 256
    feature_attention_layers: int = 1
    feature_attention_chunk_size: int = 32768
    temporal_layers: int = 6
    num_heads: int = 8
    ff_dim: int = 1024
    dropout: float = 0.1
    direction_loss_weight: float = 0.0
    direction_threshold_bps: float = 0.0


@dataclass(slots=True)
class TrainConfig:
    batch_size: int = 1024
    epochs: int = 1
    max_steps: int = 0
    learning_rate: float = 3e-4
    weight_decay: float = 1e-4
    warmup_steps: int = 1000
    lr_scheduler: str = "plateau"
    lr_plateau_factor: float = 0.5
    lr_plateau_patience: int = 3
    lr_plateau_threshold: float = 1e-4
    min_learning_rate: float = 1e-6
    grad_clip_norm: float = 1.0
    logging_steps: int = 50
    eval_steps: int = 500
    validation_window_count: int = 30000
    test_window_count: int = 50000
    max_batches_per_session: int = 0
    count_coverage: bool = False
    num_workers: int = 0
    seed: int = 17
    amp: bool = True
    compile_model: bool = False
    output_name: str = ""
    resume_latest: bool = False


@dataclass(slots=True)
class ExperimentConfig:
    data: DataConfig = field(default_factory=DataConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    train: TrainConfig = field(default_factory=TrainConfig)
