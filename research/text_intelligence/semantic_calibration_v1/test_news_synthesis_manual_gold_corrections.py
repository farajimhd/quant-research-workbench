from __future__ import annotations

import unittest

from .news_synthesis_manual_gold_corrections import (
    CORRECTIONS,
    HISTORICAL_RECAP_CORRECTIONS,
    REVIEWED_SPEC_CORRECTIONS,
    _correct_historical_recap_review_spec,
    _correct_review_spec,
    _replace_review_spec_issuer_evidence,
)


class ManualGoldCorrectionTests(unittest.TestCase):
    def test_reviewed_spec_correction_queue_contains_exactly_eighteen_issuer_labels(self) -> None:
        identities = {(row.sample_id, row.ticker) for row in REVIEWED_SPEC_CORRECTIONS}
        self.assertEqual(len(REVIEWED_SPEC_CORRECTIONS), 18)
        self.assertEqual(len(identities), 18)
        self.assertIn(("N0103", "WW"), identities)
        self.assertIn(("N0086", "AMZN"), identities)
        self.assertIn(("N1748", "QCOM"), identities)

    def test_reviewed_spec_correction_replaces_only_target_issuer_evidence(self) -> None:
        correction = next(
            row for row in REVIEWED_SPEC_CORRECTIONS
            if row.sample_id == "N1748" and row.ticker == "ALTR"
        )
        spec = {
            "sample_id": "N1748",
            "review_notes": "Original review.",
            "entities": ["ALTR", "QCOM"],
            "issuer_view_overrides": [],
            "observed_market_moves": [{"ticker": "ALTR", "evidence": "old"}],
            "statements": [{
                "statement_kind": "assessment",
                "concept_leaf": "analyst.issuer_assessment",
                "epistemic_status": "expected",
                "time_relation": "forward",
                "evidence": ["old shared evidence"],
                "participations": [
                    {"ticker": "ALTR", "semantic_sentiment": "positive", "sentiment_strength": 4},
                    {"ticker": "QCOM", "semantic_sentiment": "positive", "sentiment_strength": 2},
                ],
            }],
        }
        _replace_review_spec_issuer_evidence(spec, correction)
        self.assertEqual(spec["statements"][0]["participations"], [
            {"ticker": "QCOM", "semantic_sentiment": "positive", "sentiment_strength": 2}
        ])
        replacements = spec["statements"][1:]
        self.assertEqual(
            [(row["participations"][0]["semantic_sentiment"], row["participations"][0]["sentiment_strength"]) for row in replacements],
            [("positive", 2), ("negative", 2)],
        )
        self.assertEqual(spec["observed_market_moves"], [])
        self.assertIn("long-term favorite", spec["review_notes"])

    def test_reviewed_spec_correction_removes_spurious_issuer(self) -> None:
        correction = next(
            row for row in REVIEWED_SPEC_CORRECTIONS
            if row.sample_id == "N0086" and row.ticker == "AMZN"
        )
        spec = {
            "sample_id": "N0086",
            "review_notes": "Original review.",
            "entities": ["TGT", "AMZN"],
            "issuer_view_overrides": [],
            "statements": [{
                "evidence": ["context"],
                "participations": [{"ticker": "AMZN"}],
            }],
        }
        _replace_review_spec_issuer_evidence(spec, correction)
        _replace_review_spec_issuer_evidence(spec, correction)
        self.assertEqual(spec["entities"], ["TGT"])
        self.assertEqual(spec["statements"], [])
        self.assertIn("only named as competitive context", spec["review_notes"])

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
