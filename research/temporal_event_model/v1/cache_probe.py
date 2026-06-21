from __future__ import annotations

import argparse
import importlib
import json
import math
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn.functional as F
from torch import nn

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.env import discover_env_files, load_env_files, secret_status
from research.mlops.event_sample_cache import (
    SAMPLE_BYTES,
    EventSampleCacheDataConfig,
    EventSampleLabeledShard,
    decode_label_records,
    decode_sample_records,
    discover_event_sample_labeled_shards,
)
from research.mlops.manifest import write_run_manifest
from research.mlops.metrics import JsonlMetricLogger
from research.mlops.paths import RunPaths, default_run_root
from research.mlops.wandb_utils import init_wandb
from research.temporal_event_model.v1.model import SingleChunkFutureLabelPredictor
from research.temporal_event_model.v1.progress import ProbeProgressState, ProbeTrainingReporter


MODEL_FAMILY = "temporal_event_model"
MODEL_VERSION = "v1"
JOB_TYPE = "cache_price_probe"
CLASS_NAMES = ("strong_down", "down", "flat", "up", "strong_up")
DEFAULT_HORIZONS = (128, 256)
DEFAULT_CACHE_ROOT = Path("D:/market-data/prepared/event_sample_cache/cache_v2_cycle_20260619_134422")
DEFAULT_OUTPUT_ROOT = Path("D:/TradingML/runtimes/temporal_event_model/v1/cache_price_probe")
DEFAULT_WANDB_PROJECT = "June2026-event-encoder-linear-probes"
DEFAULT_ENCODER_VERSION = "v20"
DEFAULT_V20_RUN_ROOT = Path(
    "D:/TradingML/runtimes/masked_event_model/v20/pretrain/"
    "v20-fullpretrain-sharddecay-fixedmask070-emb32-bs8192-3epochs"
)
DEFAULT_ENCODER_CHECKPOINT = DEFAULT_V20_RUN_ROOT / "checkpoints" / "checkpoint_latest.pt"


