from __future__ import annotations

from research.news_reaction_model.v18.data import EpisodeBatch
from research.news_reaction_model.v20.data import (
    PreparedEpisodeDataset as V20PreparedEpisodeDataset,
)


class PreparedEpisodeDataset(V20PreparedEpisodeDataset):
    """Read the certified V18/V15 arrays through the unchanged V20 input seam."""


__all__ = ["EpisodeBatch", "PreparedEpisodeDataset"]
