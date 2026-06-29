from __future__ import annotations

import datetime as dt
import json
import random
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

import numpy as np

from research.mlops.clickhouse_events import encode_unified_event_windows
from research.mlops.compact_events import EVENT_BYTES, HEADER_BYTES
from research.mlops.data.contracts import FUTURE_BAR_FEATURE_KEYS
from research.mlops.rolling_loader.ticker_month_cache import (
    EVENT_PAYLOAD_COLUMNS,
    EVENT_TIME_FEATURE_COLUMNS,
    TICKER_MONTH_CACHE_FORMAT,
    TICKER_MONTH_CACHE_VERSION,
    full_months_in_period,
    read_json,
)


DEFAULT_EVENT_OUTPUT_MODE = "raw_windows"
SUPPORTED_EVENT_OUTPUT_MODES = {"none", "raw_flat", "raw_windows", "encoded_uint8"}
DEFAULT_DATA_GROUPS = ("events", "intraday_labels")
NUMERIC_EVENT_COLUMNS: tuple[str, ...] = tuple(column for column in (*EVENT_PAYLOAD_COLUMNS, *EVENT_TIME_FEATURE_COLUMNS) if column != "ticker")
ENCODER_EVENT_DTYPE = np.dtype(
    [
        ("ordinal", "<u8"),
        ("event_type", "u1"),
        ("sip_timestamp_us", "<u8"),
        ("price_primary_int", "<u4"),
        ("price_secondary_int", "<u4"),
        ("size_primary", "<f4"),
        ("size_secondary", "<f4"),
        ("exchange_primary", "u1"),
        ("exchange_secondary", "u1"),
        ("event_flags", "u1"),
        ("conditions_packed", "<u4"),
    ]
)


@dataclass(frozen=True, slots=True)
class TickerMonthLoaderConfig:
    cache_root: Path
    split: str = "train"
    start_utc: str = ""
    end_utc: str = ""
    months: tuple[str, ...] = ()
    tickers: tuple[str, ...] = ()
    batch_size: int = 4096
    seed: int = 17
    data_groups: tuple[str, ...] = DEFAULT_DATA_GROUPS
    event_output_mode: str = DEFAULT_EVENT_OUTPUT_MODE
    events_per_window: int = 128
    context_chunks: int = 32
    context_stride_events: int = 64
    flat_coverage_events: int = 0
    loaded_parts_per_group: int = 8
    read_workers: int = 4
    materialize_workers: int = 4
    max_batches: int = 0
    shuffle_parts: bool = True
    shuffle_within_loaded_group: bool = True
    include_external_context: bool = False
    strict_audit: bool = True


@dataclass(frozen=True, slots=True)
class TickerMonthPartPlan:
    month: str
    ticker: str
    package_dir: Path
    part_id: int
    files: Mapping[str, str]
    config: Mapping[str, Any]
    origin_count: int
    event_count: int
    label_count: int
    origin_ordinal_start: int
    origin_ordinal_end: int
    fetch_ordinal_start: int
    fetch_ordinal_end: int


@dataclass(slots=True)
class LoadedTickerMonthPart:
    plan: TickerMonthPartPlan
    events: Any | None = None
    origins: Any | None = None
    windows: Any | None = None
    labels: Any | None = None
    context: dict[str, Any] = field(default_factory=dict)
    _event_arrays: dict[str, np.ndarray] = field(default_factory=dict, init=False, repr=False)

    def event_array(self, column: str) -> np.ndarray:
        if self.events is None:
            raise RuntimeError("Part events were not loaded.")
        if column not in self._event_arrays:
            self._event_arrays[column] = self.events.get_column(column).to_numpy()
        return self._event_arrays[column]


@dataclass(frozen=True, slots=True)
class TickerMonthSampleRef:
    part_index: int
    origin_row: int


