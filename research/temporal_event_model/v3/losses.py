from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import torch
import torch.nn.functional as F

from research.temporal_event_model.v3.config import BAR_FAMILIES, CORPORATE_ACTION_FLAGS, EXTERNAL_ARRIVAL_FLAGS, INTRADAY_EVENT_FLAGS
from research.temporal_event_model.v3.model import TemporalModelOutput


@dataclass(slots=True)
class LossResult:
    loss: torch.Tensor
    metrics: dict[str, float]


def compute_loss(output: TemporalModelOutput, batch: Any) -> LossResult:
    y = batch.y
    device = output.modality_tokens.device
    losses: list[torch.Tensor] = []
    metrics: dict[str, float] = {}

    price_terms: list[torch.Tensor] = []
    count_terms: list[torch.Tensor] = []
    size_terms: list[torch.Tensor] = []
    for family in BAR_FAMILIES:
        pred = output.future_bar_values.get(family)
        target = y.get("future_bar_values", {}).get(family)
        mask = y.get("future_bar_masks", {}).get(family)
        if pred is None or not torch.is_tensor(target) or not torch.is_tensor(mask):
            continue
        target = target.to(device=device, dtype=pred.dtype)
        mask = mask.to(device=device, dtype=torch.bool)
        price_width = min(4, pred.shape[-1], target.shape[-1])
        if price_width:
            price_target = target[..., :price_width]
            price_loss = masked_huber(pred[..., :price_width], price_target, mask.unsqueeze(-1).expand_as(price_target))
            if price_loss is not None:
                price_terms.append(price_loss)
        if target.shape[-1] > 4 and pred.shape[-1] > 4:
            size_end = min(target.shape[-1] - 1, pred.shape[-1])
            if size_end > 4:
                size_target = target[..., 4:size_end]
                size_loss = masked_huber(pred[..., 4:size_end], size_target, mask.unsqueeze(-1).expand_as(size_target))
                if size_loss is not None:
                    size_terms.append(size_loss)
            count_target = target[..., -1]
            count_loss = masked_huber(pred[..., -1], count_target, mask)
            if count_loss is not None:
                count_terms.append(count_loss)

    _add_group_loss("price", price_terms, losses, metrics)
    _add_group_loss("event_count", count_terms, losses, metrics)
    _add_group_loss("event_size", size_terms, losses, metrics)

    intraday_labels = y.get("intraday_labels", {})
    intraday_mask = intraday_labels.get("available")
    if torch.is_tensor(intraday_mask):
        intraday_mask = intraday_mask.to(device=device, dtype=torch.bool)
    event_state_terms: list[torch.Tensor] = []
    for name in INTRADAY_EVENT_FLAGS:
        term = _bce_term(output.intraday_logits.get(name), intraday_labels.get(name), intraday_mask, device=device)
        if term is not None:
            event_state_terms.append(term)
            metrics[f"train/labels/{name}_positive_rate"] = _positive_rate(intraday_labels.get(name), intraday_mask)
    _add_group_loss("event_state", event_state_terms, losses, metrics)

    external_terms: list[torch.Tensor] = []
    for name in EXTERNAL_ARRIVAL_FLAGS:
        term = _bce_term(output.intraday_logits.get(name), intraday_labels.get(name), intraday_mask, device=device)
        if term is not None:
            external_terms.append(term)
            metrics[f"train/labels/{name}_positive_rate"] = _positive_rate(intraday_labels.get(name), intraday_mask)
    _add_group_loss("external_arrival", external_terms, losses, metrics)

    corporate_terms: list[torch.Tensor] = []
    corporate_labels = y.get("corporate_action_labels", {})
    for name in CORPORATE_ACTION_FLAGS:
        pred = output.corporate_action_logits.get(name)
        target = corporate_labels.get(name)
        if pred is None or not torch.is_tensor(target):
            continue
        mask = torch.ones_like(target, dtype=torch.bool, device=device)
        term = _bce_term(pred, target, mask, device=device)
        if term is not None:
            corporate_terms.append(term)
            metrics[f"train/labels/{name}_positive_rate"] = _positive_rate(target, mask)
    _add_group_loss("corporate_action", corporate_terms, losses, metrics)

    if losses:
        loss = torch.stack(losses).mean()
    else:
        loss = torch.zeros((), dtype=torch.float32, device=device, requires_grad=True)
    metrics["train/loss"] = float(loss.detach().float().cpu())
    metrics["train/active_task_count"] = float(len(losses))
    return LossResult(loss=loss, metrics=metrics)


