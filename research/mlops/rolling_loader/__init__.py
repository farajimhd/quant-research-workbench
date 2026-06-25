"""Stateful production-aligned rolling data loader.

The package is intentionally separate from ``research.mlops.data`` because the
older provider materializes dense low-frequency context per sample. This loader
keeps bounded caches, emits stable ids, and materializes batches only at the
final collator/profiler step.
"""

from research.mlops.rolling_loader.config import RollingLoaderConfig
from research.mlops.rolling_loader.loader import (
    MaterializedRollingBatch,
    RollingContextLoader,
    RollingSamplePointer,
)

__all__ = [
    "MaterializedRollingBatch",
    "RollingContextLoader",
    "RollingLoaderConfig",
    "RollingSamplePointer",
]
