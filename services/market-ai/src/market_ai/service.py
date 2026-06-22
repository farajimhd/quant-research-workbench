from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import numpy as np

from market_ai.batching import EncoderBatcher, TemporalBatcher
from market_ai.config import MarketAIConfig
from market_ai.encoding import SyntheticWindowEncoder
from market_ai.state import StreamState
from market_ai.types import CompactEvent, EncoderBatch, EncoderModel, PredictionRecord, TemporalBatch, TemporalModel, WindowEncoder


@dataclass(slots=True)
class StreamBatchingEngine:
    """Production/replay orchestration without owning model implementations."""

    config: MarketAIConfig
    window_encoder: WindowEncoder
    state: StreamState = field(init=False)
    encoder_batcher: EncoderBatcher = field(init=False)
    temporal_batcher: TemporalBatcher = field(init=False)

    def __post_init__(self) -> None:
        self.state = StreamState(self.config, self.window_encoder)
        self.encoder_batcher = EncoderBatcher(self.config)
        self.temporal_batcher = TemporalBatcher(self.config)

    def process_event(self, event: CompactEvent) -> EncoderBatch | None:
        chunk = self.state.process_event(event)
        if chunk is None:
            return None
        return self.encoder_batcher.add(chunk)

    def process_events(self, events: Iterable[CompactEvent]) -> list[EncoderBatch]:
        batches: list[EncoderBatch] = []
        for event in events:
            batch = self.process_event(event)
            if batch is not None:
                batches.append(batch)
        return batches

    def flush_encoder(self) -> EncoderBatch | None:
        return self.encoder_batcher.flush()

    def accept_encoder_outputs(self, batch: EncoderBatch, embeddings: np.ndarray) -> list[TemporalBatch]:
        if embeddings.shape[0] != len(batch.chunks):
            raise ValueError(f"Embedding batch size {embeddings.shape[0]} does not match {len(batch.chunks)} chunks")
        ready: list[TemporalBatch] = []
        for chunk, embedding in zip(batch.chunks, embeddings, strict=True):
            sample = self.state.add_embedding(chunk, embedding)
            if sample is None:
                continue
            temporal_batch = self.temporal_batcher.add(sample)
            if temporal_batch is not None:
                ready.append(temporal_batch)
        return ready

    def flush_temporal(self) -> TemporalBatch | None:
        return self.temporal_batcher.flush()


def run_encoder_batch(engine: StreamBatchingEngine, model: EncoderModel, batch: EncoderBatch) -> list[TemporalBatch]:
    embeddings = model.encode(batch.headers_uint8, batch.events_uint8)
    return engine.accept_encoder_outputs(batch, embeddings)


def run_temporal_batch(model: TemporalModel, batch: TemporalBatch) -> tuple[PredictionRecord, ...]:
    predictions = model.predict(batch.contexts)
    records: list[PredictionRecord] = []
    for index, sample in enumerate(batch.samples):
        prediction = predictions[index] if hasattr(predictions, "__getitem__") else predictions
        records.append(
            PredictionRecord(
                ticker=sample.ticker,
                origin_timestamp_us=sample.origin_timestamp_us,
                origin_ordinal=sample.origin_ordinal,
                prediction=prediction,
            )
        )
    return tuple(records)


class MeanByteSmokeEncoder:
    def __init__(self, embedding_dim: int) -> None:
        self.embedding_dim = int(embedding_dim)

    def encode(self, headers_uint8: np.ndarray, events_uint8: np.ndarray) -> np.ndarray:
        flat_mean = events_uint8.astype(np.float32).mean(axis=(1, 2), keepdims=False)
        offsets = np.arange(self.embedding_dim, dtype=np.float32).reshape(1, -1)
        return flat_mean.reshape(-1, 1) / 255.0 + offsets / max(1, self.embedding_dim)


class SumSmokeTemporalModel:
    def predict(self, contexts: np.ndarray) -> np.ndarray:
        return contexts.mean(axis=(1, 2), keepdims=False)


def run_synthetic_smoke() -> dict[str, int]:
    config = MarketAIConfig(
        events_per_chunk=4,
        encoder_batch_size=2,
        temporal_batch_size=2,
        embedding_dim=3,
        recent_context_embeddings=2,
        older_context_embeddings=0,
    )
    engine = StreamBatchingEngine(config=config, window_encoder=SyntheticWindowEncoder(config))
    encoder = MeanByteSmokeEncoder(config.embedding_dim)
    temporal = SumSmokeTemporalModel()
    encoder_batches = 0
    temporal_batches = 0
    temporal_samples = 0
    for index in range(7):
        batch = engine.process_event(
            CompactEvent(
                ticker="AAPL",
                sip_timestamp_us=1_000_000 + index,
                event_type=index % 2,
                price_primary_int=10_000 + index,
                price_secondary_int=9_999,
                size_primary=100.0,
                size_secondary=100.0,
                exchange_primary=1,
                exchange_secondary=2,
                event_flags=0,
                conditions_packed=0,
                source_sequence=index,
                arrival_sequence=index,
                ordinal=index,
            )
        )
        if batch is not None:
            encoder_batches += 1
            temporal_ready = run_encoder_batch(engine, encoder, batch)
            temporal_batches += len(temporal_ready)
            for temporal_batch in temporal_ready:
                temporal_samples += len(run_temporal_batch(temporal, temporal_batch))
    final_encoder = engine.flush_encoder()
    if final_encoder is not None:
        encoder_batches += 1
        temporal_ready = run_encoder_batch(engine, encoder, final_encoder)
        temporal_batches += len(temporal_ready)
        for temporal_batch in temporal_ready:
            temporal_samples += len(run_temporal_batch(temporal, temporal_batch))
    final_temporal = engine.flush_temporal()
    if final_temporal is not None:
        temporal_batches += 1
        temporal_samples += len(run_temporal_batch(temporal, final_temporal))
    return {"encoder_batches": encoder_batches, "temporal_batches": temporal_batches, "temporal_samples": temporal_samples}
