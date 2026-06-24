"""Shared data contracts and providers for ML training and serving.

The package is intentionally model-agnostic. It owns how market/news/SEC/
fundamental data becomes model-ready batches, while model versions own the
architectures and objectives that consume those batches.
"""

from research.mlops.data.batching import EncoderBatcher, MultiModalBatcher
from research.mlops.data.config import DataProviderConfig, MarketStreamConfig, TickerBlockDataConfig, TimeBarHorizon
from research.mlops.data.profiling import DataPrepProfile, DataPrepProfiler
from research.mlops.data.providers import StreamingReplayBatchProvider, TemporalBatchProvider
from research.mlops.data.ticker_blocks import ClickHouseTickerBlockBatchProvider, EventTimeBarBatch, TickerEpochScheduler
from research.mlops.data.contracts import (
    CompactEvent,
    EmbeddingRecord,
    EncoderBatch,
    EventChunk,
    MultiModalTemporalBatch,
    MultiModalTemporalSample,
)

__all__ = [
    "CompactEvent",
    "DataPrepProfile",
    "DataPrepProfiler",
    "DataProviderConfig",
    "EmbeddingRecord",
    "EncoderBatch",
    "EncoderBatcher",
    "EventChunk",
    "EventTimeBarBatch",
    "MarketStreamConfig",
    "MultiModalBatcher",
    "MultiModalTemporalBatch",
    "MultiModalTemporalSample",
    "ClickHouseTickerBlockBatchProvider",
    "StreamingReplayBatchProvider",
    "TemporalBatchProvider",
    "TickerBlockDataConfig",
    "TickerEpochScheduler",
    "TimeBarHorizon",
]