@dataclass(slots=True)
class TickerMonthTrainingBatch:
    ticker: np.ndarray
    origin_ordinal: np.ndarray
    origin_timestamp_us: np.ndarray
    event_output_mode: str
    raw_event_windows: dict[str, np.ndarray] = field(default_factory=dict)
    raw_event_flat: dict[str, np.ndarray] = field(default_factory=dict)
    raw_event_mask: np.ndarray = field(default_factory=lambda: np.zeros((0, 0), dtype=np.bool_))
    headers_uint8: np.ndarray = field(default_factory=lambda: np.zeros((0, 0, HEADER_BYTES), dtype=np.uint8))
    events_uint8: np.ndarray = field(default_factory=lambda: np.zeros((0, 0, 128, EVENT_BYTES), dtype=np.uint8))
    intraday_labels: dict[str, np.ndarray] = field(default_factory=dict)
    future_intraday_bar_horizons: tuple[str, ...] = ()
    future_intraday_bars: np.ndarray = field(default_factory=lambda: np.zeros((0, 0, len(FUTURE_BAR_FEATURE_KEYS)), dtype=np.float32))
    future_intraday_bar_mask: np.ndarray = field(default_factory=lambda: np.zeros((0, 0), dtype=np.bool_))
    input_availability: dict[str, np.ndarray] = field(default_factory=dict)
    external_context: dict[str, Any] = field(default_factory=dict)
    profile: dict[str, float | int] = field(default_factory=dict)

    @property
    def sample_count(self) -> int:
        return int(self.origin_ordinal.shape[0])


class TickerMonthCacheIndex:
    def __init__(self, config: TickerMonthLoaderConfig) -> None:
        self.config = normalize_loader_config(config)
        self.root_manifest = _read_root_manifest(Path(self.config.cache_root))
        self.parts = self._discover_parts()

    def _discover_parts(self) -> list[TickerMonthPartPlan]:
        root = Path(self.config.cache_root)
        split_dir = root / str(self.config.split)
        if not split_dir.exists():
            raise FileNotFoundError(f"Missing cache split directory: {split_dir}")
        selected_months = set(_selected_months(self.config, self.root_manifest))
        selected_tickers = {ticker.upper() for ticker in self.config.tickers}
        plans: list[TickerMonthPartPlan] = []
        for package_dir in sorted(split_dir.glob("month=*/ticker_hash=*/ticker=*")):
            if not package_dir.is_dir():
                continue
            month = _path_value(package_dir, "month")
            ticker = _path_value(package_dir, "ticker").upper()
            if selected_months and month not in selected_months:
                continue
            if selected_tickers and ticker not in selected_tickers:
                continue
            manifest_path = package_dir / "manifest.json"
            if not manifest_path.exists():
                continue
            manifest = read_json(manifest_path)
            if manifest.get("status") != "complete":
                continue
            package_config = manifest.get("config") or {}
            for part in manifest.get("parts") or ():
                if not isinstance(part, Mapping):
                    continue
                files = part.get("files") or {}
                counts = part.get("counts") or {}
                origin_count = int(counts.get("origins") or 0)
                if origin_count <= 0:
                    continue
                plans.append(
                    TickerMonthPartPlan(
                        month=str(month),
                        ticker=str(ticker),
                        package_dir=package_dir,
                        part_id=int(part.get("part_id") or 0),
                        files={str(key): str(value) for key, value in files.items()},
                        config=package_config,
                        origin_count=origin_count,
                        event_count=int(counts.get("events") or 0),
                        label_count=int(counts.get("intraday_forward_labels") or 0),
                        origin_ordinal_start=int(part.get("origin_ordinal_start") or 0),
                        origin_ordinal_end=int(part.get("origin_ordinal_end") or 0),
                        fetch_ordinal_start=int(part.get("fetch_ordinal_start") or 0),
                        fetch_ordinal_end=int(part.get("fetch_ordinal_end") or 0),
                    )
                )
        if not plans:
            raise RuntimeError(f"No complete ticker/month parts found under {split_dir}")
        return plans


class TickerMonthPartReader:
    def __init__(self, data_groups: Sequence[str], *, include_external_context: bool = False) -> None:
        self.data_groups = set(str(group) for group in data_groups)
        self.include_external_context = bool(include_external_context)

    def load(self, plan: TickerMonthPartPlan) -> LoadedTickerMonthPart:
        pl = _polars()
        loaded = LoadedTickerMonthPart(plan=plan)
        need_events = bool({"events", "event_windows", "encoded_events"}.intersection(self.data_groups))
        need_origins = True
        need_labels = "intraday_labels" in self.data_groups or "labels" in self.data_groups
        if need_events:
            loaded.events = pl.read_parquet(plan.package_dir / plan.files["events"])
        if need_origins:
            loaded.origins = pl.read_parquet(plan.package_dir / plan.files["origins"])
        if need_events and "event_window_index" in plan.files:
            loaded.windows = pl.read_parquet(plan.package_dir / plan.files["event_window_index"])
        if need_labels:
            loaded.labels = pl.read_parquet(plan.package_dir / plan.files["intraday_forward_labels"])
        if self.include_external_context:
            for key, filename in _package_context_files(plan.package_dir).items():
                if key in self.data_groups:
                    loaded.context[key] = pl.read_parquet(plan.package_dir / filename)
        return loaded


