from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import torch

from research.news_reaction_model.v18.data import EpisodeBatch
from research.news_reaction_model.v20.config import (
    ExperimentConfig,
    ModelConfig,
    TrainConfig,
)
from research.news_reaction_model.v20.losses import (
    bucketize_torch,
    compute_loss,
    signed_opportunity_torch,
)
from research.news_reaction_model.v20.metrics import DistributionAccumulator
from research.news_reaction_model.v20.model import (
    NewsReactionModelV20,
    ReturnDistributionOutput,
)
from research.news_reaction_model.v20.profile_sizes import (
    append_jsonl,
    atomic_write_json,
    build_summary,
    read_durable_results,
    select_recommendations,
)
from research.news_reaction_model.v20.targets import (
    FLAT_BUCKET_INDEX,
    RETURN_BUCKET_COUNT,
    TrainingStatistics,
    bucket_directions_numpy,
    bucketize_numpy,
    signed_opportunity_numpy,
)
from research.news_reaction_model.v20.train import learning_rate_for_progress


def statistics() -> TrainingStatistics:
    centers = (
        -120.0,
        -75.0,
        -35.0,
        -15.0,
        -7.5,
        -3.5,
        -1.5,
        -0.75,
        -0.25,
        0.0,
        0.25,
        0.75,
        1.5,
        3.5,
        7.5,
        15.0,
        35.0,
        75.0,
        120.0,
    )
    counts = tuple(10 for _ in range(RETURN_BUCKET_COUNT))
    return TrainingStatistics(
        bucket_counts=counts,
        bucket_weights=tuple(1.0 for _ in counts),
        bucket_centers=centers,
        direction_counts=(100, 100, 100),
        signed_return_median=0.0,
        signed_return_scale=5.0,
        direction_prior=(1 / 3, 1 / 3, 1 / 3),
        direction_prior_log_loss=float(np.log(3)),
        bucket_prior_log_loss=float(np.log(RETURN_BUCKET_COUNT)),
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
            [0.2, -0.1, 0.1],
            [0.1, -0.2, -0.1],
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


class V20Tests(unittest.TestCase):
    def test_signed_opportunity_reproduces_direction(self) -> None:
        direction = np.asarray([0, 1, 2, 1, 2], dtype=np.int64)
        regression = np.asarray(
            [
                [1.0, -1.0, 0.0],
                [0.2, -3.0, -1.0],
                [4.0, -0.1, 2.0],
                [150.0, -2.0, 50.0],
                [1.0, -125.0, -20.0],
            ],
            dtype=np.float32,
        )
        signed = signed_opportunity_numpy(direction, regression)
        self.assertTrue(np.allclose(signed, [0.0, 0.2, -0.1, 150.0, -125.0]))
        buckets = bucketize_numpy(signed)
        np.testing.assert_array_equal(bucket_directions_numpy(buckets), direction)
        self.assertEqual(int(buckets[0]), FLAT_BUCKET_INDEX)

    def test_torch_and_numpy_bucketization_match(self) -> None:
        values = np.asarray(
            [-150, -100, -50, -20, -10, -5, -2, -1, -0.5, -0.1, 0, 0.1, 0.5, 1, 5, 100, 150],
            dtype=np.float32,
        )
        expected = bucketize_numpy(values)
        actual = bucketize_torch(torch.from_numpy(values)).numpy()
        np.testing.assert_array_equal(actual, expected)

    def test_model_outputs_one_coherent_distribution(self) -> None:
        source = batch()
        model = NewsReactionModelV20(model_config(), statistics())
        output = model(source.x)
        self.assertEqual(output.return_logits.shape, (source.sample_count, RETURN_BUCKET_COUNT))
        self.assertEqual(output.direction_probabilities.shape, (source.sample_count, 3))
        self.assertTrue(
            torch.allclose(
                output.return_probabilities.sum(-1),
                torch.ones(source.sample_count),
                atol=1e-6,
            )
        )
        self.assertTrue(
            torch.allclose(
                output.direction_probabilities.sum(-1),
                torch.ones(source.sample_count),
                atol=1e-6,
            )
        )
        self.assertTrue(bool(torch.all(output.expected_up_return > 0)))
        self.assertTrue(bool(torch.all(output.expected_down_return < 0)))

    def test_unsupported_training_buckets_receive_zero_probability(self) -> None:
        source = batch()
        base = statistics()
        counts = list(base.bucket_counts)
        counts[0] = 0
        stats = replace(base, bucket_counts=tuple(counts))
        model = NewsReactionModelV20(model_config(), stats)
        output = model(source.x)
        self.assertTrue(torch.equal(output.return_probabilities[:, 0], torch.zeros(source.sample_count)))

    def test_empty_prior_context_is_finite_and_independent_of_dummy(self) -> None:
        source = batch(empty_context=True)
        model = NewsReactionModelV20(model_config(), statistics()).eval()
        first = model(source.x).return_logits
        source.x["prior_openai_embeddings"].normal_(mean=1000, std=100)
        source.x["prior_context_features"].normal_(mean=-1000, std=100)
        second = model(source.x).return_logits
        self.assertTrue(torch.isfinite(first).all())
        self.assertTrue(torch.allclose(first, second, atol=1e-5))

    def test_loss_is_finite_and_backpropagates(self) -> None:
        source = batch()
        model = NewsReactionModelV20(model_config(), statistics())
        result = compute_loss(model(source.x), source, statistics())
        self.assertTrue(torch.isfinite(result.loss))
        result.loss.backward()
        self.assertTrue(any(parameter.grad is not None for parameter in model.parameters()))

    def test_accumulator_reports_direction_and_return_metrics(self) -> None:
        source = batch()
        model = NewsReactionModelV20(model_config(), statistics())
        accumulator = DistributionAccumulator(statistics())
        accumulator.add(model(source.x), source)
        metrics = accumulator.compute("test")
        self.assertEqual(metrics["test/samples"], float(source.sample_count))
        self.assertIn("test/direction/macro_f1", metrics)
        self.assertIn("test/signed_return/mae_pct", metrics)
        self.assertIn("test/return_distribution/log_loss", metrics)
        self.assertIn("test/direction/ece", metrics)

    def test_distribution_output_has_no_independent_regression_head(self) -> None:
        source = batch(3)
        model = NewsReactionModelV20(model_config(), statistics())
        output = model(source.x)
        self.assertIsInstance(output, ReturnDistributionOutput)
        self.assertFalse(hasattr(output, "direction_logits"))
        self.assertFalse(hasattr(output, "regression"))

    def test_scheduler_warms_up_restarts_and_decays(self) -> None:
        config = TrainConfig(
            warmup_epochs=2,
            scheduler_cycle_epochs=10,
            learning_rate=3e-4,
            scheduler_cycle_decay=0.9,
        )
        warm = learning_rate_for_progress(
            config, epoch=0, batch_index=0, batches=10
        )
        first_peak = learning_rate_for_progress(
            config, epoch=2, batch_index=0, batches=10
        )
        second_peak = learning_rate_for_progress(
            config, epoch=12, batch_index=0, batches=10
        )
        self.assertLess(warm, first_peak)
        self.assertAlmostEqual(first_peak, config.learning_rate)
        self.assertLess(second_peak, first_peak)

    def test_profiler_persists_rows_and_summary_atomically(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            results_path = root / "profile_results.jsonl"
            row = {
                "status": "completed",
                "d_model": 768,
                "current_layers": 4,
                "experts": 6,
                "batch_size": 2048,
                "samples_per_second": 123.0,
                "peak_gpu_gib": 42.0,
            }
            append_jsonl(results_path, row)
            self.assertEqual(read_durable_results(results_path), [row])
            summary = build_summary(
                [row],
                ExperimentConfig(),
                expected_configurations=1,
                maximum_gpu_gib=90.0,
                run_dir=root,
            )
            summary_path = root / "profile_summary.json"
            atomic_write_json(summary_path, summary)
            persisted = __import__("json").loads(
                summary_path.read_text(encoding="utf-8")
            )
            self.assertTrue(persisted["complete"])
            self.assertEqual(
                persisted["recommended_fixed_architecture"]["batch_size"],
                2048,
            )

    def test_profiler_keeps_fixed_architecture_recommendation_separate(self) -> None:
        rows = [
            {
                "status": "completed",
                "d_model": 512,
                "current_layers": 4,
                "experts": 4,
                "batch_size": 2048,
                "samples_per_second": 500.0,
                "peak_gpu_gib": 30.0,
            },
            {
                "status": "completed",
                "d_model": 768,
                "current_layers": 4,
                "experts": 6,
                "batch_size": 1024,
                "samples_per_second": 250.0,
                "peak_gpu_gib": 40.0,
            },
            {
                "status": "completed",
                "d_model": 768,
                "current_layers": 4,
                "experts": 6,
                "batch_size": 2048,
                "samples_per_second": 300.0,
                "peak_gpu_gib": 60.0,
            },
        ]
        fixed, fastest = select_recommendations(
            rows,
            ExperimentConfig(),
            maximum_gpu_gib=90.0,
        )
        self.assertEqual(fixed["batch_size"], 2048)
        self.assertEqual(fastest["d_model"], 512)


if __name__ == "__main__":
    unittest.main()
