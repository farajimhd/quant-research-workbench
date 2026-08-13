from __future__ import annotations

import unittest

import torch

from research.bar_gpt.v2 import LEARNING_CONTRACT, assert_checkpoint_version
from research.bar_gpt.v2.analyze_return_classes import _rows
from research.bar_gpt.v2.config import TrainConfig
from research.bar_gpt.v2.metrics import multiclass_scores
from research.bar_gpt.v2.targets import (
    AUTOREGRESSIVE_RETURN_CLASS_THRESHOLDS_PERCENT,
    PHYSICAL_RETURN_CLASS_THRESHOLDS_PERCENT,
    RETURN_CLASS_COUNT,
    RETURN_CLASS_NAMES,
    RETURN_CLASS_NEUTRAL_BPS,
    RETURN_CLASS_NEUTRAL_PERCENT,
    RETURN_TARGET_COUNT,
    return_class_labels,
    transformed_return_to_percent,
)


def _encode_percent(values: list[float]) -> torch.Tensor:
    percent = torch.tensor(values, dtype=torch.float64)
    return torch.asinh(torch.log1p(percent / 100.0) * 100.0)


class V2LearningContractTest(unittest.TestCase):
    def test_checkpoint_contract_fails_closed_across_versions(self) -> None:
        assert_checkpoint_version({
            "model_family": "bar_gpt",
            "model_version": "v2",
            "learning_contract": LEARNING_CONTRACT,
        })
        with self.assertRaisesRegex(RuntimeError, "checkpoint version mismatch"):
            assert_checkpoint_version({
                "model_family": "bar_gpt",
                "model_version": "v1",
                "learning_contract": LEARNING_CONTRACT,
            })
        with self.assertRaisesRegex(RuntimeError, "checkpoint version mismatch"):
            assert_checkpoint_version({"model_family": "bar_gpt", "model_version": "v2"})
        with self.assertRaisesRegex(RuntimeError, "checkpoint version mismatch"):
            assert_checkpoint_version({})

    def test_threshold_tables_are_complete_and_authoritative(self) -> None:
        self.assertEqual(RETURN_CLASS_NAMES, ("negative", "neutral", "positive"))
        self.assertEqual(RETURN_CLASS_NEUTRAL_BPS, 1.0)
        self.assertEqual(RETURN_CLASS_NEUTRAL_PERCENT, 0.01)
        self.assertEqual(set(PHYSICAL_RETURN_CLASS_THRESHOLDS_PERCENT.values()), {0.01})
        self.assertEqual(set(AUTOREGRESSIVE_RETURN_CLASS_THRESHOLDS_PERCENT.values()), {0.01})

    def test_exact_inverse_and_inclusive_class_boundaries(self) -> None:
        percentages = [-0.011, -0.01, 0.0, 0.01, 0.011]
        transformed = _encode_percent(percentages)
        torch.testing.assert_close(
            transformed_return_to_percent(transformed),
            torch.tensor(percentages, dtype=torch.float64),
            atol=1e-12,
            rtol=1e-12,
        )
        labels = return_class_labels(transformed, neutral_percent=0.01)
        torch.testing.assert_close(labels, torch.tensor([0, 1, 1, 1, 2]))

    def test_multiclass_metrics_expose_collapse(self) -> None:
        perfect = torch.eye(RETURN_CLASS_COUNT, dtype=torch.float64) * 5
        self.assertEqual(multiclass_scores(perfect), (1.0, 1.0, 1.0, 1.0, 0.0))
        collapsed = torch.zeros_like(perfect)
        collapsed[:, 1] = 5
        accuracy, balanced, _f1, mcc, distance = multiclass_scores(collapsed)
        self.assertAlmostEqual(accuracy, 1.0 / 3.0)
        self.assertAlmostEqual(balanced, 1.0 / 3.0)
        self.assertEqual(mcc, 0.0)
        self.assertGreater(distance, 0.0)

    def test_analyzer_emits_only_the_fixed_three_class_contract(self) -> None:
        counts = [[10, 20, 30] for _ in range(RETURN_TARGET_COUNT)]
        rows = _rows(
            {
                "physical": {"train": [counts]},
                "autoregressive": {"train": {"1s": counts}},
            },
            (5_000_000,),
        )
        self.assertEqual(len(rows), RETURN_TARGET_COUNT * RETURN_CLASS_COUNT * 2)
        self.assertEqual({row["class"] for row in rows}, set(RETURN_CLASS_NAMES))
        self.assertEqual({row["neutral_percent"] for row in rows}, {0.01})
        self.assertTrue(all("strong_percent" not in row for row in rows))

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
