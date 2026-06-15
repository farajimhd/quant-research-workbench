from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
import time
import traceback
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = next((parent for parent in Path(__file__).resolve().parents if (parent / "research").exists()), Path(__file__).resolve().parents[3])
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import torch
from torch.utils.data import DataLoader

from research.mlops.checkpoints import AsyncCheckpointManager, CheckpointPolicy
from research.mlops.env import discover_env_files, load_env_files
from research.mlops.manifest import write_run_manifest
from research.mlops.metrics import JsonlMetricLogger
from research.mlops.paths import RunPaths, default_run_root
from research.mlops.seeds import set_seed
from research.mlops.wandb_utils import init_wandb as mlops_init_wandb
from research.temporal_event_model.v1.config import (
    JOB_TYPE,
    MODEL_FAMILY,
    MODEL_VERSION,
    DataConfig,
    EncoderConfig,
    ExperimentConfig,
    LossConfig,
    ModelConfig,
    TrainConfig,
)
from research.temporal_event_model.v1.data import (
    TemporalClickHouseBlockDataset,
    build_fixed_validation_blocks,
    iter_fixed_validation_batches,
)
from research.temporal_event_model.v1.losses import temporal_next_chunk_loss
from research.temporal_event_model.v1.model import TemporalEventPredictor, build_event_encoder


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    data_defaults = DataConfig()
    encoder_defaults = EncoderConfig()
    model_defaults = ModelConfig()
    train_defaults = TrainConfig()
    parser = argparse.ArgumentParser(description="Train v1 single-ticker temporal event model from ClickHouse event streams.")
    parser.add_argument("--clickhouse-url", default=data_defaults.clickhouse_url)
    parser.add_argument("--clickhouse-database", default=data_defaults.clickhouse_database)
    parser.add_argument("--events-table", default=data_defaults.events_table)
    parser.add_argument("--train-index-table", default=data_defaults.train_index_table)
    parser.add_argument("--validation-index-table", default=data_defaults.validation_index_table)
    parser.add_argument("--tickers", default="ALL")
    parser.add_argument("--events-per-chunk", type=int, default=data_defaults.events_per_chunk)
    parser.add_argument("--context-chunks", type=int, default=data_defaults.context_chunks)
    parser.add_argument("--target-chunks", type=int, default=data_defaults.target_chunks)
    parser.add_argument("--window-days", type=int, default=data_defaults.window_days)
    parser.add_argument("--train-stride-choices", default="16,32,64,128")
    parser.add_argument("--validation-stride-choices", default="16,32,64,128")
    parser.add_argument("--origin-stride-events", type=int, default=data_defaults.origin_stride_events)
    parser.add_argument("--block-max-events", type=int, default=data_defaults.block_max_events)
    parser.add_argument("--min-samples-per-block", type=int, default=data_defaults.min_samples_per_block)
    parser.add_argument("--validation-blocks", type=int, default=data_defaults.validation_blocks)
    parser.add_argument("--validation-batches-per-block", type=int, default=data_defaults.validation_batches_per_block)
    parser.add_argument("--clickhouse-max-threads", type=int, default=data_defaults.clickhouse_max_threads)
    parser.add_argument("--clickhouse-max-memory-usage", default=data_defaults.clickhouse_max_memory_usage)
    parser.add_argument("--encoder-version", choices=("v6", "v7"), default=encoder_defaults.version)
    parser.add_argument("--encoder-checkpoint", default=str(encoder_defaults.checkpoint))
    parser.add_argument("--freeze-encoder", action=argparse.BooleanOptionalAction, default=encoder_defaults.freeze)
    parser.add_argument("--encoder-d-byte", type=int, default=encoder_defaults.d_byte)
    parser.add_argument("--encoder-d-model", type=int, default=encoder_defaults.d_model)
    parser.add_argument("--embedding-dim", type=int, default=encoder_defaults.embedding_dim)
    parser.add_argument("--encoder-n-heads", type=int, default=encoder_defaults.n_heads)
    parser.add_argument("--encoder-layers", type=int, default=encoder_defaults.encoder_layers)
    parser.add_argument("--encoder-decoder-layers", type=int, default=encoder_defaults.decoder_layers)
    parser.add_argument("--encoder-ffn-mult", type=int, default=encoder_defaults.ffn_mult)
    parser.add_argument("--encoder-dropout", type=float, default=encoder_defaults.dropout)
    parser.add_argument("--temporal-d-model", type=int, default=model_defaults.temporal_d_model)
    parser.add_argument("--temporal-layers", type=int, default=model_defaults.temporal_layers)
    parser.add_argument("--temporal-heads", type=int, default=model_defaults.temporal_heads)
    parser.add_argument("--temporal-ffn-mult", type=int, default=model_defaults.temporal_ffn_mult)
    parser.add_argument("--decoder-layers", type=int, default=model_defaults.decoder_layers)
    parser.add_argument("--dropout", type=float, default=model_defaults.dropout)
    parser.add_argument("--event-weight", type=float, default=LossConfig().event_weight)
    parser.add_argument("--header-weight", type=float, default=LossConfig().header_weight)
    parser.add_argument("--batch-size", type=int, default=train_defaults.batch_size)
    parser.add_argument("--max-steps", type=int, default=train_defaults.max_steps)
    parser.add_argument("--epochs", type=int, default=train_defaults.epochs)
    parser.add_argument("--learning-rate", type=float, default=train_defaults.learning_rate)
    parser.add_argument("--weight-decay", type=float, default=train_defaults.weight_decay)
    parser.add_argument("--scheduler", choices=("none", "cosine_warm_restarts"), default=train_defaults.scheduler)
    parser.add_argument("--scheduler-t0-steps", type=int, default=train_defaults.scheduler_t0_steps)
    parser.add_argument("--scheduler-t-mult", type=int, default=train_defaults.scheduler_t_mult)
    parser.add_argument("--scheduler-eta-min", type=float, default=train_defaults.scheduler_eta_min)
    parser.add_argument("--grad-clip-norm", type=float, default=train_defaults.grad_clip_norm)
    parser.add_argument("--logging-steps", type=int, default=train_defaults.logging_steps)
    parser.add_argument("--detailed-metrics-steps", type=int, default=train_defaults.detailed_metrics_steps)
    parser.add_argument("--validation-frequency", type=int, default=train_defaults.validation_frequency)
    parser.add_argument("--checkpoint-latest-steps", type=int, default=train_defaults.checkpoint_latest_steps)
    parser.add_argument("--checkpoint-archive-steps", type=int, default=train_defaults.checkpoint_archive_steps)
    parser.add_argument("--seed", type=int, default=train_defaults.seed)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--amp", action=argparse.BooleanOptionalAction, default=train_defaults.amp)
    parser.add_argument("--compile-model", action=argparse.BooleanOptionalAction, default=train_defaults.compile_model)
    parser.add_argument("--progress-layout", choices=("auto", "text", "none"), default=train_defaults.progress_layout)
    parser.add_argument("--wandb-project", default=train_defaults.wandb_project)
    parser.add_argument("--wandb-entity", default=train_defaults.wandb_entity)
    parser.add_argument("--wandb-run-name", default="")
    parser.add_argument("--wandb-mode", choices=("auto", "online", "offline", "disabled"), default="online")
    parser.add_argument("--wandb-init-timeout", type=int, default=60)
    parser.add_argument("--run-root", default="")
    parser.add_argument("--output-root", default="")
    parser.add_argument("--warm-start-checkpoint", default="")
    parser.add_argument("--fresh-start", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    load_env_files(discover_env_files(REPO_ROOT))
    set_seed(args.seed)
    config = build_config(args)
    run_name = args.wandb_run_name or default_run_name(args)
    config.train.wandb_run_name = run_name
    output_dir = resolve_output_dir(config, args, run_name)
    if args.fresh_start and output_dir.exists():
        shutil.rmtree(output_dir)
    run_paths = RunPaths.create(output_dir)
    install_fatal_exception_logger(run_paths)
    device = torch.device(args.device if torch.cuda.is_available() or args.device == "cpu" else "cpu")
    print(f"Output directory: {output_dir}", flush=True)
    print(f"Device: {device}", flush=True)
    print(
        "Temporal input: "
        f"context=[B,{config.data.context_chunks},14] + [B,{config.data.context_chunks},{config.data.events_per_chunk},16], "
        f"target=[B,{config.data.target_chunks},14] + [B,{config.data.target_chunks},{config.data.events_per_chunk},16]",
        flush=True,
    )
    event_encoder = build_event_encoder(config.encoder, events_per_chunk=config.data.events_per_chunk, device=device)
    model = TemporalEventPredictor(
        event_encoder=event_encoder,
        config=config.model,
        context_chunks=config.data.context_chunks,
        target_chunks=config.data.target_chunks,
    ).to(device)
    if args.warm_start_checkpoint:
        load_training_checkpoint(model, Path(args.warm_start_checkpoint), device)
    model_parameters = sum(parameter.numel() for parameter in model.parameters())
    trainable_parameters = sum(parameter.numel() for parameter in model.parameters() if parameter.requires_grad)
    print(f"Model parameters: {model_parameters:,} trainable={trainable_parameters:,}", flush=True)
    train_model = torch.compile(model) if config.train.compile_model else model
    optimizer = torch.optim.AdamW((p for p in model.parameters() if p.requires_grad), lr=config.train.learning_rate, weight_decay=config.train.weight_decay)
    scheduler = build_scheduler(optimizer, config.train)
    scaler = torch.amp.GradScaler("cuda", enabled=config.train.amp and device.type == "cuda")
    wandb_run = init_wandb(args, config, output_dir)
    metric_logger = JsonlMetricLogger(run_paths.metrics_path, wandb_run)
    write_manifest(args, config, run_paths, run_name)
    (output_dir / "config.json").write_text(json.dumps(dataclass_tree(config), indent=2, default=str), encoding="utf-8")
    (run_paths.artifacts_dir / "model_summary.txt").write_text(str(model) + "\n", encoding="utf-8")
    checkpointer = AsyncCheckpointManager(
        run_paths.checkpoints_dir,
        run_paths.checkpoint_manifest_path,
        CheckpointPolicy(
            latest_steps=config.train.checkpoint_latest_steps,
            archive_steps=config.train.checkpoint_archive_steps,
            monitor_train_key="temporal/loss_total",
            monitor_val_key="validation/temporal/loss_total",
        ),
    )
    train_loader = DataLoader(
        TemporalClickHouseBlockDataset(config.data, split="train", batch_size=config.train.batch_size, seed=config.train.seed),
        batch_size=None,
        num_workers=0,
    )
    validation_blocks = build_fixed_validation_blocks(config.data, seed=config.train.seed + 10_000)
    print("Fixed validation blocks:", flush=True)
    for index, block in enumerate(validation_blocks[:10], start=1):
        print(f"  {index:02d} {block.ticker} stride={block.stride} start_us={block.start_us} end_us={block.end_us}", flush=True)

    if args.dry_run:
        batch = move_batch(next(iter(train_loader)), device)
        with torch.no_grad():
            output = model(batch["context_header_uint8"], batch["context_events_uint8"])
            result = temporal_next_chunk_loss(output, batch["target_header_uint8"], batch["target_events_uint8"], config.losses, detailed=True)
        print(f"Dry run loss={float(result.loss):.6f}", flush=True)
        print(json.dumps(result.metrics, indent=2), flush=True)
        return

    global_step = 0
    samples_seen = 0
    best_val_loss = float("inf")
    started = time.perf_counter()
    try:
        for epoch in range(1, config.train.epochs + 1):
            for raw_batch in train_loader:
                global_step += 1
                step_started = time.perf_counter()
                batch = move_batch(raw_batch, device)
                optimizer.zero_grad(set_to_none=True)
                detailed = should_log_detail(global_step, config.train)
                with torch.amp.autocast("cuda", enabled=config.train.amp and device.type == "cuda"):
                    output = train_model(batch["context_header_uint8"], batch["context_events_uint8"])
                    result = temporal_next_chunk_loss(
                        output,
                        batch["target_header_uint8"],
                        batch["target_events_uint8"],
                        config.losses,
                        detailed=detailed,
                    )
                if not torch.isfinite(result.loss):
                    save_failure_debug(run_paths, global_step, model, optimizer, scheduler, scaler, batch, result.metrics)
                    raise FloatingPointError(f"Non-finite temporal loss at step {global_step}.")
                scaler.scale(result.loss).backward()
                if config.train.grad_clip_norm > 0:
                    scaler.unscale_(optimizer)
                    torch.nn.utils.clip_grad_norm_(model.parameters(), config.train.grad_clip_norm)
                scaler.step(optimizer)
                scaler.update()
                if scheduler is not None:
                    scheduler.step()
                step_seconds = time.perf_counter() - step_started
                samples_seen += config.train.batch_size
                metrics = dict(result.metrics)
                metrics.update(
                    {
                        "train/step": float(global_step),
                        "train/epoch": float(epoch),
                        "train/samples_seen_total": float(samples_seen),
                        "train/lr": float(optimizer.param_groups[0]["lr"]),
                        "train/step_seconds": float(step_seconds),
                        "train/samples_per_second": float(config.train.batch_size / max(step_seconds, 1e-9)),
                    }
                )
                profile = raw_batch.get("profile") if isinstance(raw_batch, dict) else None
                if isinstance(profile, dict):
                    metrics.update({key: float(value) for key, value in profile.items() if isinstance(value, (int, float))})
                val_metrics = None
                if global_step % config.train.validation_frequency == 0:
                    val_metrics = evaluate_validation(model, config, validation_blocks, device)
                    metric_logger.log(val_metrics, step=global_step)
                    best_val_loss = min(best_val_loss, val_metrics.get("validation/temporal/loss_total", best_val_loss))
                metric_logger.log(metrics, step=global_step)
                checkpointer.maybe_save(
                    step=global_step,
                    payload=checkpoint_payload(model, optimizer, scheduler, scaler, config, global_step),
                    train_metrics=metrics,
                    val_metrics=val_metrics,
                )
                if should_print(global_step, config.train):
                    print_progress(global_step, config, metrics, val_metrics, started)
                if 0 < config.train.max_steps <= global_step:
                    raise StopIteration
    except StopIteration:
        pass
    finally:
        checkpointer.maybe_save(
            step=max(global_step, 1),
            payload=checkpoint_payload(model, optimizer, scheduler, scaler, config, global_step),
            force=True,
        )
        checkpointer.close()
        if wandb_run is not None:
            wandb_run.finish()


def build_config(args: argparse.Namespace) -> ExperimentConfig:
    encoder = EncoderConfig(
        version=args.encoder_version,
        checkpoint=Path(args.encoder_checkpoint) if args.encoder_checkpoint else Path(""),
        freeze=bool(args.freeze_encoder),
        d_byte=args.encoder_d_byte,
        d_model=args.encoder_d_model,
        embedding_dim=args.embedding_dim,
        n_heads=args.encoder_n_heads,
        encoder_layers=args.encoder_layers,
        decoder_layers=args.encoder_decoder_layers,
        ffn_mult=args.encoder_ffn_mult,
        dropout=args.encoder_dropout,
    )
    data = DataConfig(
        clickhouse_url=args.clickhouse_url,
        clickhouse_database=args.clickhouse_database,
        events_table=args.events_table,
        train_index_table=args.train_index_table,
        validation_index_table=args.validation_index_table,
        tickers=parse_csv_tuple(args.tickers),
        events_per_chunk=args.events_per_chunk,
        context_chunks=args.context_chunks,
        target_chunks=args.target_chunks,
        window_days=args.window_days,
        train_stride_choices=parse_int_tuple(args.train_stride_choices),
        validation_stride_choices=parse_int_tuple(args.validation_stride_choices),
        origin_stride_events=args.origin_stride_events,
        block_max_events=args.block_max_events,
        min_samples_per_block=args.min_samples_per_block,
        validation_blocks=args.validation_blocks,
        validation_batches_per_block=args.validation_batches_per_block,
        clickhouse_max_threads=args.clickhouse_max_threads,
        clickhouse_max_memory_usage=args.clickhouse_max_memory_usage,
    )
    model = ModelConfig(
        embedding_dim=args.embedding_dim,
        temporal_d_model=args.temporal_d_model,
        temporal_layers=args.temporal_layers,
        temporal_heads=args.temporal_heads,
        temporal_ffn_mult=args.temporal_ffn_mult,
        decoder_layers=args.decoder_layers,
        dropout=args.dropout,
    )
    losses = LossConfig(event_weight=args.event_weight, header_weight=args.header_weight)
    train = TrainConfig(
        batch_size=args.batch_size,
        max_steps=args.max_steps,
        epochs=args.epochs,
        learning_rate=args.learning_rate,
        weight_decay=args.weight_decay,
        scheduler=args.scheduler,
        scheduler_t0_steps=args.scheduler_t0_steps,
        scheduler_t_mult=args.scheduler_t_mult,
        scheduler_eta_min=args.scheduler_eta_min,
        grad_clip_norm=args.grad_clip_norm,
        logging_steps=args.logging_steps,
        detailed_metrics_steps=args.detailed_metrics_steps,
        validation_frequency=args.validation_frequency,
        checkpoint_latest_steps=args.checkpoint_latest_steps,
        checkpoint_archive_steps=args.checkpoint_archive_steps,
        seed=args.seed,
        amp=args.amp,
        compile_model=args.compile_model,
        progress_layout=args.progress_layout,
        wandb_project=args.wandb_project,
        wandb_entity=args.wandb_entity,
        wandb_run_name=args.wandb_run_name,
    )
    return ExperimentConfig(data=data, encoder=encoder, model=model, losses=losses, train=train)


def evaluate_validation(
    model: TemporalEventPredictor,
    config: ExperimentConfig,
    blocks: list[Any],
    device: torch.device,
) -> dict[str, float]:
    started = time.perf_counter()
    was_training = model.training
    model.eval()
    losses: list[float] = []
    metrics_accum: dict[str, list[float]] = {}
    with torch.no_grad():
        for batch in iter_fixed_validation_batches(config.data, blocks, batch_size=config.train.batch_size, seed=config.train.seed + 20_000):
            moved = move_batch(batch, device)
            output = model(moved["context_header_uint8"], moved["context_events_uint8"])
            result = temporal_next_chunk_loss(output, moved["target_header_uint8"], moved["target_events_uint8"], config.losses, detailed=True)
            losses.append(float(result.loss.detach().cpu()))
            for key, value in result.metrics.items():
                metrics_accum.setdefault("validation/" + key, []).append(float(value))
    if was_training:
        model.train()
    out = {key: float(sum(values) / max(1, len(values))) for key, values in metrics_accum.items()}
    out["validation/temporal/loss_total"] = float(sum(losses) / max(1, len(losses))) if losses else float("nan")
    out["validation/temporal/seconds"] = time.perf_counter() - started
    return out


def move_batch(batch: dict[str, object], device: torch.device) -> dict[str, object]:
    return {key: value.to(device, non_blocking=True) if torch.is_tensor(value) else value for key, value in batch.items()}


def build_scheduler(optimizer: torch.optim.Optimizer, config: TrainConfig) -> torch.optim.lr_scheduler.LRScheduler | None:
    if config.scheduler == "none":
        return None
    return torch.optim.lr_scheduler.CosineAnnealingWarmRestarts(
        optimizer,
        T_0=max(1, int(config.scheduler_t0_steps)),
        T_mult=max(1, int(config.scheduler_t_mult)),
        eta_min=float(config.scheduler_eta_min),
    )


def checkpoint_payload(
    model: TemporalEventPredictor,
    optimizer: torch.optim.Optimizer,
    scheduler: torch.optim.lr_scheduler.LRScheduler | None,
    scaler: torch.amp.GradScaler,
    config: ExperimentConfig,
    step: int,
) -> dict[str, Any]:
    return {
        "model_state_dict": model.state_dict(),
        "optimizer_state_dict": optimizer.state_dict(),
        "scheduler_state_dict": scheduler.state_dict() if scheduler is not None else None,
        "scaler_state_dict": scaler.state_dict(),
        "config": dataclass_tree(config),
        "step": int(step),
        "model_family": MODEL_FAMILY,
        "version": MODEL_VERSION,
    }


def load_training_checkpoint(model: TemporalEventPredictor, path: Path, device: torch.device) -> None:
    payload = torch.load(path, map_location=device)
    state = payload.get("model_state_dict") if isinstance(payload, dict) else payload
    if not isinstance(state, dict):
        raise RuntimeError(f"Checkpoint {path} does not contain model_state_dict.")
    missing, unexpected = model.load_state_dict(state, strict=False)
    print(f"Loaded warm-start checkpoint: {path}", flush=True)
    if missing:
        print(f"  missing keys: {len(missing)}", flush=True)
    if unexpected:
        print(f"  unexpected keys: {len(unexpected)}", flush=True)


def save_failure_debug(
    run_paths: RunPaths,
    step: int,
    model: TemporalEventPredictor,
    optimizer: torch.optim.Optimizer,
    scheduler: torch.optim.lr_scheduler.LRScheduler | None,
    scaler: torch.amp.GradScaler,
    batch: dict[str, object],
    metrics: dict[str, float],
) -> None:
    debug_dir = run_paths.artifacts_dir / f"nonfinite_step_{step:09d}"
    debug_dir.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "checkpoint": checkpoint_payload(model, optimizer, scheduler, scaler, ExperimentConfig(), step),
            "batch": {key: value.detach().cpu() for key, value in batch.items() if torch.is_tensor(value)},
            "metrics": metrics,
        },
        debug_dir / "debug_bundle.pt",
    )


