from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import torch

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.env import discover_env_files, load_env_files
from research.temporal_event_model.v1.cache_probe import (
    DOWN_CLASS_NAMES,
    DEFAULT_CACHE_ROOT,
    DEFAULT_OUTPUT_ROOT,
    PATH_CLASS_NAMES,
    ProbeConfig,
    UP_CLASS_NAMES,
    autocast_context,
    build_frozen_encoder,
    build_probe_batch,
    discover_labeled_shards,
    load_shard_records,
    average_metric_sums,
    probe_loss_and_metrics,
    resolve_amp_dtype,
)
from research.temporal_event_model.v1.model import SingleChunkFutureLabelPredictor


DEFAULT_EVAL_OUTPUT_ROOT = DEFAULT_OUTPUT_ROOT / "_fixed_eval"
DEFAULT_EVAL_SEED = 20260621
DEFAULT_BATCH_SIZE = 1024
DEFAULT_BATCHES = 10


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_env_files(discover_env_files(REPO_ROOT, args.env_file), verbose=True)
    result = evaluate_checkpoints_from_args(args)
    print(json.dumps(result["summary"], indent=2), flush=True)
    print(f"Wrote evaluation JSON: {result['result_path']}", flush=True)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate trained temporal v1 cache-probe checkpoints on one fixed "
            "validation set so checkpoint comparisons are fair."
        )
    )
    parser.add_argument(
        "--checkpoint",
        action="append",
        default=[],
        help="Path to a trained temporal checkpoint. Repeat up to three times. Defaults to the three newest checkpoint_latest.pt files.",
    )
    parser.add_argument("--checkpoint-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--max-checkpoints", type=int, default=3)
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_CACHE_ROOT)
    parser.add_argument("--validation-split", default="train")
    parser.add_argument("--validation-start-shard", type=int, default=1)
    parser.add_argument("--validation-max-shards", type=int, default=1)
    parser.add_argument("--validation-batches", type=int, default=DEFAULT_BATCHES)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--seed", type=int, default=DEFAULT_EVAL_SEED)
    parser.add_argument("--amp-dtype", choices=("off", "fp16", "bf16"), default="bf16")
    parser.add_argument("--device", default="auto", choices=("auto", "cuda", "cpu"))
    parser.add_argument("--output-root", type=Path, default=DEFAULT_EVAL_OUTPUT_ROOT)
    parser.add_argument("--run-name", default="fixed-shard1-10x1024")
    parser.add_argument("--flat-threshold-bps", type=float, default=None)
    parser.add_argument("--strong-threshold-bps", type=float, default=None)
    parser.add_argument("--env-file", type=Path, default=None)
    return parser.parse_args(argv)


def evaluate_checkpoints_from_args(args: argparse.Namespace) -> dict[str, Any]:
    checkpoint_paths = [Path(value) for value in args.checkpoint]
    if not checkpoint_paths:
        checkpoint_paths = discover_latest_probe_checkpoints(args.checkpoint_root, max_checkpoints=args.max_checkpoints)
    if not checkpoint_paths:
        raise RuntimeError(f"No checkpoint_latest.pt files found under {args.checkpoint_root}")
    device = resolve_device(args.device)
    amp_dtype = resolve_amp_dtype(args.amp_dtype)
    output_dir = Path(args.output_root) / args.run_name
    output_dir.mkdir(parents=True, exist_ok=True)
    result = evaluate_checkpoints(
        checkpoint_paths=checkpoint_paths,
        cache_root=Path(args.cache_root),
        validation_split=str(args.validation_split),
        validation_start_shard=int(args.validation_start_shard),
        validation_max_shards=int(args.validation_max_shards),
        validation_batches=int(args.validation_batches),
        batch_size=int(args.batch_size),
        seed=int(args.seed),
        device=device,
        amp_dtype=amp_dtype,
        output_dir=output_dir,
        flat_threshold_bps=args.flat_threshold_bps,
        strong_threshold_bps=args.strong_threshold_bps,
    )
    return result


def discover_latest_probe_checkpoints(root: Path, *, max_checkpoints: int = 3) -> list[Path]:
    candidates = [path for path in Path(root).glob("*/checkpoints/checkpoint_latest.pt") if path.is_file()]
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[: max(1, int(max_checkpoints))]


def resolve_device(value: str) -> torch.device:
    if value == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if value == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("Requested --device cuda, but CUDA is not available.")
    return torch.device(value)


