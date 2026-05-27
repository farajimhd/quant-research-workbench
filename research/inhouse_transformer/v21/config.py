from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from src.data_provider.config import DEFAULT_PROCESSED_ROOT


DEFAULT_TRAIN_START = "2025-06-02"
DEFAULT_TRAIN_END = "2025-06-30"
DEFAULT_VALIDATION_START = "2025-07-01"
DEFAULT_VALIDATION_END = "2025-07-07"
DEFAULT_TEST_START = "2025-07-08"
DEFAULT_TEST_END = "2025-07-11"


@dataclass(slots=True)
class DataConfig:
    flatfiles_root: Path = Path("D:/market-data/flatfiles/us_stock_sip")
    cache_root: Path = Path("D:/market-data/flatfiles/us_stock_sip/derived/microstructure_1s_v1")
    train_start_date: str = DEFAULT_TRAIN_START
    train_end_date: str = DEFAULT_TRAIN_END
    validation_start_date: str = DEFAULT_VALIDATION_START
    validation_end_date: str = DEFAULT_VALIDATION_END
    test_start_date: str = DEFAULT_TEST_START
    test_end_date: str = DEFAULT_TEST_END
    one_second_context: int = 60
    ten_second_context: int = 60
    horizon_steps: int = 6
    horizon_seconds: int = 10
    origin_stride_seconds: int = 1
    target_mode: str = "binary_magnitude_bps"
    binary_magnitude_bits: int = 12
    target_columns: tuple[str, ...] = ("close",)
    tickers: tuple[str, ...] = ()
    max_tickers: int = 0
    session_start_hour_utc: int = 8
    session_end_hour_utc: int = 22
    quote_size_lot_multiplier_before_2025_11_03: int = 100
    rebuild_cache: bool = False
    max_windows_per_ticker_session: int = 0
    shuffle_tickers: bool = True


@dataclass(slots=True)
class ModelConfig:
    d_model: int = 256
    one_second_layers: int = 4
    ten_second_layers: int = 4
    feature_attention_layers: int = 1
    num_heads: int = 8
    ff_dim: int = 1024
    dropout: float = 0.1
    target_bit_count: int = 13
    direction_threshold_bps: float = 0.0


@dataclass(slots=True)
class TrainConfig:
    output_root: Path = DEFAULT_PROCESSED_ROOT / "models" / "inhouse_transformer" / "v21"
    batch_size: int = 4096
    epochs: int = 3
    max_steps: int = 0
    learning_rate: float = 3e-4
    weight_decay: float = 1e-4
    warmup_steps: int = 1000
    lr_scheduler: str = "cosine_warm_restarts"
    cosine_restart_t0_steps: int = 500
    cosine_restart_t_mult: int = 2
    min_learning_rate: float = 1e-6
    grad_clip_norm: float = 1.0
    logging_steps: int = 50
    eval_steps: int = 500
    validation_window_count: int = 100000
    test_window_count: int = 100000
    num_workers: int = 4
    prefetch_factor: int = 4
    seed: int = 17
    amp: bool = True
    compile_model: bool = False
    output_name: str = ""
    resume_latest: bool = True
    fresh_start: bool = False
    checkpoint_policy: str = "last_only"


@dataclass(slots=True)
class ExperimentConfig:
    data: DataConfig = field(default_factory=DataConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    train: TrainConfig = field(default_factory=TrainConfig)
