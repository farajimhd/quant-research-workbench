from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from research.bar_gpt.v1.config import BAR_GPT_MODEL_COMPARISON_WANDB_PROJECT
from research.bar_gpt.v1.model_discovery import discovery_data_config, load_discovery_manifest
from research.bar_gpt.v1.offline_shards import verify_shard_catalog_lock
from research.bar_gpt.v1.run_train_model_comparison import (
    COMPARISON_MANIFEST_NAME,
    COMPARISON_RUNS,
    COMPARISON_TRAIN_ORIGINS,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_SHARD_ROOT,
    _validate_comparison_manifest,
)


CORRECTED_VALIDATION_CONTRACT = "bar_gpt_v1_model_comparison_corrected_validation_v1"
STATE_CONTRACT_VERSION = 1
DEFAULT_OUTPUT_DIRECTORY = "corrected_final_validation_v1"
RUN_PATTERN = re.compile(
    r"^bar-gpt-v1-epoch1-(current|medium|large)-micro(\d+)-accum(\d+)-bucket(\d+)-(.+)$"
)


@dataclass(frozen=True, slots=True)
class ComparisonCheckpoint:
    model_size: str
    run_stamp: str
    run_name: str
    run_root: Path
    checkpoint: Path
    batch_size: int
    training_origins: int
    wandb_project: str
    wandb_entity: str
    wandb_run_id: str


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Reevaluate completed BarGPT v1 comparison checkpoints on the corrected complete "
            "validation panel and append validation_* to their original W&B runs."
        )
    )
    parser.add_argument("--comparison-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--shard-root", default=str(DEFAULT_SHARD_ROOT))
    parser.add_argument("--manifest", default="")
    parser.add_argument("--run-stamp", default="", help="empty selects the newest complete model-size set")
    parser.add_argument("--model-sizes", default="all", help="comma-separated current,medium,large or all")
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--batch-size", type=int, default=0, help="0 reuses each source run's microbatch")
    parser.add_argument("--wandb-mode", choices=("auto", "online", "offline", "disabled"), default="online")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="append another corrected record even if cached")
    return parser.parse_args(list(argv) if argv is not None else None)


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _selected_model_sizes(value: str) -> tuple[str, ...]:
    selected = tuple(COMPARISON_RUNS) if value == "all" else tuple(
        item.strip() for item in value.split(",") if item.strip()
    )
    unknown = sorted(set(selected) - set(COMPARISON_RUNS))
    if unknown:
        raise ValueError(f"unknown model sizes: {unknown}")
    if not selected:
        raise ValueError("at least one model size is required")
    return selected


def _run_groups(runs_root: Path) -> dict[str, dict[str, tuple[Path, re.Match[str]]]]:
    groups: dict[str, dict[str, tuple[Path, re.Match[str]]]] = {}
    if not runs_root.is_dir():
        raise RuntimeError(f"comparison runs directory is missing: {runs_root}")
    for path in runs_root.iterdir():
        if not path.is_dir():
            continue
        match = RUN_PATTERN.fullmatch(path.name)
        if match is None:
            continue
        model_size, _microbatch, _accumulation, _bucket, run_stamp = match.groups()
        if model_size in groups.setdefault(run_stamp, {}):
            raise RuntimeError(f"duplicate {model_size} comparison run for stamp {run_stamp}")
        groups[run_stamp][model_size] = (path, match)
    return groups


def resolve_run_stamp(
    *, runs_root: Path, requested: str, model_sizes: tuple[str, ...]
) -> tuple[str, dict[str, tuple[Path, re.Match[str]]]]:
    groups = _run_groups(runs_root)
    if requested:
        selected = groups.get(requested)
        if selected is None:
            raise RuntimeError(f"no comparison runs found for stamp {requested}")
        missing = sorted(set(model_sizes) - set(selected))
        if missing:
            raise RuntimeError(f"comparison stamp {requested} is missing model sizes: {missing}")
        return requested, selected
    complete = [
        (stamp, rows)
        for stamp, rows in groups.items()
        if set(model_sizes).issubset(rows)
    ]
    if not complete:
        raise RuntimeError(f"no complete comparison run set found under {runs_root}")
    return max(
        complete,
        key=lambda item: max(item[1][name][0].stat().st_mtime_ns for name in model_sizes),
    )


