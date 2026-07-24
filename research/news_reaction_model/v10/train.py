from __future__ import annotations

import argparse
import importlib.util
import json
import math
import random
import signal
import time
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import torch

from research.mlops.checkpoints import AsyncCheckpointManager, CheckpointPolicy
from research.mlops.env import discover_env_files, load_env_files
from research.mlops.manifest import write_run_manifest
from research.mlops.metrics import JsonlMetricLogger
from research.mlops.model_artifacts import parameter_summary, write_model_artifacts, write_model_card
from research.mlops.paths import RunPaths
from research.mlops.wandb_utils import init_wandb
from research.news_reaction_model.v10 import HORIZONS, MODEL_FAMILY, MODEL_VERSION
from research.news_reaction_model.v10.config import ExperimentConfig, LoaderConfig, ModelConfig, TrainConfig, default_run_name, to_dict
from research.news_reaction_model.v10.data import ClickHouseNewsReactionDataset, NewsReactionBatch, audit_prepared_dataset, make_dummy_batch
from research.news_reaction_model.v10.evaluate import evaluate_checkpoint
from research.news_reaction_model.v10.losses import compute_loss
from research.news_reaction_model.v10.metrics import OpportunityAccumulator
from research.news_reaction_model.v10.model import NewsReactionModelV10, build_model_mermaid
from research.news_reaction_model.v10.opportunity import opportunity_contract

REPO_ROOT = Path(__file__).resolve().parents[3]
_INTERRUPTED = False


def handle_interrupt(_signum: int, _frame: Any) -> None:
    global _INTERRUPTED
    _INTERRUPTED = True
    print("Interrupt received; stopping after the current batch and saving a checkpoint.", flush=True)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    loader, model, train = LoaderConfig(), ModelConfig(), TrainConfig()
    parser = argparse.ArgumentParser(
        description="Train V10: the V8 encoder with one three-class opportunity head per horizon."
    )
    parser.add_argument("--train-start", default=loader.train_start)
    parser.add_argument("--train-end-exclusive", default=loader.train_end_exclusive)
    parser.add_argument("--validation-start", default=loader.validation_start)
    parser.add_argument("--validation-end-exclusive", default=loader.validation_end_exclusive)
    parser.add_argument("--batch-size", type=int, default=loader.batch_size)
    parser.add_argument("--query-batch-articles", type=int, default=loader.query_batch_articles)
    parser.add_argument("--loader-workers", type=int, default=loader.workers)
    parser.add_argument("--prefetch-batches", type=int, default=loader.prefetch_batches)
    parser.add_argument("--max-threads-per-query", type=int, default=loader.max_threads_per_query)
    parser.add_argument("--max-memory-usage", default=loader.max_memory_usage)
    parser.add_argument("--dataset-database", default=loader.dataset_database)
    parser.add_argument("--dataset-table", default=loader.dataset_table)
    parser.add_argument("--dataset-version", default=loader.dataset_version)
    parser.add_argument("--representation-artifact-root", default=str(loader.representation_artifact_root))
    parser.add_argument("--openai-embedding-dim", type=int, default=loader.openai_embedding_dim)
    parser.add_argument("--stock-state-dim", type=int, default=loader.stock_state_dim)
    parser.add_argument("--d-model", type=int, default=model.d_model)
    parser.add_argument("--hidden-dim", type=int, default=model.hidden_dim)
    parser.add_argument("--layers", type=int, default=model.layers)
    parser.add_argument("--dropout", type=float, default=model.dropout)
    parser.add_argument("--epochs", type=int, default=train.epochs)
    parser.add_argument("--max-samples", type=int, default=train.max_samples)
    parser.add_argument("--learning-rate", type=float, default=train.learning_rate)
    parser.add_argument("--weight-decay", type=float, default=train.weight_decay)
    parser.add_argument("--grad-clip-norm", type=float, default=train.grad_clip_norm)
    parser.add_argument("--scheduler", choices=("none", "cosine"), default=train.scheduler)
    parser.add_argument("--scheduler-restarts", type=int, default=train.scheduler_restarts)
    parser.add_argument("--scheduler-eta-min", type=float, default=train.scheduler_eta_min)
    parser.add_argument("--amp", action=argparse.BooleanOptionalAction, default=train.amp)
    parser.add_argument("--amp-dtype", choices=("bf16", "fp16", "float32"), default=train.amp_dtype)
    parser.add_argument("--compile-model", action=argparse.BooleanOptionalAction, default=train.compile_model)
    parser.add_argument("--logging-samples", type=int, default=train.logging_samples)
    parser.add_argument("--validation-max-batches", type=int, default=train.validation_max_batches)
    parser.add_argument("--checkpoint-latest-samples", type=int, default=train.checkpoint_latest_samples)
    parser.add_argument("--checkpoint-archive-samples", type=int, default=train.checkpoint_archive_samples)
    parser.add_argument("--evaluate-at-end", action=argparse.BooleanOptionalAction, default=train.evaluate_at_end)
    parser.add_argument("--output-root", default=str(train.output_root.parent))
    parser.add_argument("--run-name", default="")
    parser.add_argument("--wandb-project", default=train.wandb_project)
    parser.add_argument("--wandb-entity", default=train.wandb_entity)
    parser.add_argument("--wandb-mode", choices=("auto", "online", "offline", "disabled"), default=train.wandb_mode)
    parser.add_argument("--wandb-init-timeout", type=int, default=train.wandb_init_timeout)
    parser.add_argument("--resume-checkpoint", default="")
    parser.add_argument("--dummy-data", action="store_true")
    parser.add_argument("--dummy-batches", type=int, default=8)
    parser.add_argument("--seed", type=int, default=train.seed)
    return parser.parse_args(list(argv) if argv is not None else None)


