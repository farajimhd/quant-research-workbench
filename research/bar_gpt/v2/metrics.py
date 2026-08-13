from __future__ import annotations

import math
from dataclasses import dataclass, field

import torch

from research.bar_gpt.v2.data import BarGPTBatch
from research.bar_gpt.v2.model import BarGPTOutput
from research.bar_gpt.v2.objectives import BarGPTLoss
from research.bar_gpt.v2.targets import (
    BINARY_TARGET_NAMES,
    CONTINUOUS_TARGET_COUNT,
    PRICE_FAMILIES,
    RETURN_CLASS_COUNT,
    RETURN_CLASS_NAMES,
    RETURN_TARGET_COUNT,
    RETURN_TARGET_NAMES,
    autoregressive_return_class_labels,
    physical_return_class_labels,
    transformed_return_to_percent,
)


CLOSE_RETURN_TARGET_INDICES = tuple(
    index for index, name in enumerate(RETURN_TARGET_NAMES) if "_close_" in name
)


def _macro(values: list[float]) -> float:
    finite = [value for value in values if math.isfinite(value)]
    return float(sum(finite) / len(finite)) if finite else float("nan")


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


@dataclass(slots=True)
class ValidationAccumulator:
    horizons_us: tuple[int, ...]
    quantiles: tuple[float, ...]
    namespace: str = "validation"
    include_loss_metrics: bool = True
    include_confidence_metrics: bool = True
    include_class_diagnostics: bool = False
    loss_numerators: dict[str, float] = field(default_factory=dict)
    loss_denominators: dict[str, float] = field(default_factory=dict)
    origins: int = 0
    batches: int = 0
    return_abs_error_percent: torch.Tensor | None = None
    return_count: torch.Tensor | None = None
    horizon_return_confusion: torch.Tensor | None = None
    autoregressive_return_confusion: dict[str, torch.Tensor] = field(default_factory=dict)
    binary_brier_sum: torch.Tensor | None = None
    binary_count: torch.Tensor | None = None
    coverage_hits: torch.Tensor | None = None
    coverage_count: torch.Tensor | None = None

    def _update_losses(self, result: BarGPTLoss) -> None:
        if not self.include_loss_metrics:
            return
        for suffix, (value, support_value) in result.target_stats.items():
            support = float(support_value)
            self.loss_numerators[suffix] = self.loss_numerators.get(suffix, 0.0) + float(value) * support
            self.loss_denominators[suffix] = self.loss_denominators.get(suffix, 0.0) + support

    def update(self, output: BarGPTOutput, batch: BarGPTBatch, result: BarGPTLoss) -> None:
        self.origins += int(batch.origin_count)
        self.batches += 1
        self._update_losses(result)

        for view, logits in output.autoregressive_return_class_logits.items():
            target = batch.autoregressive_targets[view][
                :, : logits.shape[1], :RETURN_TARGET_COUNT
            ][..., CLOSE_RETURN_TARGET_INDICES]
            valid = batch.autoregressive_mask[view][
                :, : logits.shape[1], :RETURN_TARGET_COUNT
            ][..., CLOSE_RETURN_TARGET_INDICES]
            confusion = _confusion(
                autoregressive_return_class_labels(target, view),
                logits.detach().argmax(dim=-1)[..., CLOSE_RETURN_TARGET_INDICES],
                valid,
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
        confusion = torch.stack([
            _confusion(
                physical_return_class_labels(return_target, batch.horizons_us)[
                    :, :, horizon_index, CLOSE_RETURN_TARGET_INDICES
                ],
                output.horizon_return_class_logits.detach().argmax(dim=-1)[
                    :, :, horizon_index, CLOSE_RETURN_TARGET_INDICES
                ],
                return_mask[:, :, horizon_index, CLOSE_RETURN_TARGET_INDICES],
            )
            for horizon_index in range(len(self.horizons_us))
        ])
        self.horizon_return_confusion = confusion if self.horizon_return_confusion is None else self.horizon_return_confusion + confusion

        if self.include_confidence_metrics:
            endpoint_quantiles = output.horizon_quantiles[..., :RETURN_TARGET_COUNT, :].detach()
            hits = (
                (return_target[..., None] <= endpoint_quantiles) & return_mask[..., None]
            ).sum((0, 1)).double().cpu()
            coverage_count = count[..., None].expand_as(hits)
            self.coverage_hits = hits if self.coverage_hits is None else self.coverage_hits + hits
            self.coverage_count = (
                coverage_count
                if self.coverage_count is None
                else self.coverage_count + coverage_count
            )

            binary_target = batch.horizon_targets[..., CONTINUOUS_TARGET_COUNT:]
            binary_mask = (
                batch.horizon_mask[..., CONTINUOUS_TARGET_COUNT:]
                & batch.origin_mask[:, :, None, None]
            )
            probability = output.horizon_availability_logits.detach().sigmoid()
            brier_sum = torch.where(
                binary_mask,
                (probability - binary_target).square(),
                torch.zeros_like(probability),
            ).sum((0, 1)).double().cpu()
            binary_count = binary_mask.sum((0, 1)).double().cpu()
            self.binary_brier_sum = (
                brier_sum if self.binary_brier_sum is None else self.binary_brier_sum + brier_sum
            )
            self.binary_count = (
                binary_count if self.binary_count is None else self.binary_count + binary_count
            )

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

        ar_close_summary: dict[str, list[float]] = {
            name: [] for name in ("balanced_accuracy", "mcc")
        }
        for view in sorted(self.autoregressive_return_confusion):
            by_target = self.autoregressive_return_confusion[view]
            for target_index, return_target_index in enumerate(CLOSE_RETURN_TARGET_INDICES):
                target_name = RETURN_TARGET_NAMES[return_target_index]
                scores = multiclass_scores(by_target[target_index])
                prefix = f"{self.namespace}_ar_{target_name}_class_{view}"
                for metric_name, value in (
                    ("balanced_accuracy", scores[1]),
                    ("mcc", scores[3]),
                ):
                    metrics[f"{prefix}/{metric_name}"] = value
                    ar_close_summary[metric_name].append(value)
                if self.include_class_diagnostics:
                    matrix = by_target[target_index]
                    metrics[f"{prefix}_support/count"] = float(matrix.sum())
                    for class_index, class_name in enumerate(RETURN_CLASS_NAMES):
                        metrics[f"{prefix}_support/{class_name}"] = float(
                            matrix[class_index].sum()
                        )
        for metric_name, values in ar_close_summary.items():
            metrics[f"{self.namespace}_ar_close_return_class_summary/{metric_name}_macro"] = _macro(ar_close_summary[metric_name])

        if self.return_abs_error_percent is None or self.return_count is None or self.horizon_return_confusion is None:
            return metrics
        count = self.return_count
        mae = torch.where(count > 0, self.return_abs_error_percent / count, torch.full_like(count, float("nan")))
        coverage = (
            torch.where(
                self.coverage_count > 0,
                self.coverage_hits / self.coverage_count,
                torch.full_like(self.coverage_hits, float("nan")),
            )
            if self.include_confidence_metrics
            else None
        )

        close_summary: dict[str, list[float]] = {
            name: [] for name in ("balanced_accuracy", "mcc")
        }
        family_values: dict[str, dict[str, list[float]]] = {
            family: {"mae_bps": [], "calibration": [], **{name: [] for name in close_summary}}
            for family in PRICE_FAMILIES
        }
        for horizon_index, horizon_us in enumerate(self.horizons_us):
            horizon = f"{horizon_us // 1_000_000}s"
            for target_index, target_name in enumerate(RETURN_TARGET_NAMES):
                family, field, _return = target_name.split("_", 2)
                prefix = f"{self.namespace}_{family}_{field}"
                value = float(mae[horizon_index, target_index])
                metrics[f"{prefix}_return_error/mae_bps_{horizon}"] = value * 100.0
                family_values[family]["mae_bps"].append(value * 100.0)
                if target_index in CLOSE_RETURN_TARGET_INDICES:
                    close_index = CLOSE_RETURN_TARGET_INDICES.index(target_index)
                    scores = multiclass_scores(
                        self.horizon_return_confusion[horizon_index, close_index]
                    )
                    class_prefix = f"{prefix}_return_class_{horizon}"
                    for metric_name, score in (
                        ("balanced_accuracy", scores[1]),
                        ("mcc", scores[3]),
                    ):
                        metrics[f"{class_prefix}/{metric_name}"] = score
                        close_summary[metric_name].append(score)
                        family_values[family][metric_name].append(score)
                    if self.include_class_diagnostics:
                        matrix = self.horizon_return_confusion[horizon_index, close_index]
                        metrics[f"{class_prefix}_support/count"] = float(matrix.sum())
                        for class_index, class_name in enumerate(RETURN_CLASS_NAMES):
                            metrics[f"{class_prefix}_support/{class_name}"] = float(
                                matrix[class_index].sum()
                            )
                if coverage is not None:
                    calibration = _macro([
                        abs(float(coverage[horizon_index, target_index, quantile_index]) - quantile)
                        for quantile_index, quantile in enumerate(self.quantiles)
                    ])
                    family_values[family]["calibration"].append(calibration)
        for metric_name, values in close_summary.items():
            metrics[f"{self.namespace}_close_return_class_summary/{metric_name}_macro"] = _macro(values)
        for family, values in family_values.items():
            metrics[f"{self.namespace}_{family}_summary/mae_bps_macro"] = _macro(values["mae_bps"])
            if self.include_confidence_metrics:
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
                    brier_values.append(value)
            metrics[f"{self.namespace}_availability/brier_macro"] = _macro(brier_values)
        return metrics
