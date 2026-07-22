from __future__ import annotations

import datetime as dt
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import torch

from research.news_reaction_model.v5 import HORIZONS, MODEL_VERSION
from research.news_reaction_model.v4.model import NewsReactionModelV4
from research.news_reaction_model.v5.config import ExperimentConfig, FeatureConfig, LoaderConfig, ModelConfig, TrainConfig
from research.news_reaction_model.v5.data import make_dummy_batch, prepared_batch_sql, rows_to_batch
from research.news_reaction_model.v5.evaluate import PositionLedger, evaluation_batch_sql, rows_to_evaluation_batch, simulate_exits
from research.news_reaction_model.v5.inference import forecast_rows, trade_plans
from research.news_reaction_model.v5.losses import compute_loss
from research.news_reaction_model.v5.model import NewsReactionModelV5, NewsReactionRangeOutput
from research.news_reaction_model.v5.prepare_data import create_table_sql, source_rows_sql
from research.news_reaction_model.v5.profile_sizes import load_real_sample, main as profile_main
from research.news_reaction_model.v5.ranges import RANGE_SPECS, TARGET_NAMES, range_targets
from research.news_reaction_model.v5.train import SampleCosineRestartScheduler, checkpoint_payload, maybe_compile, restore, validate_config
from research.news_reaction_model.v5.text_features import TfidfLsaBundle, _l2_normalize_and_pad, article_model_text


