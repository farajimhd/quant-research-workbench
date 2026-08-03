from __future__ import annotations

from dataclasses import dataclass, field

import torch

from research.bar_gpt.v1.data import BarGPTBatch
from research.bar_gpt.v1.model import BarGPTOutput
from research.bar_gpt.v1.objectives import BarGPTLoss
from research.bar_gpt.v1.targets import CONDITION_TARGET_NAMES, CONTINUOUS_TARGET_COUNT


def _average_precision(scores: torch.Tensor, targets: torch.Tensor) -> float:
    positives = int(targets.sum())
    if positives == 0:
        return float("nan")
    order = torch.argsort(scores, descending=True)
    ranked = targets[order].to(torch.float64)
    precision = ranked.cumsum(0) / torch.arange(1, ranked.numel() + 1, dtype=torch.float64)
    return float((precision * ranked).sum() / positives)


@dataclass(slots=True)
class ValidationAccumulator:
    horizons_us: tuple[int, ...]
    quantiles: tuple[float, ...]
    weighted_metrics: dict[str, float] = field(default_factory=dict)
    metric_weight: float = 0.0
    origins: int = 0
    batches: int = 0
    median_abs_error: torch.Tensor | None = None
    median_count: torch.Tensor | None = None
    sign_correct: torch.Tensor | None = None
    sign_count: torch.Tensor | None = None
    binary_brier: torch.Tensor | None = None
    binary_count: torch.Tensor | None = None
    coverage_hits: torch.Tensor | None = None
    coverage_count: torch.Tensor | None = None
    condition_scores: dict[tuple[int, int], list[torch.Tensor]] = field(default_factory=dict)
    condition_targets: dict[tuple[int, int], list[torch.Tensor]] = field(default_factory=dict)

    def update(self, output: BarGPTOutput, batch: BarGPTBatch, result: BarGPTLoss) -> None:
        weight = float(batch.origin_count)
        self.origins += int(weight)
        self.batches += 1
        self.metric_weight += weight
        for key, value in result.metrics.items():
            name = "val/" + key.removeprefix("train/")
            self.weighted_metrics[name] = self.weighted_metrics.get(name, 0.0) + float(value) * weight
        if output.horizon_quantiles is None or output.horizon_availability_logits is None:
            return
        assert batch.horizon_targets is not None and batch.horizon_mask is not None
        continuous_target = batch.horizon_targets[..., :CONTINUOUS_TARGET_COUNT]
        continuous_mask = batch.horizon_mask[..., :CONTINUOUS_TARGET_COUNT] & batch.origin_mask[:, :, None, None]
        median_index = min(range(len(self.quantiles)), key=lambda index: abs(self.quantiles[index] - 0.5))
        median = output.horizon_quantiles[..., median_index]
        errors = (median - continuous_target).abs()
        reduce_dims = (0, 1, 3)
        error_sum = torch.where(continuous_mask, errors, torch.zeros_like(errors)).sum(reduce_dims).double().cpu()
        error_count = continuous_mask.sum(reduce_dims).double().cpu()
        self.median_abs_error = error_sum if self.median_abs_error is None else self.median_abs_error + error_sum
        self.median_count = error_count if self.median_count is None else self.median_count + error_count
        endpoint_mask = continuous_mask[..., 0]
        endpoint_target = continuous_target[..., 0]
        correct = ((median[..., 0] >= 0) == (endpoint_target >= 0)) & endpoint_mask
        sign_sum = correct.sum((0, 1)).double().cpu()
        sign_count = endpoint_mask.sum((0, 1)).double().cpu()
        self.sign_correct = sign_sum if self.sign_correct is None else self.sign_correct + sign_sum
        self.sign_count = sign_count if self.sign_count is None else self.sign_count + sign_count
        coverage_hits = []
        for index in range(len(self.quantiles)):
            coverage_hits.append(((continuous_target <= output.horizon_quantiles[..., index]) & continuous_mask).sum(reduce_dims))
        hits = torch.stack(coverage_hits, dim=-1).double().cpu()
        counts = error_count[:, None].expand_as(hits)
        self.coverage_hits = hits if self.coverage_hits is None else self.coverage_hits + hits
        self.coverage_count = counts if self.coverage_count is None else self.coverage_count + counts

        binary_target = batch.horizon_targets[..., CONTINUOUS_TARGET_COUNT:]
        binary_mask = batch.horizon_mask[..., CONTINUOUS_TARGET_COUNT:] & batch.origin_mask[:, :, None, None]
        probability = output.horizon_availability_logits.sigmoid()
        brier_sum = torch.where(binary_mask, (probability - binary_target).square(), torch.zeros_like(probability)).sum((0, 1, 3)).double().cpu()
        brier_count = binary_mask.sum((0, 1, 3)).double().cpu()
        self.binary_brier = brier_sum if self.binary_brier is None else self.binary_brier + brier_sum
        self.binary_count = brier_count if self.binary_count is None else self.binary_count + brier_count
        for condition_index in range(len(CONDITION_TARGET_NAMES)):
            channel = binary_target.shape[-1] - len(CONDITION_TARGET_NAMES) + condition_index
            for horizon_index in range(len(self.horizons_us)):
                selected = binary_mask[:, :, horizon_index, channel]
                if torch.any(selected):
                    key = (condition_index, horizon_index)
                    self.condition_scores.setdefault(key, []).append(
                        probability[:, :, horizon_index, channel][selected].detach().float().cpu()
                    )
                    self.condition_targets.setdefault(key, []).append(
                        binary_target[:, :, horizon_index, channel][selected].detach().bool().cpu()
                    )

    def finalize(self) -> dict[str, float]:
        if self.batches == 0 or self.metric_weight <= 0:
            raise RuntimeError("fixed validation panel produced no batches")
        metrics = {key: value / self.metric_weight for key, value in self.weighted_metrics.items()}
        metrics["val/origins"] = float(self.origins)
        metrics["val/batches"] = float(self.batches)
        if self.median_abs_error is not None and self.median_count is not None:
            mae = self.median_abs_error / self.median_count.clamp_min(1)
            sign = self.sign_correct / self.sign_count.clamp_min(1)
            brier = self.binary_brier / self.binary_count.clamp_min(1)
            coverage = self.coverage_hits / self.coverage_count.clamp_min(1)
            for horizon_index, horizon_us in enumerate(self.horizons_us):
                label = f"{horizon_us // 1_000_000}s"
                metrics[f"val/horizon_{label}_median_mae"] = float(mae[horizon_index].detach())
                metrics[f"val/horizon_{label}_sign_accuracy"] = float(sign[horizon_index].detach())
                metrics[f"val/horizon_{label}_binary_brier"] = float(brier[horizon_index].detach())
                for quantile_index, quantile in enumerate(self.quantiles):
                    metrics[f"val/horizon_{label}_coverage_q{quantile:g}"] = float(
                        coverage[horizon_index, quantile_index].detach()
                    )
        for condition_index, name in enumerate(CONDITION_TARGET_NAMES):
            for horizon_index, horizon_us in enumerate(self.horizons_us):
                key = (condition_index, horizon_index)
                if key not in self.condition_scores:
                    continue
                scores = torch.cat(self.condition_scores[key])
                targets = torch.cat(self.condition_targets[key])
                label = f"{horizon_us // 1_000_000}s"
                positives = int(targets.sum())
                metrics[f"val/horizon_{label}_{name}_positives"] = float(positives)
                if positives:
                    metrics[f"val/horizon_{label}_{name}_average_precision"] = _average_precision(scores, targets)
        return metrics