def init_wandb(args: argparse.Namespace, config: ExperimentConfig, output_dir: Path) -> Any | None:
    return mlops_init_wandb(
        entity=args.wandb_entity,
        project=args.wandb_project,
        run_name=config.train.wandb_run_name,
        config=dataclass_tree(config),
        run_dir=output_dir / "wandb",
        mode=args.wandb_mode,
        timeout_seconds=args.wandb_init_timeout,
    )


def write_manifest(args: argparse.Namespace, config: ExperimentConfig, run_paths: RunPaths, run_name: str) -> None:
    write_run_manifest(
        run_paths.manifest_path,
        repo_root=REPO_ROOT,
        model_family=MODEL_FAMILY,
        version=MODEL_VERSION,
        job_type=JOB_TYPE,
        run_name=run_name,
        args=vars(args),
        config=dataclass_tree(config),
        data_roots={
            "clickhouse_url": config.data.clickhouse_url,
            "clickhouse_database": config.data.clickhouse_database,
            "events_table": config.data.events_table,
            "train_index_table": config.data.train_index_table,
            "validation_index_table": config.data.validation_index_table,
            "encoder_checkpoint": str(config.encoder.checkpoint),
        },
        output_root=run_paths.run_root,
        wandb_info={"project": args.wandb_project, "entity": args.wandb_entity, "run_name": run_name},
    )