class TickerMonthBatchMaterializer:
    def __init__(self, config: TickerMonthLoaderConfig) -> None:
        self.config = normalize_loader_config(config)
        self.context_lags = tuple(index * int(self.config.context_stride_events) for index in range(int(self.config.context_chunks)))
        self.coverage_events = max(self.context_lags, default=0) + int(self.config.events_per_window)
        if int(self.config.flat_coverage_events) > 0:
            self.coverage_events = max(int(self.config.flat_coverage_events), int(self.coverage_events))

    def validate_part_config(self, plan: TickerMonthPartPlan) -> None:
        cached = int(plan.config.get("max_cached_event_lookback_rows") or plan.config.get("required_event_lookback_rows") or 0)
        required = int(self.coverage_events)
        if required > cached:
            raise ValueError(
                f"Requested event coverage {required:,} exceeds cached lookback {cached:,} for {plan.month}:{plan.ticker}:part_{plan.part_id:05d}. "
                "Rebuild cache with larger --max-cached-event-lookback-rows."
            )
        if self.config.event_output_mode == "encoded_uint8" and int(self.config.events_per_window) != 128:
            raise ValueError("encoded_uint8 mode requires events_per_window=128.")

    def materialize(self, parts: Sequence[LoadedTickerMonthPart], refs: Sequence[TickerMonthSampleRef]) -> TickerMonthTrainingBatch:
        start = time.perf_counter()
        if not refs:
            return _empty_batch(self.config.event_output_mode)
        for part in parts:
            self.validate_part_config(part.plan)
        tickers, ordinals, timestamps = _identity_arrays(parts, refs)
        output_mode = str(self.config.event_output_mode)
        raw_windows: dict[str, np.ndarray] = {}
        raw_flat: dict[str, np.ndarray] = {}
        raw_mask = np.zeros((len(refs), 0), dtype=np.bool_)
        headers = np.zeros((len(refs), 0, HEADER_BYTES), dtype=np.uint8)
        encoded_events = np.zeros((len(refs), 0, 128, EVENT_BYTES), dtype=np.uint8)
        if output_mode == "raw_windows":
            raw_windows = self._materialize_raw_windows(parts, refs)
        elif output_mode == "raw_flat":
            raw_flat, raw_mask = self._materialize_raw_flat(parts, refs)
        elif output_mode == "encoded_uint8":
            headers, encoded_events = self._materialize_encoded(parts, refs)
        labels, future_bars, future_mask, horizons = self._materialize_intraday_labels(parts, refs)
        availability = {
            "event_context_available": np.ones((len(refs),), dtype=np.bool_) if output_mode != "none" else np.zeros((len(refs),), dtype=np.bool_),
            "intraday_labels_available": future_mask.any(axis=1) if future_mask.size else np.zeros((len(refs),), dtype=np.bool_),
        }
        external_context = {}
        if self.config.include_external_context:
            external_context = _external_context_summary(parts)
        profile = {"samples": len(refs), "materialize_seconds": time.perf_counter() - start}
        return TickerMonthTrainingBatch(
            ticker=tickers,
            origin_ordinal=ordinals,
            origin_timestamp_us=timestamps,
            event_output_mode=output_mode,
            raw_event_windows=raw_windows,
            raw_event_flat=raw_flat,
            raw_event_mask=raw_mask,
            headers_uint8=headers,
            events_uint8=encoded_events,
            intraday_labels=labels,
            future_intraday_bar_horizons=horizons,
            future_intraday_bars=future_bars,
            future_intraday_bar_mask=future_mask,
            input_availability=availability,
            external_context=external_context,
            profile=profile,
        )

    def _materialize_raw_windows(self, parts: Sequence[LoadedTickerMonthPart], refs: Sequence[TickerMonthSampleRef]) -> dict[str, np.ndarray]:
        starts = self._window_starts(parts, refs)
        out: dict[str, np.ndarray] = {}
        offsets = np.arange(int(self.config.events_per_window), dtype=np.int64)
        for column in NUMERIC_EVENT_COLUMNS:
            if not _all_parts_have_event_column(parts, column):
                continue
            dtype = _event_column_dtype(parts, column)
            values = np.empty((len(refs), len(self.context_lags), int(self.config.events_per_window)), dtype=dtype)
            for row, ref in enumerate(refs):
                part = parts[int(ref.part_index)]
                arr = part.event_array(column)
                values[row] = arr[starts[row, :, None] + offsets[None, :]]
            out[column] = values
        return out

    def _materialize_raw_flat(self, parts: Sequence[LoadedTickerMonthPart], refs: Sequence[TickerMonthSampleRef]) -> tuple[dict[str, np.ndarray], np.ndarray]:
        coverage = int(self.coverage_events)
        out: dict[str, np.ndarray] = {}
        mask = np.ones((len(refs), coverage), dtype=np.bool_)
        offsets = np.arange(coverage, dtype=np.int64)
        starts = _origin_event_offsets(parts, refs) - coverage + 1
        if np.any(starts < 0):
            raise RuntimeError("Raw flat event coverage is out of bounds; rebuild cache with larger lookback or reduce coverage.")
        for column in NUMERIC_EVENT_COLUMNS:
            if not _all_parts_have_event_column(parts, column):
                continue
            dtype = _event_column_dtype(parts, column)
            values = np.empty((len(refs), coverage), dtype=dtype)
            for row, ref in enumerate(refs):
                part = parts[int(ref.part_index)]
                values[row] = part.event_array(column)[int(starts[row]) + offsets]
            out[column] = values
        return out, mask

    def _materialize_encoded(self, parts: Sequence[LoadedTickerMonthPart], refs: Sequence[TickerMonthSampleRef]) -> tuple[np.ndarray, np.ndarray]:
        starts = self._window_starts(parts, refs)
        flat_count = len(refs) * len(self.context_lags)
        windows = np.empty((flat_count, 128), dtype=ENCODER_EVENT_DTYPE)
        previous = np.full((flat_count,), -1, dtype=np.int64)
        offsets = np.arange(128, dtype=np.int64)
        flat = 0
        for row, ref in enumerate(refs):
            part = parts[int(ref.part_index)]
            for context_index in range(len(self.context_lags)):
                start = int(starts[row, context_index])
                idx = start + offsets
                windows[flat]["ordinal"] = part.event_array("ordinal")[idx].astype(np.uint64, copy=False)
                windows[flat]["event_type"] = part.event_array("event_type")[idx].astype(np.uint8, copy=False)
                windows[flat]["sip_timestamp_us"] = part.event_array("timestamp_us")[idx].astype(np.uint64, copy=False)
                windows[flat]["price_primary_int"] = part.event_array("price_primary_int")[idx].astype(np.uint32, copy=False)
                windows[flat]["price_secondary_int"] = part.event_array("price_secondary_int")[idx].astype(np.uint32, copy=False)
                windows[flat]["size_primary"] = part.event_array("size_primary")[idx].astype(np.float32, copy=False)
                windows[flat]["size_secondary"] = part.event_array("size_secondary")[idx].astype(np.float32, copy=False)
                windows[flat]["exchange_primary"] = part.event_array("exchange_primary")[idx].astype(np.uint8, copy=False)
                windows[flat]["exchange_secondary"] = part.event_array("exchange_secondary")[idx].astype(np.uint8, copy=False)
                windows[flat]["event_flags"] = part.event_array("event_flags")[idx].astype(np.uint8, copy=False)
                windows[flat]["conditions_packed"] = part.event_array("conditions_packed")[idx].astype(np.uint32, copy=False)
                if start > 0:
                    previous[flat] = int(part.event_array("timestamp_us")[start - 1])
                flat += 1
        headers, events, valid, reasons = encode_unified_event_windows(windows, previous_sip_us=previous)
        if not bool(valid.all()):
            bad = int(np.flatnonzero(~valid)[0])
            raise RuntimeError(f"Encoded event window failed validation at flat window {bad}: {reasons[bad]!r}")
        return headers.reshape(len(refs), len(self.context_lags), HEADER_BYTES), events.reshape(len(refs), len(self.context_lags), 128, EVENT_BYTES)

    def _window_starts(self, parts: Sequence[LoadedTickerMonthPart], refs: Sequence[TickerMonthSampleRef]) -> np.ndarray:
        origins = _origin_event_offsets(parts, refs)
        lag_array = np.asarray(self.context_lags, dtype=np.int64)
        starts = origins[:, None] - lag_array[None, :] - int(self.config.events_per_window) + 1
        if np.any(starts < 0):
            raise RuntimeError("Event window starts before loaded event rows; rebuild cache with larger lookback or reduce coverage.")
        for row, ref in enumerate(refs):
            part = parts[int(ref.part_index)]
            ordinals = part.event_array("ordinal").astype(np.int64, copy=False)
            ends = starts[row] + int(self.config.events_per_window) - 1
            if np.any(ends >= ordinals.shape[0]):
                raise RuntimeError("Event window exceeds loaded event rows.")
            contiguous = (ordinals[ends] - ordinals[starts[row]]) == (int(self.config.events_per_window) - 1)
            if not bool(contiguous.all()):
                raise RuntimeError(f"Event window crosses an ordinal gap for {part.plan.month}:{part.plan.ticker}:part_{part.plan.part_id:05d}.")
        return starts

    def _materialize_intraday_labels(
        self, parts: Sequence[LoadedTickerMonthPart], refs: Sequence[TickerMonthSampleRef]
    ) -> tuple[dict[str, np.ndarray], np.ndarray, np.ndarray, tuple[str, ...]]:
        if "intraday_labels" not in self.config.data_groups and "labels" not in self.config.data_groups:
            return {}, np.zeros((len(refs), 0, len(FUTURE_BAR_FEATURE_KEYS)), dtype=np.float32), np.zeros((len(refs), 0), dtype=np.bool_), ()
        horizons = _cached_horizons(parts)
        horizon_count = len(horizons)
        labels_out = {
            "price_primary_int": np.zeros((len(refs), horizon_count), dtype=np.int32),
            "price_secondary_int": np.zeros((len(refs), horizon_count), dtype=np.int32),
            "size_primary_sum": np.zeros((len(refs), horizon_count), dtype=np.float32),
            "size_secondary_sum": np.zeros((len(refs), horizon_count), dtype=np.float32),
            "event_count": np.zeros((len(refs), horizon_count), dtype=np.uint64),
            "last_event_timestamp_us": np.zeros((len(refs), horizon_count), dtype=np.int64),
            "available": np.zeros((len(refs), horizon_count), dtype=np.bool_),
        }
        bars = np.zeros((len(refs), horizon_count, len(FUTURE_BAR_FEATURE_KEYS)), dtype=np.float32)
        mask = np.zeros((len(refs), horizon_count), dtype=np.bool_)
        for row, ref in enumerate(refs):
            part = parts[int(ref.part_index)]
            if part.labels is None or part.labels.height == 0:
                continue
            origin = int(part.origins.get_column("origin_ordinal")[int(ref.origin_row)])
            label_values = _label_values_for_origin(part.labels, origin, horizon_count)
            if label_values is None:
                if self.config.strict_audit:
                    raise RuntimeError(f"Missing intraday labels for {part.plan.month}:{part.plan.ticker}|{origin}.")
                continue
            for key in labels_out:
                values = label_values[key]
                labels_out[key][row, : values.shape[0]] = values.astype(labels_out[key].dtype, copy=False)
            available = labels_out["available"][row].astype(bool)
            mask[row] = available
            bars[row, :, FUTURE_BAR_FEATURE_KEYS.index("open")] = labels_out["price_primary_int"][row].astype(np.float32)
            bars[row, :, FUTURE_BAR_FEATURE_KEYS.index("close")] = labels_out["price_primary_int"][row].astype(np.float32)
            bars[row, :, FUTURE_BAR_FEATURE_KEYS.index("high")] = labels_out["price_primary_int"][row].astype(np.float32)
            bars[row, :, FUTURE_BAR_FEATURE_KEYS.index("low")] = labels_out["price_secondary_int"][row].astype(np.float32)
            bars[row, :, FUTURE_BAR_FEATURE_KEYS.index("volume")] = labels_out["size_primary_sum"][row].astype(np.float32)
        return labels_out, bars, mask, horizons


