from __future__ import annotations

import unittest

import polars as pl

from research.inhouse_transformer.v22.config import DataConfig
from research.inhouse_transformer.v22.data import (
    BatchBuilder,
    attach_quote_state_to_trades,
    build_ticker_sparse_chunks_vectorized,
    ticker_arrays,
)
from research.inhouse_transformer.v22.targets import log_return_bps


class VectorizedChunkTests(unittest.TestCase):
    def test_chunk_sync_selection_and_overflow(self) -> None:
        config = DataConfig(chunk_ms=500, max_quote_events=2, max_trade_events=2, max_total_events=3)
        base = 10_000_000_000
        quotes = pl.DataFrame(
            {
                "ticker": ["T"] * 3,
                "session_date": ["2025-11-03"] * 3,
                "sip_timestamp": [base + 100_000_000, base + 200_000_000, base + 300_000_000],
                "sequence_number": [1, 2, 3],
                "bid_price": [10.00, 10.01, 10.02],
                "ask_price": [10.02, 10.03, 10.04],
                "mid_price": [10.01, 10.02, 10.03],
                "spread_bps": [19.98, 19.96, 19.94],
                "bid_size": [100.0, 101.0, 102.0],
                "ask_size": [120.0, 121.0, 122.0],
                "quote_imbalance": [-0.09, -0.08, -0.07],
                "bid_exchange": [1, 1, 1],
                "ask_exchange": [2, 2, 2],
            }
        )
        trades = pl.DataFrame(
            {
                "ticker": ["T"] * 3,
                "session_date": ["2025-11-03"] * 3,
                "sip_timestamp": [base + 150_000_000, base + 250_000_000, base + 350_000_000],
                "sequence_number": [1, 2, 3],
                "price": [10.02, 10.03, 10.04],
                "size": [10.0, 20.0, 30.0],
                "exchange": [3, 3, 3],
            }
        )
        trades = attach_quote_state_to_trades(trades, quotes)

        chunks = build_ticker_sparse_chunks_vectorized(config, "T", quotes, trades)
        self.assertIsNotNone(chunks)
        row = chunks.row(0, named=True)

        self.assertEqual(row["chunk_start_ns"], base)
        self.assertEqual(len(row["quote_values"]), 1)
        self.assertEqual(len(row["trade_values"]), 2)
        self.assertEqual(row["event_kinds"], [1, 0, 1])
        self.assertEqual(row["event_indices"], [0, 0, 1])
        self.assertEqual(row["quote_count"], 3.0)
        self.assertEqual(row["trade_count"], 3.0)
        self.assertEqual(row["overflow_quote_count"], 2.0)
        self.assertEqual(row["overflow_trade_count"], 1.0)
        self.assertEqual(row["overflow_total_count"], 3.0)
        self.assertAlmostEqual(row["latest_bid"], 10.02)
        self.assertAlmostEqual(row["latest_ask"], 10.04)

    def test_loader_targets_use_future_bid_ask_mid(self) -> None:
        config = DataConfig(
            chunk_ms=500,
            context_seconds=1,
            horizon_steps=1,
            horizon_seconds=1,
            max_quote_events=2,
            max_trade_events=2,
            max_total_events=3,
            target_cache_horizon_chunks=(2,),
        )
        base = 20_000_000_000
        quotes = pl.DataFrame(
            {
                "ticker": ["T"] * 5,
                "session_date": ["2025-11-03"] * 5,
                "sip_timestamp": [base + offset for offset in (100_000_000, 600_000_000, 1_100_000_000, 1_600_000_000, 2_100_000_000)],
                "sequence_number": [1, 2, 3, 4, 5],
                "bid_price": [10.00, 10.10, 10.20, 10.30, 10.40],
                "ask_price": [10.02, 10.12, 10.22, 10.32, 10.42],
                "mid_price": [10.01, 10.11, 10.21, 10.31, 10.41],
                "spread_bps": [19.98] * 5,
                "bid_size": [100.0] * 5,
                "ask_size": [120.0] * 5,
                "quote_imbalance": [-0.09] * 5,
                "bid_exchange": [1] * 5,
                "ask_exchange": [2] * 5,
            }
        )
        chunks = build_ticker_sparse_chunks_vectorized(config, "T", quotes, pl.DataFrame())
        self.assertIsNotNone(chunks)
        self.assertIn("target_bid_h2", chunks.columns)
        self.assertAlmostEqual(float(chunks.row(1, named=True)["target_bid_h2"]), 10.30, places=5)
        self.assertAlmostEqual(float(chunks.row(1, named=True)["target_ask_h2"]), 10.32, places=5)
        arrays = ticker_arrays(chunks, config)
        self.assertIsNotNone(arrays)

        batch = BatchBuilder(config=config, batch_size=1)
        batch.add(arrays, 1, ticker="T")
        current_mid = (10.10 + 10.12) * 0.5
        future_mid = (10.30 + 10.32) * 0.5
        expected = float(log_return_bps(future_mid, current_mid))

        self.assertAlmostEqual(float(batch.target_bid[0, 0, 0]), 10.30, places=5)
        self.assertAlmostEqual(float(batch.target_ask[0, 0, 0]), 10.32, places=5)
        self.assertAlmostEqual(float(batch.target_mid[0, 0, 0]), future_mid, places=5)
        self.assertAlmostEqual(float(batch.target_bps[0, 0, 0]), expected, delta=0.002)

    def test_cached_targets_are_shifted_on_dense_chunk_grid(self) -> None:
        config = DataConfig(
            chunk_ms=500,
            max_quote_events=2,
            max_trade_events=2,
            max_total_events=3,
            target_cache_horizon_chunks=(2,),
        )
        base = 30_000_000_000
        quotes = pl.DataFrame(
            {
                "ticker": ["T", "T"],
                "session_date": ["2025-11-03", "2025-11-03"],
                "sip_timestamp": [base + 100_000_000, base + 5_100_000_000],
                "sequence_number": [1, 2],
                "bid_price": [10.00, 11.00],
                "ask_price": [10.02, 11.02],
                "mid_price": [10.01, 11.01],
                "spread_bps": [19.98, 18.16],
                "bid_size": [100.0, 200.0],
                "ask_size": [120.0, 220.0],
                "quote_imbalance": [-0.09, -0.05],
                "bid_exchange": [1, 1],
                "ask_exchange": [2, 2],
            }
        )
        chunks = build_ticker_sparse_chunks_vectorized(config, "T", quotes, pl.DataFrame())
        self.assertIsNotNone(chunks)
        first = chunks.row(0, named=True)

        self.assertEqual(chunks.height, 2)
        self.assertAlmostEqual(float(first["target_bid_h2"]), 10.00, places=5)
        self.assertAlmostEqual(float(first["target_ask_h2"]), 10.02, places=5)
        self.assertAlmostEqual(float(first["target_mid_h2"]), 10.01, places=5)


if __name__ == "__main__":
    unittest.main()
