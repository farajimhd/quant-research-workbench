from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, Sequence

import numpy as np


QUOTE_EVENT_TYPE = 0
TRADE_EVENT_TYPE = 1
BAR_FEATURE_KEYS: tuple[str, ...] = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "dollar_volume",
    "trade_count",
    "quote_count",
    "vwap",
)
FUTURE_BAR_FEATURE_KEYS: tuple[str, ...] = (
    "open",
    "close",
    "high",
    "low",
    "volume",
)


class Modality(str, Enum):
    MARKET = "market"
    NEWS = "news"
    SEC = "sec"
    FUNDAMENTAL = "fundamental"
    GLOBAL = "global"


@dataclass(frozen=True, slots=True)
class CompactEvent:
    """One compact unified quote/trade event from live or historical sources."""

    ticker: str
    sip_timestamp_us: int
    event_type: int
    price_primary_int: int
    price_secondary_int: int
    size_primary: float
    size_secondary: float
    exchange_primary: int
    exchange_secondary: int
    event_flags: int
    conditions_packed: int
    source_sequence: int = 0
    arrival_sequence: int = 0
    ordinal: int | None = None
    issue_flags: int = 0

    @property
    def sort_key(self) -> tuple[int, int, int, int]:
        return (
            int(self.sip_timestamp_us),
            int(self.source_sequence),
            int(self.event_type),
            int(self.arrival_sequence),
        )


@dataclass(frozen=True, slots=True)
class TextItem:
    modality: Modality
    ticker: str
    timestamp_us: int
    text: str
    source_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FundamentalItem:
    ticker: str
    timestamp_us: int
    values: dict[str, float]
    source_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EventChunk:
    ticker: str
    origin_timestamp_us: int
    origin_ordinal: int | None
    header_uint8: np.ndarray
    events_uint8: np.ndarray
    source_events: tuple[CompactEvent, ...] = ()
    issue_flags: int = 0


@dataclass(frozen=True, slots=True)
class EncoderBatch:
    modality: Modality
    headers_uint8: np.ndarray | None
    events_uint8: np.ndarray | None
    items: tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class EmbeddingRecord:
    modality: Modality
    ticker: str
    timestamp_us: int
    embedding: np.ndarray
    source: Any
    ordinal: int | None = None


@dataclass(frozen=True, slots=True)
class ModalityContext:
    embeddings: np.ndarray
    mask: np.ndarray
    records: tuple[EmbeddingRecord, ...]


