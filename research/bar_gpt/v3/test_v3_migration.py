from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import torch

from research.bar_gpt.v2 import LEARNING_CONTRACT as V2_LEARNING_CONTRACT
from research.bar_gpt.v2.config import BarGPTConfig as V2Config
from research.bar_gpt.v2.model import BarGPTV2
from research.bar_gpt.v3.config import BarGPTConfig
from research.bar_gpt.v3.data import build_target_clock_features
from research.bar_gpt.v3.migration import load_v2_weights, snapshot_v2_checkpoint
from research.bar_gpt.v3.model import BarGPTV3
from research.bar_gpt.v3.targets import next_event_gap_class_labels


class V3MigrationTest(unittest.TestCase):
    @staticmethod
    def config() -> BarGPTConfig:
        return BarGPTConfig(
            d_model=32,
            n_layers=1,
            n_heads=4,
            n_kv_heads=2,
            horizon_rank=8,
        )

    def test_v2_migration_copies_compatible_weights_and_closes_source(self) -> None:
        torch.manual_seed(7)
        source_model = BarGPTV2(V2Config(**{
            "d_model": 32,
            "n_layers": 1,
            "n_heads": 4,
            "n_kv_heads": 2,
            "horizon_rank": 8,
        }))
        target_model = BarGPTV3(self.config())
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "checkpoint_v2.pt"
            torch.save(
                {
                    "model_family": "bar_gpt",
                    "model_version": "v2",
                    "learning_contract": V2_LEARNING_CONTRACT,
                    "samples_seen": 123,
                    "model": source_model.state_dict(),
                },
                checkpoint,
            )
            snapshot = Path(directory) / "checkpoint_v2_snapshot.pt"
            snapshot_v2_checkpoint(checkpoint, snapshot)
            report = load_v2_weights(target_model, snapshot)
            source_model.eval()
            target_model.eval()
            self.assertEqual(report["source_samples_seen"], 123)
            self.assertGreater(report["copied_parameter_fraction"], 0.95)
            torch.testing.assert_close(
                target_model.blocks[0].attention.q_proj.weight,
                source_model.blocks[0].attention.q_proj.weight,
            )
            features = torch.randn(1, 4, self.config().feature_dim)
            kwargs = {
                "timeframe_us": {"1s": 1_000_000},
                "pathway_ids": {"1s": 0},
                "base_view": "1s",
                "origin_indices": torch.tensor([[2]]),
                "horizon_ids": torch.tensor([0]),
            }
            source_output = source_model({"1s": features}, **kwargs)
            target_output = target_model({"1s": features}, **kwargs)
            torch.testing.assert_close(
                target_output.autoregressive["1s"],
                source_output.autoregressive["1s"],
                atol=1e-6,
                rtol=1e-6,
            )
            torch.testing.assert_close(
                target_output.horizon_quantiles,
                source_output.horizon_quantiles,
                atol=1e-6,
                rtol=1e-6,
            )
            torch.testing.assert_close(
                target_output.horizon_availability_logits,
                source_output.horizon_availability_logits,
                atol=1e-6,
                rtol=1e-6,
            )
            self.assertEqual(
                float(target_model.autoregressive_gap_head.weight.detach().abs().sum()),
                0.0,
            )
            self.assertEqual(
                float(target_model.target_clock_embedding.projection.weight.detach().abs().sum()),
                0.0,
            )
            renamed = checkpoint.with_name("checkpoint_v2_moved.pt")
            checkpoint.rename(renamed)
            self.assertTrue(renamed.is_file())
            self.assertTrue(snapshot.is_file())

    def test_gap_classes_include_cross_session(self) -> None:
        base = 1_767_357_000_000_000  # 2026-01-02 09:30 America/New_York
        starts = torch.tensor(
            [
                base,
                base + 1_000_000,
                base + 3_000_000,
                base + 7_000_000,
                base + 38_000_000,
                base + 100_000_000,
                base + 86_400_000_000,
            ]
        )
        labels = next_event_gap_class_labels(starts, timeframe_us=1_000_000)
        self.assertEqual(labels.tolist(), [0, 1, 2, 4, 4, 5])

    def test_target_clock_features_are_known_and_shape_stable(self) -> None:
        origins = torch.tensor([[1_767_357_000_000_000]])
        features = build_target_clock_features(origins, (5_000_000, 3_600_000_000))
        self.assertEqual(features.shape, (1, 1, 2, 8))
        self.assertTrue(bool(torch.isfinite(features).all()))


if __name__ == "__main__":
    unittest.main()
