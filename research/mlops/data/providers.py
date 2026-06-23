from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Protocol

import numpy as np

from research.mlops.data.batching import EncoderBatcher, MultiModalBatcher
from research.mlops.data.chunking import CompactEventWindowEncoder, RollingEventChunker
from research.mlops.data.config import DataProviderConfig, MarketStreamConfig
from research.mlops.data.labels import PendingFutureChunkLabeler
from research.mlops.data.market_events import events_from_rows, maybe_polars_sort_rows
from research.mlops.data.profiling import DataPrepProfile, DataPrepProfiler
from research.mlops.data.sources import EventSource
from research.mlops.data.state import MultiModalContextStore
from research.mlops.data.contracts import CompactEvent, EmbeddingRecord, EncoderBatch, EncoderModel, Modality, MultiModalTemporalBatch


class TemporalBatchProvider(Protocol):
    def next_batch(self) -> MultiModalTemporalBatch:
        ...

    def profiles(self) -> tuple[DataPrepProfile, ...]:
        ...


@dataclass(slots=True)
class StreamingReplayBatchProvider:
    """Production-compatible historical replay provider.

    Events are processed in timestamp order through rolling per-ticker state.
    Encoded market chunks become embedding records, and the final output is the
    same multimodal batch contract live serving will use.
    """

    config: DataProviderConfig
    event_source: EventSource
    encoder_model: EncoderModel
    _chunkers: dict[str, RollingEventChunker] = field(init=False, repr=False)
    _window_encoder: CompactEventWindowEncoder = field(init=False, repr=False)
    _encoder_batcher: EncoderBatcher = field(init=False, repr=False)
    _temporal_batcher: MultiModalBatcher = field(init=False, repr=False)
    _store: MultiModalContextStore = field(init=False, repr=False)
    _labeler: PendingFutureChunkLabeler = field(init=False, repr=False)
    _events: Iterator[CompactEvent] = field(init=False, repr=False)
    _profiles: list[DataPrepProfile] = field(init=False, repr=False)
    _batch_id: int = field(init=False, repr=False)

    def __post_init__(self) -> None:
        market = self.config.market
        encoder = CompactEventWindowEncoder(events_per_chunk=market.events_per_chunk)
        self._chunkers: dict[str, RollingEventChunker] = {}
        self._window_encoder = encoder
        self._encoder_batcher = EncoderBatcher(market.encoder_batch_size)
        self._temporal_batcher = MultiModalBatcher(market.temporal_batch_size)
        self._store = MultiModalContextStore(market)
        self._labeler = PendingFutureChunkLabeler(market)
        self._events = iter(self.event_source.iter_events())
        self._profiles: list[DataPrepProfile] = []
        self._batch_id = 0

    def profiles(self) -> tuple[DataPrepProfile, ...]:
        return tuple(self._profiles)

    def next_batch(self) -> MultiModalTemporalBatch:
        for batch in self.iter_batches():
            return batch
        raise StopIteration

    def iter_batches(self) -> Iterator[MultiModalTemporalBatch]:
        profiler = DataPrepProfiler(self.config.provider_name, batch_id=self._batch_id, enabled=self.config.profile_enabled)
        for event in self._events:
            profiler.profile.rows_read += 1
            if int(event.issue_flags) == 0:
                profiler.profile.valid_rows += 1
            with profiler.stage("chunk_build"):
                chunk = self._process_event(event)
            if chunk is None:
                continue
            profiler.profile.chunks_created += 1
            ready_labeled = self._labeler.register_chunk(chunk)
            for sample in ready_labeled:
                batch = self._temporal_batcher.add(sample)
                if batch is not None:
                    batch = self._attach_profile(batch, profiler)
                    self._batch_id += 1
                    yield batch
                    profiler = DataPrepProfiler(self.config.provider_name, batch_id=self._batch_id, enabled=self.config.profile_enabled)
            encoder_batch = self._encoder_batcher.add(chunk)
            if encoder_batch is not None:
                profiler.profile.encoder_batches_created += 1
                with profiler.stage("encoder_forward", count=len(encoder_batch.items)):
                    samples = self._run_encoder_batch(encoder_batch)
                profiler.profile.embeddings_created += len(encoder_batch.items)
                for sample in samples:
                    self._labeler.register_sample(sample)

    def flush(self) -> MultiModalTemporalBatch | None:
        encoder_batch = self._encoder_batcher.flush()
        if encoder_batch is not None:
            for sample in self._run_encoder_batch(encoder_batch):
                self._labeler.register_sample(sample)
        return self._temporal_batcher.flush()

    def _attach_profile(self, batch: MultiModalTemporalBatch, profiler: DataPrepProfiler) -> MultiModalTemporalBatch:
        profile = profiler.finish()
        profile.samples_created += len(batch.samples)
        profile.labels_created += sum(len(sample.labels) for sample in batch.samples)
        profile.output_batches_created += 1
        profiled_batch = MultiModalTemporalBatch(
            market_embeddings=batch.market_embeddings,
            market_mask=batch.market_mask,
            samples=batch.samples,
            news_embeddings=batch.news_embeddings,
            news_mask=batch.news_mask,
            sec_embeddings=batch.sec_embeddings,
            sec_mask=batch.sec_mask,
            fundamental_embeddings=batch.fundamental_embeddings,
            fundamental_mask=batch.fundamental_mask,
            global_embeddings=batch.global_embeddings,
            global_mask=batch.global_mask,
            labels=batch.labels,
            label_masks=batch.label_masks,
            profile=profile,
        )
        self._profiles.append(profile)
        return profiled_batch

    def _process_event(self, event: CompactEvent):
        ticker = event.ticker.upper()
        chunker = self._chunkers.get(ticker)
        if chunker is None:
            market = self.config.market
            chunker = RollingEventChunker(
                events_per_chunk=market.events_per_chunk,
                chunk_stride_events=market.chunk_stride_events,
                window_encoder=self._window_encoder,
                strict_lossless_windows=market.strict_lossless_windows,
                emit_invalid_windows=market.emit_invalid_windows,
            )
            self._chunkers[ticker] = chunker
        return chunker.add(event)

    def _run_encoder_batch(self, batch: EncoderBatch):
        if batch.headers_uint8 is None or batch.events_uint8 is None:
            return ()
        embeddings = np.asarray(self.encoder_model.encode(batch.headers_uint8, batch.events_uint8), dtype=np.float32)
        if embeddings.shape[0] != len(batch.items):
            raise ValueError(f"Encoder returned {embeddings.shape[0]} embeddings for {len(batch.items)} chunks")
        samples = []
        for chunk, embedding in zip(batch.items, embeddings):
            record = EmbeddingRecord(
                modality=Modality.MARKET,
                ticker=chunk.ticker,
                timestamp_us=chunk.origin_timestamp_us,
                ordinal=chunk.origin_ordinal,
                embedding=embedding,
                source=chunk,
            )
            self._store.add_embedding(record)
            sample = self._store.build_sample(chunk.ticker)
            if sample is not None:
                samples.append(sample)
        return tuple(samples)