@dataclass(frozen=True, slots=True)
class TemporalLabel:
    name: str
    values: np.ndarray
    mask: np.ndarray | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MultiModalTemporalSample:
    ticker: str
    origin_timestamp_us: int
    origin_ordinal: int | None
    market: ModalityContext
    news: ModalityContext | None = None
    sec: ModalityContext | None = None
    fundamental: ModalityContext | None = None
    global_context: ModalityContext | None = None
    labels: tuple[TemporalLabel, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MultiModalTemporalBatch:
    market_embeddings: np.ndarray
    market_mask: np.ndarray
    samples: tuple[MultiModalTemporalSample, ...]
    news_embeddings: np.ndarray | None = None
    news_mask: np.ndarray | None = None
    sec_embeddings: np.ndarray | None = None
    sec_mask: np.ndarray | None = None
    fundamental_embeddings: np.ndarray | None = None
    fundamental_mask: np.ndarray | None = None
    global_embeddings: np.ndarray | None = None
    global_mask: np.ndarray | None = None
    labels: dict[str, np.ndarray] = field(default_factory=dict)
    label_masks: dict[str, np.ndarray] = field(default_factory=dict)
    profile: Any | None = None


@dataclass(frozen=True, slots=True)
class ChunkWindowIndex:
    """One 128-event chunk reference inside a temporal sample.

    `lag_chunks` is measured in already-available chunk origins. A lag of zero
    is the current chunk ending at the sample origin. Higher lags point to
    older chunk origins and are used for longer market context.
    """

    ticker: str
    lag_chunks: int
    start_ordinal: int
    end_ordinal: int
    origin_ordinal: int
    origin_timestamp_us: int


@dataclass(frozen=True, slots=True)
class RollingSampleIndex:
    """A production-compatible temporal sample pointer.

    Training uses this to materialize raw compact chunks and run the encoder
    inside the training graph. Production uses the same indices to gather
    already-cached embeddings and avoid repeated encoder inference.
    """

    ticker: str
    origin_ordinal: int
    origin_timestamp_us: int
    chunk_windows: tuple[ChunkWindowIndex, ...]
    macro_asof_timestamp_us: int
    global_asof_timestamp_us: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RollingTrainingBatch:
    """Materialized compact chunks for encoder-in-the-loop training.

    Shapes:
    - `headers_uint8`: `[batch, context_chunks, 14]`
    - `events_uint8`: `[batch, context_chunks, 128, 16]`
    - `text_inputs[*]["embeddings"]`: `[batch, max_items, token_chunks, embedding_dim]`
    - `text_inputs[*]` metadata ids: `[batch, max_items]` or `[batch, max_items, max_values]`
    - `xbrl_inputs[*]`: `[batch, xbrl_max_items]`
    - `ticker_macro_bars`: `[batch, macro_timeframes, 9]`
    - `global_market_bars`: `[batch, global_symbols, macro_timeframes, 9]`
    - `future_macro_bars`: `[batch, label_timeframes, 5]`
    - `future_intraday_bars`: `[batch, intraday_label_horizons, 5]`
    - `time_features[*]`: `[batch]`
    - `chunk_time_features[*]`: `[batch, context_chunks]`
    - `input_availability[*]`: `[batch]` boolean masks for optional inputs
    """

    headers_uint8: np.ndarray
    events_uint8: np.ndarray
    ticker: np.ndarray
    origin_ordinal: np.ndarray
    origin_timestamp_us: np.ndarray
    time_features: dict[str, np.ndarray] = field(default_factory=dict)
    chunk_time_features: dict[str, np.ndarray] = field(default_factory=dict)
    bar_feature_keys: tuple[str, ...] = BAR_FEATURE_KEYS
    future_bar_feature_keys: tuple[str, ...] = FUTURE_BAR_FEATURE_KEYS
    macro_bar_timeframes: tuple[str, ...] = ()
    global_bar_symbols: tuple[str, ...] = ()
    global_bar_timeframes: tuple[str, ...] = ()
    future_macro_bar_timeframes: tuple[str, ...] = ()
    future_intraday_bar_horizons: tuple[str, ...] = ()
    ticker_macro_bars: np.ndarray = field(default_factory=lambda: np.zeros((0, 0, len(BAR_FEATURE_KEYS)), dtype=np.float32))
    ticker_macro_bar_mask: np.ndarray = field(default_factory=lambda: np.zeros((0, 0), dtype=np.bool_))
    global_market_bars: np.ndarray = field(default_factory=lambda: np.zeros((0, 0, 0, len(BAR_FEATURE_KEYS)), dtype=np.float32))
    global_market_bar_mask: np.ndarray = field(default_factory=lambda: np.zeros((0, 0, 0), dtype=np.bool_))
    future_macro_bars: np.ndarray = field(default_factory=lambda: np.zeros((0, 0, len(FUTURE_BAR_FEATURE_KEYS)), dtype=np.float32))
    future_macro_bar_mask: np.ndarray = field(default_factory=lambda: np.zeros((0, 0), dtype=np.bool_))
    future_intraday_bars: np.ndarray = field(default_factory=lambda: np.zeros((0, 0, len(FUTURE_BAR_FEATURE_KEYS)), dtype=np.float32))
    future_intraday_bar_mask: np.ndarray = field(default_factory=lambda: np.zeros((0, 0), dtype=np.bool_))
    macro_features: dict[str, np.ndarray] = field(default_factory=dict)
    global_features: dict[str, np.ndarray] = field(default_factory=dict)
    input_availability: dict[str, np.ndarray] = field(default_factory=dict)
    text_inputs: dict[str, dict[str, np.ndarray]] = field(default_factory=dict)
    xbrl_inputs: dict[str, np.ndarray] = field(default_factory=dict)
    external_context: dict[str, Any] = field(default_factory=dict)
    labels: dict[str, np.ndarray] = field(default_factory=dict)
    profile: Any | None = None


@dataclass(frozen=True, slots=True)
class RollingProductionBatch:
    """Materialized embedding contexts for live inference.

    Shapes:
    - `market_embeddings`: `[batch, context_chunks, embedding_dim]`
    - `market_mask`: `[batch, context_chunks]`
    - `text_inputs[*]["embeddings"]`: `[batch, max_items, token_chunks, embedding_dim]`
    - `xbrl_inputs[*]`: `[batch, xbrl_max_items]`
    - `ticker_macro_bars`: `[batch, macro_timeframes, 9]`
    - `global_market_bars`: `[batch, global_symbols, macro_timeframes, 9]`
    - `time_features[*]`: `[batch]`
    """

    market_embeddings: np.ndarray
    market_mask: np.ndarray
    samples: tuple[RollingSampleIndex, ...]
    time_features: dict[str, np.ndarray] = field(default_factory=dict)
    bar_feature_keys: tuple[str, ...] = BAR_FEATURE_KEYS
    macro_bar_timeframes: tuple[str, ...] = ()
    global_bar_symbols: tuple[str, ...] = ()
    global_bar_timeframes: tuple[str, ...] = ()
    ticker_macro_bars: np.ndarray = field(default_factory=lambda: np.zeros((0, 0, len(BAR_FEATURE_KEYS)), dtype=np.float32))
    ticker_macro_bar_mask: np.ndarray = field(default_factory=lambda: np.zeros((0, 0), dtype=np.bool_))
    global_market_bars: np.ndarray = field(default_factory=lambda: np.zeros((0, 0, 0, len(BAR_FEATURE_KEYS)), dtype=np.float32))
    global_market_bar_mask: np.ndarray = field(default_factory=lambda: np.zeros((0, 0, 0), dtype=np.bool_))
    macro_features: dict[str, np.ndarray] = field(default_factory=dict)
    global_features: dict[str, np.ndarray] = field(default_factory=dict)
    input_availability: dict[str, np.ndarray] = field(default_factory=dict)
    text_inputs: dict[str, dict[str, np.ndarray]] = field(default_factory=dict)
    xbrl_inputs: dict[str, np.ndarray] = field(default_factory=dict)
    external_context: dict[str, Any] = field(default_factory=dict)
    profile: Any | None = None


class WindowEncoder(Protocol):
    def encode_window(self, events: Sequence[CompactEvent], *, previous_sip_us: int | None = None) -> tuple[np.ndarray, np.ndarray]:
        """Encode exactly one fixed-size market event window into bytes."""


class EncoderModel(Protocol):
    def encode(self, headers_uint8: np.ndarray, events_uint8: np.ndarray) -> np.ndarray:
        """Return embeddings for a market encoder batch."""
