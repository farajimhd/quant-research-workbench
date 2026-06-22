from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Iterable

import numpy as np

from market_ai.config import MarketAIConfig
from market_ai.types import EncoderBatch, EventChunk, TemporalBatch, TemporalSample


@dataclass(slots=True)
class EncoderBatcher:
    config: MarketAIConfig
    _pending: deque[EventChunk] = field(default_factory=deque)

    def add(self, chunk: EventChunk) -> EncoderBatch | None:
        self._pending.append(chunk)
        if len(self._pending) >= self.config.encoder_batch_size:
            return self.flush()
        return None

    def extend(self, chunks: Iterable[EventChunk]) -> list[EncoderBatch]:
        ready: list[EncoderBatch] = []
        for chunk in chunks:
            batch = self.add(chunk)
            if batch is not None:
                ready.append(batch)
        return ready

    def flush(self) -> EncoderBatch | None:
        if not self._pending:
            return None
        chunks = tuple(self._pending)
        self._pending.clear()
        headers = np.stack([chunk.header_uint8 for chunk in chunks]).astype(np.uint8, copy=False)
        events = np.stack([chunk.events_uint8 for chunk in chunks]).astype(np.uint8, copy=False)
        return EncoderBatch(headers_uint8=headers, events_uint8=events, chunks=chunks)


@dataclass(slots=True)
class TemporalBatcher:
    config: MarketAIConfig
    _pending: deque[TemporalSample] = field(default_factory=deque)

    def add(self, sample: TemporalSample) -> TemporalBatch | None:
        self._pending.append(sample)
        if len(self._pending) >= self.config.temporal_batch_size:
            return self.flush()
        return None

    def extend(self, samples: Iterable[TemporalSample]) -> list[TemporalBatch]:
        ready: list[TemporalBatch] = []
        for sample in samples:
            batch = self.add(sample)
            if batch is not None:
                ready.append(batch)
        return ready

    def flush(self) -> TemporalBatch | None:
        if not self._pending:
            return None
        samples = tuple(self._pending)
        self._pending.clear()
        contexts = np.stack([sample.context_embeddings for sample in samples]).astype(np.float32, copy=False)
        return TemporalBatch(contexts=contexts, samples=samples)
