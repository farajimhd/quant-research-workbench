from __future__ import annotations

import math
from dataclasses import dataclass, field

import torch

from research.bar_gpt.v1.data import BarGPTBatch
from research.bar_gpt.v1.features import MODEL_FEATURE_NAMES
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


def _average_rank(values: torch.Tensor) -> torch.Tensor:
    values = values.to(torch.float64)
    if values.numel() == 0:
        return values
    order = torch.argsort(values, stable=True)
    sorted_values = values[order]
    group_start = torch.ones(sorted_values.numel(), dtype=torch.bool)
    group_start[1:] = sorted_values[1:] != sorted_values[:-1]
    group = group_start.cumsum(0) - 1
    counts = torch.bincount(group)
    ends = counts.cumsum(0)
    starts = ends - counts
    average = (starts + ends - 1).to(torch.float64) / 2.0
    ranks = torch.empty_like(values)
    ranks[order] = average[group]
    return ranks


def _spearman(scores: torch.Tensor, targets: torch.Tensor) -> float:
    if scores.numel() < 2:
        return float("nan")
    score_rank = _average_rank(scores)
    target_rank = _average_rank(targets)
    score_rank -= score_rank.mean()
    target_rank -= target_rank.mean()
    denominator = score_rank.square().sum().sqrt() * target_rank.square().sum().sqrt()
    if float(denominator) == 0.0:
        return float("nan")
    return float((score_rank * target_rank).sum() / denominator)


def _macro(values: list[float]) -> float:
    finite = [value for value in values if math.isfinite(value)]
    return float(sum(finite) / len(finite)) if finite else float("nan")


