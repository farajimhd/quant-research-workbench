from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from research.bar_gpt.v1.cohort import (
    BAR_GPT_IDENTITY_HOLDOUT_TICKERS,
    BAR_GPT_TRAINING_TICKERS,
    BAR_GPT_COHORT_2TB_TABLE,
    BAR_GPT_COHORT_2TB_MANIFEST_TABLE,
    BAR_GPT_SOURCE_ALIAS_MANIFEST_TABLE,
    BAR_GPT_SIP_DAILY_SESSION_TABLE,
    BAR_GPT_SIP_DAILY_SESSION_MANIFEST_TABLE,
    BAR_GPT_VALIDATION_SLICES_2026,
)
from research.bar_gpt.v1.features import MODEL_FEATURE_NAMES
from research.bar_gpt.v1.targets import AUTOREGRESSIVE_TARGET_NAMES, TARGET_NAMES


OFFLINE_PRODUCTION_BATCH_SIZE = 16
OFFLINE_PRODUCTION_LOADER_WORKERS = 12
OFFLINE_PRODUCTION_READY_QUEUE_BLOCKS = 128
OFFLINE_PRODUCTION_WORKER_PREFETCH_BATCHES = 1
OFFLINE_PRODUCTION_LENGTH_BUCKET_BATCHES = 4


BAR_GPT_WANDB_PROJECT = "bar gpt"


