from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from market_ai.config import MarketAIConfig
from market_ai.encoding import HistoricalWindowEncoder, SyntheticWindowEncoder
from market_ai.metrics import MarketAIMetrics
from market_ai.service import MeanByteSmokeEncoder, StreamBatchingEngine, SumSmokeTemporalModel, run_encoder_batch, run_temporal_batch
from market_ai.sources import iter_qmd_compact_events, iter_synthetic_events
from market_ai.terminal import run_terminal_dashboard
from market_ai.types import CompactEvent, EncoderBatch, TemporalBatch


@dataclass(frozen=True, slots=True)
class MarketAIServiceConfig:
    source: str = "qmd"
    qmd_url: str = "ws://127.0.0.1:8795/stream/compact-events"
    max_events: int = 0
    terminal_refresh_seconds: float = 0.5
    reconnect_delay_seconds: float = 2.0
    rich_enabled: bool = True
    rich_screen: bool = True
    synthetic_tickers: tuple[str, ...] = ("AAPL", "NVDA", "TSLA", "MSFT")
    synthetic_events_per_second: float = 5000.0


class MarketAIService:
    def __init__(self, *, market_config: MarketAIConfig, service_config: MarketAIServiceConfig) -> None:
        self.market_config = market_config
        self.service_config = service_config
        self.metrics = MarketAIMetrics()
        self.stop_event = asyncio.Event()
        window_encoder = SyntheticWindowEncoder(market_config) if service_config.source == "synthetic" else HistoricalWindowEncoder(market_config)
        self.engine = StreamBatchingEngine(config=market_config, window_encoder=window_encoder)
        self.encoder_model = MeanByteSmokeEncoder(market_config.embedding_dim)
        self.temporal_model = SumSmokeTemporalModel()
        self._encoder_flush_seconds_seen = 0.0
        self._temporal_flush_seconds_seen = 0.0

    async def run(self) -> None:
        self.metrics.message("Market AI service starting.")
        dashboard_task: asyncio.Task[None] | None = None
        if self.service_config.rich_enabled:
            dashboard_task = asyncio.create_task(run_terminal_dashboard(self))
        try:
            await self._consume()
        finally:
            self.metrics.source_status = "draining"
            await self._drain()
            self.stop_event.set()
            if dashboard_task is not None:
                done, pending = await asyncio.wait([dashboard_task], timeout=2.0)
                for task in pending:
                    task.cancel()
                for task in done:
                    task.result()
            self.metrics.source_status = "finished"
            self.metrics.message("Market AI service stopped.")

    async def _consume(self) -> None:
        self.metrics.source_status = "running"
        if self.service_config.source == "synthetic":
            async for event in self._source_iter():
                await self._handle_event(event)
                if self._should_stop_for_max_events():
                    break
            return
        while not self.stop_event.is_set():
            try:
                self.metrics.message(f"Connecting to qmd compact stream {self.service_config.qmd_url}")
                async for event in self._source_iter():
                    await self._handle_event(event)
                    if self._should_stop_for_max_events():
                        return
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.metrics.error(f"qmd stream disconnected: {error}")
                await asyncio.sleep(self.service_config.reconnect_delay_seconds)

    def _should_stop_for_max_events(self) -> bool:
        if self.service_config.max_events and self.metrics.events_received >= self.service_config.max_events:
            self.metrics.message(f"Reached max_events={self.service_config.max_events:,}.")
            return True
        return False

    def _source_iter(self):
        if self.service_config.source == "synthetic":
            return iter_synthetic_events(
                tickers=self.service_config.synthetic_tickers,
                events_per_second=self.service_config.synthetic_events_per_second,
                max_events=self.service_config.max_events,
                stop_event=self.stop_event,
            )
        return iter_qmd_compact_events(self.service_config.qmd_url, stop_event=self.stop_event)

    async def _handle_event(self, event: CompactEvent) -> None:
        self.metrics.events_received += 1
        self.metrics.last_event_at_utc = datetime.now(UTC).isoformat(timespec="seconds")
        start = time.perf_counter()
        try:
            batch = self.engine.process_event(event)
        except Exception as error:
            self.metrics.events_dropped += 1
            self.metrics.error(f"event processing failed for {event.ticker}: {error}")
            return
        self.metrics.event_process_seconds += time.perf_counter() - start
        self._collect_batcher_timing()
        if batch is None:
            return
        await self._run_encoder_batch(batch)

    async def _run_encoder_batch(self, batch: EncoderBatch) -> None:
        self.metrics.encoder_batches += 1
        self.metrics.encoder_samples += len(batch.chunks)
        self.metrics.chunks_created += len(batch.chunks)
        model_start = time.perf_counter()
        temporal_batches = run_encoder_batch(self.engine, self.encoder_model, batch)
        self.metrics.encoder_model_seconds += time.perf_counter() - model_start
        self._collect_batcher_timing()
        for temporal_batch in temporal_batches:
            await self._run_temporal_batch(temporal_batch)
        await asyncio.sleep(0)

    async def _run_temporal_batch(self, batch: TemporalBatch) -> None:
        self.metrics.temporal_batches += 1
        self.metrics.temporal_samples += len(batch.samples)
        model_start = time.perf_counter()
        predictions = run_temporal_batch(self.temporal_model, batch)
        self.metrics.temporal_model_seconds += time.perf_counter() - model_start
        self.metrics.predictions += len(predictions)
        await asyncio.sleep(0)

    async def _drain(self) -> None:
        encoder_batch = self.engine.flush_encoder()
        if encoder_batch is not None:
            await self._run_encoder_batch(encoder_batch)
        temporal_batch = self.engine.flush_temporal()
        if temporal_batch is not None:
            self._collect_batcher_timing()
            await self._run_temporal_batch(temporal_batch)

    def _collect_batcher_timing(self) -> None:
        encoder_total = float(self.engine.encoder_batcher.total_flush_seconds)
        temporal_total = float(self.engine.temporal_batcher.total_flush_seconds)
        if encoder_total > self._encoder_flush_seconds_seen:
            self.metrics.chunk_batch_prep_seconds += encoder_total - self._encoder_flush_seconds_seen
            self._encoder_flush_seconds_seen = encoder_total
        if temporal_total > self._temporal_flush_seconds_seen:
            self.metrics.temporal_context_seconds += temporal_total - self._temporal_flush_seconds_seen
            self._temporal_flush_seconds_seen = temporal_total
