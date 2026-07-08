from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

import numpy as np
import torch

REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.checkpoints import atomic_torch_save
from research.mlops.clickhouse import default_clickhouse_password, default_clickhouse_url, default_clickhouse_user
from research.mlops.env import discover_env_files, load_env_files
from research.mlops.manifest import write_run_manifest
from research.mlops.model_artifacts import parameter_summary, write_model_artifacts
from research.mlops.paths import RunPaths, default_run_root
from research.mlops.rolling_loader.daily_index_batch_audit import DailyIndexBatchAuditConfig, DailyIndexBatchAuditor
from research.temporal_event_model.v3 import MODEL_FAMILY, MODEL_VERSION
from research.temporal_event_model.v3.config import ExperimentConfig, LoaderConfig, ModelConfig, TrainConfig, default_run_name, to_dict
from research.temporal_event_model.v3.data import batch_to_torch, loader_config_from_v3, make_dummy_temporal_batch
from research.temporal_event_model.v3.losses import compute_loss
from research.temporal_event_model.v3.metrics import fast_batch_metrics, prediction_metrics
from research.temporal_event_model.v3.model import TemporalEventModelV3, build_model_mermaid
from research.temporal_event_model.v3.train import _amp_dtype, _input_contract, _output_contract, checkpoint_rng_state, restore_checkpoint_rng_state, set_seed

JOB_TYPE = "profile"


