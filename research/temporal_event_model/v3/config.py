from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_CACHE_ROOT = Path("D:/market-data/prepared/daily_index_streaming_cache/events_daily_index_2019-02")
DEFAULT_OUTPUT_ROOT = Path("D:/TradingML/runtimes/temporal_event_model/v3/train")
DEFAULT_DATA_GROUPS = (
    "events",
    "intraday_labels",
    "corporate_action_labels",
    "intraday_bars",
    "scanner_context",
    "daily_bars",
    "global_daily_bars",
    "ticker_news_embeddings",
    "market_news_embeddings",
    "sec_filing_embeddings",
    "xbrl",
    "corporate_actions",
)
DEFAULT_EVENT_FEATURE_NAMES = (
    "event_meta",
    "price_primary_int",
    "price_secondary_int",
    "size_primary",
    "size_secondary",
    "exchange_primary",
    "exchange_secondary",
    "condition_token_1",
    "condition_token_2",
    "condition_token_3",
    "condition_token_4",
    "condition_token_5",
    "utc_second_of_day_sin",
    "utc_second_of_day_cos",
    "utc_day_of_week_sin",
    "utc_day_of_week_cos",
    "utc_day_of_year_sin",
    "utc_day_of_year_cos",
    "years_since_2000",
    "session_second",
    "session_progress",
    "is_regular_hours",
    "is_premarket",
    "is_afterhours",
)
EVENT_TIME_FEATURE_NAMES = (
    "utc_second_of_day_sin",
    "utc_second_of_day_cos",
    "utc_day_of_week_sin",
    "utc_day_of_week_cos",
    "utc_day_of_year_sin",
    "utc_day_of_year_cos",
    "years_since_2000",
    "session_second",
    "session_progress",
    "is_regular_hours",
    "is_premarket",
    "is_afterhours",
)
TIME_ROLE_NAMES = (
    "event",
    "bar_start",
    "text_available",
    "xbrl_available",
    "xbrl_period_end",
    "corporate_available",
    "corporate_effective",
    "scanner_bar_end",
)
INTRADAY_EVENT_FLAGS = (
    "condition_halt_pause_flag",
    "condition_resume_flag",
    "condition_news_risk_flag",
    "condition_luld_limit_state_flag",
)
EXTERNAL_ARRIVAL_FLAGS = ("ticker_news_arrival_flag", "sec_filing_arrival_flag")
CORPORATE_ACTION_FLAGS = (
    "future_split_flag",
    "future_reverse_split_flag",
    "future_forward_split_flag",
    "future_dividend_ex_flag",
    "future_special_dividend_ex_flag",
    "future_any_corporate_action_flag",
)
BAR_FAMILIES = ("trade", "quote_bid", "quote_ask")
BAR_FEATURE_DIMS = {"trade": 6, "quote_bid": 9, "quote_ask": 9}
SCANNER_GROUPS = (
    "top_gainers",
    "top_volume_large_cap",
    "top_volume_mid_cap",
    "top_volume_small_cap",
    "top_volume_penny",
)
SCANNER_HORIZONS = ("1s", "5s", "30s", "1m")
SCANNER_NUMERIC_FEATURES = (
    "rank_score",
    "rank_percentile",
    "origin_is_leader",
    "origin_topk_position",
    "origin_rank",
    "origin_rank_percentile",
)
DEFAULT_XBRL_CATEGORY_VOCAB_SIZES = {
    "fiscal_period_id": 256,
    "calendar_period_id": 256,
    "taxonomy_id": 4096,
    "tag_id": 262144,
    "unit_id": 8192,
    "form_id": 2048,
    "row_kind_id": 1024,
    "location_id": 8192,
}
DEFAULT_INTRADAY_LABEL_HORIZONS = (
    "100ms",
    "200ms",
    "300ms",
    "400ms",
    "500ms",
    "1s",
    "2s",
    "3s",
    "5s",
    "10s",
    "15s",
    "30s",
    "60s",
    "120s",
    "180s",
    "300s",
    "600s",
    "900s",
    "1200s",
    "1800s",
    "3600s",
    "7200s",
    "3h",
    "4h",
    "5h",
    "eod",
)


@dataclass(slots=True)
class ModelConfig:
    d_model: int = 256
    event_stream_length: int = 1024
    event_feature_count: int = len(DEFAULT_EVENT_FEATURE_NAMES)
    event_layers: int = 4
    event_heads: int = 8
    fusion_layers: int = 3
    fusion_heads: int = 8
    side_encoder_dim: int = 0
    dropout: float = 0.05
    time_encoder_dim: int = 32
    time_feature_input_dim: int = len(EVENT_TIME_FEATURE_NAMES)
    event_time_feature_count: int = len(EVENT_TIME_FEATURE_NAMES)
    text_embedding_dim: int = 1024
    ticker_news_items: int = 8
    market_news_items: int = 16
    sec_filing_items: int = 4
    ticker_news_chunks: int = 2
    market_news_chunks: int = 2
    sec_filing_chunks: int = 8
    text_time_feature_count: int = 10
    text_item_dim: int = 128
    text_latents: int = 4
    text_attention_heads: int = 4
    xbrl_max_items: int = 4096
    corporate_action_max_items: int = 128
    ticker_bar_offsets: int = 8
    global_symbols: int = 16
    global_bar_offsets: int = 3
    bar_feature_count: int = 10
    bar_time_feature_count: int = 9
    bar_item_dim: int = 128
    bar_latents: int = 4
    bar_attention_heads: int = 4
    xbrl_time_feature_count: int = 10
    xbrl_period_time_feature_count: int = 7
    xbrl_item_dim: int = 64
    xbrl_latents: int = 8
    xbrl_attention_heads: int = 4
    xbrl_category_embedding_dim: int = 8
    xbrl_category_vocab_sizes: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_XBRL_CATEGORY_VOCAB_SIZES))
    corporate_action_numeric_dim: int = 13
    corporate_action_time_dim: int = 10
    corporate_action_effective_time_dim: int = 10
    scanner_groups: int = len(SCANNER_GROUPS)
    scanner_top_k: int = 5
    scanner_horizons: int = len(SCANNER_HORIZONS)
    scanner_numeric_dim: int = len(SCANNER_NUMERIC_FEATURES)
    intraday_horizons: int = len(DEFAULT_INTRADAY_LABEL_HORIZONS)
    corporate_action_days: tuple[int, ...] = (1, 2, 3, 7, 28)
    event_feature_names: tuple[str, ...] = DEFAULT_EVENT_FEATURE_NAMES
    use_text: bool = True
    use_xbrl: bool = True
    use_bars: bool = True
    use_corporate_actions: bool = True
    use_scanner: bool = True


