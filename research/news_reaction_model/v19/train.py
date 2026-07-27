from __future__ import annotations

import argparse
import dataclasses
import itertools
import json
import math
import random
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import torch

from research.mlops.env import discover_env_files, load_env_files
from research.news_reaction_model.v19 import MODEL_FAMILY, MODEL_VERSION
from research.news_reaction_model.v19.config import (
    ExperimentConfig,
    LoaderConfig,
    ModelConfig,
    TrainConfig,
    default_run_name,
)
from research.news_reaction_model.v19.data import PreparedEpisodeDataset
from research.news_reaction_model.v19.evaluate import evaluate_checkpoint
from research.news_reaction_model.v19.losses import LossResult, compute_loss
from research.news_reaction_model.v19.metrics import EpisodeAccumulator
from research.news_reaction_model.v19.model import NewsReactionModelV19, build_model_mermaid
from research.news_reaction_model.v19.targets import (
    TrainingStatistics,
    fit_training_statistics,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
PHASES = ("joint", "specialize", "path")


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
            scale_quantile=config.train.regression_scale_quantile,
            scale_floor_pct=config.train.regression_scale_floor_pct,
            minimum_scale_rows=config.train.regression_scale_minimum_rows,
        )
    finally:
        dataset.stop()


@torch.no_grad()
def validate(
    model: NewsReactionModelV19,
    config: LoaderConfig,
    statistics: TrainingStatistics,
    device: torch.device,
) -> dict[str, float]:
    dataset = PreparedEpisodeDataset(
        config,
        start=config.validation_start,
        end_exclusive=config.validation_end_exclusive,
    )
    accumulator = EpisodeAccumulator(statistics.regression_training_median)
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
    model: NewsReactionModelV19,
    optimizer: torch.optim.Optimizer,
    config: ExperimentConfig,
    statistics: TrainingStatistics,
    phase: str,
    phase_epoch: int,
    global_epoch: int,
    metrics: dict[str, Any],
    best_scores: dict[str, float],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    torch.save(
        {
            "model_family": MODEL_FAMILY,
            "model_version": MODEL_VERSION,
            "phase": phase,
            "phase_epoch": phase_epoch,
            "global_epoch": global_epoch,
            "model_state": model.state_dict(),
            "optimizer_state": optimizer.state_dict(),
            "loader_config": json_safe(config.loader),
            "model_config": json_safe(config.model),
            "train_config": json_safe(config.train),
            "training_statistics": statistics.as_dict(),
            "metrics": metrics,
            "best_scores": best_scores,
        },
        temporary,
    )
    temporary.replace(path)


def save_task(path: Path, model: NewsReactionModelV19, task: str) -> None:
    states = [module.state_dict() for module in model.modules_for_task(task)]
    temporary = path.with_suffix(path.suffix + ".tmp")
    torch.save({"task": task, "states": states}, temporary)
    temporary.replace(path)


def load_task(path: Path, model: NewsReactionModelV19, task: str) -> None:
    payload = torch.load(path, map_location="cpu", weights_only=False)
    if payload.get("task") != task:
        raise RuntimeError(f"Task checkpoint mismatch at {path}.")
    modules = model.modules_for_task(task)
    states = payload["states"]
    if len(states) != len(modules):
        raise RuntimeError(f"Task module-count mismatch at {path}.")
    for module, state in zip(modules, states, strict=True):
        module.load_state_dict(state)


def trainable_parameters(model: NewsReactionModelV19) -> list[torch.nn.Parameter]:
    return [parameter for parameter in model.parameters() if parameter.requires_grad]


def configure_phase(model: NewsReactionModelV19, phase: str) -> set[str]:
    if phase == "joint":
        tasks = {"direction", "path", "flow", "regression"}
        model.set_trainable_tasks(shared=True, tasks=tasks)
        return tasks
    if phase == "specialize":
        tasks = {"direction", "flow", "regression"}
        model.set_trainable_tasks(shared=False, tasks=tasks)
        return tasks
    if phase == "path":
        tasks = {"path"}
        model.set_trainable_tasks(shared=False, tasks=tasks)
        return tasks
    raise KeyError(phase)


