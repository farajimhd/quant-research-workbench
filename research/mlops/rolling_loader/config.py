from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RollingLoaderConfig:
    """Configuration for the stateful rolling loader.

    The defaults mirror the current market-structure encoder and the context
    sizes discussed for the production serving path. Lags are measured in
    chunk-origin steps; with ``chunk_stride_events=1`` a lag of 32 means the
    128-event chunk ending 32 events before the current origin.
    """

    events_per_chunk: int = 128
    header_bytes: int = 14
    event_bytes: int = 16
    chunk_stride_events: int = 1
    sample_stride_events: int = 1
    short_context_chunks: int = 16
    short_context_stride_chunks: int = 1
    long_context_lags: tuple[int, ...] = (32, 48, 72, 108, 162, 243, 365, 548, 822, 1233, 1850)
    batch_size: int = 4096
    max_ready_samples: int = 0
    global_news_cache_size: int = 64
    ticker_news_cache_size: int = 32
    sec_filing_cache_size: int = 16
    xbrl_cache_size: int = 512
    macro_bar_cache_size: int = 512
    global_bar_cache_size: int = 512
    text_max_tokens: int = 1024
    news_token_chunks: int = 2
    sec_token_chunks: int = 8
    xbrl_feature_width: int = 64
    bar_feature_width: int = 9
    seed: int = 17
    repeatable_randomness: bool = False
    profile_report_path: Path = Path("D:/market-data/prepared/data_provider_profiles/rolling_loader_profile.jsonl")

    @property
    def context_lags(self) -> tuple[int, ...]:
        dense = range(
            0,
            max(0, int(self.short_context_chunks)) * max(1, int(self.short_context_stride_chunks)),
            max(1, int(self.short_context_stride_chunks)),
        )
        return tuple(sorted(set(int(value) for value in dense).union(int(value) for value in self.long_context_lags)))

    @property
    def context_chunks(self) -> int:
        return len(self.context_lags)

    @property
    def max_context_lag(self) -> int:
        return max(self.context_lags, default=0)

    @property
    def warmup_events_per_ticker(self) -> int:
        return int(self.max_context_lag) * int(self.chunk_stride_events) + int(self.events_per_chunk)

    @property
    def event_cache_events_per_ticker(self) -> int:
        # Keep enough rows for the largest lag plus a headroom chunk so live
        # append/trim does not invalidate just-created sample pointers.
        return int(self.warmup_events_per_ticker) + int(self.events_per_chunk)

    @property
    def chunk_cache_size_per_ticker(self) -> int:
        return int(self.max_context_lag) + int(self.events_per_chunk) + int(self.batch_size)


@dataclass(frozen=True, slots=True)
class SyntheticRollingLoaderConfig:
    """Synthetic source settings used by the profiler and smoke tests."""

    tickers: int = 64
    rows_per_ticker: int = 8_000
    event_spacing_us: int = 1_000
    external_every_events: int = 512
    batches: int = 4
    materialize_external_payloads: bool = True
    loader: RollingLoaderConfig = field(default_factory=RollingLoaderConfig)
