from __future__ import annotations

import unittest

import numpy as np

from .rf_challenger import binary_metrics, metadata_features, select_threshold


class RandomForestChallengerTests(unittest.TestCase):
    def test_metadata_features_include_provider_tags_channels_tickers_and_time(self) -> None:
        features = metadata_features({
            "published_at_text": "2026-08-20T14:30:00+00:00", "provider": "benzinga",
            "provider_tags": ["halts"], "channels": ["News"], "tickers": ["AAA"],
            "ticker_count": 1, "session_segment": "regular", "halt": True,
        })
        self.assertEqual(features["provider=benzinga"], 1.0)
        self.assertEqual(features["tag=halts"], 1.0)
        self.assertEqual(features["channel=news"], 1.0)
        self.assertEqual(features["ticker=AAA"], 1.0)
        self.assertEqual(features["bool:halt"], 1.0)

    def test_threshold_selection_is_deterministic_and_validation_only(self) -> None:
        labels = np.asarray([0, 0, 1, 1], dtype=np.int8)
        probability = np.asarray([0.1, 0.4, 0.45, 0.9])
        first, _ = select_threshold(labels, probability)
        second, _ = select_threshold(labels, probability)
        self.assertEqual(first, second)
        self.assertGreaterEqual(binary_metrics(labels, probability, first)["balanced_accuracy"], 0.5)


if __name__ == "__main__":
    unittest.main()
