from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field

import numpy as np

from research.mlops.data.config import MarketStreamConfig
from research.mlops.data.contracts import EventChunk, MultiModalTemporalSample, TemporalLabel


@dataclass(slots=True)
class PendingFutureChunkLabeler:
    """Hold samples until their future market chunks are available."""

    config: MarketStreamConfig
    pending: dict[str, deque[MultiModalTemporalSample]] = field(default_factory=lambda: defaultdict(deque))
    chunks_by_ticker: dict[str, deque[EventChunk]] = field(default_factory=lambda: defaultdict(deque))

    def register_sample(self, sample: MultiModalTemporalSample) -> None:
        self.pending[sample.ticker.upper()].append(sample)

    def register_chunk(self, chunk: EventChunk) -> list[MultiModalTemporalSample]:
        ticker = chunk.ticker.upper()
        self.chunks_by_ticker[ticker].append(chunk)
        max_keep = max(1, int(self.config.future_chunks) + max(self.config.context_lags, default=0) + 16)
        while len(self.chunks_by_ticker[ticker]) > max_keep:
            self.chunks_by_ticker[ticker].popleft()
        ready: list[MultiModalTemporalSample] = []
        queue = self.pending[ticker]
        while queue:
            sample = queue[0]
            future = future_chunks_for_sample(tuple(self.chunks_by_ticker[ticker]), sample, self.config.future_chunks)
            if len(future) < int(self.config.future_chunks):
                break
            queue.popleft()
            labels = tuple(sample.labels) + (future_chunk_label(future),)
            ready.append(
                MultiModalTemporalSample(
                    ticker=sample.ticker,
                    origin_timestamp_us=sample.origin_timestamp_us,
                    origin_ordinal=sample.origin_ordinal,
                    market=sample.market,
                    news=sample.news,
                    sec=sample.sec,
                    fundamental=sample.fundamental,
                    global_context=sample.global_context,
                    labels=labels,
                    metadata=sample.metadata,
                )
            )
        return ready


def future_chunks_for_sample(chunks: tuple[EventChunk, ...], sample: MultiModalTemporalSample, future_chunks: int) -> tuple[EventChunk, ...]:
    if sample.origin_ordinal is not None:
        future = [chunk for chunk in chunks if chunk.origin_ordinal is not None and int(chunk.origin_ordinal) > int(sample.origin_ordinal)]
    else:
        future = [chunk for chunk in chunks if int(chunk.origin_timestamp_us) > int(sample.origin_timestamp_us)]
    return tuple(future[: max(0, int(future_chunks))])


def future_chunk_label(chunks: tuple[EventChunk, ...]) -> TemporalLabel:
    headers = np.stack([chunk.header_uint8 for chunk in chunks]).astype(np.uint8, copy=False)
    events = np.stack([chunk.events_uint8 for chunk in chunks]).astype(np.uint8, copy=False)
    values = np.concatenate([headers.reshape(len(chunks), -1), events.reshape(len(chunks), -1)], axis=1)
    mask = np.ones(values.shape, dtype=np.bool_)
    return TemporalLabel(name="future_market_chunks_uint8", values=values, mask=mask, metadata={"future_chunks": len(chunks)})

