from __future__ import annotations

import base64
import dataclasses
import datetime as dt
import struct
import unittest

import numpy as np
import torch

from research.news_reaction_model import v9, v10
from research.news_reaction_model.v9.config import ModelConfig as V9ModelConfig
from research.news_reaction_model.v9.config import TrainConfig as V9TrainConfig
from research.news_reaction_model.v10.config import LoaderConfig, ModelConfig, TrainConfig
from research.news_reaction_model.v10.data import (
    make_dummy_batch,
    prepared_batch_sql,
    prepared_dataset_audit_sql,
    rows_to_batch,
)
from research.news_reaction_model.v10.evaluate import (
    OpportunityLedger,
    evaluation_batch_sql,
    midpoint_proxy_pnl,
)
from research.news_reaction_model.v10.fit_diagnostic import fit_comparison
from research.news_reaction_model.v10.inference import (
    LiveFeatureEncoder,
    opportunity_predictions,
)
from research.news_reaction_model.v10.losses import compute_loss
from research.news_reaction_model.v10.model import (
    NewsReactionModelV10,
    NewsReactionOpportunityOutput,
)
from research.news_reaction_model.v10.memorization_test import (
    deterministic_subset_sql,
    slice_batch,
)
from research.news_reaction_model.v10.opportunity import (
    OPPORTUNITY_CLASSES,
    OPPORTUNITY_CLASS_NAMES,
    OPPORTUNITY_SPECS,
    OpportunityClass,
    opportunity_contract,
    opportunity_targets,
)