def _read_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError(f"{label} is missing: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must contain a JSON object: {path}")
    return value


def resolve_comparison_checkpoints(
    *,
    comparison_root: Path,
    manifest_path: Path,
    run_stamp: str,
    model_sizes: tuple[str, ...],
    batch_size_override: int = 0,
) -> tuple[ComparisonCheckpoint, ...]:
    selected_stamp, rows = resolve_run_stamp(
        runs_root=comparison_root / "runs",
        requested=run_stamp,
        model_sizes=model_sizes,
    )
    resolved: list[ComparisonCheckpoint] = []
    for model_size in model_sizes:
        run_root, match = rows[model_size]
        _name, microbatch, accumulation, bucket, _stamp = match.groups()
        expected = COMPARISON_RUNS[model_size]
        observed_profile = (int(microbatch), int(accumulation), int(bucket))
        expected_profile = (
            expected.microbatch,
            expected.accumulation,
            expected.length_bucket_batches,
        )
        if observed_profile != expected_profile:
            raise RuntimeError(
                f"{model_size} run profile mismatch: expected {expected_profile}, observed {observed_profile}"
            )
        checkpoint = run_root / "checkpoints" / "checkpoint_latest.pt"
        if not checkpoint.is_file():
            raise RuntimeError(f"latest checkpoint is missing for {model_size}: {checkpoint}")
        run_manifest = _read_json(run_root / "run_manifest.json", "run manifest")
        model_card = _read_json(run_root / "model_card.json", "model card")
        if run_manifest.get("model_family") != "bar_gpt" or run_manifest.get("version") != "v1":
            raise RuntimeError(f"run is not a BarGPT v1 training run: {run_root}")
        source_manifest = Path(str(run_manifest.get("args", {}).get("experiment_manifest", "")))
        if source_manifest.resolve() != manifest_path.resolve():
            raise RuntimeError(
                f"{model_size} was trained against {source_manifest}, not {manifest_path}"
            )
        training_origins = int(model_card.get("samples_seen", 0))
        if training_origins < COMPARISON_TRAIN_ORIGINS:
            raise RuntimeError(
                f"{model_size} checkpoint is incomplete: {training_origins:,} training origins"
            )
        wandb = run_manifest.get("wandb", {})
        wandb_project = str(wandb.get("project", ""))
        wandb_entity = str(wandb.get("entity", ""))
        wandb_run_id = str(wandb.get("run_id", ""))
        if wandb_project != BAR_GPT_MODEL_COMPARISON_WANDB_PROJECT:
            raise RuntimeError(f"unexpected W&B project for {model_size}: {wandb_project!r}")
        if not wandb_entity or not wandb_run_id:
            raise RuntimeError(f"durable W&B identity is incomplete for {model_size}")
        resolved.append(ComparisonCheckpoint(
            model_size=model_size,
            run_stamp=selected_stamp,
            run_name=run_root.name,
            run_root=run_root,
            checkpoint=checkpoint,
            batch_size=int(batch_size_override) or int(microbatch),
            training_origins=training_origins,
            wandb_project=wandb_project,
            wandb_entity=wandb_entity,
            wandb_run_id=wandb_run_id,
        ))
    return tuple(resolved)


def evaluation_command(
    item: ComparisonCheckpoint,
    *,
    manifest_path: Path,
    shard_root: Path,
    output_root: Path,
    local_run_name: str,
    workers: int,
    wandb_mode: str,
    wandb_log_step: int,
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
        local_run_name,
        "--panel",
        "validation",
        "--namespace",
        "validation",
        "--architecture",
        item.model_size,
        "--target-training-origins",
        str(COMPARISON_TRAIN_ORIGINS),
        "--batch-size",
        str(item.batch_size),
        "--loader-workers",
        str(workers),
        "--wandb-project",
        item.wandb_project,
        "--wandb-entity",
        item.wandb_entity,
        "--wandb-mode",
        wandb_mode,
        "--wandb-run-id",
        item.wandb_run_id,
        "--wandb-run-name",
        item.run_name,
        "--wandb-log-step",
        str(wandb_log_step),
        "--corrected-final-record",
        "--evaluation-contract",
        CORRECTED_VALIDATION_CONTRACT,
    ]


