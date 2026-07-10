from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import torch

try:
    import psutil
except ImportError:  # pragma: no cover - optional workstation dependency.
    psutil = None


REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.env import discover_env_files, load_env_files
from research.temporal_event_model.v3.config import DEFAULT_DATA_GROUPS, ExperimentConfig, LoaderConfig, ModelConfig, TrainConfig, to_dict
from research.temporal_event_model.v3.data import TemporalBatch
from research.temporal_event_model.v3.train import (
    _batch_iterator,
    _cancel_iterator,
    _install_interrupt_handlers,
    _make_loader,
    _set_interrupt_reason,
    set_seed,
)


DEFAULT_OUTPUT_ROOT = Path("D:/TradingML/runtimes/temporal_event_model/v3/exact_loader_profile")


def parse_args() -> argparse.Namespace:
    default_loader = LoaderConfig()
    parser = argparse.ArgumentParser(
        description=(
            "Profile the exact v3 training loader path: AsyncDailyIndexBatchLoader -> training raw prefetch "
            "iterator -> batch_to_torch. No synthetic cache and no narrowed component path."
        )
    )
    parser.add_argument("--cache-root", type=Path, default=default_loader.cache_root)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-name", default="")
    parser.add_argument("--split", default=default_loader.split)
    parser.add_argument("--dataset-id", default=default_loader.dataset_id)
    parser.add_argument("--months", default="2019-02")
    parser.add_argument("--training-days", default="", help="Comma-separated YYYY-MM-DD day filter. Empty uses all selected cache days.")
    parser.add_argument("--start-utc", default=default_loader.start_utc)
    parser.add_argument("--end-utc", default=default_loader.end_utc)
    parser.add_argument("--tickers", default="")
    parser.add_argument("--data-groups", default=",".join(DEFAULT_DATA_GROUPS))
    parser.add_argument("--intraday-label-horizons", default=",".join(default_loader.intraday_label_horizons))
    parser.add_argument("--batch-size", type=int, default=default_loader.batch_size)
    parser.add_argument("--warmup-batches", type=int, default=4)
    parser.add_argument("--batches", type=int, default=128)
    parser.add_argument("--max-origins-per-epoch", type=int, default=default_loader.max_origins_per_epoch)
    parser.add_argument("--seed", type=int, default=default_loader.seed)
    parser.add_argument("--read-workers", type=int, default=default_loader.read_workers)
    parser.add_argument("--materialize-workers", type=int, default=default_loader.materialize_workers)
    parser.add_argument("--loaded-parts-per-group", type=int, default=default_loader.loaded_parts_per_group)
    parser.add_argument("--materialize-chunk-size", type=int, default=default_loader.materialize_chunk_size)
    parser.add_argument("--prefetch-batches", type=int, default=default_loader.prefetch_batches)
    parser.add_argument("--chronological-replay", action=argparse.BooleanOptionalAction, default=default_loader.chronological_replay)
    parser.add_argument("--time-window-seconds", type=float, default=default_loader.time_window_seconds)
    parser.add_argument("--frontier-max-origins-per-window", type=int, default=default_loader.frontier_max_origins_per_window)
    parser.add_argument("--ticker-cache-capacity", type=int, default=default_loader.ticker_cache_capacity)
    parser.add_argument("--origin-cursor-chunk-rows", type=int, default=default_loader.origin_cursor_chunk_rows)
    parser.add_argument("--warm-all-ticker-caches", action=argparse.BooleanOptionalAction, default=default_loader.warm_all_ticker_caches)
    parser.add_argument("--scanner-index-cache-entries", type=int, default=default_loader.scanner_index_cache_entries)
    parser.add_argument("--prefetch-scanner-indexes", action=argparse.BooleanOptionalAction, default=default_loader.prefetch_scanner_indexes)
    parser.add_argument("--scanner-prefetch-workers", type=int, default=default_loader.scanner_prefetch_workers)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--amp-dtype", choices=("bf16", "bfloat16", "fp16", "float16", "float32"), default=TrainConfig().amp_dtype)
    parser.add_argument("--fresh-start", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--telemetry-seconds", type=float, default=5.0)
    parser.add_argument("--print-every-batches", type=int, default=1)
    parser.add_argument(
        "--require-all-input-coverage",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Fail the profile if requested input modalities never produce available payloads across the measured batches.",
    )
    parser.add_argument("--coverage-required-keys", default="auto", help="Comma-separated input_availability keys to require, or auto from --data-groups.")
    return parser.parse_args()


def main() -> int:
    _install_interrupt_handlers()
    _set_interrupt_reason("exact training loader profile")
    load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    config = _config_from_args(args)
    set_seed(int(config.loader.seed))
    device = _resolve_device(str(args.device))
    if device.type == "cuda":
        torch.set_float32_matmul_precision("high")
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats(device)
    run_dir = _run_dir(args)
    run_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "config": run_dir / "exact_training_loader_config.json",
        "batches": run_dir / "exact_training_loader_batches.jsonl",
        "telemetry": run_dir / "exact_training_loader_telemetry.jsonl",
        "summary": run_dir / "exact_training_loader_summary.json",
        "shapes": run_dir / "exact_training_loader_first_batch_shapes.json",
        "errors": run_dir / "fatal_error.txt",
    }
    if bool(args.fresh_start):
        for path in paths.values():
            try:
                path.unlink()
            except FileNotFoundError:
                pass
    _write_json(paths["config"], {"args": vars(args), "config": to_dict(config), "device": str(device), "created_utc": _now_iso()})
    print(f"EXACT TRAINING LOADER PROFILE {run_dir}", flush=True)
    print(
        json.dumps(
            {
                "batch_size": int(config.loader.batch_size),
                "warmup_batches": int(args.warmup_batches),
                "batches": int(args.batches),
                "prefetch_batches": int(config.loader.prefetch_batches),
                "device": str(device),
                "data_groups": list(config.loader.data_groups),
                "cache_root": str(config.loader.cache_root),
                "months": list(config.loader.months),
                "training_days": list(config.loader.training_days),
                "tickers": list(config.loader.tickers),
            },
            sort_keys=True,
        ),
        flush=True,
    )

    loader = None
    iterator = None
    telemetry_stop = threading.Event()
    telemetry_thread: threading.Thread | None = None
    rows: list[dict[str, Any]] = []
    first_shape_summary: dict[str, Any] = {}
    coverage_summary = _CoverageSummary(_coverage_required_keys(str(args.coverage_required_keys), config.loader.data_groups))
    started = time.perf_counter()
    status = "complete"
    try:
        loader = _make_loader(config.loader, validation=False)
        iterator = _batch_iterator(config, loader, device=device, dummy=False)
        telemetry_thread = _start_telemetry_sampler(
            loader=loader,
            iterator=iterator,
            path=paths["telemetry"],
            stop=telemetry_stop,
            interval_seconds=float(args.telemetry_seconds),
        )
        total_batches = max(0, int(args.warmup_batches)) + max(1, int(args.batches))
        for batch_index in range(1, total_batches + 1):
            phase = "warmup" if batch_index <= int(args.warmup_batches) else "measure"
            row, shape_summary = _profile_one_batch(
                iterator=iterator,
                loader=loader,
                phase=phase,
                batch_index=batch_index,
                device=device,
                started=started,
            )
            coverage_summary.add(row)
            if shape_summary and not first_shape_summary:
                first_shape_summary = shape_summary
                _write_json(paths["shapes"], shape_summary)
            _append_jsonl(paths["batches"], row)
            rows.append(row)
            if int(args.print_every_batches) > 0 and (batch_index % int(args.print_every_batches) == 0 or batch_index == 1):
                _print_batch_row(row, total_batches=total_batches)
        measured_rows = [row for row in rows if row.get("phase") == "measure"]
        summary = _summary_payload(
            args=args,
            config=config,
            rows=measured_rows,
            all_rows=rows,
            coverage=coverage_summary.summary(),
            run_dir=run_dir,
            device=device,
            status=status,
        )
        if bool(args.require_all_input_coverage):
            missing = coverage_summary.missing_required()
            summary["coverage_status"] = "failed" if missing else "ok"
            summary["missing_required_coverage"] = missing
            _write_json(paths["summary"], summary)
            if missing:
                print(f"COVERAGE FAILED missing_required={missing}", flush=True)
                return 2
        _write_json(paths["summary"], summary)
        print(
            "PROFILE COMPLETE "
            f"measured_batches={len(measured_rows)} measured_samples={sum(int(row.get('samples', 0)) for row in measured_rows):,} "
            f"avg_next={_avg(measured_rows, 'next_batch_seconds'):.3f}s "
            f"avg_sps={_avg(measured_rows, 'samples_per_second'):.1f} "
            f"max_rss={max((float(row.get('cpu_rss_gib', 0.0)) for row in rows), default=0.0):.2f}GiB",
            flush=True,
        )
        return 0
    except KeyboardInterrupt:
        status = "interrupted"
        summary = _summary_payload(
            args=args,
            config=config,
            rows=[row for row in rows if row.get("phase") == "measure"],
            all_rows=rows,
            coverage=coverage_summary.summary(),
            run_dir=run_dir,
            device=device,
            status=status,
        )
        _write_json(paths["summary"], summary)
        print(f"Interrupt received. Partial profile written to {run_dir}", flush=True)
        return 130
    except Exception as exc:  # noqa: BLE001
        status = "error"
        paths["errors"].write_text("".join(traceback.format_exception(exc)), encoding="utf-8")
        summary = _summary_payload(
            args=args,
            config=config,
            rows=[row for row in rows if row.get("phase") == "measure"],
            all_rows=rows,
            coverage=coverage_summary.summary(),
            run_dir=run_dir,
            device=device,
            status=status,
        )
        summary["error"] = repr(exc)
        _write_json(paths["summary"], summary)
        raise
    finally:
        telemetry_stop.set()
        if telemetry_thread is not None and telemetry_thread.is_alive():
            telemetry_thread.join(timeout=3.0)
        if iterator is not None:
            _cancel_iterator(iterator)
        elif loader is not None:
            close = getattr(loader, "close", None)
            if callable(close):
                close()
        if device.type == "cuda":
            torch.cuda.empty_cache()


