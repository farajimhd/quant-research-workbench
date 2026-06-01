from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = next((parent for parent in Path(__file__).resolve().parents if (parent / "research").exists()), Path(__file__).resolve().parents[3])
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import torch
from torch.utils.data import DataLoader

from research.masked_event_model.v4.config import DataConfig, ExperimentConfig, LossConfig, MaskConfig, ModelConfig, TrainConfig
from research.masked_event_model.v4.losses import masked_byte_bce_loss
from research.masked_event_model.v4.masking import build_byte_masks
from research.masked_event_model.v4.model import CompactByteMaskedAutoencoder
from research.mlops.checkpoints import AsyncCheckpointManager, CheckpointPolicy
from research.mlops.compact_events import CompactEventDataConfig, CompactEventIterableDataset
from research.mlops.env import discover_env_files, load_env_files
from research.mlops.manifest import write_run_manifest
from research.mlops.metrics import JsonlMetricLogger
from research.mlops.paths import RunPaths, default_run_root
from research.mlops.seeds import set_seed
from research.mlops.wandb_utils import init_wandb as mlops_init_wandb


MODEL_FAMILY = "masked_event_model"
MODEL_VERSION = "v4"
JOB_TYPE = "pretrain"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    data_defaults = DataConfig()
    model_defaults = ModelConfig()
    mask_defaults = MaskConfig()
    train_defaults = TrainConfig()
    parser = argparse.ArgumentParser(description="Train compact byte masked event autoencoder v4.")
    parser.add_argument("--canonical-root", default=str(data_defaults.canonical_root))
    parser.add_argument("--reference-dir", default=str(data_defaults.reference_dir))
    parser.add_argument("--output-root", default="")
    parser.add_argument("--run-root", default="")
    parser.add_argument("--train-start-date", default=data_defaults.train_start_date)
    parser.add_argument("--train-end-date", default=data_defaults.train_end_date)
    parser.add_argument("--validation-start-date", default=data_defaults.validation_start_date)
    parser.add_argument("--validation-end-date", default=data_defaults.validation_end_date)
    parser.add_argument("--tickers", default="ALL")
    parser.add_argument("--events-per-chunk", type=int, default=data_defaults.events_per_chunk)
    parser.add_argument("--month-cache-size", type=int, default=data_defaults.month_cache_size)
    parser.add_argument("--max-index-files", type=int, default=data_defaults.max_index_files)
    parser.add_argument("--batch-size", type=int, default=train_defaults.batch_size)
    parser.add_argument("--max-steps", type=int, default=train_defaults.max_steps)
    parser.add_argument("--num-workers", type=int, default=train_defaults.num_workers)
    parser.add_argument("--prefetch-factor", type=int, default=train_defaults.prefetch_factor)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--seed", type=int, default=train_defaults.seed)
    parser.add_argument("--learning-rate", type=float, default=train_defaults.learning_rate)
    parser.add_argument("--weight-decay", type=float, default=train_defaults.weight_decay)
    parser.add_argument("--scheduler", choices=("none", "cosine_warm_restarts"), default=train_defaults.scheduler)
    parser.add_argument("--scheduler-t0-steps", type=int, default=train_defaults.scheduler_t0_steps)
    parser.add_argument("--scheduler-t-mult", type=int, default=train_defaults.scheduler_t_mult)
    parser.add_argument("--scheduler-eta-min", type=float, default=train_defaults.scheduler_eta_min)
    parser.add_argument("--grad-clip-norm", type=float, default=train_defaults.grad_clip_norm)
    parser.add_argument("--logging-steps", type=int, default=train_defaults.logging_steps)
    parser.add_argument("--detailed-metrics-steps", type=int, default=train_defaults.detailed_metrics_steps)
    parser.add_argument("--profile-training-every-steps", type=int, default=train_defaults.profile_training_every_steps)
    parser.add_argument("--profile-inference-every-steps", type=int, default=train_defaults.profile_inference_every_steps)
    parser.add_argument("--pretrain-validation-frequency", type=int, default=train_defaults.pretrain_validation_frequency)
    parser.add_argument("--pretrain-validation-steps", type=int, default=train_defaults.pretrain_validation_steps)
    parser.add_argument("--checkpoint-latest-steps", type=int, default=train_defaults.checkpoint_latest_steps)
    parser.add_argument("--checkpoint-archive-steps", type=int, default=train_defaults.checkpoint_archive_steps)
    parser.add_argument("--mask-ratio", type=float, default=mask_defaults.mask_ratio)
    parser.add_argument("--header-mask-ratio", type=float, default=mask_defaults.header_mask_ratio)
    parser.add_argument("--d-byte", type=int, default=model_defaults.d_byte)
    parser.add_argument("--d-model", type=int, default=model_defaults.d_model)
    parser.add_argument("--embedding-dim", type=int, default=model_defaults.embedding_dim)
    parser.add_argument("--n-heads", type=int, default=model_defaults.n_heads)
    parser.add_argument("--encoder-layers", type=int, default=model_defaults.encoder_layers)
    parser.add_argument("--decoder-layers", type=int, default=model_defaults.decoder_layers)
    parser.add_argument("--ffn-mult", type=int, default=model_defaults.ffn_mult)
    parser.add_argument("--dropout", type=float, default=model_defaults.dropout)
    parser.add_argument("--wandb-project", default=train_defaults.wandb_project)
    parser.add_argument("--wandb-entity", default=train_defaults.wandb_entity)
    parser.add_argument("--wandb-run-name", default="")
    parser.add_argument("--wandb-mode", choices=("auto", "online", "offline", "disabled"), default="online")
    parser.add_argument("--wandb-init-timeout", type=int, default=60)
    parser.add_argument("--compile-model", action="store_true", default=train_defaults.compile_model)
    parser.add_argument("--fresh-start", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    load_env_files(discover_env_files(REPO_ROOT))
    set_seed(args.seed)
    config = build_config(args)
    run_name = config.train.wandb_run_name or default_run_name(args)
    config.train.wandb_run_name = run_name
    output_dir = resolve_output_dir(config, args)
    run_paths = RunPaths.create(output_dir)
    device = torch.device(args.device if torch.cuda.is_available() or args.device == "cpu" else "cpu")
    print(f"Output directory: {output_dir}", flush=True)
    print(f"Device: {device}", flush=True)
    print(f"Input shape: header=[B,14] events=[B,{config.data.events_per_chunk},16]", flush=True)

    model = CompactByteMaskedAutoencoder(events_per_chunk=config.data.events_per_chunk, config=config.model).to(device)
    print(f"Model parameters: {sum(p.numel() for p in model.parameters()):,}", flush=True)
    train_model = maybe_compile_model(model, config.train.compile_model)
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.train.learning_rate, weight_decay=config.train.weight_decay)
    scheduler = build_scheduler(optimizer, config.train)
    scaler = torch.amp.GradScaler("cuda", enabled=config.train.amp and device.type == "cuda")
    global_step = maybe_resume(model, optimizer, scheduler, output_dir, fresh_start=args.fresh_start)

    wandb_run = init_wandb(args, config, output_dir)
    metric_logger = JsonlMetricLogger(run_paths.metrics_path, wandb_run)
    write_run_manifest(
        run_paths.manifest_path,
        repo_root=REPO_ROOT,
        model_family=MODEL_FAMILY,
        version=MODEL_VERSION,
        job_type=JOB_TYPE,
        run_name=run_name,
        args=vars(args),
        config=dataclass_tree(config),
        data_roots={"canonical_root": str(config.data.canonical_root), "reference_dir": str(config.data.reference_dir)},
        output_root=output_dir,
        wandb_info={"project": args.wandb_project, "entity": args.wandb_entity, "run_name": run_name},
    )
    (output_dir / "config.json").write_text(json.dumps(dataclass_tree(config), indent=2, default=str), encoding="utf-8")
    checkpointer = AsyncCheckpointManager(
        run_paths.checkpoints_dir,
        run_paths.checkpoint_manifest_path,
        CheckpointPolicy(latest_steps=args.checkpoint_latest_steps, archive_steps=args.checkpoint_archive_steps),
    )

    if args.dry_run:
        batch = next(iter(make_loader(config, "train", args.seed)))
        batch = move_batch(batch, device)
        masks = build_byte_masks(batch["header_uint8"], batch["events_uint8"], config.masks)
        with torch.no_grad():
            output = model(batch["header_uint8"], batch["events_uint8"], masks)
            result = masked_byte_bce_loss(output, batch["header_uint8"], batch["events_uint8"], masks, config.losses, include_diagnostics=True)
            embedding = model.encode(batch["header_uint8"], batch["events_uint8"])
        print(f"Dry run loss={float(result.loss):.6f} embedding={tuple(embedding.shape)}", flush=True)
        print(json.dumps(result.metrics, indent=2), flush=True)
        return

    validation_batches = build_validation_cache(config, args.seed + 50_000)
    if validation_batches:
        val_metrics = evaluate_validation(model, validation_batches, config, device, seed=args.seed + 90_000)
        metric_logger.log(val_metrics, global_step)
        print("VALIDATION " + format_metrics(global_step, val_metrics), flush=True)

    loader = make_loader(config, "train", args.seed)
    loader_iter = iter(loader)
    while config.train.max_steps <= 0 or global_step < config.train.max_steps:
        data_wait_started = time.perf_counter()
        batch = next(loader_iter)
        data_wait_seconds = time.perf_counter() - data_wait_started
        step_started = time.perf_counter()
        global_step += 1
        profile_step = config.train.profile_training_every_steps > 0 and global_step % config.train.profile_training_every_steps == 0
        transfer_started = time.perf_counter()
        batch = move_batch(batch, device)
        sync_if_cuda(device)
        transfer_seconds = time.perf_counter() - transfer_started
        mask_started = time.perf_counter()
        masks = build_byte_masks(batch["header_uint8"], batch["events_uint8"], config.masks)
        sync_if_cuda(device)
        mask_seconds = time.perf_counter() - mask_started
        forward_started = time.perf_counter()
        include_diagnostics = config.train.detailed_metrics_steps > 0 and global_step % config.train.detailed_metrics_steps == 0
        with torch.amp.autocast("cuda", enabled=config.train.amp and device.type == "cuda"):
            output = train_model(batch["header_uint8"], batch["events_uint8"], masks)
            result = masked_byte_bce_loss(output, batch["header_uint8"], batch["events_uint8"], masks, config.losses, include_diagnostics=include_diagnostics)
        sync_if_cuda(device)
        forward_loss_seconds = time.perf_counter() - forward_started
        backward_started = time.perf_counter()
        optimizer.zero_grad(set_to_none=True)
        scaler.scale(result.loss).backward()
        sync_if_cuda(device)
        backward_seconds = time.perf_counter() - backward_started
        optimizer_started = time.perf_counter()
        scaler.unscale_(optimizer)
        torch.nn.utils.clip_grad_norm_(model.parameters(), config.train.grad_clip_norm)
        scaler.step(optimizer)
        scaler.update()
        if scheduler is not None:
            scheduler.step(global_step)
        sync_if_cuda(device)
        optimizer_seconds = time.perf_counter() - optimizer_started
        metrics = dict(result.metrics)
        metrics.update({"train/lr": float(optimizer.param_groups[0]["lr"]), "train/step_seconds": time.perf_counter() - step_started, "profile/batch_size": float(batch["header_uint8"].shape[0])})
        if profile_step:
            data_profile = batch.get("profile", {})
            metrics.update(
                {
                    "profile/data_wait_seconds": data_wait_seconds,
                    "profile/transfer_seconds": transfer_seconds,
                    "profile/mask_seconds": mask_seconds,
                    "profile/forward_loss_seconds": forward_loss_seconds,
                    "profile/backward_seconds": backward_seconds,
                    "profile/optimizer_seconds": optimizer_seconds,
                    **{f"profile/{key}": float(value) for key, value in data_profile.items()},
                }
            )
        if config.train.profile_inference_every_steps > 0 and global_step % config.train.profile_inference_every_steps == 0:
            metrics.update(profile_encode(model, batch, device))
        if global_step % config.train.logging_steps == 0:
            metric_logger.log(metrics, global_step)
            print(format_metrics(global_step, metrics), flush=True)
        val_metrics = None
        if validation_batches and config.train.pretrain_validation_frequency > 0 and global_step % config.train.pretrain_validation_frequency == 0:
            val_metrics = evaluate_validation(model, validation_batches, config, device, seed=args.seed + 90_000)
            metric_logger.log(val_metrics, global_step)
            print("VALIDATION " + format_metrics(global_step, val_metrics), flush=True)
        checkpointer.maybe_save(step=global_step, payload=checkpoint_payload(model, optimizer, scheduler, global_step, config, args), train_metrics=metrics, val_metrics=val_metrics)
    checkpointer.maybe_save(step=global_step, payload=checkpoint_payload(model, optimizer, scheduler, global_step, config, args), force=True)
    checkpointer.close()


