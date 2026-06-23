from __future__ import annotations

from typing import Iterator

from research.mlops.data.providers import StreamingReplayBatchProvider
from research.mlops.data.contracts import MultiModalTemporalBatch


def iter_replay_batches(provider: StreamingReplayBatchProvider, *, max_batches: int = 0) -> Iterator[MultiModalTemporalBatch]:
    count = 0
    for batch in provider.iter_batches():
        yield batch
        count += 1
        if max_batches and count >= int(max_batches):
            break