def build_config(args: argparse.Namespace) -> ExperimentConfig:
    loader = LoaderConfig(
        dataset_database=args.dataset_database, dataset_table=args.dataset_table, dataset_version=args.dataset_version,
        representation_artifact_root=Path(args.representation_artifact_root),
        openai_embedding_dim=max(1, args.openai_embedding_dim),
        stock_state_dim=max(1, args.stock_state_dim),
        train_start=args.train_start, train_end_exclusive=args.train_end_exclusive,
        validation_start=args.validation_start, validation_end_exclusive=args.validation_end_exclusive,
        batch_size=args.batch_size, query_batch_articles=args.query_batch_articles, workers=args.loader_workers,
        prefetch_batches=args.prefetch_batches, max_threads_per_query=args.max_threads_per_query,
        max_memory_usage=args.max_memory_usage,
    )
    model = ModelConfig(
        openai_embedding_dim=loader.openai_embedding_dim,
        stock_state_dim=loader.stock_state_dim,
        d_model=args.d_model, hidden_dim=args.hidden_dim, layers=args.layers, dropout=args.dropout,
    )
    train = TrainConfig(
        output_root=Path(args.output_root), run_name=args.run_name, epochs=args.epochs, max_samples=args.max_samples,
        learning_rate=args.learning_rate, weight_decay=args.weight_decay, grad_clip_norm=args.grad_clip_norm,
        scheduler=args.scheduler, scheduler_restarts=args.scheduler_restarts, scheduler_eta_min=args.scheduler_eta_min,
        amp=args.amp, amp_dtype=args.amp_dtype, compile_model=args.compile_model,
        logging_samples=args.logging_samples, validation_max_batches=args.validation_max_batches,
        checkpoint_latest_samples=args.checkpoint_latest_samples, checkpoint_archive_samples=args.checkpoint_archive_samples,
        evaluate_at_end=args.evaluate_at_end,
        wandb_project=args.wandb_project, wandb_entity=args.wandb_entity, wandb_mode=args.wandb_mode,
        wandb_init_timeout=args.wandb_init_timeout, seed=args.seed,
    )
    return ExperimentConfig(loader, model, train)


def validate_config(config: ExperimentConfig) -> None:
    if not 1 <= config.train.epochs <= 15:
        raise ValueError("V10 training requires --epochs between 1 and 15 inclusive")
    if config.train.scheduler == "cosine" and (
        config.train.scheduler_restarts < 0 or config.train.scheduler_restarts >= config.train.epochs
    ):
        raise ValueError("--scheduler-restarts must be nonnegative and less than --epochs")


