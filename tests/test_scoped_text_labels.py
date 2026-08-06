from __future__ import annotations

import unittest

from src.backend.scoped_text_labels import scoped_sec_summary


class ScopedSecLabelPresentationTests(unittest.TestCase):
    def test_summary_selects_requested_ticker_and_preserves_all_eligibility(self) -> None:
        labels = [
            {
                "ticker": "AAA",
                "content_role": "editorial_analysis",
                "source_origin": "editorial",
                "semantic_direction": "negative",
                "semantic_score": -0.4,
                "event_concepts": ["margin_pressure"],
                "forecast_trigger_eligible": True,
                "reaction_evaluation_eligible": True,
                "issuer_history_context_eligible": True,
                "prior_primary_context_eligible": True,
                "episode_followup_eligible": False,
            },
            {
                "ticker": "BBB",
                "content_role": "corporate_transaction",
                "source_origin": "company",
                "semantic_direction": "positive",
                "semantic_score": 0.8,
                "event_concepts": ["acquisition"],
                "forecast_trigger_eligible": True,
                "reaction_evaluation_eligible": True,
                "issuer_history_context_eligible": True,
            },
        ]

        summary = scoped_sec_summary(labels, ticker="AAA")

        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertEqual(summary["semantic_direction"], "negative")
        self.assertEqual(summary["event_concepts"], ["margin_pressure"])
        self.assertEqual(summary["issuer_count"], 1)
        self.assertTrue(summary["prior_primary_context_eligible"])
        self.assertFalse(summary["episode_followup_eligible"])


if __name__ == "__main__":
    unittest.main()
