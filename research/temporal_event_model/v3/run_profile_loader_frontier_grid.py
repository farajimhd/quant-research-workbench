from __future__ import annotations

import argparse
import csv
import gc
import itertools
import json
import math
import random
import sys
import time
import traceback
from dataclasses import asdict, dataclass, fields, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

try:
    import psutil
except ImportError:  # pragma: no cover - optional workstation dependency.
    psutil = None

REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.env import discover_env_files, load_env_files
from research.mlops.rolling_loader.daily_index_dataset import AsyncDailyIndexBatchLoader
from research.temporal_event_model.v3.config import DEFAULT_DATA_GROUPS, LoaderConfig
from research.temporal_event_model.v3.data import loader_config_from_v3


DEFAULT_CACHE_ROOT = Path("D:/market-data/prepared/daily_index_streaming_cache/events_daily_index_2019-02")
DEFAULT_OUTPUT_ROOT = Path("D:/TradingML/runtimes/temporal_event_model/v3/loader_frontier_profiles")

PROFILE_NUMERIC_KEYS = (
    "origin_frontier_selected_refs",
    "origin_frontier_selected_parts",
    "origin_frontier_selected_tickers",
    "origin_frontier_cap_origins",
    "origin_frontier_cap_reached",
    "origin_frontier_heap_remaining",
    "origin_frontier_initialized_cursors",
    "origin_frontier_cursor_seconds",
    "origin_cursor_initial_seconds",
    "origin_cursor_rows_loaded",
    "origin_cursor_rows_loaded_for_window",
    "origin_window_rss_delta_mib",
    "window_active_refs",
    "window_active_parts",
    "window_active_tickers",
    "payload_cache_parts",
    "payload_cache_limit",
    "rolling_context_seconds",
    "rolling_text_seconds",
    "rolling_xbrl_seconds",
    "rolling_corporate_action_seconds",
    "rolling_bar_seconds",
    "rolling_scanner_seconds",
    "rolling_context_estimated_bytes",
    "event_cache_state_copy_seconds",
    "event_cache_materialize_seconds",
    "event_cache_rebuilds",
    "event_cache_appends",
    "event_cache_reused",
    "event_cache_estimated_bytes",
    "event_cache_ticker_states",
    "context_cache_ticker_states",
    "context_cache_modality_states",
    "context_cache_global_states",
    "context_cache_estimated_bytes",
    "materialize_wait_seconds",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Profile the v3 daily-index chronological loader over a grid of frontier/window settings. "
            "This is loader-only: it does not instantiate the v3 model or move tensors to GPU."
        )
    )
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_CACHE_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-name", default="")
    parser.add_argument("--months", default="2019-02")
    parser.add_argument("--start-utc", default="")
    parser.add_argument("--end-utc", default="")
    parser.add_argument("--training-days", default="", help="Comma-separated YYYY-MM-DD day filter. Empty uses all selected cache days.")
    parser.add_argument("--tickers", default="")
    parser.add_argument("--data-groups", default=",".join(DEFAULT_DATA_GROUPS))
    parser.add_argument("--batch-size", type=int, default=1024)
    parser.add_argument("--warmup-batches", type=int, default=1)
    parser.add_argument("--batches", type=int, default=4)
    parser.add_argument("--max-origins-per-epoch", type=int, default=200_000)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--preset", choices=("quick", "balanced", "full", "custom"), default="quick")
    parser.add_argument("--time-window-seconds-grid", default="", help="Comma-separated override. Example: 5,15,30,60")
    parser.add_argument("--frontier-max-origins-grid", default="", help="Comma-separated override. Include 0 for automatic cap.")
    parser.add_argument("--materialize-chunk-size-grid", default="", help="Comma-separated override. Example: 128,256,512")
    parser.add_argument("--worker-grid", default="", help="Comma-separated readxmaterialize pairs. Example: 16x32,16x48")
    parser.add_argument("--loaded-parts-per-group-grid", default="", help="Comma-separated override. Example: 128,256,512")
    parser.add_argument("--origin-cursor-chunk-rows-grid", default="", help="Comma-separated override. Example: 512,1024,2048")
    parser.add_argument("--ticker-cache-capacity", type=int, default=15_000)
    parser.add_argument("--scanner-index-cache-entries", type=int, default=4)
    parser.add_argument("--scanner-prefetch-workers", type=int, default=8)
    parser.add_argument("--prefetch-scanner-indexes", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--warm-all-ticker-caches", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--shuffle-grid", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--max-runs", type=int, default=0)
    parser.add_argument("--target-rss-gib", type=float, default=96.0)
    parser.add_argument("--target-first-batch-seconds", type=float, default=180.0)
    parser.add_argument("--continue-on-error", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    random.seed(int(args.seed))
    np.random.seed(int(args.seed))
    run_dir = _run_dir(args)
    run_dir.mkdir(parents=True, exist_ok=True)
    config_path = run_dir / "loader_frontier_grid_config.json"
    batches_path = run_dir / "loader_frontier_grid_batches.jsonl"
    results_path = run_dir / "loader_frontier_grid_results.jsonl"
    summary_csv_path = run_dir / "loader_frontier_grid_summary.csv"
    summary_json_path = run_dir / "loader_frontier_grid_summary.json"
    recommendation_path = run_dir / "recommended_loader_config.json"
    errors_path = run_dir / "errors.jsonl"
    for path in (batches_path, results_path, summary_csv_path, summary_json_path, recommendation_path, errors_path):
        if path.exists():
            path.unlink()

    grid = _grid_from_args(args)
    if bool(args.shuffle_grid):
        random.Random(int(args.seed)).shuffle(grid)
    if int(args.max_runs) > 0:
        grid = grid[: int(args.max_runs)]
    if not grid:
        raise SystemExit("Loader frontier grid is empty.")

    _write_json(
        config_path,
        {
            "args": {key: _jsonable(value) for key, value in vars(args).items()},
            "grid_count": len(grid),
            "grid": [asdict(item) for item in grid],
            "created_utc": _now_iso(),
        },
    )
    print(f"LOADER FRONTIER GRID PROFILE {run_dir}", flush=True)
    print(json.dumps({"grid_count": len(grid), "batch_size": int(args.batch_size), "warmup_batches": int(args.warmup_batches), "batches": int(args.batches), "preset": str(args.preset)}, sort_keys=True), flush=True)

    results: list[dict[str, Any]] = []
    started_all = time.perf_counter()
    try:
        for run_index, grid_item in enumerate(grid, start=1):
            label = _grid_label(grid_item)
            print(f"[{run_index:03d}/{len(grid):03d}] START {label}", flush=True)
            try:
                result = _profile_one_grid_item(args=args, grid_item=grid_item, run_index=run_index, run_dir=run_dir, batches_path=batches_path)
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # noqa: BLE001
                error = {
                    "run_index": run_index,
                    "grid": asdict(grid_item),
                    "status": "error",
                    "error": repr(exc),
                    "traceback": "".join(traceback.format_exception(exc)),
                    "timestamp_utc": _now_iso(),
                }
                _append_jsonl(errors_path, error)
                print(f"[{run_index:03d}/{len(grid):03d}] ERROR {label} {exc!r}", flush=True)
                if not bool(args.continue_on_error):
                    raise
                result = {**error, "score": 0.0}
            results.append(result)
            _append_jsonl(results_path, result)
            _write_csv(summary_csv_path, results)
            recommendations = _recommendations(results, args=args)
            _write_json(summary_json_path, {"run_dir": str(run_dir), "elapsed_seconds": time.perf_counter() - started_all, "results": results, "recommendations": recommendations})
            _write_json(recommendation_path, recommendations)
            _print_result_line(run_index, len(grid), result)
            gc.collect()
    except KeyboardInterrupt:
        print("Interrupt received. Partial results are saved.", flush=True)
        _write_json(summary_json_path, {"run_dir": str(run_dir), "status": "interrupted", "elapsed_seconds": time.perf_counter() - started_all, "results": results, "recommendations": _recommendations(results, args=args)})
        return 130

    recommendations = _recommendations(results, args=args)
    _write_json(summary_json_path, {"run_dir": str(run_dir), "status": "complete", "elapsed_seconds": time.perf_counter() - started_all, "results": results, "recommendations": recommendations})
    _write_json(recommendation_path, recommendations)
    _write_csv(summary_csv_path, results)
    print("SUMMARY", json.dumps(_compact_recommendations(recommendations), sort_keys=True), flush=True)
    return 0


def _profile_one_grid_item(*, args: argparse.Namespace, grid_item: "GridItem", run_index: int, run_dir: Path, batches_path: Path) -> dict[str, Any]:
    total_requested = max(0, int(args.warmup_batches)) + max(1, int(args.batches))
    max_origins = max(int(args.max_origins_per_epoch), total_requested * int(args.batch_size) * 2)
    loader_config = LoaderConfig(
        cache_root=Path(args.cache_root),
        split="train",
        start_utc=str(args.start_utc),
        end_utc=str(args.end_utc),
        months=_split_csv(str(args.months)),
        tickers=_split_csv(str(args.tickers)),
        batch_size=int(args.batch_size),
        seed=int(args.seed),
        dataset_id=f"loader_frontier_grid_{run_index}",
        data_groups=_split_csv(str(args.data_groups)),
        read_workers=int(grid_item.read_workers),
        materialize_workers=int(grid_item.materialize_workers),
        loaded_parts_per_group=int(grid_item.loaded_parts_per_group),
        materialize_chunk_size=int(grid_item.materialize_chunk_size),
        chronological_replay=True,
        time_window_seconds=float(grid_item.time_window_seconds),
        frontier_max_origins_per_window=int(grid_item.frontier_max_origins_per_window),
        ticker_cache_capacity=int(args.ticker_cache_capacity),
        origin_cursor_chunk_rows=int(grid_item.origin_cursor_chunk_rows),
        warm_all_ticker_caches=bool(args.warm_all_ticker_caches),
        scanner_index_cache_entries=int(args.scanner_index_cache_entries),
        prefetch_scanner_indexes=bool(args.prefetch_scanner_indexes),
        scanner_prefetch_workers=int(args.scanner_prefetch_workers),
        max_origins_per_epoch=int(max_origins),
        training_days=_split_csv(str(args.training_days)),
        shuffle_parts=False,
        shuffle_within_loaded_group=False,
    )
    loader = AsyncDailyIndexBatchLoader(loader_config_from_v3(loader_config))
    iterator = loader.iter_batches()
    rss_start = _rss_mib()
    batch_rows: list[dict[str, Any]] = []
    samples_total = 0
    measured_samples = 0
    measured_seconds = 0.0
    first_batch_seconds = 0.0
    started = time.perf_counter()
    status = "ok"
    try:
        for batch_index in range(1, total_requested + 1):
            phase = "warmup" if batch_index <= int(args.warmup_batches) else "measure"
            before = time.perf_counter()
            batch = next(iterator)
            next_seconds = time.perf_counter() - before
            if batch_index == 1:
                first_batch_seconds = float(next_seconds)
            samples = int(batch.sample_count)
            samples_total += samples
            if phase == "measure":
                measured_samples += samples
                measured_seconds += float(next_seconds)
            row = {
                "run_index": int(run_index),
                "batch_index": int(batch_index),
                "phase": phase,
                "samples": samples,
                "next_seconds": float(next_seconds),
                "samples_per_sec": float(samples / next_seconds) if next_seconds > 0 else 0.0,
                "rss_mib": float(_rss_mib()),
                "batch_estimated_mib": float(_nested_nbytes(batch) / (1024.0 * 1024.0)),
                **asdict(grid_item),
                **_selected_profile_metrics(batch.profile),
            }
            batch_rows.append(row)
            _append_jsonl(batches_path, row)
    finally:
        try:
            iterator.close()  # type: ignore[attr-defined]
        except Exception:
            pass
        loader.close()
    elapsed = time.perf_counter() - started
    summary = _summarize_batch_rows(batch_rows)
    rss_end = _rss_mib()
    result = {
        "run_index": int(run_index),
        "status": status,
        "run_dir": str(run_dir),
        "batches": int(len(batch_rows)),
        "warmup_batches": int(args.warmup_batches),
        "measured_batches": int(sum(1 for row in batch_rows if row.get("phase") == "measure")),
        "samples_total": int(samples_total),
        "measured_samples": int(measured_samples),
        "elapsed_seconds": float(elapsed),
        "measured_seconds": float(measured_seconds),
        "first_batch_seconds": float(first_batch_seconds),
        "overall_samples_per_sec": float(samples_total / elapsed) if elapsed > 0 else 0.0,
        "measured_samples_per_sec": float(measured_samples / measured_seconds) if measured_seconds > 0 else 0.0,
        "rss_start_mib": float(rss_start),
        "rss_end_mib": float(rss_end),
        "rss_delta_mib": float(rss_end - rss_start),
        "max_rss_mib": float(max([rss_start, rss_end, *[float(row.get("rss_mib", 0.0) or 0.0) for row in batch_rows]])),
        **asdict(grid_item),
        **summary,
    }
    result["score"] = _balanced_score(result, args=args)
    return result


def _summarize_batch_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    measured = [row for row in rows if row.get("phase") == "measure"]
    source = measured or list(rows)
    out: dict[str, Any] = {}
    for key in ("next_seconds", "samples_per_sec", "batch_estimated_mib"):
        values = [float(row.get(key, 0.0) or 0.0) for row in source]
        out[f"avg_{key}"] = _mean(values)
        out[f"max_{key}"] = max(values) if values else 0.0
    for key in PROFILE_NUMERIC_KEYS:
        values = [float(row.get(key, 0.0) or 0.0) for row in source if key in row]
        if not values:
            continue
        out[f"avg_{key}"] = _mean(values)
        out[f"max_{key}"] = max(values)
    cap_values = [float(row.get("origin_frontier_cap_reached", 0.0) or 0.0) for row in source]
    out["frontier_cap_reached_fraction"] = _mean(cap_values)
    return out


def _selected_profile_metrics(profile: Mapping[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for key in PROFILE_NUMERIC_KEYS:
        value = profile.get(key)
        if isinstance(value, (int, float, np.integer, np.floating)):
            out[key] = float(value)
    return out


def _balanced_score(result: Mapping[str, Any], *, args: argparse.Namespace) -> float:
    sps = max(0.0, float(result.get("measured_samples_per_sec", 0.0) or 0.0))
    if sps <= 0.0:
        return 0.0
    target_rss = max(1.0, float(args.target_rss_gib) * 1024.0)
    rss_penalty = max(1.0, float(result.get("max_rss_mib", 0.0) or 0.0) / target_rss)
    target_first = max(1.0, float(args.target_first_batch_seconds))
    first_penalty = max(1.0, float(result.get("first_batch_seconds", 0.0) or 0.0) / target_first)
    cap_penalty = 1.0 + 0.05 * max(0.0, float(result.get("frontier_cap_reached_fraction", 0.0) or 0.0))
    return float(sps / rss_penalty / first_penalty / cap_penalty)


def _recommendations(results: Sequence[Mapping[str, Any]], *, args: argparse.Namespace) -> dict[str, Any]:
    good = [dict(row) for row in results if str(row.get("status", "ok")) == "ok" and float(row.get("measured_samples_per_sec", 0.0) or 0.0) > 0.0]
    if not good:
        return {"status": "no_successful_runs"}
    fastest = max(good, key=lambda row: float(row.get("measured_samples_per_sec", 0.0) or 0.0))
    balanced = max(good, key=lambda row: float(row.get("score", 0.0) or 0.0))
    memory_light = min(good, key=lambda row: (float(row.get("max_rss_mib", 0.0) or 0.0), -float(row.get("measured_samples_per_sec", 0.0) or 0.0)))
    return {
        "status": "ok",
        "balanced": _recommendation_payload(balanced),
        "fastest": _recommendation_payload(fastest),
        "lowest_memory": _recommendation_payload(memory_light),
        "target_rss_gib": float(args.target_rss_gib),
        "target_first_batch_seconds": float(args.target_first_batch_seconds),
    }


def _recommendation_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "time_window_seconds",
        "frontier_max_origins_per_window",
        "materialize_chunk_size",
        "read_workers",
        "materialize_workers",
        "loaded_parts_per_group",
        "origin_cursor_chunk_rows",
        "measured_samples_per_sec",
        "first_batch_seconds",
        "max_rss_mib",
        "frontier_cap_reached_fraction",
        "score",
    )
    return {key: _jsonable(row.get(key)) for key in keys if key in row}


def _compact_recommendations(recommendations: Mapping[str, Any]) -> dict[str, Any]:
    if recommendations.get("status") != "ok":
        return dict(recommendations)
    return {
        "balanced": recommendations.get("balanced"),
        "fastest": recommendations.get("fastest"),
        "lowest_memory": recommendations.get("lowest_memory"),
    }


def _print_result_line(run_index: int, total: int, result: Mapping[str, Any]) -> None:
    if str(result.get("status", "ok")) != "ok":
        print(f"[{run_index:03d}/{total:03d}] ERROR {result.get('error')}", flush=True)
        return
    msg = (
        f"[{run_index:03d}/{total:03d}] "
        f"tw={float(result.get('time_window_seconds', 0.0)):g}s "
        f"cap={int(result.get('frontier_max_origins_per_window', 0) or 0):d} "
        f"chunk={int(result.get('materialize_chunk_size', 0) or 0):d} "
        f"workers={int(result.get('read_workers', 0) or 0):d}x{int(result.get('materialize_workers', 0) or 0):d} "
        f"sps={float(result.get('measured_samples_per_sec', 0.0) or 0.0):,.1f} "
        f"first={float(result.get('first_batch_seconds', 0.0) or 0.0):.1f}s "
        f"rss={float(result.get('max_rss_mib', 0.0) or 0.0) / 1024.0:.1f}GiB "
        f"frontier={float(result.get('avg_origin_frontier_selected_refs', 0.0) or 0.0):,.0f} "
        f"score={float(result.get('score', 0.0) or 0.0):,.1f}"
    )
    print(msg, flush=True)


def _grid_from_args(args: argparse.Namespace) -> list["GridItem"]:
    preset = str(args.preset)
    if preset == "quick":
        time_windows = [15.0, 60.0]
        caps = [0, 16_384, 65_536]
        chunks = [256]
        workers = [(16, 32)]
        loaded_parts = [256]
        cursor_rows = [1024]
    elif preset == "balanced":
        time_windows = [5.0, 15.0, 30.0, 60.0]
        caps = [0, 8_192, 16_384, 32_768, 65_536]
        chunks = [256]
        workers = [(16, 32)]
        loaded_parts = [256]
        cursor_rows = [1024]
    elif preset == "full":
        time_windows = [1.0, 5.0, 15.0, 30.0, 60.0]
        caps = [0, 8_192, 16_384, 32_768, 65_536]
        chunks = [128, 256, 512]
        workers = [(16, 32), (16, 48)]
        loaded_parts = [128, 256, 512]
        cursor_rows = [512, 1024, 2048]
    else:
        time_windows = [60.0]
        caps = [0]
        chunks = [256]
        workers = [(16, 32)]
        loaded_parts = [256]
        cursor_rows = [1024]

    time_windows = _parse_float_grid(args.time_window_seconds_grid) or time_windows
    caps = _parse_int_grid(args.frontier_max_origins_grid) or caps
    chunks = _parse_int_grid(args.materialize_chunk_size_grid) or chunks
    workers = _parse_worker_grid(args.worker_grid) or workers
    loaded_parts = _parse_int_grid(args.loaded_parts_per_group_grid) or loaded_parts
    cursor_rows = _parse_int_grid(args.origin_cursor_chunk_rows_grid) or cursor_rows

    out: list[GridItem] = []
    for tw, cap, chunk, worker, loaded, cursor in itertools.product(time_windows, caps, chunks, workers, loaded_parts, cursor_rows):
        read_workers, materialize_workers = worker
        out.append(
            GridItem(
                time_window_seconds=float(tw),
                frontier_max_origins_per_window=int(cap),
                materialize_chunk_size=int(chunk),
                read_workers=int(read_workers),
                materialize_workers=int(materialize_workers),
                loaded_parts_per_group=int(loaded),
                origin_cursor_chunk_rows=int(cursor),
            )
        )
    return out


@dataclass(frozen=True, slots=True)
class GridItem:
    time_window_seconds: float
    frontier_max_origins_per_window: int
    materialize_chunk_size: int
    read_workers: int
    materialize_workers: int
    loaded_parts_per_group: int
    origin_cursor_chunk_rows: int


def _grid_label(item: GridItem) -> str:
    return (
        f"tw={item.time_window_seconds:g}s "
        f"cap={item.frontier_max_origins_per_window} "
        f"chunk={item.materialize_chunk_size} "
        f"workers={item.read_workers}x{item.materialize_workers} "
        f"parts={item.loaded_parts_per_group} "
        f"cursor={item.origin_cursor_chunk_rows}"
    )


def _parse_int_grid(value: str) -> list[int]:
    out: list[int] = []
    for token in str(value or "").split(","):
        token = token.strip()
        if not token:
            continue
        out.append(int(token.replace("_", "")))
    return out


def _parse_float_grid(value: str) -> list[float]:
    out: list[float] = []
    for token in str(value or "").split(","):
        token = token.strip()
        if not token:
            continue
        out.append(float(token))
    return out


def _parse_worker_grid(value: str) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for token in str(value or "").split(","):
        token = token.strip().lower().replace(" ", "")
        if not token:
            continue
        if "x" not in token:
            raise ValueError(f"Invalid worker pair {token!r}; expected readxmaterialize, e.g. 16x32")
        left, right = token.split("x", 1)
        out.append((int(left), int(right)))
    return out


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in str(value or "").split(",") if part.strip())


def _run_dir(args: argparse.Namespace) -> Path:
    name = str(args.run_name or f"loader_frontier_grid_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}")
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in name)
    return Path(args.output_root) / safe


def _rss_mib() -> float:
    if psutil is None:
        return 0.0
    try:
        return float(psutil.Process().memory_info().rss) / (1024.0 * 1024.0)
    except Exception:  # noqa: BLE001
        return 0.0


def _nested_nbytes(value: Any) -> int:
    if isinstance(value, np.ndarray):
        return int(value.nbytes)
    if isinstance(value, Mapping):
        return int(sum(_nested_nbytes(v) for v in value.values()))
    if isinstance(value, (list, tuple)):
        return int(sum(_nested_nbytes(v) for v in value))
    if is_dataclass(value):
        return int(sum(_nested_nbytes(getattr(value, field.name)) for field in fields(value)))
    if hasattr(value, "__dict__"):
        return _nested_nbytes(vars(value))
    return 0


def _mean(values: Sequence[float]) -> float:
    return float(sum(values) / len(values)) if values else 0.0


def _append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({key: _jsonable(value) for key, value in payload.items()}, sort_keys=True) + "\n")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(_jsonable(payload), indent=2, sort_keys=True), encoding="utf-8")


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        return
    priority = [
        "run_index",
        "status",
        "time_window_seconds",
        "frontier_max_origins_per_window",
        "materialize_chunk_size",
        "read_workers",
        "materialize_workers",
        "loaded_parts_per_group",
        "origin_cursor_chunk_rows",
        "measured_samples_per_sec",
        "overall_samples_per_sec",
        "first_batch_seconds",
        "max_rss_mib",
        "frontier_cap_reached_fraction",
        "score",
    ]
    keys = list(dict.fromkeys(priority + sorted({key for row in rows for key in row.keys()})))
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _jsonable(row.get(key, "")) for key in keys})


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