def build_config(args: argparse.Namespace) -> ExperimentConfig:
    return ExperimentConfig(
        data=DataConfig(
            canonical_root=Path(args.canonical_root),
            reference_dir=Path(args.reference_dir),
            train_start_date=args.train_start_date,
            train_end_date=args.train_end_date,
            validation_start_date=args.validation_start_date,
            validation_end_date=args.validation_end_date,
            tickers=tuple(part.strip().upper() for part in args.tickers.split(",") if part.strip()) or ("ALL",),
            events_per_chunk=args.events_per_chunk,
            month_cache_size=args.month_cache_size,
            max_index_files=args.max_index_files,
        ),
        masks=MaskConfig(mask_ratio=args.mask_ratio, header_mask_ratio=args.header_mask_ratio),
        model=ModelConfig(d_byte=args.d_byte, d_model=args.d_model, embedding_dim=args.embedding_dim, n_heads=args.n_heads, encoder_layers=args.encoder_layers, decoder_layers=args.decoder_layers, ffn_mult=args.ffn_mult, dropout=args.dropout),
        losses=LossConfig(),
        train=TrainConfig(batch_size=args.batch_size, max_steps=args.max_steps, learning_rate=args.learning_rate, weight_decay=args.weight_decay, scheduler=args.scheduler, scheduler_t0_steps=args.scheduler_t0_steps, scheduler_t_mult=args.scheduler_t_mult, scheduler_eta_min=args.scheduler_eta_min, grad_clip_norm=args.grad_clip_norm, logging_steps=args.logging_steps, detailed_metrics_steps=args.detailed_metrics_steps, profile_training_every_steps=args.profile_training_every_steps, profile_inference_every_steps=args.profile_inference_every_steps, pretrain_validation_frequency=args.pretrain_validation_frequency, pretrain_validation_steps=args.pretrain_validation_steps, num_workers=args.num_workers, prefetch_factor=args.prefetch_factor, seed=args.seed, compile_model=args.compile_model, output_root=Path(args.output_root), wandb_project=args.wandb_project, wandb_entity=args.wandb_entity, wandb_run_name=args.wandb_run_name),
    )


