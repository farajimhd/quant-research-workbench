from __future__ import annotations

import argparse
import json
import os
import random
import signal
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import torch

REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.checkpoints import AsyncCheckpointManager, CheckpointPolicy
from research.mlops.env import discover_env_files, load_env_files
from research.mlops.manifest import write_run_manifest
from research.mlops.metrics import JsonlMetricLogger
from research.mlops.model_artifacts import parameter_summary, write_model_artifacts, write_model_card
from research.mlops.packed_market import PackedMarketDataset, PackedMarketDatasetConfig
from research.mlops.paths import RunPaths, default_run_root
from research.mlops.wandb_utils import init_wandb as mlops_init_wandb
from research.packed_market_model.v1 import MODEL_FAMILY, MODEL_VERSION
from research.packed_market_model.v1.config import ExperimentConfig, LoaderConfig, ModelConfig, TrainConfig, default_run_name, parse_csv, to_dict
from research.packed_market_model.v1.data import PackedTorchBlock, block_to_torch, infer_contract_from_dataset, make_dummy_packed_block
from research.packed_market_model.v1.losses import compute_loss
from research.packed_market_model.v1.metrics import MetricWindow, fast_block_metrics, wandb_metric_key
from research.packed_market_model.v1.model import PackedMarketModelV1, build_model_mermaid
from research.packed_market_model.v1.progress import PackedProgressState, PackedTrainingReporter

JOB_TYPE = "train"
_INTERRUPTED = False


