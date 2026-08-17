from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import torch

from research.bar_gpt.v3 import LEARNING_CONTRACT, assert_checkpoint_version
from research.bar_gpt.v3.config import BarGPTConfig, DataConfig, ExperimentConfig, TrainConfig
from research.bar_gpt.v3.inference import _install_pathlib_pickle_compat
from research.bar_gpt.v3.metrics import (
    CLOSE_RETURN_TARGET_INDICES,
    ValidationAccumulator,
    multiclass_scores,
)
from research.bar_gpt.v3.model import BarGPTV3
from research.bar_gpt.v3.model_discovery import (
    DISCOVERY_CONTRACT_VERSION,
    discovery_shard_compatibility_hash,
)
from research.bar_gpt.v3.offline_shards import (
    OFFLINE_SHARD_CONTRACT_VERSION,
    OfflineBlockRef,
    collate_compiled_blocks,
    load_shard,
    materialize_block,
)
from research.bar_gpt.v3.train import _amp_dtype, _forward
from research.bar_gpt.v3.targets import (
    CONTINUOUS_TARGET_COUNT,
    RETURN_CLASS_NAMES,
    RETURN_TARGET_COUNT,
    RETURN_TARGET_NAMES,
    transformed_return_to_percent,
)


GROUPED_EVALUATION_CONTRACT = "bar_gpt_v3_grouped_shard_evaluation_v1"
ACTIVITY_LABELS = {0: "sparse", 1: "moderate", 2: "active"}


