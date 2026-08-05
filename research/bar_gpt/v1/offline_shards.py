from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import multiprocessing as mp
import os
import queue
import sys
import time
import traceback
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import torch

from research.bar_gpt.v1.config import DataConfig
from research.bar_gpt.v1.data import (
    AUTOREGRESSIVE_VIEW_NAMES,
    TIMEFRAME_US_BY_NAME,
    BarGPTBatch,
    BarGPTExample,
    _pad_first_dimension,
)
from research.bar_gpt.v1.features import project_stationary_features
from research.bar_gpt.v1.loader import BarGPTIterableDataset, month_units
from research.bar_gpt.v1.targets import build_next_bar_targets, build_physical_horizon_targets
from research.bar_gpt.v1.train import _stream_config, preflight
from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    discover_clickhouse_env_files,
)
from research.mlops.env import load_env_files


OFFLINE_SHARD_CONTRACT_VERSION = 1
DEFAULT_OUTPUT_ROOT = Path(r"D:\TradingML\runtimes\bar_gpt\v1\offline_shards_v1")


@dataclass(slots=True)
class CompiledBlock:
    ticker: str
    local_date: str
    views: dict[str, torch.Tensor]
    origin_indices: torch.Tensor
    origin_timestamps_us: torch.Tensor
    asof_indices: dict[str, torch.Tensor]
    autoregressive_targets: dict[str, torch.Tensor]
    autoregressive_mask: dict[str, torch.Tensor]
    horizon_targets: torch.Tensor
    horizon_mask: torch.Tensor
    activity_regime: int
    unit_index: int
    block_offset: int
    session_phase: str
    has_condition_target: bool