@dataclass(slots=True)
class ProbeConfig:
    cache_root: Path = DEFAULT_CACHE_ROOT
    train_start_shard: int = 0
    train_max_shards: int = 10
    validation_start_shard: int = 10
    validation_max_shards: int = 1
    validation_batches: int = 32
    batch_size: int = 512
    epochs: int = 3
    max_batches_per_shard: int = 0
    horizons: tuple[int, ...] = DEFAULT_HORIZONS
    flat_threshold_bps: float = 2.0
    strong_threshold_bps: float = 20.0
    learning_rate: float = 1e-3
    weight_decay: float = 1e-4
    grad_clip_norm: float = 1.0
    hidden_dim: int = 128
    dropout: float = 0.10
    seed: int = 17
    amp_dtype: str = "bf16"
    encoder_version: str = DEFAULT_ENCODER_VERSION
    encoder_checkpoint: Path = DEFAULT_ENCODER_CHECKPOINT
    encoder_d_byte: int = 40
    encoder_d_model: int = 256
    encoder_embedding_dim: int = 32
    encoder_heads: int = 8
    encoder_layers: int = 10
    encoder_decoder_layers: int = 4
    encoder_ffn_mult: int = 4
    encoder_dropout: float = 0.08
    output_root: Path = DEFAULT_OUTPUT_ROOT
    run_name: str = ""
    wandb_project: str = DEFAULT_WANDB_PROJECT
    wandb_entity: str = "mehdifaraji"
    wandb_mode: str = "auto"
    logging_steps: int = 10
    validation_frequency_shards: int = 1
    validation_frequency_steps: int = 0
    preload_shards_to_ram: bool = False
    progress_layout: str = "auto"
    progress_refresh_per_second: float = 1.0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = Path(__file__).resolve().parents[3]
    load_env_files(discover_env_files(repo_root, args.env_file), verbose=True)
    config = build_config(args)
    run_name = config.run_name or default_run_name(config)
    run_root = Path(config.output_root) / run_name
    paths = RunPaths.create(run_root)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    torch.manual_seed(config.seed)
    np.random.seed(config.seed)
    if device.type == "cuda":
        torch.set_float32_matmul_precision("high")

    print("=" * 100, flush=True)
    print("Temporal v1 cache price probe", flush=True)
    print(f"run_name={run_name}", flush=True)
    print(f"device={device}", flush=True)
    print(f"cache_root={config.cache_root}", flush=True)
    print(f"encoder={config.encoder_version} checkpoint={config.encoder_checkpoint}", flush=True)
    print(f"future_chunks={config.horizons} classes={CLASS_NAMES}", flush=True)
    print(f"thresholds flat={config.flat_threshold_bps}bps strong={config.strong_threshold_bps}bps", flush=True)
    print(f"secrets={secret_status(('WANDB_API_KEY',))}", flush=True)
    print("=" * 100, flush=True)

    train_shards = discover_labeled_shards(config, split="train", start=config.train_start_shard, max_shards=config.train_max_shards)
    validation_shards = discover_labeled_shards(
        config,
        split="train",
        start=config.validation_start_shard,
        max_shards=config.validation_max_shards,
    )
    print(f"train_shards={[s.shard_index for s in train_shards]}", flush=True)
    print(f"validation_shards={[s.shard_index for s in validation_shards]}", flush=True)

    encoder = build_frozen_encoder(config, device)
    model = SingleChunkFutureLabelPredictor(
        event_encoder=encoder,
        embedding_dim=config.encoder_embedding_dim,
        hidden_dim=config.hidden_dim,
        target_chunks=len(config.horizons),
        classes=len(CLASS_NAMES),
        dropout=config.dropout,
    ).to(device)
    optimizer = torch.optim.AdamW(
        (parameter for parameter in model.parameters() if parameter.requires_grad),
        lr=config.learning_rate,
        weight_decay=config.weight_decay,
    )
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer,
        T_max=max(1, config.epochs * sum(steps_in_shard(s, config.batch_size, config.max_batches_per_shard) for s in train_shards)),
        eta_min=config.learning_rate * 0.05,
    )
    planned_steps_per_epoch = sum(steps_in_shard(s, config.batch_size, config.max_batches_per_shard) for s in train_shards)
    planned_total_steps = config.epochs * planned_steps_per_epoch
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
        probe_parameters=sum(p.numel() for p in model.decoder.parameters()),
        frozen_encoder_parameters=sum(p.numel() for p in encoder.parameters()),
    )
    amp_dtype = resolve_amp_dtype(config.amp_dtype)
    wandb_run = init_wandb(
        entity=config.wandb_entity,
        project=config.wandb_project,
        run_name=run_name,
        config=to_manifest_config(config),
        run_dir=paths.wandb_dir,
        mode=config.wandb_mode,
        timeout_seconds=90,
    )
    metric_logger = JsonlMetricLogger(paths.metrics_path, wandb_run)
    write_run_manifest(
        paths.manifest_path,
        repo_root=repo_root,
        model_family=MODEL_FAMILY,
        version=MODEL_VERSION,
        job_type=JOB_TYPE,
        run_name=run_name,
        args=vars(args),
        config=to_manifest_config(config),
        data_roots={"cache_root": str(config.cache_root)},
        output_root=run_root,
        source_checkpoint=config.encoder_checkpoint,
        wandb_info={"project": config.wandb_project, "run_name": run_name},
    )

    global_step = 0
    best_validation_loss = math.inf
    start_time = time.perf_counter()
    try:
        with ProbeTrainingReporter(
            layout=config.progress_layout,
            state=reporter_state,
            refresh_per_second=config.progress_refresh_per_second,
        ) as reporter:
            reporter.message(f"Training started. Output: {run_root}")
            for epoch in range(1, config.epochs + 1):
                epoch_rng = np.random.default_rng(config.seed + epoch)
                order = list(train_shards)
                epoch_rng.shuffle(order)
                reporter.message(f"EPOCH START {epoch}/{config.epochs} shard_order={[s.shard_index for s in order]}")
                for shard_position, shard in enumerate(order, start=1):
                    shard_metrics, global_step = train_one_shard(
                        model=model,
                        optimizer=optimizer,
                        scheduler=scheduler,
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
                        run_start_time=start_time,
                        reporter=reporter,
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
                        reporter.update({}, step=global_step, validation_metrics=validation_metrics)
                        val_loss = validation_metrics.get("validation/loss", math.inf)
                        reporter.message(format_metric_line("VALIDATION", global_step, validation_metrics))
                        save_checkpoint(paths.checkpoints_dir / "checkpoint_latest.pt", model, optimizer, scheduler, config, global_step, epoch)
                        reporter.message(f"Saved checkpoint latest: {paths.checkpoints_dir / 'checkpoint_latest.pt'}")
                        if val_loss < best_validation_loss:
                            best_validation_loss = val_loss
                            save_checkpoint(paths.checkpoints_dir / "checkpoint_best_val.pt", model, optimizer, scheduler, config, global_step, epoch)
                            reporter.message(f"Saved checkpoint best_val: {paths.checkpoints_dir / 'checkpoint_best_val.pt'}")
                    metric_logger.log({f"training/shard_{key}": value for key, value in shard_metrics.items()}, global_step)
                epoch_checkpoint = paths.checkpoints_dir / f"checkpoint_epoch_{epoch:03d}.pt"
                save_checkpoint(epoch_checkpoint, model, optimizer, scheduler, config, global_step, epoch)
                reporter.message(f"Saved checkpoint epoch {epoch}: {epoch_checkpoint}")
    finally:
        if wandb_run is not None:
            wandb_run.finish()
    elapsed = time.perf_counter() - start_time
    print(f"DONE steps={global_step:,} elapsed_hours={elapsed / 3600.0:.2f} output={run_root}", flush=True)
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train a frozen-encoder downstream price probe from v2 event sample cache.")
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_CACHE_ROOT)
    parser.add_argument("--train-start-shard", type=int, default=0)
    parser.add_argument("--train-max-shards", type=int, default=10)
    parser.add_argument("--validation-start-shard", type=int, default=10)
    parser.add_argument("--validation-max-shards", type=int, default=1)
    parser.add_argument("--validation-batches", type=int, default=32)
    parser.add_argument("--batch-size", type=int, default=512)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--max-batches-per-shard", type=int, default=0)
    parser.add_argument("--horizons", default="128,256")
    parser.add_argument("--flat-threshold-bps", type=float, default=2.0)
    parser.add_argument("--strong-threshold-bps", type=float, default=20.0)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--grad-clip-norm", type=float, default=1.0)
    parser.add_argument("--hidden-dim", type=int, default=128)
    parser.add_argument("--dropout", type=float, default=0.10)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--amp-dtype", choices=("off", "fp16", "bf16"), default="bf16")
    parser.add_argument("--encoder-version", default=DEFAULT_ENCODER_VERSION)
    parser.add_argument("--encoder-checkpoint", type=Path, default=DEFAULT_ENCODER_CHECKPOINT)
    parser.add_argument("--encoder-d-byte", type=int, default=40)
    parser.add_argument("--encoder-d-model", type=int, default=256)
    parser.add_argument("--encoder-embedding-dim", type=int, default=32)
    parser.add_argument("--encoder-heads", type=int, default=8)
    parser.add_argument("--encoder-layers", type=int, default=10)
    parser.add_argument("--encoder-decoder-layers", type=int, default=4)
    parser.add_argument("--encoder-ffn-mult", type=int, default=4)
    parser.add_argument("--encoder-dropout", type=float, default=0.08)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-name", default="")
    parser.add_argument("--wandb-project", default=DEFAULT_WANDB_PROJECT)
    parser.add_argument("--wandb-entity", default="mehdifaraji")
    parser.add_argument("--wandb-mode", default="auto")
    parser.add_argument("--logging-steps", type=int, default=10)
    parser.add_argument("--validation-frequency-shards", type=int, default=1)
    parser.add_argument("--validation-frequency-steps", type=int, default=0)
    parser.add_argument(
        "--preload-shards-to-ram",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Load each x/y shard into RAM before shuffled batching. This is much faster for local/laptop SSD runs.",
    )
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text", "off"), default="auto")
    parser.add_argument("--progress-refresh-per-second", type=float, default=1.0)
    parser.add_argument("--env-file", type=Path, default=None)
    return parser.parse_args(argv)