class NewsReactionModelV5Tests(unittest.TestCase):
    def test_version_split_scheduler_and_wandb_comparison_contract(self) -> None:
        loader, train = LoaderConfig(), TrainConfig()
        self.assertEqual(MODEL_VERSION, "v5")
        self.assertEqual((loader.train_start, loader.train_end_exclusive), ("2019-01-01", "2026-01-01"))
        self.assertEqual((loader.validation_start, loader.validation_end_exclusive), ("2026-01-01", "2027-01-01"))
        self.assertEqual((train.epochs, train.scheduler_restarts), (15, 3))
        self.assertEqual(train.wandb_project, "news-reaction-model-v3")

    def test_source_query_reuses_v4_population_and_labels_without_recomputation(self) -> None:
        config = LoaderConfig()
        sql = source_rows_sql(config, dt.date(2026, 1, 1), dt.date(2026, 2, 1))
        self.assertIn(config.source_dataset_table, sql)
        self.assertIn(config.source_dataset_version, sql)
        self.assertIn("p.horizon_codes, p.return_targets", sql)
        self.assertIn("n.title", sql)
        self.assertIn("n.provider_tags", sql)
        self.assertNotIn(config.reaction_table, sql)
        self.assertNotIn("qwen", sql.lower())

    def test_prepared_contract_derives_classes_in_python(self) -> None:
        config = LoaderConfig(dataset_version="test-version")
        ddl = create_table_sql(config)
        self.assertIn("return_targets Array(Array(Float32))", ddl)
        self.assertIn("representation_sha256 FixedString(64)", ddl)
        self.assertNotIn("class_targets", ddl)
        sql = prepared_batch_sql(config, dt.date(2026, 1, 1), dt.date(2026, 2, 1), "2026-01-01", "AAPL", "id", 256)
        self.assertIn("dataset_version = 'test-version'", sql)
        self.assertIn("(published_at_utc, ticker, canonical_news_id) >", sql)

    def test_v5_keeps_v4_architecture_and_head_shapes_exactly(self) -> None:
        config = ModelConfig(embedding_dim=8, d_model=16, hidden_dim=16, layers=1)
        v4 = NewsReactionModelV4(config)
        v5 = NewsReactionModelV5(config)
        self.assertEqual(
            {name: tuple(value.shape) for name, value in v4.state_dict().items()},
            {name: tuple(value.shape) for name, value in v5.state_dict().items()},
        )
        self.assertEqual(sum(p.numel() for p in v4.parameters()), sum(p.numel() for p in v5.parameters()))

    def test_text_contract_is_publication_time_only_and_normalization_is_bounded(self) -> None:
        text = article_model_text({
            "ticker": "AAPL", "published_at_utc": "2025-01-01", "title": "Raises guidance 10%",
            "provider_tags": "earnings,guidance", "body_text": "Body", "future_price": 999,
        }, max_chars=80)
        self.assertIn("Raises guidance", text)
        self.assertNotIn("future_price", text)
        values = _l2_normalize_and_pad(torch.tensor([[3.0, 4.0]]).numpy(), 4)
        self.assertEqual(tuple(values.shape), (1, 4))
        self.assertAlmostEqual(float((values[0] ** 2).sum()), 1.0, places=6)

    @unittest.skipUnless(importlib.util.find_spec("sklearn"), "scikit-learn is not installed in this environment")
    def test_tfidf_bundle_produces_two_v4_compatible_channels(self) -> None:
        rows = [
            {"ticker": "AAPL", "title": "Apple raises guidance", "body_text": "Revenue beats estimates"},
            {"ticker": "MSFT", "title": "Microsoft cuts outlook", "body_text": "Margins decline"},
            {"ticker": "NVDA", "title": "Nvidia launches chip", "body_text": "Demand remains strong"},
            {"ticker": "TSLA", "title": "Tesla recalls cars", "body_text": "Software defect found"},
        ]
        features = FeatureConfig(
            word_max_features=64, char_max_features=64, output_dim=8, min_df=1,
            max_df=1.0, svd_iterations=1,
        )
        bundle = TfidfLsaBundle.fit(rows, features)
        transformed = bundle.transform(rows[:2])
        self.assertEqual(tuple(transformed.shape), (2, 2, 8))
        self.assertTrue(torch.isfinite(torch.from_numpy(transformed)).all())

    def test_range_boundaries_include_requested_tails_and_vary_by_horizon(self) -> None:
        one_minute = RANGE_SPECS["1m"]
        for boundary in (-50.0, -20.0, 20.0, 50.0, 100.0):
            self.assertIn(boundary, one_minute.upper_bounds_pct)
        self.assertGreater(one_minute.classes, RANGE_SPECS["3h"].classes)
        self.assertEqual(one_minute.class_for_return(1.20), one_minute.classes - 1)
        self.assertEqual(one_minute.class_for_return(-1.01), -1)

    def test_batch_model_range_loss_and_forecast(self) -> None:
        loader = LoaderConfig(embedding_dim=8, max_chunks=2)
        rows = [{
            "source_id": "news-1", "ticker": "AAPL", "published_at_utc": "2026-07-14 13:41:00",
            "chunks": [[0, [0.1] * 8], [1, [0.2] * 8]], "publication_session": "regular",
            "horizon_codes": ["5m", "1m"],
            "return_targets": [[0.02, 0.03, -0.01], [-0.01, 0.01, -0.02]],
        }]
        batch = rows_to_batch(rows, loader)
        self.assertEqual(int(batch.label_mask.sum()), 2)
        targets = range_targets(batch.return_targets, batch.label_mask)
        self.assertEqual(tuple(targets["1m"].shape), (1, 3))
        model = NewsReactionModelV5(ModelConfig(embedding_dim=8, d_model=16, hidden_dim=16, layers=1))
        output = model(batch.x)
        for horizon in HORIZONS:
            for target in TARGET_NAMES:
                self.assertEqual(tuple(output.logits[horizon][target].shape), (1, RANGE_SPECS[horizon].classes))
        result = compute_loss(output, batch)
        self.assertTrue(torch.isfinite(result.loss)); result.loss.backward()
        inference_rows = [{key: value for key, value in rows[0].items() if key not in {"horizon_codes", "return_targets"}}]
        forecast = forecast_rows(model, inference_rows, loader_config=loader)[0]["forecasts"]["1m"]
        self.assertIn(forecast["position"], (-1, 0, 1))
        self.assertEqual(set(forecast["ranges"]), set(TARGET_NAMES))

    def test_dominant_excursion_uses_conservative_target_ties_abstain(self) -> None:
        model = NewsReactionModelV5(ModelConfig(embedding_dim=8, d_model=16, hidden_dim=16, layers=1))
        batch = make_dummy_batch(3, LoaderConfig(embedding_dim=8))
        output = model(batch.x)
        spec = RANGE_SPECS["1m"]
        for target in TARGET_NAMES:
            output.logits["1m"][target].fill_(-20)
        positive = spec.class_for_return(0.03)  # +2..5%, conservative target +2%
        negative_small = spec.class_for_return(-0.005)  # -1..-0.5%, conservative downside 0.5%
        negative_equal = spec.class_for_return(-0.03)  # -5..-2%, conservative downside 2%
        output.logits["1m"]["high"][:, positive] = 20
        output.logits["1m"]["low"][:, negative_small] = 20
        output.logits["1m"]["low"][1, negative_equal] = 40
        plans = trade_plans(output)["1m"]
        self.assertEqual(int(plans["side"][0]), 1)
        self.assertAlmostEqual(float(plans["target_pct"][0]), 2.0)
        self.assertEqual(int(plans["side"][1]), 0)

    def test_position_ledger_tracks_target_and_fallback_exits(self) -> None:
        ledger = PositionLedger()
        ledger.add(side=torch.tensor([1, -1, 0]).numpy(), pnl=torch.tensor([2.0, 3.0, 0.0]).numpy(), touched=torch.tensor([True, False, False]).numpy())
        summary = ledger.summary()
        self.assertEqual((summary["long"], summary["short"], summary["flat"]), (1, 1, 1))
        self.assertEqual((summary["target_touches"], summary["ending_fallbacks"]), (1, 1))
        self.assertAlmostEqual(summary["one_share_pnl"], 5.0)

    def test_target_touch_and_ending_fallback_pnl_contract(self) -> None:
        touched, exit_return, pnl = simulate_exits(
            side=torch.tensor([1, -1, 1]).numpy(),
            target_pct=torch.tensor([2.0, -3.0, 2.0]).numpy(),
            actual_returns=torch.tensor([
                [0.01, 0.025, -0.005],   # long target touched
                [-0.01, 0.005, -0.035],  # short target touched
                [-0.015, 0.01, -0.02],   # long target missed; ending fallback
            ]).numpy(),
            anchors=torch.tensor([100.0, 50.0, 200.0]).numpy(),
        )
        self.assertEqual(touched.tolist(), [True, True, False])
        self.assertAlmostEqual(exit_return[0], 0.02)
        self.assertAlmostEqual(exit_return[1], -0.03)
        self.assertAlmostEqual(exit_return[2], -0.015, places=6)
        self.assertAlmostEqual(pnl[0], 2.0)
        self.assertAlmostEqual(pnl[1], 1.5)
        self.assertAlmostEqual(pnl[2], -3.0, places=5)

    def test_evaluation_query_loads_anchor_without_future_order_labels(self) -> None:
        loader = LoaderConfig(embedding_dim=8)
        sql = evaluation_batch_sql(loader, dt.date(2026, 1, 1), dt.date(2026, 2, 1), "1970-01-01", "", "", 128)
        self.assertIn("anchor_price", sql)
        self.assertNotIn("first_touch", sql)
        rows = [{
            "source_id": "news-eval", "ticker": "AAPL", "published_at_utc": "2026-07-14 13:41:00",
            "chunks": [[0, [0.1] * 8], [1, [0.2] * 8]], "publication_session": "regular", "horizon_codes": ["1m"],
            "return_targets": [[0.01, 0.02, -0.01]], "anchor_values": [["1m", 100.0]],
        }]
        evaluation = rows_to_evaluation_batch(rows, loader)
        self.assertAlmostEqual(evaluation.anchors[0, HORIZONS.index("1m")], 100.0)

    def test_scheduler_has_exactly_three_restarts(self) -> None:
        parameter = torch.nn.Parameter(torch.tensor(0.0)); optimizer = torch.optim.SGD([parameter], lr=3e-4)
        config = TrainConfig(epochs=15, scheduler_restarts=3, scheduler_eta_min=1e-6)
        scheduler = SampleCosineRestartScheduler(optimizer, config, planned_samples=15_000)
        self.assertEqual(scheduler.cycles, 4)
        for boundary in (3_750, 7_500, 11_250):
            scheduler.step(boundary); self.assertAlmostEqual(optimizer.param_groups[0]["lr"], 3e-4, places=8)
        validate_config(ExperimentConfig(train=config))

    def test_profiler_collects_exact_sample_and_rejects_truncation(self) -> None:
        loader = LoaderConfig(embedding_dim=8)
        first, second = make_dummy_batch(4, loader), make_dummy_batch(5, loader)
        with patch("research.news_reaction_model.v5.profile_sizes.ClickHouseNewsReactionDataset") as dataset_type:
            dataset_type.return_value.iter_batches.return_value = (batch for batch in (first, second))
            sample = load_real_sample(loader, "2019-01-01", "2027-01-01", 7)
        self.assertEqual(sample.sample_count, 7)
        with tempfile.TemporaryDirectory() as temp_dir, patch("research.news_reaction_model.v5.profile_sizes.make_dummy_batch", return_value=first):
            with self.assertRaisesRegex(RuntimeError, "will not silently report a truncated batch"):
                profile_main(["--model-sizes", "16", "--batch-sizes", "8", "--layers", "1", "--warmup-steps", "0", "--profile-steps", "1", "--output-root", temp_dir])

    def test_checkpoint_and_compile_fallback(self) -> None:
        model = NewsReactionModelV5(ModelConfig(embedding_dim=8, d_model=16, hidden_dim=16, layers=1))
        optimizer = torch.optim.AdamW(model.parameters()); scaler = torch.amp.GradScaler("cpu", enabled=False)
        config = ExperimentConfig(loader=LoaderConfig(embedding_dim=8), model=model.config, train=TrainConfig(output_root=Path("runtime/test")))
        payload = checkpoint_payload(model, optimizer, None, scaler, config, 10, 2)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "checkpoint.pt"; torch.save(payload, path)
            self.assertEqual(restore(str(path), model, optimizer, None, scaler, torch.device("cpu")), (10, 2))
        with patch("research.news_reaction_model.v5.train.torch.cuda.is_available", return_value=True), patch("research.news_reaction_model.v5.train.importlib.util.find_spec", return_value=None), patch("research.news_reaction_model.v5.train.torch.compile") as compile_model:
            self.assertIs(maybe_compile(model, True), model); compile_model.assert_not_called()


if __name__ == "__main__":
    unittest.main()