def _handle_interrupt(_signum: int, _frame: Any) -> None:
    global _INTERRUPTED
    _INTERRUPTED = True
    print("\nInterrupt received; saving latest checkpoint and stopping after current block.", file=sys.stderr, flush=True)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    loader = LoaderConfig()
    model = ModelConfig()
    train = TrainConfig()
    parser = argparse.ArgumentParser(description="Train packed_market_model v1 on packed market block cache.")
    parser.add_argument("--cache-root", default=str(loader.cache_root))
    parser.add_argument("--months", default="")
    parser.add_argument("--tickers", default="")
    parser.add_argument("--shuffle-blocks", action=argparse.BooleanOptionalAction, default=loader.shuffle_blocks)
    parser.add_argument("--max-blocks", type=int, default=train.max_blocks)
    parser.add_argument("--output-root", default=str(train.output_root))
    parser.add_argument("--run-name", default="")
    parser.add_argument("--dataset-id", default="packed-market-cache")
    parser.add_argument("--d-model", type=int, default=model.d_model)
    parser.add_argument("--event-layers", type=int, default=model.event_layers)
    parser.add_argument("--event-kernel-size", type=int, default=model.event_kernel_size)
    parser.add_argument("--head-hidden-dim", type=int, default=model.head_hidden_dim)
    parser.add_argument("--max-samples", type=int, default=train.max_samples)
    parser.add_argument("--epochs", type=int, default=train.epochs)
    parser.add_argument("--learning-rate", type=float, default=train.learning_rate)
    parser.add_argument("--weight-decay", type=float, default=train.weight_decay)
    parser.add_argument("--grad-clip-norm", type=float, default=train.grad_clip_norm)
    parser.add_argument("--scheduler", choices=("none", "cosine"), default=train.scheduler)
    parser.add_argument("--scheduler-eta-min", type=float, default=train.scheduler_eta_min)
    parser.add_argument("--scheduler-cycle-samples", type=int, default=train.scheduler_cycle_samples)
    parser.add_argument("--scheduler-decay-cycles", type=int, default=train.scheduler_decay_cycles)
    parser.add_argument("--scheduler-decay-factor", type=float, default=train.scheduler_decay_factor)
    parser.add_argument("--amp", action=argparse.BooleanOptionalAction, default=train.amp)
    parser.add_argument("--amp-dtype", choices=("bf16", "fp16", "float16", "bfloat16", "float32"), default=train.amp_dtype)
    parser.add_argument("--compile-model", action=argparse.BooleanOptionalAction, default=train.compile_model)
    parser.add_argument("--logging-samples", type=int, default=train.logging_samples)
    parser.add_argument("--validation-samples", type=int, default=train.validation_samples)
    parser.add_argument("--checkpoint-latest-samples", type=int, default=train.checkpoint_latest_samples)
    parser.add_argument("--checkpoint-archive-samples", type=int, default=train.checkpoint_archive_samples)
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text", "none"), default=train.progress_layout)
    parser.add_argument("--wandb-project", default=train.wandb_project)
    parser.add_argument("--wandb-entity", default=train.wandb_entity)
    parser.add_argument("--wandb-mode", choices=("auto", "online", "offline", "disabled"), default=train.wandb_mode)
    parser.add_argument("--wandb-init-timeout", type=int, default=train.wandb_init_timeout)
    parser.add_argument("--resume-checkpoint", default="")
    parser.add_argument("--dummy-data", action="store_true")
    parser.add_argument("--seed", type=int, default=train.seed)
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    signal.signal(signal.SIGINT, _handle_interrupt)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _handle_interrupt)
    load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args(argv)
    set_seed(int(args.seed))
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if torch.cuda.is_available():
        torch.set_float32_matmul_precision("high")
    config, dataset = build_config(args)
    run_name = default_run_name(config)
    config.train.run_name = run_name
    run_root = Path(args.output_root) / run_name if args.output_root else default_run_root(MODEL_FAMILY, MODEL_VERSION, JOB_TYPE, run_name)
    paths = RunPaths.create(run_root)
    write_config(paths.run_root / "config.json", config)
    model = PackedMarketModelV1(config.model).to(device)
    model = maybe_compile_model(model, bool(config.train.compile_model))
    optimizer = torch.optim.AdamW(model.parameters(), lr=float(config.train.learning_rate), weight_decay=float(config.train.weight_decay), foreach=True)
    scheduler = SampleCosineScheduler(optimizer, config.train) if config.train.scheduler == "cosine" else None
    scaler = torch.amp.GradScaler("cuda", enabled=bool(config.train.amp and config.train.amp_dtype in {"fp16", "float16"} and device.type == "cuda"))
    start_samples = restore_checkpoint(args.resume_checkpoint, model, optimizer, scheduler, scaler, dataset, device)
    wandb_run = init_wandb(args, config, paths)
    metric_logger = JsonlMetricLogger(paths.metrics_path, wandb_run, wandb_key_mapper=wandb_metric_key)
    write_run_manifest(
        paths.manifest_path,
        repo_root=REPO_ROOT,
        model_family=MODEL_FAMILY,
        version=MODEL_VERSION,
        job_type=JOB_TYPE,
        run_name=run_name,
        args=vars(args),
        config=to_dict(config),
        data_roots={"cache_root": str(config.loader.cache_root)},
        output_root=paths.run_root,
        source_checkpoint=Path(args.resume_checkpoint) if args.resume_checkpoint else None,
        wandb_info={"project": args.wandb_project, "entity": args.wandb_entity, "run_name": run_name},
    )
    write_model_artifacts(
        model=unwrap_model(model),
        artifact_dir=paths.artifacts_dir / "model",
        model_config=config.model,
        input_contract=input_contract(config.model),
        output_contract=output_contract(config.model),
        architecture_mermaid=build_model_mermaid(),
        summary_notes="Packed v1 trains directly over event-stream blocks and computes loss over all origins inside each block.",
        dummy_input_factory=lambda: ((), make_dummy_packed_block(model_config=config.model, device=device).x),
        wandb_run=wandb_run,
    )
    checkpointer = AsyncCheckpointManager(
        paths.checkpoints_dir,
        paths.checkpoint_manifest_path,
        CheckpointPolicy(
            latest_steps=max(1, int(config.train.checkpoint_latest_samples)),
            archive_steps=max(0, int(config.train.checkpoint_archive_samples)),
            monitor_train_key="train/loss",
            monitor_val_key="val/loss",
            clock_name="sample",
            archive_prefix="checkpoint_sample",
        ),
    )
    state = PackedProgressState(
        run_name=run_name,
        dataset_id=args.dataset_id,
        device=str(device),
        precision=config.train.amp_dtype if config.train.amp else "float32",
        output_dir=str(paths.run_root),
        model_parameters=int(parameter_summary(unwrap_model(model))["total_parameters"]),
        max_samples=int(config.train.max_samples),
    )
    reporter = None if config.train.progress_layout == "none" else PackedTrainingReporter(state=state, layout=config.train.progress_layout)
    iterator = dummy_iterator(config.model, device) if args.dummy_data else dataset.iter_blocks(repeat=int(config.train.epochs) > 1)
    window = MetricWindow(max_batches=16)
    samples_seen = int(start_samples)
    blocks_seen = 0
    next_log = samples_seen
    try:
        with reporter if reporter is not None else NullReporter() as active:
            checkpointer.set_message_callback(active.message)
            active.message("Packed block trainer initialized.")
            while True:
                if _INTERRUPTED:
                    break
                if int(config.train.max_samples) > 0 and samples_seen >= int(config.train.max_samples):
                    break
                loader_start = time.perf_counter()
                try:
                    raw_block = next(iterator)
                except StopIteration:
                    break
                loader_wait = time.perf_counter() - loader_start
                block = raw_block if isinstance(raw_block, PackedTorchBlock) else block_to_torch(raw_block, model_config=config.model, device=device)
                block_start = time.perf_counter()
                optimizer.zero_grad(set_to_none=True)
                amp_dtype = amp_dtype_from_name(config.train.amp_dtype)
                gpu_start = time.perf_counter()
                with torch.autocast(device_type=device.type, dtype=amp_dtype, enabled=bool(config.train.amp and device.type == "cuda")):
                    output = model.forward_with_timings(block.x, sync_cuda=device.type == "cuda") if hasattr(model, "forward_with_timings") else model(block.x)
                    loss_result = compute_loss(output, block)
                    loss = loss_result.loss
                if scaler.is_enabled():
                    scaler.scale(loss).backward()
                    if float(config.train.grad_clip_norm) > 0:
                        scaler.unscale_(optimizer)
                        torch.nn.utils.clip_grad_norm_(model.parameters(), float(config.train.grad_clip_norm))
                    scaler.step(optimizer)
                    scaler.update()
                else:
                    loss.backward()
                    if float(config.train.grad_clip_norm) > 0:
                        torch.nn.utils.clip_grad_norm_(model.parameters(), float(config.train.grad_clip_norm))
                    optimizer.step()
                if device.type == "cuda":
                    torch.cuda.synchronize()
                gpu_seconds = time.perf_counter() - gpu_start
                samples_seen += int(block.origin_count)
                blocks_seen += 1
                if scheduler is not None:
                    scheduler.step(samples_seen)
                block_seconds = time.perf_counter() - block_start
                metrics = dict(loss_result.metrics)
                metrics.update(fast_block_metrics(block, output, prefix="train"))
                metrics.update(
                    {
                        "train/learning_rate": float(optimizer.param_groups[0]["lr"]),
                        "train/samples_seen_total": float(samples_seen),
                        "train/blocks_seen": float(blocks_seen),
                        "train/block_seconds": float(block_seconds),
                        "train/loader_wait_seconds": float(loader_wait),
                        "train/gpu_seconds": float(gpu_seconds),
                        "train/samples_per_second": float(block.origin_count / max(block_seconds + loader_wait, 1e-9)),
                    }
                )
                metrics.update({f"profile/model/{k}": float(v) for k, v in getattr(output, "profile", {}).items()})
                if dataset is not None and hasattr(dataset, "telemetry_snapshot"):
                    metrics.update(dataset.telemetry_snapshot())
                window.add(metrics)
                active.update(metrics)
                if samples_seen >= next_log or blocks_seen == 1:
                    metric_logger.log(metrics, samples_seen)
                    next_log = samples_seen + int(config.train.logging_samples)
                checkpointer.maybe_save(
                    step=samples_seen,
                    payload_factory=lambda: checkpoint_payload(model, optimizer, scheduler, scaler, dataset, config, samples_seen, blocks_seen),
                    train_metrics=metrics,
                )
                if int(config.train.max_blocks) > 0 and blocks_seen >= int(config.train.max_blocks):
                    break
            active.message("Saving final checkpoint.")
            checkpointer.maybe_save(
                step=samples_seen,
                payload_factory=lambda: checkpoint_payload(model, optimizer, scheduler, scaler, dataset, config, samples_seen, blocks_seen),
                train_metrics={"train/loss": float(state.loss)},
                force=True,
            )
    finally:
        checkpointer.close(wait=True, timeout=120)
        if wandb_run is not None:
            try:
                wandb_run.finish()
            except Exception:
                pass
    write_model_card(
        paths.run_root / "model_card.json",
        {
            "model_family": MODEL_FAMILY,
            "version": MODEL_VERSION,
            "run_name": run_name,
            "samples_seen": int(samples_seen),
            "blocks_seen": int(blocks_seen),
            "cache_root": str(config.loader.cache_root),
            "months": config.loader.months,
            "tickers": config.loader.tickers,
        },
    )
    return 130 if _INTERRUPTED else 0


