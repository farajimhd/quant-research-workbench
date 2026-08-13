from __future__ import annotations

import math
from dataclasses import dataclass, field

import torch

from research.bar_gpt.v2.data import BarGPTBatch
from research.bar_gpt.v2.model import BarGPTOutput
from research.bar_gpt.v2.objectives import BarGPTLoss
from research.bar_gpt.v2.targets import (
    BINARY_TARGET_NAMES,
    CONDITION_TARGET_NAMES,
    CONTINUOUS_TARGET_COUNT,
    OHLC_FIELDS,
    PRICE_FAMILIES,
    RETURN_CLASS_COUNT,
    RETURN_CLASS_NAMES,
    RETURN_TARGET_COUNT,
    RETURN_TARGET_NAMES,
    autoregressive_return_class_labels,
    physical_return_class_labels,
    transformed_return_to_percent,
)


def _macro(values: list[float]) -> float:
    finite = [value for value in values if math.isfinite(value)]
    return float(sum(finite) / len(finite)) if finite else float("nan")


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
    return float("nan") if float(denominator) == 0.0 else float((score_rank * target_rank).sum() / denominator)


def _confusion(labels: torch.Tensor, predictions: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    """Return [..., actual, predicted] confusion over leading observation axes."""
    if labels.shape != predictions.shape or labels.shape != mask.shape:
        raise ValueError("labels, predictions, and mask must align")
    target_count = labels.shape[-1]
    result = torch.zeros(
        target_count, RETURN_CLASS_COUNT, RETURN_CLASS_COUNT,
        dtype=torch.float64, device=labels.device,
    )
    for target_index in range(target_count):
        selected = mask[..., target_index]
        encoded = (
            labels[..., target_index][selected] * RETURN_CLASS_COUNT
            + predictions[..., target_index][selected]
        )
        result[target_index] = torch.bincount(
            encoded, minlength=RETURN_CLASS_COUNT * RETURN_CLASS_COUNT
        ).reshape(RETURN_CLASS_COUNT, RETURN_CLASS_COUNT)
    return result.cpu()


def multiclass_scores(confusion: torch.Tensor) -> tuple[float, float, float, float, float]:
    """Accuracy, macro recall, macro F1, multiclass MCC, and ordinal class error."""
    matrix = confusion.to(torch.float64)
    total = float(matrix.sum())
    if total == 0:
        return (float("nan"),) * 5
    diagonal = matrix.diag()
    actual = matrix.sum(dim=1)
    predicted = matrix.sum(dim=0)
    accuracy = float(diagonal.sum()) / total
    recalls = diagonal / actual.clamp_min(1)
    precisions = diagonal / predicted.clamp_min(1)
    active_recall = actual > 0
    balanced = float(recalls[active_recall].mean())
    f1 = 2.0 * precisions * recalls / (precisions + recalls).clamp_min(1e-12)
    active_f1 = (actual + predicted) > 0
    macro_f1 = float(f1[active_f1].mean())
    correct = float(diagonal.sum())
    numerator = correct * total - float((actual * predicted).sum())
    denominator = math.sqrt(
        max(0.0, total * total - float(predicted.square().sum()))
        * max(0.0, total * total - float(actual.square().sum()))
    )
    if denominator > 0:
        mcc = numerator / denominator
    elif int(active_recall.sum()) > 1:
        mcc = 0.0
    else:
        mcc = float("nan")
    indices = torch.arange(RETURN_CLASS_COUNT, dtype=torch.float64)
    distance = (indices[:, None] - indices[None, :]).abs()
    ordinal_error = float((matrix * distance).sum()) / total
    return accuracy, balanced, macro_f1, mcc, ordinal_error


def _direction_scores(confusion: torch.Tensor) -> tuple[float, float, float]:
    """Legacy binary score helper retained only for shared regression tests."""
    tp, tn, fp, fn = (float(value) for value in confusion)
    positive = tp + fn
    negative = tn + fp
    total = positive + negative
    accuracy = (tp + tn) / total if total else float("nan")
    balanced = 0.5 * (tp / positive + tn / negative) if positive and negative else float("nan")
    denominator = math.sqrt((tp + fp) * positive * negative * (tn + fn))
    mcc = (tp * tn - fp * fn) / denominator if denominator else (0.0 if positive and negative else float("nan"))
    return accuracy, balanced, mcc


def _class_support(confusion: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    total = confusion.sum()
    if float(total) <= 0:
        nan = torch.full((RETURN_CLASS_COUNT,), float("nan"), dtype=torch.float64)
        return nan, nan
    return confusion.sum(dim=1) / total, confusion.sum(dim=0) / total


@dataclass(slots=True)
class ValidationAccumulator:
    horizons_us: tuple[int, ...]
    quantiles: tuple[float, ...]
    namespace: str = "validation"
    include_loss_metrics: bool = True
    include_condition_metrics: bool = True
    include_ranking_metrics: bool = True
    include_confidence_metrics: bool = True
    loss_numerators: dict[str, float] = field(default_factory=dict)
    loss_denominators: dict[str, float] = field(default_factory=dict)
    origins: int = 0
    batches: int = 0
    return_abs_error_percent: torch.Tensor | None = None
    return_count: torch.Tensor | None = None
    zero_baseline_abs_error_percent: torch.Tensor | None = None
    continuation_abs_error_percent: torch.Tensor | None = None
    horizon_return_confusion: torch.Tensor | None = None
    autoregressive_return_confusion: dict[str, torch.Tensor] = field(default_factory=dict)
    binary_brier_sum: torch.Tensor | None = None
    binary_count: torch.Tensor | None = None
    coverage_hits: torch.Tensor | None = None
    coverage_count: torch.Tensor | None = None
    endpoint_scores: dict[tuple[int, int], list[torch.Tensor]] = field(default_factory=dict)
    endpoint_targets: dict[tuple[int, int], list[torch.Tensor]] = field(default_factory=dict)
    condition_scores: dict[tuple[int, int], list[torch.Tensor]] = field(default_factory=dict)
    condition_targets: dict[tuple[int, int], list[torch.Tensor]] = field(default_factory=dict)

    def _update_losses(self, result: BarGPTLoss) -> None:
        if not self.include_loss_metrics:
            return
        for key, value in result.metrics.items():
            if not key.startswith("train/loss_"):
                continue
            suffix = key.removeprefix("train/loss_")
            support_key = f"train/support_{suffix}"
            if support_key not in result.metrics:
                continue
            support = float(result.metrics[support_key])
            self.loss_numerators[suffix] = self.loss_numerators.get(suffix, 0.0) + float(value) * support
            self.loss_denominators[suffix] = self.loss_denominators.get(suffix, 0.0) + support

    def update(self, output: BarGPTOutput, batch: BarGPTBatch, result: BarGPTLoss) -> None:
        self.origins += int(batch.origin_count)
        self.batches += 1
        self._update_losses(result)

        for view, logits in output.autoregressive_return_class_logits.items():
            target = batch.autoregressive_targets[view][:, : logits.shape[1], :RETURN_TARGET_COUNT]
            valid = batch.autoregressive_mask[view][:, : logits.shape[1], :RETURN_TARGET_COUNT]
            confusion = _confusion(
                autoregressive_return_class_labels(target, view), logits.detach().argmax(dim=-1), valid
            )
            previous = self.autoregressive_return_confusion.get(view)
            self.autoregressive_return_confusion[view] = confusion if previous is None else previous + confusion

        if output.horizon_quantiles is None or output.horizon_availability_logits is None:
            return
        if output.horizon_return_class_logits is None:
            raise RuntimeError("v2 physical-horizon return-class logits are required")
        assert batch.horizon_targets is not None and batch.horizon_mask is not None
        continuous_target = batch.horizon_targets[..., :CONTINUOUS_TARGET_COUNT]
        continuous_mask = (
            batch.horizon_mask[..., :CONTINUOUS_TARGET_COUNT]
            & batch.origin_mask[:, :, None, None]
        )
        return_target = continuous_target[..., :RETURN_TARGET_COUNT]
        return_mask = continuous_mask[..., :RETURN_TARGET_COUNT]
        median_index = min(range(len(self.quantiles)), key=lambda index: abs(self.quantiles[index] - 0.5))
        return_prediction = output.horizon_quantiles[..., :RETURN_TARGET_COUNT, median_index].detach()
        predicted_percent = transformed_return_to_percent(return_prediction)
        target_percent = transformed_return_to_percent(return_target)
        error_sum = torch.where(
            return_mask, (predicted_percent - target_percent).abs(), torch.zeros_like(target_percent)
        ).sum((0, 1)).cpu()
        count = return_mask.sum((0, 1)).double().cpu()
        self.return_abs_error_percent = error_sum if self.return_abs_error_percent is None else self.return_abs_error_percent + error_sum
        self.return_count = count if self.return_count is None else self.return_count + count
        zero_error = torch.where(return_mask, target_percent.abs(), torch.zeros_like(target_percent)).sum((0, 1)).cpu()
        self.zero_baseline_abs_error_percent = zero_error if self.zero_baseline_abs_error_percent is None else self.zero_baseline_abs_error_percent + zero_error

        # The v12 model input stores current one-second close returns, providing
        # the historical continuation baseline without requiring raw prices.
        from research.bar_gpt.v2.features import MODEL_FEATURE_NAMES
        return_indices = [
            MODEL_FEATURE_NAMES.index(f"{family}_close_return")
            for family in PRICE_FAMILIES for _field in OHLC_FIELDS
        ]
        rows = torch.arange(batch.origin_indices.shape[0], device=batch.origin_indices.device)[:, None]
        current = batch.views["1s"][rows, batch.origin_indices.long()][..., return_indices]
        continuation = transformed_return_to_percent(current)
        continuation_error = torch.where(
            return_mask,
            (continuation[:, :, None, :] - target_percent).abs(),
            torch.zeros_like(target_percent),
        ).sum((0, 1)).cpu()
        self.continuation_abs_error_percent = continuation_error if self.continuation_abs_error_percent is None else self.continuation_abs_error_percent + continuation_error

        confusion = torch.stack([
            _confusion(
                physical_return_class_labels(return_target, batch.horizons_us)[:, :, horizon_index],
                output.horizon_return_class_logits.detach().argmax(dim=-1)[:, :, horizon_index],
                return_mask[:, :, horizon_index],
            )
            for horizon_index in range(len(self.horizons_us))
        ])
        self.horizon_return_confusion = confusion if self.horizon_return_confusion is None else self.horizon_return_confusion + confusion

        endpoint_quantiles = output.horizon_quantiles[..., :RETURN_TARGET_COUNT, :].detach()
        hits = ((return_target[..., None] <= endpoint_quantiles) & return_mask[..., None]).sum((0, 1)).double().cpu()
        coverage_count = count[..., None].expand_as(hits)
        self.coverage_hits = hits if self.coverage_hits is None else self.coverage_hits + hits
        self.coverage_count = coverage_count if self.coverage_count is None else self.coverage_count + coverage_count

        binary_target = batch.horizon_targets[..., CONTINUOUS_TARGET_COUNT:]
        binary_mask = (
            batch.horizon_mask[..., CONTINUOUS_TARGET_COUNT:]
            & batch.origin_mask[:, :, None, None]
        )
        probability = output.horizon_availability_logits.detach().sigmoid()
        brier_sum = torch.where(
            binary_mask, (probability - binary_target).square(), torch.zeros_like(probability)
        ).sum((0, 1)).double().cpu()
        binary_count = binary_mask.sum((0, 1)).double().cpu()
        self.binary_brier_sum = brier_sum if self.binary_brier_sum is None else self.binary_brier_sum + brier_sum
        self.binary_count = binary_count if self.binary_count is None else self.binary_count + binary_count

        if self.include_ranking_metrics:
            for horizon_index in range(len(self.horizons_us)):
                for target_index in range(RETURN_TARGET_COUNT):
                    selected = return_mask[:, :, horizon_index, target_index]
                    if torch.any(selected):
                        key = (target_index, horizon_index)
                        self.endpoint_scores.setdefault(key, []).append(predicted_percent[:, :, horizon_index, target_index][selected].float().cpu())
                        self.endpoint_targets.setdefault(key, []).append(target_percent[:, :, horizon_index, target_index][selected].float().cpu())

        if self.include_condition_metrics:
            for condition_index in range(len(CONDITION_TARGET_NAMES)):
                channel = binary_target.shape[-1] - len(CONDITION_TARGET_NAMES) + condition_index
                for horizon_index in range(len(self.horizons_us)):
                    selected = binary_mask[:, :, horizon_index, channel]
                    if torch.any(selected):
                        key = (condition_index, horizon_index)
                        self.condition_scores.setdefault(key, []).append(probability[:, :, horizon_index, channel][selected].float().cpu())
                        self.condition_targets.setdefault(key, []).append(binary_target[:, :, horizon_index, channel][selected].bool().cpu())

    def _finalize_losses(self, metrics: dict[str, float]) -> None:
        groups = {
            "ar_regression": [], "ar_categorical": [], "ar_return_class": [],
            "horizon_regression": [], "horizon_categorical": [], "horizon_return_class": [],
        }
        for suffix, numerator in self.loss_numerators.items():
            denominator = self.loss_denominators[suffix]
            value = numerator / denominator if denominator > 0 else float("nan")
            metric_group = "other"
            metric_target = suffix
            for group in groups:
                if suffix.startswith(group + "_"):
                    metric_group = group
                    metric_target = suffix.removeprefix(group + "_")
                    if math.isfinite(value):
                        groups[group].append(value)
                    break
            metrics[f"{self.namespace}_loss_{metric_group}/{metric_target}"] = value
            metrics[f"{self.namespace}_support_{metric_group}/{metric_target}"] = denominator
        group_sums = {name: float(sum(values)) for name, values in groups.items()}
        ar = group_sums["ar_regression"] + group_sums["ar_categorical"] + group_sums["ar_return_class"]
        horizon = group_sums["horizon_regression"] + group_sums["horizon_categorical"] + group_sums["horizon_return_class"]
        metrics.update({
            f"{self.namespace}_loss/ar_regression": group_sums["ar_regression"],
            f"{self.namespace}_loss/ar_categorical": group_sums["ar_categorical"],
            f"{self.namespace}_loss/ar_return_class": group_sums["ar_return_class"],
            f"{self.namespace}_loss/horizon_quantile": group_sums["horizon_regression"],
            f"{self.namespace}_loss/horizon_categorical": group_sums["horizon_categorical"],
            f"{self.namespace}_loss/horizon_return_class": group_sums["horizon_return_class"],
            f"{self.namespace}_loss/autoregressive": ar,
            f"{self.namespace}_loss/horizon": horizon,
            f"{self.namespace}_loss/total": ar + horizon,
        })

    def finalize(self) -> dict[str, float]:
        if self.batches == 0:
            raise RuntimeError("metric accumulator produced no batches")
        metrics: dict[str, float] = {
            f"{self.namespace}_data/origins": float(self.origins),
            f"{self.namespace}_data/batches": float(self.batches),
        }
        self._finalize_losses(metrics)

        ar_summary: dict[str, list[float]] = {name: [] for name in ("accuracy", "balanced_accuracy", "macro_f1", "mcc", "class_distance")}
        ar_close_summary: dict[str, list[float]] = {name: [] for name in ar_summary}
        for view in sorted(self.autoregressive_return_confusion):
            by_target = self.autoregressive_return_confusion[view]
            for target_index, target_name in enumerate(RETURN_TARGET_NAMES):
                scores = multiclass_scores(by_target[target_index])
                prefix = f"{self.namespace}_ar_{target_name}_class_{view}"
                for metric_name, value in zip(ar_summary, scores, strict=True):
                    metrics[f"{prefix}/{metric_name}"] = value
                    ar_summary[metric_name].append(value)
                    if "_close_" in target_name:
                        ar_close_summary[metric_name].append(value)
                actual, predicted = _class_support(by_target[target_index])
                metrics[f"{prefix}_support/count"] = float(by_target[target_index].sum())
                metrics[f"{prefix}_support/active_actual_classes"] = float(
                    (by_target[target_index].sum(dim=1) > 0).sum()
                )
                for class_index, class_name in enumerate(RETURN_CLASS_NAMES):
                    metrics[f"{prefix}_support/{class_name}"] = float(
                        by_target[target_index, class_index].sum()
                    )
                    metrics[f"{prefix}_actual_fraction/{class_name}"] = float(actual[class_index])
                    metrics[f"{prefix}_predicted_fraction/{class_name}"] = float(predicted[class_index])
        for metric_name, values in ar_summary.items():
            metrics[f"{self.namespace}_ar_return_class_summary/{metric_name}_macro"] = _macro(values)
            metrics[f"{self.namespace}_ar_close_return_class_summary/{metric_name}_macro"] = _macro(ar_close_summary[metric_name])

        if self.return_abs_error_percent is None or self.return_count is None or self.horizon_return_confusion is None:
            return metrics
        count = self.return_count
        mae = torch.where(count > 0, self.return_abs_error_percent / count, torch.full_like(count, float("nan")))
        zero_mae = torch.where(count > 0, self.zero_baseline_abs_error_percent / count, torch.full_like(count, float("nan")))
        continuation_mae = torch.where(count > 0, self.continuation_abs_error_percent / count, torch.full_like(count, float("nan")))
        coverage = torch.where(self.coverage_count > 0, self.coverage_hits / self.coverage_count, torch.full_like(self.coverage_hits, float("nan")))

        close_summary: dict[str, list[float]] = {name: [] for name in ("accuracy", "balanced_accuracy", "macro_f1", "mcc", "class_distance")}
        family_values: dict[str, dict[str, list[float]]] = {
            family: {"mae_percent": [], "rank": [], "calibration": [], **{name: [] for name in close_summary}}
            for family in PRICE_FAMILIES
        }
        for horizon_index, horizon_us in enumerate(self.horizons_us):
            horizon = f"{horizon_us // 1_000_000}s"
            for target_index, target_name in enumerate(RETURN_TARGET_NAMES):
                family, field, _return = target_name.split("_", 2)
                prefix = f"{self.namespace}_{family}_{field}"
                value = float(mae[horizon_index, target_index])
                zero = float(zero_mae[horizon_index, target_index])
                continuation = float(continuation_mae[horizon_index, target_index])
                metrics[f"{prefix}_return_error/mae_percent_{horizon}"] = value
                metrics[f"{prefix}_return_error/mae_bps_{horizon}"] = value * 100.0
                metrics[f"{prefix}_return_baseline/zero_mae_percent_{horizon}"] = zero
                metrics[f"{prefix}_return_baseline/continuation_mae_percent_{horizon}"] = continuation
                metrics[f"{prefix}_return_skill/skill_vs_zero_{horizon}"] = 1.0 - value / zero if zero > 0 else float("nan")
                metrics[f"{prefix}_return_skill/skill_vs_continuation_{horizon}"] = 1.0 - value / continuation if continuation > 0 else float("nan")
                family_values[family]["mae_percent"].append(value)
                scores = multiclass_scores(self.horizon_return_confusion[horizon_index, target_index])
                class_prefix = f"{prefix}_return_class_{horizon}"
                for metric_name, score in zip(close_summary, scores, strict=True):
                    metrics[f"{class_prefix}/{metric_name}"] = score
                    if field == "close":
                        close_summary[metric_name].append(score)
                        family_values[family][metric_name].append(score)
                actual, predicted = _class_support(self.horizon_return_confusion[horizon_index, target_index])
                metrics[f"{class_prefix}_support/count"] = float(self.horizon_return_confusion[horizon_index, target_index].sum())
                metrics[f"{class_prefix}_support/active_actual_classes"] = float(
                    (self.horizon_return_confusion[horizon_index, target_index].sum(dim=1) > 0).sum()
                )
                for class_index, class_name in enumerate(RETURN_CLASS_NAMES):
                    metrics[f"{class_prefix}_support/{class_name}"] = float(
                        self.horizon_return_confusion[horizon_index, target_index, class_index].sum()
                    )
                    metrics[f"{class_prefix}_actual_fraction/{class_name}"] = float(actual[class_index])
                    metrics[f"{class_prefix}_predicted_fraction/{class_name}"] = float(predicted[class_index])
                calibration_errors = []
                for quantile_index, quantile in enumerate(self.quantiles):
                    observed = float(coverage[horizon_index, target_index, quantile_index])
                    quantile_name = f"q{int(round(quantile * 100)):02d}"
                    metrics[f"{prefix}_coverage_{quantile_name}/{horizon}"] = observed
                    calibration_errors.append(abs(observed - quantile))
                calibration = _macro(calibration_errors)
                metrics[f"{prefix}_calibration/error_{horizon}"] = calibration
                family_values[family]["calibration"].append(calibration)
                key = (target_index, horizon_index)
                if key in self.endpoint_scores:
                    rank = _spearman(torch.cat(self.endpoint_scores[key]), torch.cat(self.endpoint_targets[key]))
                    metrics[f"{prefix}_ranking/spearman_{horizon}"] = rank
                    family_values[family]["rank"].append(rank)
        for metric_name, values in close_summary.items():
            metrics[f"{self.namespace}_close_return_class_summary/{metric_name}_macro"] = _macro(values)
        for family, values in family_values.items():
            metrics[f"{self.namespace}_{family}_summary/mae_percent_macro"] = _macro(values["mae_percent"])
            metrics[f"{self.namespace}_{family}_summary/mae_bps_macro"] = _macro(values["mae_percent"]) * 100.0
            metrics[f"{self.namespace}_{family}_summary/rank_macro"] = _macro(values["rank"])
            metrics[f"{self.namespace}_{family}_summary/calibration_macro"] = _macro(values["calibration"])
            for metric_name in close_summary:
                metrics[f"{self.namespace}_{family}_close_return_class_summary/{metric_name}_macro"] = _macro(values[metric_name])

        if self.binary_brier_sum is not None and self.binary_count is not None:
            brier_values = []
            for horizon_index, horizon_us in enumerate(self.horizons_us):
                horizon = f"{horizon_us // 1_000_000}s"
                for target_index, target_name in enumerate(BINARY_TARGET_NAMES):
                    support = float(self.binary_count[horizon_index, target_index])
                    value = (
                        float(self.binary_brier_sum[horizon_index, target_index]) / support
                        if support > 0 else float("nan")
                    )
                    metrics[f"{self.namespace}_{target_name}_brier/brier_{horizon}"] = value
                    metrics[f"{self.namespace}_{target_name}_brier/support_{horizon}"] = support
                    brier_values.append(value)
            metrics[f"{self.namespace}_availability/brier_macro"] = _macro(brier_values)

        if self.include_condition_metrics:
            for condition_index, condition_name in enumerate(CONDITION_TARGET_NAMES):
                group = condition_name.removesuffix("_within_horizon")
                average_precisions = []
                for horizon_index, horizon_us in enumerate(self.horizons_us):
                    key = (condition_index, horizon_index)
                    if key not in self.condition_scores:
                        continue
                    scores = torch.cat(self.condition_scores[key])
                    targets = torch.cat(self.condition_targets[key])
                    horizon = f"{horizon_us // 1_000_000}s"
                    total = int(targets.numel())
                    positives = int(targets.sum())
                    metrics[f"{self.namespace}_condition_{group}/total_{horizon}"] = float(total)
                    metrics[f"{self.namespace}_condition_{group}/positives_{horizon}"] = float(positives)
                    metrics[f"{self.namespace}_condition_{group}/prevalence_{horizon}"] = positives / total if total else float("nan")
                    value = _average_precision(scores, targets)
                    metrics[f"{self.namespace}_condition_{group}/average_precision_{horizon}"] = value
                    average_precisions.append(value)
                metrics[f"{self.namespace}_condition_{group}/average_precision_macro"] = _macro(average_precisions)
        return metrics
