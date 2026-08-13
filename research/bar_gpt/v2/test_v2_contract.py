from __future__ import annotations

import unittest

import torch

from research.bar_gpt.v2 import assert_checkpoint_version
from research.bar_gpt.v2.config import TrainConfig
from research.bar_gpt.v2.metrics import multiclass_scores
from research.bar_gpt.v2.targets import (
    AUTOREGRESSIVE_RETURN_CLASS_THRESHOLDS_PERCENT,
    PHYSICAL_RETURN_CLASS_THRESHOLDS_PERCENT,
    RETURN_CLASS_COUNT,
    return_class_labels,
    transformed_return_to_percent,
)


def _encode_percent(values: list[float]) -> torch.Tensor:
    percent = torch.tensor(values, dtype=torch.float64)
    return torch.asinh(torch.log1p(percent / 100.0) * 100.0)


class V2LearningContractTest(unittest.TestCase):
    def test_checkpoint_contract_fails_closed_across_versions(self) -> None:
        assert_checkpoint_version({"model_family": "bar_gpt", "model_version": "v2"})
        with self.assertRaisesRegex(RuntimeError, "checkpoint version mismatch"):
            assert_checkpoint_version({"model_family": "bar_gpt", "model_version": "v1"})
        with self.assertRaisesRegex(RuntimeError, "checkpoint version mismatch"):
            assert_checkpoint_version({})

    def test_threshold_tables_are_complete_and_authoritative(self) -> None:
        self.assertEqual(
            PHYSICAL_RETURN_CLASS_THRESHOLDS_PERCENT,
            {
                5_000_000: (0.05, 0.20),
                30_000_000: (0.08, 0.30),
                60_000_000: (0.10, 0.40),
                300_000_000: (0.20, 0.75),
                900_000_000: (0.30, 1.25),
                3_600_000_000: (0.50, 2.00),
            },
        )
        self.assertEqual(
            AUTOREGRESSIVE_RETURN_CLASS_THRESHOLDS_PERCENT,
            {
                "1s": (0.03, 0.12),
                "5s": (0.05, 0.20),
                "10s": (0.06, 0.25),
                "30s": (0.08, 0.30),
                "1m": (0.10, 0.40),
                "5m": (0.20, 0.75),
                "30m": (0.40, 1.50),
                "1h": (0.50, 2.00),
            },
        )

    def test_exact_inverse_and_inclusive_class_boundaries(self) -> None:
        percentages = [-0.21, -0.20, -0.05, 0.0, 0.05, 0.20, 0.21]
        transformed = _encode_percent(percentages)
        torch.testing.assert_close(
            transformed_return_to_percent(transformed),
            torch.tensor(percentages, dtype=torch.float64),
            atol=1e-12,
            rtol=1e-12,
        )
        labels = return_class_labels(
            transformed, neutral_percent=0.05, strong_percent=0.20
        )
        torch.testing.assert_close(labels, torch.tensor([0, 1, 2, 2, 2, 3, 4]))

    def test_multiclass_metrics_expose_collapse(self) -> None:
        perfect = torch.eye(RETURN_CLASS_COUNT, dtype=torch.float64) * 5
        self.assertEqual(multiclass_scores(perfect), (1.0, 1.0, 1.0, 1.0, 0.0))
        collapsed = torch.zeros_like(perfect)
        collapsed[:, 2] = 5
        accuracy, balanced, _f1, mcc, distance = multiclass_scores(collapsed)
        self.assertAlmostEqual(accuracy, 0.2)
        self.assertAlmostEqual(balanced, 0.2)
        self.assertEqual(mcc, 0.0)
        self.assertGreater(distance, 0.0)

    def test_train_config_has_no_v1_loss_coefficients(self) -> None:
        config = TrainConfig()
        for removed in (
            "autoregressive_weight",
            "horizon_weight",
            "availability_weight",
            "condition_positive_weight",
            "direction_weight",
            "direction_neutral_bps",
            "latent_prediction_weight",
        ):
            self.assertFalse(hasattr(config, removed), removed)


if __name__ == "__main__":
    unittest.main()
