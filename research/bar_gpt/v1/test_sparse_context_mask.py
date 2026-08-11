from __future__ import annotations

import unittest

import torch

from research.bar_gpt.v1.config import BarGPTConfig
from research.bar_gpt.v1.features import MODEL_FEATURE_NAMES
from research.bar_gpt.v1.model import BarGPTV1


class SparseContextMaskTest(unittest.TestCase):
    def test_masked_missing_history_cannot_affect_real_origins(self) -> None:
        torch.manual_seed(3)
        config = BarGPTConfig(
            feature_dim=len(MODEL_FEATURE_NAMES), d_model=64, n_layers=2,
            n_heads=4, n_kv_heads=2, horizon_rank=16, dropout=0.0,
        )
        model = BarGPTV1(config).eval()
        value = torch.randn(1, 8, len(MODEL_FEATURE_NAMES))
        mask = torch.tensor([[False, False, False, True, True, True, True, True]])
        kwargs = {
            "timeframe_us": {"1s": 1_000_000},
            "pathway_ids": {"1s": 0},
            "base_view": "1s",
            "origin_indices": torch.tensor([[3, 4, 5, 6, 7]]),
            "view_masks": {"1s": mask},
            "horizon_ids": torch.tensor([0]),
        }
        first = model({"1s": value}, **kwargs)
        changed = value.clone()
        changed[:, :3] = torch.randn_like(changed[:, :3]) * 1_000
        second = model({"1s": changed}, **kwargs)

        self.assertTrue(bool(torch.isfinite(first.embeddings).all()))
        self.assertTrue(bool(torch.all(first.scale_embeddings["1s"][:, :3] == 0)))
        torch.testing.assert_close(first.embeddings, second.embeddings)


if __name__ == "__main__":
    unittest.main()