def masked_huber(pred: torch.Tensor, target: torch.Tensor, mask: torch.Tensor) -> torch.Tensor | None:
    mask = mask.bool()
    if not bool(mask.any()):
        return None
    value = F.smooth_l1_loss(pred, target, reduction="none")
    return value[mask].mean()


def _bce_term(pred: torch.Tensor | None, target: Any, mask: torch.Tensor | None, *, device: torch.device) -> torch.Tensor | None:
    if pred is None or not torch.is_tensor(target):
        return None
    target = target.to(device=device, dtype=pred.dtype)
    if mask is None:
        mask = torch.ones_like(target, dtype=torch.bool, device=device)
    else:
        mask = mask.to(device=device, dtype=torch.bool)
    if not bool(mask.any()):
        return None
    value = F.binary_cross_entropy_with_logits(pred, target, reduction="none")
    return value[mask].mean()


def _add_group_loss(name: str, terms: list[torch.Tensor], losses: list[torch.Tensor], metrics: dict[str, float]) -> None:
    if not terms:
        metrics[f"train/loss_{name}"] = 0.0
        return
    value = torch.stack(terms).mean()
    losses.append(value)
    metrics[f"train/loss_{name}"] = float(value.detach().float().cpu())


def _origin_prices(x: Mapping[str, Any]) -> dict[str, torch.Tensor]:
    events = x["raw_event_stream"].float()
    mask = x.get("raw_event_mask")
    if torch.is_tensor(mask) and mask.any(dim=1).all():
        last_index = mask.long().sum(dim=1).clamp(min=1) - 1
        rows = torch.arange(events.shape[0], device=events.device)
        last = events[rows, last_index]
    else:
        last = events[:, -1]
    names = tuple(str(v) for v in x.get("event_feature_names", ()))
    meta = _column(last, names, "event_meta", 0).long()
    primary = _decode_price(_column(last, names, "price_primary_int", 1), ((meta >> 1) & 1))
    secondary = _decode_price(_column(last, names, "price_secondary_int", 2), ((meta >> 2) & 1))
    mid = torch.where((primary > 0) & (secondary > 0), (primary + secondary) * 0.5, primary.clamp(min=1.0))
    return {"trade": primary.clamp(min=1.0), "ask": primary.clamp(min=1.0), "bid": secondary.clamp(min=1.0), "mid": mid.clamp(min=1.0)}


def _column(values: torch.Tensor, names: tuple[str, ...], name: str, fallback: int) -> torch.Tensor:
    index = names.index(name) if name in names else int(fallback)
    index = max(0, min(index, values.shape[-1] - 1))
    return values[..., index]


def _decode_price(price: torch.Tensor, scale_id: torch.Tensor) -> torch.Tensor:
    denom = torch.where(scale_id.bool(), torch.full_like(price, 10_000.0), torch.full_like(price, 100.0))
    return price.float() / denom


def _positive_rate(target: Any, mask: torch.Tensor | None) -> float:
    if not torch.is_tensor(target):
        return 0.0
    value = target.bool()
    if mask is not None and torch.is_tensor(mask):
        mask = mask.to(device=value.device, dtype=torch.bool)
        if not bool(mask.any()):
            return 0.0
        value = value[mask]
    return float(value.float().mean().detach().cpu()) if value.numel() else 0.0
