from __future__ import annotations

import argparse
import gc
import itertools
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import torch

from research.mlops.paths import default_run_root
from research.news_reaction_model.v20.config import (
    ExperimentConfig,
    LoaderConfig,
    ModelConfig,
)
from research.news_reaction_model.v20.data import PreparedEpisodeDataset
from research.news_reaction_model.v20.losses import compute_loss
from research.news_reaction_model.v20.model import NewsReactionModelV20
from research.news_reaction_model.v20.targets import fit_training_statistics
from research.news_reaction_model.v20 import (
    MODEL_CONTRACT_VERSION,
    MODEL_FAMILY,
    MODEL_VERSION,
)


PROFILE_CONTRACT_VERSION = "news_reaction_v20_size_profile_v2"
RESULTS_FILE = "profile_results.jsonl"
SUMMARY_FILE = "profile_summary.json"
MANIFEST_FILE = "profile_manifest.json"


def parse_ints(value: str) -> list[int]:
    return [int(item) for item in value.split(",") if item.strip()]


def attention_heads_for_width(width: int) -> int:
    for heads in (16, 12, 8, 6, 4, 2, 1):
        if width % heads == 0:
            return heads
    raise AssertionError(f"No compatible attention-head count for width {width}.")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def configuration_key(
    d_model: int,
    current_layers: int,
    experts: int,
    batch_size: int,
) -> tuple[int, int, int, int]:
    return d_model, current_layers, experts, batch_size


def row_key(row: dict[str, Any]) -> tuple[int, int, int, int]:
    return configuration_key(
        int(row["d_model"]),
        int(row["current_layers"]),
        int(row["experts"]),
        int(row["batch_size"]),
    )


def read_durable_results(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"Invalid profiler JSONL at {path}:{line_number}: {exc}"
                ) from exc
            if not isinstance(row, dict):
                raise RuntimeError(
                    f"Profiler JSONL row {line_number} is not an object."
                )
            rows.append(row)
    return rows


