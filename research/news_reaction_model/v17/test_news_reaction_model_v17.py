from __future__ import annotations

import unittest
import datetime as dt
from pathlib import Path

import numpy as np
import torch

from research.news_reaction_model.v17 import RESPONSE_WINDOWS
from research.news_reaction_model.v17.config import ModelConfig
from research.news_reaction_model.v17.model import NewsResponseModelV17
from research.news_reaction_model.v17.prepare_targets import summarize_events
from research.news_reaction_model.v17.prepared import row_key_hash
from research.news_reaction_model.v17.targets import (
    Direction,
    Flow,
    Path,
    Persistence,
    TargetThresholds,
    classify_persistence,
    classify_window,
    fit_thresholds,
)


class V17TargetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = TargetThresholds((0.01,) * len(RESPONSE_WINDOWS))

    def metrics(
        self,
        *,
        high: float,
        low: float,
        terminal: float,
        high_time: float,
        low_time: float,
        buy: float = 0.5,
        sell: float = 0.5,
    ) -> list[float]:
        return [
            100.0,
            high,
            low,
            terminal,
            high_time,
            low_time,
            0.0,
            low,
            high,
            buy,
            sell,
            0.0,
            1.0,
            terminal,
            20.0,
            3600.0,
        ]

    def test_spike_fade_and_supply_are_separate_targets(self) -> None:
        direction, path, flow = classify_window(
            self.metrics(
                high=0.08,
                low=-0.01,
                terminal=0.01,
                high_time=0.2,
                low_time=0.8,
                buy=0.30,
                sell=0.70,
            ),
            threshold=0.01,
            contract=self.contract,
        )
        self.assertEqual(direction, Direction.UPSIDE)
        self.assertEqual(path, Path.SPIKE_FADE)
        self.assertEqual(flow, Flow.SUPPLY_DOMINANT)

    def test_flush_recovery_is_order_sensitive(self) -> None:
        _, path, _ = classify_window(
            self.metrics(
                high=0.01,
                low=-0.08,
                terminal=-0.01,
                high_time=0.8,
                low_time=0.2,
            ),
            threshold=0.01,
            contract=self.contract,
        )
        self.assertEqual(path, Path.FLUSH_RECOVERY)

    def test_two_sided_excursion_uses_dominant_direction_and_keeps_path(self) -> None:
        direction, path, _ = classify_window(
            self.metrics(
                high=0.08,
                low=-0.06,
                terminal=0.01,
                high_time=0.2,
                low_time=0.8,
            ),
            threshold=0.01,
            contract=self.contract,
        )
        self.assertEqual(direction, Direction.UPSIDE)
        self.assertEqual(path, Path.SPIKE_FADE)

    def test_equal_excursions_use_terminal_then_extremum_order_as_tie_breakers(self) -> None:
        positive_terminal, _, _ = classify_window(
            self.metrics(
                high=0.05,
                low=-0.05,
                terminal=0.01,
                high_time=0.2,
                low_time=0.8,
            ),
            threshold=0.01,
            contract=self.contract,
        )
        negative_terminal, _, _ = classify_window(
            self.metrics(
                high=0.05,
                low=-0.05,
                terminal=-0.01,
                high_time=0.8,
                low_time=0.2,
            ),
            threshold=0.01,
            contract=self.contract,
        )
        zero_terminal, _, _ = classify_window(
            self.metrics(
                high=0.05,
                low=-0.05,
                terminal=0.0,
                high_time=0.8,
                low_time=0.2,
            ),
            threshold=0.01,
            contract=self.contract,
        )
        self.assertEqual(positive_terminal, Direction.UPSIDE)
        self.assertEqual(negative_terminal, Direction.DOWNSIDE)
        self.assertEqual(zero_terminal, Direction.UPSIDE)

    def test_persistence_uses_future_windows_without_actor_attribution(self) -> None:
        self.assertEqual(
            classify_persistence(
                [
                    Direction.UPSIDE,
                    Direction.NEUTRAL,
                    Direction.NEUTRAL,
                    Direction.UPSIDE,
                    Direction.UPSIDE,
                ],
                [True, False, False, True, True],
            ),
            Persistence.MULTI_SESSION,
        )
        self.assertEqual(
            classify_persistence(
                [
                    Direction.UPSIDE,
                    Direction.NEUTRAL,
                    Direction.NEUTRAL,
                    Direction.DOWNSIDE,
                    Direction.DOWNSIDE,
                ],
                [True, False, False, True, True],
            ),
            Persistence.REVERSAL,
        )
        self.assertEqual(
            classify_persistence(
                [
                    Direction.NEUTRAL,
                    Direction.NEUTRAL,
                    Direction.NEUTRAL,
                    Direction.UPSIDE,
                    Direction.UPSIDE,
                ],
                [False, False, False, True, True],
            ),
            Persistence.DELAYED,
        )

    def test_threshold_fit_uses_passed_partition_only(self) -> None:
        raw = np.zeros((4, len(RESPONSE_WINDOWS), 16), dtype=np.float32)
        mask = np.ones((4, len(RESPONSE_WINDOWS)), dtype=np.bool_)
        raw[:, :, 1] = np.asarray([0.01, 0.02, 0.03, 0.04])[:, None]
        raw[:, :, 2] = -raw[:, :, 1]
        contract = fit_thresholds(raw, mask, quantile=0.5, floor=0.001)
        np.testing.assert_allclose(contract.meaningful_return, [0.025] * 5)

    def test_row_key_hash_is_stable_and_identity_sensitive(self) -> None:
        first = row_key_hash("news", "AAPL", "2026-01-01T10:00:00Z")
        self.assertEqual(first, row_key_hash("news", "AAPL", "2026-01-01T10:00:00Z"))
        self.assertNotEqual(first, row_key_hash("news", "MSFT", "2026-01-01T10:00:00Z"))

    def test_vectorized_event_summary_preserves_order_and_quote_test_flow(self) -> None:
        start = dt.datetime(2026, 1, 2, 14, 30, tzinfo=dt.timezone.utc)
        start_us = int(start.timestamp() * 1_000_000)
        rows = np.asarray(
            [
                [start_us + 1, 1, 100.0, 10, 99.9, 100.0, 1, 1],
                [start_us + 2, 2, 110.0, 20, 109.9, 110.0, 1, 1],
                [start_us + 3, 3, 101.0, 30, 101.0, 101.1, 1, 1],
            ],
            dtype=np.float64,
        )
        metrics, valid = summarize_events(
            [rows],
            start=start,
            end=start + dt.timedelta(minutes=1),
            anchor_price=100.0,
        )
        self.assertTrue(valid)
        self.assertAlmostEqual(float(metrics[1]), 0.10, places=6)
        self.assertAlmostEqual(float(metrics[3]), 0.01, places=6)
        self.assertLess(float(metrics[7]), -0.08)
        self.assertGreater(float(metrics[9]), 0.0)
        self.assertGreater(float(metrics[10]), 0.0)


