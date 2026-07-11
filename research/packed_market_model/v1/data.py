from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import torch

from research.mlops.packed_market import PackedMarketBlock
from research.packed_market_model.v1.config import ModelConfig


@dataclass(slots=True)
class PackedTorchBlock:
    x: dict[str, Any]
    y: dict[str, torch.Tensor]
    masks: dict[str, torch.Tensor]
    identity: dict[str, Any]
    profile: dict[str, float]
    origin_count: int
    event_count: int


def block_to_torch(
    block: PackedMarketBlock,
    *,
    model_config: ModelConfig,
    device: torch.device,
    non_blocking: bool = True,
) -> PackedTorchBlock:
    events = torch.as_tensor(block.events, dtype=torch.float32, device=device)
    origin_positions = torch.as_tensor(block.origin_positions, dtype=torch.long, device=device)
    event_timestamps = torch.as_tensor(block.event_timestamp_us, dtype=torch.long, device=device)
    origin_timestamps = torch.as_tensor(block.origin_timestamp_us, dtype=torch.long, device=device)
    labels: dict[str, torch.Tensor] = {}
    masks: dict[str, torch.Tensor] = {}
    selected = set(model_config.label_names)
    for name, value in block.labels.items():
        if selected and name not in selected:
            continue
        labels[name] = torch.as_tensor(value, dtype=torch.float32, device=device)
        mask_value = block.label_masks.get(name)
        if mask_value is None:
            masks[name] = torch.ones_like(labels[name], dtype=torch.bool, device=device)
        else:
            masks[name] = torch.as_tensor(mask_value, dtype=torch.bool, device=device)
    available = block.label_masks.get("available")
    if available is not None:
        masks["available"] = torch.as_tensor(available, dtype=torch.bool, device=device)
    identity = {
        "ticker": np.asarray([block.block_manifest.ticker] * block.origin_count, dtype=object),
        "origin_ordinal": block.origin_ordinals.copy(),
        "origin_timestamp_us": block.origin_timestamp_us.copy(),
        "block_id": block.block_manifest.block_id,
        "month": block.block_manifest.month,
    }
    return PackedTorchBlock(
        x={
            "events": events,
            "origin_positions": origin_positions,
            "event_timestamps_us": event_timestamps,
            "origin_timestamp_us": origin_timestamps,
            "event_feature_names": tuple(model_config.event_feature_names or block.block_manifest.event_feature_names),
        },
        y=labels,
        masks=masks,
        identity=identity,
        profile={},
        origin_count=int(block.origin_count),
        event_count=int(block.event_count),
    )


def make_dummy_packed_block(*, model_config: ModelConfig, device: torch.device | str = "cpu") -> PackedTorchBlock:
    device = torch.device(device)
    t = 1024
    m = 64
    f = int(model_config.event_feature_dim or max(len(model_config.event_feature_names), 8))
    labels = tuple(model_config.label_names or ("future_trade_close", "future_halt_flag"))
    return PackedTorchBlock(
        x={
            "events": torch.randn(t, f, device=device),
            "origin_positions": torch.linspace(16, t - 1, m, dtype=torch.long, device=device),
            "event_timestamps_us": torch.arange(t, dtype=torch.long, device=device),
            "origin_timestamp_us": torch.arange(m, dtype=torch.long, device=device),
            "event_feature_names": tuple(model_config.event_feature_names or tuple(f"feature_{i}" for i in range(f))),
        },
        y={name: torch.randn(m, device=device) for name in labels},
        masks={name: torch.ones(m, dtype=torch.bool, device=device) for name in labels},
        identity={"ticker": np.asarray(["DUMMY"] * m, dtype=object), "origin_ordinal": np.arange(m), "origin_timestamp_us": np.arange(m)},
        profile={},
        origin_count=m,
        event_count=t,
    )


def infer_contract_from_dataset(dataset: Any) -> tuple[tuple[str, ...], tuple[str, ...], int]:
    iterator = dataset.iter_blocks()
    block = next(iterator)
    event_names = tuple(block.block_manifest.event_feature_names)
    labels = tuple(sorted(block.labels.keys()))
    return event_names, labels, int(block.events.shape[-1])