def make_loader(config: ExperimentConfig, split: str, seed: int) -> DataLoader:
    data = config.data
    start, end = (data.train_start_date, data.train_end_date) if split == "train" else (data.validation_start_date, data.validation_end_date)
    dataset_config = CompactEventDataConfig(
        canonical_root=data.canonical_root,
        reference_dir=data.reference_dir,
        start_date=start,
        end_date=end,
        tickers=data.tickers,
        events_per_chunk=data.events_per_chunk,
        batch_size=config.train.batch_size,
        seed=seed,
        month_cache_size=data.month_cache_size,
        max_index_files=data.max_index_files,
        strict_lossless=data.strict_lossless,
    )
    workers = max(0, int(config.train.num_workers))
    if workers > 0 and platform.system().lower().startswith("win"):
        print("DataLoader workers disabled on Windows for prebatched compact byte tensors.", flush=True)
        workers = 0
    kwargs: dict[str, Any] = {"batch_size": None, "num_workers": workers, "pin_memory": False}
    if workers > 0:
        kwargs.update({"prefetch_factor": max(1, int(config.train.prefetch_factor)), "persistent_workers": True})
    return DataLoader(CompactEventIterableDataset(dataset_config), **kwargs)


def build_validation_cache(config: ExperimentConfig, seed: int) -> list[dict[str, Any]]:
    if config.train.pretrain_validation_frequency <= 0 or config.train.pretrain_validation_steps <= 0:
        return []
    print(f"Building fixed validation cache batches={config.train.pretrain_validation_steps}", flush=True)
    loader = make_loader(config, "validation", seed)
    batches: list[dict[str, Any]] = []
    iterator = iter(loader)
    for index in range(config.train.pretrain_validation_steps):
        batch = next(iterator)
        batches.append(batch)
        print(f"validation cache batch {index + 1}/{config.train.pretrain_validation_steps} size={batch['header_uint8'].shape[0]}", flush=True)
    return batches