@dataclass(slots=True)
class PolarsTickerBlockBatchProvider:
    """Vectorized bounded-block provider for training experiments.

    This provider prepares a sorted in-memory ticker block with Polars when
    available, then reuses the streaming provider. It is intentionally a
    strategy wrapper: trainers still consume `MultiModalTemporalBatch`.
    """

    rows: np.ndarray
    ticker: str
    config: DataProviderConfig
    encoder_model: EncoderModel

    def build_streaming_provider(self) -> StreamingReplayBatchProvider:
        rows = maybe_polars_sort_rows(self.rows)
        events = events_from_rows(rows, ticker=self.ticker)
        from research.mlops.data.sources import InMemoryEventSource

        return StreamingReplayBatchProvider(
            config=DataProviderConfig(
                provider_name="polars_ticker_block",
                market=self.config.market,
                profile_enabled=self.config.profile_enabled,
                detailed_profile=self.config.detailed_profile,
                max_batches=self.config.max_batches,
                seed=self.config.seed,
            ),
            event_source=InMemoryEventSource(events, sort=False),
            encoder_model=self.encoder_model,
        )

    def next_batch(self) -> MultiModalTemporalBatch:
        return self.build_streaming_provider().next_batch()

    def profiles(self) -> tuple[DataPrepProfile, ...]:
        return ()