def parse_args() -> argparse.Namespace:
    default_model = ModelConfig()
    default_loader = LoaderConfig()
    default_train = TrainConfig()
    parser = argparse.ArgumentParser(description="Profile temporal_event_model v3 training on the daily-index rolling cache.")
    parser.add_argument("--cache-root", default=str(default_loader.cache_root))
    parser.add_argument("--output-root", default="D:/TradingML/runtimes/temporal_event_model/v3/profile")
    parser.add_argument("--run-name", default="")
    parser.add_argument("--dataset-id", default=default_loader.dataset_id)
    parser.add_argument("--split", default=default_loader.split)
    parser.add_argument("--months", default="2019-02")
    parser.add_argument("--tickers", default="")
    parser.add_argument("--data-groups", default=",".join(default_loader.data_groups))
    parser.add_argument("--intraday-label-horizons", default=",".join(default_loader.intraday_label_horizons))
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--warmup-batches", type=int, default=1)
    parser.add_argument("--batches", type=int, default=8)
    parser.add_argument("--max-origins-per-epoch", type=int, default=200_000)
    parser.add_argument("--read-workers", type=int, default=4)
    parser.add_argument("--materialize-workers", type=int, default=8)
    parser.add_argument("--loaded-parts-per-group", type=int, default=8)
    parser.add_argument("--materialize-chunk-size", type=int, default=0)
    parser.add_argument("--d-model", type=int, default=default_model.d_model)
    parser.add_argument("--event-layers", type=int, default=default_model.event_layers)
    parser.add_argument("--event-heads", type=int, default=default_model.event_heads)
    parser.add_argument("--fusion-layers", type=int, default=default_model.fusion_layers)
    parser.add_argument("--fusion-heads", type=int, default=default_model.fusion_heads)
    parser.add_argument("--dropout", type=float, default=default_model.dropout)
    parser.add_argument("--learning-rate", type=float, default=default_train.learning_rate)
    parser.add_argument("--weight-decay", type=float, default=default_train.weight_decay)
    parser.add_argument("--grad-clip-norm", type=float, default=default_train.grad_clip_norm)
    parser.add_argument("--seed", type=int, default=default_train.seed)
    parser.add_argument("--amp", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--amp-dtype", choices=("bf16", "bfloat16", "fp16", "float16", "float32"), default=default_train.amp_dtype)
    parser.add_argument("--compile-model", action="store_true")
    parser.add_argument("--checkpoint-every-batches", type=int, default=2)
    parser.add_argument("--resume-checkpoint", default="")
    parser.add_argument("--fresh-start", action="store_true")
    parser.add_argument("--skip-model-artifacts", action="store_true")
    parser.add_argument("--report-name", default="training_profile.jsonl")
    parser.add_argument("--audit-profile-batches", type=int, default=2, help="Measured profile batches to audit. Set 0 to disable.")
    parser.add_argument("--audit-samples-per-batch", type=int, default=10)
    parser.add_argument("--audit-strict", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--audit-report-name", default="training_profile_batch_audit.jsonl")
    parser.add_argument("--audit-summary-name", default="training_profile_batch_audit_summary.json")
    parser.add_argument("--audit-clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--audit-clickhouse-user", default=default_clickhouse_user())
    parser.add_argument("--audit-clickhouse-password", default=default_clickhouse_password())
    parser.add_argument("--audit-database", default="market_sip_compact")
    parser.add_argument("--audit-events-table", default="events")
    parser.add_argument("--audit-source-event-limit", type=int, default=250_000)
    parser.add_argument("--audit-rest-samples", type=int, default=2, help="Massive REST spot checks across the profile audit. Set 0 to keep profiling local/ClickHouse-only.")
    parser.add_argument("--audit-massive-base-url", default="https://api.massive.com")
    parser.add_argument("--audit-massive-api-key-env", default="MASSIVE_API_KEY")
    parser.add_argument("--coverage-mode", choices=("off", "require-requested"), default="require-requested", help="Require measured batches to contain real payloads for every requested input modality.")
    parser.add_argument("--coverage-min-fraction", type=float, default=1e-9, help="Minimum per-batch available fraction for each required modality. Default means at least one sample.")
    parser.add_argument("--coverage-max-skip-batches", type=int, default=512, help="Maximum batches to scan while seeking a fully covered profile batch.")
    parser.add_argument("--coverage-required-keys", default="auto", help="Comma-separated input_availability keys to require, or auto from --data-groups.")
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text", "none"), default="auto")
    return parser.parse_args()


def main() -> int:
    load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    config = _config_from_args(args)
    config.train.run_name = _profile_run_name(config, args)
    run_root = Path(args.output_root) / config.train.run_name if args.output_root else default_run_root(MODEL_FAMILY, MODEL_VERSION, JOB_TYPE, config.train.run_name)
    paths = RunPaths.create(run_root)
    report_path = paths.run_root / str(args.report_name)
    summary_path = paths.run_root / "training_profile_summary.json"
    audit_report_path = paths.run_root / str(args.audit_report_name)
    audit_summary_path = paths.run_root / str(args.audit_summary_name)
    checkpoint_path = paths.checkpoints_dir / "profile_checkpoint_latest.pt"
    error_path = paths.logs_dir / "fatal_error.txt"
    if args.fresh_start:
        for stale_path in (report_path, summary_path, audit_report_path, audit_summary_path, checkpoint_path, error_path):
            try:
                stale_path.unlink()
            except FileNotFoundError:
                pass
    _write_json(paths.run_root / "config.json", to_dict(config))
    set_seed(int(config.train.seed))
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if torch.cuda.is_available():
        torch.set_float32_matmul_precision("high")
    model = TemporalEventModelV3(config.model).to(device)
    if bool(config.train.compile_model):
        model = torch.compile(model)  # type: ignore[assignment]
    model_parameters = parameter_summary(_unwrap_model(model))
    optimizer = torch.optim.AdamW(model.parameters(), lr=float(config.train.learning_rate), weight_decay=float(config.train.weight_decay))
    scaler = torch.amp.GradScaler("cuda", enabled=bool(config.train.amp and config.train.amp_dtype in {"fp16", "float16"} and device.type == "cuda"))
    loader = _make_loader(config.loader)
    state = {
        "batch_index": 0,
        "measured_batches": 0,
        "measured_samples": 0,
        "started_at": _now_iso(),
        "last_checkpoint": "",
    }
    resumed = False
    if args.resume_checkpoint and not args.fresh_start:
        state = _restore_profile_checkpoint(Path(args.resume_checkpoint), model, optimizer, scaler, loader, device)
        resumed = True
    elif checkpoint_path.exists() and not args.fresh_start:
        state = _restore_profile_checkpoint(checkpoint_path, model, optimizer, scaler, loader, device)
        resumed = True
    elif report_path.exists():
        report_path.unlink()
    write_run_manifest(
        paths.manifest_path,
        repo_root=REPO_ROOT,
        model_family=MODEL_FAMILY,
        version=MODEL_VERSION,
        job_type=JOB_TYPE,
        run_name=config.train.run_name,
        args=vars(args),
        config=to_dict(config),
        data_roots={"cache_root": str(config.loader.cache_root)},
        output_root=paths.run_root,
        source_checkpoint=Path(args.resume_checkpoint) if args.resume_checkpoint else None,
        wandb_info={"mode": "disabled", "project": ""},
    )
    if not bool(args.skip_model_artifacts):
        write_model_artifacts(
            model=_unwrap_model(model),
            artifact_dir=paths.artifacts_dir / "model",
            model_config=config.model,
            input_contract=_input_contract(config.model),
            output_contract=_output_contract(config.model),
            architecture_mermaid=build_model_mermaid(),
            summary_notes="Temporal v3 training profiler artifact export.",
            dummy_input_factory=lambda: ((), make_dummy_temporal_batch(model_config=config.model, batch_size=2, device=device).x),
        )
    total_batches = max(0, int(args.warmup_batches)) + max(1, int(args.batches))
    raw_iter = loader.iter_batches()
    rows: list[dict[str, Any]] = _load_existing_measured_rows(report_path) if resumed else []
    first_batch_summary: dict[str, Any] = {}
    reporter = _ProfileReporter(args.progress_layout, total_batches=total_batches, run_root=paths.run_root)
    coverage_gate = _make_coverage_gate(args, config)
    auditor = _make_batch_auditor(args, audit_report_path=audit_report_path, audit_summary_path=audit_summary_path)
    try:
        reporter.start()
        while int(state["batch_index"]) < total_batches:
            batch_number = int(state["batch_index"]) + 1
            phase = "warmup" if int(state["batch_index"]) < int(args.warmup_batches) else "measure"
            row, first_summary = _run_profile_batch(
                raw_iter=raw_iter,
                model=model,
                optimizer=optimizer,
                scaler=scaler,
                config=config,
                device=device,
                phase=phase,
                batch_number=batch_number,
                coverage_gate=coverage_gate,
                auditor=auditor,
            )
            if first_summary and not first_batch_summary:
                first_batch_summary = first_summary
            state["batch_index"] = batch_number
            if phase == "measure":
                state["measured_batches"] = int(state["measured_batches"]) + 1
                state["measured_samples"] = int(state["measured_samples"]) + int(row.get("samples", 0))
                rows.append(row)
            _append_jsonl(report_path, row)
            reporter.update(row, state)
            if _should_checkpoint(args, state, total_batches):
                _save_profile_checkpoint(checkpoint_path, model, optimizer, scaler, loader, config, state)
                state["last_checkpoint"] = str(checkpoint_path)
        summary = _summary_payload(config, args, rows, state, first_batch_summary, paths.run_root, model_parameters=model_parameters, audit_summary=auditor.summary())
        _write_json(summary_path, summary)
        _save_profile_checkpoint(checkpoint_path, model, optimizer, scaler, loader, config, state)
        reporter.finish(summary)
        return 0
    except KeyboardInterrupt:
        state["interrupted_at"] = _now_iso()
        _save_profile_checkpoint(checkpoint_path, model, optimizer, scaler, loader, config, state)
        _write_json(summary_path, _summary_payload(config, args, rows, state, first_batch_summary, paths.run_root, status="interrupted", model_parameters=model_parameters, audit_summary=auditor.summary()))
        reporter.message(f"Interrupt received. Saved restart checkpoint: {checkpoint_path}")
        return 130
    except Exception as exc:  # noqa: BLE001
        error_path.write_text("".join(traceback.format_exception(exc)), encoding="utf-8")
        state["failed_at"] = _now_iso()
        state["error"] = repr(exc)
        _save_profile_checkpoint(checkpoint_path, model, optimizer, scaler, loader, config, state)
        _write_json(summary_path, _summary_payload(config, args, rows, state, first_batch_summary, paths.run_root, status="error", model_parameters=model_parameters, audit_summary=auditor.summary()))
        reporter.message(f"ERROR: {exc!r}")
        raise
    finally:
        try:
            raw_iter.close()  # type: ignore[attr-defined]
        except Exception:
            pass
        reporter.stop()
        if device.type == "cuda":
            torch.cuda.empty_cache()


def _run_profile_batch(
    *,
    raw_iter: Iterator[Any],
    model: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    scaler: torch.amp.GradScaler,
    config: ExperimentConfig,
    device: torch.device,
    phase: str,
    batch_number: int,
    coverage_gate: Mapping[str, Any],
    auditor: DailyIndexBatchAuditor | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats(device)
    seek_start = time.perf_counter()
    skipped_batches = 0
    missing_coverage: dict[str, float] = {}
    while True:
        load_start = time.perf_counter()
        raw_batch = next(raw_iter)
        loader_wait = time.perf_counter() - load_start
        coverage = _batch_coverage(raw_batch)
        ok, missing_coverage = _coverage_ok(coverage, coverage_gate)
        if ok:
            break
        skipped_batches += 1
        if skipped_batches > int(coverage_gate.get("max_skip_batches", 0)):
            required = ", ".join(str(key) for key in coverage_gate.get("required_keys", ()))
            observed = ", ".join(f"{key}={value:.4f}" for key, value in sorted(coverage.items()))
            missing = ", ".join(f"{key}={value:.4f}" for key, value in sorted(missing_coverage.items()))
            raise RuntimeError(
                "Could not find a fully covered profile batch. "
                f"required=[{required}] missing=[{missing}] observed_last_batch=[{observed}] "
                f"after_skipped_batches={skipped_batches:,}. Select a cache/month/ticker slice with these contexts or lower coverage requirements."
            )
    seek_seconds = time.perf_counter() - seek_start
    step_start = time.perf_counter()
    audit_metrics: dict[str, Any] = {}
    if auditor is not None:
        audit_metrics = auditor.audit_batch(raw_batch, batch_number=int(batch_number), phase=str(phase))
    convert_start = time.perf_counter()
    batch = batch_to_torch(raw_batch, model_config=config.model, device=device)
    if device.type == "cuda":
        torch.cuda.synchronize()
    host_to_device = time.perf_counter() - convert_start
    optimizer.zero_grad(set_to_none=True)
    amp_dtype = _amp_dtype(config.train.amp_dtype)
    forward_start = time.perf_counter()
    with torch.autocast(device_type=device.type, dtype=amp_dtype, enabled=bool(config.train.amp and device.type == "cuda")):
        output = model(batch.x)
    if device.type == "cuda":
        torch.cuda.synchronize()
    forward_seconds = time.perf_counter() - forward_start
    loss_start = time.perf_counter()
    with torch.autocast(device_type=device.type, dtype=amp_dtype, enabled=bool(config.train.amp and device.type == "cuda")):
        loss_result = compute_loss(output, batch)
        loss = loss_result.loss
    if device.type == "cuda":
        torch.cuda.synchronize()
    loss_seconds = time.perf_counter() - loss_start
    backward_start = time.perf_counter()
    if scaler.is_enabled():
        scaler.scale(loss).backward()
        if float(config.train.grad_clip_norm) > 0:
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(model.parameters(), float(config.train.grad_clip_norm))
    else:
        loss.backward()
        if float(config.train.grad_clip_norm) > 0:
            torch.nn.utils.clip_grad_norm_(model.parameters(), float(config.train.grad_clip_norm))
    if device.type == "cuda":
        torch.cuda.synchronize()
    backward_seconds = time.perf_counter() - backward_start
    optimizer_start = time.perf_counter()
    if scaler.is_enabled():
        scaler.step(optimizer)
        scaler.update()
    else:
        optimizer.step()
    if device.type == "cuda":
        torch.cuda.synchronize()
    optimizer_seconds = time.perf_counter() - optimizer_start
    total_seconds = time.perf_counter() - step_start
    row: dict[str, Any] = {
        "utc": _now_iso(),
        "phase": phase,
        "batch": int(batch_number),
        "samples": int(batch.sample_count),
        "loss": float(loss.detach().float().cpu()),
        "active_task_count": float(loss_result.metrics.get("train/active_task_count", 0.0)),
        "loader_wait_seconds": float(loader_wait),
        "coverage_seek_seconds": float(seek_seconds),
        "coverage_skipped_batches": int(skipped_batches),
        "host_to_device_seconds": float(host_to_device),
        "forward_seconds": float(forward_seconds),
        "loss_seconds": float(loss_seconds),
        "backward_seconds": float(backward_seconds),
        "optimizer_seconds": float(optimizer_seconds),
        "step_seconds": float(total_seconds),
        "samples_per_second": float(batch.sample_count / max(total_seconds, 1e-9)),
        "cpu_rss_gib": _rss_gib(),
        "gpu_memory_allocated_gib": _gpu_memory_allocated_gib(device),
        "gpu_memory_reserved_gib": _gpu_memory_reserved_gib(device),
        "gpu_memory_peak_gib": _gpu_memory_peak_gib(device),
    }
    row.update({f"loss/{key.removeprefix('train/')}": value for key, value in loss_result.metrics.items() if isinstance(value, (int, float))})
    row.update({f"loader/{key}": value for key, value in batch.profile.items() if isinstance(value, (int, float))})
    row.update({f"coverage/{key}": value for key, value in coverage.items()})
    row.update({f"audit/{key}": value for key, value in audit_metrics.items() if isinstance(value, (int, float, bool))})
    row.update(fast_batch_metrics(batch, output, prefix="batch"))
    row.update(prediction_metrics(batch, output, prefix="batch"))
    first_summary = _batch_shape_summary(batch) if int(batch_number) == 1 else {}
    return row, first_summary


def _config_from_args(args: argparse.Namespace) -> ExperimentConfig:
    intraday_label_horizons = _split_csv(args.intraday_label_horizons)
    model = ModelConfig(
        d_model=int(args.d_model),
        event_layers=int(args.event_layers),
        event_heads=int(args.event_heads),
        fusion_layers=int(args.fusion_layers),
        fusion_heads=int(args.fusion_heads),
        dropout=float(args.dropout),
        intraday_horizons=len(intraday_label_horizons),
    )
    loader = LoaderConfig(
        cache_root=Path(args.cache_root),
        split=str(args.split),
        months=_split_csv(args.months),
        tickers=_split_csv(args.tickers),
        batch_size=int(args.batch_size),
        seed=int(args.seed),
        dataset_id=str(args.dataset_id),
        data_groups=_split_csv(args.data_groups),
        intraday_label_horizons=intraday_label_horizons,
        read_workers=int(args.read_workers),
        materialize_workers=int(args.materialize_workers),
        loaded_parts_per_group=int(args.loaded_parts_per_group),
        materialize_chunk_size=int(args.materialize_chunk_size),
        max_origins_per_epoch=int(args.max_origins_per_epoch),
        shuffle_parts=True,
        shuffle_within_loaded_group=True,
    )
    train = TrainConfig(
        run_name=str(args.run_name),
        output_root=Path(args.output_root),
        learning_rate=float(args.learning_rate),
        weight_decay=float(args.weight_decay),
        grad_clip_norm=float(args.grad_clip_norm),
        amp=bool(args.amp),
        amp_dtype=str(args.amp_dtype),
        compile_model=bool(args.compile_model),
        seed=int(args.seed),
        wandb_mode="disabled",
    )
    return ExperimentConfig(model=model, loader=loader, train=train)


def _make_loader(config: LoaderConfig) -> Any:
    from research.mlops.rolling_loader.daily_index_dataset import AsyncDailyIndexBatchLoader

    return AsyncDailyIndexBatchLoader(loader_config_from_v3(config))


def _make_coverage_gate(args: argparse.Namespace, config: ExperimentConfig) -> dict[str, Any]:
    required_keys = _coverage_required_keys(args.coverage_required_keys, config.loader.data_groups)
    if str(args.coverage_mode) == "off":
        required_keys = ()
    return {
        "enabled": bool(required_keys),
        "required_keys": tuple(required_keys),
        "min_fraction": max(0.0, min(1.0, float(args.coverage_min_fraction))),
        "max_skip_batches": max(0, int(args.coverage_max_skip_batches)),
    }


def _coverage_required_keys(value: str, data_groups: tuple[str, ...]) -> tuple[str, ...]:
    text = str(value or "").strip()
    if text and text.lower() != "auto":
        return _split_csv(text)
    group_to_key = {
        "events": "event_context_available",
        "intraday_labels": "intraday_labels_available",
        "intraday_bars": "ticker_intraday_bars_available",
        "daily_bars": "ticker_daily_bars_available",
        "global_daily_bars": "global_daily_bars_available",
        "ticker_news_embeddings": "ticker_news_available",
        "market_news_embeddings": "market_news_available",
        "sec_filing_embeddings": "sec_filings_available",
        "xbrl": "xbrl_available",
        "corporate_actions": "corporate_actions_available",
    }
    required: list[str] = []
    for group in data_groups:
        key = group_to_key.get(str(group))
        if key and key not in required:
            required.append(key)
    return tuple(required)


def _batch_coverage(raw_batch: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    sample_count = max(1, int(getattr(raw_batch, "sample_count", 0) or 0))
    for key, value in getattr(raw_batch, "input_availability", {}).items():
        arr = np.asarray(value)
        if arr.size == 0:
            out[str(key)] = 0.0
            continue
        if arr.shape[:1] == (sample_count,):
            reduced = arr.reshape((sample_count, -1)).any(axis=1)
            out[str(key)] = float(np.mean(reduced.astype(np.float32)))
        else:
            out[str(key)] = float(bool(np.any(arr)))
    return out


def _coverage_ok(coverage: Mapping[str, float], gate: Mapping[str, Any]) -> tuple[bool, dict[str, float]]:
    if not bool(gate.get("enabled", False)):
        return True, {}
    minimum = float(gate.get("min_fraction", 0.0))
    missing = {
        str(key): float(coverage.get(str(key), 0.0))
        for key in tuple(gate.get("required_keys", ()))
        if float(coverage.get(str(key), 0.0)) < minimum
    }
    return not missing, missing


def _make_batch_auditor(args: argparse.Namespace, *, audit_report_path: Path, audit_summary_path: Path) -> DailyIndexBatchAuditor:
    enabled = int(args.audit_profile_batches) > 0 and int(args.audit_samples_per_batch) > 0
    coverage_gate = _make_coverage_gate(args, _config_from_args(args))
    return DailyIndexBatchAuditor(
        DailyIndexBatchAuditConfig(
            enabled=enabled,
            strict=bool(args.audit_strict),
            max_batches=max(0, int(args.audit_profile_batches)),
            samples_per_batch=max(0, int(args.audit_samples_per_batch)),
            seed=int(args.seed),
            report_path=audit_report_path if enabled else None,
            summary_path=audit_summary_path if enabled else None,
            clickhouse_url=str(args.audit_clickhouse_url),
            clickhouse_user=str(args.audit_clickhouse_user),
            clickhouse_password=str(args.audit_clickhouse_password),
            database=str(args.audit_database),
            events_table=str(args.audit_events_table),
            source_event_limit=max(1_024, int(args.audit_source_event_limit)),
            rest_samples=max(0, int(args.audit_rest_samples)),
            massive_base_url=str(args.audit_massive_base_url),
            massive_api_key_env=str(args.audit_massive_api_key_env),
            required_availability_keys=tuple(str(key) for key in coverage_gate.get("required_keys", ())),
            required_availability_min_fraction=float(coverage_gate.get("min_fraction", 0.0)),
        )
    )


def _profile_run_name(config: ExperimentConfig, args: argparse.Namespace) -> str:
    if args.run_name:
        return str(args.run_name)
    base = default_run_name(config).replace("v3-temporal-", "v3-profile-")
    return f"{base}-profile-bs{config.loader.batch_size}-b{int(args.batches)}"


def _restore_profile_checkpoint(path: Path, model: torch.nn.Module, optimizer: torch.optim.Optimizer, scaler: torch.amp.GradScaler, loader: Any, device: torch.device) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing profile checkpoint: {path}")
    ckpt = torch.load(path, map_location=device, weights_only=False)
    _unwrap_model(model).load_state_dict(ckpt["model"])
    optimizer.load_state_dict(ckpt["optimizer"])
    if ckpt.get("scaler") and scaler.is_enabled():
        scaler.load_state_dict(ckpt["scaler"])
    if ckpt.get("loader_state"):
        loader.load_state_dict(ckpt["loader_state"])
    restore_checkpoint_rng_state(ckpt.get("rng_state"))
    return dict(ckpt.get("profile_state") or {})


def _save_profile_checkpoint(path: Path, model: torch.nn.Module, optimizer: torch.optim.Optimizer, scaler: torch.amp.GradScaler, loader: Any, config: ExperimentConfig, state: Mapping[str, Any]) -> None:
    payload = {
        "model": _unwrap_model(model).state_dict(),
        "optimizer": optimizer.state_dict(),
        "scaler": scaler.state_dict() if scaler.is_enabled() else None,
        "config": to_dict(config),
        "profile_state": dict(state),
        "loader_state": loader.state_dict() if loader is not None else {},
        "rng_state": checkpoint_rng_state(),
    }
    atomic_torch_save(payload, path)


def _should_checkpoint(args: argparse.Namespace, state: Mapping[str, Any], total_batches: int) -> bool:
    every = int(args.checkpoint_every_batches)
    index = int(state.get("batch_index", 0))
    return index >= total_batches or (every > 0 and index % every == 0)


def _summary_payload(
    config: ExperimentConfig,
    args: argparse.Namespace,
    rows: list[dict[str, Any]],
    state: Mapping[str, Any],
    first_batch_summary: Mapping[str, Any],
    run_root: Path,
    *,
    status: str = "complete",
    model_parameters: Mapping[str, Any] | None = None,
    audit_summary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    numeric_keys = sorted({key for row in rows for key, value in row.items() if isinstance(value, (int, float))})
    averages = {key: float(np.mean([float(row[key]) for row in rows if key in row])) for key in numeric_keys}
    p95 = {key: float(np.percentile([float(row[key]) for row in rows if key in row], 95)) for key in numeric_keys}
    return {
        "status": status,
        "created_at": _now_iso(),
        "run_root": str(run_root),
        "cache_root": str(config.loader.cache_root),
        "months": list(config.loader.months),
        "data_groups": list(config.loader.data_groups),
        "warmup_batches": int(args.warmup_batches),
        "measured_batches": int(state.get("measured_batches", len(rows))),
        "measured_samples": int(state.get("measured_samples", sum(int(row.get("samples", 0)) for row in rows))),
        "model_parameters": dict(model_parameters or {}),
        "audit": dict(audit_summary or {}),
        "averages": averages,
        "p95": p95,
        "first_batch": dict(first_batch_summary),
        "state": dict(state),
    }


def _batch_shape_summary(batch: Any) -> dict[str, Any]:
    return {
        "sample_count": int(batch.sample_count),
        "identity": {key: _shape_value(value) for key, value in batch.identity.items()},
        "x": _shape_tree(batch.x),
        "y": _shape_tree(batch.y),
        "profile_keys": sorted(batch.profile.keys()),
    }


def _shape_tree(value: Any) -> Any:
    if torch.is_tensor(value):
        return {"shape": list(value.shape), "dtype": str(value.dtype).replace("torch.", "")}
    if isinstance(value, np.ndarray):
        return {"shape": list(value.shape), "dtype": str(value.dtype)}
    if isinstance(value, Mapping):
        return {str(key): _shape_tree(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return list(value)
    return str(type(value).__name__)


def _shape_value(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        preview = value[: min(3, int(value.shape[0]))].tolist() if value.ndim else value.item()
        return {"shape": list(value.shape), "dtype": str(value.dtype), "preview": preview}
    return _shape_tree(value)


def _append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(payload), sort_keys=True, default=str) + "\n")


def _load_existing_measured_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError:
                continue
            if row.get("phase") == "measure":
                rows.append(dict(row))
    return rows


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), indent=2, sort_keys=True, default=str), encoding="utf-8")


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in str(value or "").split(",") if part.strip())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _unwrap_model(model: torch.nn.Module) -> torch.nn.Module:
    return getattr(model, "_orig_mod", model)


def _rss_gib() -> float:
    try:
        import psutil  # type: ignore

        return float(psutil.Process(os.getpid()).memory_info().rss / (1024**3))
    except Exception:
        return 0.0


def _gpu_memory_allocated_gib(device: torch.device) -> float:
    return float(torch.cuda.memory_allocated(device) / (1024**3)) if device.type == "cuda" else 0.0


def _gpu_memory_reserved_gib(device: torch.device) -> float:
    return float(torch.cuda.memory_reserved(device) / (1024**3)) if device.type == "cuda" else 0.0


def _gpu_memory_peak_gib(device: torch.device) -> float:
    return float(torch.cuda.max_memory_allocated(device) / (1024**3)) if device.type == "cuda" else 0.0


class _ProfileReporter:
    def __init__(self, layout: str, *, total_batches: int, run_root: Path) -> None:
        self.layout = "rich" if layout == "auto" else str(layout)
        self.total_batches = int(total_batches)
        self.run_root = run_root
        self.live: Any | None = None
        self.last_row: dict[str, Any] = {}
        self.state: Mapping[str, Any] = {}
        self.started = time.perf_counter()

    def start(self) -> None:
        if self.layout != "rich":
            self.message(f"TEMPORAL V3 TRAINING PROFILE {self.run_root}")
            return
        try:
            from rich.live import Live

            self.live = Live(self._render(), refresh_per_second=2, transient=False)
            self.live.start()
        except Exception:
            self.layout = "text"
            self.message(f"TEMPORAL V3 TRAINING PROFILE {self.run_root}")

    def update(self, row: Mapping[str, Any], state: Mapping[str, Any]) -> None:
        self.last_row = dict(row)
        self.state = dict(state)
        if self.live is not None:
            self.live.update(self._render())
        elif self.layout != "none":
            self.message(
                f"{row.get('phase')} batch={row.get('batch')}/{self.total_batches} "
                f"samples={row.get('samples')} loss={float(row.get('loss', 0.0)):.5f} "
                f"sps={float(row.get('samples_per_second', 0.0)):.1f} "
                f"step={float(row.get('step_seconds', 0.0)):.3f}s"
            )

    def finish(self, summary: Mapping[str, Any]) -> None:
        self.message(
            "PROFILE COMPLETE "
            f"batches={summary.get('measured_batches')} samples={summary.get('measured_samples')} "
            f"avg_sps={float(summary.get('averages', {}).get('samples_per_second', 0.0)):.1f}"
        )

    def stop(self) -> None:
        if self.live is not None:
            self.live.stop()
            self.live = None

    def message(self, text: str) -> None:
        if self.layout != "none":
            print(text, flush=True)

    def _render(self) -> Any:
        from rich import box
        from rich.console import Group
        from rich.panel import Panel
        from rich.table import Table

        row = self.last_row
        state = self.state
        completed = int(state.get("batch_index", 0))
        elapsed = max(time.perf_counter() - self.started, 1e-9)
        rate = completed / elapsed
        remain = max(0, self.total_batches - completed)
        eta = remain / max(rate, 1e-9)
        summary = Table.grid(expand=True)
        summary.add_column(ratio=1)
        summary.add_column(ratio=1)
        summary.add_row(f"batch {completed}/{self.total_batches}", f"eta {_duration(eta)}")
        summary.add_row(f"phase {row.get('phase', '-')}", f"samples {state.get('measured_samples', 0)}")
        summary.add_row(f"run {self.run_root}", f"elapsed {_duration(elapsed)}")
        timing = Table(title="Latest Batch", box=box.ASCII, expand=True)
        timing.add_column("metric")
        timing.add_column("sec", justify="right")
        for key in ("loader_wait_seconds", "host_to_device_seconds", "forward_seconds", "loss_seconds", "backward_seconds", "optimizer_seconds", "step_seconds"):
            timing.add_row(key.replace("_seconds", ""), f"{float(row.get(key, 0.0)):.3f}")
        loader = Table(title="Loader Stages", box=box.ASCII, expand=True)
        loader.add_column("stage")
        loader.add_column("sec", justify="right")
        for key in sorted(k for k in row if k.startswith("loader/") and k.endswith("_seconds"))[:18]:
            loader.add_row(key.replace("loader/", "").replace("_seconds", ""), f"{float(row.get(key, 0.0)):.3f}")
        mem = Table(title="Memory", box=box.ASCII, expand=True)
        mem.add_column("metric")
        mem.add_column("GiB", justify="right")
        for key in ("cpu_rss_gib", "gpu_memory_allocated_gib", "gpu_memory_reserved_gib", "gpu_memory_peak_gib"):
            mem.add_row(key.replace("_gib", ""), f"{float(row.get(key, 0.0)):.2f}")
        return Group(
            Panel(summary, title="Temporal v3 Training Profile", border_style="cyan", box=box.ASCII),
            Panel(timing, title="Timing", border_style="green", box=box.ASCII),
            Panel(loader, title="Loader Detail", border_style="blue", box=box.ASCII),
            Panel(mem, title="Memory", border_style="magenta", box=box.ASCII),
        )


def _duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


if __name__ == "__main__":
    raise SystemExit(main())
