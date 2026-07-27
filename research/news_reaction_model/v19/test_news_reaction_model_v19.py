from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
import torch

from research.news_reaction_model.v18.data import EpisodeBatch
from research.news_reaction_model.v19.config import ModelConfig
from research.news_reaction_model.v19.losses import compute_loss
from research.news_reaction_model.v19.model import NewsReactionModelV19
from research.news_reaction_model.v19.profile_sizes import attention_heads_for_width
from research.news_reaction_model.v19.targets import (
    TrainingStatistics,
    effective_number_weights,
    price_regime_numpy,
    regression_components_numpy,
)
from research.news_reaction_model.v19.train import configure_phase


def statistics() -> TrainingStatistics:
    scales = tuple(
        tuple((1.0, 1.0, 1.0) for _ in range(4))
        for _ in range(5)
    )
    return TrainingStatistics(
        direction_counts=(100, 200, 300),
        path_counts=(100, 200, 30, 40, 150, 80),
        flow_counts=(200, 350, 50),
        direction_weights=(1.0, 1.0, 1.0),
        path_weights=(1.0, 1.0, 2.0, 2.0, 1.0, 1.0),
        flow_weights=(1.0, 1.0, 2.0),
        regression_scales=scales,
        regression_training_median=(1.0, -1.0, 0.0),
        training_rows=600,
    )


def model_config() -> ModelConfig:
    return ModelConfig(
        openai_embedding_dim=16,
        stock_state_dim=8,
        time_feature_dim=11,
        current_episode_feature_dim=15,
        context_size=8,
        context_feature_dim=17,
        d_model=24,
        feedforward_dim=48,
        transformer_layers=2,
        tower_hidden_dim=24,
        attention_heads=6,
        dropout=0.0,
    )