def set_phase_training_mode(model: NewsReactionModelV19, phase: str) -> None:
    if phase == "joint":
        model.train()
        return
    model.eval()
    tasks = (
        ("direction", "flow", "regression")
        if phase == "specialize"
        else ("path",)
    )
    for task in tasks:
        for module in model.modules_for_task(task):
            module.train()


def task_score(metrics: dict[str, float], task: str, recall_gate: float) -> float:
    if task == "regression":
        return metrics["val/regression_mean_mae_skill"]
    score = metrics[f"val/{task}/macro_f1"]
    guarded = {
        "path": ("spike_fade", "flush_recovery"),
        "flow": ("supply_dominant",),
    }.get(task, ())
    for class_name in guarded:
        recall = metrics[f"val/{task}/class/{class_name}/recall"]
        if recall < recall_gate:
            score -= recall_gate - recall
    return score


def joint_score(metrics: dict[str, float], recall_gate: float) -> float:
    score = metrics["val/joint_score"]
    for task, class_name in (
        ("path", "spike_fade"),
        ("path", "flush_recovery"),
        ("flow", "supply_dominant"),
    ):
        recall = metrics[f"val/{task}/class/{class_name}/recall"]
        if recall < recall_gate:
            score -= (recall_gate - recall) / 3.0
    return score


def initialize_phase_baselines(
    *,
    model: NewsReactionModelV19,
    phase: str,
    metrics: dict[str, float],
    best_scores: dict[str, float],
    checkpoints: Path,
    recall_gate: float,
) -> None:
    if phase == "specialize":
        tasks = ("direction", "flow", "regression")
    elif phase == "path":
        tasks = ("path",)
    else:
        return
    for task in tasks:
        score = task_score(metrics, task, recall_gate)
        best_scores[task] = score
        save_task(checkpoints / f"best_{task}.pt", model, task)
        print(
            f"{phase.upper()} BASELINE | task={task} score={score:.6f}",
            flush=True,
        )


def gradient_audit(
    components: dict[str, torch.Tensor],
    shared_parameters: Iterable[torch.nn.Parameter],
) -> dict[str, float]:
    parameters = [parameter for parameter in shared_parameters if parameter.requires_grad]
    gradients: dict[str, tuple[torch.Tensor | None, ...]] = {}
    for name in sorted(components):
        gradients[name] = torch.autograd.grad(
            components[name],
            parameters,
            retain_graph=True,
            allow_unused=True,
        )
    result: dict[str, float] = {}
    for left, right in itertools.combinations(sorted(gradients), 2):
        dot = torch.zeros((), device=components[left].device, dtype=torch.float32)
        left_norm = torch.zeros_like(dot)
        right_norm = torch.zeros_like(dot)
        for left_grad, right_grad in zip(
            gradients[left],
            gradients[right],
            strict=True,
        ):
            if left_grad is None or right_grad is None:
                continue
            left_float = left_grad.detach().float()
            right_float = right_grad.detach().float()
            dot += torch.sum(left_float * right_float)
            left_norm += torch.sum(left_float.square())
            right_norm += torch.sum(right_float.square())
        cosine = dot / torch.sqrt(torch.clamp(left_norm * right_norm, min=1e-24))
        result[f"gradient_cosine/{left}_vs_{right}"] = float(cosine.cpu())
    return result