def latest_result_per_configuration(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    latest: dict[tuple[int, int, int, int], dict[str, Any]] = {}
    for row in rows:
        latest[row_key(row)] = row
    return list(latest.values())


def select_recommendations(
    rows: list[dict[str, Any]],
    defaults: ExperimentConfig,
    *,
    maximum_gpu_gib: float,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    latest_rows = latest_result_per_configuration(rows)
    viable = [
        row
        for row in latest_rows
        if row.get("status") == "completed"
        and float(row.get("peak_gpu_gib", float("inf"))) <= maximum_gpu_gib
    ]
    fastest_overall = (
        max(viable, key=lambda row: float(row["samples_per_second"]))
        if viable
        else None
    )
    fixed_rows = [
        row
        for row in viable
        if int(row["d_model"]) == defaults.model.d_model
        and int(row["current_layers"]) == defaults.model.current_layers
        and int(row["experts"]) == defaults.model.expert_count
    ]
    fixed_architecture = (
        max(fixed_rows, key=lambda row: float(row["samples_per_second"]))
        if fixed_rows
        else None
    )
    return fixed_architecture, fastest_overall


def build_summary(
    rows: list[dict[str, Any]],
    defaults: ExperimentConfig,
    *,
    expected_configurations: int,
    maximum_gpu_gib: float,
    run_dir: Path,
) -> dict[str, Any]:
    latest_rows = latest_result_per_configuration(rows)
    fixed, fastest = select_recommendations(
        latest_rows,
        defaults,
        maximum_gpu_gib=maximum_gpu_gib,
    )
    terminal_statuses = {"completed", "oom"}
    terminal_count = sum(
        row.get("status") in terminal_statuses for row in latest_rows
    )
    return {
        "profile_contract_version": PROFILE_CONTRACT_VERSION,
        "updated_at_utc": utc_now(),
        "run_dir": str(run_dir),
        "expected_configurations": expected_configurations,
        "terminal_configurations": terminal_count,
        "completed_configurations": sum(
            row.get("status") == "completed" for row in latest_rows
        ),
        "oom_configurations": sum(
            row.get("status") == "oom" for row in latest_rows
        ),
        "failed_configurations": sum(
            row.get("status") == "failed" for row in latest_rows
        ),
        "complete": terminal_count == expected_configurations,
        "maximum_gpu_gib": maximum_gpu_gib,
        "recommended_fixed_architecture": fixed,
        "fastest_overall": fastest,
        "recommendation_policy": (
            "Use recommended_fixed_architecture to tune batch size while preserving "
            "the checked V20 scientific architecture. fastest_overall is a throughput "
            "observation, not an automatic architecture recommendation."
        ),
    }


def create_run_dir(output_root: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = output_root / f"profile_{stamp}"
    suffix = 1
    while candidate.exists():
        candidate = output_root / f"profile_{stamp}_{suffix:02d}"
        suffix += 1
    candidate.mkdir(parents=True, exist_ok=False)
    return candidate


def manifest_contract(
    args: argparse.Namespace,
    loader: LoaderConfig,
    device: torch.device,
) -> dict[str, Any]:
    return {
        "profile_contract_version": PROFILE_CONTRACT_VERSION,
        "model_contract_version": MODEL_CONTRACT_VERSION,
        "model_sizes": parse_ints(args.model_sizes),
        "batch_sizes": parse_ints(args.batch_sizes),
        "current_layers": parse_ints(args.current_layers),
        "experts": parse_ints(args.experts),
        "maximum_gpu_gib": float(args.maximum_gpu_gib),
        "prepared_dataset_root": str(loader.prepared_dataset_root.resolve()),
        "v15_prepared_root": str(loader.v15_prepared_root.resolve()),
        "device_type": device.type,
        "device_name": (
            torch.cuda.get_device_name(device) if device.type == "cuda" else "cpu"
        ),
        "torch_version": torch.__version__,
        "cuda_version": torch.version.cuda,
    }


def main(argv: list[str] | None = None) -> int:
    defaults = ExperimentConfig()
    parser = argparse.ArgumentParser(
        description="Profile V20 over read-only V18/V15 arrays."
    )
    parser.add_argument("--model-sizes", default="512,768")
    parser.add_argument("--batch-sizes", default="512,1024,2048")
    parser.add_argument("--current-layers", default="4,6")
    parser.add_argument("--experts", default="4,6")
    parser.add_argument("--prepared-root", default="")
    parser.add_argument("--v15-root", default="")
    parser.add_argument(
        "--output-root",
        default=str(
            default_run_root(
                MODEL_FAMILY,
                MODEL_VERSION,
                "profile",
                "size-sweep",
            )
        ),
    )
    parser.add_argument(
        "--resume-run-dir",
        default="",
        help="Resume a specific profiler run directory after validating its manifest.",
    )
    parser.add_argument("--maximum-gpu-gib", type=float, default=90.0)
    args = parser.parse_args(argv)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    maximum_batch = max(parse_ints(args.batch_sizes))
    base_loader = LoaderConfig(batch_size=maximum_batch)
    if args.prepared_root:
        base_loader.prepared_dataset_root = type(base_loader.prepared_dataset_root)(
            args.prepared_root
        )
    if args.v15_root:
        base_loader.v15_prepared_root = type(base_loader.v15_prepared_root)(
            args.v15_root
        )
    run_dir = (
        Path(args.resume_run_dir)
        if args.resume_run_dir
        else create_run_dir(Path(args.output_root))
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    results_path = run_dir / RESULTS_FILE
    summary_path = run_dir / SUMMARY_FILE
    manifest_path = run_dir / MANIFEST_FILE
    contract = manifest_contract(args, base_loader, device)
    if args.resume_run_dir:
        if not manifest_path.exists():
            raise RuntimeError(
                f"Cannot resume profiler without {manifest_path}."
            )
        existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        existing_contract = existing_manifest.get("contract")
        if existing_contract != contract:
            raise RuntimeError(
                "Profiler resume manifest drift. Use the original arguments and "
                "environment or start a new run."
            )
    else:
        atomic_write_json(
            manifest_path,
            {
                "created_at_utc": utc_now(),
                "contract": contract,
                "command_arguments": vars(args),
            },
        )
    print(f"PROFILE_RUN_DIR {run_dir}", flush=True)
    print(f"PROFILE_RESULTS {results_path}", flush=True)
    durable_rows = read_durable_results(results_path)
    completed_keys = {
        row_key(row)
        for row in durable_rows
        if row.get("status") in {"completed", "oom"}
    }
    model_sizes = parse_ints(args.model_sizes)
    current_layer_values = parse_ints(args.current_layers)
    expert_values = parse_ints(args.experts)
    batch_sizes = parse_ints(args.batch_sizes)
    expected_configurations = (
        len(model_sizes)
        * len(current_layer_values)
        * len(expert_values)
        * len(batch_sizes)
    )
    atomic_write_json(
        summary_path,
        build_summary(
            durable_rows,
            defaults,
            expected_configurations=expected_configurations,
            maximum_gpu_gib=args.maximum_gpu_gib,
            run_dir=run_dir,
        ),
    )
    source = PreparedEpisodeDataset(
        base_loader,
        start=base_loader.train_start,
        end_exclusive=base_loader.train_end_exclusive,
    )
    try:
        statistics = fit_training_statistics(
            source,
            beta=defaults.train.effective_number_beta,
            minimum_class_weight=defaults.train.minimum_class_weight,
            maximum_class_weight=defaults.train.maximum_class_weight,
        )
        full_batch = next(source.iter_batches())
    finally:
        source.stop()
    results: list[dict[str, Any]] = list(durable_rows)
    combinations = itertools.product(
        model_sizes,
        current_layer_values,
        expert_values,
        batch_sizes,
    )
    for d_model, current_layers, experts, batch_size in combinations:
        key = configuration_key(d_model, current_layers, experts, batch_size)
        if key in completed_keys:
            print(
                "SKIP_COMPLETED "
                + json.dumps(
                    {
                        "d_model": d_model,
                        "current_layers": current_layers,
                        "experts": experts,
                        "batch_size": batch_size,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            continue
        model = None
        optimizer = None
        batch = None
        output = None
        loss = None
        try:
            heads = attention_heads_for_width(d_model)
            keep = min(batch_size, full_batch.sample_count)
            indices = torch.arange(keep)
            batch = type(full_batch)(
                x={
                    key: value[indices].to(device)
                    for key, value in full_batch.x.items()
                },
                direction=full_batch.direction[indices].to(device),
                path=full_batch.path[indices].to(device),
                flow=full_batch.flow[indices].to(device),
                regression_targets=full_batch.regression_targets[indices].to(device),
                target_mask=full_batch.target_mask[indices].to(device),
                identity={},
                sample_count=keep,
            )
            model = NewsReactionModelV20(
                ModelConfig(
                    d_model=d_model,
                    feedforward_dim=3 * d_model,
                    current_layers=current_layers,
                    prior_layers=max(1, current_layers // 2),
                    cross_attention_layers=2,
                    attention_heads=heads,
                    expert_count=experts,
                    expert_top_k=2,
                    expert_hidden_dim=2 * d_model,
                ),
                statistics,
            ).to(device)
            optimizer = torch.optim.AdamW(model.parameters(), lr=3e-4)
            if device.type == "cuda":
                torch.cuda.reset_peak_memory_stats()
                torch.cuda.synchronize()
            started = time.perf_counter()
            with torch.autocast(
                device_type=device.type,
                dtype=torch.bfloat16,
                enabled=device.type == "cuda",
            ):
                output = model(batch.x)
                loss = compute_loss(output, batch, statistics).loss
            loss.backward()
            optimizer.step()
            if device.type == "cuda":
                torch.cuda.synchronize()
            elapsed = time.perf_counter() - started
            row = {
                "status": "completed",
                "completed_at_utc": utc_now(),
                "d_model": d_model,
                "attention_heads": heads,
                "current_layers": current_layers,
                "prior_layers": max(1, current_layers // 2),
                "experts": experts,
                "batch_size": keep,
                "seconds": elapsed,
                "samples_per_second": keep / elapsed,
                "parameters": sum(
                    parameter.numel() for parameter in model.parameters()
                ),
                "peak_gpu_gib": (
                    torch.cuda.max_memory_allocated() / 2**30
                    if device.type == "cuda"
                    else 0.0
                ),
            }
            print(json.dumps(row, sort_keys=True), flush=True)
            append_jsonl(results_path, row)
            results.append(row)
        except torch.OutOfMemoryError:
            row = {
                "status": "oom",
                "completed_at_utc": utc_now(),
                "d_model": d_model,
                "current_layers": current_layers,
                "experts": experts,
                "batch_size": batch_size,
            }
            print(json.dumps(row, sort_keys=True), flush=True)
            append_jsonl(results_path, row)
            results.append(row)
        except Exception as exc:
            row = {
                "status": "failed",
                "completed_at_utc": utc_now(),
                "d_model": d_model,
                "current_layers": current_layers,
                "experts": experts,
                "batch_size": batch_size,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            append_jsonl(results_path, row)
            results.append(row)
            atomic_write_json(
                summary_path,
                build_summary(
                    results,
                    defaults,
                    expected_configurations=expected_configurations,
                    maximum_gpu_gib=args.maximum_gpu_gib,
                    run_dir=run_dir,
                ),
            )
            raise
        finally:
            model = None
            optimizer = None
            batch = None
            output = None
            loss = None
            gc.collect()
            if device.type == "cuda":
                torch.cuda.empty_cache()
        atomic_write_json(
            summary_path,
            build_summary(
                results,
                defaults,
                expected_configurations=expected_configurations,
                maximum_gpu_gib=args.maximum_gpu_gib,
                run_dir=run_dir,
            ),
        )
    fixed, fastest = select_recommendations(
        results,
        defaults,
        maximum_gpu_gib=args.maximum_gpu_gib,
    )
    if fixed is not None:
        print(
            "RECOMMENDED_FIXED_ARCHITECTURE "
            + json.dumps(fixed, sort_keys=True),
            flush=True,
        )
    if fastest is not None:
        print(
            "FASTEST_OVERALL " + json.dumps(fastest, sort_keys=True),
            flush=True,
        )
    print(f"PROFILE_SUMMARY {summary_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
