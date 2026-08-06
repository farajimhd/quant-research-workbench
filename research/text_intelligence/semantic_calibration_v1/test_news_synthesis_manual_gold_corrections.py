from __future__ import annotations

import unittest

from .news_synthesis_manual_gold_corrections import CORRECTIONS, _correct_review_spec


class ManualGoldCorrectionTests(unittest.TestCase):
    def test_review_spec_correction_preserves_offset_and_makes_overall_direction_dominant(self) -> None:
        correction = CORRECTIONS[0]
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


if __name__ == "__main__":
    unittest.main()
