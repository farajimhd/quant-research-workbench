from __future__ import annotations

import hashlib
import json
import math
from typing import Any

import torch

from research.bar_gpt.v3.targets import (
    AUTOREGRESSIVE_BINARY_TARGET_NAMES,
    AUTOREGRESSIVE_CONTINUOUS_TARGET_NAMES,
    AVAILABILITY_TARGET_NAMES,
    CONTINUOUS_TARGET_NAMES,
    NEXT_EVENT_GAP_CLASS_NAMES,
    RETURN_CLASS_NAMES,
)

from .models import LoadedRelease, PreparedBatch


def decode_batch(release: LoadedRelease, batch: PreparedBatch, output: Any) -> list[dict[str, Any]]:
    quantiles = tuple(float(value) for value in release.model.config.quantiles)
    horizon_values = output.horizon_quantiles.detach().float().cpu()
    availability_values = output.horizon_availability_logits.detach().float().cpu()
    return_class_values = getattr(output, "horizon_return_class_logits", None)
    if return_class_values is not None:
        return_class_values = return_class_values.detach().float().cpu()
    results = []
    for batch_index, ticker in enumerate(batch.tickers):
        origin_us = batch.origins_us[batch_index]
        raw: dict[str, Any] = {
            "horizon_quantiles": horizon_values[batch_index, 0].tolist(),
            "horizon_availability_logits": availability_values[batch_index, 0].tolist(),
            "autoregressive": {},
        }
        if return_class_values is not None:
            raw["horizon_return_class_logits"] = return_class_values[batch_index, 0].tolist()
        fields: dict[str, float | int | str | bool | None] = {}
        physical = _physical_fields(
            release,
            ticker,
            batch.base_prices[batch_index],
            horizon_values[batch_index, 0],
            availability_values[batch_index, 0],
            return_class_values[batch_index, 0] if return_class_values is not None else None,
            quantiles,
        )
        fields.update(physical)
        for view, values in output.autoregressive.items():
            index = batch.real_lengths[view][batch_index] - 1
            raw_values = values.detach().float().cpu()[batch_index, index]
            raw["autoregressive"][view] = {"values": raw_values.tolist()}
            gap_logits = getattr(output, "autoregressive_gap_logits", {}).get(view)
            if gap_logits is not None:
                selected_gap = gap_logits.detach().float().cpu()[batch_index, index]
                raw["autoregressive"][view]["gap_logits"] = selected_gap.tolist()
                probability = torch.softmax(selected_gap, dim=-1)
                for label_index, label in enumerate(NEXT_EVENT_GAP_CLASS_NAMES):
                    fields[f"model.bargpt.{release.config.version}.next_bar.{view}.gap_logit.{label}"] = float(selected_gap[label_index])
                    fields[f"model.bargpt.{release.config.version}.next_bar.{view}.gap_probability.{label}"] = float(probability[label_index])
            class_logits = getattr(output, "autoregressive_return_class_logits", {}).get(view)
            selected_classes = None
            if class_logits is not None:
                selected_classes = class_logits.detach().float().cpu()[batch_index, index]
                raw["autoregressive"][view]["return_class_logits"] = selected_classes.tolist()
            fields.update(_autoregressive_fields(
                release.config.version,
                view,
                batch.base_prices[batch_index],
                raw_values,
                selected_classes,
            ))
        identity = {
            "ticker": ticker,
            "origin_us": origin_us,
            "model_id": release.config.model_id,
            "checkpoint_hash": release.checkpoint_hash,
            "raw": raw,
        }
        prediction_id = hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        results.append({
            "schema_version": 1,
            "prediction_id": prediction_id,
            "ticker": ticker,
            "event_at_us": origin_us,
            "available_at_us": origin_us,
            "model_id": release.config.model_id,
            "model_version": release.config.version,
            "checkpoint_hash": release.checkpoint_hash,
            "contract_hash": release.contract_hash,
            "fields": fields,
            "raw": raw,
            "base_prices": batch.base_prices[batch_index],
        })
    return results


