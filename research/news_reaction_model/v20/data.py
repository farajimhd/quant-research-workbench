from __future__ import annotations

from typing import Iterator

import numpy as np
import torch

from research.news_reaction_model.v18.data import (
    EpisodeBatch,
    PreparedEpisodeDataset as V18PreparedEpisodeDataset,
)
from research.news_reaction_model.v20.config import LoaderConfig
from research.news_reaction_model.v20.targets import (
    PUBLICATION_SESSION_COUNT,
    price_regime_numpy,
)


class PreparedEpisodeDataset(V18PreparedEpisodeDataset):
    """Read V18 arrays without copying them and derive V20 runtime-only inputs."""

    def __init__(
        self,
        config: LoaderConfig,
        *,
        start: str,
        end_exclusive: str,
        shuffle: bool = False,
        seed: int = 17,
    ) -> None:
        super().__init__(
            config,
            start=start,
            end_exclusive=end_exclusive,
            shuffle=shuffle,
            seed=seed,
        )

    def iter_batches(self, *, epoch: int = 0) -> Iterator[EpisodeBatch]:
        for batch in super().iter_batches(epoch=epoch):
            indices = np.asarray(batch.identity["prepared_row_index"], dtype=np.int64)
            source = np.asarray(self.arrays["source_index"][indices], dtype=np.int64)
            anchors = np.asarray(self.arrays["anchor_price"][indices], dtype=np.float32)
            regimes = price_regime_numpy(anchors)
            sessions = np.asarray(
                self.v15["time_features"][source, :PUBLICATION_SESSION_COUNT],
                dtype=np.float32,
            ).argmax(axis=1).astype(np.int64)
            context_count = (
                batch.x["prior_context_mask"].sum(dim=1, dtype=torch.float32)
                / float(self.config.context_size)
            )
            batch.x.update(
                {
                    "anchor_log": torch.from_numpy(np.log1p(anchors).reshape(-1, 1)),
                    "price_regime": torch.from_numpy(regimes),
                    "publication_session": torch.from_numpy(sessions),
                    "context_fraction": context_count.reshape(-1, 1),
                }
            )
            yield batch


__all__ = ["EpisodeBatch", "PreparedEpisodeDataset"]
