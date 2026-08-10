from __future__ import annotations

import math
from dataclasses import dataclass, field

import torch

from research.bar_gpt.v1.data import AUTOREGRESSIVE_VIEW_NAMES, BarGPTBatch
from research.bar_gpt.v1.features import MODEL_FEATURE_NAMES
from research.bar_gpt.v1.model import BarGPTOutput
from research.bar_gpt.v1.objectives import BarGPTLoss
from research.bar_gpt.v1.targets import (
    CONDITION_TARGET_NAMES,
    CONTINUOUS_TARGET_COUNT,
    DIRECTION_TARGET_COUNT,
    DIRECTION_TARGET_NAMES,
    OHLC_FIELDS,
    PRICE_FAMILIES,
)


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


def _direction_scores(confusion: torch.Tensor) -> tuple[float, float, float]:
    tp, tn, fp, fn = (float(value) for value in confusion)
    tpr = tp / (tp + fn) if tp + fn else float("nan")
    tnr = tn / (tn + fp) if tn + fp else float("nan")
    balanced = _macro([tpr, tnr])
    total = tp + tn + fp + fn
    accuracy = (tp + tn) / total if total else float("nan")
    denominator = math.sqrt((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn))
    mcc = ((tp * tn) - (fp * fn)) / denominator if denominator else float("nan")
    return accuracy, balanced, mcc


