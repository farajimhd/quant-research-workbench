from __future__ import annotations

import argparse
import csv
import dataclasses
import gc
import json
import math
import random
import sys
import time
import traceback
from contextlib import nullcontext
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import torch

try:
    import psutil
except ImportError:  # pragma: no cover - optional workstation dependency.
    psutil = None

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.rolling_loader.daily_index_dataset import AsyncDailyIndexBatchLoader
from research.temporal_event_model.v3.config import LoaderConfig, ModelConfig, DEFAULT_DATA_GROUPS
from research.temporal_event_model.v3.data import TemporalBatch, batch_to_torch, loader_config_from_v3, make_dummy_temporal_batch
from research.temporal_event_model.v3.losses import compute_loss
from research.temporal_event_model.v3.model import TemporalEventModelV3
from research.temporal_event_model.v3.run_sweep_training_profile import DEFAULT_MODEL_BATCH_GRID, MODEL_PRESETS


DEFAULT_CACHE_ROOT = Path("D:/market-data/prepared/daily_index_streaming_cache/events_daily_index_2019-02")
DEFAULT_OUTPUT_ROOT = Path("D:/TradingML/runtimes/temporal_event_model/v3/cached_batch_grid_profiles")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Profile v3 loader and model/batch-size combinations by loading a small fixed "
            "set of real batches once, then reusing them for every model profile run."
        )
    )
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_CACHE_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-name", default="")
    parser.add_argument("--months", default="2019-02")
    parser.add_argument("--start-utc", default="")
    parser.add_argument("--end-utc", default="")
    parser.add_argument("--tickers", default="")
    parser.add_argument("--model-batch-grid", default=DEFAULT_MODEL_BATCH_GRID)
    parser.add_argument("--models", default="", help="Comma-separated fallback preset list when --model-batch-grid is empty.")
    parser.add_argument("--batch-sizes", default="64,128,256", help="Comma-separated fallback batch sizes when --model-batch-grid is empty.")
    parser.add_argument("--max-runs", type=int, default=0)
    parser.add_argument("--raw-batches", type=int, default=2)
    parser.add_argument("--warmup-steps", type=int, default=1)
    parser.add_argument("--measure-steps", type=int, default=2)
    parser.add_argument("--detail-profile-steps", type=int, default=2)
    parser.add_argument("--profile-production-steps", type=int, default=1)
    parser.add_argument("--read-workers", type=int, default=8)
    parser.add_argument("--materialize-workers", type=int, default=16)
    parser.add_argument("--loaded-parts-per-group", type=int, default=256)
    parser.add_argument("--materialize-chunk-size", type=int, default=0)
    parser.add_argument("--prefetch-batches", type=int, default=10)
    parser.add_argument("--scanner-index-cache-entries", type=int, default=64)
    parser.add_argument("--scanner-prefetch-workers", type=int, default=8)
    parser.add_argument("--max-origins-per-epoch", type=int, default=200_000)
    parser.add_argument("--time-window-seconds", type=float, default=60.0)
    parser.add_argument("--ticker-cache-capacity", type=int, default=15_000)
    parser.add_argument("--origin-cursor-chunk-rows", type=int, default=1024)
    parser.add_argument("--chronological-replay", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--warm-all-ticker-caches", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--prefetch-scanner-indexes", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--shuffle-parts", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--shuffle-within-loaded-group", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--dummy-data", action="store_true", help="Use synthetic in-memory batches for a local smoke test.")
    parser.add_argument("--device", default="auto", choices=("auto", "cpu", "cuda"))
    parser.add_argument("--amp", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--amp-dtype", default="bf16", choices=("bf16", "fp16", "float16", "fp32", "none"))
    parser.add_argument("--compile-model", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--event-encoder-type", choices=("latent", "transformer"), default=ModelConfig().event_encoder_type)
    parser.add_argument("--event-item-dim", type=int, default=ModelConfig().event_item_dim)
    parser.add_argument("--event-latents", type=int, default=ModelConfig().event_latents)
    parser.add_argument("--event-latent-layers", type=int, default=ModelConfig().event_latent_layers)
    parser.add_argument("--event-latent-heads", type=int, default=ModelConfig().event_latent_heads)
    parser.add_argument("--learning-rate", type=float, default=3e-4)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--grad-clip-norm", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--continue-on-error", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    random.seed(int(args.seed))
    np.random.seed(int(args.seed))
    torch.manual_seed(int(args.seed))
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(int(args.seed))

    grid = _model_batch_grid(str(args.model_batch_grid), fallback_models=str(args.models), fallback_batch_sizes=str(args.batch_sizes))
    if not grid:
        raise SystemExit("Model/batch grid is empty.")
    max_batch_size = max(size for _, sizes in grid for size in sizes)
    device = _resolve_device(str(args.device))
    run_dir = _run_dir(args)
    run_dir.mkdir(parents=True, exist_ok=True)
    events_path = run_dir / "cached_batch_grid_profile.jsonl"
    summary_csv_path = run_dir / "cached_batch_grid_summary.csv"
    summary_json_path = run_dir / "cached_batch_grid_summary.json"
    config_path = run_dir / "profile_config.json"
    shapes_path = run_dir / "cached_batch_shapes.json"
    errors_path = run_dir / "errors.jsonl"
    for path in (events_path, summary_csv_path, summary_json_path, shapes_path, errors_path):
        if path.exists():
            path.unlink()

    _write_json(
        config_path,
        {
            "args": _jsonable(vars(args)),
            "device": str(device),
            "max_batch_size": int(max_batch_size),
            "model_batch_grid": [{"model": name, "batch_sizes": sizes} for name, sizes in grid],
            "model_presets": MODEL_PRESETS,
        },
    )

    print(f"CACHED BATCH GRID PROFILE {run_dir}", flush=True)
    print(json.dumps({"device": str(device), "max_batch_size": max_batch_size, "raw_batches": int(args.raw_batches), "grid": grid}, default=str), flush=True)

    event_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []
    cached_batches: list[Any] = []
    loader: AsyncDailyIndexBatchLoader | None = None
    try:
        cached_batches, loader_rows = _load_cached_batches(args=args, max_batch_size=max_batch_size, device=device)
        event_rows.extend(loader_rows)
        for row in loader_rows:
            _append_jsonl(events_path, row)
        _write_json(shapes_path, _batch_shape_summary(cached_batches[0]) if cached_batches else {})

        run_index = 0
        for model_name, batch_sizes in grid:
            if model_name not in MODEL_PRESETS:
                raise SystemExit(f"Unknown model preset {model_name!r}; choose from {sorted(MODEL_PRESETS)}")
            for batch_size in batch_sizes:
                run_index += 1
                if int(args.max_runs) > 0 and run_index > int(args.max_runs):
                    break
                try:
                    rows, summary = _profile_combo(
                        args=args,
                        model_name=model_name,
                        batch_size=int(batch_size),
                        cached_batches=cached_batches,
                        device=device,
                        run_index=run_index,
                    )
                    event_rows.extend(rows)
                    summary_rows.append(summary)
                    for row in rows:
                        _append_jsonl(events_path, row)
                    _write_summary(summary_rows, summary_csv_path, summary_json_path)
                    print("SUMMARY", json.dumps(summary, sort_keys=True), flush=True)
                except Exception as exc:
                    error = {
                        "kind": "error",
                        "model": model_name,
                        "batch_size": int(batch_size),
                        "error": repr(exc),
                        "traceback": traceback.format_exc(),
                        "utc_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    }
                    _append_jsonl(errors_path, error)
                    print("ERROR", json.dumps({k: v for k, v in error.items() if k != "traceback"}, sort_keys=True), flush=True)
                    if not bool(args.continue_on_error):
                        raise
                finally:
                    gc.collect()
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
            if int(args.max_runs) > 0 and run_index >= int(args.max_runs):
                break
    finally:
        if loader is not None:
            loader.close()

    _write_summary(summary_rows, summary_csv_path, summary_json_path)
    print(f"PROFILE COMPLETE {run_dir}", flush=True)
    return 0 if not errors_path.exists() else 1


def _load_cached_batches(*, args: argparse.Namespace, max_batch_size: int, device: torch.device) -> tuple[list[Any], list[dict[str, Any]]]:
    batches: list[Any] = []
    rows: list[dict[str, Any]] = []
    raw_batches = max(1, int(args.raw_batches))
    if bool(args.dummy_data):
        base_config = _model_config_from_preset("small", args=args)
        for index in range(raw_batches):
            started = time.perf_counter()
            batch = make_dummy_temporal_batch(model_config=base_config, batch_size=max_batch_size, device="cpu")
            elapsed = time.perf_counter() - started
            batches.append(batch)
            rows.append(
                {
                    "kind": "loader_batch",
                    "mode": "dummy",
                    "batch_index": int(index + 1),
                    "sample_count": int(batch.sample_count),
                    "loader_seconds": float(elapsed),
                    "batch_nbytes": int(_payload_nbytes(batch)),
                    "cpu_rss_gib": _rss_gib(),
                    "gpu_allocated_gib": _gpu_allocated_gib(device),
                }
            )
        return batches, rows

    loader_config = LoaderConfig(
        cache_root=Path(args.cache_root),
        start_utc=str(args.start_utc or ""),
        end_utc=str(args.end_utc or ""),
        months=_split_csv(str(args.months)),
        tickers=_split_csv(str(args.tickers)),
        batch_size=int(max_batch_size),
        seed=int(args.seed),
        data_groups=DEFAULT_DATA_GROUPS,
        loaded_parts_per_group=int(args.loaded_parts_per_group),
        read_workers=int(args.read_workers),
        materialize_workers=int(args.materialize_workers),
        materialize_chunk_size=int(args.materialize_chunk_size),
        prefetch_batches=int(args.prefetch_batches),
        chronological_replay=bool(args.chronological_replay),
        time_window_seconds=float(args.time_window_seconds),
        ticker_cache_capacity=int(args.ticker_cache_capacity),
        origin_cursor_chunk_rows=int(args.origin_cursor_chunk_rows),
        warm_all_ticker_caches=bool(args.warm_all_ticker_caches),
        max_origins_per_epoch=int(args.max_origins_per_epoch),
        scanner_index_cache_entries=int(args.scanner_index_cache_entries),
        prefetch_scanner_indexes=bool(args.prefetch_scanner_indexes),
        scanner_prefetch_workers=int(args.scanner_prefetch_workers),
        randomize_seed=False,
        shuffle_parts=bool(args.shuffle_parts),
        shuffle_within_loaded_group=bool(args.shuffle_within_loaded_group),
    )
    loader = AsyncDailyIndexBatchLoader(loader_config_from_v3(loader_config))
    try:
        iterator = loader.iter_batches()
        for index in range(raw_batches):
            started = time.perf_counter()
            batch = next(iterator)
            elapsed = time.perf_counter() - started
            batches.append(batch)
            row = {
                "kind": "loader_batch",
                "mode": "real",
                "batch_index": int(index + 1),
                "sample_count": int(batch.sample_count),
                "loader_seconds": float(elapsed),
                "batch_nbytes": int(_payload_nbytes(batch)),
                "cpu_rss_gib": _rss_gib(),
                "gpu_allocated_gib": _gpu_allocated_gib(device),
                "loader_telemetry": loader.telemetry_snapshot(),
                "batch_profile": dict(batch.profile),
            }
            rows.append(row)
            print("LOADER", json.dumps(_compact_loader_row(row), sort_keys=True, default=str), flush=True)
    finally:
        loader.close()
    return batches, rows


def _profile_combo(
    *,
    args: argparse.Namespace,
    model_name: str,
    batch_size: int,
    cached_batches: list[Any],
    device: torch.device,
    run_index: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    model_config = _model_config_from_preset(model_name, args=args)
    model = TemporalEventModelV3(model_config).to(device=device)
    model.train()
    compiled = False
    model_for_forward: torch.nn.Module = model
    if bool(args.compile_model):
        model_for_forward = torch.compile(model)  # type: ignore[assignment]
        compiled = True
    optimizer = torch.optim.AdamW(model_for_forward.parameters(), lr=float(args.learning_rate), weight_decay=float(args.weight_decay), foreach=False)
    amp_dtype = _amp_dtype(str(args.amp_dtype))
    scaler = torch.amp.GradScaler("cuda", enabled=bool(args.amp) and device.type == "cuda" and amp_dtype is torch.float16)
    rows: list[dict[str, Any]] = []
    measured: list[dict[str, Any]] = []
    total_steps = max(0, int(args.warmup_steps)) + max(1, int(args.measure_steps))
    detail_limit = int(args.detail_profile_steps)
    production_limit = int(args.profile_production_steps)

    parameter_counts = _parameter_counts(model)
    print(
        "RUN",
        json.dumps(
            {
                "model": model_name,
                "batch_size": int(batch_size),
                "parameters": int(parameter_counts["total_parameters"]),
                "compiled": bool(compiled),
            },
            sort_keys=True,
        ),
        flush=True,
    )

    for step in range(total_steps):
        phase = "warmup" if step < int(args.warmup_steps) else "measure"
        measured_step = step - int(args.warmup_steps)
        raw_batch = cached_batches[step % len(cached_batches)]
        sync = device.type == "cuda"
        if sync:
            torch.cuda.reset_peak_memory_stats(device)
            torch.cuda.synchronize(device)
        convert_started = time.perf_counter()
        sliced = _slice_batch(raw_batch, int(batch_size))
        torch_batch = _to_torch_batch(sliced, model_config=model_config, device=device)
        if sync:
            torch.cuda.synchronize(device)
        convert_seconds = time.perf_counter() - convert_started

        optimizer.zero_grad(set_to_none=True)
        full_step_started = time.perf_counter()
        profile_forward = (
            not compiled
            and phase == "measure"
            and (detail_limit <= 0 or measured_step < detail_limit)
        )
        with _autocast(device, bool(args.amp), amp_dtype):
            if profile_forward:
                if sync:
                    torch.cuda.synchronize(device)
                forward_started = time.perf_counter()
                output, model_timings = model.forward_with_timings(torch_batch.x, sync_cuda=sync)
                if sync:
                    torch.cuda.synchronize(device)
                forward_seconds = time.perf_counter() - forward_started
            else:
                if sync:
                    torch.cuda.synchronize(device)
                forward_started = time.perf_counter()
                output = model_for_forward(torch_batch.x)
                if sync:
                    torch.cuda.synchronize(device)
                forward_seconds = time.perf_counter() - forward_started
                model_timings = {}
            if sync:
                torch.cuda.synchronize(device)
            loss_started = time.perf_counter()
            loss_result = compute_loss(output, torch_batch)
            if sync:
                torch.cuda.synchronize(device)
            loss_seconds = time.perf_counter() - loss_started

        if sync:
            torch.cuda.synchronize(device)
        backward_started = time.perf_counter()
        if scaler.is_enabled():
            scaler.scale(loss_result.loss).backward()
        else:
            loss_result.loss.backward()
        if sync:
            torch.cuda.synchronize(device)
        backward_seconds = time.perf_counter() - backward_started

        if sync:
            torch.cuda.synchronize(device)
        optimizer_started = time.perf_counter()
        if float(args.grad_clip_norm) > 0:
            if scaler.is_enabled():
                scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(model_for_forward.parameters(), float(args.grad_clip_norm))
        if scaler.is_enabled():
            scaler.step(optimizer)
            scaler.update()
        else:
            optimizer.step()
        if sync:
            torch.cuda.synchronize(device)
        optimizer_seconds = time.perf_counter() - optimizer_started
        step_seconds = time.perf_counter() - full_step_started

        production_timings: dict[str, float] = {}
        if (
            bool(production_limit)
            and phase == "measure"
            and measured_step < production_limit
        ):
            production_timings = _profile_production_paths(model, torch_batch, device=device, amp=bool(args.amp), amp_dtype=amp_dtype)

        row = {
            "kind": "model_step",
            "run_index": int(run_index),
            "model": model_name,
            "batch_size": int(batch_size),
            "phase": phase,
            "step": int(step),
            "measured_step": int(measured_step) if phase == "measure" else -1,
            "sample_count": int(torch_batch.sample_count),
            "convert_seconds": float(convert_seconds),
            "forward_seconds": float(forward_seconds),
            "loss_seconds": float(loss_seconds),
            "backward_seconds": float(backward_seconds),
            "optimizer_seconds": float(optimizer_seconds),
            "step_seconds": float(step_seconds),
            "total_iteration_seconds": float(convert_seconds + step_seconds),
            "samples_per_second_model_step": float(batch_size / max(step_seconds, 1e-9)),
            "samples_per_second_total": float(batch_size / max(convert_seconds + step_seconds, 1e-9)),
            "loss": float(loss_result.loss.detach().float().cpu()),
            "cpu_rss_gib": _rss_gib(),
            "gpu_allocated_gib": _gpu_allocated_gib(device),
            "gpu_reserved_gib": _gpu_reserved_gib(device),
            "gpu_peak_allocated_gib": _gpu_peak_allocated_gib(device),
            "compiled": bool(compiled),
            **parameter_counts,
            **{f"model/{key}": float(value) for key, value in model_timings.items()},
            **{f"production/{key}": float(value) for key, value in production_timings.items()},
            **{f"loss_metric/{key}": float(value) for key, value in loss_result.metrics.items() if _is_number(value)},
        }
        rows.append(row)
        if phase == "measure":
            measured.append(row)
        print("STEP", json.dumps(_compact_step_row(row), sort_keys=True), flush=True)

    summary = _summarize_combo(rows=measured, model_name=model_name, batch_size=batch_size, run_index=run_index, parameter_counts=parameter_counts)
    del model_for_forward
    del model
    del optimizer
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    return rows, summary


@torch.no_grad()
def _profile_production_paths(
    model: TemporalEventModelV3,
    batch: TemporalBatch,
    *,
    device: torch.device,
    amp: bool,
    amp_dtype: torch.dtype,
) -> dict[str, float]:
    model.eval()
    sync = device.type == "cuda"
    with _autocast(device, amp, amp_dtype):
        if sync:
            torch.cuda.synchronize(device)
        started = time.perf_counter()
        tokens, encode_timings = model.encode_modality_tokens_with_timings(batch.x, sync_cuda=sync)
        if sync:
            torch.cuda.synchronize(device)
        encode_wall = time.perf_counter() - started
        if sync:
            torch.cuda.synchronize(device)
        started = time.perf_counter()
        _, predict_timings = model.predict_from_modality_tokens_with_timings(tokens, sync_cuda=sync)
        if sync:
            torch.cuda.synchronize(device)
        predict_wall = time.perf_counter() - started
    model.train()
    return {
        "cache_encode_wall_seconds": float(encode_wall),
        "cached_predict_wall_seconds": float(predict_wall),
        "cached_predict_samples_per_second": float(batch.sample_count / max(predict_wall, 1e-9)),
        **{f"cache_encode/{key}": float(value) for key, value in encode_timings.items()},
        **{f"cached_predict/{key}": float(value) for key, value in predict_timings.items()},
    }


def _to_torch_batch(batch: Any, *, model_config: ModelConfig, device: torch.device) -> TemporalBatch:
    if isinstance(batch, TemporalBatch):
        return _move_temporal_batch(batch, device=device)
    return batch_to_torch(batch, model_config=model_config, device=device, non_blocking=True)


def _slice_batch(value: Any, sample_count: int) -> Any:
    full = _sample_count(value)
    if full <= 0 or sample_count >= full:
        return value
    return _slice_value(value, int(sample_count), int(full))


def _slice_value(value: Any, sample_count: int, full_count: int) -> Any:
    if isinstance(value, np.ndarray):
        if value.ndim > 0 and int(value.shape[0]) == int(full_count):
            return value[:sample_count]
        return value
    if torch.is_tensor(value):
        if value.ndim > 0 and int(value.shape[0]) == int(full_count):
            return value[:sample_count]
        return value
    if isinstance(value, dict):
        return {key: _slice_value(item, sample_count, full_count) for key, item in value.items()}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        updates: dict[str, Any] = {}
        for field in dataclasses.fields(value):
            item = getattr(value, field.name)
            if field.name == "sample_count":
                updates[field.name] = int(sample_count)
            else:
                updates[field.name] = _slice_value(item, sample_count, full_count)
        return dataclasses.replace(value, **updates)
    if isinstance(value, tuple):
        return tuple(_slice_value(item, sample_count, full_count) for item in value)
    if isinstance(value, list):
        return [_slice_value(item, sample_count, full_count) for item in value]
    return value


def _move_temporal_batch(batch: TemporalBatch, *, device: torch.device) -> TemporalBatch:
    return TemporalBatch(
        x=_move_value(batch.x, device=device),
        y=_move_value(batch.y, device=device),
        identity=batch.identity,
        profile=batch.profile,
        sample_count=int(batch.sample_count),
    )


def _move_value(value: Any, *, device: torch.device) -> Any:
    if torch.is_tensor(value):
        return value.to(device=device, non_blocking=True)
    if isinstance(value, dict):
        return {key: _move_value(item, device=device) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_move_value(item, device=device) for item in value)
    if isinstance(value, list):
        return [_move_value(item, device=device) for item in value]
    return value


def _sample_count(value: Any) -> int:
    attr = getattr(value, "sample_count", None)
    if attr is not None:
        try:
            return int(attr)
        except TypeError:
            pass
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        for field in dataclasses.fields(value):
            count = _sample_count(getattr(value, field.name))
            if count > 0:
                return count
    if isinstance(value, Mapping):
        for item in value.values():
            count = _sample_count(item)
            if count > 0:
                return count
    if isinstance(value, np.ndarray) and value.ndim:
        return int(value.shape[0])
    if torch.is_tensor(value) and value.ndim:
        return int(value.shape[0])
    return 0


def _model_config_from_preset(name: str, *, args: argparse.Namespace | None = None) -> ModelConfig:
    config = ModelConfig()
    preset = MODEL_PRESETS.get(str(name), {})
    for key, value in preset.items():
        setattr(config, key.replace("-", "_"), int(value))
    if args is not None:
        config.event_encoder_type = str(args.event_encoder_type)
        config.event_item_dim = int(args.event_item_dim)
        config.event_latents = int(args.event_latents)
        config.event_latent_layers = int(args.event_latent_layers)
        config.event_latent_heads = int(args.event_latent_heads)
    return config


def _parameter_counts(model: torch.nn.Module) -> dict[str, int]:
    counts: dict[str, int] = {"total_parameters": sum(param.numel() for param in model.parameters())}
    for name, module in model.named_children():
        counts[f"parameters/{name}"] = sum(param.numel() for param in module.parameters())
    return counts


def _summarize_combo(
    *,
    rows: list[dict[str, Any]],
    model_name: str,
    batch_size: int,
    run_index: int,
    parameter_counts: dict[str, int],
) -> dict[str, Any]:
    keys = (
        "convert_seconds",
        "forward_seconds",
        "loss_seconds",
        "backward_seconds",
        "optimizer_seconds",
        "step_seconds",
        "total_iteration_seconds",
        "samples_per_second_model_step",
        "samples_per_second_total",
        "gpu_peak_allocated_gib",
        "gpu_reserved_gib",
        "cpu_rss_gib",
        "production/cache_encode_wall_seconds",
        "production/cached_predict_wall_seconds",
        "production/cached_predict_samples_per_second",
        "loss",
    )
    summary: dict[str, Any] = {
        "run_index": int(run_index),
        "model": model_name,
        "batch_size": int(batch_size),
        "measured_steps": int(len(rows)),
        **parameter_counts,
    }
    for key in keys:
        values = [float(row[key]) for row in rows if _is_number(row.get(key))]
        summary[f"{key}/mean"] = _mean(values)
        summary[f"{key}/p50"] = _percentile(values, 0.50)
        summary[f"{key}/p95"] = _percentile(values, 0.95)
    return summary


def _write_summary(rows: list[dict[str, Any]], csv_path: Path, json_path: Path) -> None:
    _write_json(json_path, rows)
    if not rows:
        return
    fields = sorted({key for row in rows for key in row})
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _batch_shape_summary(batch: Any) -> dict[str, Any]:
    return _shape_value(batch)


def _shape_value(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return {"shape": list(value.shape), "dtype": str(value.dtype), "nbytes": int(value.nbytes)}
    if torch.is_tensor(value):
        return {"shape": list(value.shape), "dtype": str(value.dtype), "device": str(value.device), "nbytes": int(value.numel() * value.element_size())}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {field.name: _shape_value(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _shape_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_shape_value(item) for item in value]
    if isinstance(value, list):
        return [_shape_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(type(value).__name__)


def _payload_nbytes(value: Any) -> int:
    if isinstance(value, np.ndarray):
        return int(value.nbytes)
    if torch.is_tensor(value):
        return int(value.numel() * value.element_size())
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return sum(_payload_nbytes(getattr(value, field.name)) for field in dataclasses.fields(value))
    if isinstance(value, Mapping):
        return sum(_payload_nbytes(item) for item in value.values())
    if isinstance(value, (tuple, list)):
        return sum(_payload_nbytes(item) for item in value)
    return 0


def _resolve_device(value: str) -> torch.device:
    requested = str(value or "auto").lower()
    if requested == "cuda":
        if not torch.cuda.is_available():
            raise SystemExit("--device cuda requested but CUDA is not available.")
        return torch.device("cuda")
    if requested == "cpu":
        return torch.device("cpu")
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def _amp_dtype(value: str) -> torch.dtype:
    normalized = str(value or "").lower()
    if normalized in {"bf16", "bfloat16"}:
        return torch.bfloat16
    if normalized in {"fp16", "float16"}:
        return torch.float16
    return torch.float32


def _autocast(device: torch.device, amp: bool, amp_dtype: torch.dtype) -> Any:
    if amp and device.type == "cuda" and amp_dtype in {torch.float16, torch.bfloat16}:
        return torch.autocast(device_type="cuda", dtype=amp_dtype)
    return nullcontext()


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in str(value or "").split(",") if part.strip())


def _model_batch_grid(value: str, *, fallback_models: str, fallback_batch_sizes: str) -> list[tuple[str, list[int]]]:
    text = str(value or "").strip()
    if not text:
        models = _split_csv(fallback_models or "small")
        batch_sizes = [int(size) for size in _split_csv(fallback_batch_sizes)]
        return [(model, batch_sizes) for model in models]
    grid: list[tuple[str, list[int]]] = []
    for chunk in text.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            raise SystemExit(f"Invalid --model-batch-grid chunk {chunk!r}; expected model:bs,bs")
        model, sizes = chunk.split(":", 1)
        parsed = [int(size) for size in _split_csv(sizes)]
        if parsed:
            grid.append((model.strip(), parsed))
    return grid


def _run_dir(args: argparse.Namespace) -> Path:
    name = str(args.run_name or "").strip()
    if not name:
        name = f"cached_batch_grid_{time.strftime('%Y%m%d_%H%M%S')}"
    return Path(args.output_root) / name


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_jsonable(value), indent=2, sort_keys=True), encoding="utf-8")


def _append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_jsonable(dict(row)), sort_keys=True) + "\n")


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, np.ndarray):
        if value.size <= 16:
            return value.tolist()
        return {"shape": list(value.shape), "dtype": str(value.dtype)}
    if isinstance(value, np.generic):
        return value.item()
    if torch.is_tensor(value):
        if value.numel() <= 16:
            return value.detach().cpu().tolist()
        return {"shape": list(value.shape), "dtype": str(value.dtype), "device": str(value.device)}
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        return str(value)
    return value


def _compact_loader_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "batch_index": row.get("batch_index"),
        "sample_count": row.get("sample_count"),
        "loader_seconds": row.get("loader_seconds"),
        "batch_gib": float(row.get("batch_nbytes", 0) or 0) / (1024**3),
        "cpu_rss_gib": row.get("cpu_rss_gib"),
    }


def _compact_step_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "model": row.get("model"),
        "batch_size": row.get("batch_size"),
        "phase": row.get("phase"),
        "step": row.get("step"),
        "convert_seconds": row.get("convert_seconds"),
        "forward_seconds": row.get("forward_seconds"),
        "backward_seconds": row.get("backward_seconds"),
        "step_seconds": row.get("step_seconds"),
        "samples_per_second_total": row.get("samples_per_second_total"),
        "gpu_peak_allocated_gib": row.get("gpu_peak_allocated_gib"),
        "cpu_rss_gib": row.get("cpu_rss_gib"),
    }


def _mean(values: list[float]) -> float:
    return float(sum(values) / len(values)) if values else 0.0


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    pos = max(0.0, min(float(len(ordered) - 1), float(q) * (len(ordered) - 1)))
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return float(ordered[lo])
    return float(ordered[lo] * (hi - pos) + ordered[hi] * (pos - lo))


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float, np.integer, np.floating)) and math.isfinite(float(value))


def _rss_gib() -> float:
    if psutil is None:
        return 0.0
    return float(psutil.Process().memory_info().rss / (1024**3))


def _gpu_allocated_gib(device: torch.device) -> float:
    if device.type != "cuda" or not torch.cuda.is_available():
        return 0.0
    return float(torch.cuda.memory_allocated(device) / (1024**3))


def _gpu_reserved_gib(device: torch.device) -> float:
    if device.type != "cuda" or not torch.cuda.is_available():
        return 0.0
    return float(torch.cuda.memory_reserved(device) / (1024**3))


def _gpu_peak_allocated_gib(device: torch.device) -> float:
    if device.type != "cuda" or not torch.cuda.is_available():
        return 0.0
    return float(torch.cuda.max_memory_allocated(device) / (1024**3))


if __name__ == "__main__":
    raise SystemExit(main())
