from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Iterable, Iterator

from market_ai.config import MarketAIConfig
from market_ai.service import StreamBatchingEngine, run_encoder_batch
from market_ai.state import StreamState
from market_ai.types import CompactEvent, EncoderModel, EventChunk, TemporalSample, TrainingSample


@dataclass(slots=True)
class FutureChunkLabeler:
    """Attach future event chunks to temporal samples during historical replay."""

    state: StreamState
    future_chunks: int = 2
    pending: dict[str, deque[TemporalSample]] = field(default_factory=lambda: defaultdict(deque))

    def register(self, sample: TemporalSample) -> None:
        self.pending[sample.ticker.upper()].append(sample)

    def update(self, event: CompactEvent) -> list[TrainingSample]:
        ticker = event.ticker.upper()
        ready: list[TrainingSample] = []
        queue = self.pending[ticker]
        if not queue:
            return ready
        future_events = self.future_chunks * self.state.config.events_per_chunk
        while queue:
            sample = queue[0]
            origin = sample.origin_ordinal
            if origin is None or event.ordinal is None:
                break
            if int(event.ordinal) < int(origin) + future_events:
                break
            queue.popleft()
            future = self._future_chunks_for_sample(ticker, sample)
            if len(future) == self.future_chunks:
                ready.append(TrainingSample(temporal_sample=sample, future_chunks=future))
        return ready

    def _future_chunks_for_sample(self, ticker: str, sample: TemporalSample) -> tuple[EventChunk, ...]:
        events = self.state.recent_events(ticker, self.future_chunks * self.state.config.events_per_chunk + self.state.config.events_per_chunk)
        if sample.origin_ordinal is None:
            return ()
        after = [event for event in events if event.ordinal is not None and int(event.ordinal) > int(sample.origin_ordinal)]
        chunks: list[EventChunk] = []
        for index in range(self.future_chunks):
            start = index * self.state.config.events_per_chunk
            end = start + self.state.config.events_per_chunk
            window = tuple(after[start:end])
            if len(window) != self.state.config.events_per_chunk:
                break
            encoded = self.state.window_encoder.encode_window(window)
            chunks.append(
                EventChunk(
                    ticker=ticker,
                    origin_timestamp_us=int(window[-1].sip_timestamp_us),
                    origin_ordinal=window[-1].ordinal,
                    header_uint8=encoded[0],
                    events_uint8=encoded[1],
                    source_events=window,
                )
            )
        return tuple(chunks)


def iter_temporal_samples(
    *,
    events: Iterable[CompactEvent],
    engine: StreamBatchingEngine,
    encoder_model: EncoderModel,
) -> Iterator[TemporalSample]:
    """Replay events through the production engine and yield temporal samples."""

    for event in events:
        batch = engine.process_event(event)
        if batch is None:
            continue
        for temporal_batch in run_encoder_batch(engine, encoder_model, batch):
            yield from temporal_batch.samples
    final_batch = engine.flush_encoder()
    if final_batch is not None:
        for temporal_batch in run_encoder_batch(engine, encoder_model, final_batch):
            yield from temporal_batch.samples
    final_temporal = engine.flush_temporal()
    if final_temporal is not None:
        yield from final_temporal.samples


def iter_labeled_replay_samples(
    *,
    events: Iterable[CompactEvent],
    engine: StreamBatchingEngine,
    encoder_model: EncoderModel,
    future_chunks: int = 2,
) -> Iterator[TrainingSample]:
    """Replay historical events and yield temporal samples once their future chunks exist."""

    labeler = FutureChunkLabeler(state=engine.state, future_chunks=future_chunks)
    for event in events:
        batch = engine.process_event(event)
        if batch is not None:
            for temporal_batch in run_encoder_batch(engine, encoder_model, batch):
                for sample in temporal_batch.samples:
                    labeler.register(sample)
        yield from labeler.update(event)
    final_batch = engine.flush_encoder()
    if final_batch is not None:
        for temporal_batch in run_encoder_batch(engine, encoder_model, final_batch):
            for sample in temporal_batch.samples:
                labeler.register(sample)


def make_replay_engine(config: MarketAIConfig, window_encoder) -> StreamBatchingEngine:
    """Factory used by trainers to guarantee they use the serving state machine."""

    return StreamBatchingEngine(config=config, window_encoder=window_encoder)
