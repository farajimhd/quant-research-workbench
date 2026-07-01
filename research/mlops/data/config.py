from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


def build_context_lags(
    *,
    recent_count: int,
    recent_stride: int,
    older_count: int,
    older_min_lag: int,
    older_max_lag: int,
) -> tuple[int, ...]:
    """Dense recent lags plus sparse older lags over already-created embeddings."""

    recent_stride = max(1, int(recent_stride))
    lags = {lag for lag in range(0, max(0, int(recent_count)) * recent_stride, recent_stride)}
    older_count = max(0, int(older_count))
    if older_count:
        older_min_lag = max(1, int(older_min_lag))
        older_max_lag = max(older_min_lag, int(older_max_lag))
        if older_count == 1:
            lags.add(older_max_lag)
        else:
            for index in range(older_count):
                fraction = index / float(older_count - 1)
                lag = round(older_min_lag * ((older_max_lag / older_min_lag) ** fraction))
                lags.add(int(lag))
    return tuple(sorted(lags))


@dataclass(frozen=True, slots=True)
class MarketStreamConfig:
    events_per_chunk: int = 128
    header_bytes: int = 14
    event_bytes: int = 16
    chunk_stride_events: int = 1
    encoder_batch_size: int = 8192
    temporal_batch_size: int = 4096
    embedding_dim: int = 32
    embedding_history: int = 4096
    recent_context_embeddings: int = 16
    recent_context_stride: int = 1
    older_context_embeddings: int = 48
    older_context_min_lag: int = 32
    older_context_max_lag: int = 2048
    strict_lossless_windows: bool = True
    emit_invalid_windows: bool = False
    future_chunks: int = 2
    prediction_horizons_events: tuple[int, ...] = field(default_factory=lambda: (128, 256))

    @property
    def context_lags(self) -> tuple[int, ...]:
        return build_context_lags(
            recent_count=self.recent_context_embeddings,
            recent_stride=self.recent_context_stride,
            older_count=self.older_context_embeddings,
            older_min_lag=self.older_context_min_lag,
            older_max_lag=self.older_context_max_lag,
        )


@dataclass(frozen=True, slots=True)
class DataProviderConfig:
    provider_name: str = "streaming_replay"
    market: MarketStreamConfig = field(default_factory=MarketStreamConfig)
    profile_enabled: bool = True
    detailed_profile: bool = False
    max_batches: int = 0
    seed: int = 17


@dataclass(frozen=True, slots=True)
class TimeBarHorizon:
    """Future or past bar horizon expressed in microseconds."""

    name: str
    microseconds: int


DEFAULT_SHORT_TIME_BAR_HORIZONS: tuple[TimeBarHorizon, ...] = (
    TimeBarHorizon("100ms", 100_000),
    TimeBarHorizon("250ms", 250_000),
    TimeBarHorizon("500ms", 500_000),
    TimeBarHorizon("750ms", 750_000),
    TimeBarHorizon("1s", 1_000_000),
    TimeBarHorizon("5s", 5_000_000),
    TimeBarHorizon("10s", 10_000_000),
    TimeBarHorizon("30s", 30_000_000),
    TimeBarHorizon("60s", 60_000_000),
)


DEFAULT_INTRADAY_LABEL_HORIZONS: tuple[TimeBarHorizon, ...] = (
    TimeBarHorizon("100ms", 100_000),
    TimeBarHorizon("250ms", 250_000),
    TimeBarHorizon("500ms", 500_000),
    TimeBarHorizon("750ms", 750_000),
    TimeBarHorizon("1s", 1_000_000),
    TimeBarHorizon("5s", 5_000_000),
    TimeBarHorizon("10s", 10_000_000),
    TimeBarHorizon("30s", 30_000_000),
    TimeBarHorizon("60s", 60_000_000),
    TimeBarHorizon("120s", 120_000_000),
    TimeBarHorizon("180s", 180_000_000),
    TimeBarHorizon("300s", 300_000_000),
    TimeBarHorizon("600s", 600_000_000),
    TimeBarHorizon("1200s", 1_200_000_000),
    TimeBarHorizon("1800s", 1_800_000_000),
    TimeBarHorizon("3600s", 3_600_000_000),
    TimeBarHorizon("7200s", 7_200_000_000),
    TimeBarHorizon("3h", 10_800_000_000),
    TimeBarHorizon("4h", 14_400_000_000),
    TimeBarHorizon("5h", 18_000_000_000),
)


@dataclass(frozen=True, slots=True)
class TickerBlockDataConfig:
    """Configuration for chronological multi-ticker block training data.

    The provider reads contiguous ordinal ranges per ticker, creates fixed
    128-event encoder chunks, and derives future time-bar labels from the same
    in-memory block. Ticker scheduling is without replacement within each
    ticker epoch.
    """

    database: str = "market_sip_compact"
    events_table: str = "events"
    index_table: str = "train_2019_to_2025"
    events_per_chunk: int = 128
    ticker_group_size: int = 128
    events_per_ticker_block: int = 250_000
    future_tail_events: int = 4096
    sample_stride_events: int = 1
    max_samples_per_ticker: int = 0
    assemble_polars_table: bool = False
    max_threads: int = 8
    max_memory_usage: str = "80G"
    seed: int = 17
    state_path: Path | None = None
    horizons: tuple[TimeBarHorizon, ...] = field(default_factory=lambda: DEFAULT_SHORT_TIME_BAR_HORIZONS)


