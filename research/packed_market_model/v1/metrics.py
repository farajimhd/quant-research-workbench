from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Mapping

import torch

from research.packed_market_model.v1.data import PackedTorchBlock
from research.packed_market_model.v1.model import PackedModelOutput


@dataclass(slots=True)
class MetricWindow:
    max_batches: int = 16
    rows: deque[dict[str, float]] = field(default_factory=deque)

    def add(self, metrics: Mapping[str, float]) -> None:
        self.rows.append({str(k): float(v) for k, v in metrics.items()})
        while len(self.rows) > int(self.max_batches):
            self.rows.popleft()

    def mean(self, prefix: str = "") -> dict[str, float]:
        if not self.rows:
            return {}
        sums: dict[str, float] = defaultdict(float)
        counts: dict[str, int] = defaultdict(int)
        for row in self.rows:
            for key, value in row.items():
                sums[key] += float(value)
                counts[key] += 1
        return {f"{prefix}{key}": sums[key] / max(counts[key], 1) for key in sums}


@torch.no_grad()
def fast_block_metrics(batch: PackedTorchBlock, output: PackedModelOutput, *, prefix: str = "train") -> dict[str, float]:
    metrics = {
        f"{prefix}/origin_count": float(batch.origin_count),
        f"{prefix}/event_count": float(batch.event_count),
        f"{prefix}/labels_requested": float(len(output.label_predictions)),
        f"{prefix}/labels_present": float(len(batch.y)),
    }
    for name, target in batch.y.items():
        mask = batch.masks.get(name)
        if mask is None:
            mask = torch.isfinite(target)
        metrics[f"{prefix}/label/{name}/valid_fraction"] = float(mask.float().mean().detach().cpu()) if mask.numel() else 0.0
    return metrics


def wandb_metric_key(key: str) -> str:
    return key