def dataclass_tree(value: Any) -> Any:
    if is_dataclass(value):
        return {key: dataclass_tree(item) for key, item in asdict(value).items()}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, list):
        return [dataclass_tree(item) for item in value]
    if isinstance(value, dict):
        return {key: dataclass_tree(item) for key, item in value.items()}
    return value


def resolve_output_dir(config: ExperimentConfig, args: argparse.Namespace, run_name: str) -> Path:
    if args.run_root:
        return Path(args.run_root)
    if args.output_root:
        return Path(args.output_root) / run_name
    return default_run_root(MODEL_FAMILY, MODEL_VERSION, JOB_TYPE, run_name)


def default_run_name(args: argparse.Namespace) -> str:
    encoder_name = f"{args.encoder_version}-emb{args.embedding_dim}"
    return f"temporal-v1-{encoder_name}-ctx{args.context_chunks}-h{args.target_chunks}-bs{args.batch_size}"


def parse_csv_tuple(value: str) -> tuple[str, ...]:
    parsed = tuple(part.strip().upper() for part in value.split(",") if part.strip())
    return parsed or ("ALL",)


def parse_int_tuple(value: str) -> tuple[int, ...]:
    parsed = tuple(int(part.strip()) for part in value.split(",") if part.strip())
    if not parsed:
        raise ValueError("Expected at least one integer.")
    return parsed