@dataclass(frozen=True, slots=True)
class ExternalAsOfContextConfig:
    """Generic as-of context source for news, SEC filings, XBRL, or globals.

    These tables are not all finalized yet, so the loader keeps the schema
    configurable. A source is valid for a training sample only when
    `timestamp_column <= sample_origin_timestamp`; this keeps future text or
    fundamental rows out of model features.
    """

    name: str
    table: str = ""
    ticker_column: str = "ticker"
    timestamp_column: str = "timestamp_us"
    timestamp_unit: str = "us"
    id_column: str = "id"
    payload_columns: tuple[str, ...] = ()
    max_items: int = 32
    max_age_microseconds: int = 0


@dataclass(frozen=True, slots=True)
class RollingMarketDataConfig:
    """Production-aligned rolling sample provider configuration.

    The same chunk-index logic is used for live serving and historical
    training. Training materializes raw compact event chunks so the encoder can
    be fine-tuned. Production usually materializes cached embeddings for those
    same chunk origins.
    """

    database: str = "market_sip_compact"
    q_live_database: str = "q_live"
    sec_context_database: str = "market_sip_compact"
    events_table: str = "events"
    condition_token_reference_table: str = "event_condition_token_reference"
    macro_bars_table: str = "macro_bars_by_time_symbol"
    sec_filing_text_context_table: str = "sec_filing_text_context"
    news_token_table: str = "news_text_tokens"
    sec_filing_text_token_table: str = "sec_filing_text_tokens"
    news_embedding_table: str = "news_text_embeddings"
    sec_filing_text_embedding_table: str = "sec_filing_text_embeddings"
    sec_xbrl_context_table: str = "sec_xbrl_context"
    category_reference_table: str = "training_category_reference"
    stock_split_table: str = "market_stock_split_v1"
    cash_dividend_table: str = "market_cash_dividend_v1"
    index_table: str = "train_2019_to_2025"
    events_per_chunk: int = 128
    header_bytes: int = 14
    event_bytes: int = 16
    short_context_chunks: int = 16
    short_context_stride_chunks: int = 1
    long_context_lags: tuple[int, ...] = (32, 48, 72, 108, 162, 243, 365, 548, 822, 1233, 1850)
    sample_stride_events: int = 1
    batch_size: int = 4096
    max_ready_samples: int = 0
    max_threads: int = 8
    max_memory_usage: str = "80G"
    global_symbols: tuple[str, ...] = ("SPY", "QQQ", "IWM", "DIA")
    macro_timeframes: tuple[str, ...] = ("1d",)
    label_timeframes: tuple[str, ...] = ("1d",)
    macro_lookback_days: int = 400
    label_lookahead_days: int = 400
    q_live_contexts: tuple[str, ...] = ("ticker_news", "market_news", "sec_filings", "xbrl")
    news_lookback_days: int = 30
    news_max_items: int = 32
    news_token_chunks: int = 2
    news_max_channels: int = 8
    news_max_provider_tags: int = 16
    news_max_quality_flags: int = 8
    market_news_max_items: int = 64
    market_news_token_chunks: int = 2
    sec_lookback_days: int = 365
    sec_max_items: int = 16
    sec_token_chunks: int = 8
    sec_max_quality_flags: int = 8
    xbrl_lookback_days: int = 730
    xbrl_max_items: int = 4096
    corporate_action_lookback_days: int = 3650
    corporate_action_max_items: int = 128
    corporate_action_label_days: tuple[int, ...] = (1, 2, 3, 5, 10, 20, 40)
    intraday_label_horizons: tuple[TimeBarHorizon, ...] = field(default_factory=lambda: DEFAULT_INTRADAY_LABEL_HORIZONS)
    text_tokenizer_model: str = "Qwen/Qwen3-0.6B"
    text_max_tokens: int = 1024
    text_tokenizer_local_files_only: bool = True
    strict_text_tokenizer: bool = False
    seed: int = 17
    external_contexts: tuple[ExternalAsOfContextConfig, ...] = ()

    @property
    def context_lags(self) -> tuple[int, ...]:
        dense = range(
            0,
            max(0, int(self.short_context_chunks)) * max(1, int(self.short_context_stride_chunks)),
            max(1, int(self.short_context_stride_chunks)),
        )
        return tuple(sorted(set(int(value) for value in dense).union(int(value) for value in self.long_context_lags)))

    @property
    def max_context_lag(self) -> int:
        return max(self.context_lags, default=0)

    @property
    def carryover_events(self) -> int:
        return int(self.max_context_lag) + int(self.events_per_chunk) - 1
