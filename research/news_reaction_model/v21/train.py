from __future__ import annotations

import argparse
import dataclasses
import json
import math
import random
from pathlib import Path
from typing import Any

import numpy as np
import torch

from research.mlops.env import discover_env_files, load_env_files
from research.news_reaction_model.v21 import MODEL_FAMILY, MODEL_VERSION
from research.news_reaction_model.v21.config import (
    ExperimentConfig,
    LoaderConfig,
    ModelConfig,
    TrainConfig,
    default_run_name,
)
from research.news_reaction_model.v21.data import PreparedEpisodeDataset
from research.news_reaction_model.v21.evaluate import evaluate_checkpoint
from research.news_reaction_model.v21.losses import LossResult, compute_loss
from research.news_reaction_model.v21.metrics import HierarchicalAccumulator
from research.news_reaction_model.v21.model import (
    NewsReactionModelV21,
    build_model_mermaid,
)
from research.news_reaction_model.v21.targets import (
    DIRECTION_NAMES,
    TrainingStatistics,
    fit_training_statistics,
)


REPO_ROOT = Path(__file__).resolve().parents[3]


def json_safe(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return json_safe(dataclasses.asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_safe(item) for item in value]
    return value


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def fit_statistics(config: ExperimentConfig) -> TrainingStatistics:
    dataset = PreparedEpisodeDataset(
        config.loader,
        start=config.loader.train_start,
        end_exclusive=config.loader.train_end_exclusive,
    )
    try:
        return fit_training_statistics(
            dataset,
            beta=config.train.effective_number_beta,
            minimum_class_weight=config.train.minimum_class_weight,
            maximum_class_weight=config.train.maximum_class_weight,
        )
    finally:
        dataset.stop()


@torch.no_grad()
def validate(
    model: NewsReactionModelV21,
    config: LoaderConfig,
    statistics: TrainingStatistics,
    device: torch.device,
) -> dict[str, float]:
    dataset = PreparedEpisodeDataset(
        config,
        start=config.validation_start,
        end_exclusive=config.validation_end_exclusive,
    )
    accumulator = HierarchicalAccumulator(statistics)
    model.eval()
    try:
        for batch in dataset.iter_batches():
            moved = batch.to(device)
            accumulator.add(model(moved.x), moved)
    finally:
        dataset.stop()
    model.train()
    return accumulator.compute("val")


def save_checkpoint(
    path: Path,
    *,
    model: NewsReactionModelV21,
    optimizer: torch.optim.Optimizer,
    config: ExperimentConfig,
    statistics: TrainingStatistics,
    epoch: int,
    metrics: dict[str, Any],
    best_score: float,
    epochs_without_improvement: int,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    torch.save(
        {
            "model_family": MODEL_FAMILY,
            "model_version": MODEL_VERSION,
            "epoch": epoch,
            "model_state": model.state_dict(),
            "optimizer_state": optimizer.state_dict(),
            "loader_config": json_safe(config.loader),
            "model_config": json_safe(config.model),
            "train_config": json_safe(config.train),
            "training_statistics": statistics.as_dict(),
            "metrics": metrics,
            "best_score": best_score,
            "epochs_without_improvement": epochs_without_improvement,
        },
        temporary,
    )
    temporary.replace(path)


def learning_rate_for_progress(
    config: TrainConfig,
    *,
    epoch: int,
    batch_index: int,
    batches: int,
) -> float:
    progress = epoch + batch_index / max(batches, 1)
    if progress < config.warmup_epochs:
        return config.learning_rate * (progress + 1.0 / max(batches, 1)) / max(
            config.warmup_epochs, 1
        )
    cycle_progress = progress - config.warmup_epochs
    cycle_index = int(cycle_progress // config.scheduler_cycle_epochs)
    within_cycle = (
        cycle_progress % config.scheduler_cycle_epochs
    ) / config.scheduler_cycle_epochs
    peak = config.learning_rate * (config.scheduler_cycle_decay**cycle_index)
    return config.scheduler_eta_min + 0.5 * (
        peak - config.scheduler_eta_min
    ) * (1.0 + math.cos(math.pi * within_cycle))


def selection_score(metrics: dict[str, float], recall_gate: float = 0.01) -> float:
    score = metrics["val/joint_score"]
    for name in DIRECTION_NAMES:
        recall = metrics[f"val/direction/class/{name}/recall"]
        if recall < recall_gate:
            score -= recall_gate - recall
    return score


def should_early_stop(
    config: TrainConfig,
    *,
    completed_epochs: int,
    epochs_without_improvement: int,
) -> bool:
    return (
        completed_epochs >= config.early_stopping_min_epochs
        and epochs_without_improvement >= config.early_stopping_patience
    )


def build_parser() -> argparse.ArgumentParser:
    defaults = ExperimentConfig()
    parser = argparse.ArgumentParser(
        description="Train V21 over read-only V18/V15 prepared arrays."
    )
    parser.add_argument(
        "--prepared-root", default=str(defaults.loader.prepared_dataset_root)
    )
    parser.add_argument("--v15-root", default=str(defaults.loader.v15_prepared_root))
    parser.add_argument("--output-root", default=str(defaults.train.output_root))
    parser.add_argument("--epochs", type=int, default=defaults.train.epochs)
    parser.add_argument("--batch-size", type=int, default=defaults.loader.batch_size)
    parser.add_argument(
        "--learning-rate", type=float, default=defaults.train.learning_rate
    )
    parser.add_argument("--d-model", type=int, default=defaults.model.d_model)
    parser.add_argument(
        "--current-layers", type=int, default=defaults.model.current_layers
    )
    parser.add_argument(
        "--prior-layers", type=int, default=defaults.model.prior_layers
    )
    parser.add_argument(
        "--cross-attention-layers",
        type=int,
        default=defaults.model.cross_attention_layers,
    )
    parser.add_argument(
        "--attention-heads", type=int, default=defaults.model.attention_heads
    )
    parser.add_argument(
        "--feedforward-dim", type=int, default=defaults.model.feedforward_dim
    )
    parser.add_argument(
        "--expert-count", type=int, default=defaults.model.expert_count
    )
    parser.add_argument(
        "--expert-top-k", type=int, default=defaults.model.expert_top_k
    )
    parser.add_argument(
        "--expert-hidden-dim", type=int, default=defaults.model.expert_hidden_dim
    )
    parser.add_argument(
        "--early-stopping-patience",
        type=int,
        default=defaults.train.early_stopping_patience,
    )
    parser.add_argument(
        "--early-stopping-min-epochs",
        type=int,
        default=defaults.train.early_stopping_min_epochs,
    )
    parser.add_argument(
        "--early-stopping-min-delta",
        type=float,
        default=defaults.train.early_stopping_min_delta,
    )
    parser.add_argument("--run-name", default="")
    parser.add_argument("--wandb-mode", default=defaults.train.wandb_mode)
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--no-evaluate", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    loader = LoaderConfig(
        prepared_dataset_root=Path(args.prepared_root),
        v15_prepared_root=Path(args.v15_root),
        batch_size=max(1, args.batch_size),
    )
    if args.d_model % args.attention_heads:
        raise ValueError("d_model must be divisible by attention_heads.")
    model_config = ModelConfig(
        d_model=args.d_model,
        current_layers=max(1, args.current_layers),
        prior_layers=max(1, args.prior_layers),
        cross_attention_layers=max(1, args.cross_attention_layers),
        attention_heads=max(1, args.attention_heads),
        feedforward_dim=max(args.d_model, args.feedforward_dim),
        expert_count=max(1, args.expert_count),
        expert_top_k=max(1, args.expert_top_k),
        expert_hidden_dim=max(args.d_model, args.expert_hidden_dim),
    )
    train_config = TrainConfig(
        output_root=Path(args.output_root),
        epochs=max(1, args.epochs),
        learning_rate=args.learning_rate,
        early_stopping_patience=max(1, args.early_stopping_patience),
        early_stopping_min_epochs=max(1, args.early_stopping_min_epochs),
        early_stopping_min_delta=max(0.0, args.early_stopping_min_delta),
        run_name=args.run_name,
        wandb_mode=args.wandb_mode,
        evaluate_at_end=not args.no_evaluate,
    )
    config = ExperimentConfig(loader, model_config, train_config)
    set_seed(config.train.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    run_name = default_run_name(config)
    run_dir = config.train.output_root / run_name
    checkpoints = run_dir / "checkpoints"
    artifacts = run_dir / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    statistics = fit_statistics(config)
    (artifacts / "training_statistics.json").write_text(
        json.dumps(statistics.as_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (artifacts / "model.mmd").write_text(build_model_mermaid(), encoding="utf-8")
    manifest = {
        "model_family": MODEL_FAMILY,
        "model_version": MODEL_VERSION,
        "job": "read_only_v18_hierarchical_return_distribution_training",
        "run_name": run_name,
        "config": json_safe(config),
        "training_statistics": statistics.as_dict(),
        "device": str(device),
        "source_dataset_write_authority": False,
        "output_contract": {
            "factorization": "P(direction) * P(magnitude_bucket | direction)",
            "direction_probabilities": ["neutral", "upside", "downside"],
            "return_percentages": [
                "unconditional_expected",
                "conditional_upside",
                "conditional_downside",
            ],
        },
    }
    (run_dir / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False),
        encoding="utf-8",
    )
    model = NewsReactionModelV21(config.model, statistics).to(device)
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=config.train.learning_rate,
        weight_decay=config.train.weight_decay,
    )
    start_epoch = 0
    best_score = -math.inf
    epochs_without_improvement = 0
    if args.resume:
        checkpoint = torch.load(args.resume, map_location="cpu", weights_only=False)
        if checkpoint.get("model_version") != MODEL_VERSION:
            raise RuntimeError("Cannot resume a non-V21 checkpoint.")
        if TrainingStatistics.from_dict(checkpoint["training_statistics"]) != statistics:
            raise RuntimeError("V21 resume training-statistics drift.")
        if ModelConfig(**checkpoint["model_config"]) != config.model:
            raise RuntimeError("V21 resume model-config drift.")
        model.load_state_dict(checkpoint["model_state"])
        optimizer.load_state_dict(checkpoint["optimizer_state"])
        start_epoch = int(checkpoint["epoch"])
        best_score = float(checkpoint["best_score"])
        epochs_without_improvement = int(
            checkpoint.get("epochs_without_improvement", 0)
        )
        print(
            f"RESUME | epoch={start_epoch} best_score={best_score:.6f} "
            f"stale={epochs_without_improvement}",
            flush=True,
        )

    wandb_run = None
    if config.train.wandb_mode != "disabled":
        try:
            import wandb

            wandb_run = wandb.init(
                project=config.train.wandb_project,
                entity=config.train.wandb_entity,
                name=run_name,
                config=manifest,
                mode=None
                if config.train.wandb_mode == "auto"
                else config.train.wandb_mode,
                tags=[
                    "v21",
                    "v18-data",
                    "hierarchical-return",
                    "mixture-of-experts",
                    "early-stopping",
                ],
                resume="allow",
            )
        except Exception as exc:
            if config.train.wandb_mode not in {"auto", "offline"}:
                raise
            print(f"WANDB unavailable; retaining local metrics: {exc}", flush=True)

    amp_enabled = bool(config.train.amp and device.type == "cuda")
    amp_dtype = torch.bfloat16 if config.train.amp_dtype == "bf16" else torch.float16
    metrics_path = run_dir / "metrics.jsonl"
    stopped_early = False
    completed_epoch = start_epoch
    for epoch in range(start_epoch, config.train.epochs):
        dataset = PreparedEpisodeDataset(
            config.loader,
            start=config.loader.train_start,
            end_exclusive=config.loader.train_end_exclusive,
            shuffle=True,
            seed=config.train.seed,
        )
        model.train()
        batches = max(1, math.ceil(dataset.indices.size / config.loader.batch_size))
        sums: dict[str, float] = {}
        labels = 0.0
        try:
            for batch_index, batch in enumerate(dataset.iter_batches(epoch=epoch)):
                lr = learning_rate_for_progress(
                    config.train,
                    epoch=epoch,
                    batch_index=batch_index,
                    batches=batches,
                )
                for group in optimizer.param_groups:
                    group["lr"] = lr
                moved = batch.to(device)
                optimizer.zero_grad(set_to_none=True)
                with torch.autocast(
                    device_type=device.type,
                    dtype=amp_dtype,
                    enabled=amp_enabled,
                ):
                    result: LossResult = compute_loss(
                        model(moved.x),
                        moved,
                        statistics,
                        direction_weight=config.train.direction_weight,
                        magnitude_cross_entropy_weight=(
                            config.train.magnitude_cross_entropy_weight
                        ),
                        magnitude_ordinal_weight=(
                            config.train.magnitude_ordinal_weight
                        ),
                        expected_magnitude_weight=(
                            config.train.expected_magnitude_weight
                        ),
                        router_balance_weight=config.train.router_balance_weight,
                    )
                result.loss.backward()
                torch.nn.utils.clip_grad_norm_(
                    model.parameters(), config.train.grad_clip_norm
                )
                optimizer.step()
                batch_labels = result.metrics["train/labels"]
                labels += batch_labels
                for name, value in result.metrics.items():
                    if name == "train/labels":
                        continue
                    sums[name] = sums.get(name, 0.0) + value * batch_labels
                if (batch_index + 1) % 25 == 0:
                    print(
                        f"EPOCH {epoch + 1}/{config.train.epochs} "
                        f"batch={batch_index + 1}/{batches} "
                        f"loss={sums.get('train/loss', 0.0) / max(labels, 1):.5f} "
                        f"lr={lr:.3g}",
                        flush=True,
                    )
        finally:
            dataset.stop()
        val = validate(model, config.loader, statistics, device)
        epoch_metrics: dict[str, Any] = {
            "epoch": float(epoch + 1),
            **{
                f"{name}_epoch": value / max(labels, 1)
                for name, value in sums.items()
            },
            **val,
        }
        score = selection_score(val)
        improved = score > best_score + config.train.early_stopping_min_delta
        if improved:
            best_score = score
            epochs_without_improvement = 0
        else:
            epochs_without_improvement += 1
        epoch_metrics.update(
            {
                "val/selection_score": score,
                "val/checkpoint_improved": float(improved),
                "train/epochs_without_improvement": float(
                    epochs_without_improvement
                ),
            }
        )
        with metrics_path.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(epoch_metrics, sort_keys=True, allow_nan=False) + "\n"
            )
        if wandb_run is not None:
            wandb_run.log(epoch_metrics, step=epoch + 1)
        if improved:
            save_checkpoint(
                checkpoints / "best_val.pt",
                model=model,
                optimizer=optimizer,
                config=config,
                statistics=statistics,
                epoch=epoch + 1,
                metrics=epoch_metrics,
                best_score=best_score,
                epochs_without_improvement=epochs_without_improvement,
            )
        save_checkpoint(
            checkpoints / "latest.pt",
            model=model,
            optimizer=optimizer,
            config=config,
            statistics=statistics,
            epoch=epoch + 1,
            metrics=epoch_metrics,
            best_score=best_score,
            epochs_without_improvement=epochs_without_improvement,
        )
        completed_epoch = epoch + 1
        print(
            f"EPOCH {epoch + 1} COMPLETE | selection={score:.4f} "
            f"direction={val['val/direction/macro_f1']:.4f} "
            f"joint_log_skill={val['val/joint_distribution/log_loss_skill']:.4f} "
            f"stale={epochs_without_improvement}/"
            f"{config.train.early_stopping_patience}",
            flush=True,
        )
        if should_early_stop(
            config.train,
            completed_epochs=epoch + 1,
            epochs_without_improvement=epochs_without_improvement,
        ):
            stopped_early = True
            print(
                f"EARLY STOP | epoch={epoch + 1} best_score={best_score:.6f}",
                flush=True,
            )
            break

    best_checkpoint = checkpoints / "best_val.pt"
    selected = torch.load(best_checkpoint, map_location="cpu", weights_only=False)
    model.load_state_dict(selected["model_state"])
    final_metrics = validate(model, config.loader, statistics, device)
    if wandb_run is not None:
        wandb_run.log(
            {
                "phase": "selected",
                "selected_checkpoint_epoch": float(selected["epoch"]),
                "completed_epoch": float(completed_epoch),
                "stopped_early": float(stopped_early),
                **final_metrics,
            },
            step=completed_epoch + 1,
        )
        wandb_run.summary["selected_checkpoint"] = "best_val.pt"
        wandb_run.summary["selected_checkpoint_epoch"] = int(selected["epoch"])
        wandb_run.summary["completed_epoch"] = completed_epoch
        wandb_run.summary["stopped_early"] = stopped_early
    if config.train.evaluate_at_end:
        evaluate_checkpoint(
            best_checkpoint,
            loader_config=config.loader,
            start=config.loader.validation_start,
            end_exclusive=config.loader.validation_end_exclusive,
            device=device,
        )
    if wandb_run is not None:
        wandb_run.finish()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
