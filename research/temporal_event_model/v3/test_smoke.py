from __future__ import annotations

import tempfile
import sys
from pathlib import Path

import torch

REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.model_artifacts import write_model_artifacts
from research.temporal_event_model.v3.config import LoaderConfig, ModelConfig
from research.temporal_event_model.v3.data import (
    loader_config_from_v3,
    make_dummy_temporal_batch,
    validation_loader_config_from_v3,
)
from research.temporal_event_model.v3.losses import compute_loss
from research.temporal_event_model.v3.metrics import fast_batch_metrics, prediction_metrics
from research.temporal_event_model.v3.model import TemporalEventModelV3, build_model_mermaid
from research.temporal_event_model.v3.train import _input_contract, _output_contract


def main() -> int:
    config = ModelConfig(
        d_model=32,
        event_stream_length=128,
        event_layers=1,
        event_heads=4,
        fusion_layers=1,
        fusion_heads=4,
        dropout=0.0,
        xbrl_max_items=32,
        corporate_action_max_items=8,
    )
    batch = make_dummy_temporal_batch(model_config=config, batch_size=2, device="cpu")
    model = TemporalEventModelV3(config)
    model.eval()
    output = model(batch.x)
    tokens, encode_timings = model.encode_modality_tokens_with_timings(batch.x)
    cached_output, cached_timings = model.predict_from_modality_tokens_with_timings(tokens)
    result = compute_loss(output, batch)
    assert torch.isfinite(result.loss), result.metrics
    assert output.future_bar_values["trade"].shape == (2, config.intraday_horizons, 6)
    assert output.future_bar_values["quote_bid"].shape == (2, config.intraday_horizons, 9)
    assert output.future_bar_values["quote_ask"].shape == (2, config.intraday_horizons, 9)
    assert output.modality_tokens.shape == (2, 10, config.d_model)
    assert set(tokens) == {
        "events",
        "ticker_intraday_bars",
        "ticker_daily_bars",
        "global_daily_bars",
        "ticker_news",
        "market_news",
        "sec_filings",
        "xbrl",
        "corporate_actions",
        "scanner_context",
    }
    assert encode_timings and cached_timings
    assert cached_output.modality_tokens.shape == output.modality_tokens.shape
    assert torch.allclose(cached_output.fused_tokens, output.fused_tokens, atol=1e-5, rtol=1e-5)
    metrics = {}
    metrics.update(fast_batch_metrics(batch, output))
    metrics.update(prediction_metrics(batch, output))
    assert metrics
    loader_config = LoaderConfig(batch_size=2, event_stream_length=128)
    assert loader_config_from_v3(loader_config).event_columns == config.event_feature_names
    assert validation_loader_config_from_v3(loader_config).split == loader_config.val_split
    with tempfile.TemporaryDirectory(prefix="temporal_v3_smoke_") as tmp:
        artifact_dir = Path(tmp) / "artifacts" / "model"
        write_model_artifacts(
            model=model,
            artifact_dir=artifact_dir,
            model_config=config,
            input_contract=_input_contract(config),
            output_contract=_output_contract(config),
            architecture_mermaid=build_model_mermaid(),
            summary_notes="Smoke-test artifacts.",
            dummy_input_factory=lambda: ((), make_dummy_temporal_batch(model_config=config, batch_size=2, device="cpu").x),
        )
        assert (artifact_dir / "model_details.json").exists()
        assert (artifact_dir / "model_summary.txt").exists()
        assert (artifact_dir / "model_architecture.mmd").exists()
        try:
            import torchinfo  # noqa: F401
        except ModuleNotFoundError:
            pass
        else:
            assert (artifact_dir / "model_summary_torchinfo.txt").exists()
            assert not (artifact_dir / "model_summary_torchinfo_error.txt").exists()
    print("temporal_event_model v3 smoke passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
