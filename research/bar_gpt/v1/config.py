from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from research.bar_gpt.v1.cohort import BAR_GPT_COHORT_2TB, BAR_GPT_COHORT_2TB_MANIFEST_TABLE, BAR_GPT_COHORT_2TB_TABLE
from research.bar_gpt.v1.features import MODEL_FEATURE_NAMES
from research.bar_gpt.v1.targets import TARGET_NAMES


INTRADAY_TIMEFRAMES_US: tuple[int, ...] = (
    1_000_000,
    5_000_000,
    30_000_000,
    60_000_000,
    300_000_000,
    900_000_000,
    3_600_000_000,
)
CALENDAR_TIMEFRAMES: tuple[str, ...] = ("1D", "1W", "1MO")
DEFAULT_HORIZONS_US: tuple[int, ...] = (
    5_000_000,
    30_000_000,
    60_000_000,
    300_000_000,
    900_000_000,
    3_600_000_000,
)

@dataclass(slots=True)
class BarGPTConfig:
    feature_dim: int = len(MODEL_FEATURE_NAMES)
    target_dim: int = len(TARGET_NAMES)
    d_model: int = 384
    n_layers: int = 8
    n_heads: int = 8
    n_kv_heads: int = 4
    ff_multiplier: float = 8.0 / 3.0
    dropout: float = 0.0
    rope_base: float = 10_000.0
    max_timeframes: int = 16
    max_horizons: int = 32
    horizon_rank: int = 96
    quantiles: tuple[float, ...] = (0.1, 0.5, 0.9)
    timeframe_fourier_dim: int = 32
    pathway_count: int = 3

    def validate(self) -> None:
        if self.d_model % self.n_heads:
            raise ValueError("d_model must be divisible by n_heads")
        if self.n_heads % self.n_kv_heads:
            raise ValueError("n_heads must be divisible by n_kv_heads")
        if (self.d_model // self.n_heads) % 2:
            raise ValueError("attention head dimension must be even for RoPE")
        if not self.quantiles:
            raise ValueError("at least one quantile is required")
        if self.timeframe_fourier_dim <= 0 or self.timeframe_fourier_dim % 2:
            raise ValueError("timeframe_fourier_dim must be a positive even number")


@dataclass(slots=True)
class DataConfig:
    database: str = "market_sip_compact"
    one_second_table: str = BAR_GPT_COHORT_2TB_TABLE
    manifest_table: str = BAR_GPT_COHORT_2TB_MANIFEST_TABLE
    daily_table: str = "macro_bars_by_time_symbol"
    base_timeframe_us: int = 1_000_000
    intraday_timeframes_us: tuple[int, ...] = INTRADAY_TIMEFRAMES_US
    calendar_timeframes: tuple[str, ...] = CALENDAR_TIMEFRAMES
    horizons_us: tuple[int, ...] = DEFAULT_HORIZONS_US
    tickers: tuple[str, ...] = BAR_GPT_COHORT_2TB
    start_date: str = "2019-01-01"
    end_date: str = "2027-01-01"
    validation_start_date: str = "2025-01-01"
    validation_ticker_fraction: float = 0.15
    context_bars_1s: int = 2_048
    origin_bars_1s: int = 512
    daily_context_bars: int = 512
    batch_size: int = 2
    maximum_target_horizon_us: int = 3_600_000_000
    loader_workers: int = 4
    ready_queue_blocks: int = 4
    clickhouse_max_threads_per_worker: int = 2
    clickhouse_max_block_size: int = 65_536
    clickhouse_max_memory_usage: int = 8 * 1024**3
    min_origins_per_block: int = 64
    pin_memory: bool = True
    persistent_workers: bool = True
    balance_activity_regimes: bool = True
    activity_regime_low: float = 1.0
    activity_regime_high: float = 25.0

    @property
    def right_support_bars_1s(self) -> int:
        return (self.maximum_target_horizon_us + self.base_timeframe_us - 1) // self.base_timeframe_us

    def validate(self) -> None:
        if len(self.tickers) < 2:
            raise ValueError("at least two tickers are required for disjoint train/validation populations")
        if not self.horizons_us:
            raise ValueError("at least one prediction horizon is required")
        if self.context_bars_1s <= 0 or self.origin_bars_1s <= 0:
            raise ValueError("context and origin bars must be positive")
        if self.maximum_target_horizon_us < max(self.horizons_us):
            raise ValueError("maximum_target_horizon_us must cover every configured horizon")
        if tuple(sorted(set(self.horizons_us))) != self.horizons_us:
            raise ValueError("horizons_us must be unique and strictly increasing")
        if any(value <= 0 or value % self.base_timeframe_us for value in self.horizons_us):
            raise ValueError("every horizon must be a positive integral multiple of the base timeframe")
        if not 0.0 < self.validation_ticker_fraction < 1.0:
            raise ValueError("validation_ticker_fraction must be between zero and one")
        if self.batch_size <= 0 or self.loader_workers < 0:
            raise ValueError("batch_size must be positive and loader_workers cannot be negative")


@dataclass(slots=True)
class TrainConfig:
    output_root: Path = Path(r"D:\TradingML\runtimes\bar_gpt\v1\train")
    run_name: str = "bar-gpt-v1"
    epochs: int = 1
    max_samples: int = 50_000_000
    learning_rate: float = 3e-4
    weight_decay: float = 0.1
    grad_clip_norm: float = 1.0
    amp_dtype: str = "bf16"
    compile_model: bool = False
    amp: bool = True
    seed: int = 17
    wandb_project: str = "bar-gpt-v1"
    wandb_entity: str = "mehdifaraji"
    wandb_mode: str = "auto"
    wandb_init_timeout: int = 120
    logging_samples: int = 65_536
    validation_samples: int = 262_144
    validation_batches: int = 32
    checkpoint_latest_samples: int = 1_048_576
    checkpoint_archive_samples: int = 16_777_216
    progress_layout: str = "auto"
    autoregressive_weight: float = 0.35
    horizon_weight: float = 1.0
    availability_weight: float = 0.25
    latent_prediction_weight: float = 0.05


@dataclass(slots=True)
class ExperimentConfig:
    model: BarGPTConfig
    data: DataConfig
    train: TrainConfig


def to_dict(config: Any) -> dict[str, Any]:
    if hasattr(config, "__dataclass_fields__"):
        return asdict(config)
    return dict(config)