def batch(batch_size: int = 7) -> EpisodeBatch:
    context_mask = torch.zeros(batch_size, 8, dtype=torch.bool)
    context_mask[1:, :3] = True
    targets = torch.tensor(
        [[2.0, -1.5, 0.5], [4.0, -3.0, -1.0], [1.0, -2.0, 0.0]],
        dtype=torch.float32,
    ).repeat((batch_size + 2) // 3, 1)[:batch_size]
    return EpisodeBatch(
        x={
            "openai_embedding": torch.randn(batch_size, 16),
            "stock_state": torch.randn(batch_size, 8),
            "time_features": torch.randn(batch_size, 11),
            "current_episode_features": torch.randn(batch_size, 15),
            "channel_mask": torch.ones(batch_size, 4, dtype=torch.bool),
            "prior_openai_embeddings": torch.randn(batch_size, 8, 16),
            "prior_context_features": torch.randn(batch_size, 8, 17),
            "prior_context_mask": context_mask,
            "anchor_log": torch.rand(batch_size, 1),
            "price_regime": torch.arange(batch_size) % 5,
            "publication_session": torch.arange(batch_size) % 4,
            "context_fraction": context_mask.sum(1, keepdim=True).float() / 8.0,
        },
        direction=torch.arange(batch_size) % 3,
        path=torch.arange(batch_size) % 6,
        flow=torch.arange(batch_size) % 3,
        regression_targets=targets,
        target_mask=torch.ones(batch_size, dtype=torch.bool),
        identity={},
        sample_count=batch_size,
    )


class TargetTest(unittest.TestCase):
    def test_profiler_selects_compatible_attention_heads(self) -> None:
        self.assertEqual(attention_heads_for_width(256), 8)
        self.assertEqual(attention_heads_for_width(384), 6)
        self.assertEqual(attention_heads_for_width(510), 6)

    def test_price_regime_boundaries(self) -> None:
        actual = price_regime_numpy(
            np.asarray([0.50, 1.0, 4.99, 5.0, 9.99, 10.0, 19.99, 20.0])
        )
        np.testing.assert_array_equal(actual, [0, 1, 1, 2, 2, 3, 3, 4])

    def test_regression_components_are_coherent(self) -> None:
        actual = regression_components_numpy(
            np.asarray([[4.0, -3.0, 1.0], [-1.0, -5.0, -2.0]])
        )
        np.testing.assert_allclose(actual, [[1.0, 3.0, 4.0], [-2.0, 1.0, 3.0]])

    def test_effective_weights_upweight_rare_classes_with_bounds(self) -> None:
        weights = effective_number_weights(
            np.asarray([10_000, 1_000, 50]),
            beta=0.9999,
            minimum=0.25,
            maximum=4.0,
        )
        self.assertGreater(weights[2], weights[1])
        self.assertGreater(weights[1], weights[0])
        self.assertGreaterEqual(float(weights.min()), 0.25)
        self.assertLessEqual(float(weights.max()), 4.1)


class ModelTest(unittest.TestCase):
    def test_forward_shapes_and_return_coherence(self) -> None:
        source = batch()
        model = NewsReactionModelV19(model_config(), statistics())
        output = model(source.x)
        self.assertEqual(output.direction_logits.shape, (7, 3))
        self.assertEqual(output.path_logits.shape, (7, 6))
        self.assertEqual(output.flow_logits.shape, (7, 3))
        self.assertEqual(output.regression.shape, (7, 3))
        self.assertTrue(torch.all(output.regression[:, 1] <= output.regression[:, 2]))
        self.assertTrue(torch.all(output.regression[:, 2] <= output.regression[:, 0]))
        self.assertTrue(torch.isfinite(output.article_embedding).all())

    def test_balanced_multitask_loss_is_finite(self) -> None:
        source = batch()
        model = NewsReactionModelV19(model_config(), statistics())
        result = compute_loss(model(source.x), source, statistics())
        self.assertTrue(torch.isfinite(result.loss))
        self.assertEqual(set(result.components), {"direction", "path", "flow", "regression"})
        result.loss.backward()
        self.assertIsNotNone(model.current_token.grad)

    def test_path_condition_does_not_update_direction_or_regression_heads(self) -> None:
        source = batch()
        model = NewsReactionModelV19(model_config(), statistics())
        configure_phase(model, "path")
        result = compute_loss(
            model(source.x),
            source,
            statistics(),
            tasks={"path"},
        )
        result.loss.backward()
        self.assertIsNone(model.direction_head.weight.grad)
        self.assertIsNone(model.regression_head.weight.grad)
        self.assertIsNotNone(model.path_head.weight.grad)

    def test_specialization_freezes_shared_encoder(self) -> None:
        model = NewsReactionModelV19(model_config(), statistics())
        configure_phase(model, "specialize")
        self.assertFalse(model.current_token.requires_grad)
        self.assertTrue(model.direction_head.weight.requires_grad)
        self.assertTrue(model.flow_head.weight.requires_grad)
        self.assertTrue(model.regression_head.weight.requires_grad)
        self.assertFalse(model.path_head.weight.requires_grad)

    def test_three_phase_training_assembles_checkpoint(self) -> None:
        source = batch(batch_size=8)

        class DummyDataset:
            def __init__(self, *_args, **_kwargs) -> None:
                self.indices = np.arange(8, dtype=np.int64)

            def iter_batches(self, *, epoch: int = 0):
                del epoch
                yield source

            def stop(self) -> None:
                return None

        validation = {
            "val/joint_score": 0.5,
            "val/direction/macro_f1": 0.5,
            "val/path/macro_f1": 0.4,
            "val/flow/macro_f1": 0.5,
            "val/regression_mean_mae_skill": 0.1,
            "val/path/class/spike_fade/recall": 0.2,
            "val/path/class/flush_recovery/recall": 0.2,
            "val/flow/class/supply_dominant/recall": 0.3,
        }

        with TemporaryDirectory() as temp:
            output = Path(temp) / "runs"
            with (
                patch(
                    "research.news_reaction_model.v19.train.PreparedEpisodeDataset",
                    DummyDataset,
                ),
                patch(
                    "research.news_reaction_model.v19.train.fit_statistics",
                    return_value=statistics(),
                ),
                patch(
                    "research.news_reaction_model.v19.train.validate",
                    return_value=validation,
                ),
                patch(
                    "research.news_reaction_model.v19.train.evaluate_checkpoint",
                    return_value={"status": "completed"},
                ),
                patch(
                    "research.news_reaction_model.v19.train.ModelConfig",
                    return_value=model_config(),
                ),
            ):
                from research.news_reaction_model.v19.train import main

                status = main(
                    [
                        "--output-root",
                        str(output),
                        "--run-name",
                        "phase-smoke",
                        "--joint-epochs",
                        "1",
                        "--specialization-epochs",
                        "1",
                        "--path-epochs",
                        "1",
                        "--batch-size",
                        "8",
                        "--d-model",
                        "24",
                        "--attention-heads",
                        "6",
                        "--transformer-layers",
                        "1",
                        "--feedforward-dim",
                        "48",
                        "--wandb-mode",
                        "disabled",
                        "--no-evaluate",
                    ]
                )

            self.assertEqual(status, 0)
            checkpoint = output / "phase-smoke" / "checkpoints" / "best_val.pt"
            self.assertTrue(checkpoint.exists())
            payload = torch.load(checkpoint, map_location="cpu", weights_only=False)
            self.assertEqual(payload["model_version"], "v19")
            self.assertEqual(payload["phase"], "path")


if __name__ == "__main__":
    unittest.main()
