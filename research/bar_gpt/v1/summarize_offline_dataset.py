from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import os
from collections import defaultdict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Sequence

import numpy as np
import torch
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from research.bar_gpt.v1.features import MODEL_FEATURE_NAMES
from research.bar_gpt.v1.offline_shards import load_shard, load_shard_storage_config, materialize_block
from research.bar_gpt.v1.targets import (
    AUTOREGRESSIVE_TARGET_NAMES,
    DIRECTION_TARGET_COUNT,
    TARGET_NAMES,
)


DEFAULT_ROOT = Path(r"D:\TradingML\runtimes\bar_gpt\v1\offline_shards_v11")
DEFAULT_OUTPUT_ROOT = Path(r"D:\TradingML\runtimes\bar_gpt\v1\dataset_reports")
QUANTILES = (0.01, 0.05, 0.25, 0.50, 0.75, 0.95, 0.99)


def _csv_values(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item.strip().upper() for item in value.split(",") if item.strip()))


def _stable_seed(*parts: object) -> int:
    digest = hashlib.sha256("|".join(map(str, parts)).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "little", signed=False)


def _unit_parts(unit_key: str) -> tuple[str, str]:
    ticker, separator, month = str(unit_key).partition(":")
    if not separator or len(month) != 7:
        raise ValueError(f"invalid shard unit key: {unit_key!r}")
    return ticker.upper(), month


def _sample_indices(length: int, limit: int, *, seed: int) -> torch.Tensor:
    if length <= 0:
        return torch.empty(0, dtype=torch.long)
    if limit <= 0 or length <= limit:
        return torch.arange(length, dtype=torch.long)
    generator = torch.Generator().manual_seed(int(seed) % (2**63 - 1))
    return torch.randperm(length, generator=generator)[:limit].sort().values


@dataclass(slots=True)
class BoundedDistribution:
    capacity: int
    seed: int
    finite: int = 0
    nonfinite: int = 0
    zeros: int = 0
    positive: int = 0
    negative: int = 0
    total: float = 0.0
    total_squared: float = 0.0
    minimum: float = math.inf
    maximum: float = -math.inf
    _values: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.float64))
    _priorities: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.float64))
    _updates: int = 0

    def update(self, values: torch.Tensor | np.ndarray) -> None:
        array = values.detach().double().cpu().numpy() if isinstance(values, torch.Tensor) else np.asarray(values)
        array = np.asarray(array, dtype=np.float64).reshape(-1)
        if not array.size:
            return
        valid = np.isfinite(array)
        self.nonfinite += int((~valid).sum())
        finite = array[valid]
        if not finite.size:
            return
        self.finite += int(finite.size)
        self.zeros += int((finite == 0).sum())
        self.positive += int((finite > 0).sum())
        self.negative += int((finite < 0).sum())
        self.total += float(finite.sum(dtype=np.float64))
        self.total_squared += float(np.square(finite).sum(dtype=np.float64))
        self.minimum = min(self.minimum, float(finite.min()))
        self.maximum = max(self.maximum, float(finite.max()))
        if self.capacity <= 0:
            return
        rng = np.random.default_rng(_stable_seed(self.seed, self._updates))
        self._updates += 1
        priorities = rng.random(finite.size)
        values_all = np.concatenate((self._values, finite))
        priorities_all = np.concatenate((self._priorities, priorities))
        if values_all.size > self.capacity:
            selected = np.argpartition(priorities_all, -self.capacity)[-self.capacity :]
            values_all = values_all[selected]
            priorities_all = priorities_all[selected]
        self._values = values_all
        self._priorities = priorities_all

    def summary(self) -> dict[str, Any]:
        if not self.finite:
            return {
                "finite": 0, "nonfinite": self.nonfinite, "zero_rate": None,
                "positive_rate": None, "negative_rate": None, "mean": None,
                "std": None, "min": None, "max": None, "reservoir": 0,
                **{f"p{int(q * 100):02d}": None for q in QUANTILES},
            }
        mean = self.total / self.finite
        variance = max(0.0, self.total_squared / self.finite - mean * mean)
        quantiles = np.quantile(self._values, QUANTILES) if self._values.size else [math.nan] * len(QUANTILES)
        return {
            "finite": self.finite,
            "nonfinite": self.nonfinite,
            "zero_rate": self.zeros / self.finite,
            "positive_rate": self.positive / self.finite,
            "negative_rate": self.negative / self.finite,
            "mean": mean,
            "std": math.sqrt(variance),
            "min": self.minimum,
            "max": self.maximum,
            "reservoir": int(self._values.size),
            **{f"p{int(q * 100):02d}": float(value) for q, value in zip(QUANTILES, quantiles, strict=True)},
        }


