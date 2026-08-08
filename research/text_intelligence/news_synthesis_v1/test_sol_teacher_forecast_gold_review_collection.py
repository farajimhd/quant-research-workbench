from __future__ import annotations

import unittest

from .sol_teacher_forecast_gold_review_collection import _validate_decision


class SolTeacherForecastGoldReviewCollectionTests(unittest.TestCase):
    def test_validates_direction_strength_and_gold_verdict(self) -> None:
        row = {
            "unit_id": "S00001::AAA",
            "reviewed_direction": "negative",
            "gold_verdict": "wrong",
            "positive_strength": 0,
            "negative_strength": 3,
            "dominant_evidence": "Guidance was cut below consensus.",
            "countervailing_evidence": "Revenue still grew.",
            "issuer_attribution": "supported",
            "confidence": "high",
            "rationale": "The benchmarked guidance cut dominates growth without a benchmark.",
        }

        decision = _validate_decision(row, "positive")

        self.assertEqual(decision["gold_verdict"], "wrong")

    def test_normalizes_reported_verdict_to_direction_only_authority(self) -> None:
        row = {
            "unit_id": "S00001::AAA",
            "reviewed_direction": "negative",
            "gold_verdict": "correct",
            "positive_strength": 0,
            "negative_strength": 3,
            "dominant_evidence": "Guidance was cut.",
            "countervailing_evidence": "",
            "issuer_attribution": "supported",
            "confidence": "high",
            "rationale": "The guidance cut is adverse.",
        }
        decision = _validate_decision(row, "positive")

        self.assertEqual(decision["gold_verdict"], "wrong")
        self.assertEqual(decision["reported_gold_verdict"], "correct")
        self.assertEqual(
            decision["verdict_normalization"], "direction_only_gold_authority"
        )

    def test_normalizes_numeric_string_strengths(self) -> None:
        row = {
            "unit_id": "S00001::AAA",
            "reviewed_direction": "mixed",
            "gold_verdict": "correct",
            "positive_strength": "2",
            "negative_strength": "2",
            "dominant_evidence": "Material evidence exists on both sides.",
            "countervailing_evidence": "The opposing evidence is balanced.",
            "issuer_attribution": "supported",
            "confidence": "high",
            "rationale": "The two effects have comparable economic importance.",
        }

        decision = _validate_decision(row, "mixed")

        self.assertEqual(decision["positive_strength"], 2)
        self.assertEqual(decision["negative_strength"], 2)


if __name__ == "__main__":
    unittest.main()