def build_config(args: argparse.Namespace) -> ProbeConfig:
    horizons = tuple(int(part.strip()) for part in str(args.horizons).split(",") if part.strip())
    if not horizons:
        raise ValueError("--horizons must contain at least one future label horizon")
    if args.flat_threshold_bps <= 0 or args.strong_threshold_bps <= args.flat_threshold_bps:
        raise ValueError("--strong-threshold-bps must be greater than --flat-threshold-bps > 0")
    return ProbeConfig(
        cache_root=args.cache_root,
        train_start_shard=args.train_start_shard,
        train_max_shards=args.train_max_shards,
        validation_start_shard=args.validation_start_shard,
        validation_max_shards=args.validation_max_shards,
        validation_batches=args.validation_batches,
        batch_size=args.batch_size,
        epochs=args.epochs,
        max_batches_per_shard=args.max_batches_per_shard,
        horizons=horizons,
        flat_threshold_bps=args.flat_threshold_bps,
        strong_threshold_bps=args.strong_threshold_bps,
        learning_rate=args.learning_rate,
        weight_decay=args.weight_decay,
        grad_clip_norm=args.grad_clip_norm,
        hidden_dim=args.hidden_dim,
        dropout=args.dropout,
        seed=args.seed,
        amp_dtype=args.amp_dtype,
        encoder_version=args.encoder_version,
        encoder_checkpoint=args.encoder_checkpoint,
        encoder_d_byte=args.encoder_d_byte,
        encoder_d_model=args.encoder_d_model,
        encoder_embedding_dim=args.encoder_embedding_dim,
        encoder_heads=args.encoder_heads,
        encoder_layers=args.encoder_layers,
        encoder_decoder_layers=args.encoder_decoder_layers,
        encoder_ffn_mult=args.encoder_ffn_mult,
        encoder_dropout=args.encoder_dropout,
        output_root=args.output_root,
        run_name=args.run_name,
        wandb_project=args.wandb_project,
        wandb_entity=args.wandb_entity,
        wandb_mode=args.wandb_mode,
        logging_steps=args.logging_steps,
        validation_frequency_shards=args.validation_frequency_shards,
        validation_frequency_steps=args.validation_frequency_steps,
        preload_shards_to_ram=bool(args.preload_shards_to_ram),
        progress_layout=args.progress_layout,
        progress_refresh_per_second=args.progress_refresh_per_second,
    )


