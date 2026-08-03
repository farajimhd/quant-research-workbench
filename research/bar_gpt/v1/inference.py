from __future__ import annotations

import hashlib
import json
from dataclasses import fields
from pathlib import Path
from typing import Any

import torch
from torch import nn

from research.bar_gpt.v1.config import BarGPTConfig, DataConfig
from research.bar_gpt.v1.data import PATHWAY_ID_BY_NAME, TIMEFRAME_US_BY_NAME, BarGPTBatch
from research.bar_gpt.v1.model import BarGPTV1


_DATA_TUPLE_FIELDS = {"intraday_timeframes_us", "calendar_timeframes", "horizons_us", "tickers", "validation_slices"}
_MODEL_TUPLE_FIELDS = {"quantiles"}


def _dataclass_values(cls: type[Any], values: dict[str, Any], tuple_fields: set[str]) -> dict[str, Any]:
    allowed = {field.name for field in fields(cls)}
    return {
        key: tuple(value) if key in tuple_fields else value
        for key, value in values.items()
        if key in allowed
    }


def checkpoint_contract_hash(payload: dict[str, Any]) -> str:
    contract = {
        "model": payload.get("config", {}).get("model", {}),
        "data": payload.get("config", {}).get("data", {}),
        "plan_hash": payload.get("plan_hash", ""),
    }
    return hashlib.sha256(json.dumps(contract, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def load_pretrained(
    checkpoint: str | Path,
    *,
    device: torch.device | str = "cpu",
) -> tuple[BarGPTV1, DataConfig, dict[str, Any]]:
    payload = torch.load(Path(checkpoint), map_location=device, weights_only=False)
    config = payload.get("config", {})
    if "model" not in config or "data" not in config:
        raise RuntimeError("checkpoint does not contain the BarGPT model/data contract")
    model_config = BarGPTConfig(**_dataclass_values(BarGPTConfig, config["model"], _MODEL_TUPLE_FIELDS))
    data_config = DataConfig(**_dataclass_values(DataConfig, config["data"], _DATA_TUPLE_FIELDS))
    model_config.validate()
    data_config.validate()
    model = BarGPTV1(model_config).to(device)
    model.load_state_dict(payload["model"])
    model.eval()
    payload["contract_hash"] = checkpoint_contract_hash(payload)
    return model, data_config, payload


class BarGPTEncoder(nn.Module):
    """Stable causal embedding contract for downstream point-in-time modalities."""

    def __init__(self, model: BarGPTV1) -> None:
        super().__init__()
        self.model = model

    def forward(self, batch: BarGPTBatch) -> tuple[torch.Tensor, torch.Tensor]:
        embeddings, _scale_states = self.model.embed(
            batch.views,
            timeframe_us=TIMEFRAME_US_BY_NAME,
            pathway_ids=PATHWAY_ID_BY_NAME,
            base_view="1s",
            origin_indices=batch.origin_indices,
            asof_indices=batch.asof_indices,
        )
        return embeddings, batch.origin_mask
