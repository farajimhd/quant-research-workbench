from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from src.data_provider.config import DEFAULT_PROCESSED_ROOT


DEFAULT_TRAIN_START = "2025-11-01"
DEFAULT_TRAIN_END = "2025-11-30"
DEFAULT_VALIDATION_START = "2025-12-01"
DEFAULT_VALIDATION_END = "2025-12-05"
DEFAULT_TEST_START = "2025-12-08"
DEFAULT_TEST_END = "2025-12-12"


@dataclass(slots=True)
class DataConfig:
    flatfiles_root: Path = Path("D:/market-data/flatfiles/us_stocks_sip")
    canonical_root: Path = Path("D:/market-data/flatfiles/us_stocks_sip/derived/canonical_events_v1")
    cache_root: Path = Path("D:/market-data/flatfiles/us_stocks_sip/derived/event_chunks_v1")
    train_start_date: str = DEFAULT_TRAIN_START
    train_end_date: str = DEFAULT_TRAIN_END
    validation_start_date: str = DEFAULT_VALIDATION_START
    validation_end_date: str = DEFAULT_VALIDATION_END
    test_start_date: str = DEFAULT_TEST_START
    test_end_date: str = DEFAULT_TEST_END
    chunk_ms: int = 500
    context_seconds: int = 60
    horizon_steps: int = 6
    horizon_seconds: int = 10
    use_target_cache_horizons: bool = True
    origin_stride_chunks: int = 1
    max_quote_events: int = 128
    max_trade_events: int = 192
    max_total_events: int = 256
    target_cache_horizon_chunks: tuple[int, ...] = (20, 40, 60, 120, 240, 600)
    max_target_cache_grid_rows_per_ticker_month: int = 5_000_000
    target_mode: str = "binary_magnitude_bps"
    binary_magnitude_bits: int = 12
    target_columns: tuple[str, ...] = ("close",)
    tickers: tuple[str, ...] = ()
    session_filter_mode: str = "market_time"
    session_timezone: str = "America/New_York"
    session_start_time_market: str = "04:00"
    session_end_time_market: str = "20:00"
    session_start_hour_utc: int = 8
    session_end_hour_utc: int = 22
    quote_size_lot_multiplier_before_2025_11_03: int = 100
    rebuild_cache: bool = False
    max_windows_per_ticker_session: int = 0
    shuffle_tickers: bool = True

    @property
    def context_chunks(self) -> int:
        return int(self.context_seconds * 1000 // self.chunk_ms)

    @property
    def horizon_chunks(self) -> int:
        return int(self.horizon_seconds * 1000 // self.chunk_ms)

    @property
    def target_horizon_chunks(self) -> tuple[int, ...]:
        if self.use_target_cache_horizons and self.target_cache_horizon_chunks:
            horizons = tuple(int(value) for value in self.target_cache_horizon_chunks[: self.horizon_steps])
            if len(horizons) == self.horizon_steps:
                return horizons
        return tuple((index + 1) * self.horizon_chunks for index in range(self.horizon_steps))

    @property
    def target_horizon_seconds(self) -> tuple[float, ...]:
        return tuple(value * self.chunk_ms / 1000.0 for value in self.target_horizon_chunks)

    @property
    def target_horizon_count(self) -> int:
        return len(self.target_horizon_chunks)


@dataclass(slots=True)
class ModelConfig:
    d_model: int = 256
    quote_hidden_dim: int = 256
    trade_hidden_dim: int = 256
    local_layers: int = 2
    global_layers: int = 6
    num_heads: int = 8
    ff_dim: int = 1024
    dropout: float = 0.1
    target_bit_count: int = 13
    direction_threshold_bps: float = 0.0


@dataclass(slots=True)
class TrainConfig:
    output_root: Path = DEFAULT_PROCESSED_ROOT / "models" / "inhouse_transformer" / "v22"
    batch_size: int = 512
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
    validation_window_count: int = 50000
    test_window_count: int = 50000
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