def default_run_name(config: ProbeConfig) -> str:
    checkpoint_name = config.encoder_checkpoint.parent.parent.name if config.encoder_checkpoint else "random_encoder"
    return f"v1-cache-probe-{config.encoder_version}-{checkpoint_name}-bs{config.batch_size}"


def to_manifest_config(config: ProbeConfig) -> dict[str, Any]:
    payload = asdict(config)
    for key, value in list(payload.items()):
        if isinstance(value, Path):
            payload[key] = str(value)
        elif isinstance(value, tuple):
            payload[key] = list(value)
    payload["class_names"] = list(CLASS_NAMES)
    return payload


def discover_labeled_shards(config: ProbeConfig, *, split: str, start: int, max_shards: int) -> list[EventSampleLabeledShard]:
    return discover_event_sample_labeled_shards(
        EventSampleCacheDataConfig(
            cache_root=Path(config.cache_root),
            split=split,
            batch_size=config.batch_size,
            start_shard_index=start,
            max_shards=max_shards,
        )
    )


def build_frozen_encoder(config: ProbeConfig, device: torch.device) -> nn.Module:
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
    payload = torch.load(config.encoder_checkpoint, map_location="cpu")
    state = payload.get("model_state_dict") or payload.get("model") or payload.get("state_dict") if isinstance(payload, dict) else payload
    if not isinstance(state, dict):
        raise RuntimeError(f"Checkpoint does not contain a model state dict: {config.encoder_checkpoint}")
    missing, unexpected = autoencoder.load_state_dict(state, strict=False)
    if unexpected:
        print(f"WARN encoder load unexpected keys={len(unexpected)}", flush=True)
    if missing:
        print(f"WARN encoder load missing keys={len(missing)}", flush=True)
    encoder = autoencoder.build_encoder_model().to(device).eval()
    for parameter in encoder.parameters():
        parameter.requires_grad_(False)
    return encoder


def resolve_amp_dtype(value: str) -> torch.dtype | None:
    if value == "off":
        return None
    if value == "fp16":
        return torch.float16
    if value == "bf16":
        return torch.bfloat16
    raise ValueError(value)


def steps_in_shard(shard: EventSampleLabeledShard, batch_size: int, max_batches: int) -> int:
    steps = int(shard.num_samples) // int(batch_size)
    return min(steps, int(max_batches)) if max_batches > 0 else steps