def evaluate_checkpoints(
    *,
    checkpoint_paths: list[Path],
    cache_root: Path,
    validation_split: str = "train",
    validation_start_shard: int = 1,
    validation_max_shards: int = 1,
    validation_batches: int = DEFAULT_BATCHES,
    batch_size: int = DEFAULT_BATCH_SIZE,
    seed: int = DEFAULT_EVAL_SEED,
    device: torch.device | None = None,
    amp_dtype: torch.dtype | None = torch.bfloat16,
    output_dir: Path | None = None,
    flat_threshold_bps: float | None = None,
    strong_threshold_bps: float | None = None,
) -> dict[str, Any]:
    device = device or torch.device("cuda" if torch.cuda.is_available() else "cpu")
    output_dir = Path(output_dir or (DEFAULT_EVAL_OUTPUT_ROOT / "fixed-shard1-10x1024"))
    output_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()

    first_config = config_from_checkpoint(load_checkpoint(checkpoint_paths[0]), checkpoint_paths[0])
    eval_config = replace_eval_config(
        first_config,
        cache_root=cache_root,
        batch_size=batch_size,
        validation_batches=validation_batches,
        seed=seed,
        flat_threshold_bps=flat_threshold_bps,
        strong_threshold_bps=strong_threshold_bps,
    )
    validation_shards = discover_labeled_shards(
        eval_config,
        split=validation_split,
        start=validation_start_shard,
        max_shards=validation_max_shards,
    )
    if len(validation_shards) != 1:
        raise RuntimeError(f"Expected exactly one fixed validation shard, got {len(validation_shards)}")
    validation_shard = validation_shards[0]
    validation_indices = fixed_validation_indices(
        validation_shard.num_samples,
        batch_size=batch_size,
        batches=validation_batches,
        seed=seed,
        shard_index=validation_shard.shard_index,
    )
    x_records, y_records = load_shard_records(validation_shard, preload=False, reporter=None, purpose="fixed_eval")

    run_results: list[dict[str, Any]] = []
    for checkpoint_path in checkpoint_paths:
        payload = load_checkpoint(checkpoint_path)
        config = config_from_checkpoint(payload, checkpoint_path)
        config = replace_eval_config(
            config,
            cache_root=cache_root,
            batch_size=batch_size,
            validation_batches=validation_batches,
            seed=seed,
            flat_threshold_bps=flat_threshold_bps,
            strong_threshold_bps=strong_threshold_bps,
        )
        run_results.append(
            evaluate_one_checkpoint(
                checkpoint_path=checkpoint_path,
                payload=payload,
                config=config,
                x_records=x_records,
                y_records=y_records,
                validation_indices=validation_indices,
                device=device,
                amp_dtype=amp_dtype,
            )
        )

    result = {
        "summary": [run["summary"] for run in run_results],
        "runs": run_results,
        "up_class_names": list(UP_CLASS_NAMES),
        "down_class_names": list(DOWN_CLASS_NAMES),
        "path_class_names": list(PATH_CLASS_NAMES),
        "validation": {
            "cache_root": str(cache_root),
            "split": validation_split,
            "shard_index": int(validation_shard.shard_index),
            "batch_size": int(batch_size),
            "batches": int(validation_batches),
            "samples": int(validation_indices.shape[0]),
            "seed": int(seed),
        },
        "elapsed_seconds": time.perf_counter() - started,
    }
    result_path = output_dir / "fixed_eval_results.json"
    result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    result["result_path"] = str(result_path)
    return result


