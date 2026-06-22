from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class MarketAIConfig:
    """Runtime knobs shared by live serving and offline replay."""

    events_per_chunk: int = 128
    header_bytes: int = 14
    event_bytes: int = 16
    chunk_stride_events: int = 1
    encoder_batch_size: int = 8192
    temporal_batch_size: int = 4096
    embedding_dim: int = 32
    embedding_history: int = 4096
    recent_context_embeddings: int = 16
    recent_context_stride: int = 1
    older_context_embeddings: int = 48
    older_context_min_lag: int = 32
    older_context_max_lag: int = 2048
    strict_lossless_windows: bool = True
    emit_invalid_windows: bool = False
    prediction_horizons_events: tuple[int, ...] = field(default_factory=lambda: (128, 256))

    @property
    def context_lags(self) -> tuple[int, ...]:
        return build_context_lags(
            recent_count=self.recent_context_embeddings,
            recent_stride=self.recent_context_stride,
            older_count=self.older_context_embeddings,
            older_min_lag=self.older_context_min_lag,
            older_max_lag=self.older_context_max_lag,
        )


def build_context_lags(
    *,
    recent_count: int,
    recent_stride: int,
    older_count: int,
    older_min_lag: int,
    older_max_lag: int,
) -> tuple[int, ...]:
    """Return unique embedding lags, with dense recent context and sparse older context."""

    recent_stride = max(1, int(recent_stride))
    lags = {lag for lag in range(0, max(0, int(recent_count)) * recent_stride, recent_stride)}
    older_count = max(0, int(older_count))
    if older_count:
        older_min_lag = max(1, int(older_min_lag))
        older_max_lag = max(older_min_lag, int(older_max_lag))
        if older_count == 1:
            lags.add(older_max_lag)
        else:
            for index in range(older_count):
                fraction = index / float(older_count - 1)
                lag = round(older_min_lag * ((older_max_lag / older_min_lag) ** fraction))
                lags.add(int(lag))
    return tuple(sorted(lags))