def train_one_shard(
    *,
    model: SingleChunkFutureLabelPredictor,
    optimizer: torch.optim.Optimizer,
    scheduler: torch.optim.lr_scheduler.LRScheduler,
    shard: EventSampleLabeledShard,
    validation_shards: list[EventSampleLabeledShard],
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
) -> tuple[dict[str, float], int]:
    model.train()
    model.event_encoder.eval()
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
    for shard_step in range(max_steps):
        step_started = time.perf_counter()
        data_started = time.perf_counter()
        batch_index = order[shard_step * config.batch_size : (shard_step + 1) * config.batch_size]
        batch = build_probe_batch(x_records, y_records, batch_index, config, device)
        data_seconds = time.perf_counter() - data_started
        optimizer.zero_grad(set_to_none=True)
        encode_started = time.perf_counter()
        with torch.no_grad(), autocast_context(device, amp_dtype):
            embedding = model.encode_chunk(batch["header_uint8"], batch["events_uint8"])
        encode_seconds = time.perf_counter() - encode_started
        forward_started = time.perf_counter()
        class_logits = model.decode_embedding(embedding.float())
        loss, metrics = probe_loss_and_metrics(
            class_logits=class_logits,
            target_one_hot=batch["target_one_hot"],
            target_classes=batch["target_classes"],
            target_return_bps=batch["target_return_bps"],
            valid_mask=batch["valid_mask"],
            config=config,
        )
        forward_seconds = time.perf_counter() - forward_started
        backward_started = time.perf_counter()
        loss.backward()
        if config.grad_clip_norm > 0:
            torch.nn.utils.clip_grad_norm_(model.decoder.parameters(), config.grad_clip_norm)
        backward_seconds = time.perf_counter() - backward_started
        optimizer_started = time.perf_counter()
        optimizer.step()
        scheduler.step()
        optimizer_seconds = time.perf_counter() - optimizer_started
        step_seconds = time.perf_counter() - step_started
        global_step += 1
        for key, value in metrics.items():
            running[key] = running.get(key, 0.0) + float(value)
        if global_step % max(1, config.logging_steps) == 0 or shard_step == 0:
            elapsed = time.perf_counter() - run_start_time
            samples_seen = global_step * config.batch_size
            rate = samples_seen / max(elapsed, 1e-6)
            row = {
                **{f"training/{key}": value for key, value in metrics.items()},
                "training/lr": float(optimizer.param_groups[0]["lr"]),
                "training/samples_per_sec": float(rate),
                "training/epoch": float(epoch),
                "training/shard_position": float(shard_position),
                "training/shard": float(shard.shard_index),
                "training/shard_step": float(shard_step + 1),
                "training/shard_steps": float(max_steps),
                "training/samples_seen_total": float(samples_seen),
                "profile/step_seconds": float(step_seconds),
                "profile/data_seconds": float(data_seconds),
                "profile/encode_seconds": float(encode_seconds),
                "profile/forward_seconds": float(forward_seconds),
                "profile/backward_seconds": float(backward_seconds),
                "profile/optimizer_seconds": float(optimizer_seconds),
                **memory_profile(device),
            }
            metric_logger.log(row, global_step)
            reporter.update(row, step=global_step)
            reporter.message(format_metric_line("TRAIN", global_step, row))
        if config.validation_frequency_steps > 0 and global_step % config.validation_frequency_steps == 0:
            validation_metrics = evaluate_probe(
                model=model,
                shards=validation_shards,
                config=config,
                device=device,
                amp_dtype=amp_dtype,
            )
            validation_metrics = {f"validation/{key}": value for key, value in validation_metrics.items()}
            metric_logger.log(validation_metrics, global_step)
            reporter.update({}, step=global_step, validation_metrics=validation_metrics)
            val_loss = validation_metrics.get("validation/loss", math.inf)
            reporter.message(format_metric_line("VALIDATION", global_step, validation_metrics))
            save_checkpoint(paths.checkpoints_dir / "checkpoint_latest.pt", model, optimizer, scheduler, config, global_step, epoch)
            reporter.message(f"Saved checkpoint latest: {paths.checkpoints_dir / 'checkpoint_latest.pt'}")
            if val_loss < best_validation_loss:
                best_validation_loss = val_loss
                save_checkpoint(paths.checkpoints_dir / "checkpoint_best_val.pt", model, optimizer, scheduler, config, global_step, epoch)
                reporter.message(f"Saved checkpoint best_val: {paths.checkpoints_dir / 'checkpoint_best_val.pt'}")
            model.train()
            model.event_encoder.eval()
    shard_elapsed = time.perf_counter() - shard_start
    out = {key: value / max(1, max_steps) for key, value in running.items()}
    out["seconds"] = shard_elapsed
    out["samples_per_sec"] = (max_steps * config.batch_size) / max(shard_elapsed, 1e-6)
    out["_best_validation_loss"] = best_validation_loss
    reporter.message(format_metric_line("SHARD DONE", global_step, {f"shard/{key}": value for key, value in out.items()}))
    del x_records, y_records, order
    return out, global_step