def build_config(args: argparse.Namespace) -> tuple[ExperimentConfig, PackedMarketDataset | None]:
    dataset = None
    loader = LoaderConfig(cache_root=Path(args.cache_root), months=parse_csv(args.months), tickers=parse_csv(args.tickers), shuffle_blocks=bool(args.shuffle_blocks), seed=int(args.seed), max_blocks=int(args.max_blocks))
    if args.dummy_data:
        model = ModelConfig(d_model=int(args.d_model), event_layers=int(args.event_layers), event_kernel_size=int(args.event_kernel_size), head_hidden_dim=int(args.head_hidden_dim), event_feature_names=tuple(f"feature_{i}" for i in range(16)), event_feature_dim=16, label_names=("future_trade_close", "future_halt_flag"))
    else:
        infer_dataset = PackedMarketDataset(PackedMarketDatasetConfig(cache_root=loader.cache_root, months=loader.months, tickers=loader.tickers, shuffle_blocks=False, seed=int(args.seed), max_blocks=1))
        event_names, label_names, event_dim = infer_contract_from_dataset(infer_dataset)
        dataset = PackedMarketDataset(PackedMarketDatasetConfig(cache_root=loader.cache_root, months=loader.months, tickers=loader.tickers, shuffle_blocks=loader.shuffle_blocks, seed=int(args.seed), max_blocks=int(args.max_blocks)))
        model = ModelConfig(d_model=int(args.d_model), event_layers=int(args.event_layers), event_kernel_size=int(args.event_kernel_size), head_hidden_dim=int(args.head_hidden_dim), event_feature_names=event_names, event_feature_dim=event_dim, label_names=label_names)
    train = TrainConfig(
        output_root=Path(args.output_root),
        run_name=args.run_name,
        max_samples=int(args.max_samples),
        max_blocks=int(args.max_blocks),
        epochs=int(args.epochs),
        learning_rate=float(args.learning_rate),
        weight_decay=float(args.weight_decay),
        grad_clip_norm=float(args.grad_clip_norm),
        scheduler=str(args.scheduler),
        scheduler_eta_min=float(args.scheduler_eta_min),
        scheduler_cycle_samples=int(args.scheduler_cycle_samples),
        scheduler_decay_cycles=int(args.scheduler_decay_cycles),
        scheduler_decay_factor=float(args.scheduler_decay_factor),
        amp=bool(args.amp),
        amp_dtype=str(args.amp_dtype),
        compile_model=bool(args.compile_model),
        logging_samples=int(args.logging_samples),
        validation_samples=int(args.validation_samples),
        checkpoint_latest_samples=int(args.checkpoint_latest_samples),
        checkpoint_archive_samples=int(args.checkpoint_archive_samples),
        progress_layout=str(args.progress_layout),
        wandb_project=str(args.wandb_project),
        wandb_entity=str(args.wandb_entity),
        wandb_mode=str(args.wandb_mode),
        wandb_init_timeout=int(args.wandb_init_timeout),
        seed=int(args.seed),
    )
    return ExperimentConfig(loader=loader, model=model, train=train), dataset


