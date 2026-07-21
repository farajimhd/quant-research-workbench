from __future__ import annotations

from pathlib import Path
from typing import Any

import torch

from research.news_reaction_model.v2 import HORIZONS
from research.news_reaction_model.v2.config import LoaderConfig, ModelConfig
from research.news_reaction_model.v2.data import rows_to_batch
from research.news_reaction_model.v2.model import NewsReactionModelV2


def load_model(checkpoint_path: str | Path, *, device: str | torch.device = "cpu") -> NewsReactionModelV2:
    with torch.serialization.safe_globals([type(Path())]):
        state = torch.load(checkpoint_path, map_location=device, weights_only=True)
    raw_config = state.get("config", {}).get("model", {})
    config = ModelConfig(**raw_config) if raw_config else ModelConfig()
    model = NewsReactionModelV2(config).to(device)
    model.load_state_dict(state["model"])
    model.eval()
    return model


@torch.inference_mode()
def forecast_rows(
    model: NewsReactionModelV2,
    rows: list[dict[str, Any]],
    *,
    loader_config: LoaderConfig | None = None,
    device: str | torch.device | None = None,
) -> list[dict[str, Any]]:
    """Forecast exact embedded article rows using only publication-time inputs."""
    config = loader_config or LoaderConfig(
        embedding_dim=model.config.embedding_dim,
        max_chunks=model.config.max_chunks,
        horizons=model.config.horizons,
    )
    target_device = torch.device(device) if device is not None else next(model.parameters()).device
    batch = rows_to_batch(rows, config).to(target_device)
    output = model(batch.x)
    returns = output.return_forecasts.float().cpu()
    results: list[dict[str, Any]] = []
    for index in range(batch.sample_count):
        forecasts = {}
        for horizon_index, horizon in enumerate(HORIZONS):
            forecasts[horizon] = {
                "abnormal_target_return": float(returns[index, horizon_index, 0]),
                "abnormal_high_return": float(returns[index, horizon_index, 1]),
                "abnormal_low_return": float(returns[index, horizon_index, 2]),
            }
        results.append({
            "canonical_news_id": batch.identity["canonical_news_id"][index],
            "ticker": batch.identity["ticker"][index],
            "published_at_utc": batch.identity["published_at_utc"][index],
            "forecasts": forecasts,
        })
    return results
