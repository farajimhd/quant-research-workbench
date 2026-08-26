from __future__ import annotations

import unittest

from scripts import apply_why_moving_label_corrections as correction
from research.text_intelligence.news_synthesis_v1.engine import why_moving_title_pattern


class _FakeClient:
    def __init__(self) -> None:
        self.query = ""

    def iter_json_each_row(self, query: str):
        self.query = query
        yield {
            "canonical_news_id": "rising",
            "title": "Kratos Defense Stock Is Rising Monday: What's Going On?",
        }
        yield {
            "canonical_news_id": "ordinary",
            "title": "Company Announces Quarterly Results",
        }


class WhyMovingLabelCorrectionTest(unittest.TestCase):
    def test_title_population_query_does_not_duplicate_engine_pattern(self) -> None:
        client = _FakeClient()

        titles = correction._titles_in_scope(client)

        self.assertEqual(set(titles), {"rising", "ordinary"})
        self.assertNotIn("match(", client.query.casefold())
        self.assertEqual(
            why_moving_title_pattern(titles["rising"]),
            "stock_or_shares_price_action",
        )
        self.assertIsNone(why_moving_title_pattern(titles["ordinary"]))

    def test_v2_publishes_from_immutable_v1_successor(self) -> None:
        self.assertTrue(correction.PARENT_TRAINING.name.endswith("_v1"))
        self.assertTrue(correction.DEFAULT_TRAINING_OUTPUT.name.endswith("_v2"))
        self.assertNotEqual(
            correction.PARENT_TRAINING,
            correction.DEFAULT_TRAINING_OUTPUT,
        )


if __name__ == "__main__":
    unittest.main()
