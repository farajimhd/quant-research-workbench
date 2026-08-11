from __future__ import annotations

import unittest

from src.backend.query_plans.news_detail_asof_v1 import (
    rendered_article,
    service_article,
    service_tickers,
    trading_article,
    trading_tickers,
)


class NewsDetailQueryPlanTests(unittest.TestCase):
    def test_service_detail_joins_exact_render_revision_and_ticker_identity(self) -> None:
        article = service_article("news'1")
        tickers = service_tickers("news'1")

        self.assertIn("n.canonical_news_id = 'news\\'1'", article)
        self.assertIn("r.source_revision_key=n.source_revision_key", article)
        self.assertIn("LIMIT 1", article)
        self.assertIn("t.canonical_news_id = 'news\\'1'", tickers)
        self.assertIn("n.source_revision_key=t.source_revision_key", tickers)

    def test_trading_detail_uses_date_prewhere_and_exact_render_key(self) -> None:
        article = trading_article("news-1", published_date="2026-08-11")
        rendered = rendered_article(
            published_date="2026-08-11",
            provider_article_id="provider-1",
            source_revision_key="revision-1",
        )
        tickers = trading_tickers("news-1", published_date="2026-08-11")

        self.assertIn("PREWHERE n.published_date = toDate('2026-08-11')", article)
        self.assertIn("provider_article_id = 'provider-1'", rendered)
        self.assertIn("source_revision_key = 'revision-1'", rendered)
        self.assertIn("PREWHERE t.published_date = toDate('2026-08-11')", tickers)

    def test_incomplete_identities_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            service_article(" ")
        with self.assertRaises(ValueError):
            rendered_article(
                published_date="2026-08-11",
                provider_article_id="",
                source_revision_key="revision-1",
            )


if __name__ == "__main__":
    unittest.main()