@dataclass(slots=True)
class ReturnDiagnosticAccumulator:
    horizons_us: tuple[int, ...]
    quantiles: tuple[float, ...]
    count: torch.Tensor | None = None
    prediction_sum: torch.Tensor | None = None
    target_sum: torch.Tensor | None = None
    prediction_square_sum: torch.Tensor | None = None
    target_square_sum: torch.Tensor | None = None
    cross_sum: torch.Tensor | None = None
    absolute_error_sum: torch.Tensor | None = None
    squared_error_sum: torch.Tensor | None = None
    absolute_target_sum: torch.Tensor | None = None

    def _add(self, name: str, value: torch.Tensor) -> None:
        value = value.double().cpu()
        current = getattr(self, name)
        setattr(self, name, value if current is None else current + value)

    def update(self, output: Any, batch: Any) -> None:
        if output.horizon_quantiles is None:
            return
        if batch.horizon_targets is None or batch.horizon_mask is None:
            raise RuntimeError("return diagnostics require physical-horizon targets")
        target = batch.horizon_targets[..., :CONTINUOUS_TARGET_COUNT][..., :RETURN_TARGET_COUNT]
        mask = (
            batch.horizon_mask[..., :CONTINUOUS_TARGET_COUNT][..., :RETURN_TARGET_COUNT]
            & batch.origin_mask[:, :, None, None]
        )
        median_index = min(
            range(len(self.quantiles)),
            key=lambda index: abs(self.quantiles[index] - 0.5),
        )
        prediction = output.horizon_quantiles[..., :RETURN_TARGET_COUNT, median_index]
        prediction = transformed_return_to_percent(prediction.detach())
        target = transformed_return_to_percent(target)
        prediction = torch.where(mask, prediction, torch.zeros_like(prediction))
        target = torch.where(mask, target, torch.zeros_like(target))
        dimensions = (0, 1)
        error = prediction - target
        self._add("count", mask.sum(dimensions))
        self._add("prediction_sum", prediction.sum(dimensions))
        self._add("target_sum", target.sum(dimensions))
        self._add("prediction_square_sum", prediction.square().sum(dimensions))
        self._add("target_square_sum", target.square().sum(dimensions))
        self._add("cross_sum", (prediction * target).sum(dimensions))
        self._add("absolute_error_sum", error.abs().sum(dimensions))
        self._add("squared_error_sum", error.square().sum(dimensions))
        self._add("absolute_target_sum", target.abs().sum(dimensions))

    def finalize(self) -> dict[str, Any]:
        if self.count is None:
            return {}
        assert self.prediction_sum is not None and self.target_sum is not None
        assert self.prediction_square_sum is not None and self.target_square_sum is not None
        assert self.cross_sum is not None and self.absolute_error_sum is not None
        assert self.squared_error_sum is not None and self.absolute_target_sum is not None
        result: dict[str, Any] = {}
        macro: dict[str, list[float]] = defaultdict(list)
        for horizon_index, horizon_us in enumerate(self.horizons_us):
            horizon = f"{horizon_us // 1_000_000}s"
            for target_index, target_name in enumerate(RETURN_TARGET_NAMES):
                count = float(self.count[horizon_index, target_index])
                if count <= 0:
                    continue
                prediction_sum = float(self.prediction_sum[horizon_index, target_index])
                target_sum = float(self.target_sum[horizon_index, target_index])
                prediction_square_sum = float(
                    self.prediction_square_sum[horizon_index, target_index]
                )
                target_square_sum = float(self.target_square_sum[horizon_index, target_index])
                cross_sum = float(self.cross_sum[horizon_index, target_index])
                mae_bps = float(self.absolute_error_sum[horizon_index, target_index]) / count * 100.0
                rmse_bps = math.sqrt(
                    float(self.squared_error_sum[horizon_index, target_index]) / count
                ) * 100.0
                zero_mae_bps = (
                    float(self.absolute_target_sum[horizon_index, target_index]) / count * 100.0
                )
                covariance = count * cross_sum - prediction_sum * target_sum
                prediction_variance = max(
                    0.0, count * prediction_square_sum - prediction_sum * prediction_sum
                )
                target_variance = max(
                    0.0, count * target_square_sum - target_sum * target_sum
                )
                correlation_denominator = math.sqrt(prediction_variance * target_variance)
                correlation = (
                    covariance / correlation_denominator
                    if correlation_denominator > 0
                    else float("nan")
                )
                diagnostic = {
                    "support": int(count),
                    "mae_bps": mae_bps,
                    "rmse_bps": rmse_bps,
                    "zero_baseline_mae_bps": zero_mae_bps,
                    "mae_improvement_vs_zero": (
                        1.0 - mae_bps / zero_mae_bps
                        if zero_mae_bps > 0
                        else float("nan")
                    ),
                    "mean_prediction_bps": prediction_sum / count * 100.0,
                    "mean_target_bps": target_sum / count * 100.0,
                    "mean_bias_bps": (prediction_sum - target_sum) / count * 100.0,
                    "correlation": correlation,
                }
                result[f"{target_name}/{horizon}"] = diagnostic
                for name, value in diagnostic.items():
                    if name != "support" and math.isfinite(float(value)):
                        macro[name].append(float(value))
        result["macro"] = {
            name: float(sum(values) / len(values))
            for name, values in sorted(macro.items())
            if values
        }
        total_count = float(self.count.sum())
        total_prediction = float(self.prediction_sum.sum())
        total_target = float(self.target_sum.sum())
        total_absolute_error = float(self.absolute_error_sum.sum())
        total_squared_error = float(self.squared_error_sum.sum())
        total_absolute_target = float(self.absolute_target_sum.sum())
        if total_count > 0:
            weighted_mae = total_absolute_error / total_count * 100.0
            weighted_zero_mae = total_absolute_target / total_count * 100.0
            result["support_weighted"] = {
                "support": int(total_count),
                "mae_bps": weighted_mae,
                "rmse_bps": math.sqrt(total_squared_error / total_count) * 100.0,
                "zero_baseline_mae_bps": weighted_zero_mae,
                "mae_improvement_vs_zero": (
                    1.0 - weighted_mae / weighted_zero_mae
                    if weighted_zero_mae > 0
                    else float("nan")
                ),
                "mean_prediction_bps": total_prediction / total_count * 100.0,
                "mean_target_bps": total_target / total_count * 100.0,
                "mean_bias_bps": (total_prediction - total_target) / total_count * 100.0,
            }
        return result


