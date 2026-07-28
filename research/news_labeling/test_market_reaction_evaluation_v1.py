from __future__ import annotations

import unittest

from research.news_labeling.market_reaction_evaluation_v1 import (
    classification_metrics,
    prediction_class,
    reaction_class,
)


class MarketReactionEvaluationTests(unittest.TestCase):
    def test_reaction_class_uses_span_then_dominant_excursion(self) -> None:
        self.assertEqual(reaction_class(0.001, -0.001, 0.20), "neutral")
        self.assertEqual(reaction_class(0.03, -0.01, 0.20), "positive")
        self.assertEqual(reaction_class(0.01, -0.03, 0.20), "negative")

    def test_mixed_semantics_abstain_as_neutral(self) -> None:
        self.assertEqual(
            prediction_class({"sentiment": {"overall": "mixed"}}), "neutral"
        )

    def test_metrics_keep_neutral_in_three_class_confusion(self) -> None:
        metrics = classification_metrics(
            [
                ("positive", "positive", 0.9),
                ("negative", "positive", 0.8),
                ("neutral", "neutral", 0.7),
            ]
        )
        self.assertEqual(metrics["rows"], 3)
        self.assertAlmostEqual(metrics["accuracy"], 2 / 3)
        self.assertEqual(metrics["active_direction_rows"], 2)
        self.assertAlmostEqual(metrics["active_direction_accuracy"], 0.5)


if __name__ == "__main__":
    unittest.main()
