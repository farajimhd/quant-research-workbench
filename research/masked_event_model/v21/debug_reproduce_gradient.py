from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import fields
from pathlib import Path
from typing import Any

import torch


REPO_ROOT = next(
    (parent for parent in Path(__file__).resolve().parents if (parent / "research").exists()),
    Path(__file__).resolve().parents[3],
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.masked_event_model.v21.config import DataConfig, ExperimentConfig, LossConfig, MaskConfig, ModelConfig, TrainConfig
from research.masked_event_model.v21.losses import masked_event_bce_loss
from research.masked_event_model.v21.masking import build_event_masks
from research.masked_event_model.v21.model import EventTokenMaskedAutoencoder
from research.masked_event_model.v21.train import build_scheduler, move_batch, resolve_amp_dtype
from research.mlops.event_sample_cache import EventSampleCacheDataConfig, discover_event_sample_shards, iter_event_sample_cache_epoch_batches
from research.mlops.seeds import set_seed


DEFAULT_RUN_DIR = Path(
    r"D:\TradingML\runtimes\masked_event_model\v21\pretrain"
    r"\v21-unweighted-fixedmask070-emb32-bs4096-unweightedmean-bf16-actuallrschedulerendofshard"
)
DEFAULT_CHECKPOINT_NAME = "checkpoint_best_val.pt"
DEFAULT_TARGET_STEP = 15_160
DEFAULT_INSPECT_WINDOW = 80
DEFAULT_LOG_GRAD_NORM = 1.0
DEFAULT_STOP_GRAD_NORM = 1_000.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Replay v21 training from a checkpoint and inspect the backward pass. "
            "This is meant for debugging gradient explosions around a known shard/step."
        )
    )
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--checkpoint", type=Path, default=None)
    parser.add_argument("--cache-root", type=Path, default=None)
    parser.add_argument("--epoch", type=int, default=0, help="0 means infer from checkpoint step.")
    parser.add_argument("--start-shard-position", type=int, default=0, help="1-based position within selected train shards; 0 means infer from checkpoint step.")
    parser.add_argument("--end-shard-position", type=int, default=0, help="1-based inclusive position within selected train shards; 0 means same as start shard.")
    parser.add_argument("--start-shard-step", type=int, default=0, help="1-based batch step inside the first replayed shard; 0 means infer from checkpoint step.")
    parser.add_argument("--max-steps", type=int, default=0, help="0 means run through the selected shard range.")
    parser.add_argument("--target-step", type=int, default=DEFAULT_TARGET_STEP, help="Global training step to inspect; 0 means inspect every replayed step.")
    parser.add_argument("--inspect-window", type=int, default=DEFAULT_INSPECT_WINDOW, help="Inspect gradients in target_step +/- this many steps.")
    parser.add_argument("--print-every", type=int, default=100, help="Fast-forward progress print frequency outside the inspection window.")
    parser.add_argument("--log-grad-norm", type=float, default=DEFAULT_LOG_GRAD_NORM, help="When inspected raw grad norm exceeds this value, log full per-parameter gradient stats.")
    parser.add_argument("--global-step", type=int, default=-1, help="Override global step; default reads checkpoint step.")
    parser.add_argument("--seed", type=int, default=-1, help="Override config seed.")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--force-fp32", action="store_true", help="Disable autocast for the replay.")
    parser.add_argument("--disable-step", action="store_true", help="Run backward/inspect only; do not optimizer.step().")
    parser.add_argument("--stop-grad-norm", type=float, default=DEFAULT_STOP_GRAD_NORM)
    parser.add_argument("--top-gradients", type=int, default=12)
    parser.add_argument("--save-debug-on-stop", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--output-dir", type=Path, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_dir = args.run_dir
    config = load_experiment_config(run_dir / "config.json")
    if args.seed >= 0:
        config.train.seed = int(args.seed)
    if args.cache_root is not None:
        config.data.sample_cache_root = args.cache_root
    config.data.data_source = "sample_cache"
    config.data.sample_cache_interleave_shards = 1
    set_seed(config.train.seed)

    checkpoint_path = args.checkpoint or run_dir / "checkpoints" / DEFAULT_CHECKPOINT_NAME
    if not checkpoint_path.exists():
        raise SystemExit(f"Checkpoint not found: {checkpoint_path}")
    device = torch.device(args.device if torch.cuda.is_available() or args.device == "cpu" else "cpu")
    output_dir = args.output_dir or run_dir / "artifacts" / "gradient_replay"
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 100, flush=True)
    print("v21 gradient replay debugger", flush=True)
    print(f"run_dir={run_dir}", flush=True)
    print(f"checkpoint={checkpoint_path}", flush=True)
    print(f"cache_root={config.data.sample_cache_root}", flush=True)
    print(
        f"requested_epoch={args.epoch or 'auto'} "
        f"requested_shard_position={args.start_shard_position or 'auto'}..{args.end_shard_position or 'auto'} "
        f"requested_start_shard_step={args.start_shard_step or 'auto'} max_steps={args.max_steps or 'target-window/all'} "
        f"target_step={args.target_step or 'disabled'} inspect_window={args.inspect_window} "
        f"log_grad_norm={args.log_grad_norm} stop_grad_norm={args.stop_grad_norm}",
        flush=True,
    )
    print(f"device={device} force_fp32={args.force_fp32} disable_step={args.disable_step}", flush=True)
    print("=" * 100, flush=True)

    model = EventTokenMaskedAutoencoder(events_per_chunk=config.data.events_per_chunk, config=config.model).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.train.learning_rate, weight_decay=config.train.weight_decay)
    scheduler = build_scheduler(optimizer, config.train)
    amp_dtype = None if args.force_fp32 else resolve_amp_dtype(config.train, device)
    scaler_enabled = amp_dtype == torch.float16
    scaler = torch.amp.GradScaler("cuda", enabled=scaler_enabled)

    checkpoint = torch.load(checkpoint_path, map_location="cpu")
    model.load_state_dict(checkpoint["model"])
    optimizer.load_state_dict(checkpoint["optimizer"])
    if scheduler is not None and checkpoint.get("scheduler") is not None:
        scheduler.load_state_dict(checkpoint["scheduler"])
    if checkpoint.get("scaler") is not None:
        scaler.load_state_dict(checkpoint["scaler"])
    train_config = sample_cache_data_config(config, "train", config.train.seed)
    shards = discover_event_sample_shards(train_config)
    checkpoint_step = int(checkpoint.get("step", 0))
    global_step = int(checkpoint_step if args.global_step < 0 else args.global_step)
    replay_epoch, start_shard_position, start_shard_step, steps_per_shard, steps_per_epoch = resolve_replay_position(
        checkpoint_step=checkpoint_step,
        shards=shards,
        batch_size=config.train.batch_size,
        epoch_override=args.epoch,
        start_shard_override=args.start_shard_position,
        start_shard_step_override=args.start_shard_step,
    )
    end_shard_position = resolve_end_shard_position(
        requested_end_shard_position=args.end_shard_position,
        start_shard_position=start_shard_position,
        target_step=args.target_step,
        inspect_window=args.inspect_window,
        steps_per_shard=steps_per_shard,
        steps_per_epoch=steps_per_epoch,
        shard_count=len(shards),
    )
    print(
        f"Loaded checkpoint step={checkpoint_step} replay_global_step_start={global_step} "
        f"inferred_epoch={replay_epoch} inferred_start_shard={start_shard_position} "
        f"inferred_start_shard_step={start_shard_step} end_shard={end_shard_position} steps_per_shard={steps_per_shard} "
        f"steps_per_epoch={steps_per_epoch}",
        flush=True,
    )
    selected = shards[
        max(0, start_shard_position - 1) : min(len(shards), end_shard_position)
    ]
    if not selected:
        raise SystemExit("No selected shards; check --start-shard-position/--end-shard-position")
    iterator = iter_event_sample_cache_epoch_batches(train_config, epoch=replay_epoch, shards=selected)

    replayed = 0
    target_stop_step = int(args.target_step) + max(0, int(args.inspect_window)) if int(args.target_step) > 0 else 0
    for batch in iterator:
        shard_position = int(batch.get("shard_position", 0)) + start_shard_position - 1
        shard_step = int(batch.get("shard_step", 0))
        if shard_position == start_shard_position and shard_step < start_shard_step:
            continue
        global_step += 1
        replayed += 1
        inspect_step = should_inspect_replay_step(
            global_step=global_step,
            target_step=args.target_step,
            inspect_window=args.inspect_window,
        )
        started = time.perf_counter()
        report = run_debug_step(
            model=model,
            optimizer=optimizer,
            scheduler=scheduler,
            scaler=scaler,
            config=config,
            batch=batch,
            device=device,
            global_step=global_step,
            amp_dtype=amp_dtype,
            disable_step=bool(args.disable_step),
            top_n=int(args.top_gradients),
            inspect_gradients=inspect_step,
            log_grad_norm=float(args.log_grad_norm),
            stop_grad_norm=float(args.stop_grad_norm),
        )
        elapsed = time.perf_counter() - started
        report.update(
            {
                "global_step": global_step,
                "epoch": replay_epoch,
                "shard_position": shard_position,
                "shard_step": shard_step,
                "elapsed_seconds": elapsed,
            }
        )
        debug_masks = report.pop("_debug_masks", None)
        should_print = (
            bool(report.get("full_gradient_log"))
            or inspect_step
            or int(args.print_every) <= 1
            or replayed % int(args.print_every) == 0
        )
        if should_print:
            print(format_report(report), flush=True)
            append_jsonl(output_dir / "gradient_replay_metrics.jsonl", report)
            if report.get("full_gradient_log"):
                append_jsonl(output_dir / "gradient_replay_full_gradients.jsonl", report)

        threshold_norm = report["total_norm64"] if report["gradients_inspected"] else report["clip_norm"]
        should_stop = (
            not report["total_norm_finite"]
            or (threshold_norm is not None and float(threshold_norm) >= float(args.stop_grad_norm))
            or report["nonfinite_gradient_count"] > 0
        )
        if should_stop:
            print("STOP: gradient threshold/non-finite condition reached.", flush=True)
            if args.save_debug_on_stop:
                save_replay_debug(
                    output_dir,
                    report,
                    model,
                    optimizer,
                    scheduler,
                    scaler,
                    batch,
                    masks=debug_masks,
                )
            break
        if args.max_steps > 0 and replayed >= args.max_steps:
            break
        if target_stop_step > 0 and global_step >= target_stop_step:
            print(f"STOP: reached target replay window end at global_step={global_step}.", flush=True)
            break