def _config_from_args(args: argparse.Namespace) -> ExperimentConfig:
    loader = LoaderConfig(
        cache_root=Path(args.cache_root),
        split=str(args.split),
        start_utc=str(args.start_utc),
        end_utc=str(args.end_utc),
        months=_split_csv(str(args.months)),
        tickers=_split_csv(str(args.tickers)),
        batch_size=int(args.batch_size),
        seed=int(args.seed),
        dataset_id=str(args.dataset_id),
        data_groups=_split_csv(str(args.data_groups)),
        intraday_label_horizons=_split_csv(str(args.intraday_label_horizons)),
        loaded_parts_per_group=int(args.loaded_parts_per_group),
        read_workers=int(args.read_workers),
        materialize_workers=int(args.materialize_workers),
        materialize_chunk_size=int(args.materialize_chunk_size),
        prefetch_batches=int(args.prefetch_batches),
        chronological_replay=bool(args.chronological_replay),
        time_window_seconds=float(args.time_window_seconds),
        frontier_max_origins_per_window=int(args.frontier_max_origins_per_window),
        ticker_cache_capacity=int(args.ticker_cache_capacity),
        origin_cursor_chunk_rows=int(args.origin_cursor_chunk_rows),
        warm_all_ticker_caches=bool(args.warm_all_ticker_caches),
        scanner_index_cache_entries=int(args.scanner_index_cache_entries),
        prefetch_scanner_indexes=bool(args.prefetch_scanner_indexes),
        scanner_prefetch_workers=int(args.scanner_prefetch_workers),
        max_origins_per_epoch=int(args.max_origins_per_epoch),
        training_days=_split_csv(str(args.training_days)),
        shuffle_parts=True,
        shuffle_within_loaded_group=True,
    )
    train = TrainConfig(
        seed=int(args.seed),
        amp=True,
        amp_dtype=str(args.amp_dtype),
        wandb_mode="disabled",
    )
    return ExperimentConfig(model=ModelConfig(intraday_horizons=len(loader.intraday_label_horizons)), loader=loader, train=train)


