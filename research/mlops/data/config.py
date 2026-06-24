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
