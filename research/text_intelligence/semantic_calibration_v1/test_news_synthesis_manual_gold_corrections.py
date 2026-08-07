from __future__ import annotations

import unittest

from .news_synthesis_manual_gold_corrections import (
    CORRECTIONS,
    HISTORICAL_RECAP_CORRECTIONS,
    _correct_historical_recap_review_spec,
    _correct_review_spec,
)


class ManualGoldCorrectionTests(unittest.TestCase):
    def test_review_spec_correction_preserves_offset_and_makes_overall_direction_dominant(self) -> None:
        correction = next(row for row in CORRECTIONS if row.sample_id == "N1130")
        spec = {
            "sample_id": correction.sample_id,
            "review_notes": "Original review.",
            "statements": [
                {
                    "concept_leaf": "capital.financing",
                    "evidence": ["proposed secondary public offering of 4,000,000 shares"],
                    "participations": [{"entity_id": "security:SNCY", "semantic_sentiment": "negative", "sentiment_strength": 2}],
                },
                {
                    "concept_leaf": "capital.return",
                    "evidence": ["approximately $5 million of shares"],
                    "participations": [{"entity_id": "security:SNCY", "semantic_sentiment": "positive", "sentiment_strength": 3}],
                },
            ],
        }
        _correct_review_spec(spec, correction)
        negative = spec["statements"][0]["participations"][0]
        positive = spec["statements"][1]["participations"][0]
        self.assertEqual(negative["sentiment_strength"], 3)
        self.assertEqual(positive["sentiment_strength"], 2)
        self.assertEqual(spec["issuer_view_overrides"][0]["composite_sentiment"], "negative")
        self.assertIn("dominant supply-overhang implication", spec["review_notes"])

    def test_historical_recap_is_not_a_fresh_trigger(self) -> None:
        correction = HISTORICAL_RECAP_CORRECTIONS[0]
        spec = {
            "sample_id": correction.sample_id,
            "review_notes": "Original review.",
            "envelope": {"communication_purpose": "report"},
            "statements": [
                {
                    "statement_kind": "event",
                    "concept_leaf": "earnings.performance",
                    "time_relation": "current",
                    "participations": [{"entity_id": "security:HIBB"}],
                },
                {
                    "statement_kind": "market_observation",
                    "concept_leaf": "market.price_move_observed",
                    "time_relation": "historical",
                    "participations": [{"entity_id": "security:HIBB"}],
                },
            ],
        }
        _correct_historical_recap_review_spec(spec, correction)
        self.assertEqual(spec["envelope"]["communication_purpose"], "recap")
        self.assertEqual(spec["statements"][0]["time_relation"], "historical")
        self.assertIn("not a fresh forecast or reaction trigger", spec["review_notes"])


if __name__ == "__main__":
    unittest.main()
