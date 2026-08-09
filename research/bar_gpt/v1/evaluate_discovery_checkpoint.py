from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from typing import Iterable

import torch

from research.bar_gpt.v1.config import BarGPTConfig, DataConfig, ExperimentConfig, TrainConfig, to_dict
from research.bar_gpt.v1.model import BarGPTV1
from research.bar_gpt.v1.model_discovery import (
    DISCOVERY_WANDB_PROJECT,
    load_discovery_manifest,
    panel_refs,
)
from research.bar_gpt.v1.offline_shards import OfflineShardDataset, discover_offline_units, make_offline_dataloader
from research.bar_gpt.v1.train import PreparedValidationBatches, _wandb_metric_key, validate
from research.mlops.metrics import AsyncJsonlMetricLogger
from research.mlops.wandb_utils import init_wandb


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate a BarGPT discovery finalist on the locked test panel.")
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--experiment-manifest", required=True)
    parser.add_argument("--offline-shard-root", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--run-name", required=True)
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
    checkpoint = torch.load(args.checkpoint, map_location="cpu", weights_only=False)
    raw_config = checkpoint["config"]
    model_config = BarGPTConfig(**raw_config["model"])
    data_values = dict(raw_config["data"])
    data_values.update(batch_size=int(args.batch_size), loader_workers=int(args.loader_workers))
    data_config = DataConfig(**data_values)
    train_values = dict(raw_config["train"])
    train_values["output_root"] = Path(train_values["output_root"])
    train_values.update(wandb_project=str(args.wandb_project), wandb_mode=str(args.wandb_mode))
    train_config = TrainConfig(**train_values)
    config = ExperimentConfig(model=model_config, data=data_config, train=train_config)
    shard_root = Path(args.offline_shard_root)
    manifest = load_discovery_manifest(
        Path(args.experiment_manifest),
        shard_root=shard_root,
        config=data_config,
    )
    validation_tickers = tuple(sorted({ref.ticker for ref in panel_refs(manifest, "locked_test")}))
    units = discover_offline_units(
        shard_root,
        data_config,
        tickers=validation_tickers,
        start_date=manifest["ranges"]["held_out"][0],
        end_date=manifest["ranges"]["held_out"][1],
    )
    evaluation_data = replace(data_config, persistent_workers=False, balance_activity_regimes=False)
    dataset = OfflineShardDataset(
        units,
        seed=train_config.seed,
        shuffle_units=False,
        block_refs=panel_refs(manifest, "locked_test"),
    )
    cache = PreparedValidationBatches(make_offline_dataloader(dataset, evaluation_data, drop_last=False))
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = BarGPTV1(model_config).to(device)
    model.load_state_dict(checkpoint["model"], strict=True)
    run_root = Path(args.output_root) / args.run_name
    run_root.mkdir(parents=True, exist_ok=True)
    wandb_run = init_wandb(
        entity=str(args.wandb_entity),
        project=str(args.wandb_project),
        run_name=str(args.run_name),
        config={**to_dict(config), "evaluation_panel": "locked_test", "manifest_hash": manifest["manifest_hash"]},
        run_dir=run_root / "wandb",
        mode=str(args.wandb_mode),
        timeout_seconds=train_config.wandb_init_timeout,
    )
    logger = AsyncJsonlMetricLogger(run_root / "metrics.jsonl", wandb_run, wandb_key_mapper=_wandb_metric_key)
    try:
        metrics = validate(model, cache, config, device, namespace="locked_test", max_batches=None)
        step = int(checkpoint.get("samples_seen", 0))
        logger.log(metrics, step)
        (run_root / "summary.json").write_text(
            json.dumps({"checkpoint": str(args.checkpoint), "step": step, **metrics}, indent=2),
            encoding="utf-8",
        )
    finally:
        cache.close()
        logger.close(timeout=300)
        if wandb_run is not None:
            wandb_run.finish()
    print(f"Locked-test evaluation complete: {run_root}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
