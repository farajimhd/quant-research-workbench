from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn.functional as F

from research.packed_market_model.v1.data import PackedTorchBlock
from research.packed_market_model.v1.model import PackedModelOutput


@dataclass(slots=True)
class LossResult:
    loss: torch.Tensor
    metrics: dict[str, float]


CLASSIFICATION_TOKENS = (
    "flag",
    "is_",
    "halt",
    "luld",
    "condition",
    "split",
    "dividend",
    "news",
    "sec",
    "arrival",
    "available",
)
COUNT_TOKENS = ("count", "num_", "event_count")
SIZE_TOKENS = ("size", "volume", "notional")
PRICE_TOKENS = ("price", "open", "high", "low", "close", "bid", "ask", "trade")


def compute_loss(output: PackedModelOutput, batch: PackedTorchBlock) -> LossResult:
    losses: dict[str, torch.Tensor] = {}
    metrics: dict[str, float] = {
        "train/origin_count": float(batch.origin_count),
        "train/event_count": float(batch.event_count),
    }
    for name, pred in output.label_predictions.items():
        target = batch.y.get(name)
        if target is None:
            continue
        mask = batch.masks.get(name)
        if mask is None:
            mask = torch.isfinite(target)
        if "available" in batch.masks and batch.masks["available"].shape == mask.shape:
            mask = mask & batch.masks["available"]
        if not bool(mask.any()):
            continue
        target = target.to(device=pred.device, dtype=pred.dtype)
        mask = mask.to(device=pred.device, dtype=torch.bool)
        group = label_group(name)
        if group in {"event_state", "external_arrival", "corporate_action"}:
            item_loss = F.binary_cross_entropy_with_logits(pred[mask], target[mask].clamp(0, 1), reduction="mean")
            with torch.no_grad():
                prob = torch.sigmoid(pred[mask])
                metrics[f"train/{name}_positive_rate"] = float(target[mask].float().mean().detach().cpu())
                metrics[f"train/{name}_accuracy"] = float(((prob >= 0.5) == (target[mask] >= 0.5)).float().mean().detach().cpu())
        else:
            item_loss = F.huber_loss(pred[mask], target[mask], reduction="mean", delta=1.0)
            with torch.no_grad():
                metrics[f"train/{name}_mae"] = float((pred[mask] - target[mask]).abs().mean().detach().cpu())
        losses.setdefault(group, []).append(item_loss)  # type: ignore[arg-type]
    grouped: dict[str, torch.Tensor] = {group: torch.stack(items).mean() for group, items in losses.items()}  # type: ignore[union-attr]
    if grouped:
        loss = torch.stack(tuple(grouped.values())).mean()
    else:
        reference = output.origin_embeddings
        loss = reference.sum() * 0.0
    for group, value in grouped.items():
        metrics[f"train/loss_{group}"] = float(value.detach().cpu())
    metrics["train/loss"] = float(loss.detach().cpu())
    metrics["train/active_task_count"] = float(len(grouped))
    return LossResult(loss=loss, metrics=metrics)


def label_group(name: str) -> str:
    lower = name.lower()
    if any(token in lower for token in ("news", "sec", "arrival")):
        return "external_arrival"
    if any(token in lower for token in ("split", "dividend", "corporate")):
        return "corporate_action"
    if any(token in lower for token in CLASSIFICATION_TOKENS):
        return "event_state"
    if any(token in lower for token in COUNT_TOKENS):
        return "event_count"
    if any(token in lower for token in SIZE_TOKENS):
        return "event_size"
    if any(token in lower for token in PRICE_TOKENS):
        return "price"
    return "regression"
