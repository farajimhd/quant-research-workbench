from __future__ import annotations

import argparse
import csv
import json
import math
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = next((parent for parent in Path(__file__).resolve().parents if (parent / "research").exists()), Path(__file__).resolve().parents[3])
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.masked_event_model.v11.config import TrainConfig  # noqa: E402
from research.masked_event_model.v11.train import JOB_TYPE, MODEL_FAMILY, MODEL_VERSION  # noqa: E402
from research.mlops.paths import MLOpsPathConfig, default_run_root  # noqa: E402


MODEL_SIZES: dict[str, dict[str, Any]] = {
    "tiny_d128": {
        "d_byte": 24,
        "d_model": 128,
        "n_heads": 4,
        "encoder_layers": 6,
        "decoder_layers": 2,
        "ffn_mult": 4,
        "dropout": 0.08,
    },
    "small_plus": {
        "d_byte": 32,
        "d_model": 192,
        "n_heads": 6,
        "encoder_layers": 8,
        "decoder_layers": 3,
        "ffn_mult": 4,
        "dropout": 0.08,
    },
    "medium": {
        "d_byte": 40,
        "d_model": 256,
        "n_heads": 8,
        "encoder_layers": 10,
        "decoder_layers": 4,
        "ffn_mult": 4,
        "dropout": 0.08,
    },
    "medium_plus": {
        "d_byte": 48,
        "d_model": 320,
        "n_heads": 8,
        "encoder_layers": 12,
        "decoder_layers": 4,
        "ffn_mult": 4,
        "dropout": 0.08,
    },
    "large": {
        "d_byte": 64,
        "d_model": 384,
        "n_heads": 12,
        "encoder_layers": 12,
        "decoder_layers": 5,
        "ffn_mult": 4,
        "dropout": 0.08,
    },
    "high": {
        "d_byte": 64,
        "d_model": 384,
        "n_heads": 12,
        "encoder_layers": 12,
        "decoder_layers": 5,
        "ffn_mult": 4,
        "dropout": 0.08,
    },
}

PRACTICAL_PROFILE_RUNS: tuple[tuple[str, int, int] | tuple[str, int, int, str] | tuple[str, int, int, str, int], ...] = (
    ("medium", 32, 4096),
    ("medium", 32, 8192),
    ("large", 32, 1024),
    ("large", 32, 2048),
    ("large", 32, 4096),
)


SUMMARY_FIELDS = [
    "run_name",
    "status",
    "model_size",
    "input_representation",
    "decoder_chunk_size",
    "embedding_dim",
    "batch_size",
    "parameters",
    "steps",
    "samples",
    "subprocess_seconds",
    "epoch_seconds",
    "samples_per_second",
    "last_loss",
    "last_event_bit_acc_pct",
    "last_event_bit_majority_baseline_pct",
    "last_event_bit_acc_lift_pct",
    "last_event_balanced_bit_acc_pct",
    "last_event_zero_bit_acc_pct",
    "last_event_one_bit_acc_pct",
    "last_event_target_one_rate_pct",
    "last_event_pred_one_rate_pct",
    "last_event_byte_exact_acc_pct",
    "last_event_byte_mode_baseline_pct",
    "last_event_byte_exact_lift_pct",
    "last_event_soft_byte_psnr_db",
    "last_event_hard_byte_psnr_db",
    "last_inference_encode_seconds",
    "last_inference_encode_ms_per_sample",
    "mean_last10_step_seconds",
    "mean_last10_data_wait_seconds",
    "mean_last10_forward_loss_seconds",
    "mean_last10_metrics_seconds",
    "mean_last10_header_metrics_seconds",
    "mean_last10_event_metrics_seconds",
    "mean_last10_backward_seconds",
    "mean_last10_decoder_backward_seconds",
    "mean_last10_encoder_backward_seconds",
    "mean_last10_optimizer_seconds",
    "last_gpu_peak_allocated_gib",
    "last_gpu_reserved_gib",
    "last_process_rss_gib",
    "run_root",
    "error",
]


@dataclass(frozen=True, slots=True)
class SweepRun:
    index: int
    total: int
    model_size: str
    input_representation: str
    decoder_chunk_size: int
    embedding_dim: int
    event_embedding_features: int
    decoder_bottleneck_tokens: int
    batch_size: int
    run_name: str
    run_root: Path
    model_config: dict[str, Any]


