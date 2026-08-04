from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from research.bar_gpt.v1.cohort import (
    BAR_GPT_TRAINING_TICKERS,
    BAR_GPT_COHORT_2TB_TABLE,
    BAR_GPT_COHORT_2TB_MANIFEST_TABLE,
    BAR_GPT_SOURCE_ALIAS_MANIFEST_TABLE,
    BAR_GPT_SIP_DAILY_SESSION_TABLE,
    BAR_GPT_SIP_DAILY_SESSION_MANIFEST_TABLE,
    BAR_GPT_VALIDATION_SLICES_2026,
)
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
    alias_manifest_table: str = BAR_GPT_SOURCE_ALIAS_MANIFEST_TABLE
    daily_table: str = BAR_GPT_SIP_DAILY_SESSION_TABLE
    daily_manifest_table: str = BAR_GPT_SIP_DAILY_SESSION_MANIFEST_TABLE
    condition_table: str = "intraday_condition_bars_by_time_ticker"
    condition_status_table: str = "intraday_base_bars_build_status"
    identity_database: str = "q_live"
    identity_interval_table: str = "id_symbol_interval_v1"
    identity_entity_table: str = "market_ticker_event_entity_v1"
    identity_event_table: str = "market_ticker_event_v1"
    split_database: str = "q_live"
    split_table: str = "market_stock_split_v1"
    base_timeframe_us: int = 1_000_000
    intraday_timeframes_us: tuple[int, ...] = INTRADAY_TIMEFRAMES_US
    calendar_timeframes: tuple[str, ...] = CALENDAR_TIMEFRAMES
    horizons_us: tuple[int, ...] = DEFAULT_HORIZONS_US
    tickers: tuple[str, ...] = BAR_GPT_TRAINING_TICKERS
    start_date: str = "2020-01-01"
    end_date: str = "2026-08-01"
    validation_start_date: str = "2026-01-01"
    daily_history_start_date: str = "2019-01-01"
    validation_slices: tuple[tuple[str, str, str], ...] = BAR_GPT_VALIDATION_SLICES_2026
    validation_blocks_per_slice: int = 4
    prior_session_halo: bool = True
    context_bars_1s: int = 2_048
    origin_bars_1s: int = 512
    daily_context_bars: int = 512
    batch_size: int = 2
    maximum_target_horizon_us: int = 3_600_000_000
    loader_workers: int = 8
    ready_queue_blocks: int = 64
    worker_prefetch_batches: int = 2
    clickhouse_max_threads_per_worker: int = 1
    clickhouse_max_block_size: int = 65_536
    clickhouse_max_memory_usage: int = 8 * 1024**3
    clickhouse_query_days: int = 7
    clickhouse_max_bytes_before_external_sort: int = 1024**3
    min_origins_per_block: int = 64
    coverage_blocks_per_unit: int = 16
    origin_fetch_candidate_blocks: int = 16
    origin_emit_blocks_per_chunk: int = 8
    pin_memory: bool = True
    persistent_workers: bool = True
    balance_activity_regimes: bool = True
    activity_regime_low: float = 1.0
    activity_regime_high: float = 25.0

    @property
    def right_support_bars_1s(self) -> int:
        return (self.maximum_target_horizon_us + self.base_timeframe_us - 1) // self.base_timeframe_us

    @property
    def training_tickers(self) -> tuple[str, ...]:
        validation = {ticker for ticker, _start, _end in self.validation_slices}
        return tuple(ticker for ticker in self.tickers if ticker not in validation)

    def validate(self) -> None:
        if "split_adjusted" in self.one_second_table or self.daily_table.endswith("_adjusted"):
            raise ValueError("globally adjusted bar authorities are retired; use raw bars with causal split metadata")
        if len(self.tickers) < 2:
            raise ValueError("at least two tickers are required for disjoint train/validation populations")
        if not self.horizons_us:
            raise ValueError("at least one prediction horizon is required")
        if self.context_bars_1s <= 0 or self.origin_bars_1s <= 0:
            raise ValueError("context and origin bars must be positive")
        if self.coverage_blocks_per_unit <= 0:
            raise ValueError("coverage_blocks_per_unit must be positive")
        if self.origin_fetch_candidate_blocks <= 0 or self.origin_emit_blocks_per_chunk <= 0:
            raise ValueError("origin fetch and emit chunk sizes must be positive")
        if self.origin_emit_blocks_per_chunk > self.origin_fetch_candidate_blocks:
            raise ValueError("origin emit blocks cannot exceed fetched candidate blocks")
        if self.maximum_target_horizon_us < max(self.horizons_us):
            raise ValueError("maximum_target_horizon_us must cover every configured horizon")
        if tuple(sorted(set(self.horizons_us))) != self.horizons_us:
            raise ValueError("horizons_us must be unique and strictly increasing")
        if any(value <= 0 or value % self.base_timeframe_us for value in self.horizons_us):
            raise ValueError("every horizon must be a positive integral multiple of the base timeframe")
        validation_tickers = {ticker for ticker, _start, _end in self.validation_slices}
        if not validation_tickers <= set(self.tickers):
            raise ValueError("fixed validation tickers must belong to the configured cohort")
        for ticker, start, end in self.validation_slices:
            if ticker not in self.tickers or not self.validation_start_date <= start < end <= self.end_date:
                raise ValueError(f"invalid validation slice: {(ticker, start, end)}")
        if self.validation_blocks_per_slice <= 0:
            raise ValueError("validation_blocks_per_slice must be positive")
        if not self.start_date <= self.validation_start_date <= self.end_date:
            raise ValueError("validation_start_date must lie inside the requested date range")
        if self.daily_history_start_date > self.start_date:
            raise ValueError("daily_history_start_date cannot be later than the training start")
        if self.batch_size <= 0 or self.loader_workers < 0:
            raise ValueError("batch_size must be positive and loader_workers cannot be negative")
        if self.ready_queue_blocks <= 0 or self.worker_prefetch_batches <= 0:
            raise ValueError("ready queue blocks and worker prefetch batches must be positive")
        if self.clickhouse_query_days <= 0 or self.clickhouse_max_bytes_before_external_sort <= 0:
            raise ValueError("ClickHouse query days and external-sort threshold must be positive")


