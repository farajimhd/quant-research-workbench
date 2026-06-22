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
    CLASS_NAMES,
    DEFAULT_CACHE_ROOT,
    DEFAULT_OUTPUT_ROOT,
    ProbeConfig,
    autocast_context,
    build_frozen_encoder,
    build_probe_batch,
    discover_labeled_shards,
    load_shard_records,
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
        "class_names": list(CLASS_NAMES),
        "direction_class_names": ["down", "up"],
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
    class_confusion = np.zeros((len(config.horizons), len(CLASS_NAMES), len(CLASS_NAMES)), dtype=np.int64)
    direction_confusion = np.zeros((len(config.horizons), 2, 2), dtype=np.int64)
    target_direction_counts = np.zeros((len(config.horizons), 2), dtype=np.int64)
    predicted_flat_for_direction_targets = np.zeros((len(config.horizons),), dtype=np.int64)
    valid_counts = np.zeros((len(config.horizons),), dtype=np.int64)
    loss_sum = 0.0
    valid_total = 0
    started = time.perf_counter()
    with torch.no_grad():
        for batch_indices in validation_indices:
            batch = build_probe_batch(x_records, y_records, batch_indices, config, device)
            with autocast_context(device, amp_dtype):
                embedding = model.encode_chunk(batch["header_uint8"], batch["events_uint8"])
                logits = model.decode_embedding(embedding.float())
            target_one_hot = batch["target_one_hot"]
            target_classes = batch["target_classes"]
            valid_mask = batch["valid_mask"]
            valid_flat = valid_mask.reshape(-1)
            if torch.any(valid_flat):
                flat_logits = logits.reshape(-1, len(CLASS_NAMES))[valid_flat]
                flat_targets = target_one_hot.reshape(-1, len(CLASS_NAMES))[valid_flat]
                loss = torch.nn.functional.binary_cross_entropy_with_logits(flat_logits, flat_targets, reduction="sum")
                loss_sum += float(loss.detach().cpu())
                valid_total += int(valid_flat.sum().detach().cpu())
            predicted = torch.argmax(logits, dim=-1).detach().cpu().numpy()
            target_np = target_classes.detach().cpu().numpy()
            valid_np = valid_mask.detach().cpu().numpy()
            update_confusions(
                class_confusion=class_confusion,
                direction_confusion=direction_confusion,
                target_direction_counts=target_direction_counts,
                predicted_flat_for_direction_targets=predicted_flat_for_direction_targets,
                valid_counts=valid_counts,
                predicted=predicted,
                target=target_np,
                valid=valid_np,
            )
    metrics = summarize_confusions(
        class_confusion=class_confusion,
        direction_confusion=direction_confusion,
        target_direction_counts=target_direction_counts,
        predicted_flat_for_direction_targets=predicted_flat_for_direction_targets,
        valid_counts=valid_counts,
        loss_sum=loss_sum,
        valid_total=valid_total,
        horizons=config.horizons,
    )
    run_name = checkpoint_path.parent.parent.name
    summary = {
        "run_name": run_name,
        "checkpoint": str(checkpoint_path),
        "step": int(payload.get("step", -1)),
        "epoch": int(payload.get("epoch", -1)),
        "loss": metrics["overall"]["loss"],
        "accuracy_pct": metrics["overall"]["accuracy_pct"],
        "macro_f1_pct": metrics["overall"]["macro_f1_pct"],
        "direction_accuracy_pct": metrics["overall"]["direction_accuracy_pct"],
        "direction_coverage_pct": metrics["overall"]["direction_coverage_pct"],
        "seconds": time.perf_counter() - started,
    }
    return {
        "summary": summary,
        "metrics": metrics,
        "class_confusion": class_confusion.tolist(),
        "direction_confusion": direction_confusion.tolist(),
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
        classes=len(CLASS_NAMES),
        dropout=config.dropout,
    ).to(device)
    state = payload.get("model")
    if not isinstance(state, dict):
        raise RuntimeError(f"Checkpoint does not contain model state: {checkpoint_path}")
    missing, unexpected = model.load_state_dict(state, strict=False)
    if missing:
        print(f"WARN {checkpoint_path.name}: missing keys={len(missing)}", flush=True)
    if unexpected:
        print(f"WARN {checkpoint_path.name}: unexpected keys={len(unexpected)}", flush=True)
    model.eval()
    model.event_encoder.eval()
    return model