@torch.no_grad()
def evaluate_probe(
    *,
    model: SingleChunkFutureLabelPredictor,
    shards: list[EventSampleLabeledShard],
    config: ProbeConfig,
    device: torch.device,
    amp_dtype: torch.dtype | None,
) -> dict[str, float]:
    started = time.perf_counter()
    model.eval()
    sums: dict[str, float] = {}
    batches = 0
    for shard in shards:
        x_records, y_records = load_shard_records(shard, preload=False, reporter=None, purpose="validation")
        rng = np.random.default_rng(config.seed + 9_000_000 + shard.shard_index)
        order = rng.permutation(shard.num_samples)
        max_batches = min(config.validation_batches, shard.num_samples // config.batch_size)
        for step in range(max_batches):
            batch_index = order[step * config.batch_size : (step + 1) * config.batch_size]
            batch = build_probe_batch(x_records, y_records, batch_index, config, device)
            with autocast_context(device, amp_dtype):
                embedding = model.encode_chunk(batch["header_uint8"], batch["events_uint8"])
                class_logits = model.decode_embedding(embedding.float())
            _, metrics = probe_loss_and_metrics(
                class_logits=class_logits,
                target_one_hot=batch["target_one_hot"],
                target_classes=batch["target_classes"],
                target_return_bps=batch["target_return_bps"],
                valid_mask=batch["valid_mask"],
                config=config,
            )
            for key, value in metrics.items():
                sums[key] = sums.get(key, 0.0) + float(value)
            batches += 1
        del x_records, y_records, order
    out = {key: value / max(1, batches) for key, value in sums.items()}
    out["seconds"] = time.perf_counter() - started
    return out


def load_shard_records(
    shard: EventSampleLabeledShard,
    *,
    preload: bool,
    reporter: ProbeTrainingReporter | None,
    purpose: str,
) -> tuple[np.ndarray, np.ndarray]:
    x_records = np.memmap(shard.x_path, dtype=np.uint8, mode="r", shape=(shard.num_samples, SAMPLE_BYTES))
    y_records = np.memmap(shard.y_path, dtype=np.uint8, mode="r", shape=(shard.num_samples, shard.y_sample_bytes))
    if not preload:
        return x_records, y_records
    started = time.perf_counter()
    if reporter is not None:
        reporter.message(
            f"LOAD {purpose} shard={shard.shard_index} samples={shard.num_samples:,} "
            f"x={shard.x_byte_size / (1024**3):.2f}GiB y={shard.y_byte_size / (1024**3):.2f}GiB"
        )
    x_loaded = np.array(x_records, dtype=np.uint8, copy=True)
    y_loaded = np.array(y_records, dtype=np.uint8, copy=True)
    del x_records, y_records
    if reporter is not None:
        reporter.message(f"LOAD DONE {purpose} shard={shard.shard_index} seconds={time.perf_counter() - started:.1f}")
    return x_loaded, y_loaded


def build_probe_batch(
    x_records: np.ndarray,
    y_records: np.ndarray,
    indices: np.ndarray,
    config: ProbeConfig,
    device: torch.device,
) -> dict[str, torch.Tensor]:
    selected_x = np.asarray(x_records[indices], dtype=np.uint8)
    selected_y = np.asarray(y_records[indices], dtype=np.uint8)
    headers, events = decode_sample_records(selected_x)
    label_headers, label_events = decode_label_records(selected_y, label_chunks=len(config.horizons))
    target_bps, classes, valid = build_targets_from_bytes(headers, events, label_headers, label_events, config)
    one_hot = np.eye(len(CLASS_NAMES), dtype=np.float32)[classes]
    one_hot = np.where(valid[:, :, None], one_hot, 0.0).astype(np.float32)
    return {
        "header_uint8": torch.from_numpy(headers.copy()).to(device=device, dtype=torch.uint8, non_blocking=True),
        "events_uint8": torch.from_numpy(events.copy()).to(device=device, dtype=torch.uint8, non_blocking=True),
        "target_return_bps": torch.from_numpy(target_bps).to(device=device, dtype=torch.float32, non_blocking=True),
        "target_one_hot": torch.from_numpy(one_hot).to(device=device, dtype=torch.float32, non_blocking=True),
        "target_classes": torch.from_numpy(classes).to(device=device, dtype=torch.long, non_blocking=True),
        "valid_mask": torch.from_numpy(valid).to(device=device, dtype=torch.bool, non_blocking=True),
    }


def build_targets_from_bytes(
    x_headers: np.ndarray,
    x_events: np.ndarray,
    y_headers: np.ndarray,
    y_events: np.ndarray,
    config: ProbeConfig,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    current_max_price = max_price_per_chunk(x_headers, x_events)
    current_valid = np.isfinite(current_max_price) & (current_max_price > 0.0)
    sample_count = int(x_headers.shape[0])
    returns = np.zeros((sample_count, len(config.horizons)), dtype=np.float32)
    classes = np.full((sample_count, len(config.horizons)), 2, dtype=np.int64)
    valid = np.zeros((sample_count, len(config.horizons)), dtype=bool)
    if y_headers.shape[1] != len(config.horizons):
        raise ValueError(f"Expected {len(config.horizons)} y chunks, got {y_headers.shape[1]}")
    for horizon_index, _horizon in enumerate(config.horizons):
        future_max_price = max_price_per_chunk(y_headers[:, horizon_index, :], y_events[:, horizon_index, :, :])
        horizon_valid = current_valid & np.isfinite(future_max_price) & (future_max_price > 0.0)
        bps = ((future_max_price / np.maximum(current_max_price, 1e-12)) - 1.0) * 10_000.0
        bps = np.where(horizon_valid, bps, 0.0).astype(np.float32)
        returns[:, horizon_index] = bps
        classes[:, horizon_index] = classify_bps(bps, config.flat_threshold_bps, config.strong_threshold_bps)
        valid[:, horizon_index] = horizon_valid
    return returns, classes, valid


def max_price_per_chunk(headers: np.ndarray, events: np.ndarray) -> np.ndarray:
    """Decode compact chunk bytes and return the maximum quote/trade price.

    The compact event representation stores prices as int16 deltas from the
    header ask anchor. For quote events the primary price is ask; for trade
    events it is trade price. Taking the maximum across those primary prices
    gives one byte-derived future target per chunk without using scalar labels.
    """

    ask_anchor_ticks = (
        headers[:, 0].astype(np.uint32)
        | (headers[:, 1].astype(np.uint32) << 8)
        | ((headers[:, 2].astype(np.uint32) & 0x0F) << 16)
    ).astype(np.float64)
    tick_size = np.where((headers[:, 13].astype(np.uint8) & 0x04) != 0, 0.01, 0.0001).astype(np.float64)
    price_delta = np.ascontiguousarray(events[:, :, 3:5]).view("<i2").reshape(events.shape[0], events.shape[1]).astype(np.float64)
    price = (ask_anchor_ticks[:, None] + price_delta) * tick_size[:, None]
    present = (events[:, :, 0] & 0x02) != 0
    price = np.where(present & np.isfinite(price) & (price > 0.0), price, -np.inf)
    max_price = np.max(price, axis=1)
    return np.where(np.isfinite(max_price), max_price, np.nan)



def classify_bps(values: np.ndarray, flat_threshold: float, strong_threshold: float) -> np.ndarray:
    out = np.full(values.shape, 2, dtype=np.int64)
    out[values <= -strong_threshold] = 0
    out[(values > -strong_threshold) & (values <= -flat_threshold)] = 1
    out[(values >= flat_threshold) & (values < strong_threshold)] = 3
    out[values >= strong_threshold] = 4
    return out


def probe_loss_and_metrics(
    *,
    class_logits: torch.Tensor,
    target_one_hot: torch.Tensor,
    target_classes: torch.Tensor,
    target_return_bps: torch.Tensor,
    valid_mask: torch.Tensor,
    config: ProbeConfig,
) -> tuple[torch.Tensor, dict[str, float]]:
    valid = valid_mask.reshape(-1)
    if not torch.any(valid):
        zero = class_logits.sum() * 0.0
        return zero, {"loss": 0.0, "bce_loss": 0.0, "valid_pct": 0.0}
    flat_logits = class_logits.reshape(-1, len(CLASS_NAMES))[valid]
    flat_targets = target_one_hot.reshape(-1, len(CLASS_NAMES))[valid]
    flat_classes = target_classes.reshape(-1)[valid]
    loss = F.binary_cross_entropy_with_logits(flat_logits, flat_targets)
    with torch.no_grad():
        predicted_classes = torch.argmax(flat_logits, dim=-1)
        acc = (predicted_classes == flat_classes).float().mean()
        confusion = torch.zeros((len(CLASS_NAMES), len(CLASS_NAMES)), device=class_logits.device, dtype=torch.float32)
        flat_index = flat_classes * len(CLASS_NAMES) + predicted_classes
        confusion.view(-1).scatter_add_(0, flat_index, torch.ones_like(flat_index, dtype=torch.float32))
        macro_f1 = macro_f1_from_confusion(confusion)
        metrics = {
            "loss": float(loss.detach().cpu()),
            "bce_loss": float(loss.detach().cpu()),
            "accuracy_pct": float(acc.detach().cpu() * 100.0),
            "macro_f1_pct": float(macro_f1.detach().cpu() * 100.0),
            "abs_return_bps_mean": float(torch.abs(target_return_bps.reshape(-1)[valid]).mean().detach().cpu()),
            "valid_pct": float(valid.float().mean().detach().cpu() * 100.0),
        }
        for horizon_index, horizon in enumerate(config.horizons):
            horizon_valid = valid_mask[:, horizon_index]
            if torch.any(horizon_valid):
                pred_h = torch.argmax(class_logits[:, horizon_index, :][horizon_valid], dim=-1)
                target_h = target_classes[:, horizon_index][horizon_valid]
                metrics[f"accuracy_pct_future_{horizon}"] = float((pred_h == target_h).float().mean().detach().cpu() * 100.0)
                metrics[f"abs_return_bps_mean_future_{horizon}"] = float(
                    torch.abs(target_return_bps[:, horizon_index][horizon_valid]).mean().detach().cpu()
                )
    return loss, metrics


def macro_f1_from_confusion(confusion: torch.Tensor) -> torch.Tensor:
    tp = torch.diag(confusion)
    fp = confusion.sum(dim=0) - tp
    fn = confusion.sum(dim=1) - tp
    precision = tp / torch.clamp(tp + fp, min=1.0)
    recall = tp / torch.clamp(tp + fn, min=1.0)
    return torch.mean((2.0 * precision * recall) / torch.clamp(precision + recall, min=1e-12))


def autocast_context(device: torch.device, amp_dtype: torch.dtype | None):
    if device.type != "cuda" or amp_dtype is None:
        return torch.amp.autocast("cpu", enabled=False)
    return torch.amp.autocast("cuda", dtype=amp_dtype)


def format_metric_line(prefix: str, step: int, metrics: dict[str, float]) -> str:
    keys = [key for key in ("training/loss", "validation/loss", "shard/loss", "training/accuracy_pct", "validation/accuracy_pct", "training/macro_f1_pct", "validation/macro_f1_pct", "training/samples_per_sec", "shard/samples_per_sec") if key in metrics]
    parts = [f"{key.split('/')[-1]}={metrics[key]:.4f}" for key in keys]
    if not parts:
        parts = [f"{key}={value:.4f}" for key, value in list(metrics.items())[:6]]
    return f"{prefix} step={step:,} " + " ".join(parts)


def memory_profile(device: torch.device) -> dict[str, float]:
    profile: dict[str, float] = {}
    if device.type == "cuda":
        profile["profile/gpu_allocated_gib"] = float(torch.cuda.memory_allocated(device) / (1024**3))
        profile["profile/gpu_reserved_gib"] = float(torch.cuda.memory_reserved(device) / (1024**3))
        profile["profile/gpu_peak_allocated_gib"] = float(torch.cuda.max_memory_allocated(device) / (1024**3))
    try:
        import psutil

        profile["profile/process_rss_gib"] = float(psutil.Process(os.getpid()).memory_info().rss / (1024**3))
    except Exception:  # noqa: BLE001
        profile["profile/process_rss_gib"] = 0.0
    return profile


def save_checkpoint(
    path: Path,
    model: SingleChunkFutureLabelPredictor,
    optimizer: torch.optim.Optimizer,
    scheduler: torch.optim.lr_scheduler.LRScheduler,
    config: ProbeConfig,
    step: int,
    epoch: int,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "model": model.state_dict(),
            "decoder": model.decoder.state_dict(),
            "optimizer": optimizer.state_dict(),
            "scheduler": scheduler.state_dict(),
            "config": to_manifest_config(config),
            "step": int(step),
            "epoch": int(epoch),
            "class_names": CLASS_NAMES,
        },
        path,
    )


if __name__ == "__main__":
    raise SystemExit(main())
