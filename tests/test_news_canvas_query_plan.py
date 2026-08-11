from __future__ import annotations

import unittest
from datetime import UTC, datetime

from src.backend.application_registry import QUERY_PLANS
from src.backend.query_plans.news_canvas_asof_v1 import (
    QUERY_PLAN_ID,
    QUERY_PLAN_VERSION,
    trading_news_queries,
)


class NewsCanvasQueryPlanTests(unittest.TestCase):
    def build(self, **overrides: object) -> tuple[str, str]:
        values: dict[str, object] = {
            "before": "2026-08-11T14:00:00Z",
            "before_id": "abc123",
            "cutoff": datetime(2026, 8, 11, 15, 0, tzinfo=UTC),
            "cursor": datetime(2026, 8, 11, 14, 0, tzinfo=UTC),
            "eligibility_filters": {},
            "engine_version": "news_synthesis_v1",
            "exact_source_id": "",
            "safe_content": "all",
            "safe_direction": "",
            "safe_eligibility": "",
            "safe_kind": "all",
            "safe_label_state": "",
            "safe_limit": 100,
            "safe_origin": "",
            "safe_role": "",
            "safe_ticker": "AAPL",
            "search_term": "",
            "window_start": datetime(2026, 8, 11, 9, 0, tzinfo=UTC),
        }
        values.update(overrides)
        return trading_news_queries(**values)  # type: ignore[arg-type]

    def test_plan_is_registered_with_canonical_and_synthesis_sources(self) -> None:
        plan = next(row for row in QUERY_PLANS if row.plan_id == QUERY_PLAN_ID)
        self.assertEqual(plan.version, QUERY_PLAN_VERSION)
        self.assertEqual(
            set(plan.source_paths),
            {
                "q_live.benzinga_news_event_v2",
                "q_live.benzinga_news_rendered_v2",
                "q_live.news_synthesis_v1",
            },
        )

    def test_page_query_is_bounded_and_cursor_stable(self) -> None:
        page, facet = self.build()
        self.assertIn("has(tickers, 'AAPL')", page)
        self.assertIn("canonical_news_id < 'abc123'", page)
        self.assertIn("LIMIT 101", page)
        self.assertNotIn("has(tickers, 'AAPL')", facet)
        self.assertNotIn("canonical_news_id < 'abc123'", facet)

    def test_exact_identity_ignores_ticker_and_content_refinements(self) -> None:
        source_id = "0123456789abcdef0123456789abcdef"
        page, _ = self.build(
            exact_source_id=source_id,
            safe_content="full",
            safe_ticker="MSFT",
            search_term=source_id,
        )
        self.assertIn(f"canonical_news_id = '{source_id}'", page)
        self.assertNotIn("has(tickers, 'MSFT')", page)
        self.assertNotIn("ifNull(r.source_count, 0) > 0", page)


if __name__ == "__main__":
    unittest.main()