class AsyncTickerMonthBatchLoader:
    def __init__(self, config: TickerMonthLoaderConfig) -> None:
        self.config = normalize_loader_config(config)
        self.index = TickerMonthCacheIndex(self.config)
        self.reader = TickerMonthPartReader(self.config.data_groups, include_external_context=self.config.include_external_context)
        self.materializer = TickerMonthBatchMaterializer(self.config)

    def iter_batches(self) -> Iterator[TickerMonthTrainingBatch]:
        rng = random.Random(int(self.config.seed))
        plans = list(self.index.parts)
        if self.config.shuffle_parts:
            rng.shuffle(plans)
        emitted = 0
        start_us = _parse_timestamp_us(self.config.start_utc) if self.config.start_utc else None
        end_us = _parse_timestamp_us(self.config.end_utc) if self.config.end_utc else None
        group_size = max(1, int(self.config.loaded_parts_per_group))
        with ThreadPoolExecutor(max_workers=max(1, int(self.config.read_workers)), thread_name_prefix="tmc-load") as read_pool:
            for group_start in range(0, len(plans), group_size):
                group_plans = plans[group_start : group_start + group_size]
                loaded = list(read_pool.map(self.reader.load, group_plans))
                refs = _sample_refs_for_loaded_parts(loaded, start_us=start_us, end_us=end_us)
                if self.config.shuffle_within_loaded_group:
                    rng.shuffle(refs)
                with ThreadPoolExecutor(max_workers=max(1, int(self.config.materialize_workers)), thread_name_prefix="tmc-materialize") as mat_pool:
                    for batch in _materialize_bounded(mat_pool, self.materializer, loaded, _batched_refs(refs, int(self.config.batch_size))):
                        if batch.sample_count == 0:
                            continue
                        yield batch
                        emitted += 1
                        if int(self.config.max_batches) > 0 and emitted >= int(self.config.max_batches):
                            return


