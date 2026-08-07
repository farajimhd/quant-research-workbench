from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import faulthandler
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
from functools import partial
from pathlib import Path
from typing import Any, Iterator, Sequence

import torch
from torch.utils.data import DataLoader, IterableDataset, get_worker_info

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
from research.bar_gpt.v1.targets import HorizonTargets, build_next_bar_targets, build_physical_horizon_targets
from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    discover_clickhouse_env_files,
)
from research.mlops.env import load_env_files


OFFLINE_SHARD_CONTRACT_VERSION = 2
# Contract 3 governs the source/session preparation used to materialize v2
# shards. Contract 4 changes only the offline runtime's worker-owned resume
# cursors, so readers normalize that runtime field before comparing the pinned
# tensor-payload hash.
OFFLINE_SHARD_BUILD_STREAM_CONTRACT_VERSION = 3
DEFAULT_OUTPUT_ROOT = Path(r"D:\TradingML\runtimes\bar_gpt\v1\offline_shards_v2")


_STORAGE_IRRELEVANT_CONFIG_FIELDS = frozenset({
    "tickers",
    "start_date",
    "end_date",
    "validation_start_date",
    "validation_slices",
    "batch_size",
    "loader_workers",
    "pin_memory",
    "persistent_workers",
    "worker_prefetch_batches",
    "ready_queue_blocks",
    "balance_activity_regimes",
    "coverage_mode",
    "coverage_blocks_per_unit",
    "validation_blocks_per_slice",
    "origin_fetch_candidate_blocks",
    "origin_emit_blocks_per_chunk",
    "clickhouse_query_days",
    "clickhouse_prefetch_pages",
    "clickhouse_max_block_size",
    "clickhouse_max_threads_per_worker",
    "clickhouse_max_memory_usage",
    "clickhouse_max_bytes_before_external_sort",
    "clickhouse_retry_attempts",
    "clickhouse_retry_initial_seconds",
    "clickhouse_retry_max_seconds",
})


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
    worker_id: int = 0


@dataclass(frozen=True, slots=True)
class OfflineShardUnit:
    unit_key: str
    path: Path
    sessions: int
    blocks: int
    origins: int
    stable_unit_index: int
    condition_positive_counts: tuple[int, int, int, int]


