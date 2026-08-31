from __future__ import annotations

import unittest
from datetime import UTC, datetime

from src.backend.query_plans.news_operations_v1 import (
    intraday_histogram,
    today_rows,
    today_summary,
)


class NewsOperationsQueryPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.start = datetime(2026, 8, 11, 4, tzinfo=UTC)
        self.end = datetime(2026, 8, 12, 4, tzinfo=UTC)

    def test_histogram_is_time_bounded_and_emits_complete_bucket_axis(self) -> None:
        sql = intraday_histogram(self.start, self.end, bin_seconds=300)

        self.assertIn("FROM `q_live`.`benzinga_news_event_v2` AS n FINAL", sql)
        self.assertIn("published_at_utc >= window_start", sql)
        self.assertIn("published_at_utc < window_end", sql)
        self.assertIn("FROM numbers(289)", sql)

    def test_summary_and_rows_share_exact_market_day_bounds(self) -> None:
        summary = today_summary(self.start, self.end)
        rows = today_rows(self.start, self.end, limit=250, ascending=True)

        for sql in (summary, rows):
            self.assertIn("toDateTime64('2026-08-11 04:00:00.000000'", sql)
            self.assertIn("toDateTime64('2026-08-12 04:00:00.000000'", sql)
        self.assertIn("benzinga_news_rendered_v3", rows)
        self.assertIn("r.provider_article_id=n.provider_article_id", rows)
        self.assertNotIn("r.source_revision_key=n.source_revision_key", rows)
        self.assertIn("ORDER BY n.published_at_utc ASC", rows)
        self.assertIn("LIMIT 250", rows)

    def test_row_limit_is_fail_safe_bounded(self) -> None:
        sql = today_rows(self.start, self.end, limit=50_000, ascending=False)
        self.assertIn("LIMIT 1000", sql)


if __name__ == "__main__":
    unittest.main()