def resolve_end_shard_position(
    *,
    requested_end_shard_position: int,
    start_shard_position: int,
    target_step: int,
    inspect_window: int,
    steps_per_shard: int,
    steps_per_epoch: int,
    shard_count: int,
) -> int:
    if int(requested_end_shard_position) > 0:
        return min(shard_count, int(requested_end_shard_position))
    if int(target_step) <= 0:
        return int(start_shard_position)
    target_window_end = max(1, int(target_step) + max(0, int(inspect_window)))
    target_step_in_epoch = (target_window_end - 1) % int(steps_per_epoch) + 1
    target_shard_position = (target_step_in_epoch - 1) // int(steps_per_shard) + 1
    return min(shard_count, max(int(start_shard_position), int(target_shard_position)))


def should_inspect_replay_step(*, global_step: int, target_step: int, inspect_window: int) -> bool:
    if int(target_step) <= 0:
        return True
    return abs(int(global_step) - int(target_step)) <= max(0, int(inspect_window))


def resolve_replay_position(
    *,
    checkpoint_step: int,
    shards: list[Any],
    batch_size: int,
    epoch_override: int,
    start_shard_override: int,
    start_shard_step_override: int,
) -> tuple[int, int, int, int, int]:
    if not shards:
        raise SystemExit("No train sample-cache shards discovered.")
    full_batch_size = max(1, int(batch_size))
    shard_steps = [max(0, int(shard.num_samples) // full_batch_size) for shard in shards]
    if any(step_count <= 0 for step_count in shard_steps):
        raise SystemExit(f"At least one selected train shard has no full batches: {shard_steps}")
    unique_steps = sorted(set(shard_steps))
    if len(unique_steps) != 1:
        raise SystemExit(
            "Gradient replay currently expects equal full-batch counts per shard; "
            f"got shard_steps={shard_steps}"
        )
    steps_per_shard = unique_steps[0]
    steps_per_epoch = sum(shard_steps)
    if steps_per_epoch <= 0:
        raise SystemExit("No full batches are available for replay.")

    next_step_in_epoch = int(checkpoint_step) % steps_per_epoch + 1
    inferred_epoch = int(checkpoint_step) // steps_per_epoch + 1
    inferred_shard_position = (next_step_in_epoch - 1) // steps_per_shard + 1
    inferred_shard_step = (next_step_in_epoch - 1) % steps_per_shard + 1

    replay_epoch = int(epoch_override) if int(epoch_override) > 0 else inferred_epoch
    start_shard_position = (
        int(start_shard_override) if int(start_shard_override) > 0 else inferred_shard_position
    )
    start_shard_step = (
        int(start_shard_step_override) if int(start_shard_step_override) > 0 else inferred_shard_step
    )
    if not 1 <= start_shard_position <= len(shards):
        raise SystemExit(
            f"Replay start shard {start_shard_position} outside available train shard count {len(shards)}"
        )
    if not 1 <= start_shard_step <= steps_per_shard:
        raise SystemExit(f"Replay start shard step {start_shard_step} outside 1..{steps_per_shard}")
    return replay_epoch, start_shard_position, start_shard_step, steps_per_shard, steps_per_epoch


def run_debug_step(
    *,
    model: EventTokenMaskedAutoencoder,
    optimizer: torch.optim.Optimizer,
    scheduler: torch.optim.lr_scheduler.LRScheduler | None,
    scaler: torch.amp.GradScaler,
    config: ExperimentConfig,
    batch: dict[str, Any],
    device: torch.device,
    global_step: int,
    amp_dtype: torch.dtype | None,
    disable_step: bool,
    top_n: int,
    inspect_gradients: bool,
    log_grad_norm: float,
    stop_grad_norm: float,
) -> dict[str, Any]:
    model.train()
    optimizer.zero_grad(set_to_none=True)
    batch = move_batch(batch, device)
    masks = build_event_masks(batch["events_uint8"], config.masks)
    with torch.amp.autocast("cuda", enabled=amp_dtype is not None, dtype=amp_dtype):
        output = model(batch["header_uint8"], batch["events_uint8"], masks, config.masks)
        result = masked_event_bce_loss(output, config.losses, include_diagnostics=True, metric_level="standard")
    loss = result.loss
    if scaler.is_enabled():
        scaler.scale(loss).backward()
        scaler.unscale_(optimizer)
    else:
        loss.backward()

    raw_norm = measure_global_grad_norm(model)
    full_gradient_log = inspect_gradients or float(raw_norm) >= float(log_grad_norm)
    grad_report = inspect_model_gradients(model, top_n=top_n) if full_gradient_log else empty_gradient_report()
    if not full_gradient_log:
        grad_report.pop("all_gradient_stats", None)
    else:
        grad_report["total_norm64"] = max(float(grad_report["total_norm64"]), float(raw_norm))
    threshold_norm = grad_report["total_norm64"]
    threshold_reached = (
        not bool(grad_report["total_norm_finite"])
        or float(threshold_norm) >= float(stop_grad_norm)
        or int(grad_report["nonfinite_gradient_count"]) > 0
    )
    clip_norm = None
    clip_error = ""
    if threshold_reached:
        clip_error = "skipped_clip_and_step_for_debug_threshold"
    else:
        try:
            clip_norm_tensor = torch.nn.utils.clip_grad_norm_(model.parameters(), config.train.grad_clip_norm, error_if_nonfinite=True)
            clip_norm = float(clip_norm_tensor.detach().cpu())
        except Exception as exc:  # noqa: BLE001
            clip_error = repr(exc)
    if not threshold_reached and not disable_step and not clip_error:
        if scaler.is_enabled():
            scaler.step(optimizer)
            scaler.update()
        else:
            optimizer.step()
        if scheduler is not None:
            scheduler.step(global_step)
    return {
        "loss": float(loss.detach().cpu()),
        "lr": float(optimizer.param_groups[0]["lr"]),
        "clip_norm": clip_norm,
        "clip_error": clip_error,
        "amp_enabled": bool(amp_dtype is not None),
        "scaler_enabled": bool(scaler.is_enabled()),
        "gradients_inspected": bool(full_gradient_log),
        "full_gradient_log": bool(full_gradient_log),
        "stopped_before_optimizer_step": bool(threshold_reached),
        "_debug_masks": masks if threshold_reached else None,
        **grad_report,
    }


def empty_gradient_report() -> dict[str, Any]:
    return {
        "total_norm64": 0.0,
        "total_norm32": 0.0,
        "total_norm_finite": True,
        "nonfinite_gradient_count": 0,
        "nonfinite_gradients": [],
        "top_abs_gradients": [],
        "top_norm_gradients": [],
        "all_gradient_stats": [],
    }


def measure_global_grad_norm(model: torch.nn.Module) -> float:
    total_sq64 = 0.0
    with torch.no_grad():
        for parameter in model.parameters():
            grad = parameter.grad
            if grad is None:
                continue
            grad32 = grad.detach().float()
            finite = torch.isfinite(grad32)
            if not bool(finite.any()):
                return float("inf")
            values = grad32[finite]
            total_sq64 += float(values.double().pow(2).sum().detach().cpu())
            if not bool(finite.all()):
                return float("inf")
    return math.sqrt(total_sq64)


def inspect_model_gradients(model: torch.nn.Module, *, top_n: int) -> dict[str, Any]:
    total_sq64 = 0.0
    total_sq32 = 0.0
    nonfinite: list[dict[str, Any]] = []
    top_abs: list[tuple[float, str, tuple[int, ...], str]] = []
    top_norm: list[tuple[float, str, tuple[int, ...], str]] = []
    all_stats: list[dict[str, Any]] = []
    with torch.no_grad():
        for name, parameter in model.named_parameters():
            grad = parameter.grad
            if grad is None:
                continue
            grad32 = grad.detach().float()
            finite = torch.isfinite(grad32)
            if not bool(finite.all()):
                nonfinite.append(
                    {
                        "name": name,
                        "shape": list(grad.shape),
                        "dtype": str(grad.dtype),
                        "bad": int((~finite).sum().item()),
                        "nan": int(torch.isnan(grad32).sum().item()),
                        "inf": int(torch.isinf(grad32).sum().item()),
                    }
                )
            if not bool(finite.any()):
                continue
            finite_values = grad32[finite]
            sq64 = float(finite_values.double().pow(2).sum().detach().cpu())
            sq32 = float(finite_values.pow(2).sum().detach().cpu())
            numel = int(grad32.numel())
            finite_count = int(finite.sum().item())
            max_abs = float(finite_values.abs().max().item())
            mean_abs = float(finite_values.abs().mean().item())
            total_sq64 += sq64
            total_sq32 += sq32
            top_abs.append((max_abs, name, tuple(grad.shape), str(grad.dtype)))
            top_norm.append((math.sqrt(sq64), name, tuple(grad.shape), str(grad.dtype)))
            all_stats.append(
                {
                    "name": name,
                    "shape": list(grad.shape),
                    "dtype": str(grad.dtype),
                    "numel": numel,
                    "finite_count": finite_count,
                    "bad_count": numel - finite_count,
                    "max_abs": max_abs,
                    "mean_abs": mean_abs,
                    "rms": math.sqrt(sq64 / max(1, finite_count)),
                    "norm64": math.sqrt(sq64),
                    "norm32": math.sqrt(sq32),
                }
            )
    total_norm64 = math.sqrt(total_sq64)
    total_norm32 = math.sqrt(total_sq32)
    return {
        "total_norm64": total_norm64,
        "total_norm32": total_norm32,
        "total_norm_finite": bool(math.isfinite(total_norm64) and math.isfinite(total_norm32)),
        "nonfinite_gradient_count": len(nonfinite),
        "nonfinite_gradients": nonfinite[:top_n],
        "top_abs_gradients": [
            {"value": value, "name": name, "shape": list(shape), "dtype": dtype}
            for value, name, shape, dtype in sorted(top_abs, reverse=True)[:top_n]
        ],
        "top_norm_gradients": [
            {"value": value, "name": name, "shape": list(shape), "dtype": dtype}
            for value, name, shape, dtype in sorted(top_norm, reverse=True)[:top_n]
        ],
        "all_gradient_stats": sorted(all_stats, key=lambda row: row["norm64"], reverse=True),
    }


def format_report(report: dict[str, Any]) -> str:
    top = report["top_abs_gradients"][0] if report["top_abs_gradients"] else {"name": "<none>", "value": 0.0}
    mode = "inspect" if report.get("gradients_inspected") else "fast"
    return (
        f"step={report['global_step']} mode={mode} epoch={report['epoch']} shard_pos={report['shard_position']} "
        f"shard_step={report['shard_step']} loss={report['loss']:.6f} lr={report['lr']:.3e} "
        f"norm64={report['total_norm64']:.3e} norm32={report['total_norm32']:.3e} "
        f"clip={report['clip_norm'] if report['clip_norm'] is not None else report['clip_error']} "
        f"full_log={int(bool(report.get('full_gradient_log')))} "
        f"top_abs={top['value']:.3e} {top['name']}"
    )


def sample_cache_data_config(config: ExperimentConfig, split: str, seed: int) -> EventSampleCacheDataConfig:
    data = config.data
    if data.sample_cache_root is None:
        raise ValueError("sample_cache_root is required")
    return EventSampleCacheDataConfig(
        cache_root=data.sample_cache_root,
        split=split,
        batch_size=config.train.batch_size,
        events_per_chunk=data.events_per_chunk,
        seed=seed,
        prefetch_shards=data.sample_cache_prefetch_shards,
        start_shard_index=data.sample_cache_train_start_shard,
        max_shards=data.sample_cache_train_max_shards,
        max_samples=0,
        max_batches_per_shard=0,
        shuffle_records=data.sample_cache_shuffle_records,
        drop_last=data.sample_cache_drop_last,
        interleave_shards=1,
    )


def load_experiment_config(path: Path) -> ExperimentConfig:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return ExperimentConfig(
        data=from_dict(DataConfig, raw["data"]),
        masks=from_dict(MaskConfig, raw["masks"]),
        model=from_dict(ModelConfig, raw["model"]),
        losses=from_dict(LossConfig, raw["losses"]),
        train=from_dict(TrainConfig, raw["train"]),
    )


def from_dict(cls, values: dict[str, Any]):
    allowed = {field.name for field in fields(cls)}
    clean = {key: convert_value(key, value) for key, value in values.items() if key in allowed}
    return cls(**clean)


def convert_value(key: str, value: Any) -> Any:
    if key.endswith("_root") or key in {"canonical_root", "precomputed_chunk_root", "sample_cache_root", "reference_dir", "output_root"}:
        return Path(value) if value not in ("", None) else None
    if key == "tickers" and isinstance(value, list):
        return tuple(value)
    return value


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")


def save_replay_debug(
    output_dir: Path,
    report: dict[str, Any],
    model: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    scheduler: torch.optim.lr_scheduler.LRScheduler | None,
    scaler: torch.amp.GradScaler,
    batch: dict[str, Any],
    masks: Any | None = None,
) -> None:
    path = output_dir / f"gradient_replay_stop_step_{report['global_step']:09d}.pt"
    torch.save(
        {
            "report": report,
            "model": {key: value.detach().cpu() for key, value in model.state_dict().items()},
            "optimizer": optimizer.state_dict(),
            "scheduler": scheduler.state_dict() if scheduler is not None else None,
            "scaler": scaler.state_dict(),
            "batch": {key: value.detach().cpu() if torch.is_tensor(value) else value for key, value in batch.items()},
            "masks": dataclass_to_cpu_dict(masks) if masks is not None else None,
        },
        path,
    )
    print(f"Saved replay debug bundle: {path}", flush=True)


def dataclass_to_cpu_dict(value: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field in fields(value):
        item = getattr(value, field.name)
        result[field.name] = item.detach().cpu() if torch.is_tensor(item) else item
    return result


if __name__ == "__main__":
    main()