def normalize_loader_config(config: TickerMonthLoaderConfig) -> TickerMonthLoaderConfig:
    mode = str(config.event_output_mode)
    if mode not in SUPPORTED_EVENT_OUTPUT_MODES:
        raise ValueError(f"Unsupported event_output_mode={mode!r}; expected one of {sorted(SUPPORTED_EVENT_OUTPUT_MODES)}")
    groups = tuple(dict.fromkeys(str(group) for group in config.data_groups))
    if mode in {"raw_flat", "raw_windows"} and "events" not in groups:
        groups = (*groups, "events")
    if mode == "encoded_uint8":
        groups = (*tuple(group for group in groups if group != "encoded_events"), "events", "encoded_events")
    return TickerMonthLoaderConfig(
        cache_root=Path(config.cache_root),
        split=str(config.split),
        start_utc=str(config.start_utc),
        end_utc=str(config.end_utc),
        months=tuple(str(month) for month in config.months),
        tickers=tuple(str(ticker).upper() for ticker in config.tickers),
        batch_size=max(1, int(config.batch_size)),
        seed=int(config.seed),
        data_groups=groups,
        event_output_mode=mode,
        events_per_window=max(1, int(config.events_per_window)),
        context_chunks=max(0, int(config.context_chunks)),
        context_stride_events=max(1, int(config.context_stride_events)),
        flat_coverage_events=max(0, int(config.flat_coverage_events)),
        loaded_parts_per_group=max(1, int(config.loaded_parts_per_group)),
        read_workers=max(1, int(config.read_workers)),
        materialize_workers=max(1, int(config.materialize_workers)),
        max_batches=max(0, int(config.max_batches)),
        shuffle_parts=bool(config.shuffle_parts),
        shuffle_within_loaded_group=bool(config.shuffle_within_loaded_group),
        include_external_context=bool(config.include_external_context),
        strict_audit=bool(config.strict_audit),
    )


