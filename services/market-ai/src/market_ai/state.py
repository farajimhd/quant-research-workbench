from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

from market_ai.config import MarketAIConfig
from market_ai.types import CompactEvent, EmbeddingRecord, EventChunk, TemporalSample, WindowEncoder


@dataclass(slots=True)
class TickerState:
    event_history: deque[CompactEvent]
    embedding_history: deque[EmbeddingRecord]
    total_events: int = 0
    emitted_chunks: int = 0
    last_previous_sip_us: int | None = None


class StreamState:
    """Per-ticker rolling state shared by live serving and historical replay."""

    def __init__(self, config: MarketAIConfig, window_encoder: WindowEncoder) -> None:
        self.config = config
        self.window_encoder = window_encoder
        self._states: dict[str, TickerState] = {}

    def get_or_create(self, ticker: str) -> TickerState:
        key = ticker.upper()
        state = self._states.get(key)
        if state is None:
            history_capacity = max(
                self.config.events_per_chunk + max(self.config.prediction_horizons_events, default=0) + 1,
                self.config.events_per_chunk * 4,
            )
            state = TickerState(
                event_history=deque(maxlen=history_capacity),
                embedding_history=deque(maxlen=max(1, self.config.embedding_history)),
            )
            self._states[key] = state
        return state

    def tickers(self) -> tuple[str, ...]:
        return tuple(sorted(self._states))

    def process_event(self, event: CompactEvent) -> EventChunk | None:
        ticker = event.ticker.upper()
        state = self.get_or_create(ticker)
        state.event_history.append(event)
        state.total_events += 1
        if len(state.event_history) < self.config.events_per_chunk:
            return None
        if (state.total_events - self.config.events_per_chunk) % max(1, self.config.chunk_stride_events) != 0:
            return None
        window = tuple(state.event_history)[-self.config.events_per_chunk :]
        try:
            header, events = self.window_encoder.encode_window(window, previous_sip_us=state.last_previous_sip_us)
        except ValueError:
            if self.config.strict_lossless_windows and not self.config.emit_invalid_windows:
                return None
            raise
        state.last_previous_sip_us = int(window[0].sip_timestamp_us)
        state.emitted_chunks += 1
        return EventChunk(
            ticker=ticker,
            origin_timestamp_us=int(window[-1].sip_timestamp_us),
            origin_ordinal=window[-1].ordinal,
            header_uint8=header,
            events_uint8=events,
            source_events=window,
        )

    def add_embedding(self, chunk: EventChunk, embedding: np.ndarray) -> TemporalSample | None:
        state = self.get_or_create(chunk.ticker)
        record = EmbeddingRecord(
            ticker=chunk.ticker,
            origin_timestamp_us=chunk.origin_timestamp_us,
            origin_ordinal=chunk.origin_ordinal,
            embedding=np.asarray(embedding, dtype=np.float32),
            chunk=chunk,
        )
        state.embedding_history.append(record)
        return self.build_temporal_sample(chunk.ticker)

    def build_temporal_sample(self, ticker: str) -> TemporalSample | None:
        state = self.get_or_create(ticker)
        lags = self.config.context_lags
        if not lags:
            return None
        required = max(lags) + 1
        if len(state.embedding_history) < required:
            return None
        records = tuple(state.embedding_history)
        selected: list[EmbeddingRecord] = []
        for lag in lags:
            selected.append(records[-1 - lag])
        selected = list(reversed(selected))
        context = np.stack([record.embedding for record in selected]).astype(np.float32, copy=False)
        latest = records[-1]
        return TemporalSample(
            ticker=ticker.upper(),
            origin_timestamp_us=latest.origin_timestamp_us,
            origin_ordinal=latest.origin_ordinal,
            context_embeddings=context,
            context_lags=lags,
            records=tuple(selected),
        )

    def recent_events(self, ticker: str, limit: int) -> tuple[CompactEvent, ...]:
        state = self.get_or_create(ticker)
        return tuple(state.event_history)[-max(0, int(limit)) :]

    def states(self) -> dict[str, TickerState]:
        return dict(self._states)
