from __future__ import annotations

import unittest

import numpy as np
import torch

from research.news_reaction_model.v18.data import EpisodeBatch
from research.news_reaction_model.v21.config import ModelConfig, TrainConfig
from research.news_reaction_model.v21.losses import (
    compute_loss,
    magnitude_bucketize_torch,
    signed_bucketize_torch,
)
from research.news_reaction_model.v21.metrics import HierarchicalAccumulator
from research.news_reaction_model.v21.model import (
    HierarchicalReturnOutput,
    NewsReactionModelV21,
)
from research.news_reaction_model.v21.targets import (
    FLAT_BUCKET_INDEX,
    MAGNITUDE_BUCKET_COUNT,
    TrainingStatistics,
    fit_training_statistics,
)
from research.news_reaction_model.v21.train import (
    learning_rate_for_progress,
    should_early_stop,
)


def statistics() -> TrainingStatistics:
    centers = (0.25, 0.75, 1.5, 3.5, 7.5, 15.0, 35.0, 75.0, 125.0)
    counts = tuple(10 for _ in range(MAGNITUDE_BUCKET_COUNT))
    prior = tuple(1.0 / MAGNITUDE_BUCKET_COUNT for _ in counts)
    return TrainingStatistics(
        direction_counts=(100, 100, 100),
        direction_weights=(1.0, 1.0, 1.0),
        direction_prior=(1 / 3, 1 / 3, 1 / 3),
        direction_prior_log_loss=float(np.log(3)),
        magnitude_counts=(counts, counts),
        magnitude_weights=(
            tuple(1.0 for _ in counts),
            tuple(1.0 for _ in counts),
        ),
        magnitude_centers=(centers, centers),
        magnitude_prior=(prior, prior),
        magnitude_prior_log_loss=(
            float(np.log(MAGNITUDE_BUCKET_COUNT)),
            float(np.log(MAGNITUDE_BUCKET_COUNT)),
        ),
        magnitude_median=(7.5, 7.5),
        magnitude_scale=(15.0, 15.0),
        joint_prior_log_loss=float(np.log(3) + (2 / 3) * np.log(9)),
        signed_return_median=0.0,
        training_rows=300,
    )


def model_config() -> ModelConfig:
    return ModelConfig(
        openai_embedding_dim=24,
        stock_state_dim=8,
        time_feature_dim=6,
        current_episode_feature_dim=5,
        context_size=3,
        context_feature_dim=7,
        d_model=48,
        feedforward_dim=96,
        current_layers=2,
        prior_layers=1,
        cross_attention_layers=1,
        attention_heads=4,
        expert_count=3,
        expert_top_k=2,
        expert_hidden_dim=64,
        dropout=0.0,
    )


def batch(batch_size: int = 6, *, empty_context: bool = False) -> EpisodeBatch:
    config = model_config()
    mask = torch.zeros(batch_size, config.context_size, dtype=torch.bool)
    if not empty_context:
        mask[:, :2] = True
    direction = torch.tensor([0, 1, 2, 1, 2, 0], dtype=torch.long)[:batch_size]
    regression = torch.tensor(
        [
            [0.2, -0.1, 0.0],
            [3.2, -0.4, 2.0],
            [0.5, -5.5, -3.0],
            [12.0, -0.1, 5.0],
            [0.1, -25.0, -10.0],
            [0.3, -0.3, 0.0],
        ],
        dtype=torch.float32,
    )[:batch_size]
    return EpisodeBatch(
        x={
            "openai_embedding": torch.randn(batch_size, config.openai_embedding_dim),
            "stock_state": torch.randn(batch_size, config.stock_state_dim),
            "time_features": torch.randn(batch_size, config.time_feature_dim),
            "current_episode_features": torch.randn(
                batch_size, config.current_episode_feature_dim
            ),
            "channel_mask": torch.ones(batch_size, 4, dtype=torch.bool),
            "prior_openai_embeddings": torch.randn(
                batch_size, config.context_size, config.openai_embedding_dim
            ),
            "prior_context_features": torch.randn(
                batch_size, config.context_size, config.context_feature_dim
            ),
            "prior_context_mask": mask,
            "anchor_log": torch.ones(batch_size, 1),
            "price_regime": torch.zeros(batch_size, dtype=torch.long),
            "publication_session": torch.zeros(batch_size, dtype=torch.long),
            "context_fraction": mask.float().mean(dim=1, keepdim=True),
        },
        direction=direction,
        path=torch.zeros(batch_size, dtype=torch.long),
        flow=torch.zeros(batch_size, dtype=torch.long),
        regression_targets=regression,
        target_mask=torch.ones(batch_size, dtype=torch.bool),
        identity={},
        sample_count=batch_size,
    )


