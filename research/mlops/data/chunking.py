from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from research.mlops.clickhouse_events import encode_unified_event_window
from research.mlops.compact_events import EVENT_BYTES, HEADER_BYTES
from research.mlops.data.market_events import events_to_rows
from research.mlops.data.contracts import CompactEvent, EventChunk, WindowEncoder


@dataclass(slots=True)
class CompactEventWindowEncoder(WindowEncoder):
    events_per_chunk: int = 128

    def encode_window(self, events: Sequence[CompactEvent], *, previous_sip_us: int | None = None) -> tuple[np.ndarray, np.ndarray]:
        if len(events) != int(self.events_per_chunk):
            raise ValueError(f"Expected {self.events_per_chunk} events, got {len(events)}")
        encoded = encode_unified_event_window(events_to_rows(events), previous_sip_us=previous_sip_us)
        if isinstance(encoded, str):
            raise ValueError(encoded)
        header, event_bytes = encoded
        if header.shape != (HEADER_BYTES,) or event_bytes.shape != (self.events_per_chunk, EVENT_BYTES):
            raise ValueError(f"Unexpected encoded event-window shape: header={header.shape} events={event_bytes.shape}")
        return header, event_bytes


@dataclass(slots=True)
class RollingEventChunker:
    events_per_chunk: int = 128
    chunk_stride_events: int = 1
    window_encoder: WindowEncoder = field(default_factory=CompactEventWindowEncoder)
    strict_lossless_windows: bool = True
    emit_invalid_windows: bool = False
    _history: deque[CompactEvent] = field(init=False)
    _total_events: int = 0

    def __post_init__(self) -> None:
        self._history = deque(maxlen=max(int(self.events_per_chunk), int(self.events_per_chunk) * 2))

    def add(self, event: CompactEvent) -> EventChunk | None:
        self._history.append(event)
        self._total_events += 1
        if len(self._history) < int(self.events_per_chunk):
            return None
        if (self._total_events - int(self.events_per_chunk)) % max(1, int(self.chunk_stride_events)) != 0:
            return None
        history = tuple(self._history)
        window = history[-int(self.events_per_chunk) :]
        previous_sip_us = int(history[-int(self.events_per_chunk) - 1].sip_timestamp_us) if len(history) > int(self.events_per_chunk) else None
        issue_flags = 0
        try:
            header, events = self.window_encoder.encode_window(window, previous_sip_us=previous_sip_us)
        except ValueError:
            if self.strict_lossless_windows and not self.emit_invalid_windows:
                return None
            if not self.emit_invalid_windows:
                return None
            header = np.zeros((HEADER_BYTES,), dtype=np.uint8)
            events = np.zeros((int(self.events_per_chunk), EVENT_BYTES), dtype=np.uint8)
            issue_flags = 1
        latest = window[-1]
        return EventChunk(
            ticker=latest.ticker.upper(),
            origin_timestamp_us=int(latest.sip_timestamp_us),
            origin_ordinal=latest.ordinal,
            header_uint8=header,
            events_uint8=events,
            source_events=window,
            issue_flags=issue_flags,
        )