class NewsReactionModelV10Tests(unittest.TestCase):
    def test_v10_is_a_class_only_ablation_over_v9(self) -> None:
        loader = LoaderConfig()
        self.assertEqual(v10.MODEL_VERSION, "v10")
        self.assertEqual(v10.HORIZONS, v9.HORIZONS)
        self.assertEqual(loader.dataset_table, "news_reaction_openai_stock_state_dataset_v8")
        self.assertEqual(loader.dataset_version, "news_reaction_openai_stock_state_dataset_v8")

    def test_encoder_and_training_defaults_match_v9(self) -> None:
        v9_model = dataclasses.asdict(V9ModelConfig())
        v10_model = dataclasses.asdict(ModelConfig())
        self.assertEqual(v10_model, v9_model)
        v9_train = dataclasses.asdict(V9TrainConfig())
        v10_train = dataclasses.asdict(TrainConfig())
        for key in (
            "epochs",
            "max_samples",
            "learning_rate",
            "weight_decay",
            "grad_clip_norm",
            "scheduler",
            "scheduler_restarts",
            "scheduler_eta_min",
            "amp",
            "amp_dtype",
            "compile_model",
            "logging_samples",
            "validation_samples",
            "validation_max_batches",
            "checkpoint_latest_samples",
            "checkpoint_archive_samples",
            "evaluate_at_end",
            "wandb_project",
            "wandb_entity",
            "wandb_mode",
            "wandb_init_timeout",
            "seed",
        ):
            self.assertEqual(v10_train[key], v9_train[key], key)
        self.assertNotIn("ordinal_loss_weight", v10_train)

    def test_opportunity_contract_has_exactly_three_classes(self) -> None:
        self.assertEqual(OPPORTUNITY_CLASSES, 3)
        self.assertEqual(
            OPPORTUNITY_CLASS_NAMES,
            (
                "no_meaningful_opportunity",
                "upside_dominant",
                "downside_dominant",
            ),
        )
        self.assertEqual(set(opportunity_contract()["rules"]), set(v10.HORIZONS))

    def test_opportunity_spec_assigns_two_sided_moves_by_larger_excursion(self) -> None:
        spec = OPPORTUNITY_SPECS["1m"]
        self.assertEqual(
            spec.classify(0.0004, -0.0004),
            int(OpportunityClass.NO_MEANINGFUL_OPPORTUNITY),
        )
        self.assertEqual(spec.classify(0.0100, -0.0020), int(OpportunityClass.UPSIDE_DOMINANT))
        self.assertEqual(spec.classify(0.0020, -0.0100), int(OpportunityClass.DOWNSIDE_DOMINANT))
        self.assertEqual(spec.classify(0.0100, -0.0090), int(OpportunityClass.UPSIDE_DOMINANT))
        self.assertEqual(spec.classify(0.0090, -0.0100), int(OpportunityClass.DOWNSIDE_DOMINANT))
        self.assertEqual(
            spec.classify(0.0100, -0.0100),
            int(OpportunityClass.NO_MEANINGFUL_OPPORTUNITY),
        )

    def test_tensor_targets_match_scalar_contract(self) -> None:
        returns = torch.zeros((5, len(v10.HORIZONS), 3), dtype=torch.float32)
        returns[0, :, 1:] = torch.tensor([0.0004, -0.0004])
        returns[1, :, 1:] = torch.tensor([0.0100, -0.0020])
        returns[2, :, 1:] = torch.tensor([0.0020, -0.0100])
        returns[3, :, 1:] = torch.tensor([0.0100, -0.0090])
        returns[4, :, 1:] = torch.tensor([0.0090, -0.0100])
        mask = torch.ones((5, len(v10.HORIZONS)), dtype=torch.bool)
        targets = opportunity_targets(returns, mask)
        self.assertEqual(
            targets["1m"].tolist(),
            [
                int(OpportunityClass.NO_MEANINGFUL_OPPORTUNITY),
                int(OpportunityClass.UPSIDE_DOMINANT),
                int(OpportunityClass.DOWNSIDE_DOMINANT),
                int(OpportunityClass.UPSIDE_DOMINANT),
                int(OpportunityClass.DOWNSIDE_DOMINANT),
            ],
        )

    def test_invalid_labels_are_masked(self) -> None:
        returns = torch.zeros((2, len(v10.HORIZONS), 3), dtype=torch.float32)
        mask = torch.ones((2, len(v10.HORIZONS)), dtype=torch.bool)
        mask[0, 0] = False
        returns[1, 0, 1] = float("nan")
        self.assertEqual(opportunity_targets(returns, mask)["1m"].tolist(), [-1, -1])

    def test_model_has_only_one_opportunity_head_per_horizon(self) -> None:
        model = NewsReactionModelV10(
            ModelConfig(
                openai_embedding_dim=16,
                stock_state_dim=8,
                d_model=16,
                hidden_dim=16,
                layers=1,
            )
        )
        self.assertFalse(hasattr(model, "range_heads"))
        self.assertEqual(set(model.opportunity_heads), set(v10.HORIZONS))
        for head in model.opportunity_heads.values():
            self.assertEqual(head.out_features, 3)

    def test_forward_and_loss_use_one_label_per_horizon(self) -> None:
        loader = LoaderConfig(openai_embedding_dim=16, stock_state_dim=8)
        model = NewsReactionModelV10(
            ModelConfig(
                openai_embedding_dim=16,
                stock_state_dim=8,
                d_model=16,
                hidden_dim=16,
                layers=1,
            )
        )
        batch = make_dummy_batch(4, loader)
        output = model(batch.x)
        self.assertEqual(tuple(output.article_embedding.shape), (4, 16))
        self.assertEqual(set(output.logits), set(v10.HORIZONS))
        self.assertTrue(all(tuple(logits.shape) == (4, 3) for logits in output.logits.values()))
        result = compute_loss(output, batch)
        self.assertTrue(torch.isfinite(result.loss))
        self.assertEqual(result.metrics["train/valid_labels"], 4 * len(v10.HORIZONS))

    def test_inference_opens_only_dominant_direction_positions(self) -> None:
        classes = torch.tensor(
            [
                int(OpportunityClass.NO_MEANINGFUL_OPPORTUNITY),
                int(OpportunityClass.UPSIDE_DOMINANT),
                int(OpportunityClass.DOWNSIDE_DOMINANT),
            ]
        )
        logits = torch.full((3, 3), -5.0)
        logits[torch.arange(3), classes] = 5.0
        output = NewsReactionOpportunityOutput(
            logits={horizon: logits.clone() for horizon in v10.HORIZONS},
            article_embedding=torch.zeros((3, 8)),
            profile={},
        )
        plan = opportunity_predictions(output)["1m"]
        self.assertEqual(plan["position"].tolist(), [0, 1, -1])
        self.assertEqual(plan["class"].tolist(), classes.tolist())

    def test_midpoint_pnl_proxy_is_signed_by_position(self) -> None:
        midpoint, pnl = midpoint_proxy_pnl(
            np.array([1, -1, 0]),
            np.array([0.06, 0.02, 0.10]),
            np.array([-0.02, -0.08, -0.10]),
            np.array([100.0, 50.0, 20.0]),
        )
        np.testing.assert_allclose(midpoint, [0.02, -0.03, 0.0])
        np.testing.assert_allclose(pnl, [2.0, 1.5, 0.0])

    def test_ledger_reports_three_class_quality_and_side_pnl(self) -> None:
        ledger = OpportunityLedger()
        ledger.add(
            predicted_class=np.array([1, 2, 0]),
            actual_class=np.array([1, 2, 0]),
            position=np.array([1, -1, 0]),
            pnl=np.array([2.0, -1.0, 0.0]),
        )
        summary = ledger.summary()
        self.assertEqual(summary["labels"], 3)
        self.assertEqual(summary["active"], 2)
        self.assertEqual(summary["abstained"], 1)
        self.assertEqual(summary["accuracy"], 1.0)
        self.assertEqual(summary["macro_f1"], 1.0)
        self.assertAlmostEqual(summary["one_share_pnl"], 1.0)

    def test_batch_decodes_lossless_openai_transport(self) -> None:
        config = LoaderConfig(openai_embedding_dim=4, stock_state_dim=2)
        values = (0.125, -0.5, 1.25, 3.0)
        row = {
            "source_id": "n1",
            "ticker": "AAPL",
            "published_at_utc": "2026-01-01 12:00:00",
            "openai_embedding_b64": base64.b64encode(struct.pack("<4f", *values)).decode("ascii"),
            "stock_state": [0.1, 0.2],
            "horizon_codes": [],
            "return_targets": [],
        }
        batch = rows_to_batch([row], config)
        self.assertTrue(torch.allclose(batch.x["openai_embedding"][0], torch.tensor(values)))

    def test_live_encoder_retains_exact_v8_input_contract(self) -> None:
        encoder = LiveFeatureEncoder(LoaderConfig(openai_embedding_dim=4, stock_state_dim=2))
        encoded = encoder.encode(
            [{"openai_embedding": [1, 2, 3, 4], "stock_state": [0.1, 0.2]}],
            device=torch.device("cpu"),
        )
        self.assertEqual(set(encoded), {"openai_embedding", "stock_state", "channel_mask"})
        with self.assertRaisesRegex(ValueError, "4-value"):
            encoder.encode(
                [{"openai_embedding": [1, 2], "stock_state": [0.1, 0.2]}],
                device=torch.device("cpu"),
            )

    def test_prepared_and_evaluation_queries_reuse_v8_schema(self) -> None:
        config = LoaderConfig()
        batch_sql = prepared_batch_sql(
            config,
            dt.date(2026, 1, 1),
            dt.date(2026, 2, 1),
            "1970-01-01",
            "",
            "",
            10,
        )
        audit_sql = prepared_dataset_audit_sql(config, "2026-01-01", "2027-01-01")
        evaluation_sql = evaluation_batch_sql(
            config,
            dt.date(2026, 1, 1),
            dt.date(2026, 2, 1),
            "1970-01-01",
            "",
            "",
            10,
        )
        for sql in (batch_sql, evaluation_sql):
            self.assertIn("news_reaction_openai_stock_state_dataset_v8", sql)
            self.assertIn("openai_embedding_b64", sql)
            self.assertIn("stock_state", sql)
            self.assertNotIn("word_ids", sql)
        self.assertIn("length(openai_embedding) != 3072", audit_sql)

    def test_fit_comparison_reports_train_validation_gaps(self) -> None:
        training = {
            "metrics": {
                f"train/{metric}": value
                for metric, value in {
                    "accuracy": 0.8,
                    "balanced_accuracy": 0.7,
                    "macro_f1": 0.6,
                    "log_loss": 0.4,
                    "mean_confidence": 0.9,
                }.items()
            }
        }
        validation = {
            "metrics": {
                f"validation/{metric}": value
                for metric, value in {
                    "accuracy": 0.5,
                    "balanced_accuracy": 0.45,
                    "macro_f1": 0.4,
                    "log_loss": 0.8,
                    "mean_confidence": 0.6,
                }.items()
            }
        }
        comparison = fit_comparison(training, validation)
        self.assertAlmostEqual(comparison["train_minus_validation_accuracy"], 0.3)
        self.assertAlmostEqual(comparison["train_minus_validation_log_loss"], -0.4)

    def test_memorization_subset_is_deterministic_and_slices_all_tensors(self) -> None:
        config = LoaderConfig(openai_embedding_dim=4, stock_state_dim=2)
        sql = deterministic_subset_sql(
            config,
            start="2019-01-01",
            end_exclusive="2026-01-01",
            subset_size=10_000,
            subset_seed=17,
        )
        self.assertIn("cityHash64", sql)
        self.assertIn("toString(17)", sql)
        self.assertIn("LIMIT 10000", sql)
        self.assertIn("news_reaction_openai_stock_state_dataset_v8", sql)

        batch = make_dummy_batch(4, config)
        sliced = slice_batch(batch, torch.tensor([3, 1]))
        self.assertEqual(sliced.sample_count, 2)
        self.assertEqual(
            sliced.identity["canonical_news_id"],
            ["dummy-3", "dummy-1"],
        )
        self.assertEqual(tuple(sliced.x["openai_embedding"].shape), (2, 4))


if __name__ == "__main__":
    unittest.main()