class SampleCosineScheduler:
    def __init__(self, optimizer: torch.optim.Optimizer, config: TrainConfig) -> None:
        self.optimizer = optimizer
        self.base_lrs = [float(group["lr"]) for group in optimizer.param_groups]
        self.eta_min = float(config.scheduler_eta_min)
        self.cycle_samples = max(1, int(config.scheduler_cycle_samples))
        self.decay_cycles = max(1, int(config.scheduler_decay_cycles))
        self.decay_factor = float(config.scheduler_decay_factor)
        self.samples = 0

    def step(self, samples: int) -> None:
        import math

        self.samples = max(0, int(samples))
        cycle = self.samples // self.cycle_samples
        pos = (self.samples % self.cycle_samples) / float(self.cycle_samples)
        decay_power = cycle // self.decay_cycles
        for base_lr, group in zip(self.base_lrs, self.optimizer.param_groups):
            peak = base_lr * (self.decay_factor ** decay_power)
            group["lr"] = self.eta_min + 0.5 * (peak - self.eta_min) * (1.0 + math.cos(math.pi * pos))

    def state_dict(self) -> dict[str, Any]:
        return {"samples": int(self.samples), "base_lrs": self.base_lrs}

    def load_state_dict(self, value: dict[str, Any]) -> None:
        self.samples = int(value.get("samples", 0) or 0)
        self.base_lrs = [float(v) for v in value.get("base_lrs", self.base_lrs)]
        self.step(self.samples)


