from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Mapping

import torch
import torch.nn.functional as F

from research.temporal_event_model.v3.config import BAR_FAMILIES, CORPORATE_ACTION_FLAGS, EXTERNAL_ARRIVAL_FLAGS, INTRADAY_EVENT_FLAGS
from research.temporal_event_model.v3.losses import _column, _decode_price, _origin_prices
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


@torch.no_grad()
def cohort_metrics(batch: Any, output: TemporalModelOutput, *, prefix: str = "train", min_count: int = 32) -> dict[str, float]:
    cohorts = batch_cohort_flags(batch)
    if not cohorts:
        return {}
    metrics: dict[str, float] = {}
    for name, mask in cohorts.items():
        if not torch.is_tensor(mask):
            continue
        mask = mask.bool()
        count_true = int(mask.sum().detach().cpu())
        count_false = int((~mask).sum().detach().cpu())
        metrics[f"{prefix}/cohort/{name}/true_count"] = float(count_true)
        metrics[f"{prefix}/cohort/{name}/false_count"] = float(count_false)
        if count_true >= int(min_count):
            metrics.update(_cohort_prediction_metrics(batch, output, prefix=prefix, cohort_name=name, cohort_mask=mask, suffix="true"))
        if count_false >= int(min_count):
            metrics.update(_cohort_prediction_metrics(batch, output, prefix=prefix, cohort_name=name, cohort_mask=~mask, suffix="false"))
    return metrics


@torch.no_grad()
def batch_cohort_flags(batch: Any) -> dict[str, torch.Tensor]:
    x = batch.x
    events = x.get("raw_event_stream")
    if not torch.is_tensor(events):
        return {}
    device = events.device
    names = tuple(str(v) for v in x.get("event_feature_names", ()))
    mask = x.get("raw_event_mask")
    if not torch.is_tensor(mask):
        mask = torch.ones(events.shape[:2], dtype=torch.bool, device=device)
    event_count = mask.sum(dim=1)
    last = _last_event_rows(events, mask)
    primary = _column(last, names, "price_primary_int", 1).float()
    secondary = _column(last, names, "price_secondary_int", 2).float()
    meta = _column(last, names, "event_meta", 0).long()
    primary = _decode_price(primary, ((meta >> 1) & 1))
    secondary = _decode_price(secondary, ((meta >> 2) & 1))
    mid = ((primary + secondary) * 0.5).clamp(min=1e-6)
    spread_bps = ((primary - secondary).abs() / mid) * 10_000.0
    size_primary = _column(last, names, "size_primary", 3).float()
    size_secondary = _column(last, names, "size_secondary", 4).float()
    flags: dict[str, torch.Tensor] = {
        "liquid_event_count_asof": event_count >= torch.quantile(event_count.float(), 0.75),
        "illiquid_event_count_asof": event_count <= torch.quantile(event_count.float(), 0.25),
        "wide_spread_asof": spread_bps >= torch.quantile(spread_bps.float(), 0.75),
        "high_size_asof": (size_primary + size_secondary) >= torch.quantile((size_primary + size_secondary).float(), 0.75),
    }
    for column in ("is_regular_hours", "is_premarket", "is_afterhours"):
        if column in names:
            flags[column] = _column(last, names, column, 0).float() > 0.5
    for group_name, payload in (x.get("text_inputs") or {}).items():
        chunk_mask = payload.get("chunk_mask") if isinstance(payload, Mapping) else None
        if torch.is_tensor(chunk_mask):
            flags[f"{group_name}_available"] = chunk_mask.reshape(chunk_mask.shape[0], -1).any(dim=1)
    xbrl_mask = (x.get("xbrl_inputs") or {}).get("mask")
    if torch.is_tensor(xbrl_mask):
        flags["xbrl_available"] = xbrl_mask.reshape(xbrl_mask.shape[0], -1).any(dim=1)
    corporate_mask = (x.get("corporate_action_inputs") or {}).get("mask")
    if torch.is_tensor(corporate_mask):
        flags["corporate_action_context_available"] = corporate_mask.reshape(corporate_mask.shape[0], -1).any(dim=1)
    scanner_mask = (x.get("scanner_inputs") or {}).get("leader_mask")
    if torch.is_tensor(scanner_mask):
        flags["scanner_context_available"] = scanner_mask.reshape(scanner_mask.shape[0], -1).any(dim=1)
    labels = batch.y.get("intraday_labels", {})
    for label_name in (*INTRADAY_EVENT_FLAGS, *EXTERNAL_ARRIVAL_FLAGS):
        value = labels.get(label_name)
        if torch.is_tensor(value):
            flags[f"future_{label_name}"] = value.to(device=device).bool().reshape(value.shape[0], -1).any(dim=1)
    return {key: value.to(device=device, dtype=torch.bool) for key, value in flags.items() if torch.is_tensor(value)}


def _cohort_prediction_metrics(batch: Any, output: TemporalModelOutput, *, prefix: str, cohort_name: str, cohort_mask: torch.Tensor, suffix: str) -> dict[str, float]:
    metrics: dict[str, float] = {}
    origin = _origin_prices(batch.x)
    for family in BAR_FAMILIES:
        pred = output.future_bar_values.get(family)
        target = batch.y.get("future_bar_values", {}).get(family)
        mask = batch.y.get("future_bar_masks", {}).get(family)
        if pred is None or not torch.is_tensor(target) or not torch.is_tensor(mask):
            continue
        target = target.to(device=pred.device, dtype=pred.dtype)
        valid = mask.to(device=pred.device, dtype=torch.bool) & cohort_mask.to(device=pred.device).unsqueeze(1)
        width = min(4, pred.shape[-1], target.shape[-1])
        if width <= 0 or not bool(valid.any()):
            continue
        base_key = "trade" if family == "trade" else "ask" if family == "quote_ask" else "bid"
        base = origin[base_key].to(device=pred.device, dtype=pred.dtype)
        normalized_target = ((target[..., :width] - base[:, None, None]) / base.clamp(min=1e-6)[:, None, None]) * 10_000.0
        error = pred[..., :width] - normalized_target
        expanded = valid.unsqueeze(-1).expand_as(error)
        metrics[f"{prefix}/cohort/{cohort_name}/{suffix}/price_mae_{family}_bps"] = float(error[expanded].abs().mean().cpu())
    return metrics


def _last_event_rows(events: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    if mask.any(dim=1).all():
        last_index = mask.long().sum(dim=1).clamp(min=1) - 1
        rows = torch.arange(events.shape[0], device=events.device)
        return events[rows, last_index]
    return events[:, -1]


def wandb_metric_key(key: str) -> str:
    return key
