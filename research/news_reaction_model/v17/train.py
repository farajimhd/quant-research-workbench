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
from research.news_reaction_model.v17 import MODEL_FAMILY, MODEL_VERSION
from research.news_reaction_model.v17.config import (
    ExperimentConfig,
    LoaderConfig,
    ModelConfig,
    TrainConfig,
)
from research.news_reaction_model.v17.data import PreparedNewsResponseDataset
from research.news_reaction_model.v17.evaluate import evaluate_checkpoint
from research.news_reaction_model.v17.losses import compute_loss
from research.news_reaction_model.v17.metrics import ResponseAccumulator
from research.news_reaction_model.v17.model import NewsResponseModelV17, build_model_mermaid


REPO_ROOT = Path(__file__).resolve().parents[3]


def _json_safe(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return _json_safe(dataclasses.asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    return value


def _set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


@torch.no_grad()
def validate(
    model: NewsResponseModelV17,
    config: LoaderConfig,
    device: torch.device,
) -> dict[str, float]:
    dataset = PreparedNewsResponseDataset(
        config,
        start=config.validation_start,
        end_exclusive=config.validation_end_exclusive,
    )
    accumulator = ResponseAccumulator()
    model.eval()
    try:
        for batch in dataset.iter_batches():
            batch = batch.to(device)
            accumulator.add(model(batch.x), batch)
    finally:
        dataset.stop()
    model.train()
    return accumulator.compute("val")


def _save_checkpoint(
    path: Path,
    *,
    model: NewsResponseModelV17,
    optimizer: torch.optim.Optimizer,
    config: ExperimentConfig,
    epoch: int,
    metrics: dict[str, float],
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
            "loader_config": _json_safe(config.loader),
            "model_config": _json_safe(config.model),
            "train_config": _json_safe(config.train),
            "metrics": metrics,
        },
        temporary,
    )
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    defaults = ExperimentConfig()
    parser = argparse.ArgumentParser(description="Train V17 response archetypes over V16 inputs.")
    parser.add_argument("--prepared-root", default=str(defaults.loader.prepared_dataset_root))
    parser.add_argument("--target-root", default=str(defaults.loader.target_root))
    parser.add_argument("--output-root", default=str(defaults.train.output_root))
    parser.add_argument("--epochs", type=int, default=defaults.train.epochs)
    parser.add_argument("--batch-size", type=int, default=defaults.loader.batch_size)
    parser.add_argument("--learning-rate", type=float, default=defaults.train.learning_rate)
    parser.add_argument("--d-model", type=int, default=defaults.model.d_model)
    parser.add_argument("--layers", type=int, default=defaults.model.layers)
    parser.add_argument("--no-amp", action="store_true")
    parser.add_argument("--wandb-mode", default=defaults.train.wandb_mode)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    config = ExperimentConfig(
        loader=LoaderConfig(
            prepared_dataset_root=Path(args.prepared_root),
            target_root=Path(args.target_root),
            batch_size=args.batch_size,
        ),
        model=ModelConfig(d_model=args.d_model, hidden_dim=args.d_model, layers=args.layers),
        train=TrainConfig(
            output_root=Path(args.output_root),
            epochs=args.epochs,
            learning_rate=args.learning_rate,
            amp=not args.no_amp,
            wandb_mode=args.wandb_mode,
        ),
    )
    _set_seed(config.train.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    run_name = (
        f"news-v17-response-v16input-d{config.model.d_model}"
        f"-l{config.model.layers}-b{config.loader.batch_size}"
    )
    run_dir = config.train.output_root / run_name
    checkpoints = run_dir / "checkpoints"
    artifacts = run_dir / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "model.mmd").write_text(build_model_mermaid(), encoding="utf-8")
    manifest = {
        "model_family": MODEL_FAMILY,
        "model_version": MODEL_VERSION,
        "job": "response_archetype_training",
        "run_name": run_name,
        "config": _json_safe(config),
        "device": str(device),
    }
    (run_dir / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False),
        encoding="utf-8",
    )
    wandb_run = None
    if config.train.wandb_mode != "disabled":
        try:
            import wandb

            wandb_run = wandb.init(
                project=config.train.wandb_project,
                entity=config.train.wandb_entity,
                name=run_name,
                config=_json_safe(config),
                mode=None if config.train.wandb_mode == "auto" else config.train.wandb_mode,
                tags=["v17", "v16-inputs", "response-archetypes"],
            )
        except Exception as exc:
            if config.train.wandb_mode not in {"auto", "offline"}:
                raise
            print(f"WANDB unavailable; continuing with durable local metrics: {exc}", flush=True)
    model = NewsResponseModelV17(config.model).to(device)
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=config.train.learning_rate,
        weight_decay=config.train.weight_decay,
    )
    amp_enabled = bool(config.train.amp and device.type == "cuda")
    amp_dtype = torch.bfloat16 if config.train.amp_dtype == "bf16" else torch.float16
    metrics_path = run_dir / "metrics.jsonl"
    best = -math.inf
    for epoch in range(config.train.epochs):
        dataset = PreparedNewsResponseDataset(
            config.loader,
            start=config.loader.train_start,
            end_exclusive=config.loader.train_end_exclusive,
            shuffle=True,
            seed=config.train.seed,
        )
        model.train()
        batches = max(1, math.ceil((dataset.upper - dataset.lower) / config.loader.batch_size))
        running_loss = 0.0
        try:
            for batch_index, batch in enumerate(dataset.iter_batches(epoch=epoch)):
                progress = batch_index / batches
                peak_lr = config.train.learning_rate * (
                    config.train.scheduler_cycle_decay ** epoch
                )
                lr = config.train.scheduler_eta_min + 0.5 * (
                    peak_lr - config.train.scheduler_eta_min
                ) * (1.0 + math.cos(math.pi * progress))
                for group in optimizer.param_groups:
                    group["lr"] = lr
                batch = batch.to(device)
                optimizer.zero_grad(set_to_none=True)
                with torch.autocast(
                    device_type=device.type,
                    dtype=amp_dtype,
                    enabled=amp_enabled,
                ):
                    result = compute_loss(model(batch.x), batch)
                result.loss.backward()
                torch.nn.utils.clip_grad_norm_(
                    model.parameters(), config.train.grad_clip_norm
                )
                optimizer.step()
                running_loss += float(result.loss.detach().cpu())
                if (batch_index + 1) % 25 == 0:
                    print(
                        f"EPOCH {epoch + 1}/{config.train.epochs} "
                        f"batch={batch_index + 1}/{batches} "
                        f"loss={running_loss / (batch_index + 1):.5f} lr={lr:.3g}",
                        flush=True,
                    )
        finally:
            dataset.stop()
        val = validate(model, config.loader, device)
        epoch_metrics = {
            "epoch": float(epoch + 1),
            "train/loss": running_loss / batches,
            **val,
        }
        with metrics_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(epoch_metrics, sort_keys=True, allow_nan=False) + "\n")
        if wandb_run is not None:
            wandb_run.log(epoch_metrics, step=epoch + 1)
        _save_checkpoint(
            checkpoints / "latest.pt",
            model=model,
            optimizer=optimizer,
            config=config,
            epoch=epoch + 1,
            metrics=epoch_metrics,
        )
        score = val.get("val/macro_head_f1", 0.0)
        if score > best:
            best = score
            _save_checkpoint(
                checkpoints / "best_val.pt",
                model=model,
                optimizer=optimizer,
                config=config,
                epoch=epoch + 1,
                metrics=epoch_metrics,
            )
        print(
            f"EPOCH {epoch + 1} COMPLETE | val/macro_head_f1={score:.4f} best={best:.4f}",
            flush=True,
        )
    if wandb_run is not None:
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