@dataclass(slots=True)
class LoaderConfig:
    cache_root: Path = DEFAULT_CACHE_ROOT
    split: str = "train"
    val_split: str = "validation"
    start_utc: str = ""
    end_utc: str = ""
    val_start_utc: str = ""
    val_end_utc: str = ""
    months: tuple[str, ...] = ()
    tickers: tuple[str, ...] = ()
    batch_size: int = 256
    seed: int = 17
    dataset_id: str = "temporal_v3_1m_2019_v1"
    data_groups: tuple[str, ...] = DEFAULT_DATA_GROUPS
    event_columns: tuple[str, ...] = DEFAULT_EVENT_FEATURE_NAMES
    intraday_label_horizons: tuple[str, ...] = DEFAULT_INTRADAY_LABEL_HORIZONS
    event_stream_length: int = 1024
    loaded_parts_per_group: int = 4
    read_workers: int = 4
    materialize_workers: int = 4
    materialize_chunk_size: int = 0
    prefetch_batches: int = 10
    chronological_replay: bool = True
    time_window_seconds: float = 1.0
    ticker_cache_capacity: int = 15_000
    origin_cursor_chunk_rows: int = 4096
    warm_all_ticker_caches: bool = True
    scanner_index_cache_entries: int = 4
    prefetch_scanner_indexes: bool = True
    scanner_prefetch_workers: int = 4
    max_origins_per_epoch: int = 1_000_000
    sample_fraction: float = 1.0
    sample_hash_modulus: int = 0
    sample_hash_buckets: tuple[int, ...] = ()
    val_sample_hash_buckets: tuple[int, ...] = ()
    training_days: tuple[str, ...] = ()
    validation_days: tuple[str, ...] = ()
    validation_reserve_policy: str = "last_n_days"
    validation_reserve_days: int = 1
    validation_origins_per_day: int = 256
    validation_random_ticker_count: int = 64
    validation_liquid_tickers: tuple[str, ...] = ("SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA")
    refresh_validation_plan: bool = False
    randomize_seed: bool = False
    shuffle_parts: bool = True
    shuffle_within_loaded_group: bool = True


@dataclass(slots=True)
class TrainConfig:
    run_name: str = ""
    output_root: Path = DEFAULT_OUTPUT_ROOT
    max_samples: int = 0
    max_steps: int = 0
    epochs: int = 1
    learning_rate: float = 3e-4
    weight_decay: float = 0.01
    grad_clip_norm: float = 1.0
    amp: bool = True
    amp_dtype: str = "bf16"
    compile_model: bool = False
    seed: int = 17
    logging_samples: int = 0
    fast_summary_samples: int = 25_000
    train_metric_window_samples: int = 250_000
    validation_samples: int = 2_000_000
    validation_batches: int = 8
    checkpoint_latest_samples: int = 250_000
    checkpoint_archive_samples: int = 2_000_000
    detail_profile_samples: int = 0
    checkpoint_best_train: bool = True
    checkpoint_best_val: bool = True
    progress_layout: str = "auto"
    loader_telemetry_log_seconds: float = 1.0
    cache_state_log_seconds: float = 5.0
    wandb_project: str = "temporal-event-model-v3"
    wandb_entity: str = "mehdifaraji"
    wandb_mode: str = "online"
    wandb_init_timeout: int = 60
    resume_checkpoint: str = ""
    warm_start_checkpoint: str = ""
    fresh_start: bool = False


@dataclass(slots=True)
class ExperimentConfig:
    model: ModelConfig = field(default_factory=ModelConfig)
    loader: LoaderConfig = field(default_factory=LoaderConfig)
    train: TrainConfig = field(default_factory=TrainConfig)


def to_dict(config: Any) -> dict[str, Any]:
    return asdict(config)


def default_run_name(config: ExperimentConfig) -> str:
    if config.train.run_name:
        return config.train.run_name
    period = ""
    if config.loader.start_utc or config.loader.end_utc:
        period = f"-{config.loader.start_utc[:10]}-{config.loader.end_utc[:10]}"
    return (
        f"v3-temporal-{config.loader.dataset_id}{period}"
        f"-d{config.model.d_model}-bs{config.loader.batch_size}-lr{config.train.learning_rate:g}"
    )