@dataclass(slots=True)
class FieldDistribution:
    capacity: int
    seed: int
    directional: bool = False
    total: int = 0
    masked: int = 0
    neutral: int = 0
    direction_positive: int = 0
    direction_negative: int = 0
    model: BoundedDistribution = field(init=False)
    decoded: BoundedDistribution = field(init=False)

    def __post_init__(self) -> None:
        self.model = BoundedDistribution(self.capacity, self.seed)
        self.decoded = BoundedDistribution(self.capacity, self.seed + 1)

    def update(self, values: torch.Tensor, mask: torch.Tensor, decoded: torch.Tensor) -> None:
        mask = mask.bool()
        self.total += int(mask.numel())
        self.masked += int((~mask).sum())
        selected = values[mask]
        decoded_selected = decoded[mask]
        self.model.update(selected)
        self.decoded.update(decoded_selected)
        if self.directional:
            finite = decoded_selected[torch.isfinite(decoded_selected)]
            self.neutral += int((finite.abs() <= 1.0).sum())
            self.direction_positive += int((finite > 1.0).sum())
            self.direction_negative += int((finite < -1.0).sum())

    def summary(self) -> dict[str, Any]:
        selected = self.total - self.masked
        decoded = self.decoded.summary()
        return {
            "observations": self.total,
            "selected": selected,
            "masked": self.masked,
            "coverage": selected / self.total if self.total else None,
            "neutral_rate_1bp": self.neutral / decoded["finite"] if self.directional and decoded["finite"] else None,
            "direction_positive_rate_1bp": (
                self.direction_positive / decoded["finite"] if self.directional and decoded["finite"] else None
            ),
            "direction_negative_rate_1bp": (
                self.direction_negative / decoded["finite"] if self.directional and decoded["finite"] else None
            ),
            **{f"model_{key}": value for key, value in self.model.summary().items()},
            **{f"decoded_{key}": value for key, value in decoded.items()},
        }


@dataclass(slots=True)
class ContextDistribution:
    view: str
    configured: int
    capacity: int
    seed: int
    origins: int = 0
    full: int = 0
    partial: int = 0
    empty: int = 0
    available: BoundedDistribution = field(init=False)
    missing: BoundedDistribution = field(init=False)
    staleness_seconds: BoundedDistribution = field(init=False)

    def __post_init__(self) -> None:
        self.available = BoundedDistribution(self.capacity, self.seed)
        self.missing = BoundedDistribution(self.capacity, self.seed + 1)
        self.staleness_seconds = BoundedDistribution(self.capacity, self.seed + 2)

    def update(self, counts: torch.Tensor, staleness: torch.Tensor) -> None:
        counts = counts.long()
        self.origins += int(counts.numel())
        self.full += int((counts >= self.configured).sum())
        self.empty += int((counts == 0).sum())
        self.partial += int(((counts > 0) & (counts < self.configured)).sum())
        self.available.update(counts)
        self.missing.update((self.configured - counts).clamp_min(0))
        self.staleness_seconds.update(staleness)

    def summary(self) -> dict[str, Any]:
        return {
            "view": self.view, "configured_bars": self.configured, "sampled_origins": self.origins,
            "full_context_rate": self.full / self.origins if self.origins else None,
            "partial_context_rate": self.partial / self.origins if self.origins else None,
            "empty_context_rate": self.empty / self.origins if self.origins else None,
            **{f"available_{key}": value for key, value in self.available.summary().items()},
            **{f"missing_{key}": value for key, value in self.missing.summary().items()},
            **{f"staleness_seconds_{key}": value for key, value in self.staleness_seconds.summary().items()},
        }