def _read_root_manifest(cache_root: Path) -> dict[str, Any]:
    path = Path(cache_root) / "manifest.json"
    if not path.exists():
        raise FileNotFoundError(f"Missing ticker/month cache manifest: {path}")
    manifest = read_json(path)
    if manifest.get("format") != TICKER_MONTH_CACHE_FORMAT:
        raise ValueError(f"Unexpected cache format: {manifest.get('format')!r}")
    if int(manifest.get("version") or 0) != TICKER_MONTH_CACHE_VERSION:
        raise ValueError(f"Unexpected cache version: {manifest.get('version')!r}")
    return manifest


def _selected_months(config: TickerMonthLoaderConfig, manifest: Mapping[str, Any]) -> tuple[str, ...]:
    if config.months:
        return tuple(config.months)
    if config.start_utc and config.end_utc:
        months = full_months_in_period(config.start_utc, config.end_utc)
        if months:
            return months
    raw = manifest.get("months") or ()
    if isinstance(raw, str):
        return (raw,)
    return tuple(str(month) for month in raw)


def _path_value(path: Path, key: str) -> str:
    prefix = f"{key}="
    for part in path.parts:
        if part.startswith(prefix):
            return part.split("=", 1)[1]
    return ""


def _package_context_files(package_dir: Path) -> dict[str, str]:
    manifest_path = package_dir / "manifest.json"
    manifest = read_json(manifest_path)
    files = manifest.get("files") or {}
    return {str(key): str(value) for key, value in files.items() if key in {"ticker_news_tokens", "sec_filing_tokens", "xbrl", "daily_bars"}}


