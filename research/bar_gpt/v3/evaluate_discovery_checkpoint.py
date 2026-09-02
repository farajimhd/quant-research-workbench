from __future__ import annotations

import argparse
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Iterable

import torch

from research.bar_gpt.v3 import LEARNING_CONTRACT, assert_checkpoint_version
from research.bar_gpt.v3.config import BarGPTConfig, DataConfig, ExperimentConfig, TrainConfig, to_dict
from research.bar_gpt.v3.full_chunk_training import (
    load_full_chunk_manifest,
    load_full_held_out_refs,
)
from research.bar_gpt.v3.model import BarGPTV3
from research.bar_gpt.v3.model_discovery import (
    DISCOVERY_WANDB_PROJECT,
    discovery_storage_config,
    load_discovery_manifest,
    panel_refs,
)
from research.bar_gpt.v3.offline_shards import (
    OfflineShardDataset,
    make_offline_dataloader,
    hydrate_offline_runtime_config,
    resolve_offline_units_for_refs,
    verify_shard_catalog_lock,
)
from research.bar_gpt.v3.train import DISCOVERY_VALIDATION_WORKERS, _wandb_metric_key, validate
from research.mlops.clickhouse import discover_clickhouse_env_files
from research.mlops.env import load_env_files
from research.mlops.metrics import AsyncJsonlMetricLogger
from research.mlops.wandb_utils import init_wandb


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate a BarGPT discovery checkpoint on one certified panel.")
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--experiment-manifest", required=True)
    parser.add_argument("--offline-shard-root", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--panel", choices=("validation", "locked_test"), default="locked_test")
    parser.add_argument(
        "--entire-held-out-population",
        action="store_true",
        help="evaluate every certified block in the full-training 2026 held-out index",
    )
    parser.add_argument("--namespace", default="", help="metric namespace; defaults to the panel name")
    parser.add_argument("--architecture", default="")
    parser.add_argument("--target-training-origins", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--loader-workers", type=int, default=16)
    parser.add_argument("--wandb-project", default=DISCOVERY_WANDB_PROJECT)
    parser.add_argument("--wandb-entity", default="mehdifaraji")
    parser.add_argument("--wandb-mode", choices=("auto", "online", "offline", "disabled"), default="online")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    if args.batch_size <= 0 or args.loader_workers < 0:
        raise ValueError("batch size must be positive and loader workers cannot be negative")
    if args.target_training_origins < 0:
        raise ValueError("target training origins cannot be negative")
    load_env_files(discover_clickhouse_env_files(), verbose=True)
    checkpoint_path = Path(args.checkpoint)
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    assert_checkpoint_version(checkpoint)
    raw_config = checkpoint["config"]
    model_config = BarGPTConfig(**raw_config["model"])
    data_values = dict(raw_config["data"])
    data_values.update(
        batch_size=int(args.batch_size),
        loader_workers=min(DISCOVERY_VALIDATION_WORKERS, int(args.loader_workers)),
        worker_prefetch_batches=1,
        persistent_workers=False,
    )
    shard_root = Path(args.offline_shard_root)
    verify_shard_catalog_lock(shard_root)
    data_config = hydrate_offline_runtime_config(shard_root, DataConfig(**data_values))
    train_values = dict(raw_config["train"])
    train_values["output_root"] = Path(train_values["output_root"])
    train_values.update(wandb_project=str(args.wandb_project), wandb_mode=str(args.wandb_mode))
    train_config = TrainConfig(**train_values)
    config = ExperimentConfig(model=model_config, data=data_config, train=train_config)
    storage_data_config = discovery_storage_config(data_config)
    manifest_path = Path(args.experiment_manifest)
    manifest = (
        load_full_chunk_manifest(
            manifest_path,
            shard_root=shard_root,
            config=data_config,
        )
        if args.entire_held_out_population
        else load_discovery_manifest(
            manifest_path,
            shard_root=shard_root,
            config=data_config,
        )
    )
    if args.entire_held_out_population and str(args.panel) != "validation":
        raise ValueError("the entire held-out population is exposed only as validation")
    if args.entire_held_out_population:
        selected_refs, population_hash = load_full_held_out_refs(
            manifest_path=manifest_path,
            manifest=manifest,
            ticker_order=data_config.tickers,
        )
        evaluation_population = "entire_held_out_index"
    else:
        selected_refs = panel_refs(manifest, str(args.panel))
        population_hash = manifest["manifest_hash"]
        evaluation_population = f"manifest_panel/{args.panel}"
    units = resolve_offline_units_for_refs(
        shard_root,
        storage_data_config,
        selected_refs,
    )
    evaluation_data = replace(
        data_config,
        balance_activity_regimes=False,
    )
    dataset = OfflineShardDataset(
        units,
        seed=train_config.seed,
        shuffle_units=False,
        block_refs=selected_refs,
    )
    loader = make_offline_dataloader(dataset, evaluation_data, drop_last=False)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = BarGPTV3(model_config).to(device)
    model.load_state_dict(checkpoint["model"], strict=True)
    source_samples = int(checkpoint.get("samples_seen", 0))
    target_samples = int(args.target_training_origins)
    source_complete = target_samples == 0 or source_samples >= target_samples
    parameter_count = sum(parameter.numel() for parameter in model.parameters())
    namespace = str(args.namespace).strip() or str(args.panel)
    run_root = Path(args.output_root) / args.run_name
    run_root.mkdir(parents=True, exist_ok=True)
    wandb_run = init_wandb(
        entity=str(args.wandb_entity),
        project=str(args.wandb_project),
        run_name=str(args.run_name),
        config={
            **to_dict(config),
            "evaluation_panel": str(args.panel),
            "evaluation_population": evaluation_population,
            "evaluation_population_hash": population_hash,
            "evaluation_blocks": len(selected_refs),
            "evaluation_origins": sum(int(ref.origins) for ref in selected_refs),
            "metric_namespace": namespace,
            "manifest_hash": manifest["manifest_hash"],
            "learning_contract": LEARNING_CONTRACT,
            "source_architecture": str(args.architecture),
            "source_checkpoint": str(checkpoint_path),
            "source_training_origins": source_samples,
            "target_training_origins": target_samples,
            "source_training_complete": source_complete,
            "model_parameters": parameter_count,
        },
        run_dir=run_root / "wandb",
        mode=str(args.wandb_mode),
        timeout_seconds=train_config.wandb_init_timeout,
    )
    logger = AsyncJsonlMetricLogger(run_root / "metrics.jsonl", wandb_run, wandb_key_mapper=_wandb_metric_key)
    try:
        metrics = validate(model, loader, config, device, namespace=namespace, max_batches=None)
        metrics.update({
            f"{namespace}_meta/training_origins": float(source_samples),
            f"{namespace}_meta/training_complete": float(source_complete),
            f"{namespace}_meta/model_parameters": float(parameter_count),
        })
        logger.log(metrics, source_samples)
        summary = {
            "architecture": str(args.architecture),
            "checkpoint": str(checkpoint_path),
            "checkpoint_size": checkpoint_path.stat().st_size,
            "checkpoint_mtime_ns": checkpoint_path.stat().st_mtime_ns,
            "step": source_samples,
            "target_training_origins": target_samples,
            "training_complete": source_complete,
            "model_parameters": parameter_count,
            "panel": str(args.panel),
            "evaluation_population": evaluation_population,
            "evaluation_population_hash": population_hash,
            "evaluation_blocks": len(selected_refs),
            "evaluation_origins": sum(int(ref.origins) for ref in selected_refs),
            "namespace": namespace,
            "manifest_hash": manifest["manifest_hash"],
            "learning_contract": LEARNING_CONTRACT,
            **metrics,
        }
        summary_path = run_root / "summary.json"
        temporary = summary_path.with_suffix(summary_path.suffix + f".tmp.{os.getpid()}")
        temporary.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        os.replace(temporary, summary_path)
    finally:
        logger.close(timeout=300)
        if wandb_run is not None:
            wandb_run.finish()
    print(f"{args.panel} evaluation complete: {run_root}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