def checkpoint_payload(model: torch.nn.Module, optimizer: torch.optim.Optimizer, scheduler: Any, scaler: Any, dataset: PackedMarketDataset | None, config: ExperimentConfig, samples_seen: int, blocks_seen: int) -> dict[str, Any]:
    return {
        "model": unwrap_model(model).state_dict(),
        "optimizer": optimizer.state_dict(),
        "scheduler": scheduler.state_dict() if scheduler is not None else None,
        "scaler": scaler.state_dict() if scaler is not None else None,
        "dataset_state": dataset.state_dict() if dataset is not None else None,
        "config": to_dict(config),
        "samples_seen": int(samples_seen),
        "blocks_seen": int(blocks_seen),
    }


def restore_checkpoint(path: str, model: torch.nn.Module, optimizer: torch.optim.Optimizer, scheduler: Any, scaler: Any, dataset: PackedMarketDataset | None, device: torch.device) -> int:
    if not path:
        return 0
    payload = torch.load(path, map_location=device)
    unwrap_model(model).load_state_dict(payload["model"])
    optimizer.load_state_dict(payload["optimizer"])
    if scheduler is not None and payload.get("scheduler") is not None:
        scheduler.load_state_dict(payload["scheduler"])
    if scaler is not None and payload.get("scaler") is not None:
        scaler.load_state_dict(payload["scaler"])
    if dataset is not None and payload.get("dataset_state") is not None:
        dataset.load_state_dict(payload["dataset_state"])
    return int(payload.get("samples_seen", 0) or 0)


def init_wandb(args: argparse.Namespace, config: ExperimentConfig, paths: RunPaths) -> Any | None:
    return mlops_init_wandb(
        entity=args.wandb_entity,
        project=args.wandb_project,
        run_name=config.train.run_name,
        config=to_dict(config),
        run_dir=paths.wandb_dir,
        mode=args.wandb_mode,
        timeout_seconds=int(args.wandb_init_timeout),
    )


def maybe_compile_model(model: torch.nn.Module, enabled: bool) -> torch.nn.Module:
    if not enabled or not hasattr(torch, "compile"):
        return model
    try:
        return torch.compile(model, mode="default")  # type: ignore[return-value]
    except Exception as exc:
        print(f"torch.compile unavailable: {exc!r}", flush=True)
        return model


def unwrap_model(model: torch.nn.Module) -> torch.nn.Module:
    return getattr(model, "_orig_mod", model)


def amp_dtype_from_name(name: str) -> torch.dtype:
    if name in {"bf16", "bfloat16"}:
        return torch.bfloat16
    if name in {"fp16", "float16"}:
        return torch.float16
    return torch.float32


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def dummy_iterator(model_config: ModelConfig, device: torch.device) -> Iterable[PackedTorchBlock]:
    while True:
        yield make_dummy_packed_block(model_config=model_config, device=device)


class NullReporter:
    def __enter__(self) -> "NullReporter":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def update(self, _metrics: dict[str, Any]) -> None:
        return None

    def message(self, text: str) -> None:
        print(text, flush=True)


def write_config(path: Path, config: ExperimentConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(to_dict(config), indent=2, default=str), encoding="utf-8")


def input_contract(config: ModelConfig) -> dict[str, Any]:
    return {
        "events": ["T", int(config.event_feature_dim), "float32 event stream stored once per block"],
        "origin_positions": ["M", "int64 positions into events"],
        "event_feature_names": list(config.event_feature_names),
    }


def output_contract(config: ModelConfig) -> dict[str, Any]:
    return {"label_predictions": {name: ["M"] for name in config.label_names}}


if __name__ == "__main__":
    raise SystemExit(main())