class V17ModelTests(unittest.TestCase):
    def test_model_reuses_v16_encoder_without_old_heads(self) -> None:
        config = ModelConfig(
            d_model=24,
            hidden_dim=32,
            layers=1,
            attention_heads=6,
            context_size=2,
            market_context_size=3,
            market_leader_size=2,
        )
        model = NewsResponseModelV17(config)
        self.assertFalse(hasattr(model.encoder, "opportunity_heads"))
        batch = 2
        x = {
            "openai_embedding": torch.randn(batch, config.openai_embedding_dim),
            "stock_state": torch.randn(batch, config.stock_state_dim),
            "time_features": torch.randn(batch, config.time_feature_dim),
            "channel_mask": torch.ones(batch, 4, dtype=torch.bool),
            "prior_openai_embeddings": torch.randn(
                batch, config.context_size, config.openai_embedding_dim
            ),
            "prior_context_features": torch.randn(
                batch, config.context_size, config.context_feature_dim
            ),
            "prior_context_mask": torch.ones(batch, config.context_size, dtype=torch.bool),
            "current_market_features": torch.randn(
                batch, config.current_market_feature_dim
            ),
            "market_context_openai_embeddings": torch.randn(
                batch, config.market_context_size, config.openai_embedding_dim
            ),
            "market_context_features": torch.randn(
                batch, config.market_context_size, config.market_news_feature_dim
            ),
            "market_context_mask": torch.ones(
                batch, config.market_context_size, dtype=torch.bool
            ),
            "market_leader_features": torch.randn(
                batch, config.market_leader_size, config.market_leader_feature_dim
            ),
            "market_leader_mask": torch.ones(
                batch, config.market_leader_size, dtype=torch.bool
            ),
        }
        output = model(x)
        self.assertEqual(tuple(output.direction_logits.shape), (batch, 5, 3))
        self.assertEqual(tuple(output.path_logits.shape), (batch, 5, 6))
        self.assertEqual(tuple(output.flow_logits.shape), (batch, 5, 3))
        self.assertEqual(tuple(output.persistence_logits.shape), (batch, 6))


if __name__ == "__main__":
    unittest.main()