def _decode_feature(name: str, values: torch.Tensor) -> tuple[torch.Tensor, str, str]:
    if name.endswith(("_close_return", "_open_gap", "_high_from_open_return", "_low_from_open_return")) or name == "midpoint_return":
        return torch.sinh(values) * 100.0, "bps", "asinh(log_return*100), decoded to bps"
    if name.endswith("_vwap_deviation_bps") or name.startswith("spread_") or name.startswith("microprice_lean_"):
        return torch.sinh(values) * 10.0, "bps", "asinh(bps/10), decoded to bps"
    if name.endswith("_size_cv"):
        return torch.sinh(values), "ratio", "asinh(ratio), decoded to ratio"
    if name.startswith("log_") or "_log_" in name or name == "trade_log_count":
        return torch.expm1(values), "raw", "log1p(raw), decoded to raw"
    return values, "model", "identity/bounded model value"


def _decode_target(name: str, values: torch.Tensor) -> tuple[torch.Tensor, str, str, bool]:
    if name.endswith("_return"):
        return torch.sinh(values) * 100.0, "bps", "asinh(log_return*100), decoded to bps", True
    if name == "trade_realized_volatility":
        return torch.sinh(values) * 100.0, "bps", "asinh(realized_log_volatility*100), decoded to bps", False
    if name.startswith("log_"):
        return torch.expm1(values), "raw", "log1p(raw), decoded to raw", False
    return values, "binary", "identity binary", False


def _context_counts(block: Any, view: str, origins: torch.Tensor, configured: int) -> tuple[torch.Tensor, torch.Tensor]:
    mask = block.view_mask[view].long()
    prefix = torch.cat((torch.zeros(1, dtype=torch.long), mask.cumsum(0)))
    if view == "1s":
        end = block.origin_indices[origins].long()
    else:
        asof = block.asof_indices[view][origins].long()
        end = torch.where(asof >= 0, asof + 1, torch.zeros_like(asof))
    start = (end - int(configured)).clamp_min(0)
    counts = prefix[end] - prefix[start]
    newest = end - 1
    valid = newest >= 0
    staleness = torch.full((origins.numel(),), float("nan"), dtype=torch.float64)
    if bool(valid.any()):
        available = block.view_available_at_us[view][newest[valid]].double()
        staleness[valid] = (block.origin_timestamps_us[origins[valid]].double() - available) / 1_000_000.0
    return counts, staleness


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(temporary, path)