def parse_args() -> argparse.Namespace:
    train_defaults = TrainConfig()
    parser = argparse.ArgumentParser(description="Sequentially profile v11 medium/large model sizes with 32-dim embeddings.")
    parser.add_argument("--cache-root", default=r"D:\market-data\prepared\event_sample_cache")
    parser.add_argument("--sweep-output-root", default="")
    parser.add_argument("--run-prefix", default="v11-eventmae-size-sweep")
    parser.add_argument("--profile-set", choices=("practical", "grid"), default="practical")
    parser.add_argument("--steps", type=int, default=200)
    parser.add_argument("--embedding-dims", default="32")
    parser.add_argument("--event-embedding-features", type=int, default=1)
    parser.add_argument("--decoder-bottleneck-tokens", type=int, default=40)
    parser.add_argument("--batch-sizes", default="4096,8192")
    parser.add_argument("--model-sizes", default="medium,large")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--decoder-chunk-size", type=int, default=0)
    parser.add_argument("--learning-rate", type=float, default=train_defaults.learning_rate)
    parser.add_argument("--scheduler-t0-steps", type=int, default=0)
    parser.add_argument("--wandb-mode", choices=("auto", "online", "offline", "disabled"), default="online")
    parser.add_argument("--wandb-project", default=train_defaults.wandb_project)
    parser.add_argument("--wandb-entity", default=train_defaults.wandb_entity)
    parser.add_argument("--trainer-progress-layout", choices=("auto", "rich", "text", "none"), default="text")
    parser.add_argument("--skip-completed", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--fresh-start", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--print-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.scheduler_t0_steps <= 0:
        args.scheduler_t0_steps = args.steps
    embedding_dims = parse_int_list(args.embedding_dims, "--embedding-dims")
    batch_sizes = parse_int_list(args.batch_sizes, "--batch-sizes")
    selected_sizes = parse_model_sizes(args.model_sizes)
    runs = build_runs(args, selected_sizes, embedding_dims, batch_sizes)
    used_model_sizes = [name for name in MODEL_SIZES if any(run.model_size == name for run in runs)]
    sweep_root = resolve_sweep_root(args)
    sweep_root.mkdir(parents=True, exist_ok=True)
    config_path = sweep_root / "sweep_config.json"
    results_jsonl = sweep_root / "sweep_results.jsonl"
    results_csv = sweep_root / "sweep_results.csv"
    config_path.write_text(
        json.dumps(
            {
                "args": vars(args),
                "model_sizes": {name: MODEL_SIZES[name] for name in used_model_sizes},
                "practical_profile_runs": PRACTICAL_PROFILE_RUNS,
                "run_count": len(runs),
                "runs": [run_to_dict(run) for run in runs],
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    print("=" * 96, flush=True)
    print("v11 event-token MAE model-size profile sweep", flush=True)
    print(f"runs={len(runs)} steps={args.steps} profile_set={args.profile_set} embedding_dims={embedding_dims} batch_sizes={batch_sizes}", flush=True)
    print(f"model_sizes={used_model_sizes}", flush=True)
    print(f"sweep_root={sweep_root}", flush=True)
    print(f"results_jsonl={results_jsonl}", flush=True)
    print(f"results_csv={results_csv}", flush=True)
    print("=" * 96, flush=True)

    if args.print_only:
        for run in runs:
            print(" ".join(build_train_command(args, run)), flush=True)
        return

    completed: list[dict[str, Any]] = []
    started_at = time.perf_counter()
    for run in runs:
        if should_skip(args, run, results_jsonl):
            result = summarize_run(run, subprocess_seconds=0.0, status="skipped", error="")
            write_result(results_jsonl, result)
            completed.append(result)
            print_run_summary(run, result, started_at, completed, len(runs))
            continue
        if args.dry_run:
            result = {"status": "dry_run", **run_to_dict(run), "run_root": str(run.run_root)}
            write_result(results_jsonl, result)
            completed.append(result)
            print(f"DRY RUN [{run.index}/{run.total}] {run.run_name}", flush=True)
            print(" ".join(build_train_command(args, run)), flush=True)
            continue
        result = execute_run(args, run, sweep_root)
        write_result(results_jsonl, result)
        completed.append(result)
        write_csv(results_csv, collect_latest_results(results_jsonl))
        print_run_summary(run, result, started_at, completed, len(runs))
        if result["status"] != "ok":
            print(f"Stopping sweep after failed run {run.run_name}. Fix the issue or rerun with completed runs skipped.", flush=True)
            break
    write_csv(results_csv, collect_latest_results(results_jsonl))
    print("=" * 96, flush=True)
    print(f"Sweep finished. Results: {results_csv}", flush=True)


def parse_int_list(raw: str, flag: str) -> list[int]:
    values: list[int] = []
    for part in raw.split(","):
        text = part.strip()
        if not text:
            continue
        try:
            values.append(int(text))
        except ValueError as exc:
            raise SystemExit(f"{flag} must be comma-separated integers, got {text!r}") from exc
    if not values:
        raise SystemExit(f"{flag} cannot be empty")
    return values


def parse_model_sizes(raw: str) -> list[str]:
    names = [part.strip() for part in raw.split(",") if part.strip()]
    unknown = [name for name in names if name not in MODEL_SIZES]
    if unknown:
        raise SystemExit(f"Unknown model size(s): {unknown}. Available: {list(MODEL_SIZES)}")
    if not names:
        raise SystemExit("--model-sizes cannot be empty")
    return names


def build_runs(args: argparse.Namespace, model_sizes: list[str], embedding_dims: list[int], batch_sizes: list[int]) -> list[SweepRun]:
    if args.profile_set == "practical":
        return build_explicit_runs(args, PRACTICAL_PROFILE_RUNS)
    return build_grid_runs(args, model_sizes, embedding_dims, batch_sizes)


def normalize_combo(combo: tuple[str, int, int] | tuple[str, int, int, str] | tuple[str, int, int, str, int], default_decoder_chunk_size: int) -> tuple[str, int, int, str, int]:
    if len(combo) == 3:
        model_size, embedding_dim, batch_size = combo
        return model_size, embedding_dim, batch_size, "bit", 0
    if len(combo) == 4:
        model_size, embedding_dim, batch_size, input_representation = combo
        decoder_chunk_size = default_decoder_chunk_size
    else:
        model_size, embedding_dim, batch_size, input_representation, decoder_chunk_size = combo
    if input_representation != "bit":
        raise SystemExit(f"Unsupported input representation in sweep combo: {input_representation!r}")
    return model_size, embedding_dim, batch_size, input_representation, 0


def build_explicit_runs(args: argparse.Namespace, combos: tuple[tuple[str, int, int] | tuple[str, int, int, str] | tuple[str, int, int, str, int], ...]) -> list[SweepRun]:
    runs: list[SweepRun] = []
    total = len(combos)
    for index, combo in enumerate(combos, start=1):
        model_size, embedding_dim, batch_size, input_representation, decoder_chunk_size = normalize_combo(combo, args.decoder_chunk_size)
        representation_suffix = "-eventmae-bit"
        decoder_suffix = ""
        run_name = f"{args.run_prefix}-{model_size}{representation_suffix}{decoder_suffix}-emb{embedding_dim}-f{args.event_embedding_features}-t{args.decoder_bottleneck_tokens}-bs{batch_size}"
        runs.append(
            SweepRun(
                index=index,
                total=total,
                model_size=model_size,
                input_representation=input_representation,
                decoder_chunk_size=decoder_chunk_size,
                embedding_dim=embedding_dim,
                event_embedding_features=int(args.event_embedding_features),
                decoder_bottleneck_tokens=int(args.decoder_bottleneck_tokens),
                batch_size=batch_size,
                run_name=run_name,
                run_root=resolve_run_root(args, run_name),
                model_config=MODEL_SIZES[model_size],
            )
        )
    return runs


def build_grid_runs(args: argparse.Namespace, model_sizes: list[str], embedding_dims: list[int], batch_sizes: list[int]) -> list[SweepRun]:
    runs: list[SweepRun] = []
    total = len(model_sizes) * len(embedding_dims) * len(batch_sizes)
    index = 0
    for model_size in model_sizes:
        for embedding_dim in embedding_dims:
            for batch_size in batch_sizes:
                index += 1
                run_name = f"{args.run_prefix}-{model_size}-emb{embedding_dim}-f{args.event_embedding_features}-t{args.decoder_bottleneck_tokens}-bs{batch_size}"
                run_root = resolve_run_root(args, run_name)
                runs.append(
                    SweepRun(
                        index=index,
                        total=total,
                        model_size=model_size,
                        input_representation="bit",
                        decoder_chunk_size=0,
                        embedding_dim=embedding_dim,
                        event_embedding_features=int(args.event_embedding_features),
                        decoder_bottleneck_tokens=int(args.decoder_bottleneck_tokens),
                        batch_size=batch_size,
                        run_name=run_name,
                        run_root=run_root,
                        model_config=MODEL_SIZES[model_size],
                    )
                )
    return runs


def resolve_sweep_root(args: argparse.Namespace) -> Path:
    if args.sweep_output_root:
        return Path(args.sweep_output_root)
    return MLOpsPathConfig.from_env().runtimes_root / MODEL_FAMILY / MODEL_VERSION / JOB_TYPE / f"{args.run_prefix}-summary"


def resolve_run_root(args: argparse.Namespace, run_name: str) -> Path:
    if args.sweep_output_root:
        return Path(args.sweep_output_root) / "runs" / run_name
    return default_run_root(MODEL_FAMILY, MODEL_VERSION, JOB_TYPE, run_name)


def run_to_dict(run: SweepRun) -> dict[str, Any]:
    return {
        "index": run.index,
        "total": run.total,
        "model_size": run.model_size,
        "input_representation": run.input_representation,
        "decoder_chunk_size": run.decoder_chunk_size,
        "embedding_dim": run.embedding_dim,
        "event_embedding_features": run.event_embedding_features,
        "decoder_bottleneck_tokens": run.decoder_bottleneck_tokens,
        "batch_size": run.batch_size,
        "run_name": run.run_name,
        "run_root": str(run.run_root),
        **{f"model/{key}": value for key, value in run.model_config.items()},
    }


def should_skip(args: argparse.Namespace, run: SweepRun, results_jsonl: Path) -> bool:
    if not args.skip_completed:
        return False
    if not (run.run_root / "metrics.jsonl").exists():
        return False
    summary = summarize_run(run, subprocess_seconds=0.0, status="ok", error="")
    return int(summary.get("steps") or 0) >= args.steps


def execute_run(args: argparse.Namespace, run: SweepRun, sweep_root: Path) -> dict[str, Any]:
    command = build_train_command(args, run)
    # Keep the parent sweep log outside the child run directory. The child
    # trainer may remove and recreate run_root on --fresh-start, and Windows
    # will fail that cleanup if this process holds a file handle inside it.
    log_path = sweep_root / "logs" / f"{run.run_name}_subprocess.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    print("=" * 96, flush=True)
    print(f"RUN START [{run.index}/{run.total}] {run.run_name}", flush=True)
    print(f"run_root={run.run_root}", flush=True)
    print(" ".join(command), flush=True)
    started = time.perf_counter()
    with log_path.open("w", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            command,
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        assert process.stdout is not None
        for line in process.stdout:
            log_file.write(line)
            log_file.flush()
            text = line.rstrip()
            if should_echo_trainer_line(text):
                print(f"[{run.index:02d}/{run.total:02d}] {text}", flush=True)
        return_code = process.wait()
    elapsed = time.perf_counter() - started
    if return_code != 0:
        error = f"subprocess exited with code {return_code}; see {log_path}"
        return summarize_run(run, subprocess_seconds=elapsed, status="failed", error=error)
    return summarize_run(run, subprocess_seconds=elapsed, status="ok", error="")


def build_train_command(args: argparse.Namespace, run: SweepRun) -> list[str]:
    cfg = run.model_config
    command = [
        sys.executable,
        "-u",
        str(REPO_ROOT / "research" / "masked_event_model" / "v11" / "train.py"),
        "--data-source",
        "sample_cache",
        "--sample-cache-root",
        args.cache_root,
        "--sample-cache-prefetch-shards",
        "1",
        "--sample-cache-shuffle-records",
        "--sample-cache-drop-last",
        "--max-index-files",
        "1",
        "--batch-size",
        str(run.batch_size),
        "--max-steps",
        str(args.steps),
        "--epochs",
        "1",
        "--pretrain-validation-frequency",
        "0",
        "--pretrain-validation-steps",
        "0",
        "--logging-steps",
        "1",
        "--detailed-metrics-steps",
        "1",
        "--profile-first-steps",
        str(args.steps),
        "--profile-training-every-steps",
        "0",
        "--profile-inference-every-steps",
        "1",
        "--decoder-chunk-size",
        str(run.decoder_chunk_size),
        "--checkpoint-latest-steps",
        "0",
        "--checkpoint-archive-steps",
        "0",
        "--no-checkpoint-best-train",
        "--no-checkpoint-best-val",
        "--device",
        args.device,
        "--learning-rate",
        str(args.learning_rate),
        "--scheduler",
        "cosine_warm_restarts",
        "--scheduler-t0-steps",
        str(args.scheduler_t0_steps),
        "--wandb-mode",
        args.wandb_mode,
        "--wandb-project",
        args.wandb_project,
        "--wandb-entity",
        args.wandb_entity,
        "--wandb-run-name",
        run.run_name,
        "--run-root",
        str(run.run_root),
        "--input-representation",
        run.input_representation,
        "--progress-layout",
        args.trainer_progress_layout,
        "--embedding-dim",
        str(run.embedding_dim),
        "--event-embedding-features",
        str(run.event_embedding_features),
        "--decoder-bottleneck-tokens",
        str(run.decoder_bottleneck_tokens),
        "--d-byte",
        str(cfg["d_byte"]),
        "--d-model",
        str(cfg["d_model"]),
        "--n-heads",
        str(cfg["n_heads"]),
        "--encoder-layers",
        str(cfg["encoder_layers"]),
        "--decoder-layers",
        str(cfg["decoder_layers"]),
        "--ffn-mult",
        str(cfg["ffn_mult"]),
        "--dropout",
        str(cfg["dropout"]),
    ]
    if args.fresh_start:
        command.append("--fresh-start")
    return command


def should_echo_trainer_line(line: str) -> bool:
    if not line:
        return False
    prefixes = (
        "Output directory:",
        "Device:",
        "Model parameters:",
        "EPOCH",
        "FATAL",
        "WARN",
        "Training started.",
        "Sample-cache training",
        "CACHE ",
        "W&B run:",
        "wandb:",
    )
    if line.startswith(prefixes):
        return True
    return "step=" in line or "loss" in line or "Saved checkpoint" in line


def summarize_run(run: SweepRun, *, subprocess_seconds: float, status: str, error: str) -> dict[str, Any]:
    metrics_path = run.run_root / "metrics.jsonl"
    config_path = run.run_root / "artifacts" / "model" / "model_details.json"
    rows = read_metrics(metrics_path)
    train_rows = [row for row in rows if "pretrain/loss_total" in row]
    epoch_rows = [row for row in rows if "train/epoch_loss_mean" in row]
    last = train_rows[-1] if train_rows else {}
    tail = train_rows[-10:]
    model_parameters = read_parameter_count(config_path)
    epoch_seconds = float(epoch_rows[-1].get("train/epoch_seconds", 0.0)) if epoch_rows else 0.0
    samples = int(epoch_rows[-1].get("train/epoch_samples", 0)) if epoch_rows else int(len(train_rows) * run.batch_size)
    samples_per_second = samples / epoch_seconds if epoch_seconds > 0 else 0.0
    result = {
        "run_name": run.run_name,
        "status": status,
        "model_size": run.model_size,
        "input_representation": run.input_representation,
        "decoder_chunk_size": run.decoder_chunk_size,
        "embedding_dim": run.embedding_dim,
        "event_embedding_features": run.event_embedding_features,
        "decoder_bottleneck_tokens": run.decoder_bottleneck_tokens,
        "batch_size": run.batch_size,
        "parameters": model_parameters,
        "steps": len(train_rows),
        "samples": samples,
        "subprocess_seconds": subprocess_seconds,
        "epoch_seconds": epoch_seconds,
        "samples_per_second": samples_per_second,
        "last_loss": last_float(last, "pretrain/loss_total"),
        "last_event_bit_acc_pct": last_float(last, "pretrain/event_bit_acc_pct"),
        "last_event_bit_majority_baseline_pct": last_float(last, "pretrain/event_bit_majority_baseline_pct"),
        "last_event_bit_acc_lift_pct": last_float(last, "pretrain/event_bit_acc_lift_pct"),
        "last_event_balanced_bit_acc_pct": last_float(last, "pretrain/event_balanced_bit_acc_pct"),
        "last_event_zero_bit_acc_pct": last_float(last, "pretrain/event_zero_bit_acc_pct"),
        "last_event_one_bit_acc_pct": last_float(last, "pretrain/event_one_bit_acc_pct"),
        "last_event_target_one_rate_pct": last_float(last, "pretrain/event_target_one_rate_pct"),
        "last_event_pred_one_rate_pct": last_float(last, "pretrain/event_pred_one_rate_pct"),
        "last_event_byte_exact_acc_pct": last_float(last, "pretrain/event_byte_exact_acc_pct"),
        "last_event_byte_mode_baseline_pct": last_float(last, "pretrain/event_byte_mode_baseline_pct"),
        "last_event_byte_exact_lift_pct": last_float(last, "pretrain/event_byte_exact_lift_pct"),
        "last_event_soft_byte_psnr_db": last_float(last, "pretrain/event_soft_byte_psnr_db"),
        "last_event_hard_byte_psnr_db": last_float(last, "pretrain/event_hard_byte_psnr_db"),
        "last_inference_encode_seconds": last_float(last, "profile/inference_encode_seconds"),
        "last_inference_encode_ms_per_sample": last_float(last, "profile/inference_encode_ms_per_sample"),
        "mean_last10_step_seconds": mean_metric(tail, "train/step_seconds"),
        "mean_last10_data_wait_seconds": mean_metric(tail, "profile/data_wait_seconds"),
        "mean_last10_forward_loss_seconds": mean_metric(tail, "profile/forward_loss_seconds"),
        "mean_last10_metrics_seconds": mean_metric(tail, "profile/metrics_seconds"),
        "mean_last10_header_metrics_seconds": mean_metric(tail, "profile/header_metrics_seconds"),
        "mean_last10_event_metrics_seconds": mean_metric(tail, "profile/event_metrics_seconds"),
        "mean_last10_backward_seconds": mean_metric(tail, "profile/backward_seconds"),
        "mean_last10_decoder_backward_seconds": mean_metric(tail, "profile/decoder_backward_seconds"),
        "mean_last10_encoder_backward_seconds": mean_metric(tail, "profile/encoder_backward_seconds"),
        "mean_last10_optimizer_seconds": mean_metric(tail, "profile/optimizer_seconds"),
        "last_gpu_peak_allocated_gib": last_float(last, "profile/gpu_peak_allocated_gib"),
        "last_gpu_reserved_gib": last_float(last, "profile/gpu_reserved_gib"),
        "last_process_rss_gib": last_float(last, "profile/process_rss_gib"),
        "run_root": str(run.run_root),
        "error": error,
    }
    if not train_rows and status == "ok":
        result["status"] = "failed"
        result["error"] = f"no training metrics found at {metrics_path}"
    return result


def read_metrics(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def read_parameter_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return 0
    if isinstance(payload.get("total_params"), int):
        return int(payload["total_params"])
    return int(payload.get("parameters", {}).get("total", 0))


def last_float(row: dict[str, Any], key: str) -> float:
    value = row.get(key)
    return float(value) if isinstance(value, (int, float)) and math.isfinite(float(value)) else 0.0


def mean_metric(rows: list[dict[str, Any]], key: str) -> float:
    values = [float(row[key]) for row in rows if isinstance(row.get(key), (int, float)) and math.isfinite(float(row[key]))]
    return sum(values) / len(values) if values else 0.0


def write_result(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(result, default=str) + "\n")


def collect_latest_results(path: Path) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            run_name = str(row.get("run_name", ""))
            if run_name:
                latest[run_name] = row
    return list(latest.values())


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def print_run_summary(
    run: SweepRun,
    result: dict[str, Any],
    started_at: float,
    completed: list[dict[str, Any]],
    total: int,
) -> None:
    ok = [item for item in completed if item.get("status") in {"ok", "skipped"}]
    elapsed = time.perf_counter() - started_at
    avg = elapsed / max(1, len(completed))
    remaining = total - len(completed)
    eta_minutes = remaining * avg / 60.0
    print("-" * 96, flush=True)
    print(
        f"RUN END [{run.index}/{run.total}] {run.run_name} status={result.get('status')} "
        f"params={int(result.get('parameters') or 0):,} "
        f"samples/sec={float(result.get('samples_per_second') or 0.0):,.1f} "
        f"loss={float(result.get('last_loss') or 0.0):.5f} "
        f"gpu_peak={float(result.get('last_gpu_peak_allocated_gib') or 0.0):.1f}GiB",
        flush=True,
    )
    print(f"progress={len(completed)}/{total} ok_or_skipped={len(ok)} elapsed_min={elapsed/60:.1f} eta_min={eta_minutes:.1f}", flush=True)
    if result.get("error"):
        print(f"error={result['error']}", flush=True)


if __name__ == "__main__":
    main()
