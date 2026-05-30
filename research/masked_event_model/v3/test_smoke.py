from __future__ import annotations

import unittest

import torch

from research.masked_event_model.v3.config import LossConfig, MaskConfig, ModelConfig
from research.masked_event_model.v3.losses import masked_autoencoder_loss
from research.masked_event_model.v3.masking import build_structured_masks
from research.masked_event_model.v3.model import MaskedEventAutoencoder


class MaskedEventModelSmokeTests(unittest.TestCase):
    def test_forward_and_masked_loss_shapes(self) -> None:
        model = MaskedEventAutoencoder(
            quote_feature_count=18,
            trade_feature_count=20,
            summary_feature_count=25,
            context_chunks=4,
            max_quote_events=8,
            max_trade_events=10,
            max_total_events=12,
            horizon_count=3,
            target_bit_count=13,
            config=ModelConfig(
                d_model=64,
                embedding_dim=32,
                n_heads=4,
                quote_event_layers=1,
                trade_event_layers=1,
                temporal_layers=1,
                decoder_layers=1,
                ffn_mult=2,
                dropout=0.0,
            ),
        )
        model.eval()
        batch = {
            "quote_values": torch.randn(2, 4, 8, 18),
            "trade_values": torch.randn(2, 4, 10, 20),
            "event_kinds": torch.randint(0, 3, (2, 4, 12)),
            "event_indices": torch.randint(0, 8, (2, 4, 12)),
            "chunk_summary": torch.randn(2, 4, 25),
            "targets": torch.rand(2, 3, 1, 13),
        }
        masks = build_structured_masks(
            quote_values=batch["quote_values"],
            trade_values=batch["trade_values"],
            chunk_summary=batch["chunk_summary"],
            event_kinds=batch["event_kinds"],
            config=MaskConfig(),
        )
        output = model(
            batch["quote_values"],
            batch["trade_values"],
            batch["event_kinds"],
            batch["event_indices"],
            batch["chunk_summary"],
            masks,
        )
        loss, metrics = masked_autoencoder_loss(output, batch, masks, LossConfig())

        self.assertEqual(tuple(output.forecast_logits.shape), (2, 3, 1, 13))
        self.assertTrue(torch.isfinite(loss))
        self.assertIn("pretrain/loss_total", metrics)
        self.assertGreater(metrics["mask/ratio_actual"], 0.5)

        encoded_with_no_mask = model.encode(
            batch["quote_values"],
            batch["trade_values"],
            batch["event_kinds"],
            batch["event_indices"],
            batch["chunk_summary"],
        )
        self.assertEqual(tuple(encoded_with_no_mask.shape), (2, 32))
        self.assertTrue(torch.isfinite(encoded_with_no_mask).all())


if __name__ == "__main__":
    unittest.main()