def _atomic_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def _discover_sidecars(root: Path, tickers: set[str], start_date: str, end_date: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted((root / "tickers").glob("*/*/*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        ticker, month = _unit_parts(str(value.get("unit_key", "")))
        if tickers and ticker not in tickers:
            continue
        if start_date and month < start_date[:7]:
            continue
        if end_date and month >= end_date[:7]:
            continue
        rows.append({**value, "sidecar_path": str(path), "tensor_path": str(path.with_suffix(".pt"))})
    return rows


def _select_sample(rows: Sequence[dict[str, Any]], limit: int, seed: int) -> list[dict[str, Any]]:
    complete = [row for row in rows if row.get("status") == "complete"]
    if limit <= 0 or len(complete) <= limit:
        return complete
    earliest: dict[str, dict[str, Any]] = {}
    for row in complete:
        ticker, month = _unit_parts(str(row["unit_key"]))
        if ticker not in earliest or month < _unit_parts(str(earliest[ticker]["unit_key"]))[1]:
            earliest[ticker] = row
    selected = list(earliest.values())[:limit]
    selected_keys = {str(row["unit_key"]) for row in selected}
    remaining = sorted(
        (row for row in complete if str(row["unit_key"]) not in selected_keys),
        key=lambda row: _stable_seed(seed, row["unit_key"]),
    )
    return [*selected, *remaining[: max(0, limit - len(selected))]]


def _padding_statistics(lengths: Sequence[int], batch_sizes: Sequence[int], seed: int) -> list[dict[str, Any]]:
    if not lengths:
        return []
    order = np.random.default_rng(seed).permutation(np.asarray(lengths, dtype=np.int64))
    rows = []
    for batch_size in batch_sizes:
        valid = padded = batches = 0
        for start in range(0, len(order), batch_size):
            batch = order[start : start + batch_size]
            if not batch.size:
                continue
            valid += int(batch.sum())
            padded += int(batch.max()) * int(batch.size)
            batches += 1
        rows.append({
            "batch_size": int(batch_size), "simulated_batches": batches,
            "valid_origins": valid, "allocated_origin_slots": padded,
            "valid_fraction": valid / padded if padded else None,
            "padding_fraction": 1.0 - valid / padded if padded else None,
        })
    return rows


@dataclass(slots=True)
class PreparedBlockSample:
    input_values: dict[str, torch.Tensor]
    input_masks: dict[str, torch.Tensor]
    context: dict[str, tuple[torch.Tensor, torch.Tensor]]
    horizon_values: torch.Tensor
    horizon_mask: torch.Tensor
    autoregressive_values: dict[str, torch.Tensor]
    autoregressive_masks: dict[str, torch.Tensor]
    sampled_origins: int
    integrity_findings: dict[str, int]


@dataclass(slots=True)
class PreparedShardSample:
    unit_key: str
    ticker: str
    year: str
    block_lengths: list[int]
    blocks: list[PreparedBlockSample]
    integrity_findings: dict[str, int]


def _prepare_shard_sample(row: dict[str, Any], config: Any, args: argparse.Namespace) -> PreparedShardSample:
    unit_key = str(row["unit_key"])
    ticker, month = _unit_parts(unit_key)
    integrity: dict[str, int] = defaultdict(int)
    try:
        shard = load_shard(Path(row["tensor_path"]))
        if int(shard.get("contract_version", -1)) != int(row.get("contract_version", -2)):
            integrity["sidecar_contract_mismatch"] += 1
        if str(shard.get("config_hash", "")) != str(row.get("config_hash", "")):
            integrity["sidecar_config_hash_mismatch"] += 1
        refs = [
            (session_index, block_index, block)
            for session_index, session in enumerate(shard["sessions"])
            for block_index, block in enumerate(session["blocks"])
        ]
        block_lengths = [int(block["origin_indices"].numel()) for _, _, block in refs]
        order = sorted(
            range(len(refs)), key=lambda index: _stable_seed(args.seed, unit_key, refs[index][0], refs[index][1])
        )[: int(args.blocks_per_shard)]
        prepared_blocks: list[PreparedBlockSample] = []
        context_sizes = {**config.intraday_context_by_name, **config.calendar_context_by_name}
        for ordinal in order:
            session_index, block_index, _stored = refs[ordinal]
            block = materialize_block(shard, session_index, block_index)
            block_integrity: dict[str, int] = defaultdict(int)
            origin_rows = _sample_indices(
                int(block.origin_indices.numel()), int(args.origins_per_block),
                seed=_stable_seed(args.seed, unit_key, session_index, block_index, "origins"),
            )
            if origin_rows.numel() > 1 and not bool(torch.all(
                block.origin_timestamps_us[origin_rows][1:] > block.origin_timestamps_us[origin_rows][:-1]
            )):
                block_integrity["non_increasing_sampled_origin_timestamps"] += 1
            input_values: dict[str, torch.Tensor] = {}
            input_masks: dict[str, torch.Tensor] = {}
            for view, values in block.views.items():
                mask = block.view_mask[view].bool()
                if bool(torch.any(values[~mask] != 0)):
                    block_integrity["nonzero_masked_input_rows"] += 1
                rows = _sample_indices(
                    int(values.shape[0]), int(args.rows_per_view),
                    seed=_stable_seed(args.seed, unit_key, session_index, block_index, view),
                )
                input_values[view] = values[rows].clone()
                input_masks[view] = mask[rows].clone()
            prepared_context: dict[str, tuple[torch.Tensor, torch.Tensor]] = {}
            for view, configured in context_sizes.items():
                counts, staleness = _context_counts(block, view, origin_rows, configured)
                if bool(torch.any(staleness[torch.isfinite(staleness)] < 0)):
                    block_integrity["future_context_selection"] += 1
                prepared_context[view] = (counts.clone(), staleness.clone())
            horizon_values = block.horizon_targets[origin_rows].clone()
            horizon_mask = block.horizon_mask[origin_rows].clone()
            ar_values: dict[str, torch.Tensor] = {}
            ar_masks: dict[str, torch.Tensor] = {}
            for view, values in block.autoregressive_targets.items():
                rows = _sample_indices(
                    int(values.shape[0]), int(args.rows_per_view),
                    seed=_stable_seed(args.seed, unit_key, session_index, block_index, view, "ar"),
                )
                ar_values[view] = values[rows].clone()
                ar_masks[view] = block.autoregressive_mask[view][rows].clone()
            prepared_blocks.append(PreparedBlockSample(
                input_values=input_values,
                input_masks=input_masks,
                context=prepared_context,
                horizon_values=horizon_values,
                horizon_mask=horizon_mask,
                autoregressive_values=ar_values,
                autoregressive_masks=ar_masks,
                sampled_origins=int(origin_rows.numel()),
                integrity_findings=dict(block_integrity),
            ))
        return PreparedShardSample(
            unit_key=unit_key,
            ticker=ticker,
            year=month[:4],
            block_lengths=block_lengths,
            blocks=prepared_blocks,
            integrity_findings=dict(integrity),
        )
    except Exception as exc:
        raise RuntimeError(f"failed to sample shard {unit_key}: {type(exc).__name__}: {exc}") from exc


def _iter_prepared_shards(
    rows: Sequence[dict[str, Any]], config: Any, args: argparse.Namespace,
) -> Iterator[PreparedShardSample]:
    workers = min(max(1, int(args.workers)), max(1, len(rows)))
    if workers == 1:
        for row in rows:
            yield _prepare_shard_sample(row, config, args)
        return
    pending_limit = min(len(rows), workers * 2)
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="bar-gpt-summary") as executor:
        futures: dict[int, Future[PreparedShardSample]] = {}
        next_submit = 0
        while next_submit < pending_limit:
            futures[next_submit] = executor.submit(_prepare_shard_sample, rows[next_submit], config, args)
            next_submit += 1
        for next_result in range(len(rows)):
            future = futures.pop(next_result)
            yield future.result()
            if next_submit < len(rows):
                futures[next_submit] = executor.submit(_prepare_shard_sample, rows[next_submit], config, args)
                next_submit += 1


def _update_autoregressive_distributions(
    *,
    view: str,
    values: torch.Tensor,
    mask: torch.Tensor,
    distributions: dict[tuple[str, str], FieldDistribution],
    target_meta: dict[str, tuple[str, str, bool]],
    capacity: int,
    seed: int,
) -> None:
    for target_index, target_name in enumerate(AUTOREGRESSIVE_TARGET_NAMES):
        key = (view, target_name)
        decoded, unit, transform, directional = _decode_target(target_name, values[:, target_index])
        target_meta[target_name] = (unit, transform, directional)
        if key not in distributions:
            distributions[key] = FieldDistribution(
                capacity, _stable_seed(seed, "ar", *key), directional=directional
            )
        distributions[key].update(values[:, target_index], mask[:, target_index], decoded)


def summarize(args: argparse.Namespace, console: Console) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    root = Path(args.root)
    config = load_shard_storage_config(root)
    sidecars = _discover_sidecars(root, set(_csv_values(args.tickers)), args.start_date, args.end_date)
    if not sidecars:
        raise RuntimeError("no shard sidecars matched the requested scope")
    sample = _select_sample(sidecars, int(args.sample_shards), int(args.seed))
    capacity = int(args.reservoir_size)
    inputs: dict[tuple[str, str], FieldDistribution] = {}
    physical: dict[tuple[int, str], FieldDistribution] = {}
    autoregressive: dict[tuple[str, str], FieldDistribution] = {}
    context = {
        view: ContextDistribution(view, count, capacity, _stable_seed(args.seed, "context", view))
        for view, count in {**config.intraday_context_by_name, **config.calendar_context_by_name}.items()
    }
    context_by_year: dict[tuple[str, str], ContextDistribution] = {}
    context_by_ticker: dict[tuple[str, str], ContextDistribution] = {}
    input_meta: dict[str, tuple[str, str]] = {}
    target_meta: dict[str, tuple[str, str, bool]] = {}
    integrity = defaultdict(int)
    sampled_blocks = 0
    sampled_origins = 0
    block_lengths: list[int] = []
    progress = Progress(
        SpinnerColumn(), TextColumn("[cyan]Sampling shards[/]"), BarColumn(), TaskProgressColumn(),
        TextColumn("{task.fields[unit]}"), TimeElapsedColumn(), console=console,
        disable=not console.is_terminal,
    )
    task = progress.add_task("scan", total=len(sample), unit="")
    progress.start()
    try:
        for shard_index, prepared in enumerate(_iter_prepared_shards(sample, config, args)):
            unit_key = prepared.unit_key
            ticker = prepared.ticker
            year = prepared.year
            progress.update(task, unit=unit_key)
            block_lengths.extend(prepared.block_lengths)
            for name, count in prepared.integrity_findings.items():
                integrity[name] += count
            for block in prepared.blocks:
                sampled_blocks += 1
                sampled_origins += block.sampled_origins
                for name, count in block.integrity_findings.items():
                    integrity[name] += count
                for view, values in block.input_values.items():
                    row_mask = block.input_masks[view]
                    for feature_index, feature_name in enumerate(MODEL_FEATURE_NAMES):
                        key = (view, feature_name)
                        if key not in inputs:
                            inputs[key] = FieldDistribution(capacity, _stable_seed(args.seed, "input", *key))
                        decoded, unit, transform = _decode_feature(feature_name, values[:, feature_index])
                        input_meta[feature_name] = (unit, transform)
                        inputs[key].update(values[:, feature_index], row_mask, decoded)
                for view, (counts, staleness) in block.context.items():
                    stats = context[view]
                    stats.update(counts, staleness)
                    year_key = (year, view)
                    ticker_key = (ticker, view)
                    if year_key not in context_by_year:
                        context_by_year[year_key] = ContextDistribution(
                            view, stats.configured, capacity, _stable_seed(args.seed, "context-year", *year_key)
                        )
                    if ticker_key not in context_by_ticker:
                        context_by_ticker[ticker_key] = ContextDistribution(
                            view, stats.configured, capacity, _stable_seed(args.seed, "context-ticker", *ticker_key)
                        )
                    context_by_year[year_key].update(counts, staleness)
                    context_by_ticker[ticker_key].update(counts, staleness)
                horizon_values = block.horizon_values
                horizon_mask = block.horizon_mask
                for horizon_index, horizon_us in enumerate(config.horizons_us):
                    for target_index, target_name in enumerate(TARGET_NAMES):
                        key = (int(horizon_us), target_name)
                        decoded, unit, transform, directional = _decode_target(
                            target_name, horizon_values[:, horizon_index, target_index]
                        )
                        target_meta[target_name] = (unit, transform, directional)
                        if key not in physical:
                            physical[key] = FieldDistribution(
                                capacity, _stable_seed(args.seed, "physical", *key), directional=directional
                            )
                        physical[key].update(
                            horizon_values[:, horizon_index, target_index],
                            horizon_mask[:, horizon_index, target_index], decoded,
                        )
                for view, values in block.autoregressive_values.items():
                    _update_autoregressive_distributions(
                        view=view,
                        values=values,
                        mask=block.autoregressive_masks[view],
                        distributions=autoregressive,
                        target_meta=target_meta,
                        capacity=capacity,
                        seed=int(args.seed),
                    )
            progress.advance(task)
            if not console.is_terminal and (shard_index + 1 == len(sample) or (shard_index + 1) % 25 == 0):
                console.print(f"Sampled {shard_index + 1}/{len(sample)} shards; current {unit_key}")
    finally:
        progress.stop()

    inventory_rows = [
        {
            "unit_key": row["unit_key"], "status": row.get("status"), "sessions": int(row.get("sessions", 0)),
            "blocks": int(row.get("blocks", 0)), "origins": int(row.get("origins", 0)),
            "bytes": int(row.get("bytes", 0)),
            "mean_origins_per_block": int(row.get("origins", 0)) / max(1, int(row.get("blocks", 0))),
        }
        for row in sidecars
    ]
    inventory_groups: dict[tuple[str, str], dict[str, Any]] = {}
    for row in inventory_rows:
        ticker, month = _unit_parts(str(row["unit_key"]))
        key = (ticker, month[:4])
        group = inventory_groups.setdefault(key, {
            "ticker": ticker, "year": month[:4], "units": 0, "complete_units": 0,
            "covered_empty_units": 0, "sessions": 0, "blocks": 0, "origins": 0, "bytes": 0,
        })
        group["units"] += 1
        group["complete_units"] += int(row["status"] == "complete")
        group["covered_empty_units"] += int(row["status"] == "covered_empty")
        for name in ("sessions", "blocks", "origins", "bytes"):
            group[name] += int(row[name])
    inventory_group_rows = [
        {**group, "mean_origins_per_block": group["origins"] / max(1, group["blocks"])}
        for _key, group in sorted(inventory_groups.items())
    ]
    input_rows = [
        {"view": view, "feature": name, "decoded_unit": input_meta[name][0], "preprocessing": input_meta[name][1], **stats.summary()}
        for (view, name), stats in sorted(inputs.items())
    ]
    physical_rows = [
        {
            "horizon_us": horizon, "horizon_seconds": horizon / 1_000_000.0, "target": name,
            "decoded_unit": target_meta[name][0], "preprocessing": target_meta[name][1], **stats.summary(),
        }
        for (horizon, name), stats in sorted(physical.items())
    ]
    ar_rows = [
        {"view": view, "target": name, "decoded_unit": target_meta[name][0], "preprocessing": target_meta[name][1], **stats.summary()}
        for (view, name), stats in sorted(autoregressive.items())
    ]
    context_rows = [context[view].summary() for view in context]
    context_year_rows = [
        {"year": year, **stats.summary()} for (year, _view), stats in sorted(context_by_year.items())
    ]
    context_ticker_rows = [
        {"ticker": ticker, **stats.summary()} for (ticker, _view), stats in sorted(context_by_ticker.items())
    ]
    padding_rows = _padding_statistics(block_lengths, tuple(args.batch_sizes), int(args.seed))
    status_counts: dict[str, int] = defaultdict(int)
    for row in sidecars:
        status_counts[str(row.get("status", "unknown"))] += 1
    complete = [row for row in inventory_rows if row["status"] == "complete"]
    report = {
        "report_version": 1,
        "created_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "root": str(root),
        "scope": {"tickers": list(_csv_values(args.tickers)), "start_date": args.start_date, "end_date": args.end_date},
        "sampling": {
            "seed": int(args.seed), "candidate_complete_shards": len(complete), "sampled_shards": len(sample),
            "workers": min(max(1, int(args.workers)), max(1, len(sample))),
            "torch_threads": int(args.torch_threads),
            "blocks_per_shard": int(args.blocks_per_shard), "sampled_blocks": sampled_blocks,
            "rows_per_view": int(args.rows_per_view), "origins_per_block": int(args.origins_per_block),
            "sampled_origins": sampled_origins, "reservoir_size_per_field": capacity,
        },
        "inventory": {
            "units": len(sidecars), "status_counts": dict(status_counts),
            "sessions": sum(row["sessions"] for row in complete), "blocks": sum(row["blocks"] for row in complete),
            "origins": sum(row["origins"] for row in complete), "bytes": sum(row["bytes"] for row in complete),
        },
        "integrity_findings": dict(integrity),
        "context": context_rows,
        "padding": padding_rows,
        "field_counts": {
            "input_rows": len(input_rows), "physical_target_rows": len(physical_rows),
            "autoregressive_target_rows": len(ar_rows),
        },
    }
    tables = {
        "inventory_by_unit": inventory_rows,
        "inventory_by_ticker_year": inventory_group_rows,
        "input_statistics": input_rows,
        "physical_target_statistics": physical_rows,
        "autoregressive_target_statistics": ar_rows,
        "context_statistics": context_rows,
        "context_by_year": context_year_rows,
        "context_by_ticker": context_ticker_rows,
        "padding_statistics": padding_rows,
    }
    return report, tables


def _render(report: dict[str, Any], output: Path, console: Console) -> None:
    inventory = report["inventory"]
    sampling = report["sampling"]
    console.print(Panel.fit(
        f"[bold green]Dataset summary completed[/]\n"
        f"{inventory['origins']:,} origins  •  {inventory['blocks']:,} blocks  •  "
        f"{inventory['bytes'] / 2**30:,.2f} GiB\n"
        f"Sample: {sampling['sampled_shards']:,} shards / {sampling['sampled_blocks']:,} blocks / "
        f"{sampling['sampled_origins']:,} origins  •  {sampling['workers']} workers",
        title="BarGPT v1 offline shards", border_style="cyan",
    ))
    context = Table(title="Sampled historical-context coverage", box=None, pad_edge=False)
    context.add_column("View", style="cyan")
    context.add_column("Configured", justify="right")
    context.add_column("Full", justify="right")
    context.add_column("Partial", justify="right")
    context.add_column("Empty", justify="right")
    context.add_column("Median available", justify="right")
    for row in report["context"]:
        context.add_row(
            row["view"], str(row["configured_bars"]),
            f"{100 * (row['full_context_rate'] or 0):.2f}%",
            f"{100 * (row['partial_context_rate'] or 0):.2f}%",
            f"{100 * (row['empty_context_rate'] or 0):.2f}%",
            f"{row['available_p50'] or 0:,.0f}",
        )
    console.print(context)
    padding = Table(title="Simulated origin padding", box=None, pad_edge=False)
    padding.add_column("Microbatch", justify="right")
    padding.add_column("Valid work", justify="right")
    padding.add_column("Padding", justify="right")
    padding.add_column("Batches", justify="right")
    for row in report["padding"]:
        padding.add_row(
            str(row["batch_size"]), f"{100 * row['valid_fraction']:.2f}%",
            f"{100 * row['padding_fraction']:.2f}%", f"{row['simulated_batches']:,}",
        )
    console.print(padding)
    findings = report["integrity_findings"]
    console.print(
        f"[bold {'green' if not findings else 'red'}]Integrity findings:[/] "
        f"{'none' if not findings else json.dumps(findings, sort_keys=True)}"
    )
    console.print(f"Detailed JSON and CSV tables: [cyan]{output}[/]")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize BarGPT offline-shard inventory and deterministic per-field distributions."
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--tickers", default="", help="Optional comma-separated ticker filter.")
    parser.add_argument("--start-date", default="", help="Optional inclusive YYYY-MM-DD boundary.")
    parser.add_argument("--end-date", default="", help="Optional exclusive YYYY-MM-DD boundary.")
    parser.add_argument("--sample-shards", type=int, default=256, help="Tensor shards to sample; zero scans all.")
    parser.add_argument("--blocks-per-shard", type=int, default=1)
    parser.add_argument("--rows-per-view", type=int, default=256)
    parser.add_argument("--origins-per-block", type=int, default=512)
    parser.add_argument("--reservoir-size", type=int, default=4096)
    parser.add_argument("--workers", type=int, default=8, help="Concurrent bounded shard preparation workers.")
    parser.add_argument("--torch-threads", type=int, default=1, help="Torch CPU threads shared by this process.")
    parser.add_argument("--batch-sizes", default="8,16,32")
    parser.add_argument("--seed", type=int, default=17)
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.sample_shards < 0 or min(
        args.blocks_per_shard, args.rows_per_view, args.origins_per_block,
        args.reservoir_size, args.workers, args.torch_threads,
    ) <= 0:
        parser.error("sample-shards cannot be negative and all other sample sizes must be positive")
    try:
        args.batch_sizes = tuple(int(value) for value in str(args.batch_sizes).split(",") if int(value) > 0)
    except ValueError as exc:
        parser.error(f"invalid --batch-sizes: {exc}")
    if not args.batch_sizes:
        parser.error("--batch-sizes must contain at least one positive integer")
    if bool(args.start_date) != bool(args.end_date):
        parser.error("--start-date and --end-date must be provided together")
    if args.start_date and dt.date.fromisoformat(args.start_date) >= dt.date.fromisoformat(args.end_date):
        parser.error("date range must be non-empty")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    torch.set_num_threads(int(args.torch_threads))
    console = Console()
    report, tables = summarize(args, console)
    run = Path(args.output_root) / f"summary-{dt.datetime.now():%Y%m%d-%H%M%S}-p{os.getpid()}"
    run.mkdir(parents=True, exist_ok=False)
    _atomic_json(run / "summary.json", report)
    _atomic_json(run / "statistics.json", tables)
    for name, rows in tables.items():
        _atomic_csv(run / f"{name}.csv", rows)
    _render(report, run, console)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