def update_confusions(
    *,
    class_confusion: np.ndarray,
    direction_confusion: np.ndarray,
    target_direction_counts: np.ndarray,
    predicted_flat_for_direction_targets: np.ndarray,
    valid_counts: np.ndarray,
    predicted: np.ndarray,
    target: np.ndarray,
    valid: np.ndarray,
) -> None:
    for horizon_index in range(target.shape[1]):
        mask = valid[:, horizon_index]
        if not np.any(mask):
            continue
        pred_h = predicted[:, horizon_index][mask].astype(np.int64)
        target_h = target[:, horizon_index][mask].astype(np.int64)
        valid_counts[horizon_index] += int(mask.sum())
        np.add.at(class_confusion[horizon_index], (target_h, pred_h), 1)
        target_dir = five_class_to_direction(target_h)
        pred_dir = five_class_to_direction(pred_h)
        directional_target = target_dir >= 0
        directional_pred = pred_dir >= 0
        for direction in (0, 1):
            target_direction_counts[horizon_index, direction] += int(np.sum(target_dir == direction))
        predicted_flat_for_direction_targets[horizon_index] += int(np.sum(directional_target & ~directional_pred))
        usable = directional_target & directional_pred
        if np.any(usable):
            np.add.at(direction_confusion[horizon_index], (target_dir[usable], pred_dir[usable]), 1)


def five_class_to_direction(values: np.ndarray) -> np.ndarray:
    out = np.full(values.shape, -1, dtype=np.int64)
    out[(values == 0) | (values == 1)] = 0
    out[(values == 3) | (values == 4)] = 1
    return out


def summarize_confusions(
    *,
    class_confusion: np.ndarray,
    direction_confusion: np.ndarray,
    target_direction_counts: np.ndarray,
    predicted_flat_for_direction_targets: np.ndarray,
    valid_counts: np.ndarray,
    loss_sum: float,
    valid_total: int,
    horizons: tuple[int, ...],
) -> dict[str, Any]:
    per_horizon: dict[str, Any] = {}
    total_class = np.zeros_like(class_confusion[0])
    total_direction = np.zeros_like(direction_confusion[0])
    for index, horizon in enumerate(horizons):
        total_class += class_confusion[index]
        total_direction += direction_confusion[index]
        per_horizon[f"future_{horizon}"] = {
            "valid_count": int(valid_counts[index]),
            "accuracy_pct": accuracy_from_confusion(class_confusion[index]),
            "macro_f1_pct": macro_f1_from_numpy_confusion(class_confusion[index]),
            "direction_accuracy_pct": accuracy_from_confusion(direction_confusion[index]),
            "direction_coverage_pct": direction_coverage_pct(direction_confusion[index], predicted_flat_for_direction_targets[index]),
            "target_direction_counts": {
                "down": int(target_direction_counts[index, 0]),
                "up": int(target_direction_counts[index, 1]),
            },
            "predicted_flat_for_direction_targets": int(predicted_flat_for_direction_targets[index]),
        }
    return {
        "overall": {
            "valid_count": int(valid_total),
            "loss": float(loss_sum / max(1, valid_total * len(CLASS_NAMES))),
            "accuracy_pct": accuracy_from_confusion(total_class),
            "macro_f1_pct": macro_f1_from_numpy_confusion(total_class),
            "direction_accuracy_pct": accuracy_from_confusion(total_direction),
            "direction_coverage_pct": direction_coverage_pct(total_direction, int(np.sum(predicted_flat_for_direction_targets))),
        },
        "per_horizon": per_horizon,
    }


def accuracy_from_confusion(confusion: np.ndarray) -> float:
    total = float(confusion.sum())
    if total <= 0:
        return math.nan
    return float(np.trace(confusion) / total * 100.0)


def macro_f1_from_numpy_confusion(confusion: np.ndarray) -> float:
    confusion_f = confusion.astype(np.float64)
    tp = np.diag(confusion_f)
    fp = confusion_f.sum(axis=0) - tp
    fn = confusion_f.sum(axis=1) - tp
    precision = tp / np.maximum(tp + fp, 1.0)
    recall = tp / np.maximum(tp + fn, 1.0)
    f1 = (2.0 * precision * recall) / np.maximum(precision + recall, 1e-12)
    return float(np.mean(f1) * 100.0)


def direction_coverage_pct(direction_confusion: np.ndarray, flat_predictions: int) -> float:
    directional_predictions = int(direction_confusion.sum())
    denom = directional_predictions + int(flat_predictions)
    if denom <= 0:
        return math.nan
    return float(directional_predictions / denom * 100.0)


if __name__ == "__main__":
    raise SystemExit(main())
