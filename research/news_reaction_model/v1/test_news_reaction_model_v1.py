from __future__ import annotations

import datetime as dt
import unittest

import torch

from research.news_reaction_model.v1 import HORIZONS
from research.news_reaction_model.v1.config import LoaderConfig, ModelConfig
from research.news_reaction_model.v1.data import prepared_batch_sql, rows_to_batch, source_batch_sql
from research.news_reaction_model.v1.losses import compute_loss
from research.news_reaction_model.v1.inference import forecast_rows
from research.news_reaction_model.v1.model import NewsReactionModelV1
from research.news_reaction_model.v1.prepare_data import (
    completed_range_sql,
    create_manifest_sql,
    create_table_sql,
    insert_month_sql,
    record_completed_range_sql,
    split_for_month,
)


class NewsReactionModelV1Tests(unittest.TestCase):
    def test_chronological_split_defaults_are_locked(self) -> None:
        config = LoaderConfig()
        self.assertEqual((config.train_start, config.train_end_exclusive), ("2019-01-01", "2026-01-01"))
        self.assertEqual((config.validation_start, config.validation_end_exclusive), ("2026-01-01", "2027-01-01"))

    def test_source_query_enforces_single_ticker_and_exact_identity(self) -> None:
        sql = source_batch_sql(LoaderConfig(), dt.date(2026, 1, 1), dt.date(2026, 2, 1), include_format=False)
        self.assertIn("HAVING uniqExact(upperUTF8(t.ticker)) = 1", sql)
        self.assertIn("s.canonical_news_id = e.source_id", sql)
        self.assertIn("s.ticker = upperUTF8(e.ticker)", sql)
        self.assertIn("s.published_at_utc = e.published_at_utc", sql)
        self.assertIn("quality.eligible_for_statistics = 1", sql)
        self.assertIn("news_reaction_robust_scale_v2_1", sql)

    def test_prepared_loader_uses_tuple_keyset_and_dataset_version(self) -> None:
        config = LoaderConfig(dataset_version="test-version")
        sql = prepared_batch_sql(config, dt.date(2026, 1, 1), dt.date(2026, 2, 1), "2026-01-01", "AAPL", "id", 256)
        self.assertIn("dataset_version = 'test-version'", sql)
        self.assertIn("(published_at_utc, ticker, canonical_news_id) >", sql)
        self.assertIn("ORDER BY published_at_utc, ticker, canonical_news_id", sql)

    def test_preparation_table_and_insert_preserve_contract(self) -> None:
        config = LoaderConfig(dataset_version="test-version")
        ddl = create_table_sql(config)
        insert = insert_month_sql(config, dt.date(2025, 12, 1), dt.date(2026, 1, 1))
        self.assertIn("Array(Tuple(UInt8, Array(Float32)))", ddl)
        self.assertIn("ReplacingMergeTree", ddl)
        self.assertIn("range_end_exclusive Date", create_manifest_sql(config))
        self.assertIn("range_start = toDate('2025-12-01')", completed_range_sql(config, dt.date(2025, 12, 1), dt.date(2026, 1, 1)))
        self.assertIn("'completed', 123", record_completed_range_sql(config, dt.date(2025, 12, 1), dt.date(2026, 1, 1), 123))
        self.assertIn("'test-version', 'train'", insert)
        self.assertNotIn("FORMAT JSONEachRow", insert)
        self.assertEqual(split_for_month(dt.date(2025, 12, 1)), "train")
        self.assertEqual(split_for_month(dt.date(2026, 1, 1)), "validation")

    def test_batch_maps_horizons_and_model_backpropagates(self) -> None:
        loader = LoaderConfig(embedding_dim=8, max_chunks=2)
        rows = [{
            "source_id": "news-1", "ticker": "AAPL", "published_at_utc": "2026-07-14 13:41:00.000000000",
            "chunks": [[0, [0.1] * 8], [1, [0.2] * 8]], "publication_session": "regular",
            "horizon_codes": ["5m", "1m"], "class_targets": [2, 0],
            "return_targets": [[0.02, 0.03, -0.01], [-0.01, 0.01, -0.02]],
        }]
        batch = rows_to_batch(rows, loader)
        self.assertEqual(set(batch.x), {"embeddings", "chunk_mask"})
        self.assertEqual(batch.identity["canonical_news_id"], ["news-1"])
        self.assertEqual(batch.class_targets[0, HORIZONS.index("1m")].item(), 0)
        self.assertEqual(batch.class_targets[0, HORIZONS.index("5m")].item(), 2)
        self.assertEqual(int(batch.label_mask.sum()), 2)
        model = NewsReactionModelV1(ModelConfig(embedding_dim=8, d_model=16, hidden_dim=16, layers=1))
        output = model(batch.x)
        self.assertEqual(tuple(output.class_logits.shape), (1, len(HORIZONS), 3))
        self.assertEqual(tuple(output.return_forecasts.shape), (1, len(HORIZONS), 3))
        result = compute_loss(output, batch)
        self.assertTrue(torch.isfinite(result.loss))
        result.loss.backward()
        self.assertTrue(any(parameter.grad is not None for parameter in model.parameters()))
        inference_rows = [{key: value for key, value in rows[0].items() if key not in {"horizon_codes", "class_targets", "return_targets"}}]
        forecasts = forecast_rows(model, inference_rows, loader_config=loader)
        self.assertEqual(forecasts[0]["canonical_news_id"], "news-1")
        self.assertEqual(set(forecasts[0]["forecasts"]), set(HORIZONS))
        probabilities = forecasts[0]["forecasts"]["1m"]
        self.assertAlmostEqual(
            probabilities["probability_negative"] + probabilities["probability_neutral"] + probabilities["probability_positive"],
            1.0,
            places=5,
        )


if __name__ == "__main__":
    unittest.main()