def _csv(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item.strip().upper() for item in value.split(",") if item.strip()))


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    defaults = DataConfig()
    parser = argparse.ArgumentParser(
        description="Compile certified BarGPT ClickHouse inputs into nonredundant mmap-ready tensor shards."
    )
    parser.add_argument("--execute", action="store_true", help="Required to write shards; omit for a safe plan.")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--tickers", default=",".join(defaults.tickers))
    parser.add_argument("--start-date", default=defaults.start_date)
    parser.add_argument("--end-date", default=defaults.end_date)
    parser.add_argument("--workers", type=int, default=min(12, max(2, (os.cpu_count() or 8) // 4)))
    parser.add_argument("--cpu-threads-per-worker", type=int, default=0, help="Zero auto-partitions CPU threads across workers.")
    parser.add_argument("--origin-bars-1s", type=int, default=4096)
    parser.add_argument("--clickhouse-query-days", type=int, default=defaults.clickhouse_query_days)
    parser.add_argument("--clickhouse-prefetch-pages", type=int, default=defaults.clickhouse_prefetch_pages)
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text"), default="auto")
    parser.add_argument("--refresh-seconds", type=float, default=0.5)
    parser.add_argument("--force-rebuild", action="store_true")
    parser.add_argument("--skip-hash", action="store_true", help="Diagnostic only; leaves shards uncertified.")
    parser.add_argument("--max-shards", type=int, default=0, help="Bounded smoke limit; zero builds the full plan.")
    parser.add_argument("--database", default=defaults.database)
    parser.add_argument("--one-second-table", default=defaults.one_second_table)
    parser.add_argument("--daily-table", default=defaults.daily_table)
    parser.add_argument("--condition-table", default=defaults.condition_table)
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.workers <= 0:
        parser.error("--workers must be positive")
    if args.cpu_threads_per_worker < 0:
        parser.error("--cpu-threads-per-worker cannot be negative")
    if args.refresh_seconds <= 0:
        parser.error("--refresh-seconds must be positive")
    if args.max_shards < 0:
        parser.error("--max-shards cannot be negative")
    return args


def build_data_config(args: argparse.Namespace) -> DataConfig:
    base = DataConfig()
    tickers = _csv(args.tickers)
    validation_start = max(str(args.start_date), min(base.validation_start_date, str(args.end_date)))
    validation_slices = tuple(
        value for value in base.validation_slices
        if value[0] in tickers and validation_start <= value[1] and value[2] <= str(args.end_date)
    )
    config = dataclasses.replace(
        base,
        database=str(args.database),
        one_second_table=str(args.one_second_table),
        daily_table=str(args.daily_table),
        condition_table=str(args.condition_table),
        tickers=tickers,
        start_date=str(args.start_date),
        end_date=str(args.end_date),
        validation_start_date=validation_start,
        validation_slices=validation_slices,
        origin_bars_1s=int(args.origin_bars_1s),
        clickhouse_query_days=int(args.clickhouse_query_days),
        clickhouse_prefetch_pages=int(args.clickhouse_prefetch_pages),
        loader_workers=0,
        persistent_workers=False,
        balance_activity_regimes=False,
    )
    # Cache compilation includes held-out tickers and extends through end_date;
    # validation ownership remains a training concern, not a storage filter.
    config.validate()
    return config


def unit_key(ticker: str, month: str) -> str:
    return f"{ticker.upper()}:{month[:7]}"


def planned_unit_keys(config: DataConfig) -> tuple[str, ...]:
    return tuple(
        unit_key(unit.ticker, unit.start_date)
        for unit in month_units(config.start_date, config.end_date, config.tickers, seed=0)
    )


def shard_path(root: Path, key: str) -> Path:
    ticker, month = key.split(":", 1)
    return root / "tickers" / ticker / month[:4] / f"{month}.pt"


def sidecar_path(path: Path) -> Path:
    return path.with_suffix(".json")


def _canonical_config(config: DataConfig) -> dict[str, Any]:
    return dataclasses.asdict(config)


def config_hash(config: DataConfig) -> str:
    payload = {
        "contract": OFFLINE_SHARD_CONTRACT_VERSION,
        "data": _canonical_config(config),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def completed_units(root: Path, expected_hash: str) -> dict[str, dict[str, Any]]:
    completed: dict[str, dict[str, Any]] = {}
    if not root.exists():
        return completed
    for path in root.glob("tickers/*/*/*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (
            value.get("status") in {"complete", "covered_empty"}
            and value.get("config_hash") == expected_hash
            and value.get("contract_version") == OFFLINE_SHARD_CONTRACT_VERSION
        ):
            tensor_path = path.with_suffix(".pt")
            if value["status"] == "covered_empty" or tensor_path.is_file():
                completed[str(value["unit_key"])] = value
    return completed


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.partial")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb", buffering=8 * 1024 * 1024) as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _merge_view(
    examples: Sequence[BarGPTExample], name: str,
) -> tuple[dict[str, torch.Tensor], list[tuple[int, int]], list[dict[str, torch.Tensor]]]:
    starts = torch.cat([example.raw_view_start_us[name].cpu() for example in examples])
    available = torch.cat([example.raw_view_available_at_us[name].cpu() for example in examples])
    raw = torch.cat([example.raw_views[name].cpu() for example in examples])
    order = torch.argsort(starts, stable=True)
    starts = starts[order]
    available = available[order]
    raw = raw[order]
    keep = torch.ones(starts.shape[0], dtype=torch.bool)
    if starts.numel() > 1:
        keep[1:] = starts[1:] != starts[:-1]
    unique_starts = starts[keep].contiguous()
    unique_available = available[keep].contiguous()
    unique_raw = raw[keep].contiguous()
    position = {int(value): index for index, value in enumerate(unique_starts.tolist())}
    slices: list[tuple[int, int]] = []
    patches: list[dict[str, torch.Tensor]] = []
    for example in examples:
        local = example.raw_view_start_us[name].tolist()
        indices = [position[int(value)] for value in local]
        if indices != list(range(indices[0], indices[0] + len(indices))):
            raise RuntimeError(f"{example.ticker} {example.local_date} {name} is not a contiguous shared slice")
        slices.append((indices[0], len(indices)))
    projected = project_stationary_features(
        unique_raw,
        unique_starts,
        timeframe_us=TIMEFRAME_US_BY_NAME[name],
    ).contiguous()
    result: dict[str, torch.Tensor] = {
        "features": projected,
        "start_us": unique_starts,
        "available_at_us": unique_available,
    }
    if name in AUTOREGRESSIVE_VIEW_NAMES:
        targets = build_next_bar_targets(
            unique_raw,
            bar_start_us=unique_starts,
            expected_step_us=TIMEFRAME_US_BY_NAME[name],
        )
        result["autoregressive_targets"] = targets.values.contiguous()
        result["autoregressive_base_mask"] = targets.mask.contiguous()
    # Stationary projection carries prior-valid state.  Store the shared
    # session projection once and only the usually tiny prefix corrections
    # required to reproduce each independently projected training block.
    for example, (start, length) in zip(examples, slices, strict=True):
        exact = project_stationary_features(
            example.raw_views[name].cpu(),
            example.raw_view_start_us[name].cpu(),
            timeframe_us=TIMEFRAME_US_BY_NAME[name],
        )
        shared_slice = projected[start : start + length]
        changed = torch.any(exact != shared_slice, dim=-1)
        indices = torch.nonzero(changed, as_tuple=False).flatten().to(torch.int32)
        patches.append({
            "indices": indices,
            "values": exact[indices.long()].contiguous(),
        })
    return result, slices, patches


def compile_session(examples: Sequence[BarGPTExample]) -> dict[str, Any]:
    if not examples:
        raise ValueError("cannot compile an empty session")
    ticker = examples[0].ticker
    local_date = examples[0].local_date
    if any(item.ticker != ticker or item.local_date != local_date for item in examples):
        raise ValueError("one compiled session cannot mix ticker or date identities")
    examples = tuple(sorted(examples, key=lambda item: item.block_offset))
    view_names = tuple(examples[0].raw_views)
    views: dict[str, dict[str, torch.Tensor]] = {}
    slices_by_view: dict[str, list[tuple[int, int]]] = {}
    patches_by_view: dict[str, list[dict[str, torch.Tensor]]] = {}
    for name in view_names:
        views[name], slices_by_view[name], patches_by_view[name] = _merge_view(examples, name)
    horizon_ids = torch.as_tensor(examples[0].horizons_us, dtype=torch.long)
    blocks: list[dict[str, Any]] = []
    for row, example in enumerate(examples):
        horizon = build_physical_horizon_targets(
            example.target_support.cpu(),
            example.support_origin_indices.cpu(),
            horizon_ids,
            base_timeframe_us=example.base_timeframe_us,
            share_factors=example.target_share_factors.cpu(),
            condition_flags=example.target_condition_flags.cpu(),
        )
        ar_masks: dict[str, torch.Tensor] = {}
        ar_patches: dict[str, dict[str, torch.Tensor]] = {}
        for name in AUTOREGRESSIVE_VIEW_NAMES:
            value = example.raw_views[name].cpu()
            item = build_next_bar_targets(
                value,
                bar_start_us=example.raw_view_start_us[name].cpu(),
                expected_step_us=TIMEFRAME_US_BY_NAME[name],
            )
            available = example.raw_view_available_at_us[name].cpu()
            if item.mask.shape[0]:
                item.mask &= (
                    (available[1:] >= int(example.origin_timestamps_us[0]))
                    & (available[1:] <= int(example.origin_timestamps_us[-1]))
                )[:, None]
            ar_masks[name] = item.mask.contiguous()
            start, length = slices_by_view[name][row]
            shared_targets = views[name]["autoregressive_targets"][start : start + max(0, length - 1)]
            changed = torch.any(item.values != shared_targets, dim=-1)
            indices = torch.nonzero(changed, as_tuple=False).flatten().to(torch.int32)
            ar_patches[name] = {
                "indices": indices,
                "values": item.values[indices.long()].contiguous(),
            }
        blocks.append({
            "view_slices": {name: slices_by_view[name][row] for name in view_names},
            "view_patches": {name: patches_by_view[name][row] for name in view_names},
            "origin_indices": example.origin_indices.cpu().contiguous(),
            "origin_timestamps_us": example.origin_timestamps_us.cpu().contiguous(),
            "asof_indices": {name: value.cpu().contiguous() for name, value in example.asof_indices.items()},
            "autoregressive_mask": ar_masks,
            "autoregressive_patches": ar_patches,
            "horizon_targets": horizon.values.cpu().contiguous(),
            "horizon_mask": horizon.mask.cpu().contiguous(),
            "activity_regime": int(example.activity_regime),
            "unit_index": int(example.unit_index),
            "block_offset": int(example.block_offset),
            "session_phase": str(example.session_phase),
            "has_condition_target": bool(example.has_condition_target),
        })
    return {
        "ticker": ticker,
        "local_date": local_date,
        "views": views,
        "blocks": blocks,
    }


def _compile_session_timed(examples: Sequence[BarGPTExample]) -> tuple[dict[str, Any], float]:
    started = time.perf_counter()
    return compile_session(examples), time.perf_counter() - started


def compile_unit(examples: Sequence[BarGPTExample], config: DataConfig, key: str) -> dict[str, Any]:
    by_date: dict[str, list[BarGPTExample]] = {}
    for example in examples:
        by_date.setdefault(example.local_date, []).append(example)
    sessions = [compile_session(by_date[day]) for day in sorted(by_date)]
    return compile_prepared_unit(sessions, config, key)


def compile_prepared_unit(sessions: Sequence[dict[str, Any]], config: DataConfig, key: str) -> dict[str, Any]:
    """Assemble already compiled sessions without retaining a month of raw examples."""
    sessions = list(sessions)
    origins = sum(
        int(block["origin_indices"].numel())
        for session in sessions for block in session["blocks"]
    )
    return {
        "contract_version": OFFLINE_SHARD_CONTRACT_VERSION,
        "config_hash": config_hash(config),
        "unit_key": key,
        "horizons_us": tuple(config.horizons_us),
        "base_timeframe_us": int(config.base_timeframe_us),
        "feature_dtype": "float32",
        "sessions": sessions,
        "counts": {
            "sessions": len(sessions),
            "blocks": sum(len(session["blocks"]) for session in sessions),
            "origins": origins,
        },
    }


def write_unit(root: Path, payload: dict[str, Any], *, certify_hash: bool) -> dict[str, Any]:
    key = str(payload["unit_key"])
    path = shard_path(root, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.partial")
    started = time.perf_counter()
    torch.save(payload, temporary)
    os.replace(temporary, path)
    digest = _sha256(path) if certify_hash else "uncertified"
    elapsed = time.perf_counter() - started
    value = {
        "contract_version": OFFLINE_SHARD_CONTRACT_VERSION,
        "config_hash": payload["config_hash"],
        "unit_key": key,
        "status": "complete" if certify_hash else "written_uncertified",
        "path": str(path),
        "sha256": digest,
        "bytes": path.stat().st_size,
        **payload["counts"],
        "completed_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "write_and_hash_seconds": elapsed,
    }
    _atomic_json(sidecar_path(path), value)
    return value


def load_shard(path: Path, *, verify_sha256: str = "") -> dict[str, Any]:
    if verify_sha256 and _sha256(path) != verify_sha256:
        raise RuntimeError(f"offline shard SHA-256 mismatch: {path}")
    value = torch.load(path, map_location="cpu", mmap=True, weights_only=False)
    if int(value.get("contract_version", -1)) != OFFLINE_SHARD_CONTRACT_VERSION:
        raise RuntimeError(f"unsupported offline shard contract: {value.get('contract_version')}")
    return value


def materialize_block(shard: dict[str, Any], session_index: int, block_index: int) -> CompiledBlock:
    session = shard["sessions"][int(session_index)]
    block = session["blocks"][int(block_index)]
    views: dict[str, torch.Tensor] = {}
    ar_targets: dict[str, torch.Tensor] = {}
    for name, (start, length) in block["view_slices"].items():
        shared = session["views"][name]
        start = int(start)
        length = int(length)
        value = shared["features"][start : start + length]
        patch = block["view_patches"][name]
        if patch["indices"].numel():
            value = value.clone()
            value[patch["indices"].long()] = patch["values"]
        views[name] = value
        if name in AUTOREGRESSIVE_VIEW_NAMES:
            target = shared["autoregressive_targets"][start : start + max(0, length - 1)]
            patch = block["autoregressive_patches"][name]
            if patch["indices"].numel():
                target = target.clone()
                target[patch["indices"].long()] = patch["values"]
            ar_targets[name] = target
    return CompiledBlock(
        ticker=str(session["ticker"]),
        local_date=str(session["local_date"]),
        views=views,
        origin_indices=block["origin_indices"],
        origin_timestamps_us=block["origin_timestamps_us"],
        asof_indices=block["asof_indices"],
        autoregressive_targets=ar_targets,
        autoregressive_mask=block["autoregressive_mask"],
        horizon_targets=block["horizon_targets"],
        horizon_mask=block["horizon_mask"],
        activity_regime=int(block["activity_regime"]),
        unit_index=int(block["unit_index"]),
        block_offset=int(block["block_offset"]),
        session_phase=str(block["session_phase"]),
        has_condition_target=bool(block["has_condition_target"]),
    )


def collate_compiled_blocks(
    blocks: Sequence[CompiledBlock],
    *,
    horizons_us: tuple[int, ...],
    base_timeframe_us: int,
    pin_memory: bool = False,
) -> BarGPTBatch:
    if not blocks:
        raise ValueError("cannot collate an empty compiled batch")
    view_names = tuple(blocks[0].views)
    if any(tuple(block.views) != view_names for block in blocks):
        raise ValueError("compiled blocks do not share the same view contract")
    origin_mask = _pad_first_dimension(
        [torch.ones(block.origin_indices.numel(), dtype=torch.bool) for block in blocks], fill=False
    )
    regimes = torch.as_tensor([block.activity_regime for block in blocks], dtype=torch.long)
    weights = torch.ones(len(blocks), dtype=torch.float32)
    for regime in range(3):
        selected = regimes == regime
        count = int(selected.sum())
        if count:
            weights[selected] = len(blocks) / (3.0 * count)
    weights /= weights.mean().clamp_min(1e-12)
    feature_dim = int(blocks[0].views["1s"].shape[-1])
    batch = BarGPTBatch(
        views={name: _pad_first_dimension([block.views[name] for block in blocks]) for name in view_names},
        origin_indices=_pad_first_dimension([block.origin_indices for block in blocks]),
        origin_timestamps_us=_pad_first_dimension([block.origin_timestamps_us for block in blocks]),
        origin_mask=origin_mask,
        asof_indices={
            name: _pad_first_dimension([block.asof_indices[name] for block in blocks], fill=-1)
            for name in view_names if name != "1s"
        },
        autoregressive_targets={
            name: _pad_first_dimension([block.autoregressive_targets[name] for block in blocks])
            for name in AUTOREGRESSIVE_VIEW_NAMES
        },
        autoregressive_mask={
            name: _pad_first_dimension([block.autoregressive_mask[name] for block in blocks], fill=False)
            for name in AUTOREGRESSIVE_VIEW_NAMES
        },
        target_support=torch.empty((len(blocks), 0, feature_dim), dtype=torch.float32),
        target_support_lengths=torch.zeros(len(blocks), dtype=torch.long),
        target_share_factors=torch.empty((len(blocks), 0), dtype=torch.float32),
        target_condition_flags=torch.empty((len(blocks), 0, 4), dtype=torch.float32),
        support_origin_indices=_pad_first_dimension([block.origin_indices for block in blocks]),
        horizons_us=tuple(horizons_us),
        base_timeframe_us=int(base_timeframe_us),
        horizon_targets=_pad_first_dimension([block.horizon_targets for block in blocks]),
        horizon_mask=_pad_first_dimension([block.horizon_mask for block in blocks], fill=False),
        sample_weights=weights,
        tickers=tuple(block.ticker for block in blocks),
        local_dates=tuple(block.local_date for block in blocks),
        worker_ids=tuple(0 for _ in blocks),
        unit_indices=tuple(block.unit_index for block in blocks),
        block_offsets=tuple(block.block_offset for block in blocks),
        session_phases=tuple(block.session_phase for block in blocks),
        condition_blocks=tuple(block.has_condition_target for block in blocks),
    )
    return batch.pin_memory() if pin_memory else batch


def _write_empty(root: Path, config: DataConfig, key: str) -> dict[str, Any]:
    path = shard_path(root, key)
    value = {
        "contract_version": OFFLINE_SHARD_CONTRACT_VERSION,
        "config_hash": config_hash(config),
        "unit_key": key,
        "status": "covered_empty",
        "path": "",
        "sha256": "",
        "bytes": 0,
        "sessions": 0,
        "blocks": 0,
        "origins": 0,
        "completed_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    _atomic_json(sidecar_path(path), value)
    return value


def _worker_main(
    worker_id: int,
    tickers: tuple[str, ...],
    config: DataConfig,
    root: str,
    skipped: frozenset[str],
    certify_hash: bool,
    events: Any,
    stop: Any,
    cpu_threads: int,
) -> None:
    try:
        torch.set_num_threads(max(1, int(cpu_threads)))
        torch.set_num_interop_threads(1)
        planned = {
            unit_key(unit.ticker, unit.start_date)
            for unit in month_units(config.start_date, config.end_date, tickers, seed=0)
            if unit_key(unit.ticker, unit.start_date) not in skipped
        }
        events.put(("worker", worker_id, "starting", ",".join(tickers), len(planned)))
        dataset = BarGPTIterableDataset(
            data_config=config,
            stream_config=_stream_config(config),
            split="cache",
            seed=17,
            unit_tickers=tickers,
            skip_unit_keys=skipped,
        )
        current_key = ""
        current_date = ""
        current_examples: list[BarGPTExample] = []
        compiled_sessions: list[dict[str, Any]] = []
        pending: deque[tuple[str, Future[tuple[dict[str, Any], float]]]] = deque()
        seen: set[str] = set()
        fetched_blocks = 0
        unit_started = 0.0
        compile_cpu_seconds = 0.0

        def collect_one(*, wait: bool) -> None:
            nonlocal compile_cpu_seconds
            if not pending or (not wait and not pending[0][1].done()):
                return
            local_date, future = pending.popleft()
            session, compile_seconds = future.result()
            compiled_sessions.append(session)
            compile_cpu_seconds += float(compile_seconds)
            events.put(("session", worker_id, current_key, local_date, len(compiled_sessions)))

        def submit_session(executor: ThreadPoolExecutor) -> None:
            nonlocal current_examples
            if not current_examples:
                return
            local_date = current_examples[0].local_date
            pending.append((local_date, executor.submit(_compile_session_timed, tuple(current_examples))))
            current_examples = []
            # Bound compiler memory while keeping one session compiling and one queued.
            if len(pending) >= 2:
                collect_one(wait=True)

        def flush_unit(executor: ThreadPoolExecutor) -> None:
            nonlocal compiled_sessions, current_key, current_date, unit_started, compile_cpu_seconds
            submit_session(executor)
            while pending:
                collect_one(wait=True)
            if not compiled_sessions:
                return
            events.put(("unit", worker_id, "assembling", current_key))
            payload = compile_prepared_unit(compiled_sessions, config, current_key)
            events.put(("unit", worker_id, "writing", current_key))
            write_started = time.perf_counter()
            evidence = write_unit(Path(root), payload, certify_hash=certify_hash)
            evidence["prepare_wall_seconds"] = max(0.0, write_started - unit_started)
            evidence["compile_cpu_seconds"] = compile_cpu_seconds
            evidence["unit_wall_seconds"] = max(0.0, time.perf_counter() - unit_started)
            _atomic_json(sidecar_path(shard_path(Path(root), current_key)), evidence)
            seen.add(current_key)
            events.put(("complete", worker_id, current_key, evidence))
            compiled_sessions = []
            current_date = ""
            unit_started = 0.0
            compile_cpu_seconds = 0.0

        with ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"bar-gpt-compile-{worker_id}") as executor:
            for example in dataset:
                if stop.is_set():
                    break
                key = unit_key(example.ticker, example.local_date)
                if current_key and key != current_key:
                    flush_unit(executor)
                if current_date and example.local_date != current_date:
                    submit_session(executor)
                current_key = key
                if not unit_started:
                    unit_started = time.perf_counter()
                current_date = example.local_date
                current_examples.append(example)
                fetched_blocks += 1
                if len(current_examples) == 1 or len(current_examples) % 8 == 0:
                    events.put(("block", worker_id, current_key, current_date, len(current_examples), fetched_blocks))
                collect_one(wait=False)
            if not stop.is_set():
                flush_unit(executor)
                for key in sorted(planned - seen):
                    evidence = _write_empty(Path(root), config, key)
                    events.put(("complete", worker_id, key, evidence))
        events.put(("worker", worker_id, "stopped" if stop.is_set() else "completed", ""))
    except BaseException as exc:
        events.put(("failure", worker_id, exc.__class__.__name__, str(exc), traceback.format_exc()))


class ShardBuildReporter:
    def __init__(
        self, *, total: int, completed: int, root: Path, workers: int, layout: str, refresh: float,
        initial_bytes: int = 0, initial_blocks: int = 0, initial_origins: int = 0,
        worker_totals: Sequence[int] = (),
    ) -> None:
        self.total = total
        self.completed = completed
        self.initial_completed = completed
        self.root = root
        self.workers = workers
        self.layout = layout
        self.refresh_seconds = refresh
        self.started = time.perf_counter()
        self.bytes = int(initial_bytes)
        self.blocks = int(initial_blocks)
        self.origins = int(initial_origins)
        self.failures = 0
        self.retries = 0
        self.state = "starting"
        self.worker_state: dict[int, tuple[str, str]] = {}
        self.worker_progress: dict[int, list[int]] = {
            worker: [0, int(total), 0, 0] for worker, total in enumerate(worker_totals)
        }
        self.messages: deque[str] = deque(maxlen=6)
        self._live: Any | None = None
        self._console: Any | None = None
        self._last_text = 0.0

    def __enter__(self) -> "ShardBuildReporter":
        use_rich = self.layout == "rich" or (self.layout == "auto" and sys.stdout.isatty())
        if use_rich:
            from rich.console import Console
            from rich.live import Live
            self._console = Console()
            self._live = Live(self._render(), console=self._console, screen=True, transient=False, auto_refresh=False)
            self._live.start()
        self.state = "running"
        self.message("Offline compilation started; completion advances only after atomic shard certification")
        return self

    def __exit__(self, exc_type: object, exc: object, _tb: object) -> bool:
        if exc_type is KeyboardInterrupt:
            self.state = "interrupted"
        elif exc:
            self.state = "failed"
        elif self.state not in {"failed", "interrupted"}:
            self.state = "completed"
        if exc:
            self.message(str(exc))
        self.refresh(force=True)
        if self._live is not None:
            self._live.stop()
            if self._console is not None:
                self._console.print(self._render())
        return False

    def message(self, value: str) -> None:
        self.messages.append(f"{time.strftime('%H:%M:%S')} {value}")
        self.refresh(force=True)

    def event(self, value: tuple[Any, ...]) -> None:
        kind = value[0]
        worker = int(value[1])
        if kind == "worker":
            self.worker_state[worker] = (str(value[2]), str(value[3]))
            if len(value) > 4:
                self.worker_progress[worker] = [0, int(value[4]), 0, 0]
        elif kind == "unit":
            self.worker_state[worker] = (str(value[2]), str(value[3]))
        elif kind == "block":
            progress = self.worker_progress.setdefault(worker, [0, 0, 0, 0])
            progress[3] = int(value[5])
            self.worker_state[worker] = ("fetching", f"{value[2]} {value[3]} block {value[4]}")
        elif kind == "session":
            progress = self.worker_progress.setdefault(worker, [0, 0, 0, 0])
            progress[2] = int(value[4])
            self.worker_state[worker] = ("compiled", f"{value[2]} {value[3]} ({value[4]} sessions)")
        elif kind == "complete":
            evidence = value[3]
            self.completed += 1
            self.bytes += int(evidence["bytes"])
            self.blocks += int(evidence["blocks"])
            self.origins += int(evidence["origins"])
            progress = self.worker_progress.setdefault(worker, [0, 0, 0, 0])
            progress[0] += 1
            progress[2] = 0
            progress[3] = 0
            self.worker_state[worker] = ("ready", "")
            self.messages.append(f"{time.strftime('%H:%M:%S')} certified {value[2]} ({int(evidence['bytes']) / 2**30:.2f} GiB)")
        elif kind == "failure":
            self.failures += 1
            self.worker_state[worker] = ("failed", f"{value[2]}: {value[3]}")
            self.messages.append(f"{time.strftime('%H:%M:%S')} worker {worker} failed: {value[2]}: {value[3]}")
        self.refresh()

    def refresh(self, *, force: bool = False) -> None:
        if self._live is not None:
            self._live.update(self._render(), refresh=True)
            return
        now = time.monotonic()
        if force or now - self._last_text >= 15:
            self._last_text = now
            elapsed = max(time.perf_counter() - self.started, 1e-9)
            rate = (self.completed - self.initial_completed) / elapsed
            eta = (self.total - self.completed) / rate if rate > 0 else 0
            active = ", ".join(
                f"w{worker}:{self.worker_progress.get(worker, [0, 0])[0]}/{self.worker_progress.get(worker, [0, 0])[1]}:{state}:{focus}"
                for worker, (state, focus) in sorted(self.worker_state.items())
            )
            print(
                f"state={self.state} certified={self.completed}/{self.total} rate={rate:.3f}_shards/s "
                f"eta={_duration(eta) if eta else '-'} written={self.bytes / 2**30:.2f}GiB "
                f"blocks={self.blocks:,} origins={self.origins:,} failures={self.failures} active=[{active}]",
                flush=True,
            )

    def _render(self) -> Any:
        from rich.console import Group
        from rich.panel import Panel
        from rich.progress import BarColumn, Progress, TextColumn
        from rich.table import Table
        width = self._console.size.width if self._console is not None else 120
        height = self._console.size.height if self._console is not None else 40
        elapsed = max(time.perf_counter() - self.started, 1e-9)
        rate = max(0.0, (self.completed - self.initial_completed) / elapsed)
        remaining = max(0, self.total - self.completed)
        eta = remaining / rate if rate else 0
        progress = Progress(
            TextColumn("[bold cyan]Certified ticker-month shards[/]"),
            BarColumn(complete_style="cyan", finished_style="green"),
            TextColumn("{task.completed:,.0f}/{task.total:,.0f}"),
            TextColumn("[bold]{task.percentage:>5.1f}%[/]"),
            expand=True,
        )
        progress.add_task("certified", total=max(1, self.total), completed=min(self.completed, max(1, self.total)))
        summary = Table.grid(expand=True, padding=(0, 2))
        if width >= 90:
            summary.add_column(); summary.add_column(); summary.add_column()
            summary.add_row(f"[bold]state[/] {self.state}", f"[bold]rate[/] {rate * 60:.2f} shards/min", f"[bold]ETA[/] {_duration(eta) if eta else '-'}")
            summary.add_row(f"[bold]written[/] {self.bytes / 2**30:.2f} GiB", f"[bold]blocks[/] {self.blocks:,}", f"[bold]origins[/] {self.origins:,}")
            summary.add_row(f"[bold]workers[/] {self.workers}", f"[bold]failures[/] {self.failures}", f"[bold]elapsed[/] {_duration(elapsed)}")
        else:
            summary.add_column(); summary.add_column()
            summary.add_row(f"[bold]state[/] {self.state}", f"[bold]ETA[/] {_duration(eta) if eta else '-'}")
            summary.add_row(f"[bold]rate[/] {rate * 60:.2f}/min", f"[bold]written[/] {self.bytes / 2**30:.2f} GiB")
            summary.add_row(f"[bold]failures[/] {self.failures}", f"[bold]elapsed[/] {_duration(elapsed)}")
        workers = Table(show_header=True, header_style="bold", expand=True)
        workers.add_column("worker", no_wrap=True)
        workers.add_column("assigned progress", ratio=1)
        workers.add_column("stage", no_wrap=True)
        workers.add_column("current ticker-month/session", ratio=2)
        worker_limit = self.workers if height >= 34 else min(self.workers, 4)
        for worker in range(worker_limit):
            state, focus = self.worker_state.get(worker, ("queued", ""))
            done, total, sessions, blocks = self.worker_progress.get(worker, [0, 0, 0, 0])
            fraction = done / total if total else 0.0
            cells = 10
            filled = min(cells, int(fraction * cells))
            bar = f"[green]{'=' * filled}[/][dim]{'-' * (cells - filled)}[/] {done}/{total}"
            detail = focus or "-"
            if sessions or blocks:
                detail = f"{detail} · sessions {sessions} · blocks {blocks}"
            workers.add_row(str(worker), bar, state, detail)
        if worker_limit < self.workers:
            workers.add_row("...", "-", "summary", f"{self.workers - worker_limit} additional workers")
        recent = "\n".join(self.messages) if self.messages else "Waiting for first durable completion"
        primary = Panel(Group(progress, summary), title="BarGPT offline tensor compiler", border_style="cyan")
        message_limit = 1 if height < 22 else (3 if height < 32 else 6)
        recent_panel = Panel("\n".join(list(self.messages)[-message_limit:]) or "Waiting for first durable completion", title="Recent durable events", border_style="yellow")
        if height < 22:
            return Group(primary, recent_panel)
        if height < 32:
            return Group(primary, Panel(workers, title="Concurrent work", border_style="green"), recent_panel)
        return Group(primary, Panel(workers, title="Concurrent work", border_style="green"), Panel(
            f"[bold]output[/] {self.root}\n[bold]resume[/] rerun the same command; certified shards are skipped",
            title="Durability", border_style="blue",
        ), recent_panel)


def _duration(value: float) -> str:
    seconds = max(0, int(value))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}h {minutes:02d}m" if hours else (f"{minutes}m {secs:02d}s" if minutes else f"{secs}s")


def _partition_tickers(tickers: tuple[str, ...], workers: int) -> list[tuple[str, ...]]:
    count = min(max(1, workers), len(tickers))
    buckets: list[list[str]] = [[] for _ in range(count)]
    for index, ticker in enumerate(tickers):
        buckets[index % count].append(ticker)
    return [tuple(bucket) for bucket in buckets if bucket]


def rebuild_catalog(root: Path, expected_hash: str) -> dict[str, Any]:
    values = completed_units(root, expected_hash)
    catalog = {
        "contract_version": OFFLINE_SHARD_CONTRACT_VERSION,
        "config_hash": expected_hash,
        "updated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "counts": {
            "units": len(values),
            "complete": sum(item["status"] == "complete" for item in values.values()),
            "covered_empty": sum(item["status"] == "covered_empty" for item in values.values()),
            "bytes": sum(int(item["bytes"]) for item in values.values()),
            "blocks": sum(int(item["blocks"]) for item in values.values()),
            "origins": sum(int(item["origins"]) for item in values.values()),
        },
        "units": [values[key] for key in sorted(values)],
    }
    _atomic_json(root / "manifest" / "catalog.json", catalog)
    return catalog


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    config = build_data_config(args)
    root = args.output_root.resolve()
    expected_hash = config_hash(config)
    plan = planned_unit_keys(config)
    existing = {} if args.force_rebuild else completed_units(root, expected_hash)
    remaining = [key for key in plan if key not in existing]
    if args.max_shards:
        remaining = remaining[: int(args.max_shards)]
    print(
        f"BarGPT offline shard plan: units={len(plan):,} certified={len(existing):,} "
        f"remaining={len(remaining):,} workers={min(args.workers, len(config.tickers))} output={root}",
        flush=True,
    )
    if not args.execute:
        print("Plan only; add --execute to write atomically certified tensor shards.", flush=True)
        return 0
    allowed = set(remaining)
    selected_tickers = tuple(ticker for ticker in config.tickers if any(key.startswith(f"{ticker}:") for key in allowed))
    partitions = _partition_tickers(selected_tickers, int(args.workers)) if selected_tickers else []
    cpu_threads = int(args.cpu_threads_per_worker) or max(1, (os.cpu_count() or 8) // max(1, len(partitions)))
    load_env_files(discover_clickhouse_env_files(), verbose=True)
    client = ClickHouseHttpClient(
        default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password()
    )
    evidence = preflight(client, config)
    _atomic_json(root / "manifest" / "build_plan.json", {
        "contract_version": OFFLINE_SHARD_CONTRACT_VERSION,
        "config_hash": expected_hash,
        "created_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_evidence": evidence,
        "data_config": _canonical_config(config),
        "planned_units": len(plan),
        "selected_units": len(remaining),
        "workers": len(partitions),
        "cpu_threads_per_worker": cpu_threads,
    })
    if args.skip_hash:
        print("WARNING: --skip-hash writes uncertified shards that will not be resume-skipped.", file=sys.stderr, flush=True)
    if not remaining:
        catalog = rebuild_catalog(root, expected_hash)
        print(f"All units already certified: {catalog['counts']}", flush=True)
        return 0
    partition_totals = [
        sum(1 for key in allowed if key.partition(":")[0] in set(tickers))
        for tickers in partitions
    ]
    skipped = frozenset(key for key in plan if key not in allowed)
    context = mp.get_context("spawn")
    events = context.Queue(maxsize=max(128, len(partitions) * 16))
    stop = context.Event()
    processes = [
        context.Process(
            target=_worker_main,
            args=(worker, tickers, config, str(root), skipped, not args.skip_hash, events, stop, cpu_threads),
            name=f"bar-gpt-shard-{worker}",
        )
        for worker, tickers in enumerate(partitions)
    ]
    interrupted = False
    with ShardBuildReporter(
        total=len(existing) + len(remaining), completed=len(existing), root=root,
        workers=len(processes), layout=args.progress_layout, refresh=args.refresh_seconds,
        initial_bytes=sum(int(item["bytes"]) for item in existing.values()),
        initial_blocks=sum(int(item["blocks"]) for item in existing.values()),
        initial_origins=sum(int(item["origins"]) for item in existing.values()),
        worker_totals=partition_totals,
    ) as reporter:
        for process in processes:
            process.start()
        try:
            alive = len(processes)
            while alive:
                try:
                    event = events.get(timeout=float(args.refresh_seconds))
                    reporter.event(event)
                except queue.Empty:
                    reporter.refresh()
                alive = sum(process.is_alive() for process in processes)
                if reporter.failures:
                    stop.set()
        except KeyboardInterrupt:
            interrupted = True
            stop.set()
            reporter.state = "interrupted"
            reporter.message("Interrupt received; finishing atomic writes and stopping workers")
        finally:
            for process in processes:
                process.join(timeout=30)
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=10)
            while True:
                try:
                    reporter.event(events.get_nowait())
                except queue.Empty:
                    break
            if not interrupted and (
                reporter.failures or any(process.exitcode not in {0, None} for process in processes)
            ):
                reporter.state = "failed"
                reporter.message("Build stopped after a worker failure; certified shards remain resumable")
                reporter.refresh(force=True)
    catalog = rebuild_catalog(root, expected_hash)
    print(f"Final certified catalog: {catalog['counts']}", flush=True)
    if interrupted:
        return 130
    return 1 if reporter.failures or any(process.exitcode not in {0, None} for process in processes) else 0


if __name__ == "__main__":
    raise SystemExit(main())
