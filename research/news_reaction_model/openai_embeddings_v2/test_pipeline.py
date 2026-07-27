from __future__ import annotations

import datetime as dt
import unittest

from research.news_reaction_model.openai_embeddings_v2.config import PipelineConfig
from research.news_reaction_model.openai_embeddings_v2.pipeline import source_rows_sql


class ArticleLevelSourceTest(unittest.TestCase):
    def test_source_query_has_one_article_scope_and_no_training_dataset_join(self) -> None:
        config = PipelineConfig()
        sql = source_rows_sql(config, dt.date(2026, 7, 1), dt.date(2026, 8, 1))
        self.assertIn("benzinga_news_rendered_v2", sql)
        self.assertIn("'' AS ticker", sql)
        self.assertIn("r.rendered_text", sql)
        self.assertNotIn("news_reaction_stock_state_dataset", sql)
        self.assertNotIn("benzinga_news_ticker_v2", sql)


if __name__ == "__main__":
    unittest.main()