def should_log_detail(step: int, config: TrainConfig) -> bool:
    return config.detailed_metrics_steps > 0 and step % config.detailed_metrics_steps == 0


def should_print(step: int, config: TrainConfig) -> bool:
    return config.progress_layout != "none" and config.logging_steps > 0 and step % config.logging_steps == 0


def print_progress(step: int, config: ExperimentConfig, metrics: dict[str, float], val_metrics: dict[str, float] | None, started: float) -> None:
    elapsed = time.perf_counter() - started
    val = ""
    if val_metrics:
        val = f" val_loss={val_metrics.get('validation/temporal/loss_total', float('nan')):.6f}"
    print(
        f"step={step:,} loss={metrics.get('temporal/loss_total', float('nan')):.6f} "
        f"event_bit={metrics.get('temporal/event_bit_acc_pct', 0.0):.3f}% "
        f"event_byte={metrics.get('temporal/event_byte_exact_acc_pct', 0.0):.3f}% "
        f"lr={metrics.get('train/lr', 0.0):.3e} "
        f"speed={metrics.get('train/samples_per_second', 0.0):,.1f}/s "
        f"elapsed_h={elapsed / 3600.0:.2f}{val}",
        flush=True,
    )


def install_fatal_exception_logger(run_paths: RunPaths) -> None:
    def hook(exc_type: type[BaseException], exc: BaseException, tb: Any) -> None:
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        (run_paths.logs_dir / "fatal_error.log").write_text(text, encoding="utf-8")
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = hook


if __name__ == "__main__":
    main()
