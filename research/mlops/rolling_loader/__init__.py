"""Stateful production-aligned rolling data loader.

The package is intentionally separate from ``research.mlops.data`` because the
older provider materializes dense low-frequency context per sample. This loader
keeps bounded caches, emits stable ids, and materializes batches only at the
final collator/profiler step.
"""

from research.mlops.rolling_loader.config import RollingLoaderConfig
from research.mlops.rolling_loader.initialize import InitializedRollingReplay, initialize_clickhouse_replay
from research.mlops.rolling_loader.loader import (
    MaterializedRollingBatch,
    RollingContextLoader,
    RollingSamplePointer,
)
from research.mlops.rolling_loader.materialized_cache import (
    MATERIALIZED_CACHE_FORMAT,
    RollingMaterializedShardWriter,
)
from research.mlops.rolling_loader.streaming_training import (
    StreamingBatchEnvelope,
    StreamingClickHouseTrainingSource,
    StreamingProfiler,
    StreamingRollingTrainingProvider,
)
from research.mlops.rolling_loader.ticker_month_dataset import (
    AsyncTickerMonthBatchLoader,
    TickerMonthCacheIndex,
    TickerMonthLoaderConfig,
    TickerMonthLoaderState,
    TickerMonthTrainingBatch,
)

__all__ = [
    "InitializedRollingReplay",
    "MATERIALIZED_CACHE_FORMAT",
    "MaterializedRollingBatch",
    "RollingMaterializedShardWriter",
    "StreamingBatchEnvelope",
    "StreamingClickHouseTrainingSource",
    "StreamingProfiler",
    "StreamingRollingTrainingProvider",
    "AsyncTickerMonthBatchLoader",
    "RollingContextLoader",
    "RollingLoaderConfig",
    "RollingSamplePointer",
    "TickerMonthCacheIndex",
    "TickerMonthLoaderConfig",
    "TickerMonthLoaderState",
    "TickerMonthTrainingBatch",
    "initialize_clickhouse_replay",
]