def classification_diagnostics(accumulator: ValidationAccumulator) -> dict[str, Any]:
    def scores(matrix: torch.Tensor) -> dict[str, Any]:
        accuracy, balanced, macro_f1, mcc, ordinal_error = multiclass_scores(matrix)
        return {
            "accuracy": accuracy,
            "balanced_accuracy": balanced,
            "macro_f1": macro_f1,
            "mcc": mcc,
            "ordinal_class_error": ordinal_error,
            "support": int(matrix.sum()),
            "class_support": {
                name: int(matrix[index].sum())
                for index, name in enumerate(RETURN_CLASS_NAMES)
            },
        }

    physical: dict[str, Any] = {}
    physical_macro: dict[str, list[float]] = defaultdict(list)
    physical_total: torch.Tensor | None = None
    if accumulator.horizon_return_confusion is not None:
        for horizon_index, horizon_us in enumerate(accumulator.horizons_us):
            horizon = f"{horizon_us // 1_000_000}s"
            for close_index, target_index in enumerate(CLOSE_RETURN_TARGET_INDICES):
                name = RETURN_TARGET_NAMES[target_index]
                matrix = accumulator.horizon_return_confusion[horizon_index, close_index]
                value = scores(matrix)
                physical_total = matrix.clone() if physical_total is None else physical_total + matrix
                physical[f"{name}/{horizon}"] = value
                for metric in ("accuracy", "balanced_accuracy", "macro_f1", "mcc", "ordinal_class_error"):
                    if math.isfinite(float(value[metric])):
                        physical_macro[metric].append(float(value[metric]))
    autoregressive: dict[str, Any] = {}
    autoregressive_macro: dict[str, list[float]] = defaultdict(list)
    autoregressive_total: torch.Tensor | None = None
    for view, matrices in sorted(accumulator.autoregressive_return_confusion.items()):
        for close_index, target_index in enumerate(CLOSE_RETURN_TARGET_INDICES):
            name = RETURN_TARGET_NAMES[target_index]
            matrix = matrices[close_index]
            value = scores(matrix)
            autoregressive_total = (
                matrix.clone()
                if autoregressive_total is None
                else autoregressive_total + matrix
            )
            autoregressive[f"{name}/{view}"] = value
            for metric in ("accuracy", "balanced_accuracy", "macro_f1", "mcc", "ordinal_class_error"):
                if math.isfinite(float(value[metric])):
                    autoregressive_macro[metric].append(float(value[metric]))
    return {
        "physical_close": physical,
        "physical_close_macro": {
            name: float(sum(values) / len(values))
            for name, values in sorted(physical_macro.items())
            if values
        },
        "physical_close_support_weighted": (
            scores(physical_total) if physical_total is not None else {}
        ),
        "autoregressive_close": autoregressive,
        "autoregressive_close_macro": {
            name: float(sum(values) / len(values))
            for name, values in sorted(autoregressive_macro.items())
            if values
        },
        "autoregressive_close_support_weighted": (
            scores(autoregressive_total) if autoregressive_total is not None else {}
        ),
    }


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate one local BarGPT v3 checkpoint on an exact subset of the "
            "certified validation panel and report support-weighted grouped metrics."
        )
    )
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--experiment-manifest", required=True)
    parser.add_argument("--shard-root", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--run-name", required=True)
    parser.add_argument(
        "--units",
        required=True,
        help="comma-separated unique ticker-month unit keys; at least one is required",
    )
    parser.add_argument("--panel", choices=("validation",), default="validation")
    parser.add_argument(
        "--ticker-metadata",
        default="",
        help=(
            "optional JSON object keyed by TICKER|YYYY-MM-DD for point-in-time fields; "
            "ticker-only keys may contain static fields such as instrument_type"
        ),
    )
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    return parser.parse_args(list(argv) if argv is not None else None)


def _canonical_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def load_portable_manifest(path: Path, *, data_config: DataConfig) -> dict[str, Any]:
    """Validate a copied discovery manifest without requiring its original root."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if int(value.get("contract_version", -1)) != DISCOVERY_CONTRACT_VERSION:
        raise RuntimeError("unsupported BarGPT discovery manifest contract")
    stored_hash = str(value.get("manifest_hash", ""))
    unsigned = dict(value)
    unsigned.pop("manifest_hash", None)
    if stored_hash != _canonical_hash(unsigned):
        raise RuntimeError("discovery manifest content hash mismatch")
    observed_config_hash = discovery_shard_compatibility_hash(data_config)
    if str(value.get("shard_config_hash", "")) != observed_config_hash:
        raise RuntimeError("checkpoint data config is incompatible with the panel manifest")
    return value


def parse_units(value: str) -> tuple[str, ...]:
    units = tuple(item.strip().upper() for item in value.split(",") if item.strip())
    if not units:
        raise ValueError("grouped evaluation requires at least one unit key")
    if len(set(units)) != len(units):
        raise ValueError("grouped evaluation requires unique unit keys")
    for unit in units:
        ticker, separator, year_month = unit.partition(":")
        if not separator or len(year_month) != 7 or year_month[4] != "-":
            raise ValueError(f"invalid ticker-month unit key: {unit!r}")
        if not ticker or not year_month[:4].isdigit() or not year_month[5:].isdigit():
            raise ValueError(f"invalid ticker-month unit key: {unit!r}")
    return units


def select_panel_refs(
    manifest: Mapping[str, Any], *, panel: str, units: Sequence[str]
) -> tuple[OfflineBlockRef, ...]:
    rows = manifest.get("panels", {}).get(panel)
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(f"manifest panel {panel!r} is absent or empty")
    selected = tuple(
        OfflineBlockRef(**row) for row in rows if str(row.get("unit_key", "")) in units
    )
    represented = {ref.unit_key for ref in selected}
    missing = sorted(set(units) - represented)
    if missing:
        raise RuntimeError(f"selected units are absent from {panel}: {missing}")
    return selected


def shard_path(root: Path, unit_key: str) -> Path:
    ticker, year_month = unit_key.split(":", 1)
    return root / "tickers" / ticker / year_month[:4] / f"{year_month}.pt"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_certified_shard(root: Path, unit_key: str, *, config_hash: str) -> dict[str, Any]:
    path = shard_path(root, unit_key)
    sidecar_path = path.with_suffix(".json")
    if not path.is_file() or not sidecar_path.is_file():
        raise RuntimeError(f"local evaluation pack is missing {unit_key}: {path}")
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    if str(sidecar.get("unit_key", "")) != unit_key:
        raise RuntimeError(f"shard sidecar identity mismatch for {unit_key}")
    if str(sidecar.get("status", "")) != "complete":
        raise RuntimeError(f"shard sidecar is not complete for {unit_key}")
    if int(sidecar.get("contract_version", -1)) != OFFLINE_SHARD_CONTRACT_VERSION:
        raise RuntimeError(f"unsupported shard contract for {unit_key}")
    if str(sidecar.get("config_hash", "")) != config_hash:
        raise RuntimeError(f"shard config hash mismatch for {unit_key}")
    expected_sha256 = str(sidecar.get("sha256", ""))
    if len(expected_sha256) != 64:
        raise RuntimeError(f"shard sidecar has no certified SHA-256 for {unit_key}")
    return load_shard(path, verify_sha256=expected_sha256)


def load_ticker_metadata(path: str) -> dict[str, dict[str, str]]:
    if not path:
        return {}
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("ticker metadata must be a JSON object")
    result: dict[str, dict[str, str]] = {}
    point_in_time_fields = {"price_bucket", "market_cap_bucket"}
    for identity, fields in raw.items():
        if not isinstance(fields, dict):
            raise ValueError(f"ticker metadata for {identity!r} must be an object")
        normalized: dict[str, str] = {}
        for name, value in fields.items():
            if isinstance(value, (str, int, float, bool)) and str(value).strip():
                normalized[str(name)] = str(value)
        key = str(identity).upper()
        if "|" not in key and point_in_time_fields & set(normalized):
            raise ValueError(
                f"point-in-time metadata for {identity!r} requires TICKER|YYYY-MM-DD identity"
            )
        if "|" in key:
            ticker, local_date = key.split("|", 1)
            try:
                dt.date.fromisoformat(local_date)
            except ValueError as exc:
                raise ValueError(f"invalid point-in-time metadata date: {identity!r}") from exc
            if not ticker:
                raise ValueError(f"invalid point-in-time metadata identity: {identity!r}")
        result[key] = normalized
    return result


def group_labels(
    block: Any,
    *,
    ticker_metadata: Mapping[str, Mapping[str, str]],
) -> tuple[str, ...]:
    activity = ACTIVITY_LABELS.get(int(block.activity_regime), f"unknown_{block.activity_regime}")
    labels = [
        "overall/all",
        f"ticker/{block.ticker}",
        f"activity/{activity}",
        f"session_phase/{block.session_phase}",
    ]
    ticker = str(block.ticker).upper()
    resolved_metadata = {
        **ticker_metadata.get(ticker, {}),
        **ticker_metadata.get(f"{ticker}|{block.local_date}", {}),
    }
    for name, value in sorted(resolved_metadata.items()):
        labels.append(f"metadata_{name}/{value}")
    return tuple(labels)


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def _checkpoint_config(checkpoint: Mapping[str, Any]) -> ExperimentConfig:
    raw = checkpoint["config"]
    train_values = dict(raw["train"])
    train_values["output_root"] = Path(train_values["output_root"])
    return ExperimentConfig(
        model=BarGPTConfig(**raw["model"]),
        data=DataConfig(**raw["data"]),
        train=TrainConfig(**train_values),
    )


@torch.no_grad()
def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    checkpoint_path = Path(args.checkpoint)
    _install_pathlib_pickle_compat()
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    assert_checkpoint_version(checkpoint)
    config = _checkpoint_config(checkpoint)
    units = parse_units(str(args.units))
    metadata = load_ticker_metadata(str(args.ticker_metadata))
    manifest = load_portable_manifest(Path(args.experiment_manifest), data_config=config.data)
    refs = select_panel_refs(manifest, panel=str(args.panel), units=units)
    refs_by_unit: dict[str, list[OfflineBlockRef]] = defaultdict(list)
    for ref in refs:
        refs_by_unit[ref.unit_key].append(ref)

    requested_device = str(args.device)
    resolved_device = (
        ("cuda" if torch.cuda.is_available() else "cpu")
        if requested_device == "auto"
        else requested_device
    )
    device = torch.device(resolved_device)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA evaluation was requested but CUDA is unavailable")
    model = BarGPTV3(config.model).to(device)
    model.load_state_dict(checkpoint["model"], strict=True)
    model.eval()
    horizon_ids = torch.arange(len(config.data.horizons_us), device=device)
    accumulators: dict[str, ValidationAccumulator] = {}
    return_diagnostics: dict[str, ReturnDiagnosticAccumulator] = {}
    coverage: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"blocks": 0, "origins": 0, "tickers": set(), "ticker_dates": set()}
    )
    rows: list[dict[str, Any]] = []
    for unit_key in units:
        shard = load_certified_shard(
            Path(args.shard_root),
            unit_key,
            config_hash=str(manifest["shard_config_hash"]),
        )
        for ref in refs_by_unit[unit_key]:
            block = materialize_block(shard, ref.session_index, ref.block_index)
            if block.ticker != ref.ticker or block.local_date != ref.local_date:
                raise RuntimeError(f"materialized block identity mismatch for {unit_key}")
            batch = collate_compiled_blocks(
                [block],
                horizons_us=tuple(config.data.horizons_us),
                base_timeframe_us=int(config.data.base_timeframe_us),
            ).to(device, non_blocking=False)
            with torch.autocast(
                device_type=device.type,
                dtype=_amp_dtype(config.train.amp_dtype),
                enabled=config.train.amp and device.type == "cuda",
            ):
                output, result = _forward(
                    model,
                    batch,
                    config,
                    horizon_ids=horizon_ids,
                )
            labels = group_labels(
                block,
                ticker_metadata=metadata,
            )
            for label in labels:
                accumulator = accumulators.setdefault(
                    label,
                    ValidationAccumulator(
                        tuple(config.data.horizons_us),
                        tuple(config.model.quantiles),
                        namespace="evaluation",
                    ),
                )
                accumulator.update(output, batch, result)
                return_accumulator = return_diagnostics.setdefault(
                    label,
                    ReturnDiagnosticAccumulator(
                        tuple(config.data.horizons_us),
                        tuple(config.model.quantiles),
                    ),
                )
                return_accumulator.update(output, batch)
                item = coverage[label]
                item["blocks"] += 1
                item["origins"] += int(batch.origin_count)
                item["tickers"].add(block.ticker)
                item["ticker_dates"].add((block.ticker, block.local_date))
            rows.append(
                {
                    "unit_key": unit_key,
                    "ticker": block.ticker,
                    "local_date": block.local_date,
                    "session_index": int(ref.session_index),
                    "block_index": int(ref.block_index),
                    "origins": int(batch.origin_count),
                    "activity_regime": int(block.activity_regime),
                    "session_phase": block.session_phase,
                    "groups": labels,
                }
            )

    grouped: dict[str, Any] = {}
    for label, accumulator in sorted(accumulators.items()):
        item = coverage[label]
        grouped[label] = {
            "coverage": {
                "blocks": int(item["blocks"]),
                "origins": int(item["origins"]),
                "tickers": len(item["tickers"]),
                "ticker_dates": len(item["ticker_dates"]),
            },
            "metrics": accumulator.finalize(),
            "classification": classification_diagnostics(accumulator),
            "returns": return_diagnostics[label].finalize(),
        }
    ticker_metric_rows = [
        value["metrics"] for label, value in grouped.items() if label.startswith("ticker/")
    ]
    ticker_macro: dict[str, dict[str, float | int]] = {}
    if ticker_metric_rows:
        for metric in sorted(set.intersection(*(set(row) for row in ticker_metric_rows))):
            values = [float(row[metric]) for row in ticker_metric_rows]
            finite = [value for value in values if math.isfinite(value)]
            if finite:
                ticker_macro[metric] = {
                    "value": float(sum(finite) / len(finite)),
                    "tickers": len(finite),
                }
    return {
        "contract": GROUPED_EVALUATION_CONTRACT,
        "learning_contract": LEARNING_CONTRACT,
        "checkpoint": {
            "path": str(checkpoint_path),
            "sha256": _sha256(checkpoint_path),
            "bytes": checkpoint_path.stat().st_size,
            "mtime_ns": checkpoint_path.stat().st_mtime_ns,
            "samples_seen": int(checkpoint.get("samples_seen", 0)),
        },
        "manifest": {
            "path": str(args.experiment_manifest),
            "manifest_hash": manifest["manifest_hash"],
            "panel": str(args.panel),
            "selected_units": list(units),
            "selected_refs": len(refs),
            "selected_origins": sum(ref.origins for ref in refs),
        },
        "grouping": {
            "activity_regimes": ACTIVITY_LABELS,
            "ticker_metadata": metadata,
            "external_metadata_required": ["price_bucket", "market_cap_bucket"],
            "external_metadata_identity": "TICKER|YYYY-MM-DD",
            "external_metadata_reason": (
                "v12 shards retain stationary projected inputs, not absolute price or "
                "point-in-time shares outstanding"
            ),
        },
        "device": str(device),
        "groups": grouped,
        "ticker_macro": ticker_macro,
        "blocks": rows,
    }


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    result = evaluate(args)
    run_root = Path(args.output_root) / str(args.run_name)
    _atomic_json(run_root / "summary.json", result)
    print(f"Grouped evaluation complete: {run_root}", flush=True)
    overall = result["groups"]["overall/all"]
    print(
        "overall "
        f"origins={overall['coverage']['origins']:,} "
        f"loss={overall['metrics']['evaluation_loss/total']:.6f}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
