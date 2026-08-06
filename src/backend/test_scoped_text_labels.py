from __future__ import annotations

import unittest

from src.backend.scoped_text_labels import scoped_sec_summary


class ScopedTextLabelTests(unittest.TestCase):
    def test_multi_issuer_disagreement_is_explicitly_mixed(self) -> None:
        labels = [
            {
                "ticker": "AAA",
                "semantic_direction": "positive",
                "semantic_score": 0.8,
                "content_role": "primary_event",
                "event_concepts": ["corporate_transaction.acquisition"],
                "forecast_trigger_eligible": True,
                "reaction_evaluation_eligible": True,
                "issuer_history_context_eligible": True,
            },
            {
                "ticker": "BBB",
                "semantic_direction": "negative",
                "semantic_score": -0.6,
                "content_role": "primary_event",
                "event_concepts": ["corporate_transaction.acquisition"],
                "forecast_trigger_eligible": True,
                "reaction_evaluation_eligible": True,
                "issuer_history_context_eligible": True,
            },
        ]
        summary = scoped_sec_summary(labels)
        self.assertIsNotNone(summary)
        self.assertEqual(summary["semantic_direction"], "mixed")
        self.assertEqual(summary["issuer_count"], 2)

    def test_ticker_summary_keeps_issuer_specific_direction(self) -> None:
        labels = [
            {
                "ticker": "AAA",
                "semantic_direction": "positive",
                "semantic_score": 0.8,
                "content_role": "primary_event",
                "event_concepts": [],
                "forecast_trigger_eligible": True,
                "reaction_evaluation_eligible": True,
                "issuer_history_context_eligible": True,
            },
            {
                "ticker": "BBB",
                "semantic_direction": "negative",
                "semantic_score": -0.6,
                "content_role": "primary_event",
                "event_concepts": [],
                "forecast_trigger_eligible": True,
                "reaction_evaluation_eligible": True,
                "issuer_history_context_eligible": True,
            },
        ]
        summary = scoped_sec_summary(labels, ticker="AAA")
        self.assertEqual(summary["semantic_direction"], "positive")
        self.assertEqual(summary["issuer_count"], 1)


if __name__ == "__main__":
    unittest.main()
