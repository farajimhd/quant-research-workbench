from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(slots=True)
class MarketAIMetrics:
    started_monotonic: float = field(default_factory=time.perf_counter)
    started_at_utc: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    events_received: int = 0
    events_dropped: int = 0
    chunks_created: int = 0
    encoder_batches: int = 0
    encoder_samples: int = 0
    temporal_batches: int = 0
    temporal_samples: int = 0
    predictions: int = 0
    warnings: int = 0
    errors: int = 0
    event_process_seconds: float = 0.0
    chunk_batch_prep_seconds: float = 0.0
    encoder_model_seconds: float = 0.0
    temporal_context_seconds: float = 0.0
    temporal_model_seconds: float = 0.0
    last_event_at_utc: str = ""
    source_status: str = "starting"
    last_error: str = ""
    messages: deque[str] = field(default_factory=lambda: deque(maxlen=12))
    _prior_snapshot: dict[str, Any] = field(default_factory=dict)
    _prior_snapshot_monotonic: float = 0.0
    _rates: dict[str, float] = field(default_factory=dict)

    def message(self, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.messages.append(f"{stamp} {text}")

    def error(self, text: str) -> None:
        self.errors += 1
        self.last_error = text
        self.message(f"ERROR {text}")

    def warning(self, text: str) -> None:
        self.warnings += 1
        self.message(f"WARN {text}")

    def snapshot(self) -> dict[str, Any]:
        now = time.perf_counter()
        elapsed = max(1e-9, now - self.started_monotonic)
        payload = {
            "started_at_utc": self.started_at_utc,
            "elapsed_seconds": elapsed,
            "source_status": self.source_status,
            "events_received": self.events_received,
            "events_dropped": self.events_dropped,
            "chunks_created": self.chunks_created,
            "encoder_batches": self.encoder_batches,
            "encoder_samples": self.encoder_samples,
            "temporal_batches": self.temporal_batches,
            "temporal_samples": self.temporal_samples,
            "predictions": self.predictions,
            "warnings": self.warnings,
            "errors": self.errors,
            "last_event_at_utc": self.last_event_at_utc,
            "last_error": self.last_error,
            "event_process_seconds": self.event_process_seconds,
            "chunk_batch_prep_seconds": self.chunk_batch_prep_seconds,
            "encoder_model_seconds": self.encoder_model_seconds,
            "temporal_context_seconds": self.temporal_context_seconds,
            "temporal_model_seconds": self.temporal_model_seconds,
            "event_process_ms_per_event": 1000.0 * self.event_process_seconds / max(1, self.events_received),
            "chunk_batch_prep_ms_per_batch": 1000.0 * self.chunk_batch_prep_seconds / max(1, self.encoder_batches),
            "encoder_model_ms_per_batch": 1000.0 * self.encoder_model_seconds / max(1, self.encoder_batches),
            "temporal_context_ms_per_batch": 1000.0 * self.temporal_context_seconds / max(1, self.temporal_batches),
            "temporal_model_ms_per_batch": 1000.0 * self.temporal_model_seconds / max(1, self.temporal_batches),
            "messages": list(self.messages),
        }
        payload.update(self._update_rates(payload, now))
        return payload

    def _update_rates(self, payload: dict[str, Any], now: float) -> dict[str, float]:
        elapsed = now - self._prior_snapshot_monotonic if self._prior_snapshot_monotonic else 0.0
        keys = ("events_received", "chunks_created", "encoder_samples", "temporal_samples", "predictions")
        if elapsed > 0:
            self._rates = {
                f"{key}_per_sec": max(0.0, (float(payload[key]) - float(self._prior_snapshot.get(key, 0))) / elapsed)
                for key in keys
            }
        self._prior_snapshot = {key: payload[key] for key in keys}
        self._prior_snapshot_monotonic = now
        return dict(self._rates)