def load_checkpoint(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    try:
        payload = torch.load(path, map_location="cpu", weights_only=False)
    except TypeError:
        payload = torch.load(path, map_location="cpu")
    if not isinstance(payload, dict):
        raise RuntimeError(f"Unsupported checkpoint payload in {path}")
    return payload


def config_from_checkpoint(payload: dict[str, Any], checkpoint_path: Path) -> ProbeConfig:
    raw_config = dict(payload.get("config") or {})
    if not raw_config:
        raise RuntimeError(f"Checkpoint has no saved ProbeConfig: {checkpoint_path}")
    field_names = set(ProbeConfig.__dataclass_fields__)
    filtered = {key: value for key, value in raw_config.items() if key in field_names}
    path_fields = {"cache_root", "encoder_checkpoint", "output_root"}
    for key in path_fields:
        if key in filtered:
            filtered[key] = Path(filtered[key])
    if "horizons" in filtered:
        filtered["horizons"] = tuple(int(value) for value in filtered["horizons"])
    return ProbeConfig(**filtered)


def replace_eval_config(
    config: ProbeConfig,
    *,
    cache_root: Path,
    batch_size: int,
    validation_batches: int,
    seed: int,
    flat_threshold_bps: float | None,
    strong_threshold_bps: float | None,
) -> ProbeConfig:
    payload = asdict(config)
    payload["cache_root"] = Path(cache_root)
    payload["batch_size"] = int(batch_size)
    payload["validation_batches"] = int(validation_batches)
    payload["seed"] = int(seed)
    payload["preload_shards_to_ram"] = False
    if flat_threshold_bps is not None:
        payload["flat_threshold_bps"] = float(flat_threshold_bps)
    if strong_threshold_bps is not None:
        payload["strong_threshold_bps"] = float(strong_threshold_bps)
    payload["horizons"] = tuple(int(value) for value in payload["horizons"])
    payload["encoder_checkpoint"] = Path(payload["encoder_checkpoint"])
    payload["output_root"] = Path(payload["output_root"])
    return ProbeConfig(**payload)


def fixed_validation_indices(num_samples: int, *, batch_size: int, batches: int, seed: int, shard_index: int) -> np.ndarray:
    required = int(batch_size) * int(batches)
    if num_samples < required:
        raise RuntimeError(f"Validation shard has {num_samples:,} samples, but fixed eval needs {required:,}.")
    rng = np.random.default_rng(int(seed) + int(shard_index) * 1_000_003)
    order = rng.permutation(int(num_samples))
    return order[:required].reshape(int(batches), int(batch_size))


def evaluate_one_checkpoint(
    *,
    checkpoint_path: Path,
    payload: dict[str, Any],
    config: ProbeConfig,
    x_records: np.ndarray,
    y_records: np.ndarray,
    validation_indices: np.ndarray,
    device: torch.device,
    amp_dtype: torch.dtype | None,
) -> dict[str, Any]:
    model = build_probe_model(payload, config, checkpoint_path, device)
    metric_sums: dict[str, float] = {}
    batches = 0
    started = time.perf_counter()
    with torch.no_grad():
        for batch_indices in validation_indices:
            batch = build_probe_batch(x_records, y_records, batch_indices, config, device)
            with autocast_context(device, amp_dtype):
                embedding = model.encode_chunk(batch["header_uint8"], batch["events_uint8"])
                low_high_tick_pred, up_class_logits, down_class_logits, path_class_logits = model.decode_embedding(embedding.float())
            _, metrics = probe_loss_and_metrics(
                low_high_tick_pred=low_high_tick_pred,
                up_class_logits=up_class_logits,
                down_class_logits=down_class_logits,
                path_class_logits=path_class_logits,
                target_low_high_ticks_norm=batch["target_low_high_ticks_norm"],
                target_up_class=batch["target_up_class"],
                target_down_class=batch["target_down_class"],
                target_path_class=batch["target_path_class"],
                target_metrics=batch["target_metrics"],
                valid_mask=batch["valid_mask"],
                config=config,
            )
            for key, value in metrics.items():
                metric_sums[key] = metric_sums.get(key, 0.0) + float(value)
            batches += 1
    metrics = average_metric_sums(metric_sums, batches)
    run_name = checkpoint_path.parent.parent.name
    summary = {
        "run_name": run_name,
        "checkpoint": str(checkpoint_path),
        "step": int(payload.get("step", -1)),
        "epoch": int(payload.get("epoch", -1)),
        "loss": metrics.get("loss", math.nan),
        "regression_mse": metrics.get("regression_mse", math.nan),
        "classification_loss": metrics.get("classification_loss", math.nan),
        "path_accuracy_pct": metrics.get("path_accuracy_pct", math.nan),
        "low_tick_mae": metrics.get("low_tick_mae", math.nan),
        "high_tick_mae": metrics.get("high_tick_mae", math.nan),
        "low_price_mae_dollars": metrics.get("low_price_mae_dollars", math.nan),
        "high_price_mae_dollars": metrics.get("high_price_mae_dollars", math.nan),
        "seconds": time.perf_counter() - started,
    }
    return {
        "summary": summary,
        "metrics": metrics,
        "confusion_matrices": extract_confusion_matrices(metrics),
    }


def build_probe_model(
    payload: dict[str, Any],
    config: ProbeConfig,
    checkpoint_path: Path,
    device: torch.device,
) -> SingleChunkFutureLabelPredictor:
    encoder = build_frozen_encoder(config, device)
    model = SingleChunkFutureLabelPredictor(
        event_encoder=encoder,
        embedding_dim=config.encoder_embedding_dim,
        hidden_dim=config.hidden_dim,
        target_chunks=len(config.horizons),
        dropout=config.dropout,
    ).to(device)
    state = payload.get("model")
    if not isinstance(state, dict):
        raise RuntimeError(f"Checkpoint does not contain model state: {checkpoint_path}")
    state = compatible_state_dict(model, state)
    missing, unexpected = model.load_state_dict(state, strict=False)
    if missing:
        print(f"WARN {checkpoint_path.name}: missing keys={len(missing)}", flush=True)
    if unexpected:
        print(f"WARN {checkpoint_path.name}: unexpected keys={len(unexpected)}", flush=True)
    model.eval()
    model.event_encoder.eval()
    return model


def compatible_state_dict(model: torch.nn.Module, state: dict[str, torch.Tensor]) -> dict[str, torch.Tensor]:
    current = model.state_dict()
    return {
        key: value
        for key, value in state.items()
        if key in current and tuple(current[key].shape) == tuple(value.shape)
    }


def extract_confusion_matrices(metrics: dict[str, float]) -> dict[str, Any]:
    return {
        "upside": confusion_matrix_from_metrics(metrics, "upside_confusion", UP_CLASS_NAMES),
        "downside": confusion_matrix_from_metrics(metrics, "downside_confusion", DOWN_CLASS_NAMES),
        "path": confusion_matrix_from_metrics(metrics, "path_confusion", PATH_CLASS_NAMES),
    }


def confusion_matrix_from_metrics(metrics: dict[str, float], prefix: str, names: tuple[str, ...]) -> list[list[float]]:
    return [
        [float(metrics.get(f"{prefix}/{target}_pred_{pred}", 0.0)) for pred in names]
        for target in names
    ]


if __name__ == "__main__":
    raise SystemExit(main())
