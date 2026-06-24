"""Shared data contracts and providers for ML training and serving.

The package is intentionally model-agnostic. It owns how market/news/SEC/
fundamental data becomes model-ready batches, while model versions own the
architectures and objectives that consume those batches.
"""

from research.mlops.data.batching import EncoderBatcher, MultiModalBatcher
from research.mlops.data.config import DataProviderConfig, ExternalAsOfContextConfig, MarketStreamConfig, RollingMarketDataConfig, TickerBlockDataConfig, TimeBarHorizon
from research.mlops.data.profiling import DataPrepProfile, DataPrepProfiler
from research.mlops.data.providers import StreamingReplayBatchProvider, TemporalBatchProvider
from research.mlops.data.ticker_blocks import ClickHouseTickerBlockBatchProvider, EventTimeBarBatch, TickerEpochScheduler
from research.mlops.data.rolling import RollingEmbeddingCache, RollingMarketSampleEngine, RollingReadyIndexBlock
from research.mlops.data.contracts import (
    ChunkWindowIndex,
    CompactEvent,
    EmbeddingRecord,
    EncoderBatch,
    EventChunk,
    MultiModalTemporalBatch,
    MultiModalTemporalSample,
    RollingProductionBatch,
    RollingSampleIndex,
    RollingTrainingBatch,
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
    "ChunkWindowIndex",
    "StreamingReplayBatchProvider",
    "TemporalBatchProvider",
    "ExternalAsOfContextConfig",
    "RollingMarketDataConfig",
    "RollingEmbeddingCache",
    "RollingMarketSampleEngine",
    "RollingReadyIndexBlock",
    "RollingProductionBatch",
    "RollingSampleIndex",
    "RollingTrainingBatch",
    "TickerBlockDataConfig",
    "TickerEpochScheduler",
    "TimeBarHorizon",
]

