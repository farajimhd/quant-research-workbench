from __future__ import annotations

from typing import Sequence

import numpy as np

from market_ai.config import MarketAIConfig
from market_ai.types import CompactEvent


class HistoricalWindowEncoder:
    """Adapter around the shared historical compact-byte window encoder."""

    def __init__(self, config: MarketAIConfig) -> None:
        self.config = config
        try:
            from research.mlops.clickhouse_events import EVENT_ROW_DTYPE, encode_unified_event_window
        except Exception as error:  # pragma: no cover - depends on repo import path.
            raise RuntimeError(
                "HistoricalWindowEncoder requires research.mlops.clickhouse_events on PYTHONPATH"
            ) from error
        self._dtype = EVENT_ROW_DTYPE
        self._encode_unified_event_window = encode_unified_event_window

    def encode_window(self, events: Sequence[CompactEvent], *, previous_sip_us: int | None = None) -> tuple[np.ndarray, np.ndarray]:
        if len(events) != self.config.events_per_chunk:
            raise ValueError(f"Expected {self.config.events_per_chunk} events, got {len(events)}")
        rows = np.zeros((len(events),), dtype=self._dtype)
        for index, event in enumerate(events):
            rows[index]["span_id"] = 0
            rows[index]["ordinal"] = 0 if event.ordinal is None else int(event.ordinal)
            rows[index]["event_type"] = int(event.event_type)
            rows[index]["sip_timestamp_us"] = int(event.sip_timestamp_us)
            rows[index]["price_primary_int"] = int(event.price_primary_int)
            rows[index]["price_secondary_int"] = int(event.price_secondary_int)
            rows[index]["size_primary"] = float(event.size_primary)
            rows[index]["size_secondary"] = float(event.size_secondary)
            rows[index]["exchange_primary"] = int(event.exchange_primary)
            rows[index]["exchange_secondary"] = int(event.exchange_secondary)
            rows[index]["event_flags"] = int(event.event_flags)
            rows[index]["conditions_packed"] = int(event.conditions_packed)
        encoded = self._encode_unified_event_window(rows, previous_sip_us=previous_sip_us)
        if isinstance(encoded, str):
            raise ValueError(f"Could not encode compact event window: {encoded}")
        return encoded


class SyntheticWindowEncoder:
    """Small deterministic encoder used by smoke tests and local unit tests."""

    def __init__(self, config: MarketAIConfig) -> None:
        self.config = config

    def encode_window(self, events: Sequence[CompactEvent], *, previous_sip_us: int | None = None) -> tuple[np.ndarray, np.ndarray]:
        if len(events) != self.config.events_per_chunk:
            raise ValueError(f"Expected {self.config.events_per_chunk} events, got {len(events)}")
        header = np.zeros((self.config.header_bytes,), dtype=np.uint8)
        header[0] = len(events) & 0xFF
        header[1] = int(events[-1].event_type) & 0xFF
        header[2] = 0 if previous_sip_us is None else min(255, max(0, int(events[0].sip_timestamp_us - previous_sip_us)))
        header[-1] = 1
        encoded = np.zeros((self.config.events_per_chunk, self.config.event_bytes), dtype=np.uint8)
        for index, event in enumerate(events):
            encoded[index, 0] = int(event.event_type) & 0xFF
            encoded[index, 1] = int(event.sip_timestamp_us) & 0xFF
            encoded[index, 2] = int(event.price_primary_int) & 0xFF
            encoded[index, 3] = int(event.price_secondary_int) & 0xFF
            encoded[index, 4] = int(event.event_flags) & 0xFF
        return header, encoded
