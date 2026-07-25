from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator

import numpy as np
import torch

from research.news_reaction_model.v16.data import (
    _date_range_indices,
    batch_from_indices,
)
from research.news_reaction_model.v17.config import LoaderConfig
from research.news_reaction_model.v17.prepared import close_arrays, open_v17_arrays


@dataclass(slots=True)
class NewsResponseBatch:
    x: dict[str, torch.Tensor]
    direction: torch.Tensor
    path: torch.Tensor
    flow: torch.Tensor
    window_mask: torch.Tensor
    persistence: torch.Tensor
    persistence_mask: torch.Tensor
    raw_metrics: torch.Tensor
    identity: dict[str, Any]
    sample_count: int

    def to(self, device: torch.device, *, non_blocking: bool = True) -> "NewsResponseBatch":
        return NewsResponseBatch(
            x={key: value.to(device, non_blocking=non_blocking) for key, value in self.x.items()},
            direction=self.direction.to(device, non_blocking=non_blocking),
            path=self.path.to(device, non_blocking=non_blocking),
            flow=self.flow.to(device, non_blocking=non_blocking),
            window_mask=self.window_mask.to(device, non_blocking=non_blocking),
            persistence=self.persistence.to(device, non_blocking=non_blocking),
            persistence_mask=self.persistence_mask.to(device, non_blocking=non_blocking),
            # Raw outcome evidence is retained for audit/evaluation only. It is
            # not a model input and must not consume accelerator bandwidth.
            raw_metrics=self.raw_metrics,
            identity=self.identity,
            sample_count=self.sample_count,
        )


def batch_from_indices_v17(
    v16_arrays: dict[str, np.ndarray],
    targets: dict[str, np.ndarray],
    indices: np.ndarray,
    config: LoaderConfig,
) -> NewsResponseBatch:
    source = batch_from_indices(v16_arrays, indices, config)
    copy = lambda name, dtype: np.array(targets[name][indices], dtype=dtype, copy=True)
    return NewsResponseBatch(
        x=source.x,
        direction=torch.from_numpy(copy("direction", np.int64)),
        path=torch.from_numpy(copy("path", np.int64)),
        flow=torch.from_numpy(copy("flow", np.int64)),
        window_mask=torch.from_numpy(copy("window_mask", np.bool_)),
        persistence=torch.from_numpy(copy("persistence", np.int64)),
        persistence_mask=torch.from_numpy(copy("persistence_mask", np.bool_)),
        raw_metrics=torch.from_numpy(copy("raw_metrics", np.float32)),
        identity=source.identity,
        sample_count=source.sample_count,
    )


class PreparedNewsResponseDataset:
    def __init__(
        self,
        config: LoaderConfig,
        *,
        start: str,
        end_exclusive: str,
        shuffle: bool = False,
        seed: int = 17,
    ) -> None:
        self.config = config
        self.v16_arrays, self.targets, self.v16_manifest, self.target_manifest = (
            open_v17_arrays(config)
        )
        self.lower, self.upper = _date_range_indices(
            self.v16_arrays["published_at_us"], start, end_exclusive
        )
        self.shuffle = shuffle
        self.seed = seed
        self._stopped = False

    def iter_batches(self, *, epoch: int = 0) -> Iterator[NewsResponseBatch]:
        indices = np.arange(self.lower, self.upper, dtype=np.int64)
        if self.shuffle:
            np.random.default_rng(self.seed + epoch).shuffle(indices)
        for offset in range(0, len(indices), self.config.batch_size):
            if self._stopped:
                return
            yield batch_from_indices_v17(
                self.v16_arrays,
                self.targets,
                indices[offset : offset + self.config.batch_size],
                self.config,
            )

    def stop(self) -> None:
        self._stopped = True
        close_arrays(self.v16_arrays, self.targets)