def _sample_refs_for_loaded_parts(loaded: Sequence[LoadedTickerMonthPart], *, start_us: int | None, end_us: int | None) -> list[TickerMonthSampleRef]:
    refs: list[TickerMonthSampleRef] = []
    for part_index, part in enumerate(loaded):
        if part.origins is None or part.origins.height == 0:
            continue
        ts = part.origins.get_column("origin_timestamp_us").to_numpy().astype(np.int64, copy=False)
        mask = np.ones((ts.shape[0],), dtype=np.bool_)
        if start_us is not None:
            mask &= ts >= int(start_us)
        if end_us is not None:
            mask &= ts < int(end_us)
        for row in np.flatnonzero(mask):
            refs.append(TickerMonthSampleRef(part_index=int(part_index), origin_row=int(row)))
    return refs


def _batched_refs(refs: Sequence[TickerMonthSampleRef], batch_size: int) -> Iterator[list[TickerMonthSampleRef]]:
    size = max(1, int(batch_size))
    for start in range(0, len(refs), size):
        yield list(refs[start : start + size])


def _materialize_bounded(
    executor: ThreadPoolExecutor,
    materializer: TickerMonthBatchMaterializer,
    loaded: Sequence[LoadedTickerMonthPart],
    batches: Iterator[list[TickerMonthSampleRef]],
) -> Iterator[TickerMonthTrainingBatch]:
    max_pending = max(1, int(getattr(executor, "_max_workers", 1))) * 2
    pending: set[Future[TickerMonthTrainingBatch]] = set()

    def submit_until_full() -> None:
        while len(pending) < max_pending:
            try:
                refs = next(batches)
            except StopIteration:
                return
            pending.add(executor.submit(materializer.materialize, loaded, refs))

    submit_until_full()
    while pending:
        done, pending = wait(pending, return_when=FIRST_COMPLETED)
        for future in done:
            yield future.result()
        submit_until_full()


