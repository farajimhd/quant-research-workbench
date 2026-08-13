from __future__ import annotations

import argparse
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Iterable

import torch

from research.bar_gpt.v1.config import BarGPTConfig, DataConfig, ExperimentConfig, TrainConfig, to_dict
from research.bar_gpt.v1.model import BarGPTV1
from research.bar_gpt.v1.model_discovery import (
    DISCOVERY_WANDB_PROJECT,
    discovery_storage_config,
    load_discovery_manifest,
    panel_refs,
)
from research.bar_gpt.v1.offline_shards import (
    OfflineShardDataset,
    make_offline_dataloader,
    hydrate_offline_runtime_config,
    resolve_offline_units_for_refs,
    verify_shard_catalog_lock,
)
from research.bar_gpt.v1.train import DISCOVERY_VALIDATION_WORKERS, _wandb_metric_key, validate
from research.mlops.clickhouse import discover_clickhouse_env_files
from research.mlops.env import load_env_files
from research.mlops.metrics import AsyncJsonlMetricLogger
from research.mlops.wandb_utils import init_wandb


FINAL_VALIDATION_SUMMARY_KEYS = (
    "validation_loss/total",
    "validation_trade_summary/mae_macro",
    "validation_close_direction_summary/balanced_accuracy_macro",
    "validation_close_direction_summary/mcc_macro",
    "validation_ar_direction_balanced/balanced_accuracy_macro",
    "validation_ar_direction_mcc/mcc_macro",
    "validation_trade_summary/rank_macro",
    "validation_trade_summary/calibration_macro",
    "validation_availability/brier_macro",
)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate a BarGPT discovery checkpoint on one certified panel.")
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--experiment-manifest", required=True)
    parser.add_argument("--offline-shard-root", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--panel", choices=("validation", "locked_test"), default="locked_test")
    parser.add_argument("--namespace", default="", help="metric namespace; defaults to the panel name")
    parser.add_argument("--architecture", default="")
    parser.add_argument("--target-training-origins", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--loader-workers", type=int, default=16)
    parser.add_argument("--wandb-project", default=DISCOVERY_WANDB_PROJECT)
    parser.add_argument("--wandb-entity", default="mehdifaraji")
    parser.add_argument("--wandb-mode", choices=("auto", "online", "offline", "disabled"), default="online")
    parser.add_argument(
        "--wandb-run-id",
        default="",
        help="explicit existing W&B run ID to resume; empty creates an independent evaluation run",
    )
    parser.add_argument(
        "--wandb-run-name",
        default="",
        help="existing W&B run name when --wandb-run-id is supplied; defaults to --run-name",
    )
    parser.add_argument(
        "--wandb-log-step",
        type=int,
        default=0,
        help="history step for the evaluation record; 0 uses checkpoint training origins",
    )
    parser.add_argument(
        "--corrected-final-record",
        action="store_true",
        help="append corrected validation_* metrics to an explicitly resumed source-training run",
    )
    parser.add_argument(
        "--evaluation-contract",
        default="",
        help="durable caller contract recorded in local and W&B provenance",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    if args.batch_size <= 0 or args.loader_workers < 0:
        raise ValueError("batch size must be positive and loader workers cannot be negative")
    if args.target_training_origins < 0:
        raise ValueError("target training origins cannot be negative")
    if args.wandb_log_step < 0:
        raise ValueError("W&B log step cannot be negative")
    if args.corrected_final_record and not args.wandb_run_id:
        raise ValueError("a corrected final record requires --wandb-run-id")
    if args.corrected_final_record and str(args.namespace).strip() != "validation":
        raise ValueError("a corrected final record must use the validation metric namespace")
    load_env_files(discover_clickhouse_env_files(), verbose=True)
    checkpoint_path = Path(args.checkpoint)
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
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
    manifest = load_discovery_manifest(
        Path(args.experiment_manifest),
        shard_root=shard_root,
        config=data_config,
    )
    selected_refs = panel_refs(manifest, str(args.panel))
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
    model = BarGPTV1(model_config).to(device)
    model.load_state_dict(checkpoint["model"], strict=True)
    source_samples = int(checkpoint.get("samples_seen", 0))
    target_samples = int(args.target_training_origins)
    source_complete = target_samples == 0 or source_samples >= target_samples
    checkpoint_wandb_run_id = str(checkpoint.get("wandb_run_id") or "")
    if args.corrected_final_record and not source_complete:
        raise ValueError(
            "a corrected final record requires a checkpoint that completed the target training origins"
        )
    if args.corrected_final_record and checkpoint_wandb_run_id != str(args.wandb_run_id):
        raise ValueError(
            "explicit W&B run ID does not match the durable identity in the checkpoint: "
            f"expected {checkpoint_wandb_run_id!r}, received {args.wandb_run_id!r}"
        )
    wandb_log_step = int(args.wandb_log_step) or source_samples
    if args.corrected_final_record and wandb_log_step <= source_samples:
        raise ValueError(
            "a corrected final W&B record must use a step above checkpoint training origins"
        )
    parameter_count = sum(parameter.numel() for parameter in model.parameters())
    namespace = str(args.namespace).strip() or str(args.panel)
    run_root = Path(args.output_root) / args.run_name
    run_root.mkdir(parents=True, exist_ok=True)
    evaluation_provenance = {
        "evaluation_panel": str(args.panel),
        "metric_namespace": namespace,
        "manifest_hash": manifest["manifest_hash"],
        "source_architecture": str(args.architecture),
        "source_checkpoint": str(checkpoint_path),
        "source_checkpoint_wandb_run_id": checkpoint_wandb_run_id,
        "source_training_origins": source_samples,
        "target_training_origins": target_samples,
        "source_training_complete": source_complete,
        "model_parameters": parameter_count,
        "corrected_final_record": bool(args.corrected_final_record),
        "corrected_validation_log_step": wandb_log_step,
        "evaluation_contract": str(args.evaluation_contract),
    }
    wandb_run_name = str(args.wandb_run_name).strip() or str(args.run_name)
    wandb_run = init_wandb(
        entity=str(args.wandb_entity),
        project=str(args.wandb_project),
        run_name=wandb_run_name,
        # Resuming an existing training run with a reconstructed evaluation
        # config can conflict with its immutable training keys. Resume with an
        # empty init config, then append evaluation-only provenance explicitly.
        config={} if args.wandb_run_id else {**to_dict(config), **evaluation_provenance},
        run_dir=run_root / "wandb",
        mode=str(args.wandb_mode),
        timeout_seconds=train_config.wandb_init_timeout,
        run_id=str(args.wandb_run_id).strip() or None,
    )
    if wandb_run is not None and args.wandb_run_id:
        wandb_run.config.update(evaluation_provenance, allow_val_change=True)
    logger = AsyncJsonlMetricLogger(run_root / "metrics.jsonl", wandb_run, wandb_key_mapper=_wandb_metric_key)
    metrics: dict[str, float] = {}
    try:
        metrics = validate(model, loader, config, device, namespace=namespace, max_batches=None)
        metrics.update({
            f"{namespace}_meta/training_origins": float(source_samples),
            f"{namespace}_meta/training_complete": float(source_complete),
            f"{namespace}_meta/model_parameters": float(parameter_count),
            f"{namespace}_meta/corrected_final_record": float(bool(args.corrected_final_record)),
        })
        logger.log(metrics, wandb_log_step)
        summary = {
            "architecture": str(args.architecture),
            "checkpoint": str(checkpoint_path),
            "checkpoint_size": checkpoint_path.stat().st_size,
            "checkpoint_mtime_ns": checkpoint_path.stat().st_mtime_ns,
            "step": source_samples,
            "wandb_log_step": wandb_log_step,
            "wandb_project": str(args.wandb_project),
            "wandb_entity": str(args.wandb_entity),
            "wandb_run_id": str(args.wandb_run_id),
            "wandb_run_name": wandb_run_name,
            "corrected_final_record": bool(args.corrected_final_record),
            "evaluation_contract": str(args.evaluation_contract),
            "target_training_origins": target_samples,
            "training_complete": source_complete,
            "model_parameters": parameter_count,
            "panel": str(args.panel),
            "namespace": namespace,
            "manifest_hash": manifest["manifest_hash"],
            **metrics,
        }
        summary_path = run_root / "summary.json"
        temporary = summary_path.with_suffix(summary_path.suffix + f".tmp.{os.getpid()}")
        temporary.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        os.replace(temporary, summary_path)
    finally:
        logger.close(timeout=300)
        if wandb_run is not None:
            if metrics:
                final_summary = {
                    _wandb_metric_key(key): metrics[key]
                    for key in FINAL_VALIDATION_SUMMARY_KEYS
                    if key in metrics
                }
                # The logger writes the complete metric record to W&B history.
                # Pin only the interpretation-critical values in the summary
                # instead of duplicating hundreds of per-target fields there.
                wandb_run.summary.update(final_summary)
                wandb_run.summary.update({
                    "validation_record/status": (
                        "corrected_final" if args.corrected_final_record else "evaluated"
                    ),
                    "validation_record/manifest_hash": manifest["manifest_hash"],
                    "validation_record/source_checkpoint": str(checkpoint_path),
                    "validation_record/source_training_origins": source_samples,
                    "validation_record/wandb_log_step": wandb_log_step,
                })
            wandb_run.finish()
    print(f"{args.panel} evaluation complete: {run_root}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
