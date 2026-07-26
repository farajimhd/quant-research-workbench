from __future__ import annotations

import unittest
import datetime as dt
from pathlib import Path

import numpy as np
import torch

from research.news_reaction_model.v17 import RESPONSE_WINDOWS
from research.news_reaction_model.v17.config import LoaderConfig, ModelConfig
from research.news_reaction_model.v17.model import NewsResponseModelV17
from research.news_reaction_model.v17.prepare_targets import (
    BuildCancelled,
    CancellationController,
    EASTERN,
    build_windows,
    event_rows_for_tickers,
    process_ticker_batch,
    session_days_between,
    summarize_events,
)
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

    def test_batched_event_query_always_prunes_dates_and_splits_tickers(self) -> None:
        class Client:
            query = ""
            query_id = ""

            def execute(self, query: str, *, query_id: str | None = None) -> str:
                self.query = query
                self.query_id = query_id or ""
                return (
                    "AAPL\t100\t1\t10.0\t5\t9.9\t10.0\t1\t1\n"
                    "MSFT\t200\t2\t20.0\t7\t19.9\t20.0\t1\t1\n"
                )

        client = Client()
        cancellation = CancellationController()
        rows = event_rows_for_tickers(
            client,
            LoaderConfig(),
            ["AAPL", "MSFT"],
            dt.date(2026, 1, 2),
            cancellation=cancellation,
        )
        self.assertIn("event_date >= toDate('2026-01-02')", client.query)
        self.assertIn("ticker IN ('AAPL', 'MSFT')", client.query)
        self.assertIn("ORDER BY t.ticker,t.sip_timestamp_us,t.ordinal", client.query)
        self.assertTrue(client.query_id.startswith("news-v17-targets-"))
        self.assertEqual(cancellation.active_query_ids(), ())
        self.assertEqual(rows["AAPL"].shape, (1, 8))
        self.assertEqual(rows["MSFT"].shape, (1, 8))
        self.assertEqual(float(rows["MSFT"][0, 2]), 20.0)

    def test_cancellation_stops_new_work_and_targets_only_registered_queries(self) -> None:
        class Client:
            query = ""

            def execute(self, query: str) -> str:
                self.query = query
                return ""

        client = Client()
        cancellation = CancellationController()
        first = cancellation.register_query()
        second = cancellation.register_query()
        cancellation.request_stop()
        self.assertEqual(cancellation.cancel_active_queries(client), 2)
        self.assertIn("KILL QUERY WHERE query_id IN (", client.query)
        self.assertIn(first, client.query)
        self.assertIn(second, client.query)
        with self.assertRaises(BuildCancelled):
            cancellation.register_query()
        cancellation.unregister_query(first)
        cancellation.unregister_query(second)
        self.assertEqual(cancellation.active_query_ids(), ())

    def test_session_slice_uses_closed_exchange_interval(self) -> None:
        sessions = [
            dt.date(2026, 1, 2),
            dt.date(2026, 1, 5),
            dt.date(2026, 1, 6),
        ]
        self.assertEqual(
            session_days_between(
                sessions, dt.date(2026, 1, 3), dt.date(2026, 1, 6)
            ),
            (dt.date(2026, 1, 5), dt.date(2026, 1, 6)),
        )

    def test_ticker_batch_fetches_each_session_once_for_multiple_tickers(self) -> None:
        sessions = [
            dt.date(2026, 1, 2),
            dt.date(2026, 1, 5),
            dt.date(2026, 1, 6),
            dt.date(2026, 1, 7),
            dt.date(2026, 1, 8),
            dt.date(2026, 1, 9),
        ]
        published = dt.datetime(2026, 1, 2, 15, 0, tzinfo=dt.timezone.utc)
        v16 = {
            "canonical_news_id": np.asarray([b"news-a", b"news-m"], dtype="S16"),
            "ticker": np.asarray([b"AAPL", b"MSFT"], dtype="S8"),
            "published_at_utc": np.asarray(
                [
                    published.isoformat().encode(),
                    published.isoformat().encode(),
                ],
                dtype="S40",
            ),
        }
        labels = {}
        for news_id, ticker in (("news-a", "AAPL"), ("news-m", "MSFT")):
            labels[(news_id, ticker)] = {
                "publication_session": "regular",
                "reaction_session_date": sessions[0],
                "anchor_price": 100.0,
                "phase": {
                    "quality_status": "clean",
                    "high_return": 0.02,
                    "low_return": -0.01,
                    "terminal_return": 0.01,
                    "high_timestamp": published.timestamp() + 60,
                    "low_timestamp": published.timestamp() + 120,
                    "observation_count": 3,
                },
            }
        calls: list[tuple[dt.date, tuple[str, ...]]] = []

        def make_rows(ticker: str, day: dt.date) -> np.ndarray:
            start = dt.datetime.combine(day, dt.time(15), tzinfo=dt.timezone.utc)
            start_us = int(start.timestamp() * 1_000_000)
            base = 100.0 if ticker == "AAPL" else 101.0
            return np.asarray(
                [
                    [start_us + 1, 1, base, 10, base - 0.1, base, 1, 1],
                    [start_us + 2, 2, base + 1, 20, base, base + 1, 1, 1],
                    [start_us + 3, 3, base + 0.5, 30, base + 0.4, base + 0.5, 1, 1],
                ],
                dtype=np.float64,
            )

        def loader(_client, _config, tickers, day, _cancellation):
            calls.append((day, tuple(tickers)))
            return {ticker: make_rows(ticker, day) for ticker in tickers}

        output, query_count, event_count = process_ticker_batch(
            client=object(),
            config=LoaderConfig(),
            v16=v16,
            labels=labels,
            sessions=sessions,
            items=[("AAPL", [0]), ("MSFT", [1])],
            event_loader=loader,
        )
        self.assertEqual(query_count, len(sessions))
        self.assertEqual(len(calls), len(sessions))
        self.assertTrue(all(set(tickers) == {"AAPL", "MSFT"} for _day, tickers in calls))
        self.assertEqual(event_count, len(sessions) * 2 * 3)
        self.assertEqual(len(output), 2)
        by_row = {row_index: (raw, mask) for row_index, raw, mask in output}
        for row_index, (raw, mask) in by_row.items():
            self.assertTrue(mask[1])
            self.assertTrue(mask[3])
            self.assertTrue(mask[4])
            ticker = "AAPL" if row_index == 0 else "MSFT"
            windows = build_windows(published, "regular", sessions[0], sessions)
            expected = np.full_like(raw, np.nan)
            expected_mask = np.zeros_like(mask)
            absolute_cache = {}
            for window_index, bounds in enumerate(windows):
                if bounds is None:
                    continue
                selected = session_days_between(
                    sessions,
                    bounds[0].astimezone(EASTERN).date(),
                    bounds[1].astimezone(EASTERN).date(),
                )
                exact = labels[
                    ("news-a" if ticker == "AAPL" else "news-m", ticker)
                ]["phase"] if window_index < 3 else None
                expected[window_index], expected_mask[window_index] = summarize_events(
                    [make_rows(ticker, day) for day in selected],
                    start=bounds[0],
                    end=bounds[1],
                    anchor_price=100.0,
                    exact_phase=exact,
                    minimum_observations=3,
                    absolute_cache=absolute_cache,
                )
            np.testing.assert_allclose(
                raw, expected, equal_nan=True, rtol=0.0, atol=0.0
            )
            np.testing.assert_array_equal(mask, expected_mask)


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
