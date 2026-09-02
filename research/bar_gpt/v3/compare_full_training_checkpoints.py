from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


DEFAULT_RUN_NAME = (
    "bar-gpt-v3-full-medium-chunks30m-epoch2-chunkepochs20-"
    "chunkcosine-decay95-micro10-accum4-bucket16-production"
)
DEFAULT_RUN_ROOT = (
    Path(r"D:\TradingML\runtimes\bar_gpt\v3\full_training") / DEFAULT_RUN_NAME
)
COMPARISON_CONTRACT_VERSION = 1
METRIC_NAMESPACE = "checkpoint_comparison"
EPOCH_CHECKPOINT = re.compile(r"checkpoint_epoch_(\d+)\.pt$")


@dataclass(frozen=True, slots=True)
class CheckpointSelection:
    role: str
    rationale: str
    checkpoint: Path
    samples_seen: int
    recorded_sha256: str
    source_global_validation: Mapping[str, Any] | None = None


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate four BarGPT v3 training checkpoints on the complete frozen "
            "validation panel and write an aligned accuracy/error scorecard."
        )
    )
    parser.add_argument("--run-root", default=str(DEFAULT_RUN_ROOT))
    parser.add_argument(
        "--manifest",
        default="",
        help="default: experiment_manifest recorded by the completed run",
    )
    parser.add_argument(
        "--shard-root",
        default="",
        help="default: offline_shard_root recorded by the completed run",
    )
    parser.add_argument(
        "--output-root",
        default="",
        help="default: <run-root>/checkpoint_comparison_full_validation_v1",
    )
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--loader-workers", type=int, default=8)
    parser.add_argument(
        "--wandb-mode",
        choices=("auto", "online", "offline", "disabled"),
        default="disabled",
    )
    parser.add_argument("--wandb-project", default="bar gpt v3 checkpoint comparison")
    parser.add_argument("--wandb-entity", default="mehdifaraji")
    parser.add_argument(
        "--force",
        action="store_true",
        help="rerun evaluations even when hash-bound cached summaries exist",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="run GPU inference; without this flag the script only verifies and previews",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected a JSON object: {path}")
    return value


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_json_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _load_global_validation_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise RuntimeError(f"invalid global-validation record at line {line_number}")
            quality = value.get("promotion_quality")
            if not isinstance(quality, dict):
                raise RuntimeError(
                    f"global-validation record {line_number} has no promotion quality"
                )
            for key in (
                "trade_close_mae_bps",
                "trade_range_mae_bps",
                "close_mcc",
                "trade_calibration",
            ):
                if key not in quality:
                    raise RuntimeError(
                        f"global-validation record {line_number} is missing {key}"
                    )
            if int(value.get("samples_seen", 0)) <= 0:
                raise RuntimeError(
                    f"global-validation record {line_number} has no sample clock"
                )
            records.append(value)
    if not records:
        raise RuntimeError(f"global-validation history is empty: {path}")
    samples = [int(item["samples_seen"]) for item in records]
    if samples != sorted(samples) or len(samples) != len(set(samples)):
        raise RuntimeError("global-validation sample clocks are not unique and increasing")
    manifest_hashes = {
        str(item.get("experiment_manifest_hash", "")) for item in records
    }
    if len(manifest_hashes) != 1 or not next(iter(manifest_hashes)):
        raise RuntimeError("global-validation records do not share one manifest hash")
    return records


def _record_checkpoint(record: Mapping[str, Any], checkpoint_root: Path) -> Path:
    name = Path(str(record.get("checkpoint", ""))).name
    if not name:
        raise RuntimeError("global-validation record has no checkpoint path")
    path = checkpoint_root / name
    if not path.is_file():
        raise RuntimeError(f"recorded immutable checkpoint is missing: {path}")
    return path


def select_checkpoints(
    *,
    run_root: Path,
    records: list[dict[str, Any]],
    model_card: Mapping[str, Any],
) -> tuple[CheckpointSelection, ...]:
    checkpoint_root = run_root / "checkpoints"
    first = records[0]
    best_close = min(
        records,
        key=lambda item: (
            float(item["promotion_quality"]["trade_close_mae_bps"]),
            -float(item["promotion_quality"]["close_mcc"]),
            int(item["samples_seen"]),
        ),
    )
    best_range = min(
        records,
        key=lambda item: (
            float(item["promotion_quality"]["trade_range_mae_bps"]),
            float(item["promotion_quality"]["trade_close_mae_bps"]),
            int(item["samples_seen"]),
        ),
    )
    epoch_checkpoints: list[tuple[int, Path]] = []
    for path in checkpoint_root.glob("checkpoint_epoch_*.pt"):
        match = EPOCH_CHECKPOINT.fullmatch(path.name)
        if match:
            epoch_checkpoints.append((int(match.group(1)), path))
    if epoch_checkpoints:
        _epoch, final_path = max(epoch_checkpoints, key=lambda item: item[0])
    else:
        final_path = checkpoint_root / "checkpoint_latest.pt"
    if not final_path.is_file():
        raise RuntimeError(f"final checkpoint is missing: {final_path}")
    if not bool(model_card.get("completed_normally", False)):
        raise RuntimeError("the model card does not certify normal training completion")
    final_samples = int(model_card.get("samples_seen", 0))
    if final_samples <= int(records[-1]["samples_seen"]):
        raise RuntimeError(
            "final checkpoint sample clock does not follow the last global validation"
        )

    def from_record(
        role: str, rationale: str, record: Mapping[str, Any]
    ) -> CheckpointSelection:
        return CheckpointSelection(
            role=role,
            rationale=rationale,
            checkpoint=_record_checkpoint(record, checkpoint_root),
            samples_seen=int(record["samples_seen"]),
            recorded_sha256=str(record.get("checkpoint_sha256", "")),
            source_global_validation=record,
        )

    selected = (
        from_record(
            "first_global",
            "first immutable global-validation checkpoint",
            first,
        ),
        from_record(
            "best_trade_close",
            "lowest global-validation trade-close MAE; MCC breaks ties",
            best_close,
        ),
        from_record(
            "best_trade_range",
            "lowest global-validation trade high/low range MAE",
            best_range,
        ),
        CheckpointSelection(
            role="final_epoch",
            rationale="last immutable completed outer-epoch checkpoint",
            checkpoint=final_path,
            samples_seen=final_samples,
            recorded_sha256="",
        ),
    )
    paths = [item.checkpoint.resolve() for item in selected]
    if len(paths) != 4 or len(set(paths)) != 4:
        raise RuntimeError(
            "the selection policy did not produce four distinct checkpoints; "
            "choose explicit alternative criteria before evaluating"
        )
    return selected


def _resolve_authorities(
    args: argparse.Namespace, run_root: Path
) -> tuple[Path, Path, Path, dict[str, Any]]:
    run_manifest = _read_json(run_root / "run_manifest.json")
    recorded_args = run_manifest.get("args")
    if not isinstance(recorded_args, dict):
        raise RuntimeError("run manifest has no argument authority")
    manifest = Path(str(args.manifest or recorded_args.get("experiment_manifest", "")))
    shard_root = Path(str(args.shard_root or recorded_args.get("offline_shard_root", "")))
    output_root = Path(
        args.output_root
        or run_root / "checkpoint_comparison_full_validation_v1"
    )
    if not manifest.is_file():
        raise RuntimeError(f"validation manifest is missing: {manifest}")
    if not shard_root.is_dir():
        raise RuntimeError(f"offline shard root is missing: {shard_root}")
    return manifest, shard_root, output_root, run_manifest


def _verify_manifest(
    manifest_path: Path,
    *,
    expected_hash: str,
) -> tuple[str, int, int]:
    manifest = _read_json(manifest_path)
    recorded_hash = str(manifest.get("manifest_hash", ""))
    unsigned = dict(manifest)
    unsigned.pop("manifest_hash", None)
    actual_hash = _canonical_json_hash(unsigned)
    if not recorded_hash or actual_hash != recorded_hash:
        raise RuntimeError("validation manifest content does not match its recorded hash")
    if actual_hash != expected_hash:
        raise RuntimeError(
            "validation manifest differs from the authority used during global validation"
        )
    panels = manifest.get("panels")
    summaries = manifest.get("summaries")
    if not isinstance(panels, dict) or not isinstance(summaries, dict):
        raise RuntimeError("validation manifest has no panels or summaries")
    rows = panels.get("validation")
    summary = summaries.get("validation")
    if not isinstance(rows, list) or not rows or not isinstance(summary, dict):
        raise RuntimeError("validation authority is empty")
    origins = sum(int(row["origins"]) for row in rows)
    blocks = len(rows)
    if origins != int(summary.get("origins", -1)) or blocks != int(
        summary.get("blocks", -1)
    ):
        raise RuntimeError("validation panel membership and summary disagree")
    return actual_hash, origins, blocks


def evaluation_command(
    selection: CheckpointSelection,
    *,
    manifest: Path,
    shard_root: Path,
    output_root: Path,
    run_name: str,
    batch_size: int,
    loader_workers: int,
    wandb_project: str,
    wandb_entity: str,
    wandb_mode: str,
) -> list[str]:
    return [
        sys.executable,
        "-B",
        "-m",
        "research.bar_gpt.v3.evaluate_discovery_checkpoint",
        "--checkpoint",
        str(selection.checkpoint),
        "--experiment-manifest",
        str(manifest),
        "--offline-shard-root",
        str(shard_root),
        "--output-root",
        str(output_root / "runs"),
        "--run-name",
        run_name,
        "--panel",
        "validation",
        "--namespace",
        METRIC_NAMESPACE,
        "--architecture",
        selection.role,
        "--batch-size",
        str(batch_size),
        "--loader-workers",
        str(loader_workers),
        "--wandb-project",
        wandb_project,
        "--wandb-entity",
        wandb_entity,
        "--wandb-mode",
        wandb_mode,
    ]


def _summary_matches(
    summary: Mapping[str, Any],
    selection: CheckpointSelection,
    *,
    manifest_hash: str,
) -> bool:
    return bool(
        Path(str(summary.get("checkpoint", ""))).resolve()
        == selection.checkpoint.resolve()
        and str(summary.get("architecture", "")) == selection.role
        and int(summary.get("step", -1)) == selection.samples_seen
        and int(summary.get("checkpoint_size", -1))
        == selection.checkpoint.stat().st_size
        and int(summary.get("checkpoint_mtime_ns", -1))
        == selection.checkpoint.stat().st_mtime_ns
        and summary.get("panel") == "validation"
        and summary.get("namespace") == METRIC_NAMESPACE
        and summary.get("manifest_hash") == manifest_hash
    )


def _metric(summary: Mapping[str, Any], suffix: str) -> float:
    key = f"{METRIC_NAMESPACE}_{suffix}"
    if key not in summary:
        raise RuntimeError(f"evaluation summary is missing required metric {key}")
    return float(summary[key])


def _write_comparison(
    *,
    output_root: Path,
    selections: tuple[CheckpointSelection, ...],
    summaries: list[dict[str, Any]],
    checkpoint_hashes: Mapping[str, str],
    manifest: Path,
    manifest_hash: str,
    validation_origins: int,
    validation_blocks: int,
) -> None:
    by_role = {str(item["architecture"]): item for item in summaries}
    rows: list[dict[str, Any]] = []
    for selection in selections:
        summary = by_role[selection.role]
        rows.append(
            {
                "role": selection.role,
                "rationale": selection.rationale,
                "checkpoint": str(selection.checkpoint),
                "checkpoint_sha256": checkpoint_hashes[selection.role],
                "training_origins": int(summary["step"]),
                "validation_origins": validation_origins,
                "loss_total": _metric(summary, "loss/total"),
                "trade_close_mae_bps": _metric(
                    summary, "trade_close_summary/mae_bps_macro"
                ),
                "trade_range_mae_bps": _metric(
                    summary, "trade_range_summary/mae_bps_macro"
                ),
                "close_balanced_accuracy": _metric(
                    summary, "close_return_class_summary/balanced_accuracy_macro"
                ),
                "close_mcc": _metric(
                    summary, "close_return_class_summary/mcc_macro"
                ),
                "ar_close_balanced_accuracy": _metric(
                    summary, "ar_close_return_class_summary/balanced_accuracy_macro"
                ),
                "ar_close_mcc": _metric(
                    summary, "ar_close_return_class_summary/mcc_macro"
                ),
                "trade_calibration": _metric(
                    summary, "trade_summary/calibration_macro"
                ),
                "availability_brier": _metric(summary, "availability/brier_macro"),
                "time_to_event_accuracy": _metric(
                    summary, "ar_time_to_event_summary/accuracy_macro"
                ),
            }
        )
    ordered = sorted(
        rows,
        key=lambda item: (
            float(item["trade_close_mae_bps"]),
            -float(item["close_mcc"]),
        ),
    )
    payload = {
        "contract_version": COMPARISON_CONTRACT_VERSION,
        "selection_policy": {
            "first_global": "minimum global-validation sample clock",
            "best_trade_close": "minimum trade-close MAE with MCC tie-break",
            "best_trade_range": "minimum trade high/low range MAE with close-MAE tie-break",
            "final_epoch": "highest immutable outer-epoch checkpoint",
        },
        "validation_manifest": str(manifest),
        "validation_manifest_hash": manifest_hash,
        "validation_origins": validation_origins,
        "validation_blocks": validation_blocks,
        "ranking": "trade-close MAE ascending, then close MCC descending",
        "models": ordered,
        "full_evaluation_summaries": summaries,
    }
    _atomic_json(output_root / "comparison.json", payload)
    csv_path = output_root / "comparison.csv"
    temporary = csv_path.with_suffix(csv_path.suffix + f".tmp.{os.getpid()}")
    with temporary.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=tuple(ordered[0]))
        writer.writeheader()
        writer.writerows(ordered)
    os.replace(temporary, csv_path)

    print("\nComplete-panel checkpoint comparison", flush=True)
    print(
        "role               train(B)  closeMAE  rangeMAE  balAcc    MCC      "
        "AR-MCC   calibration  TTE-acc",
        flush=True,
    )
    for item in ordered:
        print(
            f"{str(item['role']):<18} "
            f"{int(item['training_origins']) / 1_000_000_000:>8.3f}  "
            f"{float(item['trade_close_mae_bps']):>8.3f}  "
            f"{float(item['trade_range_mae_bps']):>8.3f}  "
            f"{float(item['close_balanced_accuracy']):>7.4f}  "
            f"{float(item['close_mcc']):>7.4f}  "
            f"{float(item['ar_close_mcc']):>7.4f}  "
            f"{float(item['trade_calibration']):>11.4f}  "
            f"{float(item['time_to_event_accuracy']):>7.4f}",
            flush=True,
        )
    print(f"Comparison JSON: {output_root / 'comparison.json'}", flush=True)
    print(f"Comparison CSV:  {csv_path}", flush=True)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    if args.batch_size <= 0 or args.loader_workers < 0:
        raise ValueError("batch size must be positive and loader workers cannot be negative")
    run_root = Path(args.run_root)
    if not run_root.is_dir():
        raise RuntimeError(f"completed training run is missing: {run_root}")
    manifest, shard_root, output_root, run_manifest = _resolve_authorities(
        args, run_root
    )
    records = _load_global_validation_records(
        run_root / "artifacts" / "global_validation_runs.jsonl"
    )
    model_card = _read_json(run_root / "model_card.json")
    selections = select_checkpoints(
        run_root=run_root,
        records=records,
        model_card=model_card,
    )
    expected_manifest_hash = str(records[0]["experiment_manifest_hash"])
    manifest_hash, validation_origins, validation_blocks = _verify_manifest(
        manifest,
        expected_hash=expected_manifest_hash,
    )
    print(f"Completed run: {run_root}", flush=True)
    print(f"Source commit recorded by training: {run_manifest.get('git_commit', '')}", flush=True)
    print(f"Validation manifest: {manifest}", flush=True)
    print(
        f"Complete validation panel: {validation_origins:,} origins in "
        f"{validation_blocks:,} immutable blocks; hash={manifest_hash[:12]}",
        flush=True,
    )
    for index, selection in enumerate(selections, start=1):
        quality = (
            selection.source_global_validation.get("promotion_quality", {})
            if selection.source_global_validation is not None
            else {}
        )
        quality_text = (
            f" closeMAE={float(quality['trade_close_mae_bps']):.3f} "
            f"rangeMAE={float(quality['trade_range_mae_bps']):.3f} "
            f"MCC={float(quality['close_mcc']):.4f}"
            if quality
            else ""
        )
        print(
            f"[{index}/4] {selection.role}: origins={selection.samples_seen:,} "
            f"checkpoint={selection.checkpoint.name}; {selection.rationale};{quality_text}",
            flush=True,
        )
    if not args.execute:
        print("Preview complete. Add --execute to run the four sequential GPU evaluations.", flush=True)
        return 0

    output_root.mkdir(parents=True, exist_ok=True)
    checkpoint_hashes: dict[str, str] = {}
    summaries: list[dict[str, Any]] = []
    child_env = os.environ.copy()
    child_env["PYTHONDONTWRITEBYTECODE"] = "1"
    repository_root = Path(__file__).resolve().parents[3]
    for index, selection in enumerate(selections, start=1):
        print(
            f"[{index}/4] hashing {selection.role}: {selection.checkpoint.name}",
            flush=True,
        )
        actual_hash = _sha256(selection.checkpoint)
        if (
            selection.recorded_sha256
            and actual_hash != selection.recorded_sha256
        ):
            raise RuntimeError(
                f"checkpoint hash mismatch for {selection.role}: {selection.checkpoint}"
            )
        checkpoint_hashes[selection.role] = actual_hash
        run_name = f"{selection.role}-{actual_hash[:12]}"
        summary_path = output_root / "runs" / run_name / "summary.json"
        if not args.force and summary_path.is_file():
            summary = _read_json(summary_path)
            if _summary_matches(
                summary,
                selection,
                manifest_hash=manifest_hash,
            ):
                print(f"[{index}/4] verified cached result for {selection.role}", flush=True)
                summaries.append(summary)
                continue
        if args.force:
            run_name = f"{run_name}-r{time.strftime('%Y%m%d-%H%M%S')}"
            summary_path = output_root / "runs" / run_name / "summary.json"
        command = evaluation_command(
            selection,
            manifest=manifest,
            shard_root=shard_root,
            output_root=output_root,
            run_name=run_name,
            batch_size=int(args.batch_size),
            loader_workers=int(args.loader_workers),
            wandb_project=str(args.wandb_project),
            wandb_entity=str(args.wandb_entity),
            wandb_mode=str(args.wandb_mode),
        )
        print(f"[{index}/4] evaluating {selection.role}", flush=True)
        print("Command: " + " ".join(shlex.quote(value) for value in command), flush=True)
        completed = subprocess.run(
            command,
            cwd=str(repository_root),
            env=child_env,
            check=False,
        )
        if completed.returncode:
            raise RuntimeError(
                f"evaluation failed for {selection.role} with exit code "
                f"{completed.returncode}; rerun the same command to resume"
            )
        if not summary_path.is_file():
            raise RuntimeError(f"evaluation produced no summary: {summary_path}")
        summary = _read_json(summary_path)
        if not _summary_matches(
            summary,
            selection,
            manifest_hash=manifest_hash,
        ):
            raise RuntimeError(
                f"evaluation summary does not certify {selection.role}: {summary_path}"
            )
        summaries.append(summary)
    _write_comparison(
        output_root=output_root,
        selections=selections,
        summaries=summaries,
        checkpoint_hashes=checkpoint_hashes,
        manifest=manifest,
        manifest_hash=manifest_hash,
        validation_origins=validation_origins,
        validation_blocks=validation_blocks,
    )
    return 0


if __name__ == "__main__":
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    raise SystemExit(main())
