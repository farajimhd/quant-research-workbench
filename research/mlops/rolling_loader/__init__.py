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
from research.mlops.rolling_loader.offline_training_batch_cache import (
    OFFLINE_BATCH_CACHE_FORMAT,
    OFFLINE_BATCH_CACHE_VERSION,
    OfflineBatchCacheWriter,
    OfflineBatchCacheWriterConfig,
    OfflineShardStats,
    load_shard_tensor_rows,
)
from research.mlops.rolling_loader.rust_chrono_loader import (
    RustNativeCacheProfileConfig,
    RustNativeCacheProfileStats,
    RustRealCachePart,
    RustRealCacheRuntimeConfig,
    RustRealCacheRuntimeStats,
    RustQueueRuntimeConfig,
    RustQueueRuntimeStats,
    RustTensorAssemblyConfig,
    RustTensorAssemblyResult,
    RustTensorAssemblyStats,
    assemble_tensors_with_rust,
    build_rust_library,
    profile_rust_native_cache,
    profile_rust_real_cache_parts,
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
    "OFFLINE_BATCH_CACHE_FORMAT",
    "OFFLINE_BATCH_CACHE_VERSION",
    "OfflineBatchCacheWriter",
    "OfflineBatchCacheWriterConfig",
    "OfflineShardStats",
    "RustNativeCacheProfileConfig",
    "RustNativeCacheProfileStats",
    "RustRealCachePart",
    "RustRealCacheRuntimeConfig",
    "RustRealCacheRuntimeStats",
    "RustQueueRuntimeConfig",
    "RustQueueRuntimeStats",
    "RustTensorAssemblyConfig",
    "RustTensorAssemblyResult",
    "RustTensorAssemblyStats",
    "assemble_tensors_with_rust",
    "build_rust_library",
    "load_shard_tensor_rows",
    "profile_rust_native_cache",
    "profile_rust_real_cache_parts",
    "profile_rust_queue_runtime",
    "rust_library_path",
    "rust_version",
]