@dataclass(slots=True)
class ValidationAccumulator:
    horizons_us: tuple[int, ...]
    quantiles: tuple[float, ...]
    namespace: str = "validation"
    include_loss_metrics: bool = True
    include_condition_metrics: bool = True
    include_ranking_metrics: bool = True
    include_confidence_metrics: bool = True
    weighted_metrics: dict[str, float] = field(default_factory=dict)
    metric_weight: float = 0.0
    origins: int = 0
    batches: int = 0
    endpoint_abs_error_bps: torch.Tensor | None = None
    endpoint_count: torch.Tensor | None = None
    zero_endpoint_abs_error_bps: torch.Tensor | None = None
    persistence_endpoint_abs_error_bps: torch.Tensor | None = None
    direction_confusion: torch.Tensor | None = None
    binary_brier: torch.Tensor | None = None
    binary_count: torch.Tensor | None = None
    coverage_hits: torch.Tensor | None = None
    coverage_count: torch.Tensor | None = None
    endpoint_scores: dict[int, list[torch.Tensor]] = field(default_factory=dict)
    endpoint_targets: dict[int, list[torch.Tensor]] = field(default_factory=dict)
    direction_confidence: dict[int, list[torch.Tensor]] = field(default_factory=dict)
    direction_correct: dict[int, list[torch.Tensor]] = field(default_factory=dict)
    condition_scores: dict[tuple[int, int], list[torch.Tensor]] = field(default_factory=dict)
    condition_targets: dict[tuple[int, int], list[torch.Tensor]] = field(default_factory=dict)

    def update(self, output: BarGPTOutput, batch: BarGPTBatch, result: BarGPTLoss) -> None:
        weight = float(batch.origin_count)
        self.origins += int(weight)
        self.batches += 1
        self.metric_weight += weight
        if self.include_loss_metrics:
            for key, value in result.metrics.items():
                leaf = key.removeprefix("train/").removeprefix("loss_")
                if leaf == "loss":
                    leaf = "total"
                name = f"{self.namespace}_loss/{leaf}"
                self.weighted_metrics[name] = self.weighted_metrics.get(name, 0.0) + float(value) * weight
        if output.horizon_quantiles is None or output.horizon_availability_logits is None:
            return
        assert batch.horizon_targets is not None and batch.horizon_mask is not None
        continuous_target = batch.horizon_targets[..., :CONTINUOUS_TARGET_COUNT]
        continuous_mask = batch.horizon_mask[..., :CONTINUOUS_TARGET_COUNT] & batch.origin_mask[:, :, None, None]
        median_index = min(range(len(self.quantiles)), key=lambda index: abs(self.quantiles[index] - 0.5))
        median = output.horizon_quantiles[..., median_index]
        endpoint_mask = continuous_mask[..., 0]
        endpoint_target = continuous_target[..., 0]
        endpoint_prediction = median[..., 0].detach()
        # Endpoint targets are asinh(log_return * 100). Inverting and multiplying
        # by 10,000 yields log-return basis points exactly: sinh(value) * 100.
        predicted_bps = torch.sinh(endpoint_prediction.double()) * 100.0
        target_bps = torch.sinh(endpoint_target.double()) * 100.0
        error_sum = torch.where(
            endpoint_mask,
            (predicted_bps - target_bps).abs(),
            torch.zeros_like(predicted_bps),
        ).sum((0, 1)).cpu()
        endpoint_count = endpoint_mask.sum((0, 1)).double().cpu()
        self.endpoint_abs_error_bps = (
            error_sum if self.endpoint_abs_error_bps is None else self.endpoint_abs_error_bps + error_sum
        )
        self.endpoint_count = endpoint_count if self.endpoint_count is None else self.endpoint_count + endpoint_count
        zero_error_sum = torch.where(
            endpoint_mask,
            target_bps.abs(),
            torch.zeros_like(target_bps),
        ).sum((0, 1)).cpu()
        trade_return_index = MODEL_FEATURE_NAMES.index("trade_close_return")
        batch_indices = torch.arange(batch.origin_indices.shape[0], device=batch.origin_indices.device)[:, None]
        current_return = batch.views["1s"][
            batch_indices,
            batch.origin_indices.long(),
            trade_return_index,
        ]
        persistence_bps = torch.sinh(current_return.double()) * 100.0
        persistence_error_sum = torch.where(
            endpoint_mask,
            (persistence_bps[:, :, None] - target_bps).abs(),
            torch.zeros_like(target_bps),
        ).sum((0, 1)).cpu()
        self.zero_endpoint_abs_error_bps = (
            zero_error_sum
            if self.zero_endpoint_abs_error_bps is None
            else self.zero_endpoint_abs_error_bps + zero_error_sum
        )
        self.persistence_endpoint_abs_error_bps = (
            persistence_error_sum
            if self.persistence_endpoint_abs_error_bps is None
            else self.persistence_endpoint_abs_error_bps + persistence_error_sum
        )

        predicted_positive = endpoint_prediction >= 0
        target_positive = endpoint_target >= 0
        confusion = torch.stack(
            (
                (predicted_positive & target_positive & endpoint_mask).sum((0, 1)),
                ((~predicted_positive) & (~target_positive) & endpoint_mask).sum((0, 1)),
                (predicted_positive & (~target_positive) & endpoint_mask).sum((0, 1)),
                ((~predicted_positive) & target_positive & endpoint_mask).sum((0, 1)),
            ),
            dim=-1,
        ).double().cpu()
        self.direction_confusion = confusion if self.direction_confusion is None else self.direction_confusion + confusion

        endpoint_quantiles = output.horizon_quantiles[..., 0, :].detach()
        coverage_hits = (
            (endpoint_target[..., None] <= endpoint_quantiles) & endpoint_mask[..., None]
        ).sum((0, 1)).double().cpu()
        coverage_count = endpoint_count[:, None].expand_as(coverage_hits)
        self.coverage_hits = coverage_hits if self.coverage_hits is None else self.coverage_hits + coverage_hits
        self.coverage_count = coverage_count if self.coverage_count is None else self.coverage_count + coverage_count

        binary_target = batch.horizon_targets[..., CONTINUOUS_TARGET_COUNT:]
        binary_mask = batch.horizon_mask[..., CONTINUOUS_TARGET_COUNT:] & batch.origin_mask[:, :, None, None]
        probability = output.horizon_availability_logits.detach().sigmoid()
        brier_sum = torch.where(
            binary_mask,
            (probability - binary_target).square(),
            torch.zeros_like(probability),
        ).sum((0, 1, 3)).double().cpu()
        brier_count = binary_mask.sum((0, 1, 3)).double().cpu()
        self.binary_brier = brier_sum if self.binary_brier is None else self.binary_brier + brier_sum
        self.binary_count = brier_count if self.binary_count is None else self.binary_count + brier_count

        if self.include_ranking_metrics or self.include_confidence_metrics:
            for horizon_index in range(len(self.horizons_us)):
                selected = endpoint_mask[:, :, horizon_index]
                if not torch.any(selected):
                    continue
                if self.include_ranking_metrics:
                    self.endpoint_scores.setdefault(horizon_index, []).append(
                        predicted_bps[:, :, horizon_index][selected].detach().float().cpu()
                    )
                    self.endpoint_targets.setdefault(horizon_index, []).append(
                        target_bps[:, :, horizon_index][selected].detach().float().cpu()
                    )
                if self.include_confidence_metrics:
                    self.direction_confidence.setdefault(horizon_index, []).append(
                        predicted_bps[:, :, horizon_index][selected].abs().detach().float().cpu()
                    )
                    self.direction_correct.setdefault(horizon_index, []).append(
                        (predicted_positive[:, :, horizon_index][selected]
                         == target_positive[:, :, horizon_index][selected]).detach().cpu()
                    )

        if self.include_condition_metrics:
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
            raise RuntimeError("metric accumulator produced no batches")
        metrics = {key: value / self.metric_weight for key, value in self.weighted_metrics.items()}
        metrics[f"{self.namespace}_data/origins"] = float(self.origins)
        metrics[f"{self.namespace}_data/batches"] = float(self.batches)
        if self.endpoint_abs_error_bps is None or self.endpoint_count is None:
            return metrics

        mae_bps = self.endpoint_abs_error_bps / self.endpoint_count.clamp_min(1)
        zero_mae_bps = self.zero_endpoint_abs_error_bps / self.endpoint_count.clamp_min(1)
        persistence_mae_bps = self.persistence_endpoint_abs_error_bps / self.endpoint_count.clamp_min(1)
        coverage = self.coverage_hits / self.coverage_count.clamp_min(1)
        brier = self.binary_brier / self.binary_count.clamp_min(1)
        balanced_values: list[float] = []
        mcc_values: list[float] = []
        mae_values: list[float] = []
        brier_values: list[float] = []
        calibration_values: list[float] = []
        coverage_macros: list[list[float]] = [[] for _ in self.quantiles]
        rank_values: list[float] = []
        zero_mae_values: list[float] = []
        persistence_mae_values: list[float] = []
        zero_skill_values: list[float] = []
        confidence_values: dict[int, list[float]] = {10: [], 20: []}
        for horizon_index, horizon_us in enumerate(self.horizons_us):
            label = f"{horizon_us // 1_000_000}s"
            mae_value = float(mae_bps[horizon_index])
            zero_mae_value = float(zero_mae_bps[horizon_index])
            persistence_mae_value = float(persistence_mae_bps[horizon_index])
            zero_skill = 1.0 - mae_value / max(zero_mae_value, 1e-12)
            brier_value = float(brier[horizon_index])
            metrics[f"{self.namespace}_return/mae_bps_{label}"] = mae_value
            metrics[f"{self.namespace}_baseline/zero_return_mae_bps_{label}"] = zero_mae_value
            metrics[f"{self.namespace}_baseline/persistence_mae_bps_{label}"] = persistence_mae_value
            metrics[f"{self.namespace}_return/mae_skill_vs_zero_{label}"] = zero_skill
            metrics[f"{self.namespace}_availability/brier_{label}"] = brier_value
            mae_values.append(mae_value)
            zero_mae_values.append(zero_mae_value)
            persistence_mae_values.append(persistence_mae_value)
            zero_skill_values.append(zero_skill)
            brier_values.append(brier_value)

            tp, tn, fp, fn = (float(value) for value in self.direction_confusion[horizon_index])
            tpr = tp / (tp + fn) if tp + fn else float("nan")
            tnr = tn / (tn + fp) if tn + fp else float("nan")
            balanced = _macro([tpr, tnr])
            denominator = math.sqrt((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn))
            mcc = ((tp * tn) - (fp * fn)) / denominator if denominator else float("nan")
            metrics[f"{self.namespace}_direction/balanced_accuracy_{label}"] = balanced
            metrics[f"{self.namespace}_direction/mcc_{label}"] = mcc
            balanced_values.append(balanced)
            mcc_values.append(mcc)

            errors = []
            for quantile_index, quantile in enumerate(self.quantiles):
                value = float(coverage[horizon_index, quantile_index])
                quantile_label = f"q{int(round(quantile * 100)):02d}"
                metrics[f"{self.namespace}_coverage_{quantile_label}/{label}"] = value
                coverage_macros[quantile_index].append(value)
                errors.append(abs(value - quantile))
            calibration = _macro(errors)
            metrics[f"{self.namespace}_calibration/error_{label}"] = calibration
            calibration_values.append(calibration)

            if horizon_index in self.endpoint_scores:
                rank = _spearman(
                    torch.cat(self.endpoint_scores[horizon_index]),
                    torch.cat(self.endpoint_targets[horizon_index]),
                )
                metrics[f"{self.namespace}_ranking/spearman_{label}"] = rank
                rank_values.append(rank)
            if horizon_index in self.direction_confidence:
                confidence = torch.cat(self.direction_confidence[horizon_index])
                correct = torch.cat(self.direction_correct[horizon_index]).float()
                order = torch.argsort(confidence, descending=True)
                for percentage in (10, 20):
                    count = max(1, math.ceil(order.numel() * percentage / 100.0))
                    value = float(correct[order[:count]].mean())
                    metrics[f"{self.namespace}_confidence/top_{percentage}pct_accuracy_{label}"] = value
                    confidence_values[percentage].append(value)

        metrics[f"{self.namespace}_return/mae_bps_macro"] = _macro(mae_values)
        metrics[f"{self.namespace}_baseline/zero_return_mae_bps_macro"] = _macro(zero_mae_values)
        metrics[f"{self.namespace}_baseline/persistence_mae_bps_macro"] = _macro(persistence_mae_values)
        metrics[f"{self.namespace}_return/mae_skill_vs_zero_macro"] = _macro(zero_skill_values)
        metrics[f"{self.namespace}_availability/brier_macro"] = _macro(brier_values)
        metrics[f"{self.namespace}_direction/balanced_accuracy_macro"] = _macro(balanced_values)
        metrics[f"{self.namespace}_direction/mcc_macro"] = _macro(mcc_values)
        metrics[f"{self.namespace}_calibration/error_macro"] = _macro(calibration_values)
        for quantile_index, quantile in enumerate(self.quantiles):
            quantile_label = f"q{int(round(quantile * 100)):02d}"
            metrics[f"{self.namespace}_coverage_{quantile_label}/macro"] = _macro(coverage_macros[quantile_index])
        if rank_values:
            metrics[f"{self.namespace}_ranking/spearman_macro"] = _macro(rank_values)
        for percentage, values in confidence_values.items():
            if values:
                metrics[f"{self.namespace}_confidence/top_{percentage}pct_accuracy_macro"] = _macro(values)

        if self.include_condition_metrics:
            for condition_index, name in enumerate(CONDITION_TARGET_NAMES):
                group = name.removesuffix("_within_horizon")
                average_precisions: list[float] = []
                active_heads = 0
                for horizon_index, horizon_us in enumerate(self.horizons_us):
                    key = (condition_index, horizon_index)
                    if key not in self.condition_scores:
                        continue
                    scores = torch.cat(self.condition_scores[key])
                    targets = torch.cat(self.condition_targets[key])
                    label = f"{horizon_us // 1_000_000}s"
                    positives = int(targets.sum())
                    metrics[f"{self.namespace}_condition_{group}/positives_{label}"] = float(positives)
                    if positives:
                        average_precision = _average_precision(scores, targets)
                        metrics[f"{self.namespace}_condition_{group}/average_precision_{label}"] = average_precision
                        average_precisions.append(average_precision)
                        active_heads += 1
                metrics[f"{self.namespace}_condition_{group}/active_horizons"] = float(active_heads)
                if average_precisions:
                    metrics[f"{self.namespace}_condition_{group}/average_precision_macro"] = _macro(average_precisions)
        return metrics