def _profile_one_batch(
    *,
    iterator: Any,
    loader: Any,
    phase: str,
    batch_index: int,
    device: torch.device,
    started: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    before = _merge_telemetry(loader, iterator)
    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats(device)
    next_start = time.perf_counter()
    batch = next(iterator)
    if device.type == "cuda":
        torch.cuda.synchronize()
    next_seconds = time.perf_counter() - next_start
    after = _merge_telemetry(loader, iterator)
    coverage = _batch_coverage(batch)
    batch_nbytes = _payload_nbytes(batch)
    row: dict[str, Any] = {
        "utc": _now_iso(),
        "phase": str(phase),
        "batch": int(batch_index),
        "samples": int(batch.sample_count),
        "next_batch_seconds": float(next_seconds),
        "samples_per_second": float(batch.sample_count / max(next_seconds, 1e-9)),
        "elapsed_seconds": float(time.perf_counter() - started),
        "batch_nbytes": int(batch_nbytes),
        "batch_gib": float(batch_nbytes / (1024**3)),
        "cpu_rss_gib": _rss_gib(),
        "gpu_memory_allocated_gib": _gpu_memory_allocated_gib(device),
        "gpu_memory_reserved_gib": _gpu_memory_reserved_gib(device),
        "gpu_memory_peak_gib": _gpu_memory_peak_gib(device),
        "source_part_keys": int(len(set(str(value) for value in np.asarray(batch.identity.get("source_part_key", [])).astype(str, copy=False)))),
        "unique_tickers": int(len(set(str(value) for value in np.asarray(batch.identity.get("ticker", [])).astype(str, copy=False)))),
        "first_origin_timestamp_us": int(np.asarray(batch.identity.get("origin_timestamp_us", [0]), dtype=np.int64)[0]) if int(batch.sample_count) else 0,
        "last_origin_timestamp_us": int(np.asarray(batch.identity.get("origin_timestamp_us", [0]), dtype=np.int64)[-1]) if int(batch.sample_count) else 0,
    }
    row.update({f"coverage/{key}": value for key, value in coverage.items()})
    row.update({f"loader_before/{key}": value for key, value in before.items() if _is_scalar(value)})
    row.update({f"loader_after/{key}": value for key, value in after.items() if _is_scalar(value)})
    row.update({f"profile/{key}": value for key, value in batch.profile.items() if _is_scalar(value)})
    first_summary = _batch_shape_summary(batch) if int(batch_index) == 1 else {}
    return row, first_summary


def _start_telemetry_sampler(*, loader: Any, iterator: Any, path: Path, stop: threading.Event, interval_seconds: float) -> threading.Thread:
    interval = max(1.0, float(interval_seconds))
    started = time.perf_counter()

    def run() -> None:
        last_print = 0.0
        while not stop.wait(interval):
            row = {
                "utc": _now_iso(),
                "elapsed_seconds": float(time.perf_counter() - started),
                "cpu_rss_gib": _rss_gib(),
                **{key: value for key, value in _merge_telemetry(loader, iterator).items() if _is_scalar(value)},
            }
            _append_jsonl(path, row)
            now = time.perf_counter()
            if now - last_print >= interval:
                last_print = now
                print(
                    "[telemetry] "
                    f"phase={row.get('loader_phase', '-')} "
                    f"raw_q={row.get('raw_prefetch_queue_size', 0)}/{row.get('raw_prefetch_queue_limit', 0)} "
                    f"produced={row.get('raw_prefetch_produced_batches', 0)} "
                    f"consumed={row.get('raw_prefetch_consumed_batches', 0)} "
                    f"emitted={row.get('emitted_batches', 0)} "
                    f"event_states={row.get('event_cache_ticker_states', 0)} "
                    f"context_states={row.get('context_cache_ticker_states', 0)} "
                    f"rss={float(row.get('cpu_rss_gib', 0.0)):.2f}GiB",
                    flush=True,
                )

    thread = threading.Thread(target=run, name="exact-loader-profile-telemetry", daemon=True)
    thread.start()
    return thread


def _merge_telemetry(loader: Any, iterator: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    loader_snapshot = getattr(loader, "telemetry_snapshot", None)
    if callable(loader_snapshot):
        try:
            out.update(dict(loader_snapshot()))
        except Exception:
            pass
    iterator_snapshot = getattr(iterator, "telemetry_snapshot", None)
    if callable(iterator_snapshot):
        try:
            out.update(dict(iterator_snapshot()))
        except Exception:
            pass
    return out


def _batch_coverage(batch: TemporalBatch) -> dict[str, float]:
    out: dict[str, float] = {}
    sample_count = max(1, int(batch.sample_count))
    availability = batch.x.get("input_availability", {}) if isinstance(batch.x, Mapping) else {}
    if not isinstance(availability, Mapping):
        return out
    for key, value in availability.items():
        arr = _to_numpy(value)
        if arr.size == 0:
            out[str(key)] = 0.0
            continue
        if arr.shape[:1] == (sample_count,):
            reduced = arr.reshape((sample_count, -1)).any(axis=1)
            out[str(key)] = float(np.mean(reduced.astype(np.float32)))
        else:
            out[str(key)] = float(bool(np.any(arr)))
    return out


class _CoverageSummary:
    def __init__(self, required_keys: tuple[str, ...]) -> None:
        self.required_keys = tuple(required_keys)
        self.max_by_key: dict[str, float] = {}
        self.sum_by_key: dict[str, float] = {}
        self.count = 0

    def add(self, row: Mapping[str, Any]) -> None:
        if row.get("phase") != "measure":
            return
        self.count += 1
        for key, value in row.items():
            if not str(key).startswith("coverage/"):
                continue
            name = str(key).split("/", 1)[1]
            numeric = float(value)
            self.max_by_key[name] = max(float(self.max_by_key.get(name, 0.0)), numeric)
            self.sum_by_key[name] = float(self.sum_by_key.get(name, 0.0)) + numeric

    def missing_required(self) -> list[str]:
        return [key for key in self.required_keys if float(self.max_by_key.get(key, 0.0)) <= 0.0]

    def summary(self) -> dict[str, Any]:
        return {
            "required_keys": list(self.required_keys),
            "measured_batches": int(self.count),
            "max_fraction": dict(sorted(self.max_by_key.items())),
            "mean_fraction": {
                key: float(value / max(1, self.count))
                for key, value in sorted(self.sum_by_key.items())
            },
        }


def _coverage_required_keys(value: str, data_groups: tuple[str, ...]) -> tuple[str, ...]:
    text = str(value or "").strip()
    if text and text.lower() != "auto":
        return _split_csv(text)
    group_to_key = {
        "events": "event_context_available",
        "intraday_labels": "intraday_labels_available",
        "corporate_action_labels": "corporate_action_labels_available",
        "intraday_bars": "ticker_intraday_bars_available",
        "daily_bars": "ticker_daily_bars_available",
        "global_daily_bars": "global_daily_bars_available",
        "ticker_news_embeddings": "ticker_news_available",
        "market_news_embeddings": "market_news_available",
        "sec_filing_embeddings": "sec_filings_available",
        "xbrl": "xbrl_available",
        "corporate_actions": "corporate_actions_available",
        "scanner_context": "scanner_context_available",
    }
    required: list[str] = []
    for group in data_groups:
        key = group_to_key.get(str(group))
        if key and key not in required:
            required.append(key)
    return tuple(required)


def _summary_payload(
    *,
    args: argparse.Namespace,
    config: ExperimentConfig,
    rows: list[Mapping[str, Any]],
    all_rows: list[Mapping[str, Any]],
    coverage: Mapping[str, Any],
    run_dir: Path,
    device: torch.device,
    status: str,
) -> dict[str, Any]:
    measured_samples = sum(int(row.get("samples", 0)) for row in rows)
    elapsed = 0.0
    if all_rows:
        elapsed = float(all_rows[-1].get("elapsed_seconds", 0.0))
    keys = (
        "next_batch_seconds",
        "samples_per_second",
        "batch_gib",
        "cpu_rss_gib",
        "gpu_memory_allocated_gib",
        "gpu_memory_reserved_gib",
        "gpu_memory_peak_gib",
    )
    averages = {key: _avg(rows, key) for key in keys}
    p95 = {key: _percentile(rows, key, 95.0) for key in keys}
    return {
        "status": str(status),
        "run_dir": str(run_dir),
        "created_utc": _now_iso(),
        "device": str(device),
        "args": {key: _jsonable(value) for key, value in vars(args).items()},
        "config": to_dict(config),
        "warmup_batches": int(args.warmup_batches),
        "measured_batches": int(len(rows)),
        "measured_samples": int(measured_samples),
        "elapsed_seconds": float(elapsed),
        "overall_samples_per_second": float(measured_samples / max(elapsed, 1e-9)) if elapsed > 0 else 0.0,
        "averages": averages,
        "p95": p95,
        "coverage": dict(coverage),
        "last_row": dict(all_rows[-1]) if all_rows else {},
    }


def _batch_shape_summary(batch: TemporalBatch) -> dict[str, Any]:
    return {
        "sample_count": int(batch.sample_count),
        "identity": _shape_tree(batch.identity),
        "x": _shape_tree(batch.x),
        "y": _shape_tree(batch.y),
        "profile_keys": sorted(str(key) for key in batch.profile),
    }


def _shape_tree(value: Any) -> Any:
    if torch.is_tensor(value):
        return {"shape": list(value.shape), "dtype": str(value.dtype).replace("torch.", ""), "device": str(value.device)}
    if isinstance(value, np.ndarray):
        return {"shape": list(value.shape), "dtype": str(value.dtype)}
    if isinstance(value, Mapping):
        return {str(key): _shape_tree(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        if len(value) <= 32 and all(isinstance(item, (str, int, float, bool)) or item is None for item in value):
            return list(value)
        return [_shape_tree(item) for item in value[:32]]
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


def _to_numpy(value: Any) -> np.ndarray:
    if torch.is_tensor(value):
        return value.detach().to("cpu").numpy()
    return np.asarray(value)


def _print_batch_row(row: Mapping[str, Any], *, total_batches: int) -> None:
    print(
        f"{row.get('phase')} batch={row.get('batch')}/{total_batches} "
        f"samples={row.get('samples')} next={float(row.get('next_batch_seconds', 0.0)):.3f}s "
        f"sps={float(row.get('samples_per_second', 0.0)):.1f} "
        f"raw_q={row.get('loader_after/raw_prefetch_queue_size', 0)}/{row.get('loader_after/raw_prefetch_queue_limit', 0)} "
        f"produced={row.get('loader_after/raw_prefetch_produced_batches', 0)} "
        f"phase_now={row.get('loader_after/loader_phase', '-')} "
        f"rss={float(row.get('cpu_rss_gib', 0.0)):.2f}GiB "
        f"gpu={float(row.get('gpu_memory_allocated_gib', 0.0)):.2f}GiB",
        flush=True,
    )


def _run_dir(args: argparse.Namespace) -> Path:
    name = str(args.run_name or "").strip()
    if not name:
        name = f"exact_loader_{time.strftime('%Y%m%d_%H%M%S')}"
    return Path(args.output_root) / name


def _resolve_device(value: str) -> torch.device:
    requested = str(value or "auto").lower()
    if requested == "cuda":
        if not torch.cuda.is_available():
            raise SystemExit("--device cuda requested but CUDA is not available.")
        return torch.device("cuda")
    if requested == "cpu":
        return torch.device("cpu")
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in str(value or "").split(",") if part.strip())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _rss_gib() -> float:
    try:
        if psutil is None:
            return _windows_rss_gib()
        return float(psutil.Process(os.getpid()).memory_info().rss / (1024**3))
    except Exception:
        return 0.0


def _windows_rss_gib() -> float:
    if os.name != "nt":
        return 0.0
    try:
        import ctypes
        from ctypes import wintypes

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
        kernel32 = ctypes.WinDLL("kernel32.dll")
        psapi = ctypes.WinDLL("psapi.dll")
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        handle = kernel32.GetCurrentProcess()
        ok = psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
        if not ok:
            return 0.0
        return float(int(counters.WorkingSetSize) / (1024**3))
    except Exception:
        return 0.0


def _gpu_memory_allocated_gib(device: torch.device) -> float:
    return float(torch.cuda.memory_allocated(device) / (1024**3)) if device.type == "cuda" else 0.0


def _gpu_memory_reserved_gib(device: torch.device) -> float:
    return float(torch.cuda.memory_reserved(device) / (1024**3)) if device.type == "cuda" else 0.0


def _gpu_memory_peak_gib(device: torch.device) -> float:
    return float(torch.cuda.max_memory_allocated(device) / (1024**3)) if device.type == "cuda" else 0.0


def _avg(rows: list[Mapping[str, Any]], key: str) -> float:
    values = [float(row.get(key, 0.0) or 0.0) for row in rows if key in row]
    return float(sum(values) / max(1, len(values)))


def _percentile(rows: list[Mapping[str, Any]], key: str, percentile: float) -> float:
    values = sorted(float(row.get(key, 0.0) or 0.0) for row in rows if key in row)
    if not values:
        return 0.0
    index = min(len(values) - 1, max(0, int(round((float(percentile) / 100.0) * (len(values) - 1)))))
    return float(values[index])


def _append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_jsonable(dict(row)), sort_keys=True) + "\n")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_jsonable(value), indent=2, sort_keys=True), encoding="utf-8")


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _jsonable(dataclasses.asdict(value))
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
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _is_scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool, np.integer, np.floating))


if __name__ == "__main__":
    raise SystemExit(main())
