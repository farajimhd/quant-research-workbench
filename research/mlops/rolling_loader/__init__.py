"""Daily-index rolling cache builder and loader.

The rolling-loader package now exposes only the daily-index streaming cache path.
Older materialized, replay, indexed-daily, and ticker-month experiments were
removed so training code cannot accidentally depend on stale cache contracts.
"""

from research.mlops.rolling_loader.daily_index_cache import (
    DAILY_INDEX_CACHE_FORMAT,
    DAILY_INDEX_CACHE_VERSION,
    DEFAULT_DAILY_INDEX_CACHE_ROOT,
)
from research.mlops.rolling_loader.daily_index_dataset import (
    AsyncDailyIndexBatchLoader,
    DailyIndexCacheIndex,
    DailyIndexLoaderConfig,
    DailyIndexLoaderState,
    DailyIndexTrainingBatch,
)
from research.mlops.rolling_loader.rust_chrono_loader import (
    RustQueueRuntimeConfig,
    RustQueueRuntimeStats,
    build_rust_library,
    profile_rust_queue_runtime,
    rust_library_path,
    rust_version,
)

__all__ = [
    "AsyncDailyIndexBatchLoader",
    "DAILY_INDEX_CACHE_FORMAT",
    "DAILY_INDEX_CACHE_VERSION",
    "DEFAULT_DAILY_INDEX_CACHE_ROOT",
    "DailyIndexCacheIndex",
    "DailyIndexLoaderConfig",
    "DailyIndexLoaderState",
    "DailyIndexTrainingBatch",
    "RustQueueRuntimeConfig",
    "RustQueueRuntimeStats",
    "build_rust_library",
    "profile_rust_queue_runtime",
    "rust_library_path",
    "rust_version",
]
