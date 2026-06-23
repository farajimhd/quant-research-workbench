from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Iterable

import numpy as np

from research.mlops.data.config import MarketStreamConfig
from research.mlops.data.profiling import DataPrepProfile
from research.mlops.data.contracts import EncoderBatch, EventChunk, Modality, MultiModalTemporalBatch, MultiModalTemporalSample


@dataclass(slots=True)
class EncoderBatcher:
    batch_size: int
    _pending: deque[EventChunk] = field(default_factory=deque)

    def add(self, chunk: EventChunk) -> EncoderBatch | None:
        self._pending.append(chunk)
        if len(self._pending) >= int(self.batch_size):
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
        return EncoderBatch(modality=Modality.MARKET, headers_uint8=headers, events_uint8=events, items=chunks)


@dataclass(slots=True)
class MultiModalBatcher:
    batch_size: int
    _pending: deque[MultiModalTemporalSample] = field(default_factory=deque)

    def add(self, sample: MultiModalTemporalSample) -> MultiModalTemporalBatch | None:
        self._pending.append(sample)
        if len(self._pending) >= int(self.batch_size):
            return self.flush()
        return None

    def extend(self, samples: Iterable[MultiModalTemporalSample]) -> list[MultiModalTemporalBatch]:
        ready: list[MultiModalTemporalBatch] = []
        for sample in samples:
            batch = self.add(sample)
            if batch is not None:
                ready.append(batch)
        return ready

    def flush(self, *, profile: DataPrepProfile | None = None) -> MultiModalTemporalBatch | None:
        if not self._pending:
            return None
        samples = tuple(self._pending)
        self._pending.clear()
        return pack_multimodal_batch(samples, profile=profile)


def pack_multimodal_batch(samples: tuple[MultiModalTemporalSample, ...], *, profile: DataPrepProfile | None = None) -> MultiModalTemporalBatch:
    if not samples:
        raise ValueError("Cannot pack an empty multimodal batch.")
    market_embeddings = np.stack([sample.market.embeddings for sample in samples]).astype(np.float32, copy=False)
    market_mask = np.stack([sample.market.mask for sample in samples]).astype(np.bool_, copy=False)
    labels: dict[str, np.ndarray] = {}
    label_masks: dict[str, np.ndarray] = {}
    names = sorted({label.name for sample in samples for label in sample.labels})
    for name in names:
        values = []
        masks = []
        for sample in samples:
            match = next((label for label in sample.labels if label.name == name), None)
            if match is None:
                continue
            values.append(match.values)
            if match.mask is not None:
                masks.append(match.mask)
        if values:
            labels[name] = np.stack(values)
        if masks:
            label_masks[name] = np.stack(masks).astype(np.bool_, copy=False)
    if profile is not None:
        profile.output_batches_created += 1
    return MultiModalTemporalBatch(
        market_embeddings=market_embeddings,
        market_mask=market_mask,
        samples=samples,
        labels=labels,
        label_masks=label_masks,
        profile=profile,
    )


def batcher_from_config(config: MarketStreamConfig) -> tuple[EncoderBatcher, MultiModalBatcher]:
    return EncoderBatcher(config.encoder_batch_size), MultiModalBatcher(config.temporal_batch_size)

