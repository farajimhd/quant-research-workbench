from __future__ import annotations

import argparse
import csv
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from research.bar_gpt.v1.model_discovery import (
    ARCHITECTURE_GRID,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_SHARD_ROOT,
    DISCOVERY_EPOCHS,
    discovery_data_config,
    load_discovery_manifest,
)


FINAL_VALIDATION_CONTRACT_VERSION = 6
FINAL_VALIDATION_WANDB_PROJECT = "bar gpt model discovery final validation"


@dataclass(frozen=True, slots=True)
class ArchitectureCheckpoint:
    architecture: str
    run_name: str
    checkpoint: Path
    batch_size: int


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate every BarGPT discovery architecture checkpoint on the complete fixed validation panel."
    )
    parser.add_argument("--discovery-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--shard-root", default=str(DEFAULT_SHARD_ROOT))
    parser.add_argument("--manifest", default="")
    parser.add_argument("--architectures", default="all", help="comma-separated architecture names or all")
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--batch-size", type=int, default=0, help="0 uses each architecture's trained microbatch")
    parser.add_argument("--wandb-project", default=FINAL_VALIDATION_WANDB_PROJECT)
    parser.add_argument("--wandb-entity", default="mehdifaraji")
    parser.add_argument("--wandb-mode", choices=("auto", "online", "offline", "disabled"), default="online")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="create a new evaluation even if the checkpoint was already evaluated")
    return parser.parse_args(list(argv) if argv is not None else None)


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def resolve_architecture_checkpoints(
    *,
    discovery_root: Path,
    campaign_state: dict[str, Any],
    architecture_names: tuple[str, ...],
    batch_size_override: int = 0,
) -> tuple[ArchitectureCheckpoint, ...]:
    by_name = {item.name: item for item in ARCHITECTURE_GRID}
    unknown = sorted(set(architecture_names) - set(by_name))
    if unknown:
        raise ValueError(f"unknown architectures: {unknown}")
    campaign_id = str(campaign_state.get("campaign_id", "")).strip()
    if not campaign_id:
        raise RuntimeError("discovery campaign state has no campaign_id")
    completed_runs = dict(campaign_state.get("runs", {}))
    resolved: list[ArchitectureCheckpoint] = []
    for name in architecture_names:
        architecture = by_name[name]
        run_name = str(
            completed_runs.get(
                f"architecture/{name}",
                f"discovery-architecture-{name}-{campaign_id}",
            )
        )
        checkpoint = discovery_root / "runs" / run_name / "checkpoints" / "checkpoint_latest.pt"
        if not checkpoint.is_file():
            raise RuntimeError(f"latest checkpoint is missing for {name}: {checkpoint}")
        resolved.append(
            ArchitectureCheckpoint(
                architecture=name,
                run_name=run_name,
                checkpoint=checkpoint,
                batch_size=int(batch_size_override) or int(architecture.microbatch),
            )
        )
    return tuple(resolved)


def evaluation_command(
    item: ArchitectureCheckpoint,
    *,
    manifest_path: Path,
    shard_root: Path,
    output_root: Path,
    run_name: str,
    target_training_origins: int,
    workers: int,
    wandb_project: str,
    wandb_entity: str,
    wandb_mode: str,
) -> list[str]:
    return [
        sys.executable,
        "-B",
        "-m",
        "research.bar_gpt.v1.evaluate_discovery_checkpoint",
        "--checkpoint",
        str(item.checkpoint),
        "--experiment-manifest",
        str(manifest_path),
        "--offline-shard-root",
        str(shard_root),
        "--output-root",
        str(output_root),
        "--run-name",
        run_name,
        "--panel",
        "validation",
        "--namespace",
        "final_validation",
        "--architecture",
        item.architecture,
        "--target-training-origins",
        str(target_training_origins),
        "--batch-size",
        str(item.batch_size),
        "--loader-workers",
        str(workers),
        "--wandb-project",
        wandb_project,
        "--wandb-entity",
        wandb_entity,
        "--wandb-mode",
        wandb_mode,
    ]


