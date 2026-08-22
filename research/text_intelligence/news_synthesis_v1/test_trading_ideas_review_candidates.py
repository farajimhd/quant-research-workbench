from __future__ import annotations

import unittest

from .trading_ideas_review_candidates import (
    event_evidence,
    is_trading_idea,
    noise_evidence,
    review_priority,
    strict_low_rate_channel_sets,
)


class TradingIdeasReviewCandidateTests(unittest.TestCase):
    def test_trading_idea_matches_channel_or_tag(self) -> None:
        self.assertTrue(is_trading_idea({"channels": ["Trading Ideas"]}))
        self.assertTrue(is_trading_idea({"provider_tags": ["trading ideas"]}))
        self.assertFalse(is_trading_idea({"channels": ["News"]}))

    def test_event_and_noise_evidence_remain_separate(self) -> None:
        row = {
            "channels": ["Trading Ideas", "Earnings", "Movers"],
            "provider_tags": ["Why It's Moving"],
            "material_event": True,
            "why_moving": True,
        }
        self.assertIn("channel:earnings", event_evidence(row))
        self.assertIn("text:material_event", event_evidence(row))
        self.assertIn("channel:movers", noise_evidence(row))
        self.assertIn("tag:why it's moving", noise_evidence(row))

    def test_priority_preserves_event_overlap_for_review(self) -> None:
        self.assertEqual(
            review_priority(model_disagreement=True, has_event=False, has_noise=True),
            "p0_model_disagreement_no_event",
        )
        self.assertEqual(
            review_priority(model_disagreement=False, has_event=True, has_noise=True),
            "p2_event_overlap_explicit_noise",
        )
        self.assertEqual(
            review_priority(model_disagreement=False, has_event=True, has_noise=False),
            "p3_event_overlap_only",
        )

    def test_strict_path_requires_support_and_low_rate_in_every_split(self) -> None:
        base = {
            "support": 900,
            "discovery_support": 300,
            "validation_support": 300,
            "final_support": 300,
            "eligible_rate": 0.02,
            "discovery_eligible_rate": 0.01,
            "validation_eligible_rate": 0.02,
            "final_eligible_rate": 0.03,
        }
        selected = strict_low_rate_channel_sets([
            {**base, "feature": "channel_set=news|trading ideas"},
            {**base, "feature": "tag=trading ideas"},
            {**base, "feature": "channel_set=drift", "final_eligible_rate": 0.06},
        ])
        self.assertEqual(set(selected), {"channel_set=news|trading ideas"})


if __name__ == "__main__":
    unittest.main()