def _summary_matches(
    summary: dict[str, Any],
    *,
    item: ComparisonCheckpoint,
    manifest_hash: str,
) -> bool:
    checkpoint = item.checkpoint
    return (
        summary.get("evaluation_contract") == CORRECTED_VALIDATION_CONTRACT
        and bool(summary.get("corrected_final_record"))
        and summary.get("panel") == "validation"
        and summary.get("namespace") == "validation"
        and summary.get("manifest_hash") == manifest_hash
        and summary.get("wandb_run_id") == item.wandb_run_id
        and Path(str(summary.get("checkpoint", ""))) == checkpoint
        and int(summary.get("checkpoint_size", -1)) == checkpoint.stat().st_size
        and int(summary.get("checkpoint_mtime_ns", -1)) == checkpoint.stat().st_mtime_ns
    )


def _write_consolidated_summary(output_root: Path, summaries: list[dict[str, Any]]) -> None:
    ordered = sorted(summaries, key=lambda item: str(item["architecture"]))
    _atomic_json(output_root / "summary.json", {
        "contract_version": STATE_CONTRACT_VERSION,
        "evaluation_contract": CORRECTED_VALIDATION_CONTRACT,
        "models": ordered,
    })
    columns = (
        "architecture",
        "step",
        "wandb_log_step",
        "wandb_run_id",
        "training_complete",
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
    csv_path = output_root / "summary.csv"
    temporary = csv_path.with_suffix(csv_path.suffix + f".tmp.{os.getpid()}")
    with temporary.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(ordered)
    os.replace(temporary, csv_path)
    print("\nCorrected final comparison validation", flush=True)
    print("model      origins    loss       MAE bps   close MCC  AR MCC", flush=True)
    for item in ordered:
        print(
            f"{str(item['architecture']):<9} "
            f"{int(item['step']):>10,}  "
            f"{float(item['validation_loss/total']):>9.5f}  "
            f"{float(item['validation_trade_summary/mae_macro']):>8.3f}  "
            f"{float(item['validation_close_direction_summary/mcc_macro']):>9.4f}  "
            f"{float(item['validation_ar_direction_mcc/mcc_macro']):>7.4f}",
            flush=True,
        )
    print(f"Consolidated JSON: {output_root / 'summary.json'}", flush=True)
    print(f"Consolidated CSV:  {csv_path}", flush=True)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    if args.workers < 0 or args.batch_size < 0:
        raise ValueError("workers and batch size cannot be negative")
    comparison_root = Path(args.comparison_root)
    shard_root = Path(args.shard_root)
    verify_shard_catalog_lock(shard_root)
    manifest_path = Path(args.manifest) if args.manifest else comparison_root / COMPARISON_MANIFEST_NAME
    manifest = load_discovery_manifest(
        manifest_path,
        shard_root=shard_root,
        config=discovery_data_config(shard_root),
    )
    all_tickers = tuple(discovery_data_config(shard_root).tickers)
    _validate_comparison_manifest(manifest, all_tickers=all_tickers)
    model_sizes = _selected_model_sizes(str(args.model_sizes))
    checkpoints = resolve_comparison_checkpoints(
        comparison_root=comparison_root,
        manifest_path=manifest_path,
        run_stamp=str(args.run_stamp),
        model_sizes=model_sizes,
        batch_size_override=int(args.batch_size),
    )
    selected_stamp = checkpoints[0].run_stamp
    output_root = comparison_root / DEFAULT_OUTPUT_DIRECTORY / selected_stamp
    state_path = output_root / "state.json"
    state = _read_json(state_path, "evaluation state") if state_path.is_file() else {
        "contract_version": STATE_CONTRACT_VERSION,
        "evaluation_contract": CORRECTED_VALIDATION_CONTRACT,
        "evaluation_id": time.strftime("%Y%m%d-%H%M%S"),
        "run_stamp": selected_stamp,
        "manifest": str(manifest_path),
        "manifest_hash": str(manifest["manifest_hash"]),
        "results": {},
        "attempts": {},
    }
    if int(state.get("contract_version", -1)) != STATE_CONTRACT_VERSION:
        raise RuntimeError("unsupported corrected-validation state contract")
    if state.get("evaluation_contract") != CORRECTED_VALIDATION_CONTRACT:
        raise RuntimeError("corrected-validation state has the wrong evaluation contract")
    if state.get("run_stamp") != selected_stamp or state.get("manifest_hash") != manifest["manifest_hash"]:
        raise RuntimeError("corrected-validation state belongs to a different comparison population")
    print(f"Comparison stamp: {selected_stamp}", flush=True)
    print(f"Manifest: {manifest_path} ({manifest['manifest_hash']})", flush=True)
    print(
        f"Validation panel: {int(manifest['summaries']['validation']['origins']):,} origins, "
        f"{int(manifest['summaries']['validation']['tickers']):,} tickers",
        flush=True,
    )
    for item in checkpoints:
        print(
            f"{item.model_size}: checkpoint={item.checkpoint} batch={item.batch_size} "
            f"W&B={item.wandb_entity}/{item.wandb_project}/{item.wandb_run_id}",
            flush=True,
        )
    if args.dry_run:
        for item in checkpoints:
            command = evaluation_command(
                item,
                manifest_path=manifest_path,
                shard_root=shard_root,
                output_root=output_root / "runs",
                local_run_name=f"corrected-validation-{item.model_size}-dry-run",
                workers=int(args.workers),
                wandb_mode=str(args.wandb_mode),
                wandb_log_step=item.training_origins + 1,
            )
            print("Command: " + " ".join(shlex.quote(value) for value in command), flush=True)
        print("Dry run complete; omit --dry-run to evaluate sequentially.", flush=True)
        return 0

    results = dict(state.get("results", {}))
    attempts = dict(state.get("attempts", {}))
    summaries: list[dict[str, Any]] = []
    for index, item in enumerate(checkpoints, start=1):
        existing_run = str(results.get(item.model_size, ""))
        existing_summary_path = output_root / "runs" / existing_run / "summary.json" if existing_run else None
        if not args.force and existing_summary_path is not None and existing_summary_path.is_file():
            existing = _read_json(existing_summary_path, "evaluation summary")
            if _summary_matches(existing, item=item, manifest_hash=str(manifest["manifest_hash"])):
                print(f"[{index}/{len(checkpoints)}] {item.model_size}: verified cached result", flush=True)
                summaries.append(existing)
                continue
        attempt = int(attempts.get(item.model_size, 0)) + 1
        attempts[item.model_size] = attempt
        local_run_name = (
            f"corrected-validation-{item.model_size}-{state['evaluation_id']}-a{attempt}"
        )
        state["attempts"] = attempts
        _atomic_json(state_path, state)
        command = evaluation_command(
            item,
            manifest_path=manifest_path,
            shard_root=shard_root,
            output_root=output_root / "runs",
            local_run_name=local_run_name,
            workers=int(args.workers),
            wandb_mode=str(args.wandb_mode),
            wandb_log_step=item.training_origins + attempt,
        )
        print(f"[{index}/{len(checkpoints)}] evaluating {item.model_size}", flush=True)
        print("Command: " + " ".join(shlex.quote(value) for value in command), flush=True)
        completed = subprocess.run(
            command,
            cwd=str(Path(__file__).resolve().parents[3]),
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            check=False,
        )
        if completed.returncode:
            raise RuntimeError(
                f"corrected validation failed for {item.model_size} with exit code "
                f"{completed.returncode}; rerun to retry"
            )
        summary_path = output_root / "runs" / local_run_name / "summary.json"
        summary = _read_json(summary_path, "evaluation summary")
        if not _summary_matches(summary, item=item, manifest_hash=str(manifest["manifest_hash"])):
            raise RuntimeError(f"evaluation summary does not certify {item.model_size}: {summary_path}")
        results[item.model_size] = local_run_name
        state["results"] = results
        _atomic_json(state_path, state)
        summaries.append(summary)
    _write_consolidated_summary(output_root, summaries)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