@dataclass(slots=True)
class TrainConfig:
    output_root: Path = Path(r"D:\TradingML\runtimes\bar_gpt\v1\train")
    run_name: str = "bar-gpt-v1"
    epochs: int = 1
    max_samples: int = 0
    learning_rate: float = 3e-4
    weight_decay: float = 0.1
    grad_clip_norm: float = 1.0
    amp_dtype: str = "bf16"
    compile_model: bool = False
    gradient_accumulation_steps: int = 4
    cuda_prefetch: bool = True
    amp: bool = True
    seed: int = 17
    wandb_project: str = "bar-gpt-v1"
    wandb_entity: str = "mehdifaraji"
    wandb_mode: str = "auto"
    wandb_init_timeout: int = 120
    logging_samples: int = 65_536
    validation_batches: int = 16
    validation_runs_per_epoch: int = 4
    validation_interval_samples: int = 0
    warmup_samples: int = 1_048_576
    minimum_learning_rate: float = 3e-5
    checkpoint_latest_samples: int = 1_048_576
    checkpoint_archive_samples: int = 16_777_216
    progress_layout: str = "auto"
    autoregressive_weight: float = 0.35
    horizon_weight: float = 1.0
    availability_weight: float = 0.25
    condition_positive_weight: float = 32.0
    latent_prediction_weight: float = 0.05

    def validate(self) -> None:
        if self.epochs <= 0:
            raise ValueError("epochs must be positive")
        if self.max_samples < 0:
            raise ValueError("max_samples cannot be negative")
        if self.gradient_accumulation_steps <= 0:
            raise ValueError("gradient_accumulation_steps must be positive")
        if self.validation_runs_per_epoch <= 0 or self.validation_batches <= 0:
            raise ValueError("validation runs and batches must be positive")
        if self.validation_interval_samples < 0 or self.warmup_samples < 0:
            raise ValueError("validation interval and warmup samples cannot be negative")
        if self.learning_rate <= 0 or not 0 < self.minimum_learning_rate <= self.learning_rate:
            raise ValueError("learning rates must satisfy 0 < minimum <= peak")
        if self.grad_clip_norm <= 0:
            raise ValueError("grad_clip_norm must be positive")


@dataclass(slots=True)
class ExperimentConfig:
    model: BarGPTConfig
    data: DataConfig
    train: TrainConfig


def to_dict(config: Any) -> dict[str, Any]:
    if hasattr(config, "__dataclass_fields__"):
        return asdict(config)
    return dict(config)