class V21Tests(unittest.TestCase):
    def test_magnitude_and_signed_bucket_mapping(self) -> None:
        values = torch.tensor([0.25, 0.75, 1.5, 3.0, 7.0, 15.0, 35.0, 75.0, 150.0])
        magnitude = magnitude_bucketize_torch(values)
        torch.testing.assert_close(
            magnitude, torch.arange(MAGNITUDE_BUCKET_COUNT)
        )
        signed = torch.tensor([-150.0, -3.0, 0.0, 3.0, 150.0])
        buckets = signed_bucketize_torch(signed)
        self.assertEqual(int(buckets[2]), FLAT_BUCKET_INDEX)
        self.assertEqual(int(buckets[0]), 0)
        self.assertEqual(int(buckets[-1]), 2 * MAGNITUDE_BUCKET_COUNT)

    def test_hierarchy_is_normalized_and_directionally_coherent(self) -> None:
        source = batch()
        model = NewsReactionModelV21(model_config(), statistics())
        output = model(source.x)
        self.assertIsInstance(output, HierarchicalReturnOutput)
        torch.testing.assert_close(
            output.joint_return_probabilities.sum(-1),
            torch.ones(source.sample_count),
        )
        torch.testing.assert_close(
            output.joint_return_probabilities[:, :MAGNITUDE_BUCKET_COUNT].sum(-1),
            output.direction_probabilities[:, 2],
        )
        torch.testing.assert_close(
            output.joint_return_probabilities[:, FLAT_BUCKET_INDEX],
            output.direction_probabilities[:, 0],
        )
        torch.testing.assert_close(
            output.joint_return_probabilities[
                :, FLAT_BUCKET_INDEX + 1 :
            ].sum(-1),
            output.direction_probabilities[:, 1],
        )
        self.assertTrue(bool(torch.all(output.expected_up_return > 0)))
        self.assertTrue(bool(torch.all(output.expected_down_return < 0)))

    def test_loss_is_finite_and_backpropagates(self) -> None:
        source = batch()
        model = NewsReactionModelV21(model_config(), statistics())
        result = compute_loss(model(source.x), source, statistics())
        self.assertTrue(torch.isfinite(result.loss))
        result.loss.backward()
        self.assertTrue(any(parameter.grad is not None for parameter in model.parameters()))

    def test_neutral_only_batch_has_finite_loss(self) -> None:
        source = batch(2)
        source.direction.zero_()
        source.regression_targets.zero_()
        model = NewsReactionModelV21(model_config(), statistics())
        result = compute_loss(model(source.x), source, statistics())
        self.assertTrue(torch.isfinite(result.loss))

    def test_metrics_report_hierarchical_skills(self) -> None:
        source = batch()
        model = NewsReactionModelV21(model_config(), statistics())
        accumulator = HierarchicalAccumulator(statistics())
        accumulator.add(model(source.x), source)
        metrics = accumulator.compute("test")
        self.assertIn("test/direction/log_loss_skill", metrics)
        self.assertIn("test/magnitude/upside/mae_skill", metrics)
        self.assertIn("test/magnitude/downside/log_loss_skill", metrics)
        self.assertIn("test/joint_distribution/log_loss_skill", metrics)

    def test_empty_context_is_independent_of_dummy_values(self) -> None:
        source = batch(empty_context=True)
        model = NewsReactionModelV21(model_config(), statistics()).eval()
        first = model(source.x).direction_logits
        source.x["prior_openai_embeddings"].normal_(1000, 100)
        source.x["prior_context_features"].normal_(-1000, 100)
        second = model(source.x).direction_logits
        torch.testing.assert_close(first, second, atol=1e-5, rtol=1e-5)

    def test_training_statistics_preserve_direction_contract(self) -> None:
        class Dataset:
            indices = np.arange(6, dtype=np.int64)
            arrays = {
                "direction": np.asarray([0, 1, 2, 1, 2, 0], dtype=np.int64),
                "regression_targets": np.asarray(
                    [
                        [0.1, -0.1, 0.0],
                        [3.0, -0.2, 1.0],
                        [0.2, -5.0, -2.0],
                        [12.0, -1.0, 5.0],
                        [1.0, -25.0, -10.0],
                        [0.2, -0.2, 0.0],
                    ],
                    dtype=np.float32,
                ),
            }

        fitted = fit_training_statistics(
            Dataset(),
            beta=0.9,
            minimum_class_weight=0.2,
            maximum_class_weight=5.0,
        )
        self.assertEqual(fitted.direction_counts, (2, 2, 2))
        self.assertEqual(sum(fitted.magnitude_counts[0]), 2)
        self.assertEqual(sum(fitted.magnitude_counts[1]), 2)

    def test_scheduler_and_early_stopping_contract(self) -> None:
        config = TrainConfig(
            warmup_epochs=2,
            scheduler_cycle_epochs=10,
            early_stopping_patience=6,
            early_stopping_min_epochs=8,
        )
        warm = learning_rate_for_progress(
            config, epoch=0, batch_index=0, batches=10
        )
        peak = learning_rate_for_progress(
            config, epoch=2, batch_index=0, batches=10
        )
        self.assertLess(warm, peak)
        self.assertFalse(
            should_early_stop(
                config, completed_epochs=7, epochs_without_improvement=6
            )
        )
        self.assertTrue(
            should_early_stop(
                config, completed_epochs=8, epochs_without_improvement=6
            )
        )


if __name__ == "__main__":
    unittest.main()
