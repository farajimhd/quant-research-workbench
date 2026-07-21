from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import torch

from research.news_reaction_model.v2 import HORIZONS
from research.news_reaction_model.v2.config import ExperimentConfig, LoaderConfig, ModelConfig, TrainConfig
from research.news_reaction_model.v2.data import make_dummy_batch, prepared_batch_sql, rows_to_batch, source_batch_sql
from research.news_reaction_model.v2.losses import compute_loss
from research.news_reaction_model.v2.inference import forecast_rows
from research.news_reaction_model.v2.metrics import PositionPnlAccumulator, RegressionAccumulator
from research.news_reaction_model.v2.model import NewsReactionModelV2
from research.news_reaction_model.v2.evaluate import evaluation_batch_sql, rows_to_evaluation_batch
from research.news_reaction_model.v2.profile_sizes import load_real_sample, main as profile_main
from research.news_reaction_model.v2.train import SampleCosineRestartScheduler, checkpoint_payload, maybe_compile, restore
from research.news_reaction_model.v2.prepare_data import (
    completed_range_sql,
    create_manifest_sql,
    create_table_sql,
    insert_month_sql,
    record_completed_range_sql,
    split_for_month,
)


class NewsReactionModelV2Tests(unittest.TestCase):
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
        self.assertNotIn("class_targets", sql)

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
            "horizon_codes": ["5m", "1m"],
            "return_targets": [[0.02, 0.03, -0.01], [-0.01, 0.01, -0.02]],
        }]
        batch = rows_to_batch(rows, loader)
        self.assertEqual(set(batch.x), {"embeddings", "chunk_mask"})
        self.assertEqual(batch.identity["canonical_news_id"], ["news-1"])
        self.assertEqual(int(batch.label_mask.sum()), 2)
        model = NewsReactionModelV2(ModelConfig(embedding_dim=8, d_model=16, hidden_dim=16, layers=1))
        output = model(batch.x)
        self.assertEqual(tuple(output.return_forecasts.shape), (1, len(HORIZONS), 3))
        result = compute_loss(output, batch)
        self.assertTrue(torch.isfinite(result.loss))
        result.loss.backward()
        self.assertTrue(any(parameter.grad is not None for parameter in model.parameters()))
        inference_rows = [{key: value for key, value in rows[0].items() if key not in {"horizon_codes", "return_targets"}}]
        forecasts = forecast_rows(model, inference_rows, loader_config=loader)
        self.assertEqual(forecasts[0]["canonical_news_id"], "news-1")
        self.assertEqual(set(forecasts[0]["forecasts"]), set(HORIZONS))
        one_minute = forecasts[0]["forecasts"]["1m"]
        self.assertEqual(set(one_minute), {
            "abnormal_target_return", "abnormal_high_return", "abnormal_low_return",
        })

    def test_masked_chunk_pooling_supports_bfloat16_autocast(self) -> None:
        loader = LoaderConfig(embedding_dim=8, max_chunks=2)
        rows = [{
            "source_id": "news-bf16", "ticker": "AAPL", "published_at_utc": "2026-07-14 13:41:00",
            "chunks": [[0, [0.1] * 8]], "publication_session": "regular",
            "horizon_codes": ["1m"],
            "return_targets": [[0.0, 0.01, -0.01]],
        }]
        batch = rows_to_batch(rows, loader)
        model = NewsReactionModelV2(ModelConfig(embedding_dim=8, d_model=16, hidden_dim=16, layers=1))
        with torch.autocast(device_type="cpu", dtype=torch.bfloat16):
            output = model(batch.x)
        self.assertTrue(torch.isfinite(output.return_forecasts).all())

    def test_loss_is_plain_mse_over_actual_return_values(self) -> None:
        loader = LoaderConfig(embedding_dim=8, max_chunks=2)
        batch = make_dummy_batch(1, loader)
        batch.label_mask.zero_()
        batch.label_mask[0, 0] = True
        batch.return_targets[0, 0] = torch.tensor([0.10, 0.20, -0.10])
        output = NewsReactionModelV2(ModelConfig(embedding_dim=8, d_model=16, hidden_dim=16, layers=1))(batch.x)
        output.return_forecasts = torch.zeros_like(output.return_forecasts)
        result = compute_loss(output, batch)
        self.assertAlmostEqual(result.loss.item(), (0.10**2 + 0.20**2 + 0.10**2) / 3, places=6)

    def test_regression_metrics_compare_against_zero_forecast(self) -> None:
        accumulator = RegressionAccumulator()
        forecasts = torch.tensor([[[0.10, 0.20, -0.10]], [[0.00, 0.00, 0.00]]])
        targets = torch.tensor([[[0.20, 0.10, -0.20]], [[0.10, -0.10, 0.10]]])
        accumulator.add(forecasts, targets, torch.tensor([[True], [True]]))
        metrics = accumulator.compute("val")
        self.assertEqual(metrics["val/samples"], 2.0)
        self.assertGreater(metrics["val/zero_mse"], metrics["val/mse"])

    def test_evaluation_query_joins_raw_returns_and_training_scale(self) -> None:
        sql = evaluation_batch_sql(
            LoaderConfig(), dt.date(2026, 1, 1), dt.date(2026, 2, 1),
            "1970-01-01", "", "", 2048,
        )
        self.assertIn("r.target_return", sql)
        self.assertIn("r.high_return", sql)
        self.assertIn("r.low_return", sql)
        self.assertIn("scale_version", sql)
        self.assertIn("p.dataset_version", sql)
        self.assertIn("p.canonical_news_id", sql)

    def test_evaluation_batch_maps_raw_returns_and_scale_by_horizon(self) -> None:
        loader = LoaderConfig(embedding_dim=8, max_chunks=2)
        rows = [{
            "source_id": "news-eval", "ticker": "AAPL", "published_at_utc": "2026-07-14 13:41:00",
            "chunks": [[0, [0.1] * 8]], "publication_session": "regular",
            "horizon_codes": ["5m", "1m"],
            "return_targets": [[0.02, 0.03, -0.01], [-0.01, 0.01, -0.02]],
            "pnl_targets": [["1m", -0.008, 0.012, -0.014, 0.01], ["5m", 0.025, 0.04, -0.02, 0.02]],
        }]
        batch = rows_to_evaluation_batch(rows, loader)
        self.assertAlmostEqual(batch.raw_returns[0, HORIZONS.index("1m"), 0], -0.008)
        self.assertAlmostEqual(batch.raw_returns[0, HORIZONS.index("5m"), 1], 0.04)
        self.assertAlmostEqual(batch.robust_scales[0, HORIZONS.index("5m")], 0.02)

    def test_position_accuracy_flat_band_and_costed_pnl(self) -> None:
        accumulator = PositionPnlAccumulator()
        accumulator.add(
            predicted=np.array([0.02, -0.03, 0.001, 0.02]),
            actual_abnormal=np.array([0.01, -0.01, 0.0, -0.01]),
            actual_raw=np.array([0.012, -0.008, 0.002, -0.009]),
            raw_high=np.array([0.02, 0.005, 0.004, 0.003]),
            raw_low=np.array([-0.004, -0.015, -0.002, -0.012]),
            robust_scale=np.array([0.01, 0.01, 0.01, 0.01]),
        )
        metrics = accumulator.compute(flat_z=0.5, cost_bps=(0, 10), notional=10_000)
        self.assertEqual(metrics["samples"], 4)
        self.assertEqual(metrics["positions"], {"long": 2, "flat": 1, "short": 1, "coverage": 0.75})
        self.assertAlmostEqual(metrics["classification"]["accuracy"], 0.75)
        self.assertAlmostEqual(metrics["classification"]["active_directional_accuracy"], 2 / 3)
        self.assertAlmostEqual(metrics["gross"]["raw_total_return"], 0.011)
        self.assertAlmostEqual(metrics["gross"]["fixed_notional_raw_pnl"], 110.0)
        self.assertAlmostEqual(metrics["cost_scenarios"]["10_bps"]["total_return"], 0.008)

    def test_scheduler_performs_two_restarts_across_three_segments(self) -> None:
        parameter = torch.nn.Parameter(torch.tensor(0.0))
        optimizer = torch.optim.SGD([parameter], lr=3e-4)
        config = TrainConfig(scheduler_restarts=2, scheduler_eta_min=1e-6)
        scheduler = SampleCosineRestartScheduler(optimizer, config, planned_samples=1_000)
        self.assertEqual(scheduler.cycles, 3)
        self.assertEqual(scheduler.cycle, 334)
        scheduler.step(333)
        self.assertLess(optimizer.param_groups[0]["lr"], 2e-6)
        scheduler.step(334)
        self.assertAlmostEqual(optimizer.param_groups[0]["lr"], 3e-4, places=8)
        scheduler.step(668)
        self.assertAlmostEqual(optimizer.param_groups[0]["lr"], 3e-4, places=8)
        scheduler.step(1_000)
        self.assertAlmostEqual(optimizer.param_groups[0]["lr"], 1e-6, places=10)

    def test_profiler_rejects_silently_truncated_batch(self) -> None:
        loader = LoaderConfig(embedding_dim=8, max_chunks=2)
        source = make_dummy_batch(4, loader)
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("research.news_reaction_model.v2.profile_sizes.make_dummy_batch", return_value=source):
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
        with patch("research.news_reaction_model.v2.profile_sizes.ClickHouseNewsReactionDataset") as dataset_type:
            dataset_type.return_value.iter_batches.return_value = (batch for batch in (first, second))
            sample = load_real_sample(loader, "2019-01-01", "2027-01-01", 7)
        self.assertEqual(sample.sample_count, 7)
        self.assertEqual(tuple(sample.x["embeddings"].shape), (7, 2, 8))
        self.assertEqual(len(sample.identity["canonical_news_id"]), 7)
        dataset_type.return_value.stop.assert_called_once()

    def test_compile_falls_back_to_eager_when_cuda_has_no_triton(self) -> None:
        model = NewsReactionModelV2(ModelConfig(embedding_dim=8, d_model=16, hidden_dim=16, layers=1))
        with patch("research.news_reaction_model.v2.train.torch.cuda.is_available", return_value=True):
            with patch("research.news_reaction_model.v2.train.importlib.util.find_spec", return_value=None):
                with patch("research.news_reaction_model.v2.train.torch.compile") as compile_model:
                    selected = maybe_compile(model, True)
        self.assertIs(selected, model)
        compile_model.assert_not_called()

    def test_checkpoint_resume_accepts_legacy_windows_path_and_writes_safe_config(self) -> None:
        model = NewsReactionModelV2(ModelConfig(embedding_dim=8, d_model=16, hidden_dim=16, layers=1))
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