def main(argv: Iterable[str] | None = None) -> int:
    signal.signal(signal.SIGINT, handle_interrupt)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, handle_interrupt)
    load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args(argv)
    set_seed(args.seed)
    config = build_config(args)
    validate_config(config)
    config.train.run_name = default_run_name(config)
    train_articles = config.loader.batch_size * max(1, args.dummy_batches)
    if not args.dummy_data:
        train_audit = audit_prepared_dataset(config.loader, config.loader.train_start, config.loader.train_end_exclusive)
        validation_audit = audit_prepared_dataset(config.loader, config.loader.validation_start, config.loader.validation_end_exclusive)
        if train_audit["representation_sha256"] != validation_audit["representation_sha256"]:
            raise RuntimeError("V10 train and validation rows were built by different representations.")
        manifest_path = config.loader.representation_artifact_root / "manifest.json"
        if not manifest_path.exists():
            raise RuntimeError(f"V10 representation manifest is missing: {manifest_path}")
        feature_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if train_audit["representation_sha256"] != feature_manifest["representation_sha256"]:
            raise RuntimeError(
                "Prepared V10 rows do not match the checksummed OpenAI-plus-stock-state representation."
            )
        print(
            f"DATASET ready | train={train_audit['rows']:,} articles | validation={validation_audit['rows']:,} articles | "
            f"version={config.loader.dataset_version}",
            flush=True,
        )
        train_articles = int(train_audit["rows"])
    run_root = Path(config.train.output_root) / config.train.run_name
    paths = RunPaths.create(run_root)
    (paths.run_root / "config.json").write_text(json.dumps(to_dict(config), indent=2, default=str), encoding="utf-8")
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if device.type == "cuda":
        torch.set_float32_matmul_precision("high")
    raw_model = NewsReactionModelV10(config.model).to(device)
    wandb_run = init_wandb(entity=config.train.wandb_entity, project=config.train.wandb_project,
                           run_name=config.train.run_name, config=to_dict(config), run_dir=paths.wandb_dir,
                           mode=config.train.wandb_mode, timeout_seconds=config.train.wandb_init_timeout)
    write_run_manifest(paths.manifest_path, repo_root=REPO_ROOT, model_family=MODEL_FAMILY, version=MODEL_VERSION,
                       job_type="train", run_name=config.train.run_name, args=vars(args), config=to_dict(config),
                       data_roots={"prepared_dataset": f"{config.loader.dataset_database}.{config.loader.dataset_table}",
                                   "dataset_version": config.loader.dataset_version,
                                   "source_model_version": "v8",
                                   "representation": config.loader.representation_name,
                                   "feature_artifacts": str(config.loader.representation_artifact_root),
                                   "openai_embedding_version": config.loader.embedding_version,
                                   "source_reactions": f"{config.loader.news_database}.{config.loader.reaction_table}"},
                       output_root=paths.run_root,
                       wandb_info={"project": config.train.wandb_project, "run_id": getattr(wandb_run, "id", "")})
    write_model_artifacts(
        model=raw_model, artifact_dir=paths.artifacts_dir, model_config=config.model,
        input_contract={
            "openai_embedding": ["B", config.loader.openai_embedding_dim],
            "stock_state": ["B", config.loader.stock_state_dim],
            "channel_mask": ["B", 2],
        },
        output_contract={"opportunity_logits": opportunity_contract()},
        architecture_mermaid=build_model_mermaid(),
        summary_notes=(
            "V10 retains V8's OpenAI embedding, stock-state input, and encoder. Only the supervised "
            "output is replaced by one three-class opportunity head per horizon."
        ),
        dummy_input_factory=lambda: ((make_dummy_batch(2, config.loader, device=device).x,), {}), wandb_run=wandb_run,
    )
    model = maybe_compile(raw_model, config.train.compile_model)
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.train.learning_rate, weight_decay=config.train.weight_decay, foreach=True)
    planned_samples = train_articles * config.train.epochs
    if config.train.max_samples > 0:
        planned_samples = min(planned_samples, config.train.max_samples)
    scheduler = (
        SampleCosineRestartScheduler(optimizer, config.train, planned_samples)
        if config.train.scheduler == "cosine" else None
    )
    scaler = torch.amp.GradScaler("cuda", enabled=device.type == "cuda" and config.train.amp and config.train.amp_dtype == "fp16")
    samples_seen, epoch_start = restore(args.resume_checkpoint, raw_model, optimizer, scheduler, scaler, device)
    logger = JsonlMetricLogger(paths.metrics_path, wandb_run)
    checkpointer = AsyncCheckpointManager(paths.checkpoints_dir, paths.checkpoint_manifest_path, CheckpointPolicy(
        latest_steps=max(1, config.train.checkpoint_latest_samples), archive_steps=max(0, config.train.checkpoint_archive_samples),
        monitor_train_key="train/loss", monitor_val_key="val/log_loss", clock_name="sample", archive_prefix="checkpoint_sample",
    ))
    next_log = samples_seen
    last_train: dict[str, float] = {}
    last_val: dict[str, float] = {}
    started = time.perf_counter()
    try:
        for epoch in range(epoch_start, config.train.epochs):
            if _INTERRUPTED:
                break
            iterator = dummy_batches(config, device, args.dummy_batches) if args.dummy_data else real_batches(config, train=True)
            model.train()
            for batch in iterator:
                if _INTERRUPTED or config.train.max_samples > 0 and samples_seen >= config.train.max_samples:
                    break
                batch = batch.to(device)
                step_started = time.perf_counter()
                optimizer.zero_grad(set_to_none=True)
                with torch.autocast(device_type=device.type, dtype=amp_dtype(config.train.amp_dtype), enabled=device.type == "cuda" and config.train.amp):
                    output = model(batch.x)
                    result = compute_loss(output, batch)
                if scaler.is_enabled():
                    scaler.scale(result.loss).backward(); scaler.unscale_(optimizer)
                    torch.nn.utils.clip_grad_norm_(model.parameters(), config.train.grad_clip_norm)
                    scaler.step(optimizer); scaler.update()
                else:
                    result.loss.backward(); torch.nn.utils.clip_grad_norm_(model.parameters(), config.train.grad_clip_norm); optimizer.step()
                samples_seen += batch.sample_count
                if scheduler is not None:
                    scheduler.step(samples_seen)
                last_train = {**result.metrics, "train/samples_seen": float(samples_seen), "train/epoch": float(epoch + 1),
                              "train/learning_rate": float(optimizer.param_groups[0]["lr"]),
                              "train/samples_per_second": batch.sample_count / max(time.perf_counter() - step_started, 1e-9)}
                if samples_seen >= next_log:
                    logger.log(last_train, samples_seen)
                    print(
                        f"TRAIN samples={samples_seen:,} epoch={epoch + 1}/{config.train.epochs} "
                        f"loss={last_train['train/loss']:.4f} accuracy={last_train['train/accuracy']:.3f} "
                        f"rate={last_train['train/samples_per_second']:,.0f} articles/s lr={last_train['train/learning_rate']:.2e}",
                        flush=True,
                    )
                    next_log = samples_seen + config.train.logging_samples
                checkpointer.maybe_save(step=samples_seen, payload_factory=lambda: checkpoint_payload(raw_model, optimizer, scheduler, scaler, config, samples_seen, epoch), train_metrics=last_train, val_metrics=last_val)
            last_val = validate(model, config, device, args.dummy_data, args.dummy_batches)
            logger.log({**last_val, "val/epoch": float(epoch + 1)}, samples_seen)
            print_validation_summary(last_val)
            checkpointer.maybe_save(step=samples_seen, payload_factory=lambda: checkpoint_payload(raw_model, optimizer, scheduler, scaler, config, samples_seen, epoch + 1), train_metrics=last_train, val_metrics=last_val, force=True)
    finally:
        checkpointer.close(wait=True, timeout=180)
        if wandb_run is not None:
            wandb_run.finish()
    final_evaluation: dict[str, Any] = {}
    if config.train.evaluate_at_end and not args.dummy_data and not _INTERRUPTED:
        best_checkpoint = paths.checkpoints_dir / "checkpoint_best_val.pt"
        if not best_checkpoint.exists():
            raise RuntimeError(f"Best validation checkpoint was not written: {best_checkpoint}")
        final_evaluation = evaluate_checkpoint(
            best_checkpoint,
            output_dir=paths.run_root / "evaluation",
            start=config.loader.validation_start,
            end_exclusive=config.loader.validation_end_exclusive,
        )
    write_model_card(paths.run_root / "model_card.json", {
        "model_family": MODEL_FAMILY, "version": MODEL_VERSION, "run_name": config.train.run_name,
        "samples_seen": samples_seen, "elapsed_seconds": time.perf_counter() - started,
        "train_range": [config.loader.train_start, config.loader.train_end_exclusive],
        "validation_range": [config.loader.validation_start, config.loader.validation_end_exclusive],
        "single_ticker_only": True, "exact_join": ["source_id=canonical_news_id", "ticker", "published_at_utc"],
        "final_validation": last_val,
        "best_checkpoint_position_evaluation": final_evaluation,
    })
    return 130 if _INTERRUPTED else 0


