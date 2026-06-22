from __future__ import annotations

import argparse
import importlib
import json
import math
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import torch

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.env import discover_env_files, load_env_files, secret_status
from research.mlops.manifest import write_run_manifest
from research.mlops.metrics import JsonlMetricLogger
from research.mlops.paths import RunPaths
from research.mlops.wandb_utils import init_wandb
from research.temporal_event_model.v1.cache_probe import (
    DEFAULT_CACHE_ROOT,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_WANDB_PROJECT,
    ProbeConfig,
    autocast_context,
    build_frozen_encoder,
    build_probe_batch,
    discover_labeled_shards,
    evaluate_probe,
    format_metric_line,
    load_shard_records,
    log_confusion_tables_to_wandb,
    memory_profile,
    probe_loss_and_metrics,
    resolve_amp_dtype,
    save_checkpoint,
    steps_in_shard,
    to_manifest_config,
)
from research.temporal_event_model.v1.evaluate_cache_probe_checkpoints import (
    config_from_checkpoint,
    discover_latest_probe_checkpoints,
    load_checkpoint,
)
from research.temporal_event_model.v1.model import SingleChunkFutureLabelPredictor
from research.temporal_event_model.v1.progress import ProbeProgressState, ProbeTrainingReporter


JOB_TYPE = "cache_price_probe_finetune"
DEFAULT_FINETUNE_OUTPUT_ROOT = DEFAULT_OUTPUT_ROOT.parent / "cache_price_probe_finetune"
DEFAULT_FINETUNE_PROJECT = DEFAULT_WANDB_PROJECT + "-finetune"
DEFAULT_FINETUNE_CHECKPOINT_ROOT = DEFAULT_OUTPUT_ROOT.parent / "cache_price_probe_laptop"
FINETUNE_MODES = ("bottleneck", "full", "encoder", "scratch_full")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_env_files(discover_env_files(REPO_ROOT, args.env_file), verbose=True)
    checkpoint_paths = [Path(value) for value in args.checkpoint]
    if not checkpoint_paths:
        checkpoint_paths = discover_latest_probe_checkpoints(Path(args.checkpoint_root), max_checkpoints=args.max_checkpoints)
    if not checkpoint_paths:
        raise RuntimeError(f"No checkpoint_latest.pt files found under {args.checkpoint_root}")
    device = torch.device("cuda" if torch.cuda.is_available() and args.device != "cpu" else "cpu")
    if device.type == "cuda":
        torch.set_float32_matmul_precision("high")
    print("=" * 100, flush=True)
    print("Temporal v1 cache probe fine-tuning", flush=True)
    print(f"mode={args.mode}", flush=True)
    print(f"device={device}", flush=True)
    print(f"checkpoints={len(checkpoint_paths)}", flush=True)
    for path in checkpoint_paths:
        print(f"  {path}", flush=True)
    print(f"secrets={secret_status(('WANDB_API_KEY',))}", flush=True)
    print("=" * 100, flush=True)
    if args.print_only:
        return 0

    for checkpoint_path in checkpoint_paths:
        finetune_one_checkpoint(checkpoint_path=checkpoint_path, args=args, device=device)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fine-tune trained temporal v1 cache-probe checkpoints. Modes: "
            "'bottleneck' unfreezes only v20 fixed_grid_to_chunk_embedding plus "
            "the probe MLP head; 'full' unfreezes all loaded event-encoder "
            "parameters plus the probe MLP head; 'scratch_full' trains the same "
            "model from random event-encoder/probe weights to measure the value "
            "of pretraining. 'encoder' is kept as a backward-compatible alias "
            "for 'full'."
        )
    )
    parser.add_argument("--print-only", action="store_true")
    parser.add_argument("--checkpoint", action="append", default=[], help="Trained temporal checkpoint; repeat for each run.")
    parser.add_argument("--checkpoint-root", type=Path, default=DEFAULT_FINETUNE_CHECKPOINT_ROOT)
    parser.add_argument("--max-checkpoints", type=int, default=1)
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_CACHE_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_FINETUNE_OUTPUT_ROOT)
    parser.add_argument("--mode", choices=FINETUNE_MODES, default="bottleneck")
    parser.add_argument("--train-start-shard", type=int, default=0)
    parser.add_argument("--train-max-shards", type=int, default=1)
    parser.add_argument("--validation-start-shard", type=int, default=1)
    parser.add_argument("--validation-max-shards", type=int, default=1)
    parser.add_argument("--validation-batches", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--max-batches-per-shard", type=int, default=0)
    parser.add_argument("--learning-rate", type=float, default=4e-4)
    parser.add_argument("--lr-decay", type=float, default=0.9)
    parser.add_argument("--eta-min-ratio", type=float, default=0.05)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--grad-clip-norm", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=20260622)
    parser.add_argument("--amp-dtype", choices=("off", "fp16", "bf16"), default="bf16")
    parser.add_argument("--preload-shards-to-ram", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--logging-steps", type=int, default=20)
    parser.add_argument("--validation-frequency-shards", type=int, default=1)
    parser.add_argument("--wandb-project", default=DEFAULT_FINETUNE_PROJECT)
    parser.add_argument("--wandb-entity", default="mehdifaraji")
    parser.add_argument("--wandb-mode", default="auto")
    parser.add_argument("--run-prefix", default="")
    parser.add_argument("--device", choices=("auto", "cpu"), default="auto")
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text", "off"), default="auto")
    parser.add_argument("--progress-refresh-per-second", type=float, default=1.0)
    parser.add_argument("--env-file", type=Path, default=None)
    return parser.parse_args(argv)


def finetune_one_checkpoint(*, checkpoint_path: Path, args: argparse.Namespace, device: torch.device) -> None:
    payload = load_checkpoint(checkpoint_path)
    config = build_finetune_config(payload, checkpoint_path, args)
    mode = normalize_finetune_mode(args.mode)
    run_name = default_finetune_run_name(checkpoint_path, mode, config, prefix=args.run_prefix)
    run_root = Path(config.output_root) / run_name
    paths = RunPaths.create(run_root)
    torch.manual_seed(config.seed)
    np.random.seed(config.seed)
    train_shards = discover_labeled_shards(config, split="train", start=config.train_start_shard, max_shards=config.train_max_shards)
    validation_shards = discover_labeled_shards(
        config,
        split="train",
        start=config.validation_start_shard,
        max_shards=config.validation_max_shards,
    )
    model = build_finetune_model(payload, config, checkpoint_path, device, mode=mode)
    trainable_names = configure_trainable_parameters(model, mode=mode)
    trainable_parameters = [parameter for parameter in model.parameters() if parameter.requires_grad]
    if not trainable_parameters:
        raise RuntimeError("No trainable parameters selected for fine-tuning.")
    optimizer = torch.optim.AdamW(trainable_parameters, lr=args.learning_rate, weight_decay=args.weight_decay)
    planned_steps_per_epoch = sum(steps_in_shard(shard, config.batch_size, config.max_batches_per_shard) for shard in train_shards)
    planned_total_steps = int(config.epochs) * int(planned_steps_per_epoch)
    amp_dtype = resolve_amp_dtype(config.amp_dtype)
    reporter_state = ProbeProgressState(
        run_name=run_name,
        device=str(device),
        data_source=str(config.cache_root),
        encoder_checkpoint=str(config.encoder_checkpoint),
        output_dir=str(run_root),
        batch_size=config.batch_size,
        epochs=config.epochs,
        total_steps=planned_total_steps,
        train_shard_count=len(train_shards),
        validation_shard_count=len(validation_shards),
        probe_parameters=sum(p.numel() for p in model.decoder.parameters() if p.requires_grad),
        frozen_encoder_parameters=sum(p.numel() for p in model.event_encoder.parameters() if not p.requires_grad),
    )
    wandb_run = init_wandb(
        entity=config.wandb_entity,
        project=config.wandb_project,
        run_name=run_name,
        config={
            **to_manifest_config(config),
            "finetune_mode": mode,
            "requested_finetune_mode": args.mode,
            "source_probe_checkpoint": str(checkpoint_path),
            "loads_temporal_probe_weights": mode != "scratch_full",
            "loads_pretrained_event_encoder": mode != "scratch_full",
            "trainable_parameter_count": sum(p.numel() for p in trainable_parameters),
            "trainable_names_preview": trainable_names[:100],
            "lr_decay": float(args.lr_decay),
            "eta_min_ratio": float(args.eta_min_ratio),
        },
        run_dir=paths.wandb_dir,
        mode=config.wandb_mode,
        timeout_seconds=90,
    )
    metric_logger = JsonlMetricLogger(paths.metrics_path, wandb_run)
    write_run_manifest(
        paths.manifest_path,
        repo_root=REPO_ROOT,
        model_family="temporal_event_model",
        version="v1",
        job_type=JOB_TYPE,
        run_name=run_name,
        args=vars(args),
        config={
            **to_manifest_config(config),
            "finetune_mode": mode,
            "requested_finetune_mode": args.mode,
            "source_probe_checkpoint": str(checkpoint_path),
            "loads_temporal_probe_weights": mode != "scratch_full",
            "loads_pretrained_event_encoder": mode != "scratch_full",
            "trainable_parameter_count": sum(p.numel() for p in trainable_parameters),
        },
        data_roots={"cache_root": str(config.cache_root)},
        output_root=run_root,
        source_checkpoint=checkpoint_path,
        wandb_info={"project": config.wandb_project, "run_name": run_name},
    )

    global_step = 0
    best_validation_loss = math.inf
    run_start = time.perf_counter()
    try:
        with ProbeTrainingReporter(
            layout=config.progress_layout,
            state=reporter_state,
            refresh_per_second=config.progress_refresh_per_second,
        ) as reporter:
            reporter.message(f"Fine-tune started. Output: {run_root}")
            reporter.message(f"Trainable parameters: {sum(p.numel() for p in trainable_parameters):,}")
            reporter.message(f"Trainable module preview: {', '.join(trainable_names[:12])}")
            for epoch in range(1, config.epochs + 1):
                base_lr = float(args.learning_rate) * (float(args.lr_decay) ** (epoch - 1))
                eta_min = base_lr * float(args.eta_min_ratio)
                set_optimizer_lr(optimizer, base_lr)
                epoch_rng = np.random.default_rng(config.seed + epoch)
                order = list(train_shards)
                epoch_rng.shuffle(order)
                reporter.message(
                    f"EPOCH START {epoch}/{config.epochs} base_lr={base_lr:.6g} "
                    f"eta_min={eta_min:.6g} shard_order={[shard.shard_index for shard in order]}"
                )
                for shard_position, shard in enumerate(order, start=1):
                    shard_metrics, global_step = finetune_one_shard(
                        model=model,
                        optimizer=optimizer,
                        shard=shard,
                        validation_shards=validation_shards,
                        config=config,
                        device=device,
                        amp_dtype=amp_dtype,
                        epoch=epoch,
                        shard_position=shard_position,
                        global_step=global_step,
                        best_validation_loss=best_validation_loss,
                        paths=paths,
                        metric_logger=metric_logger,
                        run_start_time=run_start,
                        reporter=reporter,
                        base_lr=base_lr,
                        eta_min=eta_min,
                        steps_per_epoch=max(1, planned_steps_per_epoch),
                        trainable_parameters=trainable_parameters,
                        mode=mode,
                    )
                    best_validation_loss = shard_metrics.pop("_best_validation_loss", best_validation_loss)
                    if config.validation_frequency_shards > 0 and shard_position % config.validation_frequency_shards == 0:
                        validation_metrics = evaluate_probe(
                            model=model,
                            shards=validation_shards,
                            config=config,
                            device=device,
                            amp_dtype=amp_dtype,
                        )
                        validation_metrics = {f"validation/{key}": value for key, value in validation_metrics.items()}
                        metric_logger.log(validation_metrics, global_step)
                        log_confusion_tables_to_wandb(metric_logger.wandb_run, validation_metrics, global_step, prefix="validation")
                        reporter.update({}, step=global_step, validation_metrics=validation_metrics)
                        reporter.message(format_metric_line("VALIDATION", global_step, validation_metrics))
                        val_loss = validation_metrics.get("validation/loss", math.inf)
                        save_checkpoint(paths.checkpoints_dir / "checkpoint_latest.pt", model, optimizer, NoOpScheduler(), config, global_step, epoch)
                        reporter.message(f"Saved checkpoint latest: {paths.checkpoints_dir / 'checkpoint_latest.pt'}")
                        if val_loss < best_validation_loss:
                            best_validation_loss = val_loss
                            save_checkpoint(paths.checkpoints_dir / "checkpoint_best_val.pt", model, optimizer, NoOpScheduler(), config, global_step, epoch)
                            reporter.message(f"Saved checkpoint best_val: {paths.checkpoints_dir / 'checkpoint_best_val.pt'}")
                    metric_logger.log({f"training/shard_{key}": value for key, value in shard_metrics.items()}, global_step)
                epoch_checkpoint = paths.checkpoints_dir / f"checkpoint_epoch_{epoch:03d}.pt"
                save_checkpoint(epoch_checkpoint, model, optimizer, NoOpScheduler(), config, global_step, epoch)
                reporter.message(f"Saved checkpoint epoch {epoch}: {epoch_checkpoint}")
    finally:
        if wandb_run is not None:
            wandb_run.finish()


class NoOpScheduler:
    def state_dict(self) -> dict[str, Any]:
        return {"type": "manual_epoch_cosine_decay"}


def build_finetune_config(payload: dict[str, Any], checkpoint_path: Path, args: argparse.Namespace) -> ProbeConfig:
    config = config_from_checkpoint(payload, checkpoint_path)
    saved = asdict(config)
    saved.update(
        cache_root=Path(args.cache_root),
        output_root=Path(args.output_root),
        train_start_shard=int(args.train_start_shard),
        train_max_shards=int(args.train_max_shards),
        validation_start_shard=int(args.validation_start_shard),
        validation_max_shards=int(args.validation_max_shards),
        validation_batches=int(args.validation_batches),
        batch_size=int(args.batch_size),
        epochs=int(args.epochs),
        max_batches_per_shard=int(args.max_batches_per_shard),
        learning_rate=float(args.learning_rate),
        weight_decay=float(args.weight_decay),
        grad_clip_norm=float(args.grad_clip_norm),
        seed=int(args.seed),
        amp_dtype=str(args.amp_dtype),
        preload_shards_to_ram=bool(args.preload_shards_to_ram),
        logging_steps=int(args.logging_steps),
        validation_frequency_shards=int(args.validation_frequency_shards),
        validation_frequency_steps=0,
        wandb_project=str(args.wandb_project),
        wandb_entity=str(args.wandb_entity),
        wandb_mode=str(args.wandb_mode),
        progress_layout=str(args.progress_layout),
        progress_refresh_per_second=float(args.progress_refresh_per_second),
    )
    saved["horizons"] = tuple(int(value) for value in saved["horizons"])
    saved["encoder_checkpoint"] = Path(saved["encoder_checkpoint"])
    return ProbeConfig(**saved)


def build_finetune_model(
    payload: dict[str, Any],
    config: ProbeConfig,
    checkpoint_path: Path,
    device: torch.device,
    *,
    mode: str,
) -> SingleChunkFutureLabelPredictor:
    if mode == "scratch_full":
        encoder = build_random_encoder(config, device)
    else:
        encoder = build_frozen_encoder(config, device)
    model = SingleChunkFutureLabelPredictor(
        event_encoder=encoder,
        embedding_dim=config.encoder_embedding_dim,
        hidden_dim=config.hidden_dim,
        target_chunks=len(config.horizons),
        dropout=config.dropout,
    ).to(device)
    if mode == "scratch_full":
        return model
    state = payload.get("model")
    if not isinstance(state, dict):
        raise RuntimeError(f"Checkpoint does not contain model state: {checkpoint_path}")
    state = compatible_state_dict(model, state)
    missing, unexpected = model.load_state_dict(state, strict=False)
    if missing:
        print(f"WARN {checkpoint_path.name}: missing keys={len(missing)}", flush=True)
    if unexpected:
        print(f"WARN {checkpoint_path.name}: unexpected keys={len(unexpected)}", flush=True)
    return model


def compatible_state_dict(model: torch.nn.Module, state: dict[str, torch.Tensor]) -> dict[str, torch.Tensor]:
    current = model.state_dict()
    filtered: dict[str, torch.Tensor] = {}
    skipped: list[str] = []
    for key, value in state.items():
        if key not in current:
            skipped.append(key)
            continue
        if tuple(current[key].shape) != tuple(value.shape):
            skipped.append(key)
            continue
        filtered[key] = value
    if skipped:
        print(f"WARN skipped incompatible checkpoint tensors={len(skipped)} preview={skipped[:8]}", flush=True)
    return filtered


def build_random_encoder(config: ProbeConfig, device: torch.device) -> torch.nn.Module:
    """Build the same standalone event encoder without loading pretraining weights."""

    version = config.encoder_version.lower().strip()
    model_module = importlib.import_module(f"research.masked_event_model.{version}.model")
    config_module = importlib.import_module(f"research.masked_event_model.{version}.config")
    model_config = config_module.ModelConfig(
        d_byte=config.encoder_d_byte,
        d_model=config.encoder_d_model,
        embedding_dim=config.encoder_embedding_dim,
        n_heads=config.encoder_heads,
        encoder_layers=config.encoder_layers,
        decoder_layers=config.encoder_decoder_layers,
        ffn_mult=config.encoder_ffn_mult,
        dropout=config.encoder_dropout,
    )
    autoencoder = model_module.EventTokenMaskedAutoencoder(events_per_chunk=128, config=model_config)
    return autoencoder.build_encoder_model().to(device)


def normalize_finetune_mode(mode: str) -> str:
    return "full" if mode == "encoder" else mode


def configure_trainable_parameters(model: SingleChunkFutureLabelPredictor, *, mode: str) -> list[str]:
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    for parameter in model.decoder.parameters():
        parameter.requires_grad_(True)
    if mode == "bottleneck":
        target = model.event_encoder.chunk_embedding_bottleneck.fixed_grid_to_chunk_embedding
        for parameter in target.parameters():
            parameter.requires_grad_(True)
    elif mode in {"full", "scratch_full"}:
        for parameter in model.event_encoder.parameters():
            parameter.requires_grad_(True)
    else:
        raise ValueError(mode)
    return [name for name, parameter in model.named_parameters() if parameter.requires_grad]


def default_finetune_run_name(checkpoint_path: Path, mode: str, config: ProbeConfig, *, prefix: str = "") -> str:
    source_run = checkpoint_path.parent.parent.name
    stem = f"{source_run}-finetune-{mode}-lr{config.learning_rate:g}-bs{config.batch_size}"
    return f"{prefix}-{stem}" if prefix else stem


def set_optimizer_lr(optimizer: torch.optim.Optimizer, lr: float) -> None:
    for group in optimizer.param_groups:
        group["lr"] = float(lr)


def cosine_lr_for_step(*, base_lr: float, eta_min: float, step_in_epoch: int, steps_per_epoch: int) -> float:
    progress = min(max(float(step_in_epoch) / max(1.0, float(steps_per_epoch)), 0.0), 1.0)
    return float(eta_min + 0.5 * (base_lr - eta_min) * (1.0 + math.cos(math.pi * progress)))


def set_train_modes(model: SingleChunkFutureLabelPredictor, *, mode: str) -> None:
    model.train()
    if mode == "bottleneck":
        # Keep the frozen transformer path deterministic; only the bottleneck
        # projection and downstream probe head receive gradients in this mode.
        model.event_encoder.eval()
        model.event_encoder.chunk_embedding_bottleneck.fixed_grid_to_chunk_embedding.train()
        model.decoder.train()
    elif mode in {"full", "scratch_full"}:
        model.event_encoder.train()
        model.decoder.train()
    else:
        raise ValueError(mode)


def trainable_grad_norm(parameters: Iterable[torch.nn.Parameter]) -> float:
    total = 0.0
    for parameter in parameters:
        if parameter.grad is None:
            continue
        grad = parameter.grad.detach()
        total += float(torch.sum(grad.float() * grad.float()).cpu())
    return math.sqrt(total)


def finetune_one_shard(
    *,
    model: SingleChunkFutureLabelPredictor,
    optimizer: torch.optim.Optimizer,
    shard,
    validation_shards,
    config: ProbeConfig,
    device: torch.device,
    amp_dtype: torch.dtype | None,
    epoch: int,
    shard_position: int,
    global_step: int,
    best_validation_loss: float,
    paths: RunPaths,
    metric_logger: JsonlMetricLogger,
    run_start_time: float,
    reporter: ProbeTrainingReporter,
    base_lr: float,
    eta_min: float,
    steps_per_epoch: int,
    trainable_parameters: list[torch.nn.Parameter],
    mode: str,
) -> tuple[dict[str, float], int]:
    del validation_shards, paths
    set_train_modes(model, mode=mode)
    x_records, y_records = load_shard_records(shard, preload=config.preload_shards_to_ram, reporter=reporter, purpose="train")
    rng = np.random.default_rng(config.seed + epoch * 100_000 + shard.shard_index)
    order = rng.permutation(shard.num_samples)
    max_steps = steps_in_shard(shard, config.batch_size, config.max_batches_per_shard)
    running: dict[str, float] = {}
    shard_start = time.perf_counter()
    reporter.message(
        f"SHARD START epoch={epoch}/{config.epochs} position={shard_position} shard={shard.shard_index} "
        f"samples={shard.num_samples:,} steps={max_steps:,}"
    )
    epoch_step_offset = (epoch - 1) * int(steps_per_epoch)
    for shard_step in range(max_steps):
        step_started = time.perf_counter()
        current_epoch_step = min(max(0, global_step - epoch_step_offset), max(0, int(steps_per_epoch) - 1))
        lr = cosine_lr_for_step(base_lr=base_lr, eta_min=eta_min, step_in_epoch=current_epoch_step, steps_per_epoch=steps_per_epoch)
        set_optimizer_lr(optimizer, lr)
        data_started = time.perf_counter()
        batch_index = order[shard_step * config.batch_size : (shard_step + 1) * config.batch_size]
        batch = build_probe_batch(x_records, y_records, batch_index, config, device)
        data_seconds = time.perf_counter() - data_started
        optimizer.zero_grad(set_to_none=True)
        forward_started = time.perf_counter()
        with autocast_context(device, amp_dtype):
            output = model(batch["header_uint8"], batch["events_uint8"])
            loss, metrics = probe_loss_and_metrics(
                low_high_tick_pred=output.low_high_tick_pred,
                up_class_logits=output.up_class_logits,
                down_class_logits=output.down_class_logits,
                path_class_logits=output.path_class_logits,
                target_low_high_ticks_norm=batch["target_low_high_ticks_norm"],
                target_up_class=batch["target_up_class"],
                target_down_class=batch["target_down_class"],
                target_path_class=batch["target_path_class"],
                target_metrics=batch["target_metrics"],
                valid_mask=batch["valid_mask"],
                config=config,
            )
        forward_seconds = time.perf_counter() - forward_started
        backward_started = time.perf_counter()
        loss.backward()
        grad_norm_before_clip = trainable_grad_norm(trainable_parameters)
        if config.grad_clip_norm > 0:
            torch.nn.utils.clip_grad_norm_(trainable_parameters, config.grad_clip_norm)
        backward_seconds = time.perf_counter() - backward_started
        optimizer_started = time.perf_counter()
        optimizer.step()
        optimizer_seconds = time.perf_counter() - optimizer_started
        step_seconds = time.perf_counter() - step_started
        global_step += 1
        for key, value in metrics.items():
            running[key] = running.get(key, 0.0) + float(value)
        if global_step % max(1, config.logging_steps) == 0 or shard_step == 0:
            elapsed = time.perf_counter() - run_start_time
            samples_seen = global_step * config.batch_size
            row = {
                **{f"training/{key}": value for key, value in metrics.items()},
                "training/lr": float(lr),
                "training/epoch": float(epoch),
                "training/shard_position": float(shard_position),
                "training/shard": float(shard.shard_index),
                "training/shard_step": float(shard_step + 1),
                "training/shard_steps": float(max_steps),
                "training/samples_seen_total": float(samples_seen),
                "training/samples_per_sec": float(samples_seen / max(elapsed, 1e-6)),
                "profile/step_seconds": float(step_seconds),
                "profile/data_seconds": float(data_seconds),
                "profile/forward_seconds": float(forward_seconds),
                "profile/backward_seconds": float(backward_seconds),
                "profile/optimizer_seconds": float(optimizer_seconds),
                "profile/grad_norm_before_clip": float(grad_norm_before_clip),
                **memory_profile(device),
            }
            metric_logger.log(row, global_step)
            reporter.update(row, step=global_step)
            reporter.message(format_metric_line("TRAIN", global_step, row))
    shard_elapsed = time.perf_counter() - shard_start
    out = {key: value / max(1, max_steps) for key, value in running.items()}
    out["seconds"] = shard_elapsed
    out["samples_per_sec"] = (max_steps * config.batch_size) / max(shard_elapsed, 1e-6)
    out["_best_validation_loss"] = best_validation_loss
    reporter.message(format_metric_line("SHARD DONE", global_step, {f"shard/{key}": value for key, value in out.items()}))
    del x_records, y_records, order
    return out, global_step


if __name__ == "__main__":
    raise SystemExit(main())