def _summary_matches_checkpoint(summary: dict[str, Any], checkpoint: Path) -> bool:
    return (
        Path(str(summary.get("checkpoint", ""))) == checkpoint
        and int(summary.get("checkpoint_size", -1)) == checkpoint.stat().st_size
        and int(summary.get("checkpoint_mtime_ns", -1)) == checkpoint.stat().st_mtime_ns
        and summary.get("panel") == "validation"
        and summary.get("namespace") == "final_validation"
    )


def _write_consolidated_summary(output_root: Path, summaries: list[dict[str, Any]]) -> None:
    ordered = sorted(
        summaries,
        key=lambda item: (
            float(item.get("final_validation_loss/total", float("inf"))),
            -float(item.get("final_validation_trade_summary/mcc_macro", float("-inf"))),
            -float(item.get("final_validation_ar_direction_mcc/mcc_macro", float("-inf"))),
        ),
    )
    _atomic_json(
        output_root / "summary.json",
        {
            "contract_version": FINAL_VALIDATION_CONTRACT_VERSION,
            "architectures": ordered,
        },
    )
    columns = (
        "architecture",
        "step",
        "target_training_origins",
        "training_complete",
        "model_parameters",
        "final_validation_loss/total",
        "final_validation_trade_summary/mae_macro",
        "final_validation_trade_summary/balanced_macro",
        "final_validation_trade_summary/mcc_macro",
        "final_validation_ar_direction_balanced/balanced_accuracy_macro",
        "final_validation_ar_direction_mcc/mcc_macro",
        "final_validation_trade_summary/rank_macro",
        "final_validation_trade_summary/calibration_macro",
        "final_validation_availability/brier_macro",
    )
    csv_path = output_root / "summary.csv"
    temporary = csv_path.with_suffix(csv_path.suffix + f".tmp.{os.getpid()}")
    with temporary.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(ordered)
    os.replace(temporary, csv_path)
    print("\nFinal validation scorecard", flush=True)
    print("architecture             train       complete  loss      MAE bps   H-MCC     AR-MCC    Spearman", flush=True)
    for item in ordered:
        print(
            f"{str(item['architecture']):<24} "
            f"{int(item['step']) / 1_000_000:>7.1f}M  "
            f"{str(bool(item['training_complete'])):<8}  "
            f"{float(item['final_validation_loss/total']):>8.4f}  "
            f"{float(item['final_validation_trade_summary/mae_macro']):>8.2f}  "
            f"{float(item['final_validation_trade_summary/mcc_macro']):>8.4f}  "
            f"{float(item['final_validation_ar_direction_mcc/mcc_macro']):>8.4f}  "
            f"{float(item.get('final_validation_trade_summary/rank_macro', float('nan'))):>8.4f}",
            flush=True,
        )
    print(f"Consolidated JSON: {output_root / 'summary.json'}", flush=True)
    print(f"Consolidated CSV:  {csv_path}", flush=True)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    if args.workers < 0 or args.batch_size < 0:
        raise ValueError("workers and batch size cannot be negative")
    discovery_root = Path(args.discovery_root)
    shard_root = Path(args.shard_root)
    manifest_path = Path(args.manifest) if args.manifest else discovery_root / "fixed_panels_v5.json"
    campaign_state_path = discovery_root / "campaign_state_v5.json"
    if not campaign_state_path.is_file():
        raise RuntimeError(f"discovery campaign state is missing: {campaign_state_path}")
    campaign_state = json.loads(campaign_state_path.read_text(encoding="utf-8"))
    manifest = load_discovery_manifest(
        manifest_path,
        shard_root=shard_root,
        config=discovery_data_config(),
    )
    by_name = {item.name: item for item in ARCHITECTURE_GRID}
    architecture_names = tuple(by_name) if args.architectures == "all" else tuple(
        value.strip() for value in str(args.architectures).split(",") if value.strip()
    )
    checkpoints = resolve_architecture_checkpoints(
        discovery_root=discovery_root,
        campaign_state=campaign_state,
        architecture_names=architecture_names,
        batch_size_override=int(args.batch_size),
    )
    target_training_origins = int(manifest["targets"]["train_origins_per_epoch"]) * DISCOVERY_EPOCHS
    output_root = discovery_root / "final_validation_v2"
    state_path = output_root / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {
        "contract_version": FINAL_VALIDATION_CONTRACT_VERSION,
        "evaluation_id": time.strftime("%Y%m%d-%H%M%S"),
        "source_campaign_id": str(campaign_state["campaign_id"]),
        "manifest": str(manifest_path),
        "manifest_hash": str(manifest["manifest_hash"]),
        "results": {},
        "attempts": {},
    }
    if int(state.get("contract_version", -1)) != FINAL_VALIDATION_CONTRACT_VERSION:
        raise RuntimeError("unsupported final-validation state contract")
    if state.get("manifest_hash") != manifest["manifest_hash"]:
        raise RuntimeError("final-validation state belongs to a different manifest")
    print(f"W&B project: {args.wandb_project}", flush=True)
    print("Metric namespace: final_validation_*", flush=True)
    print(f"Panel: validation ({manifest['summaries']['validation']['origins']:,} origins)", flush=True)
    print(f"Target training exposure: {target_training_origins:,} origins", flush=True)
    for item in checkpoints:
        print(
            f"{item.architecture}: checkpoint={item.checkpoint} batch={item.batch_size}",
            flush=True,
        )
    if args.dry_run:
        print("Dry run complete; omit --dry-run to evaluate sequentially.", flush=True)
        return 0

    results = dict(state.get("results", {}))
    attempts = dict(state.get("attempts", {}))
    summaries: list[dict[str, Any]] = []
    for index, item in enumerate(checkpoints, start=1):
        existing_run = str(results.get(item.architecture, ""))
        existing_summary_path = output_root / "runs" / existing_run / "summary.json" if existing_run else None
        if not args.force and existing_summary_path is not None and existing_summary_path.is_file():
            existing_summary = json.loads(existing_summary_path.read_text(encoding="utf-8"))
            if _summary_matches_checkpoint(existing_summary, item.checkpoint):
                print(f"[{index}/{len(checkpoints)}] {item.architecture}: verified cached result", flush=True)
                summaries.append(existing_summary)
                continue
        attempt = int(attempts.get(item.architecture, 0)) + 1
        attempts[item.architecture] = attempt
        run_name = (
            f"final-validation-{item.architecture}-{state['evaluation_id']}-a{attempt}"
        )
        state["attempts"] = attempts
        _atomic_json(state_path, state)
        command = evaluation_command(
            item,
            manifest_path=manifest_path,
            shard_root=shard_root,
            output_root=output_root / "runs",
            run_name=run_name,
            target_training_origins=target_training_origins,
            workers=int(args.workers),
            wandb_project=str(args.wandb_project),
            wandb_entity=str(args.wandb_entity),
            wandb_mode=str(args.wandb_mode),
        )
        print(f"[{index}/{len(checkpoints)}] evaluating {item.architecture}", flush=True)
        print("Command: " + " ".join(shlex.quote(value) for value in command), flush=True)
        result = subprocess.run(command, cwd=str(Path(__file__).resolve().parents[3]), check=False)
        if result.returncode:
            raise RuntimeError(
                f"final validation failed for {item.architecture} with exit code {result.returncode}; rerun to retry"
            )
        summary_path = output_root / "runs" / run_name / "summary.json"
        if not summary_path.is_file():
            raise RuntimeError(f"evaluation completed without a summary: {summary_path}")
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        if not _summary_matches_checkpoint(summary, item.checkpoint):
            raise RuntimeError(f"evaluation summary does not certify the requested checkpoint: {summary_path}")
        results[item.architecture] = run_name
        state["results"] = results
        _atomic_json(state_path, state)
        summaries.append(summary)
    _write_consolidated_summary(output_root, summaries)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