@dataclass(slots=True)
class ValidationAccumulator:
    horizons_us: tuple[int, ...]
    quantiles: tuple[float, ...]
    namespace: str = "validation"
    include_loss_metrics: bool = True
    include_condition_metrics: bool = True
    include_ranking_metrics: bool = True
    include_confidence_metrics: bool = True
    direction_neutral_bps: float = 1.0
    weighted_metrics: dict[str, float] = field(default_factory=dict)
    metric_weight: float = 0.0
    origins: int = 0
    batches: int = 0
    endpoint_abs_error_bps: torch.Tensor | None = None
    endpoint_count: torch.Tensor | None = None
    zero_endpoint_abs_error_bps: torch.Tensor | None = None
    persistence_endpoint_abs_error_bps: torch.Tensor | None = None
    direction_confusion: torch.Tensor | None = None
    direction_neutral_count: torch.Tensor | None = None
    direction_total_count: torch.Tensor | None = None
    autoregressive_direction_confusion: dict[str, torch.Tensor] = field(default_factory=dict)
    autoregressive_direction_neutral_count: dict[str, torch.Tensor] = field(default_factory=dict)
    autoregressive_direction_total_count: dict[str, torch.Tensor] = field(default_factory=dict)
    binary_brier: torch.Tensor | None = None
    binary_count: torch.Tensor | None = None
    coverage_hits: torch.Tensor | None = None
    coverage_count: torch.Tensor | None = None
    endpoint_scores: dict[tuple[int, int], list[torch.Tensor]] = field(default_factory=dict)
    endpoint_targets: dict[tuple[int, int], list[torch.Tensor]] = field(default_factory=dict)
    direction_confidence: dict[tuple[int, int], list[torch.Tensor]] = field(default_factory=dict)
    direction_correct: dict[tuple[int, int], list[torch.Tensor]] = field(default_factory=dict)
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
                ar_view_leaf = leaf.removeprefix("ar_")
                group = (
                    f"{self.namespace}_loss_ar_views"
                    if ar_view_leaf in AUTOREGRESSIVE_VIEW_NAMES
                    else f"{self.namespace}_loss"
                )
                name = f"{group}/{leaf}"
                self.weighted_metrics[name] = self.weighted_metrics.get(name, 0.0) + float(value) * weight
        transformed_threshold = math.asinh(float(self.direction_neutral_bps) / 100.0)
        for name, logits in output.autoregressive_direction_logits.items():
            target = batch.autoregressive_targets[name][:, : logits.shape[1], :DIRECTION_TARGET_COUNT]
            valid = batch.autoregressive_mask[name][:, : logits.shape[1], :DIRECTION_TARGET_COUNT]
            directional = valid & (target.abs() > transformed_threshold)
            predicted_positive = logits.detach() > 0
            target_positive = target > transformed_threshold
            confusion = torch.stack(
                (
                    (predicted_positive & target_positive & directional).sum((0, 1)),
                    ((~predicted_positive) & (~target_positive) & directional).sum((0, 1)),
                    (predicted_positive & (~target_positive) & directional).sum((0, 1)),
                    ((~predicted_positive) & target_positive & directional).sum((0, 1)),
                ),
                dim=-1,
            ).double().cpu()
            previous = self.autoregressive_direction_confusion.get(name)
            self.autoregressive_direction_confusion[name] = confusion if previous is None else previous + confusion
            total = valid.sum((0, 1)).double().cpu()
            neutral = (valid & ~directional).sum((0, 1)).double().cpu()
            self.autoregressive_direction_total_count[name] = (
                self.autoregressive_direction_total_count.get(name, 0.0) + total
            )
            self.autoregressive_direction_neutral_count[name] = (
                self.autoregressive_direction_neutral_count.get(name, 0.0) + neutral
            )
        if output.horizon_quantiles is None or output.horizon_availability_logits is None:
            return
        if output.horizon_direction_logits is None:
            raise RuntimeError("physical-horizon direction logits are required for metrics")
        assert batch.horizon_targets is not None and batch.horizon_mask is not None
        continuous_target = batch.horizon_targets[..., :CONTINUOUS_TARGET_COUNT]
        continuous_mask = batch.horizon_mask[..., :CONTINUOUS_TARGET_COUNT] & batch.origin_mask[:, :, None, None]
        median_index = min(range(len(self.quantiles)), key=lambda index: abs(self.quantiles[index] - 0.5))
        median = output.horizon_quantiles[..., median_index]
        endpoint_mask = continuous_mask[..., :DIRECTION_TARGET_COUNT]
        endpoint_target = continuous_target[..., :DIRECTION_TARGET_COUNT]
        endpoint_prediction = median[..., :DIRECTION_TARGET_COUNT].detach()
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
        return_indices = [
            MODEL_FEATURE_NAMES.index(f"{family}_close_return")
            for family in PRICE_FAMILIES
            for _ in OHLC_FIELDS
        ]
        batch_indices = torch.arange(batch.origin_indices.shape[0], device=batch.origin_indices.device)[:, None]
        current_return = batch.views["1s"][batch_indices, batch.origin_indices.long()][..., return_indices]
        persistence_bps = torch.sinh(current_return.double()) * 100.0
        persistence_error_sum = torch.where(
            endpoint_mask,
            (persistence_bps[:, :, None, :] - target_bps).abs(),
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

        directional_mask = endpoint_mask & (endpoint_target.abs() > transformed_threshold)
        predicted_positive = output.horizon_direction_logits.detach() > 0
        target_positive = endpoint_target > transformed_threshold
        confusion = torch.stack(
            (
                (predicted_positive & target_positive & directional_mask).sum((0, 1)),
                ((~predicted_positive) & (~target_positive) & directional_mask).sum((0, 1)),
                (predicted_positive & (~target_positive) & directional_mask).sum((0, 1)),
                ((~predicted_positive) & target_positive & directional_mask).sum((0, 1)),
            ),
            dim=-1,
        ).double().cpu()
        self.direction_confusion = confusion if self.direction_confusion is None else self.direction_confusion + confusion
        neutral_count = (endpoint_mask & ~directional_mask).sum((0, 1)).double().cpu()
        total_count = endpoint_mask.sum((0, 1)).double().cpu()
        self.direction_neutral_count = neutral_count if self.direction_neutral_count is None else self.direction_neutral_count + neutral_count
        self.direction_total_count = total_count if self.direction_total_count is None else self.direction_total_count + total_count

        endpoint_quantiles = output.horizon_quantiles[..., :DIRECTION_TARGET_COUNT, :].detach()
        coverage_hits = ((endpoint_target[..., None] <= endpoint_quantiles) & endpoint_mask[..., None]).sum((0, 1)).double().cpu()
        coverage_count = endpoint_count[..., None].expand_as(coverage_hits)
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
                for target_index in range(DIRECTION_TARGET_COUNT):
                    key = (target_index, horizon_index)
                    if self.include_ranking_metrics:
                        selected = endpoint_mask[:, :, horizon_index, target_index]
                        if torch.any(selected):
                            self.endpoint_scores.setdefault(key, []).append(
                                predicted_bps[:, :, horizon_index, target_index][selected].detach().float().cpu()
                            )
                            self.endpoint_targets.setdefault(key, []).append(
                                target_bps[:, :, horizon_index, target_index][selected].detach().float().cpu()
                            )
                    directional = directional_mask[:, :, horizon_index, target_index]
                    if self.include_confidence_metrics and torch.any(directional):
                        self.direction_confidence.setdefault(key, []).append(
                            output.horizon_direction_logits[:, :, horizon_index, target_index][directional].abs().detach().float().cpu()
                        )
                        self.direction_correct.setdefault(key, []).append(
                            (predicted_positive[:, :, horizon_index, target_index][directional]
                             == target_positive[:, :, horizon_index, target_index][directional]).detach().cpu()
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
        ar_accuracy_values: list[float] = []
        ar_balanced_values: list[float] = []
        ar_mcc_values: list[float] = []
        ar_neutral_values: list[float] = []
        for name in sorted(self.autoregressive_direction_confusion):
            confusion_by_target = self.autoregressive_direction_confusion[name]
            total_by_target = self.autoregressive_direction_total_count[name]
            neutral_by_target = self.autoregressive_direction_neutral_count[name]
            accuracy, balanced, mcc = _direction_scores(confusion_by_target.sum(dim=0))
            total = float(total_by_target.sum())
            neutral_fraction = float(neutral_by_target.sum()) / total if total else float("nan")
            metrics[f"{self.namespace}_ar_direction_accuracy/accuracy_{name}"] = accuracy
            metrics[f"{self.namespace}_ar_direction_balanced/balanced_accuracy_{name}"] = balanced
            metrics[f"{self.namespace}_ar_direction_mcc/mcc_{name}"] = mcc
            metrics[f"{self.namespace}_ar_direction_neutral/neutral_fraction_{name}"] = neutral_fraction
            ar_accuracy_values.append(accuracy)
            ar_balanced_values.append(balanced)
            ar_mcc_values.append(mcc)
            ar_neutral_values.append(neutral_fraction)
            for target_index, target_name in enumerate(DIRECTION_TARGET_NAMES):
                target_accuracy, target_balanced, target_mcc = _direction_scores(
                    confusion_by_target[target_index]
                )
                target_total = float(total_by_target[target_index])
                target_neutral = (
                    float(neutral_by_target[target_index]) / target_total
                    if target_total
                    else float("nan")
                )
                prefix = f"{self.namespace}_ar_{target_name}_direction"
                metrics[f"{prefix}_accuracy/accuracy_{name}"] = target_accuracy
                metrics[f"{prefix}_balanced/balanced_accuracy_{name}"] = target_balanced
                metrics[f"{prefix}_mcc/mcc_{name}"] = target_mcc
                metrics[f"{prefix}_neutral/neutral_fraction_{name}"] = target_neutral
        if ar_accuracy_values:
            metrics[f"{self.namespace}_ar_direction_accuracy/accuracy_macro"] = _macro(ar_accuracy_values)
            metrics[f"{self.namespace}_ar_direction_balanced/balanced_accuracy_macro"] = _macro(ar_balanced_values)
            metrics[f"{self.namespace}_ar_direction_mcc/mcc_macro"] = _macro(ar_mcc_values)
            metrics[f"{self.namespace}_ar_direction_neutral/neutral_fraction_macro"] = _macro(ar_neutral_values)
        if self.endpoint_abs_error_bps is None or self.endpoint_count is None:
            return metrics

        mae_bps = self.endpoint_abs_error_bps / self.endpoint_count.clamp_min(1)
        zero_mae_bps = self.zero_endpoint_abs_error_bps / self.endpoint_count.clamp_min(1)
        persistence_mae_bps = self.persistence_endpoint_abs_error_bps / self.endpoint_count.clamp_min(1)
        coverage = self.coverage_hits / self.coverage_count.clamp_min(1)
        brier = self.binary_brier / self.binary_count.clamp_min(1)
        brier_values: list[float] = []
        family_names = PRICE_FAMILIES
        family_summary = {
            family: {key: [] for key in (
                "mae", "zero_mae", "persistence_mae", "skill", "accuracy",
                "balanced", "mcc", "neutral", "calibration", "rank", "top10", "top20",
            )}
            for family in family_names
        }
        coverage_macros = {
            family: [[] for _ in self.quantiles] for family in family_names
        }
        for horizon_index, horizon_us in enumerate(self.horizons_us):
            label = f"{horizon_us // 1_000_000}s"
            brier_value = float(brier[horizon_index])
            metrics[f"{self.namespace}_availability/brier_{label}"] = brier_value
            brier_values.append(brier_value)
            for target_index, target_name in enumerate(DIRECTION_TARGET_NAMES):
                family, field, _ = target_name.split("_", 2)
                summary = family_summary[family]
                mae_value = float(mae_bps[horizon_index, target_index])
                zero_mae_value = float(zero_mae_bps[horizon_index, target_index])
                persistence_mae_value = float(persistence_mae_bps[horizon_index, target_index])
                zero_skill = 1.0 - mae_value / max(zero_mae_value, 1e-12)
                target_prefix = f"{self.namespace}_{family}_{field}"
                metrics[f"{target_prefix}_return_error/mae_bps_{label}"] = mae_value
                metrics[f"{target_prefix}_return_error/persistence_mae_bps_{label}"] = persistence_mae_value
                metrics[f"{target_prefix}_return_skill/zero_mae_bps_{label}"] = zero_mae_value
                metrics[f"{target_prefix}_return_skill/skill_vs_zero_{label}"] = zero_skill
                summary["mae"].append(mae_value)
                summary["zero_mae"].append(zero_mae_value)
                summary["persistence_mae"].append(persistence_mae_value)
                summary["skill"].append(zero_skill)

                accuracy, balanced, mcc = _direction_scores(self.direction_confusion[horizon_index, target_index])
                direction_total = float(self.direction_total_count[horizon_index, target_index])
                neutral_fraction = float(self.direction_neutral_count[horizon_index, target_index]) / direction_total if direction_total else float("nan")
                direction_group = f"{target_prefix}_direction"
                metrics[f"{direction_group}/accuracy_{label}"] = accuracy
                metrics[f"{direction_group}/balanced_accuracy_{label}"] = balanced
                metrics[f"{target_prefix}_direction_quality/mcc_{label}"] = mcc
                metrics[f"{target_prefix}_direction_quality/neutral_fraction_{label}"] = neutral_fraction
                summary["accuracy"].append(accuracy)
                summary["balanced"].append(balanced)
                summary["mcc"].append(mcc)
                summary["neutral"].append(neutral_fraction)

                errors = []
                for quantile_index, quantile in enumerate(self.quantiles):
                    value = float(coverage[horizon_index, target_index, quantile_index])
                    quantile_label = f"q{int(round(quantile * 100)):02d}"
                    metrics[f"{target_prefix}_coverage_{quantile_label}/{label}"] = value
                    coverage_macros[family][quantile_index].append(value)
                    errors.append(abs(value - quantile))
                calibration = _macro(errors)
                metrics[f"{target_prefix}_calibration/error_{label}"] = calibration
                summary["calibration"].append(calibration)

                key = (target_index, horizon_index)
                if key in self.endpoint_scores:
                    rank = _spearman(torch.cat(self.endpoint_scores[key]), torch.cat(self.endpoint_targets[key]))
                    metrics[f"{target_prefix}_ranking/spearman_{label}"] = rank
                    summary["rank"].append(rank)
                if key in self.direction_confidence:
                    confidence = torch.cat(self.direction_confidence[key])
                    correct = torch.cat(self.direction_correct[key]).float()
                    order = torch.argsort(confidence, descending=True)
                    for percentage in (10, 20):
                        count = max(1, math.ceil(order.numel() * percentage / 100.0))
                        value = float(correct[order[:count]].mean())
                        metrics[f"{target_prefix}_confidence/top_{percentage}pct_{label}"] = value
                        summary[f"top{percentage}"].append(value)

        metrics[f"{self.namespace}_availability/brier_macro"] = _macro(brier_values)
        for family in family_names:
            summary = family_summary[family]
            for name, values in summary.items():
                if values:
                    metrics[f"{self.namespace}_{family}_summary/{name}_macro"] = _macro(values)
            for quantile_index, quantile in enumerate(self.quantiles):
                quantile_label = f"q{int(round(quantile * 100)):02d}"
                metrics[f"{self.namespace}_{family}_coverage_{quantile_label}/macro"] = _macro(
                    coverage_macros[family][quantile_index]
                )

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
