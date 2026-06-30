from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence

import numpy as np

QUOTE_EVENT_TYPE = 0
TRADE_EVENT_TYPE = 1


@dataclass(frozen=True, slots=True)
class CompactEvent:
    """One compact unified market event from qmd-gateway or historical replay."""

    ticker: str
    sip_timestamp_us: int
    event_meta: int
    price_primary_int: int
    price_secondary_int: int
    size_primary: float
    size_secondary: float
    exchange_primary: int
    exchange_secondary: int
    condition_token_1: int
    condition_token_2: int
    condition_token_3: int
    condition_token_4: int
    condition_token_5: int
    source_sequence: int = 0
    arrival_sequence: int = 0
    ordinal: int | None = None
    issue_flags: int = 0

    @property
    def event_type(self) -> int:
        return int(self.event_meta) & 0x01

    @property
    def sort_key(self) -> tuple[int, int, int, int]:
        return (
            int(self.sip_timestamp_us),
            int(self.source_sequence),
            int(self.event_type),
            int(self.arrival_sequence),
        )


@dataclass(frozen=True, slots=True)
class EventChunk:
    """A model-ready event chunk for one ticker origin."""

    ticker: str
    origin_timestamp_us: int
    origin_ordinal: int | None
    header_uint8: np.ndarray
    events_uint8: np.ndarray
    source_events: tuple[CompactEvent, ...]


@dataclass(frozen=True, slots=True)
class EncoderBatch:
    """Batched chunks passed to the event encoder."""

    headers_uint8: np.ndarray
    events_uint8: np.ndarray
    chunks: tuple[EventChunk, ...]


@dataclass(frozen=True, slots=True)
class EmbeddingRecord:
    """One encoder output associated with a ticker chunk origin."""

    ticker: str
    origin_timestamp_us: int
    origin_ordinal: int | None
    embedding: np.ndarray
    chunk: EventChunk


@dataclass(frozen=True, slots=True)
class TemporalSample:
    """One temporal-model input built from a ticker's embedding history."""

    ticker: str
    origin_timestamp_us: int
    origin_ordinal: int | None
    context_embeddings: np.ndarray
    context_lags: tuple[int, ...]
    records: tuple[EmbeddingRecord, ...]


@dataclass(frozen=True, slots=True)
class TemporalBatch:
    """Batched temporal-model contexts."""

    contexts: np.ndarray
    samples: tuple[TemporalSample, ...]


@dataclass(frozen=True, slots=True)
class PredictionRecord:
    """One model prediction with enough metadata to route back to a ticker."""

    ticker: str
    origin_timestamp_us: int
    origin_ordinal: int | None
    prediction: Any


@dataclass(frozen=True, slots=True)
class TrainingSample:
    """A replay sample with a temporal context and future event chunks."""

    temporal_sample: TemporalSample
    future_chunks: tuple[EventChunk, ...]


class WindowEncoder(Protocol):
    def encode_window(self, events: Sequence[CompactEvent], *, previous_sip_us: int | None = None) -> tuple[np.ndarray, np.ndarray]:
        """Encode a fixed-size event window into header/event byte tensors."""


class EncoderModel(Protocol):
    def encode(self, headers_uint8: np.ndarray, events_uint8: np.ndarray) -> np.ndarray:
        """Return encoder embeddings for a batch of chunks."""


class TemporalModel(Protocol):
    def predict(self, contexts: np.ndarray) -> Any:
        """Return temporal predictions for a batch of contexts."""