def evaluate_validation(model: CompactByteMaskedAutoencoder, batches: list[dict[str, Any]], config: ExperimentConfig, device: torch.device, *, seed: int) -> dict[str, float]:
    was_training = model.training
    model.eval()
    totals: dict[str, float] = {}
    started = time.perf_counter()
    with torch.no_grad():
        for index, cpu_batch in enumerate(batches):
            torch.manual_seed(seed + index)
            batch = move_batch(cpu_batch, device)
            masks = build_byte_masks(batch["header_uint8"], batch["events_uint8"], config.masks)
            with torch.amp.autocast("cuda", enabled=config.train.amp and device.type == "cuda"):
                output = model(batch["header_uint8"], batch["events_uint8"], masks)
                result = masked_byte_bce_loss(output, batch["header_uint8"], batch["events_uint8"], masks, config.losses)
            for key, value in result.metrics.items():
                totals["validation/" + key] = totals.get("validation/" + key, 0.0) + float(value)
    count = max(1, len(batches))
    averaged = {key: value / count for key, value in totals.items()}
    averaged["validation/pretrain/batches"] = float(len(batches))
    averaged["validation/pretrain/seconds"] = time.perf_counter() - started
    if was_training:
        model.train()
    return averaged


def move_batch(batch: dict[str, Any], device: torch.device) -> dict[str, Any]:
    moved = dict(batch)
    moved["header_uint8"] = batch["header_uint8"].to(device, non_blocking=True)
    moved["events_uint8"] = batch["events_uint8"].to(device, non_blocking=True)
    moved["origin_timestamp_ns"] = batch["origin_timestamp_ns"].to(device, non_blocking=True)
    return moved