def _json_ready(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if dataclasses.is_dataclass(value):
        return _json_ready(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_ready(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _process_exit_detail(exit_code: int) -> dict[str, Any]:
    code = int(exit_code)
    unsigned = code & 0xFFFFFFFF
    meanings = {
        0xC0000005: "STATUS_ACCESS_VIOLATION",
        0xC0000017: "STATUS_NO_MEMORY",
        0xC000009A: "STATUS_INSUFFICIENT_RESOURCES",
        0xC00000FD: "STATUS_STACK_OVERFLOW",
        0xC000012D: "STATUS_COMMITMENT_LIMIT",
        0xC0000374: "STATUS_HEAP_CORRUPTION",
        0xC0000409: "STATUS_STACK_BUFFER_OVERRUN",
    }
    return {
        "value": code,
        "windows_hex": f"0x{unsigned:08X}",
        "meaning": meanings.get(unsigned, ""),
    }


class BuildRunLog:
    """Parent-owned durable event log for one offline-shard invocation."""

    def __init__(self, root: Path, *, arguments: dict[str, Any]) -> None:
        now = dt.datetime.now().astimezone()
        self.run_id = f"{now:%Y%m%d-%H%M%S}-p{os.getpid()}-{time.time_ns() % 1_000_000:06d}"
        self.directory = root / "manifest" / "build_runs" / self.run_id
        self.directory.mkdir(parents=True, exist_ok=False)
        self.events_path = self.directory / "events.jsonl"
        self.summary_path = self.directory / "summary.json"
        self.workers_directory = self.directory / "workers"
        self.workers_directory.mkdir()
        self.latest_path = root / "manifest" / "build_runs" / "latest.json"
        self.started_at = now.isoformat(timespec="microseconds")
        self.event_count = 0
        self._handle = self.events_path.open("a", encoding="utf-8", buffering=1)
        self.record(
            "run_started",
            durable=True,
            run_id=self.run_id,
            pid=os.getpid(),
            arguments=arguments,
            cwd=str(Path.cwd()),
        )
        _atomic_json(
            self.latest_path,
            {
                "run_id": self.run_id,
                "directory": str(self.directory),
                "events_path": str(self.events_path),
                "started_at": self.started_at,
            },
        )

    def worker_fault_path(self, worker: int, ticker: str) -> Path:
        safe_ticker = "".join(character for character in ticker.upper() if character.isalnum() or character in "-._")
        return self.workers_directory / f"worker-{int(worker):03d}-{safe_ticker}-fatal.log"

    def record(self, event: str, *, durable: bool = False, **fields: Any) -> None:
        value = {
            "timestamp": dt.datetime.now().astimezone().isoformat(timespec="microseconds"),
            "event": str(event),
            **fields,
        }
        self._handle.write(json.dumps(_json_ready(value), sort_keys=True) + "\n")
        self._handle.flush()
        self.event_count += 1
        if durable:
            os.fsync(self._handle.fileno())

    def record_worker_event(self, value: tuple[Any, ...]) -> None:
        kind = str(value[0])
        worker = int(value[1])
        if kind == "worker":
            self.record(
                "worker_state", worker=worker, state=str(value[2]), focus=str(value[3]),
                assigned_units=int(value[4]) if len(value) > 4 else None,
                assigned_blocks=int(value[5]) if len(value) > 5 else None,
            )
        elif kind == "unit":
            self.record("unit_stage", worker=worker, stage=str(value[2]), unit_key=str(value[3]))
        elif kind == "block":
            self.record(
                "block_progress", worker=worker, unit_key=str(value[2]), local_date=str(value[3]),
                unit_blocks=int(value[4]), fetched_blocks=int(value[5]),
                memory=value[6] if len(value) > 6 else {},
            )
        elif kind == "session":
            self.record(
                "session_compiled", worker=worker, unit_key=str(value[2]), local_date=str(value[3]),
                compiled_sessions=int(value[4]), compiled_blocks=int(value[5]),
            )
        elif kind == "complete":
            self.record("unit_certified", worker=worker, unit_key=str(value[2]), evidence=value[3])
        elif kind == "failure":
            self.record(
                "worker_exception", durable=True, worker=worker,
                exception_type=str(value[2]), message=str(value[3]),
                traceback=str(value[4]) if len(value) > 4 else "",
            )
        elif kind == "process_exit":
            self.record(
                "worker_process_exit", durable=True, worker=worker, ticker=str(value[2]),
                pid=int(value[3]), exit_code=int(value[4]),
                exit_detail=_process_exit_detail(int(value[4])), fault_log=str(value[5]),
                last_state=str(value[6]), last_focus=str(value[7]),
                last_memory=value[8] if len(value) > 8 else {},
            )

    def finalize(self, *, status: str, exit_code: int, **fields: Any) -> None:
        finished_at = dt.datetime.now().astimezone().isoformat(timespec="microseconds")
        self.record("run_finished", durable=True, status=status, exit_code=int(exit_code), **fields)
        summary = {
            "run_id": self.run_id,
            "status": status,
            "exit_code": int(exit_code),
            "started_at": self.started_at,
            "finished_at": finished_at,
            "events": self.event_count,
            "events_path": str(self.events_path),
            **fields,
        }
        _atomic_json(self.summary_path, _json_ready(summary))
        _atomic_json(
            self.latest_path,
            {
                "run_id": self.run_id,
                "directory": str(self.directory),
                "events_path": str(self.events_path),
                "summary_path": str(self.summary_path),
                "started_at": self.started_at,
                "finished_at": finished_at,
                "status": status,
                "exit_code": int(exit_code),
            },
        )
        self._handle.close()


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
    parser.add_argument(
        "--clickhouse-max-concurrent-pages",
        type=int,
        default=0,
        help="Global Arrow-response concurrency across workers; zero selects a worker-aware bound.",
    )
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
    if args.clickhouse_max_concurrent_pages < 0:
        parser.error("--clickhouse-max-concurrent-pages cannot be negative")
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


def stable_unit_index(key: str) -> int:
    return int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:8], "big") & ((1 << 63) - 1)


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


def _storage_contract_config(config: DataConfig) -> dict[str, Any]:
    """Return only fields capable of changing one ticker-month tensor payload."""
    return {
        key: value
        for key, value in _canonical_config(config).items()
        if key not in _STORAGE_IRRELEVANT_CONFIG_FIELDS
    }


def config_hash(config: DataConfig) -> str:
    payload = {
        "contract": OFFLINE_SHARD_CONTRACT_VERSION,
        "data": _storage_contract_config(config),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def shard_compatibility_hash(config: DataConfig) -> str:
    storage_config = dataclasses.replace(
        config,
        loader_stream_contract_version=OFFLINE_SHARD_BUILD_STREAM_CONTRACT_VERSION,
    )
    return config_hash(storage_config)


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
    slices: list[tuple[int, int]] = []
    for example in examples:
        local = example.raw_view_start_us[name].cpu()
        indices = torch.searchsorted(unique_starts, local)
        expected = torch.arange(indices[0], indices[0] + indices.numel(), dtype=indices.dtype)
        if not torch.equal(indices, expected) or not torch.equal(unique_starts[indices], local):
            raise RuntimeError(f"{example.ticker} {example.local_date} {name} is not a contiguous shared slice")
        slices.append((int(indices[0]), int(indices.numel())))
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
    patches: list[dict[str, torch.Tensor] | None] = [None] * len(examples)
    shape_groups: dict[tuple[int, ...], list[int]] = {}
    for index, example in enumerate(examples):
        shape_groups.setdefault(tuple(example.raw_views[name].shape), []).append(index)
    for indices in shape_groups.values():
        raw_batch = torch.stack([examples[index].raw_views[name].cpu() for index in indices])
        start_batch = torch.stack([examples[index].raw_view_start_us[name].cpu() for index in indices])
        exact_batch = project_stationary_features(
            raw_batch, start_batch, timeframe_us=TIMEFRAME_US_BY_NAME[name]
        )
        for batch_row, example_index in enumerate(indices):
            start, length = slices[example_index]
            exact = exact_batch[batch_row]
            changed = torch.any(exact != projected[start : start + length], dim=-1)
            changed_indices = torch.nonzero(changed, as_tuple=False).flatten().to(torch.int32)
            patches[example_index] = {
                "indices": changed_indices,
                "values": exact[changed_indices.long()].contiguous(),
            }
    if any(patch is None for patch in patches):
        raise RuntimeError("failed to compile every block-local view patch")
    return result, slices, [patch for patch in patches if patch is not None]


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
    exact_ar_by_view: dict[str, list[HorizonTargets | None]] = {}
    for name in AUTOREGRESSIVE_VIEW_NAMES:
        exact_items: list[HorizonTargets | None] = [None] * len(examples)
        shape_groups: dict[tuple[int, ...], list[int]] = {}
        for index, example in enumerate(examples):
            shape_groups.setdefault(tuple(example.raw_views[name].shape), []).append(index)
        for indices in shape_groups.values():
            raw_batch = torch.stack([examples[index].raw_views[name].cpu() for index in indices])
            start_batch = torch.stack([examples[index].raw_view_start_us[name].cpu() for index in indices])
            batch = build_next_bar_targets(
                raw_batch,
                bar_start_us=start_batch,
                expected_step_us=TIMEFRAME_US_BY_NAME[name],
            )
            for batch_row, example_index in enumerate(indices):
                exact_items[example_index] = HorizonTargets(
                    batch.values[batch_row].contiguous(), batch.mask[batch_row].contiguous()
                )
        exact_ar_by_view[name] = exact_items
    horizon_ids = torch.as_tensor(examples[0].horizons_us, dtype=torch.long)
    exact_horizons: list[HorizonTargets | None] = [None] * len(examples)
    horizon_shape_groups: dict[tuple[tuple[int, ...], tuple[int, ...]], list[int]] = {}
    for index, example in enumerate(examples):
        signature = (tuple(example.target_support.shape), tuple(example.support_origin_indices.shape))
        horizon_shape_groups.setdefault(signature, []).append(index)
    for indices in horizon_shape_groups.values():
        batch = build_physical_horizon_targets(
            torch.stack([examples[index].target_support.cpu() for index in indices]),
            torch.stack([examples[index].support_origin_indices.cpu() for index in indices]),
            horizon_ids,
            base_timeframe_us=examples[indices[0]].base_timeframe_us,
            share_factors=torch.stack([examples[index].target_share_factors.cpu() for index in indices]),
            condition_flags=torch.stack([examples[index].target_condition_flags.cpu() for index in indices]),
        )
        for batch_row, example_index in enumerate(indices):
            exact_horizons[example_index] = HorizonTargets(
                batch.values[batch_row].contiguous(), batch.mask[batch_row].contiguous()
            )
    blocks: list[dict[str, Any]] = []
    for row, example in enumerate(examples):
        horizon = exact_horizons[row]
        if horizon is None:
            raise RuntimeError(f"failed to compile physical horizon targets for block {row}")
        ar_masks: dict[str, torch.Tensor] = {}
        ar_patches: dict[str, dict[str, torch.Tensor]] = {}
        for name in AUTOREGRESSIVE_VIEW_NAMES:
            item = exact_ar_by_view[name][row]
            if item is None:
                raise RuntimeError(f"failed to compile {name} autoregressive targets for block {row}")
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


def condition_positive_counts(sessions: Sequence[dict[str, Any]]) -> tuple[int, int, int, int]:
    """Count valid positive values in the four condition-target channels."""
    counts = [0, 0, 0, 0]
    for session in sessions:
        for block in session["blocks"]:
            targets = block["horizon_targets"]
            mask = block["horizon_mask"]
            if not isinstance(targets, torch.Tensor) or not isinstance(mask, torch.Tensor):
                raise TypeError("offline shard horizon targets and masks must be tensors")
            if targets.shape != mask.shape or targets.ndim < 1 or targets.shape[-1] < 4:
                raise ValueError(
                    "offline shard horizon targets and masks must have matching shapes "
                    "with at least four target channels"
                )
            values = targets[..., -4:]
            valid = mask[..., -4:].to(dtype=torch.bool)
            dimensions = tuple(range(values.ndim - 1))
            positive = ((values > 0) & valid).sum(dim=dimensions).tolist()
            for index, count in enumerate(positive):
                counts[index] += int(count)
    return counts[0], counts[1], counts[2], counts[3]


def compile_prepared_unit(sessions: Sequence[dict[str, Any]], config: DataConfig, key: str) -> dict[str, Any]:
    """Assemble already compiled sessions without retaining a month of raw examples."""
    sessions = list(sessions)
    unit_index = stable_unit_index(key)
    for session in sessions:
        for block in session["blocks"]:
            block["unit_index"] = unit_index
    origins = sum(
        int(block["origin_indices"].numel())
        for session in sessions for block in session["blocks"]
    )
    positive_counts = condition_positive_counts(sessions)
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
            "condition_positive_counts": list(positive_counts),
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


def discover_offline_units(
    root: Path,
    config: DataConfig,
    *,
    tickers: Sequence[str],
    start_date: str,
    end_date: str,
) -> tuple[OfflineShardUnit, ...]:
    """Fail closed unless every requested ticker-month has a certified v2 sidecar."""
    start = dt.date.fromisoformat(start_date)
    end = dt.date.fromisoformat(end_date)
    if start.day != 1 or end.day != 1 or start >= end:
        raise ValueError("offline shard selections must use non-empty month boundaries")
    expected_hash = shard_compatibility_hash(config)
    expected: list[str] = []
    cursor = start
    while cursor < end:
        expected.extend(f"{ticker}:{cursor:%Y-%m}" for ticker in tickers)
        cursor = (cursor.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    units: list[OfflineShardUnit] = []
    missing: list[str] = []
    incompatible: list[str] = []
    missing_condition_counts: list[str] = []
    for key in expected:
        path = shard_path(root, key)
        sidecar = sidecar_path(path)
        try:
            value = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            missing.append(key)
            continue
        if (
            int(value.get("contract_version", -1)) != OFFLINE_SHARD_CONTRACT_VERSION
            or value.get("config_hash") != expected_hash
        ):
            incompatible.append(key)
            continue
        status = str(value.get("status", ""))
        if status == "covered_empty":
            continue
        if status != "complete" or not path.is_file():
            missing.append(key)
            continue
        raw_condition_counts = value.get("condition_positive_counts")
        if raw_condition_counts is None:
            missing_condition_counts.append(key)
            continue
        if not isinstance(raw_condition_counts, list) or len(raw_condition_counts) != 4:
            incompatible.append(key)
            continue
        units.append(OfflineShardUnit(
            unit_key=key,
            path=path,
            sessions=int(value["sessions"]),
            blocks=int(value["blocks"]),
            origins=int(value["origins"]),
            stable_unit_index=stable_unit_index(key),
            condition_positive_counts=tuple(int(item) for item in raw_condition_counts),
        ))
    if missing or incompatible or missing_condition_counts:
        detail = []
        if missing:
            detail.append(f"missing={len(missing)} first={missing[:5]}")
        if incompatible:
            detail.append(f"incompatible={len(incompatible)} first={incompatible[:5]}")
        if missing_condition_counts:
            detail.append(
                "missing_condition_positive_counts="
                f"{len(missing_condition_counts)} first={missing_condition_counts[:5]}"
            )
        raise RuntimeError("offline shard coverage is incomplete: " + "; ".join(detail))
    if not units:
        raise RuntimeError("offline shard selection contains no completed trainable units")
    return tuple(units)


class OfflineShardDataset(IterableDataset[CompiledBlock]):
    """Worker-owned mmap stream; batching and prefetch remain DataLoader concerns."""

    def __init__(
        self,
        units: Sequence[OfflineShardUnit],
        *,
        seed: int,
        shuffle_units: bool,
        resume_cursors: dict[int, Any] | None = None,
        validation_slices: Sequence[tuple[str, str, str]] = (),
        blocks_per_validation_slice: int = 0,
    ) -> None:
        super().__init__()
        self.units = tuple(units)
        self.seed = int(seed)
        self.shuffle_units = bool(shuffle_units)
        self.resume_cursors = dict(resume_cursors or {})
        self.validation_slices = tuple(validation_slices)
        self.blocks_per_validation_slice = int(blocks_per_validation_slice)
        self.epoch = 0

    def _ordered_units(self) -> list[OfflineShardUnit]:
        units = list(self.units)
        if self.shuffle_units:
            generator = torch.Generator().manual_seed(self.seed + self.epoch * 1_000_003)
            order = torch.randperm(len(units), generator=generator).tolist()
            units = [units[index] for index in order]
        return units

    def _iter_validation(self, worker_id: int, workers: int) -> Iterator[CompiledBlock]:
        if self.resume_cursors:
            raise RuntimeError("fixed validation sampling does not accept training resume cursors")
        owned_slices = self.validation_slices[worker_id::workers]
        units_by_ticker: dict[str, list[OfflineShardUnit]] = {}
        for unit in self.units:
            ticker = unit.unit_key.partition(":")[0]
            units_by_ticker.setdefault(ticker, []).append(unit)
        requested = int(self.blocks_per_validation_slice)
        for ticker, start, end in owned_slices:
            candidates: list[tuple[bytes, OfflineShardUnit, int, int]] = []
            for unit in units_by_ticker.get(ticker, ()):
                shard = load_shard(unit.path)
                for session_index, session in enumerate(shard["sessions"]):
                    local_date = str(session["local_date"])
                    if not start <= local_date < end:
                        continue
                    for block_index, raw_block in enumerate(session["blocks"]):
                        block_offset = int(raw_block["block_offset"])
                        identity = (
                            f"{self.seed}|validation|{ticker}|{unit.unit_key}|"
                            f"{local_date}|{block_offset}"
                        )
                        candidates.append((
                            hashlib.sha256(identity.encode("utf-8")).digest(),
                            unit,
                            session_index,
                            block_index,
                        ))
            if len(candidates) < requested:
                raise RuntimeError(
                    f"validation ticker {ticker} [{start},{end}) has only "
                    f"{len(candidates)} eligible blocks; {requested} required"
                )
            candidates.sort(key=lambda item: item[0])
            loaded_path: Path | None = None
            loaded_shard: dict[str, Any] | None = None
            for _score, unit, session_index, block_index in candidates[:requested]:
                if loaded_path != unit.path:
                    loaded_path = unit.path
                    loaded_shard = load_shard(unit.path)
                assert loaded_shard is not None
                block = materialize_block(loaded_shard, session_index, block_index)
                block.worker_id = worker_id
                yield block

    def __iter__(self) -> Iterator[CompiledBlock]:
        info = get_worker_info()
        worker_id = int(info.id) if info is not None else 0
        workers = int(info.num_workers) if info is not None else 1
        if self.validation_slices and self.blocks_per_validation_slice > 0:
            yield from self._iter_validation(worker_id, workers)
            return
        units = self._ordered_units()[worker_id::workers]
        resume = self.resume_cursors.get(worker_id)
        resume_reached = resume is None
        validation_ranges = {
            ticker: (start, end)
            for ticker, start, end in self.validation_slices
        }
        for unit in units:
            if not resume_reached:
                if unit.stable_unit_index != int(resume.unit_index):
                    continue
                resume_reached = True
            shard = load_shard(unit.path)
            selected: list[tuple[int, int]] = []
            ticker = unit.unit_key.partition(":")[0]
            validation_range = validation_ranges.get(ticker)
            for session_index, session in enumerate(shard["sessions"]):
                local_date = str(session["local_date"])
                if validation_range is not None and not (
                    validation_range[0] <= local_date < validation_range[1]
                ):
                    continue
                selected.extend((session_index, block_index) for block_index in range(len(session["blocks"])))
            for session_index, block_index in selected:
                block = materialize_block(shard, session_index, block_index)
                if resume is not None and unit.stable_unit_index == int(resume.unit_index):
                    if block.block_offset <= int(resume.block_offset):
                        continue
                block.worker_id = worker_id
                yield block
        if resume is not None and not resume_reached:
            raise RuntimeError(
                f"offline resume cursor unit {resume.unit_index} is not owned by worker {worker_id}"
            )


def make_offline_dataloader(
    dataset: OfflineShardDataset,
    config: DataConfig,
    *,
    drop_last: bool,
) -> DataLoader[CompiledBlock]:
    kwargs: dict[str, Any] = {}
    if config.loader_workers > 0:
        kwargs["prefetch_factor"] = int(config.worker_prefetch_batches)
        kwargs["persistent_workers"] = False
        kwargs["in_order"] = False
    return DataLoader(
        dataset,
        batch_size=int(config.batch_size),
        num_workers=int(config.loader_workers),
        pin_memory=bool(config.pin_memory),
        drop_last=drop_last,
        collate_fn=partial(
            collate_compiled_blocks,
            horizons_us=tuple(config.horizons_us),
            base_timeframe_us=int(config.base_timeframe_us),
        ),
        **kwargs,
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
        worker_ids=tuple(block.worker_id for block in blocks),
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


def _process_memory_snapshot() -> dict[str, int]:
    """Return process-private diagnostics without adding a runtime dependency."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCountersEx(ctypes.Structure):
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
                ("PrivateUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCountersEx()
        counters.cb = ctypes.sizeof(counters)
        get_current_process = ctypes.windll.kernel32.GetCurrentProcess
        get_current_process.restype = wintypes.HANDLE
        get_process_memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
        get_process_memory_info.argtypes = (
            wintypes.HANDLE, ctypes.POINTER(ProcessMemoryCountersEx), wintypes.DWORD,
        )
        get_process_memory_info.restype = wintypes.BOOL
        handle = get_current_process()
        if get_process_memory_info(handle, ctypes.byref(counters), counters.cb):
            return {
                "worker_working_set_bytes": int(counters.WorkingSetSize),
                "worker_peak_working_set_bytes": int(counters.PeakWorkingSetSize),
                "worker_private_bytes": int(counters.PrivateUsage),
                "worker_peak_pagefile_bytes": int(counters.PeakPagefileUsage),
            }
    return {}


def _ticker_worker_main(
    worker_id: int,
    ticker: str,
    config: DataConfig,
    root: str,
    skipped: frozenset[str],
    certify_hash: bool,
    events: Any,
    stop: Any,
    cpu_threads: int,
    query_gate: Any,
    progress_block_offset: int,
    fatal_log_path: str,
) -> None:
    fault_path = Path(fatal_log_path)
    fault_path.parent.mkdir(parents=True, exist_ok=True)
    fault_handle = fault_path.open("a", encoding="utf-8", buffering=1)
    fault_handle.write(
        f"{dt.datetime.now().astimezone().isoformat(timespec='microseconds')} "
        f"worker_started pid={os.getpid()} worker={worker_id} ticker={ticker}\n"
    )
    faulthandler.enable(file=fault_handle, all_threads=True)
    try:
        from research.bar_gpt.v1.train import _stream_config

        torch.set_num_threads(max(1, int(cpu_threads)))
        torch.set_num_interop_threads(1)
        planned = {
            unit_key(unit.ticker, unit.start_date)
            for unit in month_units(config.start_date, config.end_date, (ticker,), seed=0)
            if unit_key(unit.ticker, unit.start_date) not in skipped
        }
        events.put(("unit", worker_id, "starting ticker", ticker))
        dataset = BarGPTIterableDataset(
            data_config=config,
            stream_config=_stream_config(config),
            split="cache",
            seed=17,
            unit_tickers=(ticker,),
            skip_unit_keys=skipped,
            query_gate=query_gate,
        )
        current_key = ""
        current_date = ""
        current_examples: list[BarGPTExample] = []
        compiled_sessions: list[dict[str, Any]] = []
        pending: deque[tuple[str, Future[tuple[dict[str, Any], float]]]] = deque()
        seen: set[str] = set()
        fetched_blocks = int(progress_block_offset)
        compiled_blocks = int(progress_block_offset)
        unit_started = 0.0
        compile_cpu_seconds = 0.0
        loader_stage_seconds: dict[str, float] = {}

        def collect_one(*, wait: bool) -> None:
            nonlocal compile_cpu_seconds, compiled_blocks
            if not pending or (not wait and not pending[0][1].done()):
                return
            local_date, future = pending.popleft()
            session, compile_seconds = future.result()
            compiled_sessions.append(session)
            compile_cpu_seconds += float(compile_seconds)
            compiled_blocks += len(session["blocks"])
            events.put(("session", worker_id, current_key, local_date, len(compiled_sessions), compiled_blocks))

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
            nonlocal compiled_sessions, current_key, current_date, unit_started, compile_cpu_seconds, loader_stage_seconds
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
            evidence["loader_stage_seconds"] = {
                name: float(seconds) for name, seconds in sorted(loader_stage_seconds.items())
            }
            evidence.update(_process_memory_snapshot())
            _atomic_json(sidecar_path(shard_path(Path(root), current_key)), evidence)
            seen.add(current_key)
            events.put(("complete", worker_id, current_key, evidence))
            compiled_sessions = []
            current_date = ""
            unit_started = 0.0
            compile_cpu_seconds = 0.0
            loader_stage_seconds = {}

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
                for name, seconds in example.loader_stage_seconds.items():
                    loader_stage_seconds[name] = loader_stage_seconds.get(name, 0.0) + float(seconds)
                current_examples.append(example)
                fetched_blocks += 1
                if len(current_examples) == 1 or len(current_examples) % 8 == 0:
                    events.put((
                        "block", worker_id, current_key, current_date, len(current_examples), fetched_blocks,
                        _process_memory_snapshot(),
                    ))
                collect_one(wait=False)
            if not stop.is_set():
                flush_unit(executor)
                for key in sorted(planned - seen):
                    evidence = _write_empty(Path(root), config, key)
                    events.put(("complete", worker_id, key, evidence))
        fault_handle.write(
            f"{dt.datetime.now().astimezone().isoformat(timespec='microseconds')} "
            f"worker_completed pid={os.getpid()} worker={worker_id} ticker={ticker}\n"
        )
    except BaseException as exc:
        formatted_traceback = traceback.format_exc()
        fault_handle.write(
            f"{dt.datetime.now().astimezone().isoformat(timespec='microseconds')} "
            f"worker_exception pid={os.getpid()} worker={worker_id} ticker={ticker} "
            f"type={exc.__class__.__name__} message={exc}\n{formatted_traceback}\n"
        )
        fault_handle.flush()
        os.fsync(fault_handle.fileno())
        events.put(("failure", worker_id, exc.__class__.__name__, str(exc), formatted_traceback))
        raise
    finally:
        faulthandler.disable()
        fault_handle.close()


class ShardBuildReporter:
    def __init__(
        self, *, total: int, completed: int, root: Path, workers: int, layout: str, refresh: float,
        initial_bytes: int = 0, initial_blocks: int = 0, initial_origins: int = 0,
        worker_totals: Sequence[int] = (), worker_block_totals: Sequence[int] = (),
        run_log: BuildRunLog | None = None,
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
        self.total_work_blocks = sum(int(value) for value in worker_block_totals)
        self.compiled_work_blocks = 0
        self.failures = 0
        self.retries = 0
        self.state = "starting"
        self.run_log = run_log
        self.failed_workers: set[int] = set()
        self.worker_memory: dict[int, dict[str, int]] = {}
        self.worker_state: dict[int, tuple[str, str]] = {}
        self.worker_progress: dict[int, list[int]] = {
            worker: [0, int(total), 0, int(worker_block_totals[worker]), 0, 0]
            for worker, total in enumerate(worker_totals)
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
        if self.run_log is not None:
            self.run_log.record("controller_message", message=value)
        self.refresh(force=True)

    def event(self, value: tuple[Any, ...]) -> None:
        if self.run_log is not None:
            self.run_log.record_worker_event(value)
        kind = value[0]
        worker = int(value[1])
        force_refresh = False
        if kind == "worker":
            if worker not in self.failed_workers:
                self.worker_state[worker] = (str(value[2]), str(value[3]))
            if len(value) > 4:
                block_total = int(value[5]) if len(value) > 5 else 0
                self.worker_progress[worker] = [0, int(value[4]), 0, block_total, 0, 0]
        elif kind == "unit":
            self.worker_state[worker] = (str(value[2]), str(value[3]))
        elif kind == "block":
            progress = self.worker_progress.setdefault(worker, [0, 0, 0, 0, 0, 0])
            progress[5] = int(value[5])
            if len(value) > 6:
                self.worker_memory[worker] = {
                    str(name): int(amount) for name, amount in dict(value[6]).items()
                }
            self.worker_state[worker] = ("fetching", f"{value[2]} {value[3]} block {value[4]}")
        elif kind == "session":
            progress = self.worker_progress.setdefault(worker, [0, 0, 0, 0, 0, 0])
            new_compiled = int(value[5])
            self.compiled_work_blocks += max(0, new_compiled - progress[2])
            progress[2] = new_compiled
            progress[4] += 1
            self.worker_state[worker] = ("compiled", f"{value[2]} {value[3]}")
        elif kind == "complete":
            evidence = value[3]
            self.completed += 1
            self.bytes += int(evidence["bytes"])
            self.blocks += int(evidence["blocks"])
            self.origins += int(evidence["origins"])
            progress = self.worker_progress.setdefault(worker, [0, 0, 0, 0, 0, 0])
            progress[0] += 1
            self.worker_state[worker] = ("ready", "")
            self.messages.append(f"{time.strftime('%H:%M:%S')} certified {value[2]} ({int(evidence['bytes']) / 2**30:.2f} GiB)")
        elif kind == "failure":
            if worker not in self.failed_workers:
                self.failures += 1
                self.failed_workers.add(worker)
            self.state = "failed"
            self.worker_state[worker] = ("failed", f"{value[2]}: {value[3]}")
            self.messages.append(f"{time.strftime('%H:%M:%S')} worker {worker} failed: {value[2]}: {value[3]}")
            force_refresh = True
        elif kind == "process_exit":
            if worker not in self.failed_workers:
                self.failures += 1
                self.failed_workers.add(worker)
            self.state = "failed"
            ticker = str(value[2])
            exit_code = int(value[4])
            exit_detail = _process_exit_detail(exit_code)
            exit_label = str(exit_code)
            if exit_detail["meaning"]:
                exit_label += f"/{exit_detail['windows_hex']} {exit_detail['meaning']}"
            fault_log = str(value[5])
            self.worker_state[worker] = ("failed", f"{ticker} exited {exit_label}")
            self.messages.append(
                f"{time.strftime('%H:%M:%S')} worker {worker} {ticker} exited with code {exit_label}; "
                f"diagnostics: {fault_log}"
            )
            force_refresh = True
        self.refresh(force=force_refresh)

    def refresh(self, *, force: bool = False) -> None:
        if self._live is not None:
            self._live.update(self._render(), refresh=True)
            return
        now = time.monotonic()
        if force or now - self._last_text >= 15:
            self._last_text = now
            elapsed = max(time.perf_counter() - self.started, 1e-9)
            rate = self.compiled_work_blocks / elapsed
            eta = (self.total_work_blocks - self.compiled_work_blocks) / rate if rate > 0 else 0
            active = ", ".join(
                f"w{worker}:{self.worker_progress.get(worker, [0, 0, 0, 0])[2]}/{self.worker_progress.get(worker, [0, 0, 0, 0])[3]}blocks:{state}:{focus}"
                for worker, (state, focus) in sorted(self.worker_state.items())
            )
            print(
                f"state={self.state} compiled_blocks={self.compiled_work_blocks}/{self.total_work_blocks} "
                f"certified_shards={self.completed}/{self.total} rate={rate:.1f}_blocks/s "
                f"eta={_duration(eta) if eta else '-'} written={self.bytes / 2**30:.2f}GiB "
                f"certified_blocks={self.blocks:,} origins={self.origins:,} failures={self.failures} active=[{active}]",
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
        rate = max(0.0, self.compiled_work_blocks / elapsed)
        remaining = max(0, self.total_work_blocks - self.compiled_work_blocks)
        eta = remaining / rate if rate else 0
        progress = Progress(
            TextColumn("[bold cyan]Compiled training blocks[/]"),
            BarColumn(complete_style="cyan", finished_style="green"),
            TextColumn("{task.completed:,.0f}/{task.total:,.0f}"),
            TextColumn("[bold]{task.percentage:>5.1f}%[/]"),
            expand=True,
        )
        progress.add_task(
            "compiled", total=max(1, self.total_work_blocks),
            completed=min(self.compiled_work_blocks, max(1, self.total_work_blocks)),
        )
        summary = Table.grid(expand=True, padding=(0, 2))
        if width >= 90:
            summary.add_column(); summary.add_column(); summary.add_column()
            summary.add_row(f"[bold]state[/] {self.state}", f"[bold]rate[/] {rate:.1f} blocks/s", f"[bold]ETA[/] {_duration(eta) if eta else '-'}")
            summary.add_row(f"[bold]certified[/] {self.completed:,}/{self.total:,} shards", f"[bold]written[/] {self.bytes / 2**30:.2f} GiB", f"[bold]origins[/] {self.origins:,}")
            summary.add_row(f"[bold]workers[/] {self.workers}", f"[bold]failures[/] {self.failures}", f"[bold]elapsed[/] {_duration(elapsed)}")
        else:
            summary.add_column(); summary.add_column()
            summary.add_row(f"[bold]state[/] {self.state}", f"[bold]ETA[/] {_duration(eta) if eta else '-'}")
            summary.add_row(f"[bold]rate[/] {rate:.1f} blocks/s", f"[bold]certified[/] {self.completed}/{self.total}")
            summary.add_row(f"[bold]failures[/] {self.failures}", f"[bold]elapsed[/] {_duration(elapsed)}")
        workers = Table(show_header=True, header_style="bold", expand=True)
        workers.add_column("worker", no_wrap=True)
        workers.add_column("assigned progress", ratio=1)
        workers.add_column("stage", no_wrap=True)
        workers.add_column("current ticker-month/session", ratio=2)
        worker_limit = self.workers if height >= 34 else min(self.workers, 4)
        for worker in range(worker_limit):
            state, focus = self.worker_state.get(worker, ("queued", ""))
            shards_done, shards_total, blocks_done, blocks_total, sessions, fetched = self.worker_progress.get(
                worker, [0, 0, 0, 0, 0, 0]
            )
            fraction = blocks_done / blocks_total if blocks_total else (
                1.0 if shards_done == shards_total and shards_total else 0.0
            )
            cells = 10
            filled = min(cells, int(fraction * cells))
            bar = f"[green]{'=' * filled}[/][dim]{'-' * (cells - filled)}[/] {blocks_done:,}/{blocks_total:,} blocks"
            detail = f"{focus or '-'} | shards {shards_done}/{shards_total}"
            blocks = fetched
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
            f"[bold]output[/] {self.root}\n"
            f"[bold]log[/] {self.run_log.events_path if self.run_log is not None else '-'}\n"
            "[bold]resume[/] rerun the same command; certified shards are skipped",
            title="Durability", border_style="blue",
        ), recent_panel)


def _duration(value: float) -> str:
    seconds = max(0, int(value))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}h {minutes:02d}m" if hours else (f"{minutes}m {secs:02d}s" if minutes else f"{secs}s")


def _resolve_cpu_threads_per_worker(*, workers: int, requested: int, logical_cpus: int | None = None) -> int:
    if requested > 0:
        return int(requested)
    available = max(1, int(logical_cpus or os.cpu_count() or 8))
    # Leave ClickHouse and the OS headroom while avoiding hundreds of native
    # allocator arenas.  Dense page conversion benefits from modest intra-op
    # parallelism; additional build workers supply the outer parallelism.
    cap = 8 if workers <= 40 else 6
    return max(1, min(cap, available // max(1, workers)))


def _resolve_max_concurrent_pages(*, workers: int, prefetch_pages: int, requested: int) -> int:
    if requested > 0:
        return int(requested)
    return max(1, min(32, int(workers) * max(1, int(prefetch_pages))))


def _partition_tickers(
    tickers: tuple[str, ...], workers: int, weights: dict[str, int] | None = None,
) -> list[tuple[str, ...]]:
    count = min(max(1, workers), len(tickers))
    buckets: list[list[str]] = [[] for _ in range(count)]
    loads = [0] * count
    order = {ticker: index for index, ticker in enumerate(tickers)}
    resolved_weights = {ticker: max(0, int((weights or {}).get(ticker, 0))) for ticker in tickers}
    for ticker in sorted(tickers, key=lambda item: (-resolved_weights[item], order[item])):
        target = min(range(count), key=lambda index: (loads[index], len(buckets[index]), index))
        buckets[target].append(ticker)
        loads[target] += resolved_weights[ticker]
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


def _run_main(args: argparse.Namespace, run_log: BuildRunLog | None) -> int:
    from research.bar_gpt.v1.train import preflight, sequential_coverage_counts

    config = build_data_config(args)
    root = args.output_root.resolve()
    expected_hash = config_hash(config)
    plan = planned_unit_keys(config)
    existing = {} if args.force_rebuild else completed_units(root, expected_hash)
    remaining = [key for key in plan if key not in existing]
    if args.max_shards:
        remaining = remaining[: int(args.max_shards)]
    if run_log is not None:
        run_log.record(
            "build_plan_resolved", durable=True, config_hash=expected_hash,
            planned_units=len(plan), certified_units=len(existing), remaining_units=len(remaining),
            output_root=str(root),
        )
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
    load_env_files(discover_clickhouse_env_files(), verbose=True)
    client = ClickHouseHttpClient(
        default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password()
    )
    evidence = preflight(client, config)
    coverage_config = dataclasses.replace(
        config,
        tickers=selected_tickers,
        validation_slices=(),
        validation_start_date=config.end_date,
    )
    _sessions, _exact_blocks, _exact_origins, unit_block_plan, _block_plan = sequential_coverage_counts(
        client, coverage_config, seed=17,
    )
    ticker_weights = {
        ticker: sum(
            int(unit_block_plan.get(key, (0, 0))[0])
            for key in allowed if key.partition(":")[0] == ticker
        )
        for ticker in selected_tickers
    }
    partitions = (
        _partition_tickers(selected_tickers, int(args.workers), ticker_weights)
        if selected_tickers else []
    )
    cpu_threads = _resolve_cpu_threads_per_worker(
        workers=max(1, len(partitions)), requested=int(args.cpu_threads_per_worker)
    )
    max_concurrent_pages = _resolve_max_concurrent_pages(
        workers=max(1, len(partitions)),
        prefetch_pages=int(config.clickhouse_prefetch_pages),
        requested=int(args.clickhouse_max_concurrent_pages),
    )
    _atomic_json(root / "manifest" / "build_plan.json", {
        "contract_version": OFFLINE_SHARD_CONTRACT_VERSION,
        "config_hash": expected_hash,
        "created_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_evidence": evidence,
        "storage_config": _storage_contract_config(config),
        "selection": {
            "tickers": list(config.tickers),
            "start_date": config.start_date,
            "end_date": config.end_date,
        },
        "planned_units": len(plan),
        "selected_units": len(remaining),
        "workers": len(partitions),
        "cpu_threads_per_worker": cpu_threads,
        "clickhouse_query_days": int(config.clickhouse_query_days),
        "clickhouse_prefetch_pages": int(config.clickhouse_prefetch_pages),
        "clickhouse_max_concurrent_pages": max_concurrent_pages,
        "ticker_partitions": [list(tickers) for tickers in partitions],
        "build_run_id": run_log.run_id if run_log is not None else "",
        "build_run_events": str(run_log.events_path) if run_log is not None else "",
        "selected_blocks": sum(int(unit_block_plan.get(key, (0, 0))[0]) for key in allowed),
        "selected_origins": sum(int(unit_block_plan.get(key, (0, 0))[1]) for key in allowed),
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
    partition_block_totals = [
        sum(int(unit_block_plan.get(key, (0, 0))[0]) for key in allowed if key.partition(":")[0] in set(tickers))
        for tickers in partitions
    ]
    skipped = frozenset(key for key in plan if key not in allowed)
    context = mp.get_context("spawn")
    events = context.Queue(maxsize=max(128, len(partitions) * 16))
    stop = context.Event()
    query_gate = context.BoundedSemaphore(max_concurrent_pages)
    ticker_queues = [deque(tickers) for tickers in partitions]
    active_processes: dict[int, Any] = {}
    active_tickers: dict[int, str] = {}
    worker_block_offsets = [0] * len(partitions)
    started_processes: list[Any] = []

    def launch_next_ticker(worker: int) -> None:
        ticker = ticker_queues[worker].popleft()
        fatal_path = (
            run_log.worker_fault_path(worker, ticker)
            if run_log is not None
            else root / "manifest" / f"worker-{worker:03d}-{ticker}-fatal.log"
        )
        process = context.Process(
            target=_ticker_worker_main,
            args=(
                worker, ticker, config, str(root), skipped, not args.skip_hash,
                events, stop, cpu_threads, query_gate, worker_block_offsets[worker], str(fatal_path),
            ),
            name=f"bar-gpt-shard-{worker}-{ticker}",
        )
        active_processes[worker] = process
        active_tickers[worker] = ticker
        started_processes.append(process)
        process.start()
        if run_log is not None:
            run_log.record(
                "worker_process_started", durable=True, worker=worker, ticker=ticker,
                pid=int(process.pid or -1), process_name=process.name, fault_log=str(fatal_path),
                progress_block_offset=worker_block_offsets[worker], cpu_threads=cpu_threads,
            )

    interrupted = False
    with ShardBuildReporter(
        total=len(existing) + len(remaining), completed=len(existing), root=root,
        workers=len(partitions), layout=args.progress_layout, refresh=args.refresh_seconds,
        initial_bytes=sum(int(item["bytes"]) for item in existing.values()),
        initial_blocks=sum(int(item["blocks"]) for item in existing.values()),
        initial_origins=sum(int(item["origins"]) for item in existing.values()),
        worker_totals=partition_totals,
        worker_block_totals=partition_block_totals,
        run_log=run_log,
    ) as reporter:
        for worker, tickers in enumerate(partitions):
            reporter.event((
                "worker", worker, "starting", ",".join(tickers),
                partition_totals[worker], partition_block_totals[worker],
            ))
            launch_next_ticker(worker)
        try:
            while active_processes:
                try:
                    event = events.get(timeout=float(args.refresh_seconds))
                    reporter.event(event)
                except queue.Empty:
                    reporter.refresh()
                while True:
                    try:
                        reporter.event(events.get_nowait())
                    except queue.Empty:
                        break
                if reporter.failures:
                    stop.set()
                for worker, process in tuple(active_processes.items()):
                    if process.is_alive():
                        continue
                    process.join(timeout=1)
                    del active_processes[worker]
                    ticker = active_tickers.pop(worker)
                    if process.exitcode not in {0, None}:
                        last_state, last_focus = reporter.worker_state.get(worker, ("unknown", ""))
                        fault_path = (
                            run_log.worker_fault_path(worker, ticker)
                            if run_log is not None
                            else root / "manifest" / f"worker-{worker:03d}-{ticker}-fatal.log"
                        )
                        reporter.event((
                            "process_exit", worker, ticker, int(process.pid or -1), int(process.exitcode),
                            str(fault_path), last_state, last_focus, reporter.worker_memory.get(worker, {}),
                        ))
                        stop.set()
                    else:
                        if run_log is not None:
                            run_log.record(
                                "worker_process_completed", worker=worker, ticker=ticker,
                                pid=int(process.pid or -1), exit_code=0,
                            )
                        worker_block_offsets[worker] += int(ticker_weights[ticker])
                    if process.exitcode in {0, None} and ticker_queues[worker] and not stop.is_set():
                        launch_next_ticker(worker)
                    else:
                        reporter.event((
                            "worker", worker,
                            "stopped" if stop.is_set() else "completed", "",
                        ))
        except KeyboardInterrupt:
            interrupted = True
            stop.set()
            reporter.state = "interrupted"
            reporter.message("Interrupt received; finishing atomic writes and stopping workers")
        finally:
            for process in active_processes.values():
                process.join(timeout=30)
            for worker, process in active_processes.items():
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=10)
                    if run_log is not None:
                        run_log.record(
                            "worker_process_terminated", durable=True,
                            worker=worker, ticker=active_tickers.get(worker, ""),
                            pid=int(process.pid or -1), process_name=process.name,
                            exit_code=process.exitcode,
                        )
            while True:
                try:
                    reporter.event(events.get_nowait())
                except queue.Empty:
                    break
            if not interrupted and (
                reporter.failures or any(process.exitcode not in {0, None} for process in started_processes)
            ):
                reporter.state = "failed"
                reporter.message("Build stopped after a worker failure; certified shards remain resumable")
                reporter.refresh(force=True)
    catalog = rebuild_catalog(root, expected_hash)
    if run_log is not None:
        run_log.record("catalog_rebuilt", durable=True, counts=catalog["counts"])
    print(f"Final certified catalog: {catalog['counts']}", flush=True)
    if interrupted:
        return 130
    return 1 if reporter.failures or any(
        process.exitcode not in {0, None} for process in started_processes
    ) else 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.execute:
        return _run_main(args, None)
    root = args.output_root.resolve()
    run_log = BuildRunLog(root, arguments=vars(args))
    print(f"Durable build diagnostics: {run_log.directory}", flush=True)
    try:
        exit_code = _run_main(args, run_log)
    except BaseException as exc:
        formatted_traceback = traceback.format_exc()
        run_log.record(
            "controller_exception", durable=True,
            exception_type=exc.__class__.__name__, message=str(exc), traceback=formatted_traceback,
        )
        run_log.finalize(
            status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
            exit_code=130 if isinstance(exc, KeyboardInterrupt) else 1,
            exception_type=exc.__class__.__name__, message=str(exc),
        )
        print(f"Failure diagnostics: {run_log.directory}", file=sys.stderr, flush=True)
        raise
    status = "completed" if exit_code == 0 else ("interrupted" if exit_code == 130 else "failed")
    run_log.finalize(status=status, exit_code=exit_code)
    print(f"Build diagnostics: {run_log.directory}", flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