def _identity_arrays(parts: Sequence[LoadedTickerMonthPart], refs: Sequence[TickerMonthSampleRef]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    tickers = np.empty((len(refs),), dtype=object)
    ordinals = np.empty((len(refs),), dtype=np.int64)
    timestamps = np.empty((len(refs),), dtype=np.int64)
    for row, ref in enumerate(refs):
        part = parts[int(ref.part_index)]
        origin = part.origins.row(int(ref.origin_row), named=True)
        tickers[row] = str(origin["ticker"])
        ordinals[row] = int(origin["origin_ordinal"])
        timestamps[row] = int(origin["origin_timestamp_us"])
    return tickers, ordinals, timestamps


def _origin_event_offsets(parts: Sequence[LoadedTickerMonthPart], refs: Sequence[TickerMonthSampleRef]) -> np.ndarray:
    offsets = np.empty((len(refs),), dtype=np.int64)
    for row, ref in enumerate(refs):
        part = parts[int(ref.part_index)]
        offsets[row] = int(part.origins.get_column("event_row_offset")[int(ref.origin_row)])
    return offsets


def _all_parts_have_event_column(parts: Sequence[LoadedTickerMonthPart], column: str) -> bool:
    return all(part.events is not None and column in part.events.columns for part in parts)


def _event_column_dtype(parts: Sequence[LoadedTickerMonthPart], column: str) -> np.dtype:
    for part in parts:
        if part.events is not None and column in part.events.columns:
            return np.asarray(part.event_array(column)).dtype
    return np.float32


def _cached_horizons(parts: Sequence[LoadedTickerMonthPart]) -> tuple[str, ...]:
    for part in parts:
        horizons = part.plan.config.get("intraday_label_horizons") or ()
        if horizons:
            return tuple(str(item) for item in horizons)
    return ()


def _label_rows_for_origin(labels: Any, origin_ordinal: int, expected: int) -> Any | None:
    if labels is None or labels.height == 0:
        return None
    ordinals = labels.get_column("origin_ordinal").to_numpy().astype(np.int64, copy=False)
    left = int(np.searchsorted(ordinals, int(origin_ordinal), side="left"))
    right = int(np.searchsorted(ordinals, int(origin_ordinal), side="right"))
    if right <= left:
        return None
    frame = labels.slice(left, right - left)
    if expected and frame.height != expected:
        return None
    return frame


def _label_values_for_origin(labels: Any, origin_ordinal: int, expected: int) -> dict[str, np.ndarray] | None:
    if labels is None or labels.height == 0:
        return None
    ordinals = labels.get_column("origin_ordinal").to_numpy().astype(np.int64, copy=False)
    left = int(np.searchsorted(ordinals, int(origin_ordinal), side="left"))
    right = int(np.searchsorted(ordinals, int(origin_ordinal), side="right"))
    if right <= left:
        return None
    keys = (
        "price_primary_int",
        "price_secondary_int",
        "size_primary_sum",
        "size_secondary_sum",
        "event_count",
        "last_event_timestamp_us",
        "available",
    )
    if _labels_are_pivoted(labels):
        if right - left != 1:
            return None
        row = labels.row(left, named=True)
        values = {key: _cell_array(row.get(key)) for key in keys}
    else:
        frame = labels.slice(left, right - left)
        if expected and frame.height != expected:
            return None
        values = {key: frame.get_column(key).to_numpy() for key in keys}
    if expected and any(int(value.shape[0]) != int(expected) for value in values.values()):
        return None
    return values


def _labels_are_pivoted(labels: Any) -> bool:
    if labels is None or int(getattr(labels, "height", 0) or 0) <= 0 or "horizon_us" not in labels.columns:
        return False
    dtype_text = str(labels.schema.get("horizon_us", "")).lower()
    return "list" in dtype_text or "array" in dtype_text


def _cell_array(value: Any) -> np.ndarray:
    if value is None:
        return np.asarray([])
    if hasattr(value, "to_numpy"):
        return value.to_numpy()
    if isinstance(value, np.ndarray):
        return value
    if isinstance(value, (list, tuple)):
        return np.asarray(value)
    return np.asarray([value])


def _external_context_summary(parts: Sequence[LoadedTickerMonthPart]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for part in parts:
        for name, frame in part.context.items():
            summary.setdefault(name, 0)
            summary[name] += int(getattr(frame, "height", 0) or 0)
    return summary


def _empty_batch(mode: str) -> TickerMonthTrainingBatch:
    return TickerMonthTrainingBatch(
        ticker=np.asarray([], dtype=object),
        origin_ordinal=np.asarray([], dtype=np.int64),
        origin_timestamp_us=np.asarray([], dtype=np.int64),
        event_output_mode=str(mode),
    )


def _parse_timestamp_us(value: str) -> int:
    text = str(value).strip().replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return int(parsed.astimezone(dt.timezone.utc).timestamp() * 1_000_000)


def _polars() -> Any:
    try:
        import polars as pl  # type: ignore
    except ModuleNotFoundError as exc:
        raise RuntimeError("Install polars to use ticker/month cache loader.") from exc
    return pl