def profile_encode(model: CompactByteMaskedAutoencoder, batch: dict[str, Any], device: torch.device) -> dict[str, float]:
    was_training = model.training
    model.eval()
    sync_if_cuda(device)
    started = time.perf_counter()
    with torch.no_grad(), torch.amp.autocast("cuda", enabled=device.type == "cuda"):
        embedding = model.encode(batch["header_uint8"], batch["events_uint8"])
    sync_if_cuda(device)
    elapsed = time.perf_counter() - started
    if was_training:
        model.train()
    batch_size = float(batch["header_uint8"].shape[0])
    return {"profile/inference_encode_seconds": elapsed, "profile/inference_encode_ms_per_sample": elapsed * 1000.0 / max(batch_size, 1.0), "profile/inference_encode_output_elements": float(embedding.numel())}


def build_scheduler(optimizer: torch.optim.Optimizer, train_config: TrainConfig) -> torch.optim.lr_scheduler.LRScheduler | None:
    if train_config.scheduler == "none":
        return None
    return torch.optim.lr_scheduler.CosineAnnealingWarmRestarts(optimizer, T_0=max(1, train_config.scheduler_t0_steps), T_mult=max(1, train_config.scheduler_t_mult), eta_min=max(0.0, train_config.scheduler_eta_min))


