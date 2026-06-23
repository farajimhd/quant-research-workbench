from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

from research.mlops.data.config import MarketStreamConfig
from research.mlops.data.contracts import EmbeddingRecord, Modality, ModalityContext, MultiModalTemporalSample


@dataclass(slots=True)
class TickerContextState:
    market_embeddings: deque[EmbeddingRecord]
    news_embeddings: deque[EmbeddingRecord]
    sec_embeddings: deque[EmbeddingRecord]
    fundamental_embeddings: deque[EmbeddingRecord]


class MultiModalContextStore:
    """Per-ticker embedding queues shared by historical replay and live serving."""

    def __init__(self, config: MarketStreamConfig) -> None:
        self.config = config
        self._states: dict[str, TickerContextState] = {}

    def get_or_create(self, ticker: str) -> TickerContextState:
        key = ticker.upper()
        state = self._states.get(key)
        if state is None:
            capacity = max(1, int(self.config.embedding_history))
            state = TickerContextState(
                market_embeddings=deque(maxlen=capacity),
                news_embeddings=deque(maxlen=capacity),
                sec_embeddings=deque(maxlen=capacity),
                fundamental_embeddings=deque(maxlen=capacity),
            )
            self._states[key] = state
        return state

    def add_embedding(self, record: EmbeddingRecord) -> None:
        state = self.get_or_create(record.ticker)
        target = {
            Modality.MARKET: state.market_embeddings,
            Modality.NEWS: state.news_embeddings,
            Modality.SEC: state.sec_embeddings,
            Modality.FUNDAMENTAL: state.fundamental_embeddings,
        }.get(record.modality)
        if target is None:
            return
        target.append(record)

    def build_sample(self, ticker: str, *, labels=()) -> MultiModalTemporalSample | None:
        state = self.get_or_create(ticker)
        market = select_lagged_context(tuple(state.market_embeddings), self.config.context_lags, embedding_dim=self.config.embedding_dim)
        if market is None:
            return None
        latest = market.records[-1]
        return MultiModalTemporalSample(
            ticker=ticker.upper(),
            origin_timestamp_us=int(latest.timestamp_us),
            origin_ordinal=latest.ordinal,
            market=market,
            news=select_recent_context(tuple(state.news_embeddings)),
            sec=select_recent_context(tuple(state.sec_embeddings)),
            fundamental=select_recent_context(tuple(state.fundamental_embeddings)),
            labels=tuple(labels),
        )

    def states(self) -> dict[str, TickerContextState]:
        return dict(self._states)


def select_lagged_context(records: tuple[EmbeddingRecord, ...], lags: tuple[int, ...], *, embedding_dim: int) -> ModalityContext | None:
    if not lags:
        return None
    required = max(lags) + 1
    if len(records) < required:
        return None
    selected = [records[-1 - lag] for lag in lags]
    selected = list(reversed(selected))
    embeddings = np.stack([record.embedding for record in selected]).astype(np.float32, copy=False)
    mask = np.ones((len(selected),), dtype=np.bool_)
    if embeddings.shape[-1] != int(embedding_dim):
        raise ValueError(f"Expected embedding_dim={embedding_dim}, got {embeddings.shape[-1]}")
    return ModalityContext(embeddings=embeddings, mask=mask, records=tuple(selected))


def select_recent_context(records: tuple[EmbeddingRecord, ...], *, max_items: int = 32) -> ModalityContext | None:
    if not records:
        return None
    selected = records[-max(1, int(max_items)) :]
    embeddings = np.stack([record.embedding for record in selected]).astype(np.float32, copy=False)
    mask = np.ones((len(selected),), dtype=np.bool_)
    return ModalityContext(embeddings=embeddings, mask=mask, records=tuple(selected))

