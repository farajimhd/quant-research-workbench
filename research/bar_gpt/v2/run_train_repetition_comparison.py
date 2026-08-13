from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shlex
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from research.bar_gpt.v2.config import (
    BAR_GPT_MODEL_COMPARISON_WANDB_PROJECT,
    MODEL_SIZE_PRESETS,
)
from research.bar_gpt.v2.model_discovery import (
    discovery_data_config,
    load_discovery_manifest,
)
from research.bar_gpt.v2.run_train import default_argv
from research.bar_gpt.v2.run_train_model_comparison import (
    COMPARISON_MONITOR_INTERVAL_ORIGINS,
    COMPARISON_MONITOR_ORIGINS,
    COMPARISON_RUNS,
    COMPARISON_SEED,
    COMPARISON_VALIDATION_ORIGINS,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_SHARD_ROOT,
    DEFAULT_WANDB_MODE,
    ensure_comparison_manifest,
)
from research.bar_gpt.v2.train import main as train_main


REPETITION_EPOCHS = 4
REPETITION_TRAIN_ORIGINS = 25_000_000
REPETITION_MANIFEST_NAME = "fixed_panels_v2_repeat25m_x4.json"
REPETITION_CONTRACT_VERSION = 1
SELECTION_CANDIDATE_SALTS = 256
MAX_ABSOLUTE_SHARE_DRIFT = 0.01


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Plan or execute BarGPT's fixed 25M-origin panel for four data "
            "epochs against the existing 100M-unique comparison."
        )
    )
    parser.add_argument("--model-size", choices=("all", *COMPARISON_RUNS), default="all")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--prepare-manifest-only",
        action="store_true",
        help="build or verify the nested repetition manifest without training",
    )
    parser.add_argument("--shard-root", default=str(DEFAULT_SHARD_ROOT))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument(
        "--run-stamp",
        default="",
        help="optional shared run suffix; defaults to YYYYmmdd-HHMMSS",
    )
    parser.add_argument(
        "--wandb-mode",
        choices=("auto", "online", "offline", "disabled"),
        default=DEFAULT_WANDB_MODE,
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def repetition_run_name(model_size: str, run_stamp: str) -> str:
    run = COMPARISON_RUNS[model_size]
    return (
        f"bar-gpt-v2-repeat25m-x4-{model_size}-micro{run.microbatch}-"
        f"accum{run.accumulation}-bucket{run.length_bucket_batches}-{run_stamp}"
    )


def _identity(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        str(row["unit_key"]),
        int(row["session_index"]),
        int(row["block_index"]),
        int(row["unit_index"]),
        int(row["block_offset"]),
    )


def _selection_digest(row: dict[str, Any], *, salt: int) -> bytes:
    payload = "|".join(
        str(value)
        for value in (COMPARISON_SEED, "repeat25m", salt, *_identity(row))
    )
    return hashlib.sha256(payload.encode("utf-8")).digest()


def _dimension_functions(
    rows: Sequence[dict[str, Any]],
) -> tuple[tuple[str, Callable[[dict[str, Any]], Any]], ...]:
    maximum_origins = max(int(row["origins"]) for row in rows)

    def length_bin(row: dict[str, Any]) -> int:
        # Ten fixed fill-ratio bins preserve the block-length/padding profile
        # without pretending every rare tail length must appear in 25% data.
        return min(9, (int(row["origins"]) * 10 - 1) // maximum_origins)

    return (
        ("ticker", lambda row: str(row["ticker"])),
        ("year", lambda row: str(row["local_date"])[:4]),
        ("month", lambda row: str(row["local_date"])[:7]),
        ("activity_regime", lambda row: int(row["activity_regime"])),
        ("session_phase", lambda row: str(row["session_phase"])),
        ("origin_length_decile", length_bin),
    )


def _distribution_audit(
    parent: Sequence[dict[str, Any]], subset: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    parent_origins = sum(int(row["origins"]) for row in parent)
    subset_origins = sum(int(row["origins"]) for row in subset)
    dimensions: dict[str, Any] = {}
    maximum_drift = 0.0
    total_drift = 0.0
    missing_total = 0
    for name, key in _dimension_functions(parent):
        parent_blocks = Counter(key(row) for row in parent)
        subset_blocks = Counter(key(row) for row in subset)
        parent_origin_counts: dict[Any, int] = defaultdict(int)
        subset_origin_counts: dict[Any, int] = defaultdict(int)
        for row in parent:
            parent_origin_counts[key(row)] += int(row["origins"])
        for row in subset:
            subset_origin_counts[key(row)] += int(row["origins"])
        missing = sorted(str(value) for value in set(parent_blocks) - set(subset_blocks))
        block_drift = max(
            abs(subset_blocks[value] / len(subset) - count / len(parent))
            for value, count in parent_blocks.items()
        )
        origin_drift = max(
            abs(
                subset_origin_counts[value] / subset_origins
                - count / parent_origins
            )
            for value, count in parent_origin_counts.items()
        )
        dimensions[name] = {
            "parent_values": len(parent_blocks),
            "subset_values": len(subset_blocks),
            "missing_values": missing,
            "max_absolute_block_share_drift": block_drift,
            "max_absolute_origin_share_drift": origin_drift,
        }
        missing_total += len(missing)
        maximum_drift = max(maximum_drift, block_drift, origin_drift)
        total_drift += block_drift + origin_drift
    return {
        "missing_dimension_values": missing_total,
        "max_absolute_share_drift": maximum_drift,
        "sum_absolute_share_drift": total_drift,
        "dimensions": dimensions,
    }


def _candidate_subset(
    parent: Sequence[dict[str, Any]], *, salt: int, target_origins: int
) -> tuple[dict[str, Any], ...]:
    ordered = sorted(parent, key=lambda row: (_selection_digest(row, salt=salt), _identity(row)))
    selected: list[dict[str, Any]] = []
    origins = 0
    for row in ordered:
        selected.append(row)
        origins += int(row["origins"])
        if origins >= target_origins:
            return tuple(selected)
    raise RuntimeError(
        f"parent comparison panel has only {origins:,} origins; {target_origins:,} required"
    )


def select_repetition_subset(
    parent: Sequence[dict[str, Any]],
) -> tuple[tuple[dict[str, Any], ...], int, dict[str, Any]]:
    if not parent:
        raise ValueError("parent training panel cannot be empty")
    identities = [_identity(row) for row in parent]
    if len(set(identities)) != len(identities):
        raise RuntimeError("parent training panel contains duplicate block identities")
    parent_origins = sum(int(row["origins"]) for row in parent)
    target_origins = max(
        REPETITION_TRAIN_ORIGINS,
        (parent_origins + REPETITION_EPOCHS - 1) // REPETITION_EPOCHS,
    )
    candidates: list[
        tuple[tuple[int, float, float, int], tuple[dict[str, Any], ...], int, dict[str, Any]]
    ] = []
    for salt in range(SELECTION_CANDIDATE_SALTS):
        selected = _candidate_subset(parent, salt=salt, target_origins=target_origins)
        audit = _distribution_audit(parent, selected)
        if audit["missing_dimension_values"]:
            continue
        if float(audit["max_absolute_share_drift"]) > MAX_ABSOLUTE_SHARE_DRIFT:
            continue
        selected_origins = sum(int(row["origins"]) for row in selected)
        rank = (
            abs(selected_origins * REPETITION_EPOCHS - parent_origins),
            float(audit["max_absolute_share_drift"]),
            float(audit["sum_absolute_share_drift"]),
            salt,
        )
        candidates.append((rank, selected, salt, audit))
    if not candidates:
        raise RuntimeError(
            "no deterministic 25% subset preserved every ticker/year/month/regime/phase/"
            "length bin within the 1% share-drift contract"
        )
    _rank, selected, salt, audit = min(candidates, key=lambda item: item[0])
    return selected, salt, audit


def _panel_summary(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return {
        "blocks": len(rows),
        "origins": sum(int(row["origins"]) for row in rows),
        "tickers": len({str(row["ticker"]) for row in rows}),
        "ticker_dates": len(
            {(str(row["ticker"]), str(row["local_date"])) for row in rows}
        ),
        "condition_blocks": sum(bool(row["has_condition_target"]) for row in rows),
        "activity_regimes": {
            str(regime): sum(int(row["activity_regime"]) == regime for row in rows)
            for regime in range(3)
        },
    }


def _canonical_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _validate_repetition_manifest(
    manifest: dict[str, Any], *, parent: dict[str, Any]
) -> None:
    experiment = manifest.get("repetition_experiment")
    if not isinstance(experiment, dict):
        raise RuntimeError("repetition manifest has no experiment provenance")
    expected_experiment = {
        "contract_version": REPETITION_CONTRACT_VERSION,
        "parent_manifest_hash": parent["manifest_hash"],
        "passes": REPETITION_EPOCHS,
        "requested_origins_per_pass": REPETITION_TRAIN_ORIGINS,
        "selection_seed": COMPARISON_SEED,
        "selection_candidate_salts": SELECTION_CANDIDATE_SALTS,
        "maximum_absolute_share_drift": MAX_ABSOLUTE_SHARE_DRIFT,
    }
    for key, expected in expected_experiment.items():
        if experiment.get(key) != expected:
            raise RuntimeError(f"repetition manifest has wrong {key}: {experiment.get(key)!r}")
    expected_targets = {
        "train_origins_per_epoch": REPETITION_TRAIN_ORIGINS,
        "monitor_origins": COMPARISON_MONITOR_ORIGINS,
        "validation_origins": COMPARISON_VALIDATION_ORIGINS,
        "locked_test_origins": 0,
    }
    if manifest.get("targets") != expected_targets:
        raise RuntimeError("repetition manifest has incorrect panel targets")
    if manifest.get("ranges") != parent.get("ranges") or manifest.get("cohorts") != parent.get("cohorts"):
        raise RuntimeError("repetition manifest changed comparison ranges or cohorts")
    panels = manifest.get("panels")
    parent_panels = parent.get("panels")
    if not isinstance(panels, dict) or not isinstance(parent_panels, dict):
        raise RuntimeError("repetition or parent manifest has no panels")
    for name in ("monitor", "validation"):
        if panels.get(name) != parent_panels.get(name):
            raise RuntimeError(f"repetition manifest changed the fixed {name} panel")
    parent_train = tuple(parent_panels["train"])
    selected = tuple(panels.get("train", ()))
    parent_identities = {_identity(row) for row in parent_train}
    selected_identities = [_identity(row) for row in selected]
    if not selected or len(set(selected_identities)) != len(selected_identities):
        raise RuntimeError("repetition training panel is empty or contains duplicates")
    if not set(selected_identities) <= parent_identities:
        raise RuntimeError("repetition training panel is not nested in the 100M parent")
    salt = int(experiment.get("selection_salt", -1))
    parent_origins = sum(int(row["origins"]) for row in parent_train)
    target_origins = max(
        REPETITION_TRAIN_ORIGINS,
        (parent_origins + REPETITION_EPOCHS - 1) // REPETITION_EPOCHS,
    )
    reproduced = _candidate_subset(parent_train, salt=salt, target_origins=target_origins)
    if [_identity(row) for row in reproduced] != selected_identities:
        raise RuntimeError("repetition training selection does not reproduce from its salt")
    audit = _distribution_audit(parent_train, selected)
    if audit["missing_dimension_values"] or float(audit["max_absolute_share_drift"]) > MAX_ABSOLUTE_SHARE_DRIFT:
        raise RuntimeError("repetition training panel violates its distribution contract")
    selected_origins = sum(int(row["origins"]) for row in selected)
    if selected_origins < REPETITION_TRAIN_ORIGINS:
        raise RuntimeError("repetition training panel is below 25M origins")
    maximum_block_origins = max(int(row["origins"]) for row in parent_train)
    if abs(selected_origins * REPETITION_EPOCHS - parent_origins) > maximum_block_origins:
        raise RuntimeError("repetition exposure differs from the parent by more than one block")
    expected_runtime_evidence = {
        "actual_origins_per_pass": selected_origins,
        "total_planned_origins": selected_origins * REPETITION_EPOCHS,
        "parent_actual_origins": parent_origins,
        "selection_audit": audit,
    }
    for key, expected in expected_runtime_evidence.items():
        if experiment.get(key) != expected:
            raise RuntimeError(f"repetition manifest has stale {key}")
    expected_summaries = {
        name: _panel_summary(tuple(rows)) for name, rows in panels.items()
    }
    if manifest.get("summaries") != expected_summaries:
        raise RuntimeError("repetition manifest panel summaries are stale")
    expected_subset_hash = hashlib.sha256(
        json.dumps(selected_identities, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if experiment.get("subset_identity_hash") != expected_subset_hash:
        raise RuntimeError("repetition subset identity hash mismatch")


def ensure_repetition_manifest(*, shard_root: Path, output_root: Path) -> Path:
    parent_path = ensure_comparison_manifest(shard_root=shard_root, output_root=output_root)
    config = discovery_data_config(shard_root)
    parent = load_discovery_manifest(parent_path, shard_root=shard_root, config=config)
    destination = output_root / REPETITION_MANIFEST_NAME
    if destination.is_file():
        manifest = load_discovery_manifest(destination, shard_root=shard_root, config=config)
        _validate_repetition_manifest(manifest, parent=parent)
        print(f"Reusing verified repetition manifest: {destination}", flush=True)
        return destination

    parent_train = tuple(parent["panels"]["train"])
    selected, salt, audit = select_repetition_subset(parent_train)
    selected_identities = [_identity(row) for row in selected]
    panels = {
        "train": list(selected),
        "monitor": list(parent["panels"]["monitor"]),
        "validation": list(parent["panels"]["validation"]),
    }
    selected_origins = sum(int(row["origins"]) for row in selected)
    value = {
        "contract_version": parent["contract_version"],
        "created_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "seed": parent["seed"],
        "shard_root": parent["shard_root"],
        "shard_config_hash": parent["shard_config_hash"],
        "cohorts": parent["cohorts"],
        "ranges": parent["ranges"],
        "targets": {
            "train_origins_per_epoch": REPETITION_TRAIN_ORIGINS,
            "monitor_origins": COMPARISON_MONITOR_ORIGINS,
            "validation_origins": COMPARISON_VALIDATION_ORIGINS,
            "locked_test_origins": 0,
        },
        "summaries": {name: _panel_summary(rows) for name, rows in panels.items()},
        "panels": panels,
        "repetition_experiment": {
            "contract_version": REPETITION_CONTRACT_VERSION,
            "parent_manifest_hash": parent["manifest_hash"],
            "passes": REPETITION_EPOCHS,
            "requested_origins_per_pass": REPETITION_TRAIN_ORIGINS,
            "actual_origins_per_pass": selected_origins,
            "total_planned_origins": selected_origins * REPETITION_EPOCHS,
            "parent_actual_origins": sum(int(row["origins"]) for row in parent_train),
            "selection_seed": COMPARISON_SEED,
            "selection_candidate_salts": SELECTION_CANDIDATE_SALTS,
            "selection_salt": salt,
            "maximum_absolute_share_drift": MAX_ABSOLUTE_SHARE_DRIFT,
            "selection_audit": audit,
            "subset_identity_hash": hashlib.sha256(
                json.dumps(selected_identities, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        },
    }
    value["manifest_hash"] = _canonical_hash(value)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    os.replace(temporary, destination)
    _validate_repetition_manifest(value, parent=parent)
    print(
        f"Built nested repetition manifest: origins/pass={selected_origins:,} "
        f"passes={REPETITION_EPOCHS} total={selected_origins * REPETITION_EPOCHS:,} "
        f"parent={value['repetition_experiment']['parent_actual_origins']:,} "
        f"max_share_drift={audit['max_absolute_share_drift']:.4%}",
        flush=True,
    )
    return destination


def trainer_argv(
    model_size: str,
    *,
    run_stamp: str,
    wandb_mode: str = DEFAULT_WANDB_MODE,
    shard_root: Path = DEFAULT_SHARD_ROOT,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    manifest_path: Path | None = None,
) -> list[str]:
    run = COMPARISON_RUNS[model_size]
    model = MODEL_SIZE_PRESETS[model_size]
    argv = [
        *default_argv(),
        "--run-name",
        repetition_run_name(model_size, run_stamp),
        "--wandb-project",
        BAR_GPT_MODEL_COMPARISON_WANDB_PROJECT,
        "--offline-shard-root",
        str(shard_root),
        "--experiment-manifest",
        str(manifest_path or output_root / REPETITION_MANIFEST_NAME),
        "--output-root",
        str(output_root / "runs"),
        "--epochs",
        str(REPETITION_EPOCHS),
        "--max-samples",
        "0",
        "--batch-size",
        str(run.microbatch),
        "--gradient-accumulation-steps",
        str(run.accumulation),
        "--offline-length-bucket-batches",
        str(run.length_bucket_batches),
        "--validation-batches",
        "0",
        "--warmup-samples",
        "4000000",
        "--scheduler-mode",
        "single-cosine",
        "--validation-runs-per-epoch",
        "1",
        "--validation-interval-samples",
        str(COMPARISON_MONITOR_INTERVAL_ORIGINS),
        "--validation-initial-samples",
        str(COMPARISON_MONITOR_INTERVAL_ORIGINS),
        "--monitor-evaluation-origins",
        "250000",
        "--full-validation-final-epoch-only",
        "--d-model",
        str(model["d_model"]),
        "--n-layers",
        str(model["n_layers"]),
        "--n-heads",
        str(model["n_heads"]),
        "--n-kv-heads",
        str(model["n_kv_heads"]),
    ]
    if wandb_mode != DEFAULT_WANDB_MODE:
        argv.extend(("--wandb-mode", wandb_mode))
    checkpoint = (
        output_root
        / "runs"
        / repetition_run_name(model_size, run_stamp)
        / "checkpoints"
        / "checkpoint_latest.pt"
    )
    if checkpoint.is_file():
        argv.extend(("--resume-checkpoint", str(checkpoint)))
    return argv


def _launcher_command(
    model_size: str,
    *,
    run_stamp: str,
    wandb_mode: str,
    execute: bool,
    shard_root: Path,
    output_root: Path,
) -> list[str]:
    command = [
        sys.executable,
        "-B",
        "-m",
        "research.bar_gpt.v2.run_train_repetition_comparison",
        "--model-size",
        model_size,
        "--run-stamp",
        run_stamp,
        "--shard-root",
        str(shard_root),
        "--output-root",
        str(output_root),
    ]
    if wandb_mode != DEFAULT_WANDB_MODE:
        command.extend(("--wandb-mode", wandb_mode))
    if execute:
        command.append("--execute")
    return command


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    run_stamp = args.run_stamp or time.strftime("%Y%m%d-%H%M%S")
    shard_root = Path(args.shard_root)
    output_root = Path(args.output_root)
    selected = tuple(COMPARISON_RUNS) if args.model_size == "all" else (args.model_size,)
    print(f"W&B project: {BAR_GPT_MODEL_COMPARISON_WANDB_PROJECT}", flush=True)
    print(
        "Repetition experiment: deterministic nested >=25M training origins x 4; "
        "same fixed monitor/validation panels, seed, model profiles, and cumulative "
        "~100M single-cosine schedule as the existing comparison.",
        flush=True,
    )
    print(
        "Evaluation cadence: monitor_* at the ends of data epochs 1/2/3; identical "
        "epoch_train_*, validation_*, and generalization-gap metrics only after epoch 4.",
        flush=True,
    )
    for model_size in selected:
        run = COMPARISON_RUNS[model_size]
        print(
            f"{model_size}: microbatch={run.microbatch} accumulation={run.accumulation} "
            f"effective_blocks={run.effective_blocks} bucket={run.length_bucket_batches}",
            flush=True,
        )
        print(
            "Command: "
            + " ".join(
                shlex.quote(item)
                for item in _launcher_command(
                    model_size,
                    run_stamp=run_stamp,
                    wandb_mode=args.wandb_mode,
                    execute=True,
                    shard_root=shard_root,
                    output_root=output_root,
                )
            ),
            flush=True,
        )
    if args.prepare_manifest_only:
        manifest = ensure_repetition_manifest(
            shard_root=shard_root, output_root=output_root
        )
        print(f"Repetition manifest ready: {manifest}", flush=True)
        return 0
    if not args.execute:
        return 0
    if args.model_size == "all":
        child_env = os.environ.copy()
        child_env["PYTHONDONTWRITEBYTECODE"] = "1"
        for index, model_size in enumerate(selected, start=1):
            command = _launcher_command(
                model_size,
                run_stamp=run_stamp,
                wandb_mode=args.wandb_mode,
                execute=True,
                shard_root=shard_root,
                output_root=output_root,
            )
            print(
                f"Starting repetition run {index}/{len(selected)}: {model_size}",
                flush=True,
            )
            completed = subprocess.run(command, env=child_env, check=False)
            if completed.returncode:
                print(
                    f"Repetition comparison stopped: {model_size} exited with code "
                    f"{completed.returncode}; later sizes were not started.",
                    flush=True,
                )
                return int(completed.returncode)
            print(f"Completed repetition run {index}/{len(selected)}: {model_size}", flush=True)
        return 0
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    manifest_path = ensure_repetition_manifest(
        shard_root=shard_root, output_root=output_root
    )
    resolved = trainer_argv(
        selected[0],
        run_stamp=run_stamp,
        wandb_mode=args.wandb_mode,
        shard_root=shard_root,
        output_root=output_root,
        manifest_path=manifest_path,
    )
    equivalent = [sys.executable, "-B", "-m", "research.bar_gpt.v2.train", *resolved]
    print(
        "Equivalent trainer command: "
        + " ".join(shlex.quote(item) for item in equivalent),
        flush=True,
    )
    return int(train_main(resolved) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
