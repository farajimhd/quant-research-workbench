from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import numpy as np

from market_ai.config import MarketAIConfig
from market_ai.encoding import SyntheticWindowEncoder
from market_ai.service import MeanByteSmokeEncoder, StreamBatchingEngine, run_encoder_batch
from market_ai.training import iter_labeled_replay_samples
from market_ai.types import CompactEvent


def synthetic_event(index: int, *, ticker: str = "AAPL") -> CompactEvent:
    return CompactEvent(
        ticker=ticker,
        sip_timestamp_us=1_000_000 + index,
        event_type=index % 2,
        price_primary_int=10_000 + index,
        price_secondary_int=9_990,
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


class StreamBatchingTests(unittest.TestCase):
    def test_emits_chunks_after_context_is_available(self) -> None:
        config = MarketAIConfig(events_per_chunk=4, encoder_batch_size=2, temporal_batch_size=2, recent_context_embeddings=2, older_context_embeddings=0)
        engine = StreamBatchingEngine(config=config, window_encoder=SyntheticWindowEncoder(config))
        self.assertIsNone(engine.process_event(synthetic_event(0)))
        self.assertIsNone(engine.process_event(synthetic_event(1)))
        self.assertIsNone(engine.process_event(synthetic_event(2)))
        self.assertIsNone(engine.process_event(synthetic_event(3)))
        batch = engine.process_event(synthetic_event(4))
        self.assertIsNotNone(batch)
        assert batch is not None
        self.assertEqual(batch.headers_uint8.shape, (2, 14))
        self.assertEqual(batch.events_uint8.shape, (2, 4, 16))
        self.assertEqual([chunk.origin_ordinal for chunk in batch.chunks], [3, 4])

    def test_encoder_outputs_create_temporal_samples(self) -> None:
        config = MarketAIConfig(events_per_chunk=4, encoder_batch_size=1, temporal_batch_size=2, embedding_dim=3, recent_context_embeddings=2, older_context_embeddings=0)
        engine = StreamBatchingEngine(config=config, window_encoder=SyntheticWindowEncoder(config))
        encoder = MeanByteSmokeEncoder(config.embedding_dim)
        temporal_batches = []
        for index in range(5):
            batch = engine.process_event(synthetic_event(index))
            if batch is not None:
                temporal_batches.extend(run_encoder_batch(engine, encoder, batch))
        final_temporal = engine.flush_temporal()
        if final_temporal is not None:
            temporal_batches.append(final_temporal)
        self.assertEqual(len(temporal_batches), 1)
        self.assertEqual(temporal_batches[0].contexts.shape, (1, 2, 3))

    def test_training_replay_emits_future_labeled_samples(self) -> None:
        config = MarketAIConfig(events_per_chunk=4, encoder_batch_size=1, temporal_batch_size=1, embedding_dim=2, recent_context_embeddings=1, older_context_embeddings=0)
        engine = StreamBatchingEngine(config=config, window_encoder=SyntheticWindowEncoder(config))
        encoder = MeanByteSmokeEncoder(config.embedding_dim)
        samples = list(iter_labeled_replay_samples(events=(synthetic_event(index) for index in range(12)), engine=engine, encoder_model=encoder, future_chunks=1))
        self.assertGreaterEqual(len(samples), 1)
        self.assertEqual(samples[0].future_chunks[0].events_uint8.shape, (4, 16))
        self.assertTrue(np.all(samples[0].temporal_sample.context_embeddings >= 0.0))


if __name__ == "__main__":
    unittest.main()