def real_batches(config: ExperimentConfig, *, train: bool) -> Iterable[NewsReactionBatch]:
    start = config.loader.train_start if train else config.loader.validation_start
    end = config.loader.train_end_exclusive if train else config.loader.validation_end_exclusive
    dataset = ClickHouseNewsReactionDataset(config.loader, start=start, end_exclusive=end, shuffle_months=train, seed=config.train.seed)
    try:
        yield from dataset.iter_batches()
    finally:
        dataset.stop()


def dummy_batches(config: ExperimentConfig, device: torch.device, count: int) -> Iterable[NewsReactionBatch]:
    for _ in range(max(1, count)):
        yield make_dummy_batch(config.loader.batch_size, config.loader, device=device)


def print_validation_summary(metrics: dict[str, float]) -> None:
    print(
        f"VALIDATION samples={int(metrics.get('val/samples', 0)):,} "
        f"loss={metrics.get('val/loss', 0.0):.4f} accuracy={metrics.get('val/accuracy', 0.0):.3f} "
        f"macro_f1={metrics.get('val/macro_f1', 0.0):.3f} "
        f"balanced_accuracy={metrics.get('val/balanced_accuracy', 0.0):.3f} "
        f"log_loss={metrics.get('val/log_loss', 0.0):.4f} confidence={metrics.get('val/mean_confidence', 0.0):.3f}",
        flush=True,
    )
    horizon_parts = []
    for horizon in HORIZONS:
        count_key = f"val/{horizon}/samples"
        if count_key in metrics:
            horizon_parts.append(
                f"{horizon}:{int(metrics[count_key])}/{metrics[f'val/{horizon}/macro_f1']:.2f}"
            )
    if horizon_parts:
        print("HORIZONS labels/macro_f1 | " + "  ".join(horizon_parts), flush=True)


