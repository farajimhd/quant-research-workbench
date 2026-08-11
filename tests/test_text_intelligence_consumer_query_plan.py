from __future__ import annotations

import unittest

from src.backend.query_plans.text_intelligence_consumer_v1 import (
    news_synthesis_by_id,
    scoped_labels,
)


def quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class TextIntelligenceConsumerQueryPlanTests(unittest.TestCase):
    def test_scoped_labels_are_bounded_and_preserve_causal_filters(self) -> None:
        sql = scoped_labels(
            "sec",
            ["doc-2", "doc-1", "doc-1"],
            labeling_version="scoped_text_labeling_v5",
            quote=quote,
            source_start="2026-08-01T00:00:00Z",
            source_end="2026-08-02T00:00:00Z",
            ticker="aaa",
        )

        self.assertIn("FROM q_live.scoped_text_labels_v5", sql)
        self.assertIn("corpus='sec' AND ticker='AAA'", sql)
        self.assertIn("source_id IN ('doc-1','doc-2')", sql)
        self.assertIn("source_timestamp >= parseDateTime64BestEffort", sql)
        self.assertIn("source_timestamp <= parseDateTime64BestEffort", sql)
        self.assertIn("LIMIT 1 BY corpus,ticker,source_timestamp,source_id,unit_id,labeling_version", sql)

    def test_news_synthesis_is_bounded_to_current_version_and_identity(self) -> None:
        sql = news_synthesis_by_id(
            ["news-2", "news-1"],
            engine_version="v1",
            synthesis_table="news_synthesis_v1",
            quote=quote,
        )

        self.assertIn("FROM q_live.news_synthesis_v1 FINAL", sql)
        self.assertIn("engine_version='v1'", sql)
        self.assertIn("canonical_news_id IN ('news-1','news-2')", sql)
        self.assertIn("LIMIT 1 BY canonical_news_id,engine_version", sql)

    def test_empty_id_sets_and_untrusted_identifiers_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            scoped_labels("sec", [], labeling_version="v5", quote=quote)
        with self.assertRaises(ValueError):
            news_synthesis_by_id(
                ["news-1"],
                engine_version="v1",
                synthesis_table="news_synthesis_v1 FINAL; DROP TABLE x",
                quote=quote,
            )


if __name__ == "__main__":
    unittest.main()
