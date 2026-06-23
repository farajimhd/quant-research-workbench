from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import torch

from research.temporal_event_model.v2.config import ModelConfig
from research.temporal_event_model.v2.losses import masked_return_loss, return_metrics
from research.temporal_event_model.v2.model import MarketTemporalReturnPredictor


def main() -> int:
    torch.manual_seed(17)
    config = ModelConfig(embedding_dim=32, temporal_d_model=64, temporal_layers=1, temporal_heads=4, dropout=0.0)
    model = MarketTemporalReturnPredictor(context_chunks=8, horizons=(8, 16), config=config)
    context = torch.randn(4, 8, 32)
    target = torch.randn(4, 2) * 0.1
    valid = torch.ones(4, 2, dtype=torch.bool)
    output = model(context)
    loss = masked_return_loss(output.return_prediction_norm, target, valid)
    loss.backward()
    metrics = return_metrics(
        output.return_prediction_norm.detach(),
        target,
        valid,
        return_bps_scale=100.0,
        horizon_names=("h8", "h16"),
        prefix="smoke",
    )
    assert output.return_prediction_norm.shape == (4, 2)
    assert torch.isfinite(loss)
    assert "smoke/mae_bps" in metrics
    print(f"smoke_ok loss={float(loss.item()):.6f} mae_bps={metrics['smoke/mae_bps']:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