def _physical_fields(
    release: LoadedRelease,
    ticker: str,
    bases: dict[str, float],
    quantile_values: torch.Tensor,
    availability_logits: torch.Tensor,
    return_class_logits: torch.Tensor | None,
    quantiles: tuple[float, ...],
) -> dict[str, float | None]:
    del ticker
    fields: dict[str, float | None] = {}
    for horizon_index, horizon_us in enumerate(release.data_config.horizons_us):
        horizon = _duration_label(int(horizon_us))
        for target_index, target in enumerate(CONTINUOUS_TARGET_NAMES):
            for quantile_index, quantile in enumerate(quantiles):
                raw_value = float(quantile_values[horizon_index, target_index, quantile_index])
                q_label = f"q{int(round(quantile * 100)):02d}"
                prefix = f"model.bargpt.{release.config.version}.physical.{horizon}.{target}.{q_label}"
                fields[f"{prefix}.raw"] = raw_value
                fields[f"{prefix}.value"] = _decode_continuous(target, raw_value, bases)
        probabilities = torch.sigmoid(availability_logits[horizon_index])
        for target_index, target in enumerate(AVAILABILITY_TARGET_NAMES):
            prefix = f"model.bargpt.{release.config.version}.physical.{horizon}.{target}"
            fields[f"{prefix}.logit"] = float(availability_logits[horizon_index, target_index])
            fields[f"{prefix}.probability"] = float(probabilities[target_index])
        if return_class_logits is not None:
            probabilities = torch.softmax(return_class_logits[horizon_index], dim=-1)
            for target_index, target in enumerate(CONTINUOUS_TARGET_NAMES[:12]):
                for class_index, label in enumerate(RETURN_CLASS_NAMES):
                    prefix = f"model.bargpt.{release.config.version}.physical.{horizon}.{target}.class_{label}"
                    fields[f"{prefix}.logit"] = float(return_class_logits[horizon_index, target_index, class_index])
                    fields[f"{prefix}.probability"] = float(probabilities[target_index, class_index])
    return fields


def _autoregressive_fields(
    version: str,
    view: str,
    bases: dict[str, float],
    values: torch.Tensor,
    return_class_logits: torch.Tensor | None,
) -> dict[str, float | None]:
    fields: dict[str, float | None] = {}
    continuous_count = len(AUTOREGRESSIVE_CONTINUOUS_TARGET_NAMES)
    for index, target in enumerate(AUTOREGRESSIVE_CONTINUOUS_TARGET_NAMES):
        raw_value = float(values[index])
        prefix = f"model.bargpt.{version}.next_bar.{view}.{target}"
        fields[f"{prefix}.raw"] = raw_value
        fields[f"{prefix}.value"] = _decode_continuous(target, raw_value, bases)
    for index, target in enumerate(AUTOREGRESSIVE_BINARY_TARGET_NAMES):
        logit = float(values[continuous_count + index])
        prefix = f"model.bargpt.{version}.next_bar.{view}.{target}"
        fields[f"{prefix}.logit"] = logit
        fields[f"{prefix}.probability"] = 1.0 / (1.0 + math.exp(-max(-80.0, min(80.0, logit))))
    if return_class_logits is not None:
        probabilities = torch.softmax(return_class_logits, dim=-1)
        for target_index, target in enumerate(AUTOREGRESSIVE_CONTINUOUS_TARGET_NAMES[:12]):
            for class_index, label in enumerate(RETURN_CLASS_NAMES):
                prefix = f"model.bargpt.{version}.next_bar.{view}.{target}.class_{label}"
                fields[f"{prefix}.logit"] = float(return_class_logits[target_index, class_index])
                fields[f"{prefix}.probability"] = float(probabilities[target_index, class_index])
    return fields


def _decode_continuous(target: str, raw: float, bases: dict[str, float]) -> float | None:
    if target.endswith("_return"):
        family = target.split("_", 1)[0]
        base = bases.get(family)
        return None if not base else base * math.exp(math.sinh(raw) / 100.0)
    if target == "trade_realized_volatility":
        return math.sinh(raw) / 100.0
    if target.startswith("log_"):
        return math.expm1(raw)
    return raw


def _duration_label(value_us: int) -> str:
    for unit, divisor in (("h", 3_600_000_000), ("m", 60_000_000), ("s", 1_000_000), ("ms", 1_000)):
        if value_us % divisor == 0:
            return f"{value_us // divisor}{unit}"
    return f"{value_us}us"