def build_parser() -> argparse.ArgumentParser:
    defaults = ExperimentConfig()
    parser = argparse.ArgumentParser(description="Train V19 over read-only V18 arrays.")
    parser.add_argument("--prepared-root", default=str(defaults.loader.prepared_dataset_root))
    parser.add_argument("--v15-root", default=str(defaults.loader.v15_prepared_root))
    parser.add_argument("--output-root", default=str(defaults.train.output_root))
    parser.add_argument("--joint-epochs", type=int, default=defaults.train.joint_epochs)
    parser.add_argument(
        "--specialization-epochs",
        type=int,
        default=defaults.train.specialization_epochs,
    )
    parser.add_argument("--path-epochs", type=int, default=defaults.train.path_epochs)
    parser.add_argument("--batch-size", type=int, default=defaults.loader.batch_size)
    parser.add_argument("--learning-rate", type=float, default=defaults.train.learning_rate)
    parser.add_argument(
        "--specialization-learning-rate",
        type=float,
        default=defaults.train.specialization_learning_rate,
    )
    parser.add_argument("--d-model", type=int, default=defaults.model.d_model)
    parser.add_argument(
        "--transformer-layers",
        type=int,
        default=defaults.model.transformer_layers,
    )
    parser.add_argument(
        "--attention-heads",
        type=int,
        default=defaults.model.attention_heads,
    )
    parser.add_argument(
        "--feedforward-dim",
        type=int,
        default=defaults.model.feedforward_dim,
    )
    parser.add_argument("--regression-weight", type=float, default=defaults.train.regression_weight)
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
    model_config = ModelConfig(
        d_model=args.d_model,
        transformer_layers=args.transformer_layers,
        attention_heads=args.attention_heads,
        feedforward_dim=args.feedforward_dim,
        tower_hidden_dim=args.d_model,
    )
    train_config = TrainConfig(
        output_root=Path(args.output_root),
        joint_epochs=max(1, args.joint_epochs),
        specialization_epochs=max(1, args.specialization_epochs),
        path_epochs=max(1, args.path_epochs),
        learning_rate=args.learning_rate,
        specialization_learning_rate=args.specialization_learning_rate,
        regression_weight=args.regression_weight,
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
        "job": "read_only_v18_token_multitask_training",
        "run_name": run_name,
        "config": json_safe(config),
        "training_statistics": statistics.as_dict(),
        "device": str(device),
        "source_dataset_write_authority": False,
    }
    (run_dir / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False),
        encoding="utf-8",
    )
    model = NewsReactionModelV19(config.model, statistics).to(device)

    start_phase_index = 0
    start_phase_epoch = 0
    global_epoch = 0
    resume_optimizer_state: dict[str, Any] | None = None
    best_scores = {
        "joint": -math.inf,
        "direction": -math.inf,
        "flow": -math.inf,
        "regression": -math.inf,
        "path": -math.inf,
    }
    if args.resume:
        checkpoint = torch.load(args.resume, map_location="cpu", weights_only=False)
        if checkpoint.get("model_version") != MODEL_VERSION:
            raise RuntimeError("Cannot resume a non-V19 checkpoint.")
        if TrainingStatistics.from_dict(checkpoint["training_statistics"]) != statistics:
            raise RuntimeError("V19 resume training-statistics drift.")
        model.load_state_dict(checkpoint["model_state"])
        start_phase_index = PHASES.index(str(checkpoint["phase"]))
        start_phase_epoch = int(checkpoint["phase_epoch"])
        global_epoch = int(checkpoint["global_epoch"])
        best_scores.update(
            {key: float(value) for key, value in checkpoint["best_scores"].items()}
        )
        resume_optimizer_state = checkpoint["optimizer_state"]
        print(
            f"RESUME | phase={PHASES[start_phase_index]} "
            f"phase_epoch={start_phase_epoch} global_epoch={global_epoch}",
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
                mode=None if config.train.wandb_mode == "auto" else config.train.wandb_mode,
                tags=["v19", "v18-data", "episode-transformer", "multitask"],
                resume="allow",
            )
        except Exception as exc:
            if config.train.wandb_mode not in {"auto", "offline"}:
                raise
            print(f"WANDB unavailable; retaining local metrics: {exc}", flush=True)

    amp_enabled = bool(config.train.amp and device.type == "cuda")
    amp_dtype = torch.bfloat16 if config.train.amp_dtype == "bf16" else torch.float16
    metrics_path = run_dir / "metrics.jsonl"
    phase_epochs = {
        "joint": config.train.joint_epochs,
        "specialize": config.train.specialization_epochs,
        "path": config.train.path_epochs,
    }
    final_metrics: dict[str, Any] = {}
    final_optimizer: torch.optim.Optimizer | None = None
    for phase_index in range(start_phase_index, len(PHASES)):
        phase = PHASES[phase_index]
        phase_start = start_phase_epoch if phase_index == start_phase_index else 0
        if phase_index > start_phase_index or (
            phase_index == start_phase_index and phase_start == 0
        ):
            if phase == "specialize":
                best_joint = checkpoints / "best_joint.pt"
                if not best_joint.exists():
                    raise RuntimeError("V19 specialization requires best_joint.pt.")
                model.load_state_dict(
                    torch.load(best_joint, map_location="cpu", weights_only=False)[
                        "model_state"
                    ]
                )
            elif phase == "path":
                for task in ("direction", "flow", "regression"):
                    load_task(checkpoints / f"best_{task}.pt", model, task)
            if phase in {"specialize", "path"}:
                baseline_metrics = validate(model, config.loader, statistics, device)
                initialize_phase_baselines(
                    model=model,
                    phase=phase,
                    metrics=baseline_metrics,
                    best_scores=best_scores,
                    checkpoints=checkpoints,
                    recall_gate=config.train.class_recall_gate,
                )
        tasks = configure_phase(model, phase)
        learning_rate = (
            config.train.learning_rate
            if phase == "joint"
            else config.train.specialization_learning_rate
        )
        optimizer = torch.optim.AdamW(
            trainable_parameters(model),
            lr=learning_rate,
            weight_decay=config.train.weight_decay,
        )
        final_optimizer = optimizer
        if (
            phase_index == start_phase_index
            and phase_start > 0
            and resume_optimizer_state is not None
        ):
            optimizer.load_state_dict(resume_optimizer_state)
        for phase_epoch in range(phase_start, phase_epochs[phase]):
            dataset = PreparedEpisodeDataset(
                config.loader,
                start=config.loader.train_start,
                end_exclusive=config.loader.train_end_exclusive,
                shuffle=True,
                seed=config.train.seed,
            )
            set_phase_training_mode(model, phase)
            batches = max(1, math.ceil(dataset.indices.size / config.loader.batch_size))
            running = {
                "loss": 0.0,
                "direction": 0.0,
                "path": 0.0,
                "flow": 0.0,
                "regression": 0.0,
            }
            audit: dict[str, float] = {}
            try:
                for batch_index, batch in enumerate(dataset.iter_batches(epoch=global_epoch)):
                    progress = batch_index / batches
                    peak = learning_rate * (
                        config.train.scheduler_cycle_decay ** phase_epoch
                    )
                    lr = config.train.scheduler_eta_min + 0.5 * (
                        peak - config.train.scheduler_eta_min
                    ) * (1 + math.cos(math.pi * progress))
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
                            tasks=tasks,
                            regression_weight=config.train.regression_weight,
                        )
                    if (
                        phase == "joint"
                        and phase_epoch < config.train.gradient_audit_epochs
                        and batch_index == 0
                    ):
                        audit = gradient_audit(
                            result.components,
                            model.shared_parameters(),
                        )
                    result.loss.backward()
                    torch.nn.utils.clip_grad_norm_(
                        trainable_parameters(model),
                        config.train.grad_clip_norm,
                    )
                    optimizer.step()
                    running["loss"] += float(result.loss.detach().cpu())
                    for name, value in result.components.items():
                        running[name] += float(value.detach().cpu())
                    if (batch_index + 1) % 25 == 0:
                        print(
                            f"{phase.upper()} {phase_epoch + 1}/{phase_epochs[phase]} "
                            f"batch={batch_index + 1}/{batches} "
                            f"loss={running['loss'] / (batch_index + 1):.5f} "
                            f"lr={lr:.3g}",
                            flush=True,
                        )
            finally:
                dataset.stop()
            global_epoch += 1
            val = validate(model, config.loader, statistics, device)
            epoch_metrics: dict[str, Any] = {
                "global_epoch": float(global_epoch),
                "phase_epoch": float(phase_epoch + 1),
                "phase": phase,
                **{
                    f"train/{name}_epoch": value / batches
                    for name, value in running.items()
                },
                **audit,
                **val,
            }
            final_metrics = epoch_metrics
            with metrics_path.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(epoch_metrics, sort_keys=True, allow_nan=False) + "\n"
                )
            if wandb_run is not None:
                wandb_run.log(epoch_metrics, step=global_epoch)

            if phase == "joint":
                score = joint_score(val, config.train.class_recall_gate)
                if score > best_scores["joint"]:
                    best_scores["joint"] = score
                    save_checkpoint(
                        checkpoints / "best_joint.pt",
                        model=model,
                        optimizer=optimizer,
                        config=config,
                        statistics=statistics,
                        phase=phase,
                        phase_epoch=phase_epoch + 1,
                        global_epoch=global_epoch,
                        metrics=epoch_metrics,
                        best_scores=best_scores,
                    )
            elif phase == "specialize":
                for task in ("direction", "flow", "regression"):
                    score = task_score(val, task, config.train.class_recall_gate)
                    if score > best_scores[task]:
                        best_scores[task] = score
                        save_task(checkpoints / f"best_{task}.pt", model, task)
            else:
                score = task_score(val, "path", config.train.class_recall_gate)
                if score > best_scores["path"]:
                    best_scores["path"] = score
                    save_task(checkpoints / "best_path.pt", model, "path")

            save_checkpoint(
                checkpoints / "latest.pt",
                model=model,
                optimizer=optimizer,
                config=config,
                statistics=statistics,
                phase=phase,
                phase_epoch=phase_epoch + 1,
                global_epoch=global_epoch,
                metrics=epoch_metrics,
                best_scores=best_scores,
            )
            print(
                f"{phase.upper()} {phase_epoch + 1} COMPLETE | "
                f"joint={val['val/joint_score']:.4f} "
                f"direction={val['val/direction/macro_f1']:.4f} "
                f"path={val['val/path/macro_f1']:.4f} "
                f"flow={val['val/flow/macro_f1']:.4f} "
                f"reg_skill={val['val/regression_mean_mae_skill']:.4f}",
                flush=True,
            )
        if phase == "joint":
            model.load_state_dict(
                torch.load(
                    checkpoints / "best_joint.pt",
                    map_location="cpu",
                    weights_only=False,
                )["model_state"]
            )
        elif phase == "specialize":
            for task in ("direction", "flow", "regression"):
                load_task(checkpoints / f"best_{task}.pt", model, task)
            final_metrics = validate(model, config.loader, statistics, device)
            save_checkpoint(
                checkpoints / "specialized_base.pt",
                model=model,
                optimizer=optimizer,
                config=config,
                statistics=statistics,
                phase=phase,
                phase_epoch=phase_epochs[phase],
                global_epoch=global_epoch,
                metrics=final_metrics,
                best_scores=best_scores,
            )
        else:
            load_task(checkpoints / "best_path.pt", model, "path")
            final_metrics = validate(model, config.loader, statistics, device)
            save_checkpoint(
                checkpoints / "best_val.pt",
                model=model,
                optimizer=optimizer,
                config=config,
                statistics=statistics,
                phase=phase,
                phase_epoch=phase_epochs[phase],
                global_epoch=global_epoch,
                metrics=final_metrics,
                best_scores=best_scores,
            )
        start_phase_epoch = 0
        resume_optimizer_state = None

    if wandb_run is not None:
        wandb_run.log(
            {
                "phase": "assembled",
                "global_epoch": float(global_epoch),
                **final_metrics,
            },
            step=global_epoch + 1,
        )
        wandb_run.summary["selected_checkpoint"] = "best_val.pt"
        wandb_run.finish()
    if config.train.evaluate_at_end:
        evaluate_checkpoint(
            checkpoints / "best_val.pt",
            loader_config=config.loader,
            start=config.loader.validation_start,
            end_exclusive=config.loader.validation_end_exclusive,
            device=device,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
