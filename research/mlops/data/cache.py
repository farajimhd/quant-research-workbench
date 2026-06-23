from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from research.mlops.data.contracts import MultiModalTemporalBatch


@dataclass(frozen=True, slots=True)
class BatchCacheWriter:
    root: Path

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def write_batch(self, batch: MultiModalTemporalBatch, index: int) -> Path:
        path = self.root / f"batch_{int(index):08d}.npz"
        labels = {f"label_{key}": value for key, value in batch.labels.items()}
        label_masks = {f"label_mask_{key}": value for key, value in batch.label_masks.items()}
        np.savez_compressed(
            path,
            market_embeddings=batch.market_embeddings,
            market_mask=batch.market_mask,
            **labels,
            **label_masks,
        )
        metadata = {
            "batch_index": int(index),
            "samples": len(batch.samples),
            "tickers": [sample.ticker for sample in batch.samples],
            "origin_timestamp_us": [int(sample.origin_timestamp_us) for sample in batch.samples],
        }
        (self.root / f"batch_{int(index):08d}.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        return path


def read_cached_batch(path: Path) -> dict[str, np.ndarray]:
    with np.load(path) as payload:
        return {key: payload[key] for key in payload.files}

