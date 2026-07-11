from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.rolling_loader.daily_index_cache import DEFAULT_DAILY_INDEX_CACHE_ROOT
from research.mlops.rolling_loader.rust_chrono_loader import (
    RustNativeCacheProfileConfig,
    build_rust_library,
    profile_rust_native_cache,
    rust_library_path,
    rust_version,
)
from research.temporal_event_model.v3.config import DEFAULT_DATA_GROUPS, DEFAULT_INTRADAY_LABEL_HORIZONS, TrainConfig


DEFAULT_CACHE_ROOT = DEFAULT_DAILY_INDEX_CACHE_ROOT / "events_daily_index_2019-02"
DEFAULT_OUTPUT_ROOT = Path("D:/TradingML/runtimes/rolling_loader/rust_native_cache_loader_profiles")
DEFAULT_BATCH_SIZE = 1024
DEFAULT_BATCHES = 20
DEFAULT_WARMUP_BATCHES = 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Profile the daily-index cache loader. By default this runs the "
            "full trainer-facing experiment: cache warmup plus 20 complete "
            "1024-sample materialized batches. Use --mode native-artifact-smoke "
            "for the low-level Rust parquet reader smoke."
        )
    )
    parser.add_argument("--mode", choices=("full-trainer-batches", "native-artifact-smoke"), default="full-trainer-batches")
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_CACHE_ROOT)
    parser.add_argument("--month", default="2019-02")
    parser.add_argument("--months", default="", help="Comma-separated month list for full-trainer-batches mode. Defaults to --month.")
    parser.add_argument("--training-days", default="", help="Comma-separated YYYY-MM-DD day filter for full-trainer-batches mode.")
    parser.add_argument("--tickers", default="")
    parser.add_argument("--start-utc", default="")
    parser.add_argument("--end-utc", default="")
    parser.add_argument("--data-groups", default=",".join(DEFAULT_DATA_GROUPS))
    parser.add_argument("--intraday-label-horizons", default=",".join(DEFAULT_INTRADAY_LABEL_HORIZONS))
    parser.add_argument(
        "--ticker-limit",
        type=int,
        default=0,
        help="Native artifact smoke ticker packages. 0 uses the resolved worker count.",
    )
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--batches", type=int, default=DEFAULT_BATCHES)
    parser.add_argument("--warmup-batches", type=int, default=DEFAULT_WARMUP_BATCHES)
    parser.add_argument("--event-stream-len", type=int, default=1024)
    parser.add_argument("--read-workers", type=int, default=0, help="Reader workers. 0 uses an automatic workstation-sized default.")
    parser.add_argument("--materialize-workers", type=int, default=0, help="Full-batch materializer workers. 0 uses an automatic workstation-sized default.")
    parser.add_argument("--loaded-parts-per-group", type=int, default=256)
    parser.add_argument("--materialize-chunk-size", type=int, default=0)
    parser.add_argument("--prefetch-batches", type=int, default=64)
    parser.add_argument("--time-window-seconds", type=float, default=60.0)
    parser.add_argument("--frontier-max-origins-per-window", type=int, default=0)
    parser.add_argument("--ticker-cache-capacity", type=int, default=15_000)
    parser.add_argument("--origin-cursor-chunk-rows", type=int, default=1024)
    parser.add_argument("--warm-all-ticker-caches", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--scanner-index-cache-entries", type=int, default=4)
    parser.add_argument("--prefetch-scanner-indexes", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--scanner-prefetch-workers", type=int, default=0)
    parser.add_argument("--max-origins-per-epoch", type=int, default=0)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--amp-dtype", choices=("bf16", "bfloat16", "fp16", "float16", "float32"), default=TrainConfig().amp_dtype)
    parser.add_argument("--telemetry-seconds", type=float, default=5.0)
    parser.add_argument("--print-every-batches", type=int, default=1)
    parser.add_argument("--require-all-input-coverage", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--coverage-required-keys", default="auto")
    parser.add_argument("--coverage-min-fraction", type=float, default=1e-9)
    parser.add_argument("--coverage-max-skip-batches", type=int, default=0)
    parser.add_argument("--coverage-auto-plan", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--coverage-auto-ticker-limit", type=int, default=256)
    parser.add_argument("--strict", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-name", default="")
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if str(args.mode) == "full-trainer-batches":
        return _run_full_trainer_batches(args)
    return _run_native_artifact_smoke(args)


def _run_native_artifact_smoke(args: argparse.Namespace) -> int:
    release = not bool(args.debug)
    if not bool(args.no_build) and not rust_library_path(release=release).exists():
        build_rust_library(release=release)
    run_dir = Path(args.output_root) / time.strftime("rust_native_cache_loader_%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = run_dir / "rust_native_cache_loader_profile.jsonl"
    summary_path = run_dir / "rust_native_cache_loader_summary.json"
    resolved_read_workers = _resolve_read_workers(int(args.read_workers))
    resolved_ticker_limit = _resolve_ticker_limit(int(args.ticker_limit), resolved_read_workers)
    config = RustNativeCacheProfileConfig(
        cache_root=Path(args.cache_root),
        month=str(args.month),
        ticker_limit=resolved_ticker_limit,
        batch_size=int(args.batch_size),
        max_batches=int(args.batches),
        event_stream_len=int(args.event_stream_len),
        read_workers=resolved_read_workers,
        strict=bool(args.strict),
    )
    header = {
        "profile_goal": "native_artifact_smoke",
        "cache_root": str(config.cache_root),
        "month": str(config.month),
        "ticker_limit_requested": int(args.ticker_limit),
        "ticker_limit": int(config.ticker_limit),
        "batch_size": int(config.batch_size),
        "batches": int(config.max_batches),
        "event_stream_len": int(config.event_stream_len),
        "read_workers_requested": int(args.read_workers),
        "read_workers": int(config.read_workers),
        "strict": bool(config.strict),
        "library": str(rust_library_path(release=release)),
        "version": rust_version(),
    }
    print(f"RUST NATIVE CACHE LOADER PROFILE {run_dir}", flush=True)
    print(json.dumps(header, sort_keys=True), flush=True)
    started = time.perf_counter()
    try:
        stats = profile_rust_native_cache(config, build_if_missing=not bool(args.no_build), release=release)
    except KeyboardInterrupt:
        print("Interrupted; stopping native Rust cache profile.", flush=True)
        return 130
    row = {
        "utc": _now_iso(),
        "wall_seconds": float(time.perf_counter() - started),
        "config": asdict(config) | {"cache_root": str(config.cache_root)},
        "stats": stats.to_dict(),
    }
    _append_jsonl(jsonl_path, row)
    summary = {"header": header, "stats": stats.to_dict(), "status": "ok" if int(stats.status) == 0 else "error"}
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print("SUMMARY", json.dumps(summary["stats"], sort_keys=True), flush=True)
    print(
        "NATIVE_THROUGHPUT "
        f"packages={stats.packages_processed:,}/{stats.packages_discovered:,} "
        f"parts={stats.parts_processed:,} samples={stats.samples:,} batches={stats.batches:,} "
        f"elapsed={stats.elapsed_seconds:.3f}s samples/s={stats.samples_per_second:,.1f} "
        f"parquet_files={stats.parquet_files_opened:,} rows_seen={stats.parquet_rows_seen:,} "
        f"event_rows={stats.event_rows:,} origin_rows={stats.origin_rows:,} "
        f"text_selected={stats.text_selected:,} xbrl_selected={stats.xbrl_selected:,} "
        f"corporate_selected={stats.corporate_action_selected:,} scanner_rows={stats.scanner_rows:,} "
        f"invalid={stats.invalid_event_windows:,} ordinal_mismatch={stats.ordinal_mismatches:,} "
        f"schema_errors={stats.schema_errors:,} io_errors={stats.io_errors:,}",
        flush=True,
    )
    return 0


def _run_full_trainer_batches(args: argparse.Namespace) -> int:
    from research.mlops.env import discover_env_files, load_env_files
    from research.temporal_event_model.v3.config import ExperimentConfig, LoaderConfig, ModelConfig, to_dict
    from research.temporal_event_model.v3.run_profile_exact_training_loader import (
        _CoverageSummary,
        _append_jsonl as _append_exact_jsonl,
        _apply_coverage_auto_plan,
        _coverage_required_keys,
        _print_batch_row,
        _profile_one_batch,
        _resolve_device,
        _run_dir,
        _split_csv,
        _start_telemetry_sampler,
        _summary_payload,
        _write_json,
    )
    from research.temporal_event_model.v3.train import (
        _batch_iterator,
        _cancel_iterator,
        _install_interrupt_handlers,
        _make_loader,
        _set_interrupt_reason,
        set_seed,
    )

    _install_interrupt_handlers()
    _set_interrupt_reason("full trainer cache loader profile")
    load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    resolved_read_workers = _resolve_loader_workers(int(args.read_workers))
    resolved_materialize_workers = _resolve_loader_workers(int(args.materialize_workers))
    resolved_scanner_workers = _resolve_scanner_workers(int(args.scanner_prefetch_workers))
    months = str(args.months or "").strip() or str(args.month)
    run_args = argparse.Namespace(
        cache_root=Path(args.cache_root),
        output_root=Path(args.output_root),
        run_name=str(args.run_name or f"full_trainer_batches_{time.strftime('%Y%m%d_%H%M%S')}"),
        split="train",
        dataset_id="rust_native_full_trainer_batches",
        months=months,
        training_days=str(args.training_days),
        start_utc=str(args.start_utc),
        end_utc=str(args.end_utc),
        tickers=str(args.tickers),
        data_groups=str(args.data_groups),
        intraday_label_horizons=str(args.intraday_label_horizons),
        batch_size=int(args.batch_size),
        warmup_batches=max(0, int(args.warmup_batches)),
        batches=max(1, int(args.batches)),
        max_origins_per_epoch=_resolve_max_origins(args),
        seed=17,
        read_workers=resolved_read_workers,
        materialize_workers=resolved_materialize_workers,
        loaded_parts_per_group=int(args.loaded_parts_per_group),
        materialize_chunk_size=int(args.materialize_chunk_size),
        prefetch_batches=int(args.prefetch_batches),
        chronological_replay=True,
        time_window_seconds=float(args.time_window_seconds),
        frontier_max_origins_per_window=int(args.frontier_max_origins_per_window),
        ticker_cache_capacity=int(args.ticker_cache_capacity),
        origin_cursor_chunk_rows=int(args.origin_cursor_chunk_rows),
        warm_all_ticker_caches=bool(args.warm_all_ticker_caches),
        scanner_index_cache_entries=int(args.scanner_index_cache_entries),
        prefetch_scanner_indexes=bool(args.prefetch_scanner_indexes),
        scanner_prefetch_workers=resolved_scanner_workers,
        device=str(args.device),
        amp_dtype=str(args.amp_dtype),
        fresh_start=True,
        telemetry_seconds=float(args.telemetry_seconds),
        print_every_batches=int(args.print_every_batches),
        require_all_input_coverage=bool(args.require_all_input_coverage),
        coverage_required_keys=str(args.coverage_required_keys),
        coverage_min_fraction=float(args.coverage_min_fraction),
        coverage_max_skip_batches=int(args.coverage_max_skip_batches),
        coverage_auto_plan=bool(args.coverage_auto_plan),
        coverage_auto_ticker_limit=int(args.coverage_auto_ticker_limit),
    )
    loader = LoaderConfig(
        cache_root=Path(run_args.cache_root),
        split=str(run_args.split),
        start_utc=str(run_args.start_utc),
        end_utc=str(run_args.end_utc),
        months=_split_csv(str(run_args.months)),
        tickers=_split_csv(str(run_args.tickers)),
        batch_size=int(run_args.batch_size),
        seed=int(run_args.seed),
        dataset_id=str(run_args.dataset_id),
        data_groups=_split_csv(str(run_args.data_groups)),
        intraday_label_horizons=_split_csv(str(run_args.intraday_label_horizons)),
        loaded_parts_per_group=int(run_args.loaded_parts_per_group),
        read_workers=int(run_args.read_workers),
        materialize_workers=int(run_args.materialize_workers),
        materialize_chunk_size=int(run_args.materialize_chunk_size),
        prefetch_batches=int(run_args.prefetch_batches),
        chronological_replay=True,
        time_window_seconds=float(run_args.time_window_seconds),
        frontier_max_origins_per_window=int(run_args.frontier_max_origins_per_window),
        ticker_cache_capacity=int(run_args.ticker_cache_capacity),
        origin_cursor_chunk_rows=int(run_args.origin_cursor_chunk_rows),
        warm_all_ticker_caches=bool(run_args.warm_all_ticker_caches),
        scanner_index_cache_entries=int(run_args.scanner_index_cache_entries),
        prefetch_scanner_indexes=bool(run_args.prefetch_scanner_indexes),
        scanner_prefetch_workers=int(run_args.scanner_prefetch_workers),
        max_origins_per_epoch=int(run_args.max_origins_per_epoch),
        training_days=_split_csv(str(run_args.training_days)),
        shuffle_parts=True,
        shuffle_within_loaded_group=True,
    )
    config = ExperimentConfig(
        model=ModelConfig(intraday_horizons=len(loader.intraday_label_horizons)),
        loader=loader,
        train=TrainConfig(seed=int(run_args.seed), amp=True, amp_dtype=str(run_args.amp_dtype), wandb_mode="disabled"),
    )
    required_coverage_keys = _coverage_required_keys(str(run_args.coverage_required_keys), config.loader.data_groups)
    coverage_plan = _apply_coverage_auto_plan(run_args, config, required_coverage_keys)
    set_seed(int(config.loader.seed))
    device = _resolve_device(str(run_args.device))
    run_dir = _run_dir(run_args)
    run_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "config": run_dir / "full_trainer_cache_loader_config.json",
        "batches": run_dir / "full_trainer_cache_loader_batches.jsonl",
        "telemetry": run_dir / "full_trainer_cache_loader_telemetry.jsonl",
        "summary": run_dir / "full_trainer_cache_loader_summary.json",
        "shapes": run_dir / "full_trainer_cache_loader_first_batch_shapes.json",
        "errors": run_dir / "fatal_error.txt",
    }
    for path in paths.values():
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    _write_json(
        paths["config"],
        {
            "args": vars(run_args),
            "config": to_dict(config),
            "coverage_plan": coverage_plan,
            "device": str(device),
            "created_utc": _now_iso(),
            "profile_goal": "cache_warmup_plus_full_trainer_batches",
        },
    )
    print(f"FULL TRAINER CACHE LOADER PROFILE {run_dir}", flush=True)
    print(
        json.dumps(
            {
                "profile_goal": "cache_warmup_plus_full_trainer_batches",
                "cache_root": str(config.loader.cache_root),
                "months": list(config.loader.months),
                "batch_size": int(config.loader.batch_size),
                "warmup_batches": int(run_args.warmup_batches),
                "measured_batches": int(run_args.batches),
                "read_workers": int(config.loader.read_workers),
                "materialize_workers": int(config.loader.materialize_workers),
                "prefetch_batches": int(config.loader.prefetch_batches),
                "device": str(device),
                "data_groups": list(config.loader.data_groups),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    loader_instance = None
    iterator = None
    telemetry_stop = None
    telemetry_thread = None
    rows: list[dict[str, Any]] = []
    coverage_summary = _CoverageSummary(required_coverage_keys)
    started = time.perf_counter()
    status = "complete"
    try:
        import threading
        import torch

        if device.type == "cuda":
            torch.set_float32_matmul_precision("high")
            torch.cuda.empty_cache()
            torch.cuda.reset_peak_memory_stats(device)
        loader_instance = _make_loader(config.loader, validation=False)
        iterator = _batch_iterator(config, loader_instance, device=device, dummy=False)
        telemetry_stop = threading.Event()
        telemetry_thread = _start_telemetry_sampler(
            loader=loader_instance,
            iterator=iterator,
            path=paths["telemetry"],
            stop=telemetry_stop,
            interval_seconds=float(run_args.telemetry_seconds),
        )
        first_shape_summary: dict[str, Any] = {}
        total_batches = int(run_args.warmup_batches) + int(run_args.batches)
        for batch_index in range(1, total_batches + 1):
            phase = "warmup" if batch_index <= int(run_args.warmup_batches) else "measure"
            row, shape_summary = _profile_one_batch(
                iterator=iterator,
                loader=loader_instance,
                phase=phase,
                batch_index=batch_index,
                device=device,
                started=started,
            )
            if shape_summary and not first_shape_summary:
                first_shape_summary = shape_summary
                _write_json(paths["shapes"], first_shape_summary)
            if phase == "measure":
                coverage_summary.add(row)
            _append_exact_jsonl(paths["batches"], row)
            rows.append(row)
            if int(run_args.print_every_batches) > 0 and (
                batch_index == 1 or batch_index % int(run_args.print_every_batches) == 0
            ):
                _print_batch_row(row, total_batches=total_batches)
        measured_rows = [row for row in rows if row.get("phase") == "measure"]
        summary = _summary_payload(
            args=run_args,
            config=config,
            rows=measured_rows,
            all_rows=rows,
            coverage=coverage_summary.summary(),
            coverage_plan=coverage_plan,
            run_dir=run_dir,
            device=device,
            status=status,
        )
        _write_json(paths["summary"], summary)
        print(
            "FULL PROFILE COMPLETE "
            f"warmup_batches={int(run_args.warmup_batches)} measured_batches={len(measured_rows)} "
            f"measured_samples={sum(int(row.get('samples', 0)) for row in measured_rows):,} "
            f"elapsed={float(summary.get('elapsed_seconds', 0.0)):.2f}s "
            f"avg_next={float(summary.get('averages', {}).get('next_batch_seconds', 0.0)):.3f}s "
            f"avg_sps={float(summary.get('averages', {}).get('samples_per_second', 0.0)):.1f}",
            flush=True,
        )
        return 0
    except KeyboardInterrupt:
        status = "interrupted"
        print(f"Interrupt received. Partial full profile is in {run_dir}", flush=True)
        return 130
    except Exception as exc:  # noqa: BLE001
        import traceback

        paths["errors"].write_text("".join(traceback.format_exception(exc)), encoding="utf-8")
        raise
    finally:
        if telemetry_stop is not None:
            telemetry_stop.set()
        if telemetry_thread is not None and telemetry_thread.is_alive():
            telemetry_thread.join(timeout=3.0)
        if iterator is not None:
            _cancel_iterator(iterator)
        elif loader_instance is not None:
            close = getattr(loader_instance, "close", None)
            if callable(close):
                close()


def _append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(row), sort_keys=True) + "\n")


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _resolve_read_workers(value: int) -> int:
    if value > 0:
        return value
    return max(1, int(os.cpu_count() or 1))


def _resolve_loader_workers(value: int) -> int:
    if value > 0:
        return value
    cpu_count = max(1, int(os.cpu_count() or 1))
    return max(1, min(64, cpu_count))


def _resolve_scanner_workers(value: int) -> int:
    if value > 0:
        return value
    cpu_count = max(1, int(os.cpu_count() or 1))
    return max(1, min(16, cpu_count))


def _resolve_max_origins(args: argparse.Namespace) -> int:
    requested = int(args.max_origins_per_epoch)
    if requested > 0:
        return requested
    total_batches = max(1, int(args.warmup_batches) + int(args.batches))
    return int(max(1_000_000, total_batches * int(args.batch_size) * 20))


def _resolve_ticker_limit(value: int, read_workers: int) -> int:
    if value > 0:
        return value
    return max(1, int(read_workers))


if __name__ == "__main__":
    raise SystemExit(main())
