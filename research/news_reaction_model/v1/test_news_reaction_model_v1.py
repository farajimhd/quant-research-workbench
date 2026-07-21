from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import torch

from research.news_reaction_model.v1 import HORIZONS
from research.news_reaction_model.v1.config import ExperimentConfig, LoaderConfig, ModelConfig, TrainConfig
from research.news_reaction_model.v1.data import make_dummy_batch, prepared_batch_sql, rows_to_batch, source_batch_sql
from research.news_reaction_model.v1.losses import compute_loss
from research.news_reaction_model.v1.inference import forecast_rows
from research.news_reaction_model.v1.model import NewsReactionModelV1
from research.news_reaction_model.v1.profile_sizes import load_real_sample, main as profile_main
from research.news_reaction_model.v1.train import checkpoint_payload, maybe_compile, restore
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

    def test_masked_chunk_pooling_supports_bfloat16_autocast(self) -> None:
        loader = LoaderConfig(embedding_dim=8, max_chunks=2)
        rows = [{
            "source_id": "news-bf16", "ticker": "AAPL", "published_at_utc": "2026-07-14 13:41:00",
            "chunks": [[0, [0.1] * 8]], "publication_session": "regular",
            "horizon_codes": ["1m"], "class_targets": [1],
            "return_targets": [[0.0, 0.01, -0.01]],
        }]
        batch = rows_to_batch(rows, loader)
        model = NewsReactionModelV1(ModelConfig(embedding_dim=8, d_model=16, hidden_dim=16, layers=1))
        with torch.autocast(device_type="cpu", dtype=torch.bfloat16):
            output = model(batch.x)
        self.assertTrue(torch.isfinite(output.class_logits).all())
        self.assertTrue(torch.isfinite(output.return_forecasts).all())

    def test_profiler_rejects_silently_truncated_batch(self) -> None:
        loader = LoaderConfig(embedding_dim=8, max_chunks=2)
        source = make_dummy_batch(4, loader)
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("research.news_reaction_model.v1.profile_sizes.make_dummy_batch", return_value=source):
                with self.assertRaisesRegex(RuntimeError, "will not silently report a truncated batch"):
                    profile_main([
                        "--model-sizes", "16", "--batch-sizes", "8", "--layers", "1",
                        "--warmup-steps", "0", "--profile-steps", "1",
                        "--output-root", temp_dir,
                    ])

    def test_profiler_collects_exact_sample_across_month_batches(self) -> None:
        loader = LoaderConfig(embedding_dim=8, max_chunks=2)
        first = make_dummy_batch(4, loader)
        second = make_dummy_batch(5, loader)
        with patch("research.news_reaction_model.v1.profile_sizes.ClickHouseNewsReactionDataset") as dataset_type:
            dataset_type.return_value.iter_batches.return_value = (batch for batch in (first, second))
            sample = load_real_sample(loader, "2019-01-01", "2027-01-01", 7)
        self.assertEqual(sample.sample_count, 7)
        self.assertEqual(tuple(sample.x["embeddings"].shape), (7, 2, 8))
        self.assertEqual(len(sample.identity["canonical_news_id"]), 7)
        dataset_type.return_value.stop.assert_called_once()

    def test_compile_falls_back_to_eager_when_cuda_has_no_triton(self) -> None:
        model = NewsReactionModelV1(ModelConfig(embedding_dim=8, d_model=16, hidden_dim=16, layers=1))
        with patch("research.news_reaction_model.v1.train.torch.cuda.is_available", return_value=True):
            with patch("research.news_reaction_model.v1.train.importlib.util.find_spec", return_value=None):
                with patch("research.news_reaction_model.v1.train.torch.compile") as compile_model:
                    selected = maybe_compile(model, True)
        self.assertIs(selected, model)
        compile_model.assert_not_called()

    def test_checkpoint_resume_accepts_legacy_windows_path_and_writes_safe_config(self) -> None:
        model = NewsReactionModelV1(ModelConfig(embedding_dim=8, d_model=16, hidden_dim=16, layers=1))
        optimizer = torch.optim.AdamW(model.parameters(), lr=3e-4)
        scaler = torch.amp.GradScaler("cpu", enabled=False)
        config = ExperimentConfig(
            loader=LoaderConfig(embedding_dim=8),
            model=ModelConfig(embedding_dim=8, d_model=16, hidden_dim=16, layers=1),
            train=TrainConfig(output_root=Path("runtime/test")),
        )
        safe_payload = checkpoint_payload(model, optimizer, None, scaler, config, 10, 2)
        self.assertIsInstance(safe_payload["config"]["train"]["output_root"], str)
        legacy_payload = {**safe_payload, "config": {"train": {"output_root": Path("runtime/test")}}}
        with tempfile.TemporaryDirectory() as temp_dir:
            checkpoint = Path(temp_dir) / "legacy.pt"
            torch.save(legacy_payload, checkpoint)
            self.assertEqual(restore(str(checkpoint), model, optimizer, None, scaler, torch.device("cpu")), (10, 2))


if __name__ == "__main__":
    unittest.main()
