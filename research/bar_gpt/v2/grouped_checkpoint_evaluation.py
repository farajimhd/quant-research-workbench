from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import torch

from research.bar_gpt.v2 import LEARNING_CONTRACT, assert_checkpoint_version
from research.bar_gpt.v2.config import BarGPTConfig, DataConfig, ExperimentConfig, TrainConfig
from research.bar_gpt.v2.inference import _install_pathlib_pickle_compat
from research.bar_gpt.v2.metrics import ValidationAccumulator
from research.bar_gpt.v2.model import BarGPTV2
from research.bar_gpt.v2.model_discovery import (
    DISCOVERY_CONTRACT_VERSION,
    discovery_shard_compatibility_hash,
)
from research.bar_gpt.v2.offline_shards import (
    OFFLINE_SHARD_CONTRACT_VERSION,
    OfflineBlockRef,
    collate_compiled_blocks,
    load_shard,
    materialize_block,
)
from research.bar_gpt.v2.train import _amp_dtype, _forward


GROUPED_EVALUATION_CONTRACT = "bar_gpt_v2_grouped_shard_evaluation_v1"
ACTIVITY_LABELS = {0: "sparse", 1: "moderate", 2: "active"}


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate one local BarGPT v2 checkpoint on an exact subset of the "
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
        help="comma-separated ticker-month unit keys; exactly five are required",
    )
    parser.add_argument("--panel", choices=("validation",), default="validation")
    parser.add_argument(
        "--ticker-metadata",
        default="",
        help=(
            "optional point-in-time JSON object keyed by ticker; scalar fields such as "
            "price_bucket or market_cap_bucket become metadata groups"
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


def parse_units(value: str, *, expected: int = 5) -> tuple[str, ...]:
    units = tuple(item.strip().upper() for item in value.split(",") if item.strip())
    if len(units) != expected or len(set(units)) != expected:
        raise ValueError(f"grouped evaluation requires exactly {expected} unique unit keys")
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
    for ticker, fields in raw.items():
        if not isinstance(fields, dict):
            raise ValueError(f"ticker metadata for {ticker!r} must be an object")
        normalized: dict[str, str] = {}
        for name, value in fields.items():
            if isinstance(value, (str, int, float, bool)) and str(value).strip():
                normalized[str(name)] = str(value)
        result[str(ticker).upper()] = normalized
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
    for name, value in sorted(ticker_metadata.get(str(block.ticker).upper(), {}).items()):
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
    model = BarGPTV2(config.model).to(device)
    model.load_state_dict(checkpoint["model"], strict=True)
    model.eval()
    horizon_ids = torch.arange(len(config.data.horizons_us), device=device)
    accumulators: dict[str, ValidationAccumulator] = {}
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
