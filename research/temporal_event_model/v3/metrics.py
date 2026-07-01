from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Mapping

import torch
import torch.nn.functional as F

from research.temporal_event_model.v3.config import BAR_FAMILIES, CORPORATE_ACTION_FLAGS, EXTERNAL_ARRIVAL_FLAGS, INTRADAY_EVENT_FLAGS
from research.temporal_event_model.v3.losses import _origin_prices
from research.temporal_event_model.v3.model import TemporalModelOutput


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
def fast_batch_metrics(batch: Any, output: TemporalModelOutput, *, prefix: str = "train") -> dict[str, float]:
    metrics: dict[str, float] = {
        f"{prefix}/batch_samples": float(batch.sample_count),
    }
    x = batch.x
    if torch.is_tensor(x.get("raw_event_mask")):
        metrics[f"{prefix}/event_window_valid_fraction"] = float(x["raw_event_mask"].float().mean().cpu())
    for group_name, payload in x.get("text_inputs", {}).items():
        mask = payload.get("chunk_mask")
        if torch.is_tensor(mask):
            metrics[f"{prefix}/{group_name}_available_fraction"] = float(mask.reshape(mask.shape[0], -1).any(dim=1).float().mean().cpu())
    xbrl_mask = x.get("xbrl_inputs", {}).get("mask")
    if torch.is_tensor(xbrl_mask):
        metrics[f"{prefix}/xbrl_available_fraction"] = float(xbrl_mask.any(dim=1).float().mean().cpu())
    ca_mask = x.get("corporate_action_inputs", {}).get("mask")
    if torch.is_tensor(ca_mask):
        metrics[f"{prefix}/corporate_action_available_fraction"] = float(ca_mask.any(dim=1).float().mean().cpu())
    for key, payload in x.get("bar_inputs", {}).items():
        mask = payload.get("mask")
        if torch.is_tensor(mask):
            metrics[f"{prefix}/{key}_available_fraction"] = float(mask.reshape(mask.shape[0], -1).any(dim=1).float().mean().cpu())
    labels = batch.y.get("intraday_labels", {})
    available = labels.get("available")
    if torch.is_tensor(available):
        metrics[f"{prefix}/label_available_fraction"] = float(available.float().mean().cpu())
    return metrics


@torch.no_grad()
def prediction_metrics(batch: Any, output: TemporalModelOutput, *, prefix: str = "train") -> dict[str, float]:
    metrics: dict[str, float] = {}
    origin = _origin_prices(batch.x)
    for family in BAR_FAMILIES:
        pred = output.future_bar_values.get(family)
        target = batch.y.get("future_bar_values", {}).get(family)
        mask = batch.y.get("future_bar_masks", {}).get(family)
        if pred is None or not torch.is_tensor(target) or not torch.is_tensor(mask):
            continue
        target = target.to(device=pred.device, dtype=pred.dtype)
        mask = mask.to(device=pred.device, dtype=torch.bool)
        width = min(4, pred.shape[-1], target.shape[-1])
        if width <= 0 or not bool(mask.any()):
            continue
        base_key = "trade" if family == "trade" else "ask" if family == "quote_ask" else "bid"
        base = origin[base_key].to(device=pred.device, dtype=pred.dtype)
        normalized_target = ((target[..., :width] - base[:, None, None]) / base.clamp(min=1e-6)[:, None, None]) * 10_000.0
        error = pred[..., :width] - normalized_target
        expanded = mask.unsqueeze(-1).expand_as(error)
        metrics[f"{prefix}/price_mae_{family}_bps"] = float(error[expanded].abs().mean().cpu())
        metrics[f"{prefix}/price_rmse_{family}_bps"] = float(torch.sqrt((error[expanded] ** 2).mean()).cpu())
        if width >= 2:
            sign_pred = torch.sign(pred[..., 1])
            sign_target = torch.sign(normalized_target[..., 1])
            metrics[f"{prefix}/price_sign_acc_{family}"] = float((sign_pred[mask] == sign_target[mask]).float().mean().cpu())
    for name in (*INTRADAY_EVENT_FLAGS, *EXTERNAL_ARRIVAL_FLAGS):
        pred = output.intraday_logits.get(name)
        target = batch.y.get("intraday_labels", {}).get(name)
        mask = batch.y.get("intraday_labels", {}).get("available")
        if pred is not None and torch.is_tensor(target) and torch.is_tensor(mask) and bool(mask.any()):
            target = target.to(device=pred.device, dtype=pred.dtype)
            mask = mask.to(device=pred.device, dtype=torch.bool)
            bce = F.binary_cross_entropy_with_logits(pred, target, reduction="none")
            metrics[f"{prefix}/{name}_bce"] = float(bce[mask].mean().cpu())
            metrics[f"{prefix}/{name}_positive_rate"] = float(target[mask].float().mean().cpu())
    for name in CORPORATE_ACTION_FLAGS:
        pred = output.corporate_action_logits.get(name)
        target = batch.y.get("corporate_action_labels", {}).get(name)
        if pred is not None and torch.is_tensor(target) and target.numel():
            target = target.to(device=pred.device, dtype=pred.dtype)
            metrics[f"{prefix}/{name}_bce"] = float(F.binary_cross_entropy_with_logits(pred, target).cpu())
            metrics[f"{prefix}/{name}_positive_rate"] = float(target.float().mean().cpu())
    return metrics


def wandb_metric_key(key: str) -> str:
    return key