def maybe_compile_model(model: torch.nn.Module, enabled: bool) -> torch.nn.Module:
    if enabled and hasattr(torch, "compile"):
        print("Compiling model with torch.compile...", flush=True)
        return torch.compile(model)
    return model


def maybe_resume(model: torch.nn.Module, optimizer: torch.optim.Optimizer, scheduler: torch.optim.lr_scheduler.LRScheduler | None, output_dir: Path, *, fresh_start: bool) -> int:
    path = output_dir / "checkpoints" / "checkpoint_latest.pt"
    if fresh_start or not path.exists():
        return 0
    checkpoint = torch.load(path, map_location="cpu")
    model.load_state_dict(checkpoint["model"])
    optimizer.load_state_dict(checkpoint["optimizer"])
    if scheduler is not None and checkpoint.get("scheduler") is not None:
        scheduler.load_state_dict(checkpoint["scheduler"])
    print(f"Resumed checkpoint: {path} step={checkpoint.get('step', 0)}", flush=True)
    return int(checkpoint.get("step", 0))


def checkpoint_payload(model: torch.nn.Module, optimizer: torch.optim.Optimizer, scheduler: torch.optim.lr_scheduler.LRScheduler | None, step: int, config: ExperimentConfig, args: argparse.Namespace) -> dict[str, Any]:
    return {"model": model.state_dict(), "optimizer": optimizer.state_dict(), "scheduler": scheduler.state_dict() if scheduler is not None else None, "step": step, "config": dataclass_tree(config), "args": vars(args)}


def resolve_output_dir(config: ExperimentConfig, args: argparse.Namespace) -> Path:
    if args.run_root:
        return Path(args.run_root)
    if args.output_root:
        return Path(args.output_root) / config.train.wandb_run_name
    return default_run_root(MODEL_FAMILY, MODEL_VERSION, JOB_TYPE, config.train.wandb_run_name)


def init_wandb(args: argparse.Namespace, config: ExperimentConfig, output_dir: Path) -> Any | None:
    return mlops_init_wandb(entity=args.wandb_entity, project=args.wandb_project, run_name=config.train.wandb_run_name, config=dataclass_tree(config), run_dir=output_dir / "wandb", mode=args.wandb_mode, timeout_seconds=args.wandb_init_timeout)


def default_run_name(args: argparse.Namespace) -> str:
    return f"mem-v4-byte-bce-emb{args.embedding_dim}-d{args.d_model}-e{args.encoder_layers}-mask{int(args.mask_ratio * 100)}-events{args.events_per_chunk}"


def format_metrics(step: int, metrics: dict[str, float]) -> str:
    keys = ["pretrain/loss_total", "pretrain/event_bit_acc_pct", "pretrain/event_byte_exact_acc_pct", "profile/inference_encode_ms_per_sample", "train/step_seconds"]
    parts = [f"step={step}"]
    for key in keys:
        if key in metrics:
            parts.append(f"{key.split('/')[-1]}={metrics[key]:.4f}")
    return " | ".join(parts)


def dataclass_tree(value: Any) -> Any:
    if is_dataclass(value):
        return {key: dataclass_tree(item) for key, item in asdict(value).items()}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: dataclass_tree(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [dataclass_tree(item) for item in value]
    return value


def sync_if_cuda(device: torch.device) -> None:
    if device.type == "cuda":
        torch.cuda.synchronize(device)


if __name__ == "__main__":
    main()