@torch.no_grad()
def validate(model: torch.nn.Module, config: ExperimentConfig, device: torch.device, dummy: bool, dummy_count: int) -> dict[str, float]:
    model.eval(); accumulator = OpportunityAccumulator(); loss_sum = 0.0; batches = 0
    iterator = dummy_batches(config, device, min(dummy_count, 2)) if dummy else real_batches(config, train=False)
    for batch in iterator:
        batch = batch.to(device)
        with torch.autocast(device_type=device.type, dtype=amp_dtype(config.train.amp_dtype), enabled=device.type == "cuda" and config.train.amp):
            output = model(batch.x)
            result = compute_loss(output, batch)
        accumulator.add(output, batch.return_targets, batch.label_mask)
        loss_sum += float(result.loss.detach().cpu()); batches += 1
        if config.train.validation_max_batches > 0 and batches >= config.train.validation_max_batches:
            break
    metrics = accumulator.compute("val")
    metrics["val/loss"] = loss_sum / max(batches, 1); metrics["val/batches"] = float(batches)
    return metrics


class SampleCosineRestartScheduler:
    def __init__(self, optimizer: torch.optim.Optimizer, config: TrainConfig, planned_samples: int) -> None:
        self.optimizer, self.base_lrs = optimizer, [float(group["lr"]) for group in optimizer.param_groups]
        self.planned_samples = max(1, int(planned_samples))
        self.restarts = int(config.scheduler_restarts)
        self.cycles = self.restarts + 1
        self.cycle = max(1, math.ceil(self.planned_samples / self.cycles))
        self.eta_min, self.samples = config.scheduler_eta_min, 0

    def cycle_index(self, samples: int) -> int:
        return min(max(0, int(samples)) // self.cycle, self.restarts)

    def step(self, samples: int) -> None:
        self.samples = min(max(0, int(samples)), self.planned_samples)
        cycle_index = self.cycle_index(self.samples)
        cycle_start = cycle_index * self.cycle
        cycle_end = (
            self.planned_samples
            if cycle_index == self.restarts
            else min((cycle_index + 1) * self.cycle, self.planned_samples)
        )
        position = (self.samples - cycle_start) / max(1, cycle_end - cycle_start)
        position = min(max(position, 0.0), 1.0)
        for base, group in zip(self.base_lrs, self.optimizer.param_groups):
            group["lr"] = self.eta_min + 0.5 * (base - self.eta_min) * (1 + math.cos(math.pi * position))

    def state_dict(self) -> dict[str, Any]:
        return {
            "samples": self.samples,
            "base_lrs": self.base_lrs,
            "planned_samples": self.planned_samples,
            "restarts": self.restarts,
            "cycle": self.cycle,
        }

    def load_state_dict(self, state: dict[str, Any]) -> None:
        if (
            int(state.get("planned_samples", self.planned_samples)) != self.planned_samples
            or int(state.get("restarts", self.restarts)) != self.restarts
        ):
            raise ValueError("checkpoint scheduler plan does not match the current sample count and restart count")
        self.samples = int(state.get("samples", 0))
        self.base_lrs = list(state.get("base_lrs", self.base_lrs))
        self.step(self.samples)


def checkpoint_payload(model: torch.nn.Module, optimizer: torch.optim.Optimizer, scheduler: Any, scaler: Any, config: ExperimentConfig, samples: int, epoch: int) -> dict[str, Any]:
    serializable_config = json.loads(json.dumps(to_dict(config), default=str))
    return {"model": model.state_dict(), "optimizer": optimizer.state_dict(), "scheduler": scheduler.state_dict() if scheduler else None,
            "scaler": scaler.state_dict(), "config": serializable_config, "samples_seen": samples, "epoch": epoch}


def restore(path: str, model: torch.nn.Module, optimizer: torch.optim.Optimizer, scheduler: Any, scaler: Any, device: torch.device) -> tuple[int, int]:
    if not path: return 0, 0
    # PyTorch 2.6+ defaults to restricted weights-only loading. Older versioned
    # checkpoints contain only one non-default safe type: the local output-root
    # WindowsPath stored in config metadata. Allowlist that exact type while
    # keeping restricted loading enabled; new checkpoints stringify paths.
    with torch.serialization.safe_globals([type(Path())]):
        state = torch.load(path, map_location=device, weights_only=True)
    model.load_state_dict(state["model"]); optimizer.load_state_dict(state["optimizer"])
    if scheduler and state.get("scheduler"): scheduler.load_state_dict(state["scheduler"])
    if state.get("scaler"): scaler.load_state_dict(state["scaler"])
    return int(state.get("samples_seen", 0)), int(state.get("epoch", 0))


def maybe_compile(model: torch.nn.Module, enabled: bool) -> torch.nn.Module:
    if not enabled:
        return model
    if not hasattr(torch, "compile"):
        print("WARN --compile-model requested, but this PyTorch build does not expose torch.compile.", flush=True)
        return model
    if torch.cuda.is_available() and importlib.util.find_spec("triton") is None:
        print("WARN --compile-model requested, but Triton is unavailable; continuing without torch.compile.", flush=True)
        return model
    print("Compiling model with torch.compile...", flush=True)
    return torch.compile(model)


def amp_dtype(name: str) -> torch.dtype:
    return torch.bfloat16 if name == "bf16" else torch.float16 if name == "fp16" else torch.float32


def set_seed(seed: int) -> None:
    random.seed(seed); np.random.seed(seed); torch.manual_seed(seed)
    if torch.cuda.is_available(): torch.cuda.manual_seed_all(seed)


if __name__ == "__main__":
    raise SystemExit(main())