INTRADAY_TIMEFRAMES_US: tuple[int, ...] = (
    1_000_000,
    5_000_000,
    10_000_000,
    30_000_000,
    60_000_000,
    300_000_000,
    1_800_000_000,
    3_600_000_000,
)
CALENDAR_TIMEFRAMES: tuple[str, ...] = ("1D", "1W", "1MO")
TIMEFRAME_US: dict[str, int] = {
    "1s": 1_000_000, "5s": 5_000_000, "10s": 10_000_000, "30s": 30_000_000,
    "1m": 60_000_000, "5m": 300_000_000, "30m": 1_800_000_000, "1h": 3_600_000_000,
}
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
    autoregressive_target_dim: int = len(AUTOREGRESSIVE_TARGET_NAMES)
    d_model: int = 384
    n_layers: int = 8
    n_heads: int = 8
    n_kv_heads: int = 4
    ff_multiplier: float = 8.0 / 3.0
    dropout: float = 0.08
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
        if not 0.0 <= self.dropout < 1.0:
            raise ValueError("dropout must satisfy 0 <= dropout < 1")
        if (self.d_model // self.n_heads) % 2:
            raise ValueError("attention head dimension must be even for RoPE")
        if not self.quantiles:
            raise ValueError("at least one quantile is required")
        if self.timeframe_fourier_dim <= 0 or self.timeframe_fourier_dim % 2:
            raise ValueError("timeframe_fourier_dim must be a positive even number")


@dataclass(slots=True)
class DataConfig:
    # Stream v13 compiles directly from compact events. An eligible trade is
    # mandatory for every stored 1s token, origin, and intraday context row;
    # quotes may enrich a trade-bearing second but cannot create one.
    loader_stream_contract_version: int = 13
    source_mode: str = "direct_events"
    events_table_base: str = "events"
    condition_reference_table: str = "event_condition_token_reference"
    max_quote_spread_bps: float = 1_000.0
    database: str = "market_sip_compact"
    one_second_table: str = BAR_GPT_COHORT_2TB_TABLE
    manifest_table: str = BAR_GPT_COHORT_2TB_MANIFEST_TABLE
    alias_manifest_table: str = BAR_GPT_SOURCE_ALIAS_MANIFEST_TABLE
    daily_table: str = BAR_GPT_SIP_DAILY_SESSION_TABLE
    daily_manifest_table: str = BAR_GPT_SIP_DAILY_SESSION_MANIFEST_TABLE
    condition_table: str = "intraday_condition_bars_by_time_ticker"
    condition_status_table: str = "intraday_base_bars_build_status"
    # Populated by training preflight. A channel without positive evidence in
    # the training authority must not be learned as an always-negative label.
    condition_target_active: tuple[bool, bool, bool, bool] = (True, True, True, True)
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
    start_date: str = "2019-01-01"
    end_date: str = "2026-08-01"
    validation_start_date: str = "2026-01-01"
    daily_history_start_date: str = "2019-01-01"
    validation_slices: tuple[tuple[str, str, str], ...] = BAR_GPT_VALIDATION_SLICES_2026
    validation_blocks_per_slice: int = 2
    prior_session_halo: bool = True
    context_bars_1s: int = 720
    origin_bars_1s: int = 512
    intraday_context_bars: tuple[tuple[str, int], ...] = (
        ("1s", 720), ("5s", 360), ("10s", 360), ("30s", 240),
        ("1m", 240), ("5m", 96), ("30m", 16), ("1h", 8),
    )
    calendar_context_bars: tuple[tuple[str, int], ...] = (("1D", 90), ("1W", 52), ("1MO", 24))
    calendar_warmup_daily_bars: int = 500
    daily_context_bars: int = 90
    batch_size: int = 2
    maximum_target_horizon_us: int = 3_600_000_000
    loader_workers: int = 8
    ready_queue_blocks: int = 64
    worker_prefetch_batches: int = 2
    # Bounded deterministic look-ahead used only by the offline loader to
    # group blocks with similar valid-origin lengths and reduce padding.
    offline_length_bucket_batches: int = 4
    clickhouse_max_threads_per_worker: int = 1
    clickhouse_max_block_size: int = 65_536
    clickhouse_max_memory_usage: int = 8 * 1024**3
    clickhouse_query_days: int = 7
    # Keep several bounded Arrow pages in flight per worker.  This is page
    # concurrency, not ClickHouse execution threads within a query.
    clickhouse_prefetch_pages: int = 4
    clickhouse_max_bytes_before_external_sort: int = 1024**3
    clickhouse_retry_attempts: int = 5
    clickhouse_retry_initial_seconds: float = 0.5
    clickhouse_retry_max_seconds: float = 8.0
    min_origins_per_block: int = 64
    coverage_mode: str = "sequential"
    coverage_blocks_per_unit: int = 16
    origin_fetch_candidate_blocks: int = 16
    origin_emit_blocks_per_chunk: int = 16
    pin_memory: bool = True
    persistent_workers: bool = True
    balance_activity_regimes: bool = True
    activity_regime_low: float = 1.0
    activity_regime_high: float = 25.0

    @property
    def right_support_bars_1s(self) -> int:
        return (self.maximum_target_horizon_us + self.base_timeframe_us - 1) // self.base_timeframe_us

    @property
    def intraday_context_by_name(self) -> dict[str, int]:
        return dict(self.intraday_context_bars)

    @property
    def calendar_context_by_name(self) -> dict[str, int]:
        return dict(self.calendar_context_bars)

    @property
    def attention_window_by_name(self) -> dict[str, int]:
        """Direct causal attention span for each model-ready view.

        The 1s stream contains the current origin in addition to its configured
        past context. Coarser and calendar streams gather their latest completed
        context bar directly, so their configured count is already inclusive.
        """
        windows = self.intraday_context_by_name
        windows["1s"] = int(windows["1s"]) + 1
        windows.update(self.calendar_context_by_name)
        return windows

    @property
    def intraday_warmup_bars_1s(self) -> int:
        """Maximum sparse 1s source-buffer capacity, never a context length."""
        contexts = self.intraday_context_by_name
        return max(int(contexts[name]) * (TIMEFRAME_US[name] // self.base_timeframe_us) for name in contexts)

    @property
    def training_tickers(self) -> tuple[str, ...]:
        holdout = set(BAR_GPT_IDENTITY_HOLDOUT_TICKERS)
        return tuple(ticker for ticker in self.tickers if ticker not in holdout)

    def validate(self) -> None:
        if self.loader_stream_contract_version != 13:
            raise ValueError("this BarGPT version requires loader_stream_contract_version 13")
        if self.source_mode not in {"direct_events", "materialized_bars"}:
            raise ValueError("source_mode must be direct_events or materialized_bars")
        if not self.events_table_base or not self.condition_reference_table:
            raise ValueError("direct event source names cannot be empty")
        if self.max_quote_spread_bps <= 0:
            raise ValueError("max_quote_spread_bps must be positive")
        if "split_adjusted" in self.one_second_table or self.daily_table.endswith("_adjusted"):
            raise ValueError("globally adjusted bar authorities are retired; use raw bars with causal split metadata")
        if not self.tickers:
            raise ValueError("at least one ticker is required")
        if not self.horizons_us:
            raise ValueError("at least one prediction horizon is required")
        if len(self.condition_target_active) != 4:
            raise ValueError("condition_target_active must contain four condition channels")
        if self.context_bars_1s <= 0 or self.origin_bars_1s <= 0:
            raise ValueError("context and origin bars must be positive")
        if set(self.intraday_context_by_name) != {"1s", "5s", "10s", "30s", "1m", "5m", "30m", "1h"}:
            raise ValueError("intraday_context_bars must define the fixed intraday view contract")
        if int(self.context_bars_1s) != int(self.intraday_context_by_name["1s"]):
            raise ValueError("context_bars_1s must equal the authoritative intraday 1s context")
        if set(self.calendar_context_by_name) != {"1D", "1W", "1MO"}:
            raise ValueError("calendar_context_bars must define 1D, 1W, and 1MO")
        if any(int(value) <= 0 for value in (*self.intraday_context_by_name.values(), *self.calendar_context_by_name.values())):
            raise ValueError("all context lengths must be positive")
        if self.calendar_warmup_daily_bars < max(self.calendar_context_by_name.values()):
            raise ValueError("calendar daily warmup must cover every calendar context")
        if self.coverage_blocks_per_unit <= 0:
            raise ValueError("coverage_blocks_per_unit must be positive")
        if self.coverage_mode not in {"sequential", "stratified"}:
            raise ValueError("coverage_mode must be sequential or stratified")
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
        if len(validation_tickers) != len(self.validation_slices):
            raise ValueError("fixed validation requires exactly one slice per ticker")
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
        if self.offline_length_bucket_batches < 0:
            raise ValueError("offline_length_bucket_batches cannot be negative")
        if self.clickhouse_query_days <= 0 or self.clickhouse_prefetch_pages <= 0 or self.clickhouse_max_bytes_before_external_sort <= 0:
            raise ValueError("ClickHouse query days and external-sort threshold must be positive")
        if self.clickhouse_retry_attempts <= 0:
            raise ValueError("ClickHouse retry attempts must be positive")
        if self.clickhouse_retry_initial_seconds < 0 or self.clickhouse_retry_max_seconds < 0:
            raise ValueError("ClickHouse retry delays cannot be negative")
        if self.clickhouse_retry_max_seconds < self.clickhouse_retry_initial_seconds:
            raise ValueError("ClickHouse retry maximum cannot be below the initial delay")


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
    wandb_project: str = BAR_GPT_WANDB_PROJECT
    wandb_entity: str = "mehdifaraji"
    wandb_mode: str = "auto"
    wandb_init_timeout: int = 120
    # A default optimizer update currently covers 131,072 origins.  Logging
    # below that cadence forces a CUDA-to-host scalar transfer after every
    # update and prevents the CPU from getting ahead of the GPU.
    logging_samples: int = 1_048_576
    training_metrics_interval_samples: int = 8_388_608
    validation_batches: int = 16
    # Validation is intentionally spread across the epoch to expose drift;
    # the epoch-end evaluation is included in this count.
    validation_runs_per_epoch: int = 100
    validation_interval_samples: int = 0
    # First validation is deliberately early enough to catch a bad run; the
    # remaining validations are spread across the epoch.
    validation_initial_samples: int = 33_554_432
    warmup_samples: int = 0
    warmup_fraction: float = 0.01
    minimum_learning_rate: float = 3e-5
    cosine_cycle_samples: int = 100_000_000
    cosine_restart_decay: float = 0.98
    scheduler_mode: str = "cosine-restarts"
    # A consistent snapshot is staged once after each validation evaluation;
    # disk serialization remains asynchronous while training resumes.
    checkpoint_validation_evaluations: int = 1
    progress_layout: str = "auto"
    autoregressive_weight: float = 0.35
    horizon_weight: float = 1.0
    availability_weight: float = 0.25
    condition_positive_weight: float = 32.0
    direction_weight: float = 0.10
    direction_neutral_bps: float = 1.0
    latent_prediction_weight: float = 0.05

    def validate(self) -> None:
        if self.epochs <= 0:
            raise ValueError("epochs must be positive")
        if self.max_samples < 0:
            raise ValueError("max_samples cannot be negative")
        if self.gradient_accumulation_steps <= 0:
            raise ValueError("gradient_accumulation_steps must be positive")
        if self.training_metrics_interval_samples <= 0:
            raise ValueError("training_metrics_interval_samples must be positive")
        if self.checkpoint_validation_evaluations <= 0:
            raise ValueError("checkpoint_validation_evaluations must be positive")
        if self.validation_runs_per_epoch <= 0 or self.validation_batches < 0:
            raise ValueError("validation runs per epoch must be positive and validation batches cannot be negative")
        if self.validation_interval_samples < 0 or self.validation_initial_samples <= 0 or self.warmup_samples < 0:
            raise ValueError("validation interval/initial samples must be positive and warmup samples cannot be negative")
        if not 0 <= self.warmup_fraction < 1:
            raise ValueError("warmup_fraction must satisfy 0 <= fraction < 1")
        if self.learning_rate <= 0 or not 0 < self.minimum_learning_rate <= self.learning_rate:
            raise ValueError("learning rates must satisfy 0 < minimum <= peak")
        if self.cosine_cycle_samples <= 0 or not 0 < self.cosine_restart_decay <= 1:
            raise ValueError("cosine restart settings are invalid")
        if self.scheduler_mode not in {"cosine-restarts", "single-cosine"}:
            raise ValueError("scheduler_mode must be cosine-restarts or single-cosine")
        if self.grad_clip_norm <= 0:
            raise ValueError("grad_clip_norm must be positive")
        if self.direction_weight < 0:
            raise ValueError("direction_weight cannot be negative")
        if self.direction_neutral_bps < 0:
            raise ValueError("direction_neutral_bps cannot be negative")


@dataclass(slots=True)
class ExperimentConfig:
    model: BarGPTConfig
    data: DataConfig
    train: TrainConfig


def to_dict(config: Any) -> dict[str, Any]:
    if hasattr(config, "__dataclass_fields__"):
        return asdict(config)
    return dict(config)
